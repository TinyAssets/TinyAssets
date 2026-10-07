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

from tinyassets.engine_mcp_server import _handbook_read

CHAPTER = "interfaces"


def test_the_chapter_is_fetchable_by_the_route_the_index_advertises() -> None:
    listing = json.loads(_handbook_read(""))
    assert CHAPTER in listing["handbook"]["write_graph"]

    # The exact query form the index tells the agent to send.
    assert 'query="<handle>.<chapter>"' in listing["read_one"]
    fetched = json.loads(_handbook_read(f"write_graph.{CHAPTER}"))
    assert fetched["chapter"] == CHAPTER
    assert fetched["handle"] == "write_graph"
    assert len(fetched["text"]) > 1000


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
