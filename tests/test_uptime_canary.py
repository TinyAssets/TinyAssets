"""Tests for the uptime canary Layer-1 probe + alarm evaluator.

Spec: 2026-04-19-uptime-canary-layered.md (archived in git). Exercises:
    - probe wrapper format (green + red lines)
    - log rotation boundary
    - alarm evaluator escalation table (0/1/2+ reds, recovery, dedupe)

Stdlib-only; no live MCP or network required.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mcp_public_canary  # noqa: E402
import uptime_alarm  # noqa: E402
import uptime_canary  # noqa: E402


@pytest.fixture(autouse=True)
def _canary_token(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "t" * 40)
    monkeypatch.setattr(
        mcp_public_canary, "canary_bearer_for", lambda url, prog, timeout=10.0: "t" * 40
    )


def test_missing_token_exits_before_network(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    monkeypatch.setattr(
        uptime_canary, "probe_result", lambda *a, **k: calls.append((a, k)),
    )
    with pytest.raises(SystemExit) as exc:
        uptime_canary.main([])
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err


@pytest.fixture
def tmp_log_env(tmp_path, monkeypatch):
    """Redirect canary + alarm I/O to tmp_path."""
    uptime_log = tmp_path / "uptime.log"
    alarm_log = tmp_path / "uptime_alarms.log"
    alarm_state = tmp_path / ".uptime_alarm_state.json"
    monkeypatch.setattr(uptime_canary, "LOG_PATH", uptime_log)
    monkeypatch.setattr(uptime_alarm, "UPTIME_LOG", uptime_log)
    monkeypatch.setattr(uptime_alarm, "ALARM_LOG", alarm_log)
    monkeypatch.setattr(uptime_alarm, "ALARM_STATE", alarm_state)
    return {"uptime": uptime_log, "alarm": alarm_log, "state": alarm_state}


# ---- canonical URL assertion -------------------------------------------------


def test_default_url_is_canonical():
    """DEFAULT_URL must be the single canonical public endpoint.

    Host directive 2026-04-20: retire mcp.tinyassets.io/mcp as a public URL.
    Only tinyassets.io/mcp (via Cloudflare Worker) is the canonical entry point.
    """
    assert uptime_canary.DEFAULT_URL == "https://tinyassets.io/mcp", (
        "DEFAULT_URL must be the canonical apex URL, not the direct tunnel. "
        "Host directive 2026-04-20 retired mcp.tinyassets.io/mcp as a public URL."
    )


# ---- uptime_canary.run_probe ----------------------------------------------


def test_run_probe_logs_green_on_success(tmp_log_env, monkeypatch):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    code = uptime_canary.run_probe("https://example/mcp", 5.0)
    assert code == 0
    text = tmp_log_env["uptime"].read_text(encoding="utf-8")
    assert "GREEN layer=1 url=https://example/mcp" in text
    assert "rtt_ms=" in text


def test_run_probe_logs_red_on_canary_error(tmp_log_env, monkeypatch):
    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(2, f"HTTP 404 from {url}: Not Found")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    code = uptime_canary.run_probe("https://broken/mcp", 5.0)
    assert code == 2
    text = tmp_log_env["uptime"].read_text(encoding="utf-8")
    assert "RED" in text
    assert "exit=2" in text
    assert "HTTP 404" in text


def test_run_probe_never_crashes_on_unexpected_exception(tmp_log_env, monkeypatch):
    def boom(url, timeout):
        raise RuntimeError("out of left field")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    code = uptime_canary.run_probe("https://x/mcp", 5.0)
    assert code == 99
    text = tmp_log_env["uptime"].read_text(encoding="utf-8")
    assert "RED" in text
    assert "exit=99" in text
    assert "unexpected" in text


def test_run_probe_log_line_is_single_line(tmp_log_env, monkeypatch):
    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(1, "multi\nline\nreason")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    uptime_canary.run_probe("https://x/mcp", 1.0)
    lines = tmp_log_env["uptime"].read_text(encoding="utf-8").splitlines()
    # Exactly one log line emitted; newlines in reason were collapsed.
    assert len(lines) == 1


def test_rotate_when_over_limit(tmp_log_env, monkeypatch):
    # Write a giant pre-existing log, then probe once — it must rotate.
    tmp_log_env["uptime"].write_bytes(b"x" * (uptime_canary.LOG_ROTATE_BYTES + 1))
    monkeypatch.setattr(uptime_canary, "probe_result", lambda u, t: None)
    uptime_canary.run_probe("https://x/mcp", 1.0)
    backup = tmp_log_env["uptime"].with_suffix(".log.1")
    assert backup.is_file()
    # New log has one green entry, not the giant payload.
    new_text = tmp_log_env["uptime"].read_text(encoding="utf-8")
    assert new_text.count("\n") == 1
    assert "GREEN" in new_text


# ---- --format=gha + --once CLI flags -------------------------------------


def test_gha_format_green_emits_status_zero(tmp_log_env, monkeypatch, capsys):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    code = uptime_canary.run_probe("https://example/mcp", 5.0, fmt="gha")
    assert code == 0
    out = capsys.readouterr().out
    assert "status=0" in out
    assert "msg=" in out


def test_gha_format_red_emits_nonzero_status(tmp_log_env, monkeypatch, capsys):
    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(2, "HTTP 503 from server")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    code = uptime_canary.run_probe("https://x/mcp", 5.0, fmt="gha")
    assert code == 2
    out = capsys.readouterr().out
    assert "status=2" in out
    assert "msg=" in out
    assert "HTTP 503" in out


def test_gha_format_unexpected_error(tmp_log_env, monkeypatch, capsys):
    def boom(url, timeout):
        raise RuntimeError("disk full")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    code = uptime_canary.run_probe("https://x/mcp", 5.0, fmt="gha")
    assert code == 99
    out = capsys.readouterr().out
    assert "status=99" in out
    assert "msg=" in out


def test_gha_format_still_writes_log(tmp_log_env, monkeypatch, capsys):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    uptime_canary.run_probe("https://example/mcp", 5.0, fmt="gha")
    assert tmp_log_env["uptime"].is_file()
    assert "GREEN" in tmp_log_env["uptime"].read_text(encoding="utf-8")


def test_gha_multiline_reason_uses_heredoc(tmp_log_env, monkeypatch, capsys):
    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(1, "line1\nline2")
    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    uptime_canary.run_probe("https://x/mcp", 5.0, fmt="gha")
    out = capsys.readouterr().out
    # Delimiter is now random (EOF_<hex>) — just check that heredoc syntax is present.
    assert "msg<<EOF_" in out
    assert "line1" in out
    assert "line2" in out


def test_once_flag_is_accepted(tmp_log_env, monkeypatch):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    code = uptime_canary.main(["--url", "https://example/mcp", "--once"])
    assert code == 0


def test_format_gha_via_cli(tmp_log_env, monkeypatch, capsys):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    code = uptime_canary.main([
        "--url", "https://example/mcp",
        "--once",
        "--format", "gha",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "status=0" in out


def test_format_log_default_no_stdout(tmp_log_env, monkeypatch, capsys):
    monkeypatch.setattr(uptime_canary, "probe_result", lambda url, timeout: None)
    uptime_canary.run_probe("https://example/mcp", 5.0, fmt="log")
    out = capsys.readouterr().out
    assert out == ""


# ---- uptime_alarm.evaluate ------------------------------------------------


def _write_log(env, lines: list[str]) -> None:
    env["uptime"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def _red(ts: str, exit_code: int = 2, reason: str = "HTTP 404") -> str:
    return (
        f"2026-04-19T{ts}-07:00 RED   layer=1 url=https://x/mcp "
        f"exit={exit_code} rtt_ms=300 reason={reason!r}"
    )


def _green(ts: str, rtt_ms: int = 200) -> str:
    return f"2026-04-19T{ts}-07:00 GREEN layer=1 url=https://x/mcp rtt_ms={rtt_ms}"


def _skip(ts: str) -> str:
    return (
        f"2026-04-19T{ts}-07:00 SKIP  layer=1 url=https://x/mcp "
        "exit=14 reason='probe_skipped'"
    )


def _alarm_lines(env) -> list[str]:
    if not env["alarm"].is_file():
        return []
    return env["alarm"].read_text(encoding="utf-8").strip().splitlines()


def test_single_red_does_not_alarm(tmp_log_env):
    _write_log(tmp_log_env, [_green("17:30:00", 120), _red("17:32:00")])
    uptime_alarm.evaluate()
    assert _alarm_lines(tmp_log_env) == []


def test_two_consecutive_reds_alarm(tmp_log_env):
    _write_log(tmp_log_env, [
        _green("17:30:00", 120),
        _red("17:32:00"),
        _red("17:34:00"),
    ])
    uptime_alarm.evaluate()
    alarms = _alarm_lines(tmp_log_env)
    assert len(alarms) == 1
    assert "ALARM PUBLIC_MCP_OUTAGE" in alarms[0]
    assert "url=https://x/mcp" in alarms[0]
    assert "exit=2" in alarms[0]


def test_sustained_reds_dedupe(tmp_log_env):
    _write_log(tmp_log_env, [_red("17:32:00"), _red("17:34:00")])
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_log_env)) == 1

    # Append more reds of the same kind; alarm should NOT re-fire.
    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_red("17:36:00") + "\n")
        fp.write(_red("17:38:00") + "\n")
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_log_env)) == 1, "dedupe by (url, exit) fingerprint must hold"


def test_different_exit_code_re_alarms(tmp_log_env):
    """If the failure mode changes exit codes mid-outage, emit a new alarm."""
    _write_log(tmp_log_env, [_red("17:32:00"), _red("17:34:00")])
    uptime_alarm.evaluate()
    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_red("17:36:00", exit_code=1, reason="non-MCP body") + "\n")
        fp.write(_red("17:38:00", exit_code=1, reason="non-MCP body") + "\n")
    uptime_alarm.evaluate()
    alarms = _alarm_lines(tmp_log_env)
    assert len(alarms) == 2
    assert "exit=2" in alarms[0]
    assert "exit=1" in alarms[1]


def test_recovery_emits_recovered_line(tmp_log_env):
    _write_log(tmp_log_env, [_red("17:32:00"), _red("17:34:00")])
    uptime_alarm.evaluate()  # alarm fires
    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_green("17:36:00") + "\n")
    uptime_alarm.evaluate()  # recovery fires
    alarms = _alarm_lines(tmp_log_env)
    assert len(alarms) == 2
    assert "ALARM" in alarms[0]
    assert "RECOVERED" in alarms[1]
    assert "url=https://x/mcp" in alarms[1]


def test_recovery_waits_for_green_not_skip(tmp_log_env):
    _write_log(tmp_log_env, [_red("17:32:00"), _red("17:34:00")])
    uptime_alarm.evaluate()  # alarm fires

    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_skip("17:36:00") + "\n")
    uptime_alarm.evaluate()

    alarms = _alarm_lines(tmp_log_env)
    assert len(alarms) == 1
    assert "RECOVERED" not in "\n".join(alarms)

    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_green("17:38:00") + "\n")
    uptime_alarm.evaluate()

    alarms = _alarm_lines(tmp_log_env)
    assert len(alarms) == 2
    assert "RECOVERED" in alarms[1]


def test_recovery_resets_dedupe_so_next_red_alarms(tmp_log_env):
    _write_log(tmp_log_env, [
        _red("17:32:00"),
        _red("17:34:00"),
        _green("17:36:00"),
    ])
    uptime_alarm.evaluate()  # alarm + recovered both fire across two writes
    uptime_alarm.evaluate()  # idempotent — nothing new
    baseline = len(_alarm_lines(tmp_log_env))

    with tmp_log_env["uptime"].open("a", encoding="utf-8") as fp:
        fp.write(_red("17:38:00") + "\n")
        fp.write(_red("17:40:00") + "\n")
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_log_env)) == baseline + 1


def test_empty_log_is_no_op(tmp_log_env):
    # No log file yet — evaluator must not crash.
    result = uptime_alarm.evaluate()
    assert result == 0
    assert not tmp_log_env["alarm"].exists()


def test_malformed_log_lines_ignored(tmp_log_env):
    _write_log(tmp_log_env, [
        "garbled nonsense",
        _red("17:32:00"),
        "!@#$%^",
        _red("17:34:00"),
    ])
    uptime_alarm.evaluate()
    assert len(_alarm_lines(tmp_log_env)) == 1


# ---- heredoc delimiter hardening -------------------------------------------


def test_emit_gha_kv_delimiter_is_randomized(tmp_log_env, monkeypatch, capsys):
    """Each _emit_gha_kv call must use a distinct delimiter."""
    delimiters = []

    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(1, "a\nb")

    monkeypatch.setattr(uptime_canary, "probe_result", boom)

    # Collect delimiters from two separate probe calls.
    for _ in range(2):
        uptime_canary.run_probe("https://x/mcp", 5.0, fmt="gha")
        out = capsys.readouterr().out
        for line in out.splitlines():
            if line.startswith("msg<<EOF_"):
                delimiters.append(line[len("msg<<"):])

    assert len(delimiters) == 2, "expected one heredoc delimiter per call"
    assert delimiters[0] != delimiters[1], "delimiters must differ across calls"


def test_emit_gha_kv_literal_canary_eof_in_msg_does_not_truncate(
    tmp_log_env, monkeypatch, capsys
):
    """Probe output containing a bare CANARY_EOF line must not corrupt the output."""
    # The message contains the old static delimiter as a literal string.
    poisoned_msg = "start\nCANARY_EOF\nend"

    def boom(url, timeout):
        raise mcp_public_canary.CanaryError(1, poisoned_msg)

    monkeypatch.setattr(uptime_canary, "probe_result", boom)
    uptime_canary.run_probe("https://x/mcp", 5.0, fmt="gha")
    out = capsys.readouterr().out

    # All three payload lines must be present in the output.
    assert "start" in out
    assert "CANARY_EOF" in out  # literal string — OK inside random-delimited heredoc
    assert "end" in out

    # The delimiter that opened the block must also close it.
    lines = out.splitlines()
    open_line = next((ln for ln in lines if ln.startswith("msg<<EOF_")), None)
    assert open_line is not None, "heredoc open line not found"
    delim = open_line[len("msg<<"):]
    # The bare delimiter (close marker) must appear exactly once as a standalone line.
    assert lines.count(delim) == 1, (
        f"closing delimiter {delim!r} should appear exactly once as a standalone line"
    )


# Defensive: make sure re-importing clears module-level sys.path shim cleanly.
def test_modules_are_stable_on_reimport():
    importlib.reload(uptime_canary)
    importlib.reload(uptime_alarm)
