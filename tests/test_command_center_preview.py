"""Public-only visual reads and deterministic adoption without a provider."""
from __future__ import annotations

import json

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    _as,
    _bob_files,
    _bobs_branches,
    _pin_data_dir,  # noqa: F401
    _published,
)
from tests.test_command_center_packages import home as home
from tests.test_command_center_system_copy import _legacy
from tinyassets.api.graph_reads import read_graph
from tinyassets.app_ui_store import get_app_ui
from tinyassets.automations import (
    AutomationStore,
)
from tinyassets.command_center_preview import read_preview
from tinyassets.commons_catalog import get_definition
from tinyassets.storage.pending_requests import list_pending

pytestmark = [
    pytest.mark.usefixtures("cloud_runtime"),
    pytest.mark.verifies('owner-daemon-in-box:T10'),
]


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




def test_preview_refuses_withdrawn_public_workflow(home, monkeypatch):
    definition_id = _legacy(home)
    monkeypatch.setattr("tinyassets.branch_versions.branch_version_is_public", lambda *a: False)
    with _as(BOB):
        out = read_preview(universe_id=BOB_UNIVERSE, definition_id=definition_id)
    assert out["error"] == "preview_unavailable" and "ui" not in out






@pytest.mark.parametrize("asset_state", ["public", "missing", "changed"])
def test_preview_assets_come_only_from_verified_public_package(home, monkeypatch, asset_state):
    import base64
    import hashlib

    from tests.test_command_center_packages import _hostile_package
    from tests.test_in_platform_agent_systems import UI
    from tinyassets.commons_catalog import publish_definition

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

    monkeypatch.setattr("tinyassets.app_ui_store.read_app_ui_asset", private_asset_read)
    with _as(BOB):
        out = read_preview(universe_id=BOB_UNIVERSE,
                           definition_id=source["agent_definition_id"])
    if asset_state == "public":
        assert out["assets"] == [{"path": path, "mime_type": "text/css",
                                  "base64": base64.b64encode(data).decode("ascii")}]
    else:
        assert out["error"] == "preview_unavailable" and "ui" not in out
    assert reads == [] and list_pending(home / BOB_UNIVERSE) == []
