"""An agent asked for an always-on, multi-agent system must build it HERE.

Live acceptance run (lead, 2026-09-30): asked for a team of persona agents with a
shared board, heartbeats and its own screen, published for others, the founder's
universe wrote an external service under /u with deployment instructions, called
its UI a preview in which "no agents run", and asked the person for a hosting
destination and a code-host token. Every piece already existed inside the
universe; nothing it was served mapped the request onto them.

These tests go through the routes the agent actually has: the resident served
guidance, the handbook fetch it names, and the folder prompt every founder turn
carries. They also hold the chapter to the real surface, because guidance that
promises a call the platform refuses is worse than none.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tinyassets import automations, graph_compiler, universe_tools
from tinyassets.engine_mcp_server import (
    SERVED_TOOL_CHAPTERS,
    _handbook_read,
    served_tool_guidance,
)
from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

CHAPTER = "systems"


def _chapter(name: str = CHAPTER) -> str:
    return json.loads(_handbook_read(f"write_graph.{name}"))["text"]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_resident_index_maps_the_request_onto_the_chapter() -> None:
    guidance = served_tool_guidance("write_graph")
    assert f"``{CHAPTER}``" in guidance
    flat = _flat(guidance).lower()
    # The words the request itself uses, so the match is on the ask.
    for trigger in ("always on", "several agents", "product for others"):
        assert trigger in flat, trigger
    assert "never hosted elsewhere" in flat


def test_every_founder_turn_says_where_long_running_work_belongs(tmp_path) -> None:
    """The folder prompt told the agent what NOT to use bash for and nothing
    else; the service it then wrote under /u followed from that gap."""
    from tinyassets.starter_skills import starter_agent_files

    prompt = _flat(starter_agent_files()["skills/starter-workspace/SKILL.md"])
    assert "ta search" in universe_tools.harness_prompt(tmp_path)
    assert "workflows and automations in this command center" in prompt
    assert "never a service hosted elsewhere" in prompt
    assert f"write_graph.{CHAPTER}" in prompt
    # And that name resolves by the route the index advertises.
    assert _chapter()


def test_the_chapter_maps_each_part_onto_a_live_primitive() -> None:
    text = _flat(_chapter())
    for needle in (
        # Agents.
        '``"agent"`` in ``tools_allowed``',
        # Always on, and the wakes between agents.
        'target="automation"',
        "interval_seconds",
        "cron_expr",
        "event_filter",
        "enqueue_branch_run",
        # Shared state, the screen, and sharing.
        "/u",
        "app_ui",
        "browse_commons",
        "remix_shape",
    ):
        assert needle in text, needle
    # The explicit refusal of the shape the live run chose.
    assert "hosting destination" in text
    assert "wrong shape" in text


def test_the_chapter_promises_only_what_the_platform_accepts() -> None:
    """Each named primitive checked against the code that would refuse it."""
    text = _chapter()

    # Every event type it names is one an automation accepts.
    named_events = set(
        re.findall(r"``(run_completed|pending_request_answered|owner_message|[a-z_]*_event)``",
                   text))
    # Every event it names is one an automation accepts, and it names them all.
    assert named_events == set(automations.EVENT_TYPES)

    # The in-node wake is a verb a code node can actually be granted.
    assert "enqueue_branch_run" in graph_compiler._NODE_MCP_ACTION_ALIASES

    # Every tool it tells an agent node to hold is a served tool, or the marker.
    grant = re.search(r'``(\["agent"[^`]*\])``', text)
    assert grant, "the chapter shows a concrete narrowed grant"
    granted = set(json.loads(grant.group(1))) - {"agent"}
    assert granted and granted <= set(BACKEND_ENGINE_CAPABILITIES), granted

    # Every chapter it points at exists.
    for pointed in re.findall(r"chapter ``([a-z_]+)``", text):
        assert pointed in SERVED_TOOL_CHAPTERS["write_graph"], pointed

    # No bridge call beyond the frozen allowlist the renderer really has.
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    allowlist = re.search(r"ACTIONS:Object\.freeze\(\{(.*?)\}\)", controller, re.S)
    assert allowlist
    available = set(re.findall(r":\"([A-Za-z_]+)\"", allowlist.group(1)))
    assert set(re.findall(r"tinyassets\.([A-Za-z]+)\(", text)) <= available


def test_no_chapter_hands_the_agent_a_publish_it_cannot_make(monkeypatch) -> None:
    """The served write_graph refuses target="agent" and every publish op.

    The interfaces chapter used to tell the universe to publish with
    ``write_graph target="agent" operation="publish"``, which its own surface
    refuses. A mention of a publish is allowed only where the same sentence says
    whose act it is.
    """
    from tinyassets import engine_mcp_server as engine

    # Past the binding gate, so the refusal asserted is the TARGET refusal and
    # not "unbound" -- which would pass whatever the served targets were.
    monkeypatch.setattr(engine, "_binding_error", lambda: None)
    refused = json.loads(engine.write_graph(target="agent", operation="publish"))
    assert "target must be" in refused["error"], refused
    assert "'agent'" not in refused["error"].split("(got")[0]

    for name in ("interfaces", CHAPTER):
        flat = _flat(_chapter(name))
        for sentence in re.split(r"(?<=[.:])\s", flat):
            # A target the served surface refuses is named only as the
            # connector's, never as a call this agent makes.
            if 'target="agent"' in sentence:
                assert "connector" in sentence, (name, sentence)
            if 'operation="publish"' in sentence or "Publishing" in sentence:
                assert "connector" in sentence or "person" in sentence, (name, sentence)


def test_the_publish_ask_it_shows_is_one_the_platform_accepts() -> None:
    """The example action carries exactly the keys the ask validator reads."""
    from tinyassets.api.publish_requests import validate_action

    text = _chapter()
    shown = re.search(r'"action": \{"type": "publish",(.*?)\}\}', text, re.S)
    assert shown, "the chapter shows the publish ask"
    # Validate the actual example, including its nested explicit package,
    # rather than comparing it with a different legacy workflow-only ask.
    action, _ = json.JSONDecoder().raw_decode(text.split('"action": ', 1)[1])
    keys = set(action) - {"type"}
    accepted = set(validate_action(action))
    assert action["publish_kind"] == "command_center"
    assert action["package"] == {} and action["ui_id"]
    assert keys == accepted - {"type"}, (keys, accepted)
    # And the installer's steps name only calls the served surface has.
    for call in ("browse_commons", "read_commons_shape", "remix_shape", "app_ui"):
        assert call in text, call
