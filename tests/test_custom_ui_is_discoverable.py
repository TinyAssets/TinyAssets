"""An agent asked for an interface must be able to FIND where to build one.

On the first user-route run (lead, 2026-09-26) the universe did try: it read the
`workspaces` and `code_nodes` chapters and browsed the commons, and found no UI
primitive at all. A primitive nobody can discover is not shipped, so these
assertions are about reachability rather than about the text being nice.

They deliberately go through the two routes an agent actually has — the served
guidance it is handed, and the handbook fetch that guidance tells it to make —
rather than reading the module constant, which would pass even if the chapter were
unregistered.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.engine_mcp_server import _handbook_read, served_tool_guidance

CHAPTER = "interfaces"


def _guidance() -> str:
    return served_tool_guidance("write_graph")


def test_the_served_guidance_names_the_interfaces_chapter() -> None:
    guidance = _guidance()

    # Named in the resident index, so it costs a lookup rather than a guess.
    assert f"``{CHAPTER}``" in guidance

    # With the words a request for an interface actually uses. An agent matches on
    # the ask, not on our internal vocabulary.
    lowered = guidance.lower()
    for trigger in ("interface", "dashboard", "game"):
        assert trigger in lowered, trigger


def test_the_chapter_is_fetchable_by_the_route_the_index_advertises() -> None:
    listing = json.loads(_handbook_read(""))
    assert CHAPTER in listing["handbook"]["write_graph"]

    # The exact query form the index tells the agent to send.
    assert 'query="<handle>.<chapter>"' in listing["read_one"]
    fetched = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))
    assert fetched["chapter"] == CHAPTER
    assert fetched["handle"] == "write_graph"
    assert len(fetched["text"]) > 1000


@pytest.mark.parametrize(
    "needle",
    [
        # The component it must write, by its exact kind.
        "tinyassets.app-ui.v1",
        # Every field, because an unexpected or missing one is refused outright.
        "ui_id",
        "markup",
        "style",
        "script",
        # The storage calls: read first, then a compare-and-set save naming the
        # revision read (0 for a first save).
        'read_graph target="app_ui"',
        'write_graph target="app_ui" operation="save"',
        "ui_library",
        "expected_revision",
        # The complete capability list. An agent that programs against an action
        # the bridge does not have writes a UI that fails at runtime.
        "tinyassets.whoami",
        "tinyassets.listAgents",
        "tinyassets.sendMessage",
        "tinyassets.readConversation",
        # One UI at a time, so a library bigger than one tool result never
        # has to be read whole (live 2026-09-30: a cut read stopped a switch).
        'operation="activate"',
        'operation="use_default"',
        'operation="add_ui"',
        'operation="replace_ui"',
        'operation="edit_ui"',
        'operation="remove_ui"',
        'query="<ui_id>"',
        "field_name",
        "expected_etag",
        # Switching and sharing.
        "ui_selection",
        'operation="publish"',
        'operation="remix"',
    ],
)
def test_the_chapter_carries_what_a_build_actually_needs(needle: str) -> None:
    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"]
    assert needle in text, needle


def test_the_chapter_states_the_limits_that_cause_refusals() -> None:
    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"]

    # Bounds read from the controller rather than restated here, so a bound that
    # changes without the chapter changing fails this instead of misleading an
    # agent into writing a bundle that is refused.
    import re
    from pathlib import Path

    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")

    def constant(name: str) -> int:
        found = re.search(rf"\b{name}:(\d+)", controller)
        assert found, name
        return int(found.group(1))

    for name in ("MAX_TEXT_BYTES", "MAX_ASSET_BYTES", "MAX_UI_ASSET_BYTES", "MAX_ASSET_FILES"):
        assert str(constant(name)) in text, name
    # The library has no bound at all -- not a count and not a byte total -- so
    # the handbook must say so rather than quote a ceiling that no longer exists.
    assert "no limit on how many UIs" in text
    assert "none on its total size" in text

    # And the two refusals that would otherwise look like platform bugs.
    assert "does NOT run" in text, "a <script> inside markup is inert"
    assert "refused" in text


def test_the_chapter_does_not_promise_a_capability_the_bridge_lacks() -> None:
    """A handbook that over-promises is worse than one that is missing.

    Every `tinyassets.<call>` the chapter shows has to exist in the controller's
    frozen allowlist, and per-message agent addressing — which the server does not
    support — must be described as refused rather than as working.
    """
    import re
    from pathlib import Path

    text = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))["text"]
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")

    promised = set(re.findall(r"tinyassets\.([A-Za-z]+)\(", text))
    allowlist = re.search(r"ACTIONS:Object\.freeze\(\{(.*?)\}\)", controller, re.S)
    assert allowlist, "the controller must still declare a frozen allowlist"
    available = set(re.findall(r":\"([A-Za-z_]+)\"", allowlist.group(1)))

    # Frame-local helpers are not bridge actions: they are defined on the
    # frame's own `tinyassets` object and never reach the parent.
    from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML

    local = {name for name in ("asset",) if f"    {name}: {name}," in BOOTSTRAP_HTML}
    assert local == {"asset"}, "the frame must still define tinyassets.asset"

    assert promised, "the chapter must show the calls a UI can make"
    assert promised <= available | local, promised - available - local

    # The honest limit on addressing a named agent.
    assert "refused" in text and "selected" in text
