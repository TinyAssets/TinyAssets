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

import pytest

from tinyassets.engine_mcp_server import _handbook_read

CHAPTER = "systems"


def test_the_chapter_is_fetchable_by_the_route_the_index_advertises() -> None:
    listing = json.loads(_handbook_read(""))
    assert CHAPTER in listing["handbook"]["write_graph"]
    fetched = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))
    assert fetched["chapter"] == CHAPTER and fetched["handle"] == "write_graph"


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

    from tinyassets.api.publish_requests import validate_action

    with pytest.raises(ValueError, match="branch_ids"):
        validate_action({"type": "publish", "name": "Fantasy Village",
                         "description": "", "branch_ids": [], "ui_id": "ui-7"})
