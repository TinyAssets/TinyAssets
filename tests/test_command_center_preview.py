"""Public-only visual reads and deterministic adoption without a provider."""
from __future__ import annotations

import json

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import _registration_kwargs, _seed_branch, _seed_owner
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    UNIVERSE,
    _answer,
    _as,
    _bob_files,
    _bobs_branches,
    _pin_data_dir,  # noqa: F401
    _published,
)
from tests.test_command_center_packages import home as home
from tests.test_command_center_system_copy import _legacy
from tinyassets.api.graph_reads import read_graph
from tinyassets.api.pending_requests import try_package
from tinyassets.automations import (
    STATE_PAUSED,
    AutomationStore,
    AutomationUnavailable,
    _runtime_authority_reason,
    register_automation,
)
from tinyassets.command_center_preview import read_preview
from tinyassets.custom_agents import get_app_ui, get_definition
from tinyassets.daemon_server import db_path
from tinyassets.provider_assignment import load_provider_assignment
from tinyassets.storage.pending_requests import list_pending

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.mark.parametrize("publication", ["system", "package"])
def test_preview_is_a_public_read_with_no_request_or_private_copy(home, publication):
    definition_id = (_legacy(home) if publication == "system" else
                     _published(home)["done"]["agent_definition_id"])
    before = _bob_files(home)
    with _as(BOB):
        out = json.loads(read_graph(target="command_center_preview", graph_id=BOB_UNIVERSE,
                                    agent_definition_id=definition_id))
    assert out["available"] and out["publication_kind"] == publication
    assert out["ui"]["markup"] and out["source_fingerprint"]
    assert out["assets"] == []
    assert list_pending(home / BOB_UNIVERSE) == []
    assert _bob_files(home) == before and _bobs_branches(home) == []
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
    assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"] == []


def test_preview_requires_the_recipient_home_owner(home):
    definition_id = _legacy(home)
    with _as(OWNER):
        out = read_preview(universe_id=BOB_UNIVERSE, definition_id=definition_id)
    assert out.get("error") and "ui" not in out
    with _as(BOB):
        missing = read_preview(universe_id=BOB_UNIVERSE, definition_id="absent")
    assert missing["error"] == "preview_unavailable"


def test_preview_refuses_withdrawn_public_workflow(home, monkeypatch):
    definition_id = _legacy(home)
    monkeypatch.setattr("tinyassets.branch_versions.branch_version_is_public", lambda *a: False)
    with _as(BOB):
        out = read_preview(universe_id=BOB_UNIVERSE, definition_id=definition_id)
    assert out["error"] == "preview_unavailable" and "ui" not in out


@pytest.mark.parametrize("publication", ["system", "package"])
def test_unpowered_recipient_adopts_only_after_consent_with_zero_provider_calls(
        home, monkeypatch, publication):
    import sqlite3

    from tinyassets.providers.router import ProviderRouter

    definition_id = (_legacy(home) if publication == "system" else
                     _published(home)["done"]["agent_definition_id"])
    source_before = get_definition(home, definition_id)
    # Delete synthetic fixture setup, not a live account. The actual assignment
    # loader must see no provider, and the background consumer is off too.
    with sqlite3.connect(db_path(home)) as conn:
        conn.execute("DELETE FROM provider_assignments WHERE universe_id=?", (BOB_UNIVERSE,))
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("adoption must not call a model")

    monkeypatch.setattr("tinyassets.providers.call.call_provider", forbidden)
    for name in ("call", "call_async", "call_sync", "call_with_policy", "call_with_policy_sync",
                 "call_judge_ensemble"):
        if hasattr(ProviderRouter, name):
            monkeypatch.setattr(ProviderRouter, name, forbidden)
    with _as(BOB):
        preview = read_preview(universe_id=BOB_UNIVERSE, definition_id=definition_id)
        ask = try_package(universe_id=BOB_UNIVERSE,
                          payload={"agent_definition_id": definition_id})
    assert preview["available"] and "request_id" in ask
    assert _bobs_branches(home) == []
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
    assert _answer(OWNER, BOB_UNIVERSE, ask["request_id"]).get("error")
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    assert len(_bobs_branches(home)) == 2
    rows = AutomationStore(home).list(universe_id=BOB_UNIVERSE)
    assert len(rows) == 2
    assert all(row.desired_state == STATE_PAUSED for row in rows)
    assert all(_runtime_authority_reason(home, row) == "no_serving_assignment" for row in rows)
    assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert get_definition(home, definition_id) == source_before and calls == []
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None


def test_paused_registration_preserves_owner_and_branch_fences(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    with pytest.raises(AutomationUnavailable, match="owner_not_admin"):
        register_automation(tmp_path, **_registration_kwargs(
            owner_principal_id=BOB, paused_reason="package_installed"))
    with pytest.raises(AutomationUnavailable, match="branch_not_readable"):
        register_automation(tmp_path, **_registration_kwargs(
            branch_def_id="missing", paused_reason="package_installed"))
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


@pytest.mark.parametrize("asset_state", ["public", "missing", "changed"])
def test_preview_assets_come_only_from_verified_public_package(home, monkeypatch, asset_state):
    import base64
    import hashlib

    from tests.test_command_center_packages import _hostile_package
    from tests.test_in_platform_agent_systems import UI
    from tinyassets.custom_agents import publish_definition

    data = b"p { color: blue; }"
    path = "notes/style.css"
    files = {} if asset_state == "missing" else {path: data}
    original = get_definition(home, _hostile_package(home, files))
    ui = {**UI, "assets": {path: {
        "sha256": hashlib.sha256(data if asset_state != "changed" else b"changed").hexdigest(),
        "size": len(data), "media_type": "text/css"}}}
    source = publish_definition(home, author_id=original["author_id"], payload={
        "schema_version": 1, "name": "Public asset screen", "description": "Preview fixture",
        "tags": original["tags"], "components": {**original["components"], "ui": ui}})
    reads = []

    def private_asset_read(*args, **kwargs):
        reads.append(args)
        raise AssertionError("preview may not read a private owner asset")

    monkeypatch.setattr("tinyassets.custom_agents.read_app_ui_asset", private_asset_read)
    with _as(BOB):
        out = read_preview(universe_id=BOB_UNIVERSE,
                           definition_id=source["agent_definition_id"])
    if asset_state == "public":
        assert out["assets"] == [{"path": path, "mime_type": "text/css",
                                  "base64": base64.b64encode(data).decode("ascii")}]
    else:
        assert out["error"] == "preview_unavailable" and "ui" not in out
    assert reads == [] and list_pending(home / BOB_UNIVERSE) == []


@pytest.mark.parametrize("version", [{}, [], 7, None, ""])
def test_malformed_public_workflow_reference_refuses_preview(home, version):
    from tests.test_command_center_packages import _hostile_package
    from tests.test_in_platform_agent_systems import UI
    from tinyassets.api.publish_requests import BRANCH_REF_KIND
    from tinyassets.custom_agents import publish_definition

    original = get_definition(home, _hostile_package(home, {}))
    definition = publish_definition(home, author_id=original["author_id"], payload={
        "schema_version": 1, "name": "Malformed preview", "description": "",
        "tags": original["tags"], "components": {**original["components"], "ui": UI,
            "workflow-1": {"kind": BRANCH_REF_KIND, "published_version_id": version}}})
    with _as(BOB):
        out = read_preview(universe_id=BOB_UNIVERSE,
                           definition_id=definition["agent_definition_id"])
    assert out["error"] == "preview_unavailable" and "ui" not in out
    assert list_pending(home / BOB_UNIVERSE) == []
