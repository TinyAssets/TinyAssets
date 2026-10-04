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


def test_published_screen_bundle_is_findable_in_commons(home, monkeypatch):
    from tests.engine_authority_helpers import mock_engine_admission
    from tinyassets import engine_mcp_server as engine

    legacy = _legacy(home)
    package = _published(home)["done"]["agent_definition_id"]
    definition = get_definition(home, legacy)
    monkeypatch.setattr(engine, "_ACTOR_ID", BOB)
    monkeypatch.setattr(engine, "_GRAPH_ID", BOB_UNIVERSE)
    mock_engine_admission(monkeypatch, {BOB_UNIVERSE})
    result = json.loads(engine.browse_commons(kind="systems", query=definition["name"]))
    assert "error" not in result, result
    [row] = result["content"]["systems"]
    assert row["agent_definition_id"] == legacy
    assert row["publication_kind"] == "system"
    packages = json.loads(engine.browse_commons(kind="packages"))["content"]["packages"]
    assert [row["agent_definition_id"] for row in packages] == [package]


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


@pytest.mark.parametrize("field,value", [
    ("trigger", 1), ("trigger", ["event"]),
    ("workflow", []), ("workflow", {}), ("overlap", []),
    ("trigger.kind", []), ("trigger.kind", "once"),
    ("trigger.interval_seconds", {}), ("trigger.interval_seconds", True),
    ("trigger.event_filter", 1), ("trigger.event_filter.branch_def_id", ["workflow-1"]),
    ("trigger.event_filter.branch_def_id", []),
    ("trigger.event_filter.branch_def_id", None),
    ("published_version_id", {}), ("published_version_id", ["private"]),
])
def test_malformed_public_components_disable_only_their_own_card(home, field, value):
    from copy import deepcopy

    from tinyassets.custom_agents import publish_definition

    good_id = _legacy(home)
    source = get_definition(home, good_id)
    components = deepcopy(source["components"])
    if field == "published_version_id":
        components["workflow-1"][field] = value
    else:
        target = components["automation-1"]
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    bad = publish_definition(home, author_id="unrelated-publisher", payload={
        "schema_version": 1, "name": "Malformed public system", "description": "",
        "tags": ["tinyassets.system.v1"], "components": components})
    with _as(BOB):
        catalogue = read_packages(universe_id=BOB_UNIVERSE)
    rows = {row["agent_definition_id"]: row for row in catalogue["systems"]}
    assert rows[good_id]["available"]
    assert not rows[bad["agent_definition_id"]]["available"]
    assert rows[bad["agent_definition_id"]]["unavailable_reason"]
    assert catalogue["can_try"]
    refused = _ask(BOB, BOB_UNIVERSE, {
        "type": "install", "agent_definition_id": bad["agent_definition_id"]})
    assert "error" in refused and "request_id" not in refused
    assert _bobs_branches(home) == []


def test_copied_legacy_workflow_runs_with_recipients_own_provider(home):
    from tests.test_automations import _real_providers
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.api.automations import automations
    from tinyassets.automations import run_due_automation
    from tinyassets.runs import get_run, wait_for

    ask = _preview(_legacy(home))
    copied = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert copied.get("installed"), copied
    rows = AutomationStore(home).list(universe_id=BOB_UNIVERSE)
    beat = next(row for row in rows if row.name == "scout heartbeat")
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert library[0]["workflow_refs"]["scout"] == beat.branch_def_id
    with _as(BOB):
        resumed = automations(action="resume", universe_id=BOB_UNIVERSE,
                              automation_id=beat.automation_id, expected_revision=beat.revision)
    assert not resumed.get("error"), resumed
    provider = _CountingProvider()
    with _real_providers(codex=provider):
        outcome = run_due_automation(home, AutomationStore(home).get(beat.automation_id),
                                     "2026-10-01T12:05:00+00:00")
    run_id = str(outcome).rsplit(":", 1)[-1]
    wait_for(run_id, timeout=30)
    run = get_run(home, run_id) or {}
    assert run.get("status") == "completed", (outcome, run)
    assert run.get("branch_def_id") == beat.branch_def_id
    assert beat.branch_def_id not in {SCOUT, SCRIBE}
    assert provider.calls, "the recipient's copied workflow never reached its own provider"
    follow = next(row for row in AutomationStore(home).list(universe_id=BOB_UNIVERSE)
                  if row.name == "scribe follows")
    assert follow.desired_state == STATE_PAUSED


@pytest.mark.parametrize("invalid", [
    {"overlap": "not-a-policy"},
    {"trigger": {"kind": "cron", "cron_expr": "not-cron"}},
    {"trigger": {"kind": "event", "event_type": "unknown"}},
    {"trigger": {"kind": "event", "event_type": "run_completed",
                 "event_filter": {"unexpected": "value"}}},
])
def test_permanently_invalid_automation_refuses_before_recipient_mutation(home, invalid):
    from copy import deepcopy

    from tinyassets.custom_agents import publish_definition

    original = _legacy(home)
    components = deepcopy(get_definition(home, original)["components"])
    # Put the failure last: a valid earlier automation must not be installed either.
    components["automation-2"].update(invalid)
    bad = publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Invalid schedule", "description": "",
        "tags": ["tinyassets.system.v1"], "components": components})
    definition_id = bad["agent_definition_id"]
    before_ui = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    before_files = _bob_files(home)
    with _as(BOB):
        catalogue = read_packages(universe_id=BOB_UNIVERSE)
        preview = try_package(universe_id=BOB_UNIVERSE,
                              payload={"agent_definition_id": definition_id})
    # Exercise the actual confirmation too on the vulnerable implementation,
    # so the regression reports its partial copies rather than only a bad card.
    result = (_answer(BOB, BOB_UNIVERSE, preview["request_id"])
              if "request_id" in preview else preview)
    assert _bobs_branches(home) == [], result
    assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before_ui
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
    assert _bob_files(home) == before_files
    card = next(c for c in catalogue["systems"] if c["agent_definition_id"] == definition_id)
    assert not card["available"] and card["unavailable_reason"]
    assert "request_id" not in preview and "error" in preview


@pytest.mark.parametrize("after_preview", [False, True])
def test_withdrawn_second_workflow_refuses_before_snapshot_or_copy(home, monkeypatch,
                                                                  after_preview):
    from tinyassets import branch_versions, universe_server

    definition_id = _legacy(home)
    source = get_definition(home, definition_id)
    second = source["components"]["workflow-2"]["published_version_id"]
    ask = _preview(definition_id) if after_preview else None
    with _as(OWNER):
        withdrawn = json.loads(universe_server.write_graph(
            target="branch", operation="patch", branch_id=SCRIBE,
            changes_json=json.dumps([{"op": "set_visibility", "visibility": "private"}]),
        ))
    assert "error" not in withdrawn, withdrawn
    assert branch_versions.branch_version_is_public(home, second)
    reads = []
    real_read = branch_versions.get_branch_version

    def observed_read(base, version_id):
        reads.append(version_id)
        return real_read(base, version_id)

    monkeypatch.setattr(branch_versions, "get_branch_version", observed_read)
    before_ui = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    before_files = _bob_files(home)
    if ask:
        result = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    else:
        with _as(BOB):
            preview = try_package(universe_id=BOB_UNIVERSE,
                                  payload={"agent_definition_id": definition_id})
        result = (_answer(BOB, BOB_UNIVERSE, preview["request_id"])
                  if "request_id" in preview else preview)
    assert _bobs_branches(home) == [], result
    assert second not in reads, "withdrawn snapshot was read during preflight"
    assert "error" in result
    if after_preview:
        assert "no longer public" in json.dumps(result)
    assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before_ui
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
    assert _bob_files(home) == before_files
    with _as(BOB):
        [card] = list_systems()
    assert not card["available"] and "no longer public" in card["unavailable_reason"]
