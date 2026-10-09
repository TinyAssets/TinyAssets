"""A node timeout ABANDONS a live provider subprocess; it does not reap it.

Question under test (bounded): on a configured node timeout in a
prompt-template foreground/parallel node, does the platform terminate and reap
the owned provider subprocess *tree*, or abandon a live call?

Answer these tests encode: it abandons. ``_run_with_timeout``
(``tinyassets/graph_compiler.py``) raises ``NodeTimeoutError`` and returns to
the graph while the worker thread keeps running; nothing carries a handle to
the process that worker owns. The backstop it names --
``_sync_call_timeout_s`` (``providers/router.py``) at ``max(legacy,
absolute_cap) + 30`` -- fires on a strictly later clock, and when it does fire
the reap is ``proc.kill()`` on the DIRECT CHILD only: no provider spawn site
creates a process group, so a grandchild (the Windows ``.cmd`` shim's real
``claude``, or the engine-MCP server ``claude`` itself spawns) survives.

These assert the CURRENT abandonment, so they are the executable spec a fix has
to invert -- not a passing guard that would still pass if the bug were fixed.
``test_provider_spawn_sites_create_no_process_group`` is the exception: it
flips to an ordinary regression guard the moment the spawn sites gain a group.

Controlled local ``sys.executable`` children only. No LLM, no network, no
effects, no provider binary. Every child is short-lived and killed by the test
that started it.

Not re-derived here: queued expiry (PR #3926, ``_DeadlineExpiredBeforeStart``)
and bounded-admission cancel (PR #3927). Both stop work BEFORE it starts; this
module is only about work that already started.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tinyassets.branches import NodeDefinition
from tinyassets.graph_compiler import NodeTimeoutError, _build_prompt_template_node

# A node budget small enough to keep the suite fast. The mechanism is
# scale-free: 0.4s here is 300s in production, and the assertions below are
# about ordering (terminal-before-dead), never about the magnitude.
NODE_TIMEOUT_S = 0.4

# The child outlives the node budget by a wide margin so "still alive after the
# node went terminal" cannot be a scheduling coincidence.
CHILD_LIFETIME_S = 6.0

# How long to let a hypothetical reap LAND before sampling liveness or growth.
#
# Measured, not guessed: a mutation run that killed the owned child exactly at
# the node deadline still let 3 further ticks through, because ``taskkill /F
# /T`` latency exceeds the 0.05s tick interval. Sampling immediately after the
# node goes terminal therefore passes whether or not a reap exists -- a vacuous
# assertion. Opening the window a full second later makes these tests actually
# sensitive to the behaviour they claim to pin.
REAP_SETTLE_S = 1.0

_TICKER = textwrap.dedent(
    """
    import sys, time
    marker = sys.argv[1]
    deadline = time.monotonic() + float(sys.argv[2])
    while time.monotonic() < deadline:
        with open(marker, 'a', encoding='utf-8') as fh:
            fh.write('tick\\n')
            fh.flush()
        time.sleep(0.05)
    """
)

# A parent that spawns a ticking grandchild and then just waits. Killing the
# parent is what ``proc.kill()`` does today; the grandchild is what survives.
_PARENT_SPAWNING_GRANDCHILD = textwrap.dedent(
    """
    import subprocess, sys, time
    grandchild = subprocess.Popen(
        [sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]
    )
    with open(sys.argv[4], 'w', encoding='utf-8') as fh:
        fh.write(str(grandchild.pid))
        fh.flush()
    time.sleep(float(sys.argv[3]))
    """
)


def _tick_count(marker: Path) -> int:
    try:
        return len(marker.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return 0


def _kill_pid(pid: int) -> None:
    """Best-effort cleanup. Never the code under test -- only hygiene."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, check=False, timeout=20,
            )
        else:
            os.kill(pid, 9)
    except Exception:  # noqa: BLE001 - already gone
        pass


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False, timeout=20,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def reaped_children():
    """Kill every pid the test registered, whatever the test concluded."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        _kill_pid(pid)


def _timing_out_node() -> NodeDefinition:
    return NodeDefinition(
        node_id="angle_a",
        display_name="Angle A",
        prompt_template="Do {x}.",
        input_keys=["x"],
        output_keys=["out"],
        timeout_seconds=NODE_TIMEOUT_S,
    )


def _bridge_owning_a_child(marker: Path, pids: list[int]):
    """A provider bridge that owns a real subprocess, like the real one does.

    It spawns the child, then blocks on it -- exactly the shape of
    ``call_with_policy_sync`` blocking on a ``claude -p`` stream. The node
    timeout fires while this is still running.
    """
    started: dict[str, subprocess.Popen] = {}

    def _bridge(prompt, system, *, role="writer", config=None, **kwargs):
        proc = subprocess.Popen(
            [sys.executable, "-c", _TICKER, str(marker), str(CHILD_LIFETIME_S)],
        )
        started["proc"] = proc
        pids.append(proc.pid)
        proc.wait(timeout=CHILD_LIFETIME_S + 10)
        return "never reached before the node deadline"

    return _bridge, started


def test_node_timeout_abandons_a_live_owned_subprocess(tmp_path, reaped_children):
    """The node goes terminal; the subprocess it owns keeps working.

    This is the whole finding. If a fix ever reaps at the node deadline, the
    ``poll() is None`` assertion flips red and this test must be rewritten to
    assert the reap -- that is the point of encoding it.
    """
    marker = tmp_path / "ticks.log"
    bridge, started = _bridge_owning_a_child(marker, reaped_children)

    fn = _build_prompt_template_node(
        _timing_out_node(), provider_call=bridge, event_sink=None,
    )

    with pytest.raises(NodeTimeoutError) as exc_info:
        fn({"x": "thing"})

    terminal_at = time.monotonic()
    assert exc_info.value.node_id == "angle_a"
    # The message production emits today -- the one the user's failure quoted.
    assert "may still be running in the background" in str(exc_info.value)

    proc = started.get("proc")
    assert proc is not None, "the bridge never got to spawn its child"

    # Let any reap that exists land before measuring anything (REAP_SETTLE_S).
    while time.monotonic() - terminal_at < REAP_SETTLE_S:
        time.sleep(0.05)

    # ABANDONED, not reaped: a full second past the node's terminal verdict the
    # process the node owned is still running.
    assert proc.poll() is None, (
        f"the child was dead {REAP_SETTLE_S}s after node-terminal time; if the "
        "platform gained a reap, rewrite this test to assert it"
    )

    # And it is still doing work, not merely lingering. Sample twice, both
    # samples taken after the settle window, so growth here cannot be reap
    # latency bleeding through.
    ticks_after_settle = _tick_count(marker)
    sample_at = time.monotonic()
    while time.monotonic() - sample_at < 0.6:
        time.sleep(0.05)
    ticks_later = _tick_count(marker)

    assert ticks_later > ticks_after_settle, (
        "expected the abandoned provider child to keep producing output well "
        f"after the node failed ({ticks_after_settle} -> {ticks_later})"
    )
    assert proc.poll() is None, "child died on its own, not by any platform reap"


def test_platform_never_signals_the_abandoned_child(tmp_path, reaped_children):
    """Nothing in the platform holds a handle: only the test can stop it.

    ``future.cancel()`` in ``_run_with_timeout`` returns False once the worker
    started, and there is no other route. The child therefore survives until
    this test kills it -- which is the proof that no platform code path can.
    """
    marker = tmp_path / "ticks.log"
    bridge, started = _bridge_owning_a_child(marker, reaped_children)

    fn = _build_prompt_template_node(
        _timing_out_node(), provider_call=bridge, event_sink=None,
    )
    with pytest.raises(NodeTimeoutError):
        fn({"x": "thing"})

    proc = started["proc"]

    # Well past the node budget, with no platform actor in between.
    deadline = time.monotonic() + (NODE_TIMEOUT_S * 4)
    while time.monotonic() < deadline:
        time.sleep(0.05)

    assert proc.poll() is None, (
        f"child exited without the test killing it after "
        f"{NODE_TIMEOUT_S * 4:.1f}s; something reaped it -- find out what"
    )

    # Only now, by the test's own hand.
    _kill_pid(proc.pid)
    proc.wait(timeout=20)
    assert proc.poll() is not None


def test_direct_child_kill_orphans_the_grandchild(tmp_path, reaped_children):
    """``proc.kill()`` is not a tree reap -- the grandchild survives.

    This is the shape the real path takes twice over: on Windows
    ``_resolve_claude_cmd`` returns ``use_shell=True`` for a ``.cmd`` shim, so
    the direct child is the shell and ``claude`` is a grandchild; and on every
    platform ``_engine_mcp_flags`` makes ``python -m
    tinyassets.engine_mcp_server`` a child of ``claude``. The providers' finally
    block kills only ``proc``.
    """
    marker = tmp_path / "grandchild.log"
    pid_file = tmp_path / "grandchild.pid"

    parent = subprocess.Popen(
        [
            sys.executable, "-c", _PARENT_SPAWNING_GRANDCHILD,
            _TICKER, str(marker), str(CHILD_LIFETIME_S), str(pid_file),
        ],
    )
    reaped_children.append(parent.pid)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.05)
    assert pid_file.exists(), "the parent never reported its grandchild's pid"
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    reaped_children.append(grandchild_pid)

    # Exactly what claude_provider.py / codex_provider.py do on their backstop.
    parent.kill()
    parent.wait(timeout=20)
    assert parent.poll() is not None

    killed_at = time.monotonic()
    # Same settle discipline as above: a tree reap, were one added, needs time
    # to land, and sampling inside that latency would pass either way.
    while time.monotonic() - killed_at < REAP_SETTLE_S:
        time.sleep(0.05)

    assert _pid_alive(grandchild_pid), (
        "the grandchild died with its parent; if the spawn sites gained a "
        "process group, this test should assert the tree reap instead"
    )

    ticks_after_settle = _tick_count(marker)
    sample_at = time.monotonic()
    while time.monotonic() - sample_at < 0.6:
        time.sleep(0.05)
    assert _tick_count(marker) > ticks_after_settle, (
        "expected the orphaned grandchild to keep working well after its "
        "parent was killed"
    )


def test_provider_spawn_sites_own_their_family_through_one_module():
    """Source invariant: the providers delegate ownership; they never signal.

    This test previously asserted the *finding* -- that the providers spawned
    with no process group and reaped nothing -- and its own message said to
    convert it once a tree reap existed. That reap now exists, so the assertion
    is inverted here rather than deleted: the evidence it carried (the contrast
    with the modules that always owned their trees) is kept below.

    Asserted against the SOURCE of the real modules, not a stub -- a stub would
    encode the behaviour under test. Enforcement, not configuration: the
    group-signalling tokens must be ABSENT from the adapters, and the one
    ``killpg`` in ``owned_process`` must be the anchor signalling its OWN group.
    """
    root = Path(__file__).resolve().parents[1] / "tinyassets"
    group_tokens = ("start_new_session", "CREATE_NEW_PROCESS_GROUP")

    for provider in ("providers/claude_provider.py", "providers/codex_provider.py"):
        source = (root / provider).read_text(encoding="utf-8", errors="replace")
        assert "aspawn_owned" in source, (
            f"{provider} no longer spawns through owned_process; its CLI's "
            "descendants would again outlive the turn"
        )
        assert "kill_owned_tree" in source, f"{provider} lost its tree reap"
        present = [token for token in group_tokens if token in source]
        assert not present, (
            f"{provider} creates its own process group ({present}); session "
            "setup belongs to owned_process, which also supplies the anchor "
            "that makes the group safe to signal"
        )
        assert "killpg" not in source and "taskkill" not in source, (
            f"{provider} signals a process group directly; only a live member "
            "of that group may do so"
        )

    owned = (root / "providers/owned_process.py").read_text(
        encoding="utf-8", errors="replace",
    )
    # The spawn point signals nothing: a provider runs in its owner's cell and
    # teardown is revocation through the mapper's lifetime channel.
    signals = [
        node for node in ast.walk(ast.parse(owned))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("killpg", "kill", "send_signal")
        and getattr(node.func.value, "id", "") == "os"
    ]
    assert signals == [], "owned_process gained a direct signal"

    # The contrast that made the original omission a defect, kept as evidence.
    for owner in ("node_sandbox.py", "workspace_git.py"):
        source = (root / owner).read_text(encoding="utf-8", errors="replace")
        assert any(token in source for token in group_tokens), (
            f"{owner} lost its process-group spawn -- that is its own "
            "regression, unrelated to the provider finding"
        )
        assert "killpg" in source, f"{owner} lost its tree reap"
