"""Real publishing and owner receipts; only the isolated renderer is replaced."""
import json
import time

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _publish_action,
    home,  # noqa: F401
)
from tests.test_ui_preview import _png
from tinyassets import ui_preview

pytestmark = pytest.mark.usefixtures("cloud_runtime")
_REAL_RENDER = ui_preview._render_spec




@pytest.fixture(autouse=True)
def renderer(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    captured = []

    def render(spec, wall_seconds, **_):
        captured.append(spec)
        return {"png": _png()}

    monkeypatch.setattr(ui_preview, "_render_spec", render)
    return captured


def wait_for_preview(base, done):
    """Read the actual owner receipt after background completion, never render here."""
    from tinyassets.storage.pending_requests import get_request

    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        row = get_request(base / UNIVERSE, done["request_id"])
        completion = row["answer"]["completion"]
        if completion["preview_status"] != "pending":
            # Ensure the worker has released admission before the next publish.
            from tinyassets.publication_completion import _BACKGROUND_SLOT

            assert _BACKGROUND_SLOT.acquire(timeout=5)
            _BACKGROUND_SLOT.release()
            return {**done, "completion": completion}
        time.sleep(0.01)
    pytest.fail("Publication preview did not complete within its render bound")




def test_preview_uses_public_definition_even_if_private_screen_changes(home, renderer, monkeypatch):  # noqa: F811
    from tinyassets.api import publish_requests
    from tinyassets.app_ui_store import get_app_ui, save_app_ui

    real_publish = publish_requests._publish_snapshot

    def publish_then_edit(*args, **kwargs):
        receipt = real_publish(*args, **kwargs)
        row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
        screen = {**row["ui_library"][0], "markup": "PRIVATE AFTER PUBLICATION"}
        save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                    expected_revision=row["revision"], changes={"ui_library": [screen]})
        return receipt

    monkeypatch.setattr(publish_requests, "_publish_snapshot", publish_then_edit)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    done = wait_for_preview(home, done)
    assert done["completion"]["preview_status"] == "ready", done
    assert "PRIVATE AFTER PUBLICATION" not in json.dumps(renderer)
    assert "PRIVATE AFTER PUBLICATION" not in json.dumps(done)








def test_direct_publication_returns_completion_and_respects_owner_context(home):  # noqa: F811
    from tinyassets.api.commons_catalog import commons_catalog
    from tinyassets.api.publish_requests import build_snapshot, validate_action
    from tinyassets.universe_server import write_graph

    with _as(OWNER):
        doc = build_snapshot(UNIVERSE, validate_action(_publish_action()))["definition"]
        done = json.loads(write_graph(target="agent", operation="publish", graph_id=UNIVERSE,
                                      payload_json=json.dumps(doc)))
        assert done["completion"]["listing_id"] == done["agent"]["agent_definition_id"]
        assert done["completion"]["preview_status"] == "ready"
        # The commons version guard (#4475) refuses a publication built from a
        # snapshot older than the bundle's current version, so the wrong-owner
        # attempt uses a fresh snapshot taken after the first publication.
        doc = build_snapshot(UNIVERSE, validate_action(_publish_action()))["definition"]
        denied = commons_catalog(action="publish_agent", universe_id=BOB_UNIVERSE, payload=doc)
        assert denied["completion"]["preview_status"] == "owner_context_required"
        assert denied["completion"]["preview_image_path"] is None




def test_public_preview_refuses_private_asset_references():
    with pytest.raises(ui_preview.PreviewUnavailable, match="assets_unsupported"):
        ui_preview.preview_public_component({"assets": {"secret.png": {"sha256": "private"}}})


def test_release_successor_is_an_update_with_immutable_version(home):  # noqa: F811
    from tests.test_command_center_release_surface import action

    first = _ask(OWNER, UNIVERSE, action())
    initial = _answer(OWNER, UNIVERSE, first["request_id"])
    initial = wait_for_preview(home, initial)
    release = initial["release"]
    next_ask = _ask(OWNER, UNIVERSE, {
        **action(series_id=release["series_id"], parent_release_id=release["release_id"]),
        "description": "Updated public screen bundle",
    })
    updated = _answer(OWNER, UNIVERSE, next_ask["request_id"])
    updated = wait_for_preview(home, updated)
    assert updated["completion"]["change_kind"] == "update"
    assert updated["completion"]["version"] != initial["completion"]["version"]


def test_publication_preview_selector_is_owner_scoped(home, monkeypatch):  # noqa: F811
    from tests.engine_authority_helpers import mock_engine_admission, seed_engine_authority
    from tinyassets import engine_mcp_server as server
    from tinyassets.api.app_ui import preview_app_ui

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    done = wait_for_preview(home, done)
    selector = "publication:" + done["agent_definition_id"]
    with _as(BOB):
        assert preview_app_ui(universe_id=BOB_UNIVERSE, ui_id=selector) == {
            "error": "app_ui_not_found"}
    monkeypatch.setattr(server, "_ACTOR_ID", OWNER)
    monkeypatch.setattr(server, "_GRAPH_ID", UNIVERSE)
    seed_engine_authority(home, actor=OWNER, graph=UNIVERSE)
    mock_engine_admission(monkeypatch, {UNIVERSE})
    response = server.read_graph(target="app_ui_preview", query=selector)
    assert done["completion"]["preview_image_path"] in response
