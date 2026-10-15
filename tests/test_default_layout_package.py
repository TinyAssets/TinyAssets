"""The shipped fallback is also an ordinary, revocable extension card."""
from pathlib import Path

from tests.test_one_extension_unit import backend, call
from tinyassets.app_ui_store import get_app_ui
from tinyassets.command_center_picker import PLATFORM_DEFAULT_UI
from tinyassets.extension_capabilities import ExtensionCapabilities


def test_default_layout_uses_the_normal_install_activate_revoke_path(tmp_path):
    root = Path(__file__).parents[1] / "tinyassets" / "starter" / "default-layout"
    files = {path.name: path.read_bytes() for path in root.iterdir()}
    service = backend(tmp_path)
    ext = ExtensionCapabilities(service)
    installed = ext.store.install(files)
    pin = {"name": "default-layout", "revision": installed["revision"],
           "expected_generation": 0}
    active = call(service, "extension:activate", pin)["result"]
    doc = get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")
    component = doc["ui_library"][0]
    assert component["ui_id"] == active["ui_ids"][0]
    assert component["ui_id"].startswith("ta-ext-")
    assert component["ui_id"] != PLATFORM_DEFAULT_UI["ui_id"]
    assert {k: v for k, v in component.items() if k != "ui_id"} == {
        k: v for k, v in PLATFORM_DEFAULT_UI.items() if k != "ui_id"}
    assert not get_app_ui(tmp_path, owner_user_id="user-2", universe_id="home")["ui_library"]
    call(service, "extension:revoke", {**pin, "expected_generation": 1})
    assert not get_app_ui(tmp_path, owner_user_id="user-1", universe_id="home")["ui_library"]
