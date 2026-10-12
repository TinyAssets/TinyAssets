"""Tests for uptime_canary_layer2 + SOFT_YELLOW alarm path in uptime_alarm.

Spec: 2026-04-19-layer2-canary-scope.md (archived in git) §2.6 amendment.

Exercises:
  - exit-code table (GREEN, SOFT_YELLOW, RED variants, SKIP)
  - log-line format for each state
  - browser-lock skip path
  - fabrication-mode soft-threshold boundary (=150000 ms is GREEN, >150000 is SOFT_YELLOW)
  - uptime_alarm SOFT_YELLOW consecutive-counter + FABRICATION_MODE_SUSPECTED breadcrumb
  - SOFT_YELLOW state reset on non-soft-yellow tail
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import browser_lock  # noqa: E402
import uptime_alarm  # noqa: E402
import uptime_canary  # noqa: E402
import uptime_canary_layer2 as l2  # noqa: E402

# ---- Fixtures ---------------------------------------------------------------


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Redirect all I/O to tmp_path and ensure no real browser lock."""
    uptime_log = tmp_path / "uptime.log"
    alarm_log = tmp_path / "uptime_alarms.log"
    alarm_state = tmp_path / ".uptime_alarm_state.json"
    lock_file = tmp_path / ".browser_lock.json"

    monkeypatch.setattr(uptime_canary, "LOG_PATH", uptime_log)
    monkeypatch.setattr(uptime_alarm, "UPTIME_LOG", uptime_log)
    monkeypatch.setattr(uptime_alarm, "ALARM_LOG", alarm_log)
    monkeypatch.setattr(uptime_alarm, "ALARM_STATE", alarm_state)
    monkeypatch.setattr(browser_lock, "LOCK", lock_file)

    return {
        "uptime": uptime_log,
        "alarm": alarm_log,
        "state": alarm_state,
        "lock": lock_file,
    }


def _probe_green(url, message, *, rtt_ms=80_000):
    """Stub: tool called, field matched, fast settle."""
    response = "get_status was called; llm_endpoint_bound is true"
    return response, rtt_ms


def _probe_slow(url, message, *, rtt_ms=163_000):
    """Stub: tool called, field matched, slow settle (SOFT_YELLOW territory)."""
    response = "get_status was called; llm_endpoint_bound is true"
    return response, rtt_ms


def _probe_no_tool(url, message, *, rtt_ms=9_000):
    """Stub: tool not invoked."""
    return "Here is a fictional workflow with 6 nodes...", rtt_ms


def _probe_empty(url, message, *, rtt_ms=5_000):
    """Stub: tool invoked but response is just the tool name + whitespace."""
    return "get_status   ", rtt_ms


def _probe_no_field(url, message, *, rtt_ms=10_000):
    """Stub: tool invoked but field not present in response."""
    return "get_status was called and returned something.", rtt_ms


def _alarm_lines(env):
    if not env["alarm"].is_file():
        return []
    return env["alarm"].read_text(encoding="utf-8").strip().splitlines()


def _log_lines(env):
    if not env["uptime"].is_file():
        return []
    return env["uptime"].read_text(encoding="utf-8").strip().splitlines()


# ---- exit-code + log-line tests --------------------------------------------


def test_green_exit_0_and_log_line(tmp_env):
    code = l2.run_probe("https://claude.ai/new", _probe_fn=_probe_green)
    assert code == 0
    lines = _log_lines(tmp_env)
    assert len(lines) == 1
    assert "GREEN layer=2" in lines[0]
    assert "tool_called=get_status" in lines[0]
    assert "rtt_ms=" in lines[0]


def test_soft_yellow_exit_0_and_soft_yellow_log(tmp_env):
    """exit=8 (SOFT_YELLOW) must exit 0 — it's a soft signal, not a failure."""
    code = l2.run_probe("https://claude.ai/new", _probe_fn=_probe_slow)
    assert code == 0
    lines = _log_lines(tmp_env)
    assert len(lines) == 1
    assert "SOFT_YELLOW layer=2" in lines[0]
    assert "exit=8" in lines[0]
    assert "settle_time_exceeded_150s" in lines[0]
    assert "tool_called=get_status" in lines[0]


def test_threshold_boundary_at_exactly_150000ms_is_green(tmp_env):
    """settle_ms == 150000 is still GREEN (strictly greater than triggers SOFT_YELLOW)."""
    code = l2.run_probe(
        "https://claude.ai/new",
        _probe_fn=lambda u, m: (_probe_green(u, m, rtt_ms=150_000)),
    )
    assert code == 0
    lines = _log_lines(tmp_env)
    assert "GREEN layer=2" in lines[0]
    assert "SOFT_YELLOW" not in lines[0]


def test_threshold_boundary_just_above_150000ms_is_soft_yellow(tmp_env):
    code = l2.run_probe(
        "https://claude.ai/new",
        _probe_fn=lambda u, m: (_probe_green(u, m, rtt_ms=150_001)),
    )
    assert code == 0
    lines = _log_lines(tmp_env)
    assert "SOFT_YELLOW layer=2" in lines[0]


def test_red_exit_10_tool_not_invoked(tmp_env):
    code = l2.run_probe("https://claude.ai/new", _probe_fn=_probe_no_tool)
    assert code == 10
    lines = _log_lines(tmp_env)
    assert "RED   layer=2" in lines[0]
    assert "exit=10" in lines[0]
    assert "tool_not_invoked" in lines[0]


def test_red_exit_12_field_not_matched(tmp_env):
    code = l2.run_probe("https://claude.ai/new", _probe_fn=_probe_no_field)
    assert code == 12
    lines = _log_lines(tmp_env)
    assert "exit=12" in lines[0]
    assert "field_not_matched" in lines[0]


def test_red_exit_13_browser_load_error(tmp_env):
    def boom(url, msg):
        raise l2.BrowserLoadError("503 from claude.ai")

    code = l2.run_probe("https://claude.ai/new", _probe_fn=boom)
    assert code == 13
    lines = _log_lines(tmp_env)
    assert "exit=13" in lines[0]


def test_red_exit_15_auth_expired(tmp_env):
    def boom(url, msg):
        raise l2.AuthExpiredError("login loop detected")

    code = l2.run_probe("https://claude.ai/new", _probe_fn=boom)
    assert code == 15
    lines = _log_lines(tmp_env)
    assert "exit=15" in lines[0]


def test_red_exit_99_unexpected_exception(tmp_env):
    def boom(url, msg):
        raise RuntimeError("disk on fire")

    code = l2.run_probe("https://claude.ai/new", _probe_fn=boom)
    assert code == 99
    lines = _log_lines(tmp_env)
    assert "exit=99" in lines[0]
    assert "unexpected" in lines[0]


# ---- Browser lock skip path ------------------------------------------------


def test_browser_lock_pid_probe_does_not_terminate_current_process():
    assert browser_lock._pid_alive(os.getpid()) is True


def test_skip_exit_0_when_lock_held(tmp_env):
    """When another owner holds the browser lock, probe exits 0 (SKIP)."""
    browser_lock.acquire("user-sim", "live-mission")
    try:
        code = l2.run_probe("https://claude.ai/new", _probe_fn=_probe_green)
        assert code == 0
        lines = _log_lines(tmp_env)
        assert len(lines) == 1
        assert "SKIP" in lines[0]
        assert "exit=14" in lines[0]
        assert "browser_lock_held_by_user-sim" in lines[0]
    finally:
        browser_lock.release("user-sim")


def test_skip_does_not_count_as_red_in_alarm(tmp_env):
    """SKIP lines must not trigger the RED alarm."""
    # Write two SKIP lines then evaluate — no alarm should fire.
    for _ in range(2):
        browser_lock.acquire("user-sim", "live-mission")
        try:
            l2.run_probe("https://claude.ai/new", _probe_fn=_probe_green)
        finally:
            browser_lock.release("user-sim")
    uptime_alarm.evaluate()
    assert _alarm_lines(tmp_env) == []


# ---- uptime_alarm SOFT_YELLOW path -----------------------------------------


def _write_log(env, lines):
    env["uptime"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def _soft_yellow(ts, rtt_ms=163_000):
    return (
        f"2026-04-20T{ts}-07:00 SOFT_YELLOW layer=2 url=https://claude.ai/new "
        f"exit=8 rtt_ms={rtt_ms} tool_called=get_status "
        f"reason='settle_time_exceeded_150s'"
    )


def _green2(ts, rtt_ms=80_000):
    return (
        f"2026-04-20T{ts}-07:00 GREEN layer=2 url=https://claude.ai/new "
        f"rtt_ms={rtt_ms} tool_called=get_status"
    )


def test_single_soft_yellow_does_not_alarm(tmp_env):
    _write_log(tmp_env, [_soft_yellow("10:00:00")])
    uptime_alarm.evaluate()
    assert _alarm_lines(tmp_env) == []


def test_two_consecutive_soft_yellows_emit_breadcrumb(tmp_env):
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    alarms = _alarm_lines(tmp_env)
    assert len(alarms) == 1
    assert "FABRICATION_MODE_SUSPECTED" in alarms[0]
    assert "url=https://claude.ai/new" in alarms[0]
    assert "consecutive_soft_yellows=2" in alarms[0]


def test_soft_yellow_breadcrumb_dedupes(tmp_env):
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    _write_log(tmp_env, [
        _soft_yellow("10:00:00"),
        _soft_yellow("11:00:00"),
        _soft_yellow("12:00:00"),
    ])
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_env)) == 1, "breadcrumb must dedupe on same URL"


def test_soft_yellow_state_resets_after_green(tmp_env):
    """After a green line, the soft-yellow counter resets so the next pair re-fires."""
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    baseline = len(_alarm_lines(tmp_env))

    # Green line resets the state.
    _write_log(tmp_env, [
        _soft_yellow("10:00:00"),
        _soft_yellow("11:00:00"),
        _green2("12:00:00"),
    ])
    uptime_alarm.evaluate()

    # New pair of soft yellows after the green should fire again.
    _write_log(tmp_env, [
        _green2("12:00:00"),
        _soft_yellow("13:00:00"),
        _soft_yellow("14:00:00"),
    ])
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_env)) == baseline + 1


def test_soft_yellow_does_not_trigger_public_mcp_outage_alarm(tmp_env):
    """SOFT_YELLOW must never produce a PUBLIC_MCP_OUTAGE alarm line."""
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    alarms = _alarm_lines(tmp_env)
    for line in alarms:
        assert "PUBLIC_MCP_OUTAGE" not in line


# ---- GHA-mode stdout mirroring (uptime_alarm._is_gha + _append_alarm) ------


def test_alarm_mirrored_to_stdout_in_gha_mode(tmp_env, monkeypatch, capsys):
    """When GITHUB_ACTIONS=true, _append_alarm echoes the alarm line to stdout."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    out = capsys.readouterr().out
    assert "[uptime-alarm]" in out
    assert "FABRICATION_MODE_SUSPECTED" in out


def test_alarm_not_mirrored_to_stdout_outside_gha(tmp_env, monkeypatch, capsys):
    """When GITHUB_ACTIONS is unset, _append_alarm must not emit to stdout."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_log(tmp_env, [_soft_yellow("10:00:00"), _soft_yellow("11:00:00")])
    uptime_alarm.evaluate()
    out = capsys.readouterr().out
    assert "[uptime-alarm]" not in out


def test_is_gha_true_variants(monkeypatch):
    """_is_gha() accepts 'true' and '1' for GITHUB_ACTIONS."""
    for val in ("true", "True", "TRUE", "1"):
        monkeypatch.setenv("GITHUB_ACTIONS", val)
        assert uptime_alarm._is_gha() is True


def test_is_gha_false_when_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert uptime_alarm._is_gha() is False


def test_is_gha_false_for_other_values(monkeypatch):
    for val in ("false", "0", "yes", "no", ""):
        monkeypatch.setenv("GITHUB_ACTIONS", val)
        assert uptime_alarm._is_gha() is False


def test_red_alarm_also_mirrored_in_gha_mode(tmp_env, monkeypatch, capsys):
    """RED PUBLIC_MCP_OUTAGE alarm is also mirrored to stdout in GHA mode."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    red_line = (
        "2026-04-20T10:00:00-07:00 RED   layer=1 url=https://tinyassets.io/mcp "
        "exit=1 rtt_ms=3000 reason='connect_timeout'"
    )
    _write_log(tmp_env, [red_line, red_line])  # 2 consecutive reds
    uptime_alarm.evaluate()
    out = capsys.readouterr().out
    assert "[uptime-alarm]" in out
    assert "PUBLIC_MCP_OUTAGE" in out


# ---- _real_browser_probe integration tests --------------------------------
#
# These don't drive a real browser. They exercise `_real_browser_probe` against
# a stubbed claude_chat.py subprocess so the canary's production path is
# protected from the regression class that broke it 2026-04-27 (calling
# `lead_browser.navigate()` when no such symbol existed). The 22 unit tests
# above all inject `_probe_fn`, bypassing this path entirely; without these
# tests, the production code can drift away from the actual scripts/
# module APIs and CI stays green.


def _stub_claude_chat(tmp_path, *, exit_code: int, trace_body: str | None = None,
                     stderr: str = "") -> Path:
    """Write a stub `claude_chat.py` that exits with `exit_code` and optionally
    writes `trace_body` to `<tmp>/output/claude_chat_trace.md` before exiting.

    Returns the directory path containing the stub script (caller monkeypatches
    `_REPO_ROOT` to this so subprocess invocation finds the stub instead of
    the real script).
    """
    repo = tmp_path / "stub_repo"
    (repo / "scripts").mkdir(parents=True)
    output_dir = repo / "output"
    output_dir.mkdir()
    trace_path = output_dir / "claude_chat_trace.md"
    if trace_body is not None:
        trace_path.write_text(trace_body, encoding="utf-8")

    # Append a fresh assistant block to the trace BEFORE exiting (mimics
    # cmd_ask flow). We only do this when caller didn't pre-populate.
    body_lines = ["import sys"]
    if trace_body is None and exit_code == 0:
        stub_block = (
            "\n\n## [2026-04-28T00:00:00.000-07:00] CLAUDE -> USER_SIM\n"
            "stub response calling get_status; llm_endpoint_bound=ok\n"
        )
        body_lines += [
            "from pathlib import Path",
            "trace = (Path(__file__).resolve().parent.parent / "
            "'output' / 'claude_chat_trace.md')",
            "trace.parent.mkdir(parents=True, exist_ok=True)",
            f"trace.write_text({stub_block!r}, encoding='utf-8')",
        ]
    if stderr:
        body_lines.append(f"sys.stderr.write({stderr!r})")
    body_lines.append(f"sys.exit({exit_code})")
    body = "\n".join(body_lines) + "\n"

    (repo / "scripts" / "claude_chat.py").write_text(body, encoding="utf-8")
    return repo


def test_real_browser_probe_green_with_subprocess_stub(tmp_path, monkeypatch):
    """Stub claude_chat exits 0 + writes a trace block → returns response + ms."""
    repo = _stub_claude_chat(tmp_path, exit_code=0)
    monkeypatch.setattr(l2, "_REPO_ROOT", repo)

    response, rtt_ms = l2._real_browser_probe(l2.PROBE_URL, "stub message")

    assert "get_status" in response
    assert "llm_endpoint_bound" in response
    assert isinstance(rtt_ms, int) and rtt_ms >= 0


def test_real_browser_probe_subprocess_nonzero_raises_browser_load_error(tmp_path, monkeypatch):
    """Stub claude_chat exits 3 (input-not-found) → _BrowserLoadError with stderr tail."""
    repo = _stub_claude_chat(tmp_path, exit_code=3, stderr="ERROR: chat input gone\n")
    monkeypatch.setattr(l2, "_REPO_ROOT", repo)

    with pytest.raises(l2._BrowserLoadError) as exc_info:
        l2._real_browser_probe(l2.PROBE_URL, "stub message")

    assert "exit=3" in str(exc_info.value)
    assert "chat input gone" in str(exc_info.value)


def test_real_browser_probe_exit_zero_no_trace_raises(tmp_path, monkeypatch):
    """Stub exits 0 but never writes a trace block → _BrowserLoadError."""
    # Create empty output dir without a trace file, and a stub that just exits 0.
    repo = tmp_path / "stub_repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "output").mkdir()
    (repo / "scripts" / "claude_chat.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setattr(l2, "_REPO_ROOT", repo)

    with pytest.raises(l2._BrowserLoadError) as exc_info:
        l2._real_browser_probe(l2.PROBE_URL, "stub message")

    assert "no assistant block" in str(exc_info.value)


def test_real_browser_probe_subprocess_timeout_raises(tmp_path, monkeypatch):
    """A subprocess timeout becomes _BrowserLoadError without starting a sleeper."""
    repo = _stub_claude_chat(tmp_path, exit_code=0)
    monkeypatch.setattr(l2, "_REPO_ROOT", repo)

    def raise_timeout(cmd, **kwargs):
        assert kwargs["timeout"] == l2._PROBE_SUBPROCESS_TIMEOUT_S
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    with pytest.raises(l2._BrowserLoadError) as exc_info:
        l2._real_browser_probe(l2.PROBE_URL, "stub message")

    assert "timed out" in str(exc_info.value)


# ---- API-contract regression tests ----------------------------------------
# These pin down the exact module attributes _real_browser_probe relies on.
# When claude_chat.py refactors, these surface the break BEFORE production
# (the 2026-04-27 incident was lead_browser.navigate disappearing silently;
# unit tests bypassed _real_browser_probe so CI stayed green).


def test_claude_chat_subprocess_invocation_shape():
    """The probe invokes `python claude_chat.py ask <message>`; verify ask subcommand exists."""
    import claude_chat
    # claude_chat exposes cmd_ask as the canonical entry the canary's subprocess
    # form drives via the `ask` argparse subparser. If this disappears, the
    # canary's subprocess will exit 2 (argparse error) before any browser work.
    assert hasattr(claude_chat, "cmd_ask"), (
        "claude_chat.cmd_ask is the canonical message-send entry"
    )


def test_trace_file_path_is_canonical():
    """The probe reads `output/claude_chat_trace.md`; verify claude_chat writes there."""
    import claude_chat
    expected = (
        Path(claude_chat.__file__).resolve().parent.parent
        / "output" / "claude_chat_trace.md"
    )
    assert claude_chat.TRACE.resolve() == expected.resolve(), (
        "claude_chat.TRACE diverged from output/claude_chat_trace.md; "
        "uptime_canary_layer2._read_last_assistant_block must be updated to match."
    )


def test_trace_assistant_block_marker_is_canonical(tmp_path):
    """Verify _append_trace emits the marker the canary scans for."""
    # The canary's _read_last_assistant_block scans for `] CLAUDE -> USER_SIM\n`.
    # Pin that marker via _append_trace's actual output, redirected to tmp_path
    # so we don't pollute the real trace file.
    import claude_chat as _cc
    fake_path = tmp_path / "_test_trace_probe.md"
    saved = _cc.TRACE
    _cc.TRACE = fake_path
    try:
        _cc._append_trace("CLAUDE -> USER_SIM", "probe body")
        written = fake_path.read_text(encoding="utf-8")
    finally:
        _cc.TRACE = saved

    assert "] CLAUDE -> USER_SIM\n" in written, (
        "claude_chat._append_trace no longer emits the "
        "`] CLAUDE -> USER_SIM\\n` marker; "
        "uptime_canary_layer2._read_last_assistant_block parses on it."
    )
