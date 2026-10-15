"""Shared-loop admission, real turn concurrency and cancellation fencing."""
from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest

from tests import test_interactive_http_agent as base
from tinyassets.agent_loop.execution import ExecutionOwner, off_loop
from tinyassets.provider_assignment import ProviderAssignmentAdmission
from tinyassets.providers import call as provider_calls
from tinyassets.storage.agent_turn_journal import AgentTurnJournal

rig = base.rig
reader = base.reader
served = base.served
agent = base.agent


@pytest.fixture
def owner():
    value = ExecutionOwner()
    yield value
    value.close()


def test_async_readers_share_loop_and_writer_precedes_new_reader(tmp_path):
    admission = ProviderAssignmentAdmission()
    universe = tmp_path / "cc"

    async def scenario():
        order = []
        first_entered, release = asyncio.Event(), asyncio.Event()

        async def first():
            async with admission.shared_async(universe):
                first_entered.set()
                await release.wait()

        async def writer():
            async with admission.exclusive_async(universe):
                order.append("writer")
                await asyncio.sleep(0)

        async def next_reader():
            async with admission.shared_async(universe):
                order.append("reader")

        first_task = asyncio.create_task(first())
        await first_entered.wait()
        # Another task on exactly the same thread can hold a shared admission.
        async with admission.shared_async(universe):
            writer_task = asyncio.create_task(writer())
            await asyncio.sleep(0)
            reader_task = asyncio.create_task(next_reader())
            await asyncio.sleep(0.02)
            assert order == []
        release.set()
        await asyncio.wait_for(asyncio.gather(first_task, writer_task, reader_task), 3)
        assert order == ["writer", "reader"]

    asyncio.run(scenario())
    assert not universe.exists()  # platform admission never creates an owner root


def test_cancelled_writer_removes_priority_and_sync_writer_does_not_deadlock_loop(tmp_path):
    admission = ProviderAssignmentAdmission()
    universe = tmp_path / "cc"
    order = []

    def writer():
        with admission.exclusive(universe):
            order.append("writer")

    async def scenario():
        async with admission.shared_async(universe):
            pending = asyncio.create_task(admission.exclusive_async(universe).__aenter__())
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            async def another_reader():
                async with admission.shared_async(universe):
                    pass
            await asyncio.create_task(another_reader())
            writing = asyncio.create_task(asyncio.to_thread(writer))
            async with asyncio.timeout(3):
                while not admission._state(universe).waiting_writers:
                    await asyncio.sleep(0.001)
            # This heartbeat is on the loop whose first reader must finish.
            await asyncio.sleep(0.02)
            assert order == []
        await asyncio.wait_for(writing, 3)
        assert order == ["writer"]

    asyncio.run(scenario())


def test_cross_process_style_flock_wait_can_be_cancelled(tmp_path):
    universe = tmp_path / "cc"
    first, second = ProviderAssignmentAdmission(), ProviderAssignmentAdmission()

    async def scenario():
        async with first.exclusive_async(universe):
            async def waiting():
                async with second.shared_async(universe):
                    pytest.fail("foreign writer must exclude the reader")
            task = asyncio.create_task(waiting())
            await asyncio.sleep(0.05)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with second.exclusive_async(universe):
            pass
        assert second._state(universe).readers == 0

    asyncio.run(scenario())


def test_context_and_500_waiting_turns_use_one_loop(owner):
    identity = contextvars.ContextVar("test_owner", default=None)
    identity.set("owner-a")

    async def scenario():
        ready, release = asyncio.Event(), asyncio.Event()
        entered = []

        async def turn():
            entered.append((asyncio.get_running_loop(), threading.get_ident(), identity.get()))
            if len(entered) == 500:
                ready.set()
            await release.wait()
            return await off_loop(identity.get)

        tasks = [asyncio.create_task(turn()) for _ in range(500)]
        await asyncio.wait_for(ready.wait(), 3)
        assert len(set(entered)) == 1
        release.set()
        assert set(await asyncio.gather(*tasks)) == {"owner-a"}

    owner.run(scenario())


def _turn(agent):
    return provider_calls.make_interactive_agent_turn(
        prompt="exact user prompt", system="exact system",
        universe_context=agent.served.context, config=agent.config,
    )


def test_two_real_router_turns_share_one_loop_and_overlap_inference(agent, owner):
    barrier = threading.Barrier(2, timeout=5)
    committed = threading.Barrier(2, timeout=5)
    agent.requested_rounds = 0
    agent.before_reply = barrier.wait
    loops = []
    original = agent.adapter.infer

    async def infer(**kwargs):
        loops.append((asyncio.get_running_loop(), threading.get_ident()))
        observer = kwargs["observer"]

        def committed_observer(*args):
            observer(*args)
            committed.wait()

        kwargs["observer"] = committed_observer
        return await original(**kwargs)

    agent.adapter.infer = infer
    turns = [_turn(agent), _turn(agent)]

    async def scenario():
        return await asyncio.wait_for(asyncio.gather(*(turn.run() for turn in turns)), 10)

    responses = owner.run(scenario())
    assert [response.text for response in responses] == ["finished exact answer"] * 2
    assert len(loops) == 2 and len(set(loops)) == 1
    assert len(agent.wires) == 2
    assert all(turn.turn.state == "completed" for turn in turns)
    assert len({turn.turn.turn_id for turn in turns}) == 2


def test_cancel_during_journal_commit_drains_before_release_and_never_dispatches(
    agent, owner, monkeypatch,
):
    entered, release = threading.Event(), threading.Event()
    original = AgentTurnJournal.begin_round

    def blocked(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AgentTurnJournal, "begin_round", blocked)
    turn = _turn(agent)

    async def scenario():
        task = asyncio.create_task(turn.run())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0.03)
            # Cancellation cannot release ownership ahead of the SQLite writer.
            assert not task.done()
            assert agent.wires == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert turn.turn.state == "held_transport"
            assert turn.turn.rounds[-1].state == "failed"
            assert agent.wires == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    owner.run(scenario())


def test_journal_cannot_adopt_new_owner_generation(agent):
    from tinyassets import owner_lease

    journal = agent.journal
    uid = agent.served.context.universe_dir.name
    turn = journal.create("owner", uid, prompt="x", system="")
    old = journal._lease(uid)
    owner_lease.release(old)
    successor = AgentTurnJournal(agent.served.rig.base)
    assert successor._lease(uid).generation == old.generation + 1
    with pytest.raises(owner_lease.LeaseLost, match="generation changed"):
        journal.abandon("owner", uid, turn.turn_id, expected_generation=turn.generation)
    with pytest.raises(owner_lease.LeaseLost, match="earlier owner generation"):
        successor.abandon("owner", uid, turn.turn_id, expected_generation=turn.generation)
    assert successor.get("owner", uid, turn.turn_id).state == "ready"
