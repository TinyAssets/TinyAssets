"""The four box tools: forwarded by op_id, lost replies asked about, never re-run."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from tests.agent_loop_fakes import FakeBox, exit_event
from tinyassets.agent_loop import box_tools
from tinyassets.agent_loop.box_tools import (
    BoxExecutor,
    BoxOperationRefused,
    BoxTools,
    box_tool_definitions,
)
from tinyassets.engine_tool_client import EngineToolError


def tools(box: FakeBox, root: str = "/cc") -> BoxTools:
    handle = box.bind("cc-1", account_id="owner", turn_id="t1")
    return BoxTools(BoxExecutor(box, handle, limits="limits", cwd=root), root=root)


def run(coro):
    return asyncio.run(coro)


def test_bash_is_argv_to_bash_never_a_command_string_and_carries_the_op_id():
    box = FakeBox(lambda argv, stdin: (b"hello", 0))
    assert run(tools(box).bash("t1:1:1", "echo hello")) == "hello\n[exit code 0]"
    record = box.execs["t1:1:1"]
    assert record.argv == ["/bin/bash", "-c", "echo hello"]
    assert record.cwd == "/cc"


def test_relative_paths_resolve_under_the_box_root():
    box = FakeBox(lambda argv, stdin: (b"line\n", 0))
    assert run(tools(box).read("op", "notes/a.md")) == "line\n"
    assert box.execs["op"].argv[4:] == ["/cc/notes/a.md", "1", "2000"]


def test_lost_start_reply_asks_again_with_the_same_op_id_and_runs_once():
    box = FakeBox(lambda argv, stdin: (b"x", 0))
    box.fail_start = 1
    run(tools(box).bash("op-1", "true"))
    assert box.starts == ["op-1", "op-1"]
    assert list(box.execs) == ["op-1"]


def test_unresolved_start_is_an_unknown_outcome_not_a_retry_with_a_new_id():
    box = FakeBox()
    box.fail_start = 2
    with pytest.raises(EngineToolError) as raised:
        run(tools(box).bash("op-1", "rm -rf x"))
    assert raised.value.outcome == "unknown"
    assert box.starts == ["op-1", "op-1"]


def test_a_refusal_before_the_operation_existed_propagates_as_refused():
    box = FakeBox()

    def refuse(*args, **kwargs):
        raise BoxOperationRefused("stale placement epoch")

    box.start_exec = refuse
    with pytest.raises(BoxOperationRefused):
        run(tools(box).bash("op-1", "true"))


def test_a_refusal_only_on_the_retry_is_unknown_because_the_first_may_have_run():
    box = FakeBox()
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("lost")
        raise BoxOperationRefused("epoch moved")

    box.start_exec = flaky
    with pytest.raises(EngineToolError) as raised:
        run(tools(box).bash("op-1", "true"))
    assert raised.value.outcome == "unknown"


def test_stream_break_resumes_once_from_its_offset():
    box = FakeBox(lambda argv, stdin: (b"done", 0))
    box.fail_stream = 1
    assert run(tools(box).bash("op", "true")).startswith("done")


def test_unresolved_stream_is_unknown_even_when_status_says_exited():
    box = FakeBox()
    box.fail_stream = 2
    box.status = SimpleNamespace(state="exited", code=0)
    with pytest.raises(EngineToolError) as raised:
        run(tools(box).bash("op", "true"))
    assert raised.value.outcome == "unknown"


def test_stream_ending_without_exit_is_a_lost_reply():
    box = FakeBox()
    box.stream = lambda h, exec_id, from_offset=0: iter(())
    with pytest.raises(EngineToolError):
        run(tools(box).bash("op", "true"))


def test_timeout_cancels_the_execution_in_the_box(monkeypatch):
    box = FakeBox()
    box.hang = True
    result = run(tools(box).bash("op", "sleep 999", timeout=1))
    assert box.cancels == ["op"]
    assert result.endswith("[killed: ran longer than 1s]")


def test_a_cancelled_turn_cancels_the_box_execution_then_propagates():
    box = FakeBox()
    box.hang = True

    async def scenario():
        task = asyncio.ensure_future(tools(box).bash("op", "sleep 999"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert box.cancels == ["op"]


def test_cancel_during_a_slow_start_still_cancels_what_the_box_accepted():
    box = FakeBox()
    box.hang = True
    original = box.start_exec

    def slow_start(*args, **kwargs):
        time.sleep(0.5)  # the box accepts it, the reply is slow
        return original(*args, **kwargs)

    box.start_exec = slow_start

    async def scenario():
        task = asyncio.ensure_future(tools(box).bash("op", "make deploy"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    # Cancelled by the launching thread when the box's reply lands.
    deadline = time.monotonic() + 5
    while not box.cancels and time.monotonic() < deadline:
        time.sleep(0.05)
    assert box.cancels == ["op"]


def test_a_timeout_whose_end_is_unconfirmed_is_unknown_not_a_result(monkeypatch):
    monkeypatch.setattr(box_tools, "_CANCEL_GRACE_SECONDS", 0.3)
    box = FakeBox()
    box.hang = True

    def lost_cancel(h, exec_id):
        box.cancels.append(exec_id)
        raise ConnectionError("cancel reply lost")

    box.cancel = lost_cancel
    try:
        with pytest.raises(EngineToolError) as raised:
            run(tools(box).bash("op", "sleep 999", timeout=1))
        assert raised.value.outcome == "unknown"
        assert box.cancels == ["op"]
    finally:
        box.released.set()


def test_cancel_never_queues_behind_the_reads_waiting_for_it():
    box = FakeBox()
    box.hang = True

    async def scenario():
        # One shared executor thread: a cancel on it would wait for the very
        # read that is waiting for the cancel.
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=1))
        task = asyncio.ensure_future(tools(box).bash("op", "sleep 999"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)

    run(scenario())
    assert box.cancels == ["op"]


def test_a_start_reply_arriving_after_the_turn_ended_is_still_cancelled():
    box = FakeBox()
    box.hang = True
    original = box.start_exec
    replied = threading.Event()

    def very_slow_start(*args, **kwargs):
        time.sleep(1.0)  # longer than anything the turn waits for
        try:
            return original(*args, **kwargs)
        finally:
            replied.set()

    box.start_exec = very_slow_start

    async def scenario():
        task = asyncio.ensure_future(tools(box).bash("op", "make deploy"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())  # the turn's loop is gone before the box answers
    assert replied.wait(5)
    deadline = time.monotonic() + 5
    while not box.cancels and time.monotonic() < deadline:
        time.sleep(0.05)
    assert box.cancels == ["op"]


def test_a_cancel_the_box_never_acknowledges_is_bounded_and_unknown(monkeypatch):
    monkeypatch.setattr(box_tools, "_CANCEL_GRACE_SECONDS", 0.3)
    box = FakeBox()
    box.hang = True
    stuck = threading.Event()
    box.cancel = lambda h, exec_id: stuck.wait(30)
    started = time.monotonic()
    try:
        with pytest.raises(EngineToolError) as raised:
            run(tools(box).bash("op", "sleep 999", timeout=1))
        assert raised.value.outcome == "unknown"
        assert time.monotonic() - started < 3.0
    finally:
        stuck.set()
        box.released.set()


def test_a_box_host_that_stops_answering_exhausts_a_bound_not_the_process(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(box_tools, "_BOX_CALL_SLOTS", slots)
    box = FakeBox()
    with pytest.raises(BoxOperationRefused):
        run(tools(box).bash("op", "true"))
    assert box.starts == []


def test_a_refused_read_after_a_started_command_is_unknown_not_refused(monkeypatch):
    class OneSlot:
        def __init__(self):
            self.taken = 0

        def acquire(self, blocking=True):
            self.taken += 1
            return self.taken == 1  # the start gets a slot, the reader does not

        def release(self):
            pass

    monkeypatch.setattr(box_tools, "_BOX_CALL_SLOTS", OneSlot())
    box = FakeBox()
    with pytest.raises(EngineToolError) as raised:
        run(tools(box).bash("op", "make deploy"))
    assert raised.value.outcome == "unknown"
    assert box.starts == ["op"] and box.cancels == ["op"]


def test_output_past_the_cap_kills_the_execution():
    box = FakeBox(lambda argv, stdin: (b"0123456789abcdef", 0))
    outcome = run(BoxExecutor(box, object(), limits=None).run(
        "op", ["yes"], wall_seconds=5, output_bytes=8,
    ))
    assert box.cancels == ["op"]
    assert outcome.output == b"01234567" and outcome.killed == "output_limit"


def test_write_sends_content_as_stdin_and_renames_into_place():
    box = FakeBox(lambda argv, stdin: (b"", 0))
    assert run(tools(box).write("op", "a.txt", "héllo")) == "wrote 6 bytes to /cc/a.txt"
    record = box.execs["op"]
    assert record.stdin == "héllo".encode()
    assert record.argv[4:] == ["/cc/a.txt", ""]
    assert 'flock -x "$d"' in record.argv[2] and 'mv -f -- "$3" "$1"' in record.argv[2]


def test_edit_writes_only_if_the_file_still_has_the_bytes_it_read():
    original = b"alpha beta"

    def script(argv, stdin):
        return (original, 0) if stdin is None else (b"", 0)

    box = FakeBox(script)
    assert run(tools(box).edit("op", "f.txt", "beta", "gamma")) == "edited /cc/f.txt"
    write = box.execs["op/write"]
    assert write.stdin == b"alpha gamma"
    assert write.argv[5] == hashlib.sha256(original).hexdigest()
    assert list(box.execs) == ["op/read", "op/write"]


@pytest.mark.parametrize(("content", "message"), [
    (b"no match here", "old_text was not found"),
    (b"beta beta", "matches 2 places"),
])
def test_edit_refuses_ambiguous_or_missing_text_without_writing(content, message):
    box = FakeBox(lambda argv, stdin: (content, 0))
    assert message in run(tools(box).edit("op", "f.txt", "beta", "x"))
    assert list(box.execs) == ["op/read"]


def test_an_argument_the_tool_does_not_take_is_refused_before_the_box():
    box = FakeBox()
    result = run(tools(box).call("bash", "op", {"command": "true", "cwd": "/"}))
    assert result.startswith("error: bash does not take")
    assert box.starts == []


def test_definitions_take_exactly_the_tool_jails_arguments():
    """Parity with the tool jail's MCP tools: same names, same parameters."""
    from tinyassets import engine_mcp_server as engine

    jail = {"read": engine.read_file, "write": engine.write_file,
            "edit": engine.edit_file, "bash": engine.run_bash}
    definitions = box_tool_definitions()
    assert tuple(definitions) == box_tools.BOX_TOOLS
    for name, fn in jail.items():
        target = getattr(fn, "fn", fn)
        parameters = set(inspect.signature(target).parameters)
        assert set(definitions[name]["inputSchema"]["properties"]) == parameters, name


def test_executor_refuses_an_unbound_handle():
    with pytest.raises(ValueError):
        BoxExecutor(FakeBox(), None, limits=None)


# ── the scripts themselves, run for real on a POSIX shell ───────────────────


class LocalBox(FakeBox):
    """Runs argv with a real ``sh`` in a directory: proves the scripts, not the box."""

    def __init__(self):
        super().__init__()

        def script(argv, stdin):
            proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=30)
            return proc.stdout + proc.stderr, proc.returncode

        self.script = script


posix = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sha256sum") is None,
    reason="the box tool scripts run in a POSIX box",
)


@posix
def test_scripts_write_read_edit_round_trip(tmp_path):
    root = str(tmp_path)
    box = LocalBox()
    t = tools(box, root)
    assert run(t.write("w", "dir/f.txt", "one\ntwo\n")) == f"wrote 8 bytes to {root}/dir/f.txt"
    assert run(t.read("r", "dir/f.txt", offset=2)) == "two\n"
    assert run(t.edit("e", "dir/f.txt", "two", "three")) == f"edited {root}/dir/f.txt"
    assert (tmp_path / "dir" / "f.txt").read_text() == "one\nthree\n"
    assert not [p for p in os.listdir(tmp_path / "dir") if ".ta-write." in p]


@posix
def test_script_edit_refuses_a_file_changed_between_read_and_write(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("alpha beta")
    box = LocalBox()
    inner = box.script

    def racing(argv, stdin):
        if stdin is not None:
            target.write_text("alpha beta, edited by bash meanwhile")
        return inner(argv, stdin)

    box.script = racing
    result = run(tools(box, str(tmp_path)).edit("e", "f.txt", "beta", "gamma"))
    assert "changed while it was being edited" in result
    assert target.read_text() == "alpha beta, edited by bash meanwhile"


@posix
def test_script_read_reports_a_missing_file(tmp_path):
    result = run(tools(LocalBox(), str(tmp_path)).read("r", "nope.txt"))
    assert result.startswith("error: no such file")


def test_exit_event_codes_map_to_the_jails_trailers():
    box = FakeBox()
    box.script = lambda argv, stdin: (b"", 137)
    assert run(tools(box).bash("op", "x")).endswith("[killed: a cpu time or memory limit]")
    assert exit_event(0).kind == "exit"


@posix
def test_concurrent_edits_never_silently_lose_one(tmp_path):
    """Every edit that reports success is in the final file (the flock orders them)."""
    target = tmp_path / "f.txt"
    tokens = [f"t{i:02d}" for i in range(12)]
    target.write_text(" ".join(tokens))
    box = LocalBox()
    t = tools(box, str(tmp_path))

    async def scenario():
        return await asyncio.gather(*(
            t.edit(f"e{i}", "f.txt", token, token.upper()) for i, token in enumerate(tokens)))

    results = run(scenario())
    final = target.read_text()
    succeeded = [tok for tok, res in zip(tokens, results) if res.startswith("edited")]
    assert succeeded, results
    for token in succeeded:
        assert token.upper() in final, (token, results)
    for token, res in zip(tokens, results):
        assert res.startswith("edited") or "changed while it was being edited" in res, res


@posix
def test_edits_wait_on_the_directory_lock_whose_identity_survives_the_rename(tmp_path):
    """The lock is the directory's: a rename replaces the file's inode, never it."""
    target = tmp_path / "f.txt"
    target.write_text("aa bb")
    holder = subprocess.Popen(["flock", "-x", str(tmp_path), "sleep", "1.5"])
    try:
        time.sleep(0.3)  # the holder has the lock
        t = tools(LocalBox(), str(tmp_path))

        async def scenario():
            return await asyncio.gather(t.edit("a", "f.txt", "aa", "AA"),
                                        t.edit("b", "f.txt", "bb", "BB"))

        started = time.monotonic()
        results = run(scenario())
        waited = time.monotonic() - started
    finally:
        holder.wait(10)
    assert waited >= 0.8, "the edits did not wait for the directory lock"
    assert sorted(r.startswith("edited") for r in results) == [False, True], results
    assert target.read_text() in {"AA bb", "aa BB"}


def test_no_free_cancel_slot_is_unknown_and_spawns_nothing(monkeypatch):
    monkeypatch.setattr(box_tools, "_CANCEL_GRACE_SECONDS", 0.3)
    empty = threading.BoundedSemaphore(1)
    empty.acquire()
    monkeypatch.setattr(box_tools, "_BOX_CANCEL_SLOTS", empty)
    box = FakeBox()
    box.hang = True
    try:
        with pytest.raises(EngineToolError) as raised:
            run(tools(box).bash("op", "sleep 999", timeout=1))
        assert raised.value.outcome == "unknown"
        assert box.cancels == []
    finally:
        box.released.set()
