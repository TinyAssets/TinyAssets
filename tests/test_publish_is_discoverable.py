"""An agent asked to publish must be able to FIND how, without being told.

Live on prod in the founder's desktop app (2026-10-03, with #4366 deployed): asked
to publish, the main agent answered that it has NO publish action -- it saw only
browse/read/remix/share-provider in the commons, said packages "are installed via
an install request but I have no authoring/publish surface", and called the
founder's own Fantasy Village "a leftover UI surface in one command center".

Nothing was missing. #4315 shipped publish as a pending-request ask, and
``write_graph`` is on the served allowlist, so the capability was reachable the
whole time. What the agent read was a resident sentence saying the opposite:

    "Publishing to the commons, changing visibility to public, and forking a
     foreign shape are NOT available here (they stay in the browser flow)"

true before #4315 and false after it. The agent was obeying its instructions. A
capability the agent is told it does not have is not shipped, so these
assertions are about reachability and about the resident text not contradicting
it -- the same rule tests/test_custom_ui_is_discoverable.py was written for.

They go through the two routes an agent actually has: the guidance it is handed,
and the handbook fetch that guidance tells it to make.
"""

from __future__ import annotations

import json
import re

import pytest

from tinyassets.engine_mcp_server import _handbook_read, served_tool_guidance, write_graph

CHAPTER = "systems"


def _resident() -> str:
    """Only what rides on every round-trip -- no chapter fetch."""
    return write_graph.__doc__ or ""


def test_the_resident_text_does_not_say_publishing_is_unavailable() -> None:
    """The regression itself. A stale denial costs a capability outright.

    An agent that reads "not available" stops there: it has no reason to fetch a
    chapter to check whether the denial is true.
    """
    resident = _resident().lower()
    for denial in (
        "publishing to the commons, changing",
        "not available here",
        "stay in the browser flow",
    ):
        assert denial not in resident, (
            f"the resident text still tells the agent {denial!r}; it was the whole bug")


def test_the_resident_text_says_publishing_is_the_agents_to_do() -> None:
    """Resident on purpose: a chapter cannot answer a question never asked.

    Guidance whose absence produces a WRONG call stays resident
    (openspec/changes/archive/2026-09-26-engine-tool-manual-on-demand/). Absence here produced a
    wrong REFUSAL, which is worse -- the person is told their platform cannot do
    something it does.
    """
    resident = _resident()
    lowered = resident.lower()
    assert "publishing is mine" in lowered, "the resident text must claim the capability"
    # The route, so the agent can compose the call without a fetch if it must.
    assert 'target="pending_request"' in resident
    assert '"publish"' in resident or "``publish``" in resident
    assert '"package": {}' in resident, "the whole-command-center form must be named"
    # And where the payload lives.
    assert f"``{CHAPTER}``" in resident


def test_the_index_points_a_publish_request_at_the_right_chapter() -> None:
    """An agent matches on the ask's words, not our internal vocabulary."""
    guidance = served_tool_guidance("write_graph").lower()
    for trigger in ("publishing", "sharing", "installing"):
        assert trigger in guidance, trigger


@pytest.mark.parametrize(
    "needle",
    [
        # The ask, exactly as it must be composed.
        'target="pending_request"',
        'operation="ask"',
        '"type": "publish"',
        # Sharing the whole command center, and installing someone else's.
        '"package": {}',
        '"type": "install"',
        'browse_commons kind="packages"',
        # The fields a publish names.
        "branch_ids",
        "ui_id",
        "automation_ids",
    ],
)
def test_the_chapter_carries_the_whole_publish_recipe(needle: str) -> None:
    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"]
    assert needle in text, needle


def test_the_chapter_is_fetchable_by_the_route_the_index_advertises() -> None:
    listing = json.loads(_handbook_read(""))
    assert CHAPTER in listing["handbook"]["write_graph"]
    fetched = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))
    assert fetched["chapter"] == CHAPTER and fetched["handle"] == "write_graph"


def test_the_agent_is_taught_the_persons_word_for_their_screen() -> None:
    """Founder direction 2026-10-01: every word the person OR THE AGENT reads
    says "command center". The app's switcher moves between them.

    The live reply called the founder's Fantasy Village "a leftover UI surface
    in one command center", which is both the wrong word and dismissive of the
    thing they asked about. And "publish my Fantasy Village" has to mean the
    package: a screen without its workflows installs as a picture that cannot
    do anything.
    """
    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"]
    lowered = text.lower()
    assert "fantasy village" in lowered, (
        "the naming rule should use the founder's own example, not an invented one")
    assert "never call it a ui to them" in lowered
    assert re.search(r"package form", lowered), (
        "publishing a named screen must route to the package form")


def test_the_documented_payload_is_one_the_validator_accepts() -> None:
    """A recipe that fails validation is the same bug one layer down.

    The chapter is the only thing standing between "the agent knows publish
    exists" and "the agent composes a call that lands", so the shapes it
    documents are submitted here against the real validator.
    """
    from tinyassets.api.publish_requests import validate_action

    documented = {"type": "publish", "name": "Fantasy Village",
                  "description": "A village that shows my projects",
                  "branch_ids": ["b-1"], "ui_id": "ui-7", "automation_ids": ["a-1"]}
    assert validate_action(dict(documented))["type"] == "publish"
    # The whole-command-center form, and the two options the chapter names.
    assert "package" in validate_action({**documented, "package": {}})
    assert "package" in validate_action(
        {**documented, "package": {"exclude": ["notes/private.md"],
                                    "memory_items": ["m_7f3a"]}})


def test_the_chapter_does_not_promise_a_screen_only_publish() -> None:
    """``branch_ids`` is required, so "just the screen" cannot be sent.

    An earlier draft of this guidance offered it as a fallback. The validator
    refuses it, so the agent would have composed a call that could never land --
    and told the person it could.
    """
    import pytest

    from tinyassets.api.publish_requests import validate_action

    with pytest.raises(ValueError, match="branch_ids"):
        validate_action({"type": "publish", "name": "Fantasy Village",
                         "description": "", "branch_ids": [], "ui_id": "ui-7"})

    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"].lower()
    assert "no screen-only publish" in text, (
        "the chapter must say the screen cannot go alone, not imply it can")
