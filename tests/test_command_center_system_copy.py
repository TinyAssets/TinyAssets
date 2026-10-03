"""Legacy public systems use pinned component copies, never invented file packages."""
from __future__ import annotations

import json

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
    _bob_files,
    _bobs_branches,
    _pin_data_dir,  # noqa: F401
    _publish_action,
    _published,
)
from tests.test_command_center_packages import (
    home as package_home,
)
from tinyassets.api.pending_requests import answer_request, try_package
from tinyassets.api.system_copy_requests import list_systems
from tinyassets.automations import STATE_PAUSED, AutomationStore
from tinyassets.command_center_picker import read_packages
from tinyassets.custom_agents import get_app_ui, get_definition, save_app_ui

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


def test_zero_legacy_and_mixed_catalogues_are_explicit(home):
    with _as(BOB):
        empty = read_packages(universe_id=BOB_UNIVERSE)
    assert empty["packages"] == empty["systems"] == [] and not empty["can_try"]
    legacy = _legacy(home)
    definition = get_definition(home, legacy)
    assert definition["tags"] == ["tinyassets.system.v1"]
    assert "package" not in definition["components"]
    [card] = list_systems()
    assert card["agent_definition_id"] == legacy and card["publication_kind"] == "system"
    assert card["available"] and card["workflow_count"] == card["automation_count"] == 2
    package = _published(home)["done"]["agent_definition_id"]
    with _as(BOB):
        mixed = read_packages(universe_id=BOB_UNIVERSE)
    assert mixed["can_try"]
    assert [p["agent_definition_id"] for p in mixed["packages"]] == [package]
    assert [p["agent_definition_id"] for p in mixed["systems"]] == [legacy]


def test_preview_and_cancel_do_not_copy_any_components_or_files(home):
    definition_id = _legacy(home)
    before = _bob_files(home)
    ask = _preview(definition_id)
    from tinyassets.storage.pending_requests import get_request

    preview = get_request(home / BOB_UNIVERSE, ask["request_id"])
    assert "Component-only copy. No package or files are imported." in preview["body"]
    assert "paused" in preview["body"]
    assert _bob_files(home) == before and _bobs_branches(home) == []
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    with _as(BOB):
        cancelled = answer_request(universe_id=BOB_UNIVERSE, payload={
            "request_id": ask["request_id"], "decision": "declined"})
    assert cancelled["decision"] == "declined"
    assert _answer(BOB, BOB_UNIVERSE, ask["request_id"])["error"] == "already_resolved"
    assert _bob_files(home) == before and _bobs_branches(home) == []
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []


def test_confirm_copies_private_workflows_ui_and_paused_automations_additively(home):
    definition_id = _legacy(home)
    source = get_definition(home, definition_id)
    alice_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    alice_automations = AutomationStore(home).list(universe_id=UNIVERSE)
    own = {**UI, "ui_id": "my-own", "name": "Bob's existing command center"}
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                expected_revision=0, changes={"ui_library": [own]})
    before = _bob_files(home)
    ask = _preview(definition_id)
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed") and done["publication_kind"] == "system", done
    assert done["files"] == [] and _bob_files(home) == before
    copies = _bobs_branches(home)
    ids = {c["branch_def_id"] for c in copies}
    assert len(ids) == 2 and ids.isdisjoint({SCOUT, SCRIBE})
    assert all(c["visibility"] == "private" and c.get("default_llm_policy") is None
               for c in copies)
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert library[0] == own and len(library) == 2
    assert set(library[1]["workflow_refs"].values()) == ids
    rows = {r.name: r for r in AutomationStore(home).list(universe_id=BOB_UNIVERSE)}
    assert len(rows) == 2
    assert all(r.desired_state == STATE_PAUSED and r.owner_principal_id == BOB
               and r.branch_def_id in ids and r.inputs == {} for r in rows.values())
    assert rows["scribe follows"].event_filter["branch_def_id"] == rows[
        "scout heartbeat"].branch_def_id
    assert get_definition(home, definition_id) == source
    assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE) == alice_ui
    assert AutomationStore(home).list(universe_id=UNIVERSE) == alice_automations
    assert _answer(BOB, BOB_UNIVERSE, ask["request_id"])["error"] == "already_resolved"
    assert len(_bobs_branches(home)) == len(rows) == 2


def test_only_the_recipient_owner_can_confirm_component_copy(home):
    ask = _preview(_legacy(home))
    assert _answer(OWNER, BOB_UNIVERSE, ask["request_id"])["error"] == "not_found"
    assert _bobs_branches(home) == []
    assert _answer(BOB, UNIVERSE, ask["request_id"])["error"] == "not_found"
    assert _bobs_branches(home) == []


def test_embedded_publisher_ids_remain_visible_but_cannot_be_copied(home):
    definition_id = _legacy(home, embedded=True)
    [card] = list_systems()
    assert not card["available"] and "declared workflow_refs" in card["unavailable_reason"]
    assert card["agent_definition_id"] == definition_id
    out = _ask(BOB, BOB_UNIVERSE, {"type": "install", "agent_definition_id": definition_id})
    assert "request_id" not in out and "workflow_refs" in json.dumps(out)
    assert _bobs_branches(home) == []


def test_partial_component_copy_retries_without_duplicate_workflows(home, monkeypatch):
    from tinyassets.api import package_requests

    ask = _preview(_legacy(home))
    real = package_requests._add_ui
    monkeypatch.setattr(package_requests, "_add_ui", lambda *a, **k: (_ for _ in ()).throw(
        OSError("synthetic interrupted UI save")))
    failed = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert "error" in failed and "resumes" in json.dumps(failed), failed
    first = {c["branch_def_id"] for c in _bobs_branches(home)}
    assert len(first) == 2
    monkeypatch.setattr(package_requests, "_add_ui", real)
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    assert {c["branch_def_id"] for c in _bobs_branches(home)} == first
    assert len(AutomationStore(home).list(universe_id=BOB_UNIVERSE)) == 2
    assert len(get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]) == 1


def test_public_version_withdrawn_after_preview_refuses_before_copy(home, monkeypatch):
    ask = _preview(_legacy(home))
    monkeypatch.setattr("tinyassets.branch_versions.branch_version_is_public", lambda *a: False)
    out = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert "error" in out and "no longer public" in json.dumps(out), out
    assert _bobs_branches(home) == []


def test_legacy_catalogue_pages_past_newer_file_packages(home, monkeypatch):
    from tinyassets import custom_agents

    definition_id = _legacy(home)
    source = get_definition(home, definition_id)
    offsets = []

    def page(base, *, tags, limit, offset):
        offsets.append(offset)
        assert tags == ["tinyassets.system.v1"] and limit == 100
        if offset == 0:
            return [{"tags": ["tinyassets.package.v1"]}] * 100
        return [source]

    from tinyassets.command_center_packages import PACKAGE_TAG

    def packages_first(base, **kwargs):
        batch = page(base, **kwargs)
        return [{"tags": [PACKAGE_TAG]} for _ in batch] if kwargs["offset"] == 0 else batch

    monkeypatch.setattr(custom_agents, "list_definitions", packages_first)
    assert [row["agent_definition_id"] for row in list_systems()] == [definition_id]
    assert offsets == [0, 100]


def test_unpublished_workflow_is_refused_before_snapshot_is_read(home, monkeypatch):
    from tinyassets.api.system_copy_requests import _source

    definition_id = _legacy(home)
    monkeypatch.setattr("tinyassets.branch_versions.branch_version_is_public", lambda *a: False)
    reads = []
    monkeypatch.setattr("tinyassets.branch_versions.get_branch_version",
                        lambda *a: reads.append(a))
    with pytest.raises(ValueError, match="no longer public"):
        _source(definition_id)
    assert reads == []
