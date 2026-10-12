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


def test_timeline_joins_cookie_oauth_account_and_checkout():
    cookie = "s_" + "a" * 32
    oauth = "o_" + "b" * 16
    checkout = "c_" + "c" * 16
    events = [
        _event(START, session_id=cookie, trace_id="trace-page", step="page.load",
               links={"cookie": cookie, "oauth_state": oauth, "checkout": checkout}),
        _event(START + timedelta(minutes=1), session_id="s_" + "d" * 32, trace_id="trace-back",
               step="oauth.callback", links={"oauth_state": oauth},
               context={"client": "browser", "account_id": "user_1", "signed_in": True}),
        _event(START + timedelta(minutes=2), session_id="mcp-session", trace_id="trace-tools",
               step="mcp.tools_list",
               context={"client": "claude", "account_id": "user_1", "signed_in": True}),
        _event(START + timedelta(minutes=3), session_id="s_" + "e" * 32, trace_id="trace-hook",
               step="checkout.paid", links={"checkout": checkout}, commit="b" * 40,
               error={"type": "HTTP500", "stack": ""}),
        _event(
            START + timedelta(days=3), session_id=cookie, trace_id="trace-late", step="page.load",
        ),
        _event(START + timedelta(minutes=4), session_id="s_" + "f" * 32, trace_id="trace-other",
               step="http", context={"client": "other", "account_id": "user_2", "signed_in": True}),
    ]
    chosen, truncated = reader.select_timeline(
        events, since=START, until=END, session=cookie,
    )
    assert truncated is False
    assert [event["trace_id"] for event in chosen] == [
        "trace-page", "trace-back", "trace-tools", "trace-hook",
    ]
    by_trace, _ = reader.select_timeline(
        events, since=START, until=END, trace="trace-tools",
    )
    assert {event["trace_id"] for event in by_trace} == {
        "trace-page", "trace-back", "trace-tools", "trace-hook",
    }
    by_account, _ = reader.select_timeline(
        events, since=START, until=END, account="user_1",
    )
    assert "trace-other" not in {event["trace_id"] for event in by_account}
    assert "trace-late" not in {event["trace_id"] for event in by_account}
    window, _ = reader.select_timeline(events, since=START, until=END)
    assert [event["trace_id"] for event in window] == [
        "trace-page", "trace-back", "trace-tools", "trace-hook", "trace-other",
    ]


def test_grep_runs_after_the_trail_is_joined_and_summary_counts_by_commit():
    cookie = "s_" + "a" * 32
    checkout = "c_" + "c" * 16
    events = [
        _event(START, session_id=cookie, trace_id="trace-page", step="checkout.open",
               links={"checkout": checkout}),
        _event(START + timedelta(minutes=1), session_id="s_" + "e" * 32,
               trace_id="trace-hook", step="checkout.paid", links={"checkout": checkout},
               commit="b" * 40, error={"type": "StripeError", "stack": "needle-only-here"}),
    ]
    chosen, _ = reader.select_timeline(
        events, since=START, until=END, session=cookie, grep="needle-only-here",
    )
    assert [event["trace_id"] for event in chosen] == ["trace-hook"]
    counts = reader.summary_for(events, truncated=False)
    assert counts["errors_by_commit"]["b" * 40]["StripeError"] == 1
    assert counts["lines"] == 2


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
