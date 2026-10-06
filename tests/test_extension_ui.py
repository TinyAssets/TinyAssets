"""The existing UI row is a revocable projection of immutable package bytes."""
import json

import pytest

from tests.test_one_extension_unit import backend, call, files
from tinyassets.custom_agents import change_app_ui_entry, get_app_ui
from tinyassets.extension_capabilities import ExtensionCapabilities


def test_malformed_settings_hide_only_affected_projection(tmp_path, caplog):
    service = backend(tmp_path)
    ext = ExtensionCapabilities(service)
    installed = ext.store.install(files())
    active = call(service, "extension:activate", {"name": "sample",
        "revision": installed["revision"], "expected_generation": 0})["result"]
    ordinary = {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": "ordinary",
                "name": "Ordinary", "markup": "<p>OK</p>", "style": "", "script": ""}
    change_app_ui_entry(tmp_path, owner_user_id="user-1", universe_id="home",
                        operation="add_ui", payload={"component": ordinary})
    change_app_ui_entry(tmp_path, owner_user_id="user-1", universe_id="home",
                        operation="activate", payload={"ui_id": active["ui_ids"][0]})
    (service.root / "settings.yaml").write_text("schema_version: INVALID", encoding="utf-8")
    result = get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")
    assert [row["ui_id"] for row in result["ui_library"]] == ["ordinary"]
    assert result["ui_selection"] == {"version": 1, "state": "default"}
    assert "SettingsError" in caplog.text and active["ui_ids"][0] in caplog.text


def test_projection_update_revoke_and_private_rows(tmp_path, monkeypatch):
    service = backend(tmp_path)
    ext = ExtensionCapabilities(service)
    first = ext.store.install(files())
    pin = {"name": "sample", "revision": first["revision"], "expected_generation": 0}
    active = call(service, "extension:activate", pin)["result"]
    ui_id = active["ui_ids"][0]
    doc = get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")
    assert doc["ui_library"][0]["markup"] == "<h1>Hello</h1>"
    assert doc["ui_library"][0]["ui_id"] == ui_id
    assert get_app_ui(tmp_path, owner_user_id="user-2", universe_id="home")["ui_library"] == []
    assert get_app_ui(tmp_path, owner_user_id="user-1", universe_id="other")["ui_library"] == []
    change_app_ui_entry(tmp_path, owner_user_id="user-1", universe_id="home",
                        operation="activate", payload={"ui_id": ui_id})
    # Authoring the UI backend cannot replace revision-pinned executable code.
    change_app_ui_entry(tmp_path, owner_user_id="user-1", universe_id="home",
                        operation="edit_ui", payload={"ui_id": ui_id, "set": {"script": "bad()"}})
    assert not get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")["ui_library"]
    # Re-activation projects exact bytes with a new identity and retires the old row.
    active = call(service, "extension:activate", {**pin, "expected_generation": 1})["result"]
    assert active["ui_ids"][0] != ui_id
    from tinyassets import extension_ui

    monkeypatch.setattr(extension_ui, "project", lambda *a: [])  # simulate cleanup unavailable
    result = call(service, "extension:revoke", {**pin, "expected_generation": 2})
    assert result["result"]["state"] == "revoked"
    assert not get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")["ui_library"]


def test_interactive_json_card_and_invalid_asset_activation(tmp_path):
    service = backend(tmp_path)
    ext = ExtensionCapabilities(service)
    content = files(cards=[{"name": "view", "description": "View", "asset": "ui.json"}])
    ui = {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": "working", "name": "View",
          "markup": "<button>Go</button>", "style": "button{color:red}", "script": "void 0"}
    content["ui.json"] = json.dumps(ui).encode()
    first = ext.store.install(content)
    pin = {"name": "sample", "revision": first["revision"], "expected_generation": 0}
    assert call(service, "extension:activate", pin)["result"]["ui_ids"]
    rendered = get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")["ui_library"][0]
    assert rendered["script"] == ui["script"] and rendered["style"] == ui["style"]
    content["ui.json"] = b'{"kind":"unsupported"}'
    second = ext.store.install(content)
    with pytest.raises(ValueError):
        ext.call("extension:activate", {**pin, "revision": second["revision"],
                                        "expected_generation": 1})
    prior = next(s for s in ext.store.list() if s["revision"] == first["revision"])
    assert prior["state"] == "active"
