import asyncio

import pytest

from tinyassets import provider_admission as pa


def test_nested_reserve_deadlock_reproduction(monkeypatch):
    monkeypatch.setenv('TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS', '2')
    pa.reset_for_tests()
    async def chain():
        async with pa.provider_slot_async():
            async with pa.provider_slot_async(nested=True) as child:
                assert pa.admission_snapshot()['live'] == 2
                async with pa.provider_slot_async(nested=True, parent_slot=child):
                    assert pa.admission_snapshot()["live"] == 2
                    return 'finished'
    assert asyncio.run(asyncio.wait_for(chain(), timeout=0.5)) == "finished"
    assert pa.admission_snapshot()['live'] == 0
    assert pa.admission_snapshot()['waiting'] == 0


@pytest.mark.parametrize("raises", [False, True])
def test_slot_returns_to_parent(monkeypatch, raises):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    with pa.provider_slot() as parent:
        for _ in range(2):
            try:
                with pa.provider_slot(parent_slot=parent) as child:
                    assert parent.lent
                    assert child.parent is parent
                    assert pa.admission_snapshot()["live"] == 1
                    if raises:
                        raise ValueError("child failed")
            except ValueError:
                pass
            assert parent.active and not parent.lent
            assert pa.admission_snapshot()["live"] == 1
    assert pa.admission_snapshot()["live"] == 0


def test_parallel_siblings_count_separately(monkeypatch):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
    pa.reset_for_tests()
    async def run():
        async with pa.provider_slot_async() as parent:
            entered = asyncio.Event()
            release = asyncio.Event()
            async def child():
                async with pa.provider_slot_async(nested=True, parent_slot=parent):
                    entered.set()
                    await release.wait()
            first = asyncio.create_task(child())
            await entered.wait()
            try:
                async with pa.provider_slot_async(nested=True, parent_slot=parent) as sibling:
                    assert sibling.parent is None
                    assert pa.admission_snapshot()["live"] == 2
            finally:
                release.set()
                await first
            assert not parent.lent
    asyncio.run(asyncio.wait_for(run(), 1))
    assert pa.admission_snapshot()["live"] == 0


def test_waiting_sibling_can_take_returned_loan(monkeypatch):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    async def run():
        async with pa.provider_slot_async() as parent:
            async def child():
                async with pa.provider_slot_async(parent_slot=parent):
                    await asyncio.sleep(0.01)
            await asyncio.gather(child(), child(), child())
            assert not parent.lent
    asyncio.run(asyncio.wait_for(run(), 1))
    assert pa.admission_snapshot()["live"] == 0


def test_deep_transfers_and_cancel_restore_ownership(monkeypatch):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    async def descend(parent, depth):
        async with pa.provider_slot_async(parent_slot=parent) as child:
            if depth:
                await descend(child, depth - 1)
            else:
                raise asyncio.CancelledError()
    async def run():
        async with pa.provider_slot_async() as parent:
            with pytest.raises(asyncio.CancelledError):
                await descend(parent, 20)
            assert parent.active and not parent.lent
            assert pa.admission_snapshot()["live"] == 1
    asyncio.run(asyncio.wait_for(run(), 1))
    assert pa.admission_snapshot()["live"] == 0


def test_stale_or_foreign_process_handle_cannot_transfer(monkeypatch):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
    pa.reset_for_tests()
    with pa.provider_slot() as stale:
        pass
    with pa.provider_slot(parent_slot=stale) as fresh:
        assert fresh.parent is None
        foreign = pa.HeldProviderSlot(pid=-1)
        with pa.provider_slot(nested=True, parent_slot=foreign) as other:
            assert other.parent is None
            assert pa.admission_snapshot()["live"] == 2

@pytest.mark.parametrize("entry", ["call_sync", "call_with_policy_sync", "call"])
def test_router_nested_chain_uses_the_held_slot(monkeypatch, tmp_path, entry):
    from unittest.mock import MagicMock

    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers import router as routing
    from tinyassets.providers.base import (
        BaseProvider,
        ModelConfig,
        ProviderResponse,
        UniverseContext,
    )

    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
    pa.reset_for_tests()
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner")
    for name, value in dict(provider="codex", role="judge", operation="run_graph",
                            max_tokens=10, max_cost_microunits=5, selected_model=None,
                            native_selection=None, settlement_owner=None).items():
        setattr(carrier, name, value)
    carrier.validate_for_call.return_value = "codex"
    monkeypatch.setattr(routing, "_provider_invocation_carrier", lambda *a, **kw: carrier)
    context = UniverseContext(universe_dir=tmp_path, provider_invocation=carrier)
    config = ModelConfig(max_tokens=10)
    seen = []

    # Inject a test-only escape into admission, including worker threads; a
    # broken transfer must fail this test rather than hang interpreter shutdown.
    import time
    deadline = time.monotonic() + 3
    take = pa._take_lease_locked
    def bounded_take(*args):
        lease, limit = take(*args)
        if lease is None and time.monotonic() > deadline:
            raise AssertionError("test harness: nested admission deadlocked")
        return lease, limit
    monkeypatch.setattr(pa, "_take_lease_locked", bounded_take)

    class NestedProvider(BaseProvider):
        name = "codex"
        family = "openai"

        async def complete(self, prompt, system, config, **kwargs):
            seen.append(pa.admission_snapshot()["live"])
            depth = int(prompt)
            if depth:
                args = ("judge", str(depth - 1), "s")
                kw = dict(operation="run_graph", universe_context=context)
                if entry == "call":
                    with pa.blocking_provider_child():
                        await router.call(*args, config, **kw)
                elif entry == "call_with_policy_sync":
                    await asyncio.to_thread(router.call_with_policy_sync, *args, {}, config, **kw)
                else:
                    await asyncio.to_thread(router.call_sync, *args, config, **kw)
            return ProviderResponse(text="finished", provider="codex", model="fake",
                                    family="openai", latency_ms=0)

    router = routing.ProviderRouter(providers={"codex": NestedProvider()})
    async def run():
        # An outer turn holds slot 1. This agent child acquires slot 2, then
        # invokes grandchildren through the actual router, including its pool.
        async with pa.provider_slot_async():
            return await router.call("judge", "3", "s", config,
                                     operation="run_graph", universe_context=context)
    response = asyncio.run(asyncio.wait_for(run(), 4))
    assert response.text == "finished"
    assert seen == [2, 2, 2, 2]
    assert pa.admission_snapshot()["live"] == 0


def test_compiler_blocking_invoke_lends_across_node_worker(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from tinyassets import graph_compiler as gc
    from tinyassets import runs
    from tinyassets.branches import NodeDefinition

    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    monkeypatch.setattr(gc, "_authorize_child_ref", lambda *a, **kw: object())
    monkeypatch.setattr(gc, "_charge_child_run", lambda *a: None)
    monkeypatch.setattr(gc, "_bind_child_ticket", lambda *a: None)
    monkeypatch.setattr(gc, "_emit_invoke_design_used", lambda **kw: None)

    def execute(*args, **kwargs):
        def provider():
            parent = pa.blocking_parent_slot()
            assert parent is not None
            with pa.provider_slot(parent_slot=parent):
                assert pa.admission_snapshot()["live"] == 1
            return "done"
        result = gc._run_with_timeout(provider, timeout_s=1, node_id="child-agent")
        return SimpleNamespace(run_id="child", status="completed", output={"answer": result})
    monkeypatch.setattr(runs, "execute_branch", execute)
    node = NodeDefinition(node_id="invoke", display_name="Invoke", invoke_branch_spec={
        "branch_def_id": "child", "wait_mode": "blocking",
        "output_mapping": {"result": "answer"},
    })
    invoke = gc._build_invoke_branch_node(
        node, base_path=tmp_path, event_sink=None,
        execution_context=gc.BranchExecutionContext(actor="tester", universe_id="u"),
    )
    with pa.provider_slot() as parent:
        assert invoke({}) == {"result": "done"}
        assert not parent.lent
    assert pa.admission_snapshot()["live"] == 0


def test_queued_context_cannot_borrow_and_stale_context_cannot_reenter(monkeypatch):
    import contextvars

    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    with pa.provider_slot() as parent:
        with pa.blocking_provider_child():
            borrowed_context = contextvars.copy_context()
            with pa.independent_provider_work():
                assert pa.blocking_parent_slot() is None
                with pa.blocking_provider_child():
                    assert pa.blocking_parent_slot() is None
        stale = borrowed_context.run(pa.blocking_parent_slot)
        assert stale is not None and not stale.active
        assert not parent.lent


def test_parent_does_not_resume_before_a_timed_out_borrower_settles(monkeypatch):
    import contextvars
    import threading

    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    pa.reset_for_tests()
    borrowed = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    errors = []

    def blocked_call():
        try:
            with pa.blocking_provider_child():
                parent = pa.blocking_parent_slot()
                def child():
                    with pa.provider_slot(parent_slot=parent):
                        borrowed.set()
                        release.wait(2)
                worker = threading.Thread(target=child)
                worker.start()
                assert borrowed.wait(1)
                raise TimeoutError("caller timeout while worker is still running")
        except TimeoutError:
            returned.set()
        except BaseException as exc:
            errors.append(exc)

    with pa.provider_slot() as parent:
        ctx = contextvars.copy_context()
        caller = threading.Thread(target=ctx.run, args=(blocked_call,))
        caller.start()
        try:
            assert borrowed.wait(1)
            assert not returned.wait(0.05)
            assert parent.lent
            assert pa.admission_snapshot()["live"] == 1
        finally:
            release.set()
            caller.join(2)
        assert not caller.is_alive()
        assert returned.is_set() and not errors
        assert not parent.lent
    assert pa.admission_snapshot()["live"] == 0
