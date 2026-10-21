"""Legacy public systems use pinned component copies, never invented file packages."""
from __future__ import annotations


import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    SCOUT,
    SCRIBE,
    UI,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _automations,
    _pin_data_dir,  # noqa: F401
    _publish_action,
)
from tests.test_command_center_packages import (
    home as package_home,
)
from tinyassets.api.pending_requests import try_package
from tinyassets.app_ui_store import get_app_ui, save_app_ui

home = package_home
pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _legacy(home, *, embedded=False):
    ui = {**UI, "workflow_refs": {"scout": SCOUT, "scribe": SCRIBE}}
    if embedded:
        ui["script"] = f"tinyassets.call('read_run',{{branch_def_id:'{SCOUT}'}})"
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                expected_revision=row["revision"], changes={"ui_library": [ui]})
    beat, follow = _automations(home)
    action = _publish_action()
    del action["package"]
    action["automation_ids"] = [beat.automation_id, follow.automation_id]
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    out = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert out.get("published"), out
    return out["agent_definition_id"]


def _preview(definition_id):
    with _as(BOB):
        out = try_package(universe_id=BOB_UNIVERSE,
                          payload={"agent_definition_id": definition_id})
    assert "request_id" in out, out
    return out




























