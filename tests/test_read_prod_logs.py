"""The read-only timeline joins a person and refuses anything else."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "read_prod_logs", ROOT / "deploy" / "read-prod-logs.py",
)
reader = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(reader)

START = datetime(2026, 10, 12, 17, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=2)


def _event(stamp, **fields):
    base = {
        "schema": reader.SCHEMA,
        "ts": stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "event": "session.step",
        "session_id": None,
        "trace_id": "trace-0001",
        "commit": "a" * 40,
    }
    base.update(fields)
    return base


def test_command_parser_rejects_shell_metacharacters():
    ok = reader.parse_command([
        "read-logs", "--since", "2026-10-12T17:00:00Z", "--until", "2026-10-12T18:00:00Z",
    ])
    assert ok["since"] == "2026-10-12T17:00:00Z"
    with pytest_raises():
        reader.parse_command([
            "read-logs", "--since", "2026-10-12T17:00:00Z",
            "--until", "2026-10-12T18:00:00Z", "&&", "whoami",
        ])
    with pytest_raises():
        reader.parse_command([
            "read-logs", "--since", "2026-10-12T17:00:00Z;whoami",
            "--until", "2026-10-12T18:00:00Z",
        ])
    with pytest_raises():
        reader.parse_command([
            "read-logs", "--since", "2026-10-12T17:00:00Z",
            "--until", "2026-10-12T18:00:00Z", "--session", "user@example.com",
        ])
    with pytest_raises():
        reader.parse_command([
            "read-logs", "--since", "2026-10-12T17:00:00Z",
            "--until", "2026-10-19T17:00:01Z",
        ])


def pytest_raises():
    import pytest
    return pytest.raises(reader.QueryError)


def test_timeline_joins_only_through_the_signed_cookie_and_the_account():
    cookie = "s_" + "a" * 32
    oauth = "o_" + "b" * 16
    checkout = "c_" + "c" * 16
    events = [
        _event(START, session_id=cookie, trace_id="trace-page", step="page.load",
               links={"cookie": cookie, "oauth_state": oauth, "checkout": checkout}),
        # Same cookie after sign-in: the account now joins the MCP client.
        _event(START + timedelta(minutes=1), session_id=cookie, trace_id="trace-back",
               step="oauth.callback", links={"cookie": cookie},
               context={"client": "browser", "account_id": "user_1", "signed_in": True}),
        _event(START + timedelta(minutes=2), session_id="mcp-session", trace_id="trace-tools",
               step="mcp.tools_list",
               context={"client": "claude", "account_id": "user_1", "signed_in": True}),
        # Request-supplied links never join: a caller can name anyone's value.
        _event(START + timedelta(minutes=3), session_id="s_" + "e" * 32, trace_id="trace-hook",
               step="checkout.paid", links={"checkout": checkout, "oauth_state": oauth,
                                            "mcp_session": "mcp-session"},
               commit="b" * 40, error={"type": "HTTP500", "stack": ""}),
        _event(
            START + timedelta(days=3), session_id=cookie, trace_id="trace-late", step="page.load",
        ),
        _event(START + timedelta(minutes=4), session_id="s_" + "f" * 32, trace_id="trace-other",
               step="http", context={"client": "other", "account_id": "user_2", "signed_in": True}),
        # A caller reusing another request's trace id names requests, not a trail.
        _event(START + timedelta(minutes=5), session_id="s_" + "9" * 32, trace_id="trace-tools",
               step="http"),
    ]
    chosen, truncated = reader.select_timeline(
        events, since=START, until=END, session=cookie,
    )
    assert truncated is False
    assert [event["trace_id"] for event in chosen] == [
        "trace-page", "trace-back", "trace-tools",
    ]
    by_trace, _ = reader.select_timeline(
        events, since=START, until=END, trace="trace-tools",
    )
    assert [event["step"] for event in by_trace] == ["mcp.tools_list", "http"]
    # With a session, the trace only narrows the seed; a colliding trace id
    # from another trail is never admitted or expanded.
    both, _ = reader.select_timeline(
        events, since=START, until=END, session="mcp-session", trace="trace-tools",
    )
    assert "s_" + "9" * 32 not in {event["session_id"] for event in both}
    by_account, _ = reader.select_timeline(
        events, since=START, until=END, account="user_1",
    )
    assert {event["trace_id"] for event in by_account} == {
        "trace-page", "trace-back", "trace-tools",
    }
    window, _ = reader.select_timeline(events, since=START, until=END)
    assert len(window) == 6


def test_grep_runs_after_the_trail_is_joined_and_summary_counts_by_commit():
    cookie = "s_" + "a" * 32
    checkout = "c_" + "c" * 16
    events = [
        _event(START, session_id=cookie, trace_id="trace-page", step="checkout.open",
               links={"checkout": checkout}, context={"account_id": "user_1"}),
        _event(START + timedelta(minutes=1), session_id="s_" + "e" * 32,
               trace_id="trace-hook", step="checkout.paid", links={"checkout": checkout},
               context={"account_id": "user_1"},
               commit="b" * 40, error={"type": "StripeError", "stack": "needle-only-here"}),
    ]
    chosen, _ = reader.select_timeline(
        events, since=START, until=END, session=cookie, grep="needle-only-here",
    )
    assert [event["trace_id"] for event in chosen] == ["trace-hook"]
    counts = reader.summary_for(events, truncated=False)
    assert counts["errors_by_commit"]["b" * 40]["StripeError"] == 1
    assert counts["lines"] == 2


def test_public_summary_never_keys_on_a_browser_supplied_error_type():
    cookie = "s_" + "a" * 32
    browser = {"client": "browser", "account_id": None, "signed_in": False}
    idlike = "s_" + "b" * 32
    events = [
        _event(START, event="client.error", session_id=cookie, trace_id="trace-beacon",
               step="client.error", context=browser, error={"type": idlike, "stack": ""}),
        _event(START + timedelta(minutes=1), event="client.error", session_id=cookie,
               trace_id="trace-beacon-2", step="client.error", context=browser,
               error={"type": "TypeError", "stack": "at render (x.js:1:2)"}),
        _event(START + timedelta(minutes=2), session_id=cookie, trace_id="trace-page",
               step="page.load", context=browser),
        _event(START + timedelta(minutes=3), session_id=cookie, trace_id="trace-http",
               step="http", context=browser, http={"status": 500},
               error={"type": "HTTP500", "stack": ""}),
        _event(START + timedelta(minutes=4), session_id="s_" + "e" * 32, trace_id="trace-hook",
               step="checkout.paid", error={"type": "StripeError", "stack": ""}),
    ]
    summary = reader.summary_for(events, truncated=False)
    counts = summary["errors_by_commit"]["a" * 40]
    assert counts == {reader.CLIENT_ERROR_KIND: 2, "HTTP500": 1, "StripeError": 1}
    rendered = json.dumps(summary)
    assert idlike not in rendered
    assert "TypeError" not in rendered
    assert summary["lines"] == 5


def test_unwrap_and_render_keep_redaction():
    inner = _event(START, session_id="s_" + "a" * 32, trace_id="trace-0001")
    wrapped = json.dumps({"log": json.dumps(inner)})
    assert reader.unwrap_line(wrapped)["trace_id"] == "trace-0001"
    leaked = dict(inner)
    leaked["error"] = {"type": "Error", "stack": "bearer " + "z" * 20}
    rendered = reader.render(leaked)
    assert "zzzz" not in rendered
    assert "bearer [redacted]" in rendered


def test_journal_argv_is_fixed_and_not_a_shell():
    argv = reader.journal_argv(START, END)
    assert "whoami" not in argv
    assert argv[-2:] == ["-o", "cat"]
    assert "CONTAINER_NAME=tinyassets-logs" in argv
    assert any(part.startswith("2026-10-12 17:00:00") for part in argv)


def test_redaction_covers_cookies_cards_and_stripe_ids():
    cookie = reader.redact("Cookie: workos_session=abc123xyz")
    assert "abc123xyz" not in cookie
    assert cookie.startswith("Cookie: [redacted]")
    pair = reader.redact("('4111111111111111', 'cus_123456789')")
    assert "4111111111111111" not in pair
    assert "cus_123456789" not in pair
    assert pair == "('[redacted]', '[redacted]')"
    assert "s_" + "a" * 32 not in reader.redact("ta_session=s_" + "a" * 32 + "; Path=/")
    assert "trace-0001 HTTP500 at /app" == reader.redact("trace-0001 HTTP500 at /app")


def _fake_popen(line: bytes, *, lines: int | None = None, returncode: int = 0):
    """A Popen stand-in whose stdout never ends unless ``lines`` is set."""

    class Stdout:
        def __init__(self) -> None:
            self.served = 0
            self.closed = False

        def readline(self, limit: int = -1) -> bytes:
            if lines is not None and self.served >= lines:
                return b""
            self.served += 1
            return line

        def close(self) -> None:
            self.closed = True

    class Process:
        instances: list = []

        def __init__(self, argv, **kwargs) -> None:
            assert argv[-2:] == ["-o", "cat"]
            self.stdout = Stdout()
            self.returncode = None if lines is None else returncode
            self.terminated = False
            Process.instances.append(self)

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    return Process


def test_journal_is_streamed_and_stopped_at_the_byte_cap(monkeypatch):
    line = (json.dumps(_event(START, session_id="s_" + "a" * 32)) + "\n").encode()
    process_type = _fake_popen(line)
    monkeypatch.setattr(reader.subprocess, "Popen", process_type)
    monkeypatch.setattr(reader, "_READ_BYTES", len(line) * 50)
    events, truncated = reader.read_journal(START, END)
    process = process_type.instances[-1]
    assert truncated is True
    assert len(events) == 50
    assert process.stdout.served == 51
    assert process.terminated is True
    assert process.stdout.closed is True


def test_journal_stops_at_the_event_cap_and_reports_a_failed_read(monkeypatch):
    line = (json.dumps(_event(START, session_id="s_" + "a" * 32)) + "\n").encode()
    process_type = _fake_popen(line)
    monkeypatch.setattr(reader.subprocess, "Popen", process_type)
    monkeypatch.setattr(reader, "_READ_EVENTS", 7)
    events, truncated = reader.read_journal(START, END)
    assert (len(events), truncated) == (7, True)
    assert process_type.instances[-1].stdout.served == 7

    finite = _fake_popen(line, lines=3)
    monkeypatch.setattr(reader.subprocess, "Popen", finite)
    events, truncated = reader.read_journal(START, END)
    assert (len(events), truncated) == (3, False)
    assert finite.instances[-1].terminated is False

    failed = _fake_popen(line, lines=3, returncode=1)
    monkeypatch.setattr(reader.subprocess, "Popen", failed)
    with pytest_raises():
        reader.read_journal(START, END)
