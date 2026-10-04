"""Harness S4: the owner watches the agent's tools work, live.

Founder, 2026-10-01: the app should show the agent working the way Claude Code
shows each tool as it runs. On production that day ``agent_turn_tools`` held
109 rows, all from the HTTP loop and none from a native CLI turn, so nobody
could see what a turn called (design #4172 §3.2).

Every adapter's tools run through the per-universe engine process, so the
engine records each call; the owner's status carries the running turn's latest
calls in their OWN thread; the page says the newest one on its one status line.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import agent_activity, engine_tool_activity

THREAD = "thread:principal:owner-1"


def _universe(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "u-alpha"
    path.mkdir(parents=True)
    return path


def test_a_call_is_recorded_running_then_done_with_its_summary(tmp_path):
    universe = _universe(tmp_path)
    call = agent_activity.started(universe, THREAD, "bash", "pytest -q tests/x.py")
    [running] = agent_activity.recent(universe, THREAD)
    assert running["tool"] == "bash" and running["summary"] == "pytest -q tests/x.py"
    assert running["state"] == "running"
    agent_activity.finished(universe, call, ok=True)
    [done] = agent_activity.recent(universe, THREAD)
    assert done["state"] == "done" and "took_s" in done


def test_a_failure_keeps_only_the_first_line_of_its_real_cause(tmp_path):
    universe = _universe(tmp_path)
    call = agent_activity.started(universe, THREAD, "write_graph", "target=automation")
    agent_activity.finished(universe, call, ok=False,
                            error="refused schedule id unknown\nTraceback ...")
    [failed] = agent_activity.recent(universe, THREAD)
    assert failed["state"] == "failed"
    assert failed["error"] == "refused schedule id unknown"


def test_newest_first_per_session_and_bounded(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(agent_activity, "KEEP_PER_SESSION", 3)
    for index in range(5):
        agent_activity.started(universe, THREAD, "read", f"notes/{index}.md")
    agent_activity.started(universe, "node:b:agent", "bash", "background work")
    rows = agent_activity.recent(universe, THREAD, limit=10)
    assert [r["summary"] for r in rows] == ["notes/4.md", "notes/3.md", "notes/2.md"]
    assert all(r["summary"] != "background work" for r in rows)


def test_since_drops_calls_from_an_earlier_turn(tmp_path):
    universe = _universe(tmp_path)
    agent_activity.started(universe, THREAD, "bash", "last turn")
    cut = time.time() + 0.001
    time.sleep(0.01)
    agent_activity.started(universe, THREAD, "bash", "this turn")
    assert [r["summary"] for r in agent_activity.recent(universe, THREAD, since=cut)] == [
        "this turn"]


@pytest.mark.parametrize("tool, args, line", [
    ("bash", {"command": "pip install   rich"}, "pip install \u2026"),
    ("bash", {"command": "pytest -q tests/x.py"}, "pytest \u2026"),
    ("bash", {"command": "git status"}, "git status"),
    ("bash", {"command": "/usr/bin/python3 -m http.server"}, "python3 \u2026"),
    ("read", {"path": "wiki/pages/a.md"}, "wiki/pages/a.md"),
    ("write_graph", {"target": "automation", "x": 1}, "target=automation"),
    ("get_status", {}, ""),
])
def test_the_summary_comes_from_the_calls_own_arguments(tool, args, line):
    assert agent_activity.summarize(tool, args) == line


def test_a_long_command_shows_its_first_words_only():
    out = agent_activity.summarize("bash", {"command": " ".join(["echo"] * 40)})
    assert out.endswith("…") and len(out) <= agent_activity.MAX_LINE


# Synthetic credentials in every shape a command or an error can carry them.
_URL_SECRET = "https://alice:example-secret@api.example.com/v1?access_token=example-token"
_SECRETS = ("example-secret", "example-token", "alice", "sk_live_" + "Z" * 24,
            "ghp_" + "a1" * 18)


@pytest.mark.parametrize("command", [
    f"curl {_URL_SECRET}",
    "curl -H 'Authorization: Bearer sk_live_" + "Z" * 24 + "' https://api.example.com",
    "export TOKEN=example-token && run",
    "git clone https://ghp_" + "a1" * 18 + "@github.com/o/r.git",
    "python tool.py --api-key=example-token",
])
def test_a_command_never_stores_a_credential(command):
    out = agent_activity.summarize("bash", {"command": command})
    assert not any(secret in out for secret in _SECRETS), out


def test_a_command_line_keeps_its_program_and_subcommand_only():
    """Shown live, also to UIs the owner builds (read_live): nothing past the
    program and a plain-word subcommand, not even a path or a host."""
    for command, line in [
        (f"curl {_URL_SECRET}", "curl \u2026"),
        ("cat ~/.ssh/id_rsa", "cat \u2026"),
        ("cat .ssh/id_rsa", "cat \u2026"),
        ("git push origin main", "git push \u2026"),
        ("TOKEN=example-token run", "\u2026"),
        ("sk_live_" + "Z" * 24, "\u2026"),
    ]:
        assert agent_activity.summarize("bash", {"command": command}) == line, command


def test_an_error_line_never_stores_a_credential(tmp_path):
    universe = _universe(tmp_path)
    call = agent_activity.started(universe, THREAD, "bash", "curl")
    agent_activity.finished(universe, call, ok=False,
                            error=f"connect failed: {_URL_SECRET} (403)\nmore")
    [row] = agent_activity.recent(universe, THREAD)
    assert not any(secret in row["error"] for secret in _SECRETS), row["error"]
    assert "https://api.example.com" in row["error"]


def test_the_store_lives_outside_the_universe_folder(tmp_path):
    universe = _universe(tmp_path)
    agent_activity.started(universe, THREAD, "bash", "ls")
    assert not any(universe.rglob("activity.db"))
    assert (tmp_path / "data" / ".agent-sessions" / "u-alpha" / "activity.db").exists()


# -- the engine records every call of a named session ---------------------------


def _result(text: str, *, error: bool = False):
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent

    result = ToolResult(content=[TextContent(type="text", text=text)])
    if error:
        result.is_error = True
    return result


def _drive(monkeypatch, tmp_path, session, call_next):
    universe = _universe(tmp_path)
    monkeypatch.setattr("tinyassets.engine_steering._session_key", lambda: session)
    monkeypatch.setattr(engine_tool_activity, "_root", lambda: universe)
    context = SimpleNamespace(message=SimpleNamespace(name="bash",
                                                      arguments={"command": "pytest -q"}))
    try:
        asyncio.run(engine_tool_activity.ToolActivity().on_call_tool(context, call_next))
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 - inspects what was recorded
        pass
    return agent_activity.recent(universe, session or THREAD)


def test_the_engine_records_a_named_sessions_call(monkeypatch, tmp_path):
    async def ok(_context):
        return _result("3 passed")

    [row] = _drive(monkeypatch, tmp_path, THREAD, ok)
    assert (row["tool"], row["summary"], row["state"]) == ("bash", "pytest …", "done")


def test_the_engine_records_a_refusal_with_its_cause(monkeypatch, tmp_path):
    from fastmcp.exceptions import ToolError

    async def refused(_context):
        raise ToolError("egress refused: 10.0.0.1: resolved address is not globally routable")

    [row] = _drive(monkeypatch, tmp_path, THREAD, refused)
    assert row["state"] == "failed" and row["error"].startswith("egress refused")


def test_a_raw_tool_refusal_is_recorded_as_failed(monkeypatch, tmp_path):
    """gpt-6-astra: read/write/edit/bash answer a refusal as text, so it was
    recorded as done. The handler now reports it to the log, typed."""
    async def refused_as_text(_context):
        engine_tool_activity.note_refusal("path escapes the universe")
        return _result("error: path escapes the universe")

    [row] = _drive(monkeypatch, tmp_path, THREAD, refused_as_text)
    assert row["state"] == "failed" and row["error"] == "path escapes the universe"


def test_a_cancelled_call_is_never_left_running(monkeypatch, tmp_path):
    async def cancelled(_context):
        raise asyncio.CancelledError

    [row] = _drive(monkeypatch, tmp_path, THREAD, cancelled)
    assert row["state"] == "failed" and "cancelled" in row["error"]


def test_a_call_with_no_session_is_not_recorded(monkeypatch, tmp_path):
    async def ok(_context):
        return _result("x")

    assert _drive(monkeypatch, tmp_path, "", ok) == []


# -- the owner's status carries their own thread's calls -------------------------


def test_status_shows_only_the_callers_own_thread(tmp_path):
    from tinyassets.api.status import _thread_tool_activity

    universe = _universe(tmp_path)
    agent_activity.started(universe, THREAD, "bash", "owner's command")
    agent_activity.started(universe, "thread:principal:collaborator", "bash", "theirs")
    # A conversation-memory SESSION, not a bare owner: the main thread's is
    # `principal:<owner>`, another agent's `agent:<id>:principal:<owner>`.
    mine = _thread_tool_activity(universe, "principal:owner-1")
    assert [r["summary"] for r in mine] == ["owner's command"]
    assert _thread_tool_activity(universe, "") is None


# -- the page says the newest call on its one status line -------------------------

_PAGE = r"""
setQueueOwner("p-1");
const first=sendTurn("start the long job");
await settle();
readServerTurn({active_turn:{turn_id:"t",state:"tools_pending",age_s:12,
  tools:SCENARIO.tools}});
renderWorking();
const line=indicator().line;
gates[0].resolve({reply:"Done."}); await first; await settle();
console.log(JSON.stringify({line, after:indicator().line}));
"""


def _page(tmp_path, tools):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required to execute the page's own source"
    page, _csp = onboarding.render_app_html()
    return _run(tmp_path, page, {"tools": tools}, _PAGE)


def test_the_status_line_says_the_running_tool(tmp_path):
    out = _page(tmp_path, [{"tool": "bash", "summary": "pytest -q", "state": "running"}])
    assert out["line"] == "Your agent is thinking... · running bash: pytest -q"


def test_the_status_line_says_a_failed_tool_and_why(tmp_path):
    out = _page(tmp_path, [{"tool": "bash", "summary": "pip install x", "state": "failed",
                            "error": "egress refused"}])
    assert out["line"].endswith("ran bash: pip install x (failed: egress refused)")


def test_no_tool_activity_leaves_the_original_line(tmp_path):
    out = _page(tmp_path, [])
    assert out["line"] == "Your agent is thinking..."


def test_model_wait_survives_tool_painter_then_yields_to_running_tool(tmp_path):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required to execute the page's own source"
    page, _csp = onboarding.render_app_html()
    out = _run(tmp_path, page, {}, r"""
setQueueOwner("p-1");
const first=sendTurn("continue the task"); await settle();
readServerTurn({active_turn:{turn_id:"t",state:"inference_started",age_s:300,
  round:4,model:"vendor/example-model",round_age_s:240,
  tools:[{tool:"bash",summary:"old command",state:"done"}]}});
renderWorking(); const waiting=indicator().line;
readServerTurn({active_turn:{turn_id:"t",state:"tools_pending",age_s:301,
  round:4,model:"vendor/example-model",round_age_s:241,
  tools:[{tool:"bash",summary:"pytest",state:"running"}]}});
renderWorking(); const running=indicator().line;
gates[0].resolve({reply:"Done."}); await first; await settle();
console.log(JSON.stringify({waiting,running}));
""")
    assert "step 4" in out["waiting"]
    assert "waiting on example-model for 4 min" in out["waiting"]
    assert "old command" not in out["waiting"]
    assert "running bash: pytest" in out["running"]
    assert "waiting on" not in out["running"]
