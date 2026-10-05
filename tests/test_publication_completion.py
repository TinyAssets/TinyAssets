"""Real publishing and owner receipts; only the isolated renderer is replaced."""
import json
import shutil
import sqlite3

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
from tinyassets.api.pending_requests import list_requests

pytestmark = pytest.mark.usefixtures("cloud_runtime")
_REAL_RENDER = ui_preview._render_spec


@pytest.fixture(autouse=True)
def renderer(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    captured = []

    def render(spec, wall_seconds):
        captured.append(spec)
        return {"png": _png()}

    monkeypatch.setattr(ui_preview, "_render_spec", render)
    return captured


def test_real_publish_and_update_reach_owner_answer(home, renderer):  # noqa: F811
    for version in (1, 2):
        ask = _ask(OWNER, UNIVERSE, _publish_action())
        assert "request_id" in ask, ask
        done = _answer(OWNER, UNIVERSE, ask["request_id"])
        assert done.get("published"), done
        receipt = done["completion"]
        assert receipt["listing_id"] == done["agent_definition_id"]
        assert receipt["share_url"] is None  # No listing-specific public URL exists.
        assert receipt["change_kind"] == ("new" if version == 1 else "update")
        assert receipt["version"] == version
        assert receipt["preview_status"] == "ready"
        image = home / UNIVERSE / receipt["preview_image_path"].removeprefix("/u/")
        assert image.read_bytes() == _png()
        with _as(OWNER):
            rows = list_requests(universe_id=UNIVERSE)["recently_answered"]
        row = next(r for r in rows if r["request_id"] == ask["request_id"])
        assert row["answer"]["completion"] == receipt
        with _as(BOB):
            assert "error" in list_requests(universe_id=UNIVERSE)
            assert ask["request_id"] not in json.dumps(list_requests(universe_id=BOB_UNIVERSE))
    assert len(renderer) == 2
    for spec in renderer:
        assert spec["universe_id"] == "preview"
        assert spec["owner_user_id"] == spec["base_path"] == ""
        assert spec["hashes"] == {} and spec["files"] == []


def test_preview_uses_public_definition_even_if_private_screen_changes(home, renderer, monkeypatch):  # noqa: F811
    from tinyassets.api import publish_requests
    from tinyassets.custom_agents import get_app_ui, save_app_ui

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
    assert done["completion"]["preview_status"] == "ready", done
    assert "PRIVATE AFTER PUBLICATION" not in json.dumps(renderer)
    assert "PRIVATE AFTER PUBLICATION" not in json.dumps(done)


def test_preview_failure_preserves_publication_and_records_no_image(home, monkeypatch):  # noqa: F811
    def unavailable(*args):
        raise ui_preview.PreviewUnavailable("browser unavailable")

    monkeypatch.setattr(ui_preview, "preview_public_component", unavailable)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] is True
    assert done["completion"]["preview_status"] == "unavailable"
    assert done["completion"]["preview_image_path"] is None
    with _as(OWNER):
        row = list_requests(universe_id=UNIVERSE)["recently_answered"][0]
        assert row["answer"]["completion"] == done["completion"]


@pytest.mark.parametrize("legacy", [False, True])
def test_finished_publish_retries_resolution_without_republishing(home, monkeypatch, legacy):  # noqa: F811
    from tinyassets import command_center_packages as packages
    from tinyassets.api import publish_requests
    from tinyassets.storage import pending_requests

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    request_id = ask["request_id"]
    renders = []

    def render_after_finish(*args):
        pin = packages.pin_for_request(home, universe_id=UNIVERSE, request_id=request_id)
        assert pin["state"] == "activated", "Rendering must not hold the publication claim"
        renders.append(pin["progress"]["agent_definition_id"])
        return {"png": _png()}

    monkeypatch.setattr(ui_preview, "_render_spec", render_after_finish)
    with monkeypatch.context() as failure:
        failure.setattr(pending_requests, "resolve_request", lambda *a, **k: False)
        first = _answer(OWNER, UNIVERSE, request_id)
    assert first["error"] == "request_resolution_unconfirmed"
    pin = packages.pin_for_request(home, universe_id=UNIVERSE, request_id=request_id)
    if legacy:
        progress = dict(pin["progress"])
        del progress["completion"]
        with sqlite3.connect(packages.database_path(home)) as conn:
            conn.execute("UPDATE pins SET progress_json=? WHERE pin_id=?",
                         (json.dumps(progress), pin["pin_id"]))

    def must_not_publish(*args, **kwargs):
        raise AssertionError("An activated pin must not publish again")

    monkeypatch.setattr(publish_requests, "execute_action", must_not_publish)
    retried = _answer(OWNER, UNIVERSE, request_id)
    assert retried["already_published"] is True
    assert retried["completion"]["listing_id"] == pin["progress"]["agent_definition_id"]
    assert retried["completion"]["preview_status"] == "ready"
    assert renders == [retried["agent_definition_id"]] * 2


def test_direct_idempotent_replay_retains_original_listing_kind(home):  # noqa: F811
    from tinyassets.api.custom_agents import custom_agents

    doc = {"schema_version": 1, "name": "Public identity", "description": "",
           "tags": [], "components": {"identity": {"kind": "soul", "config": {}}}}
    with _as(OWNER):
        first = custom_agents(action="publish_agent", payload=doc, idempotency_key="same")
        replay = custom_agents(action="publish_agent", payload=doc, idempotency_key="same")
    assert first["completion"] == replay["completion"]
    assert replay["completion"]["change_kind"] == "new"


def test_direct_publication_returns_completion_and_respects_owner_context(home):  # noqa: F811
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.api.publish_requests import build_snapshot, validate_action
    from tinyassets.universe_server import write_graph

    with _as(OWNER):
        doc = build_snapshot(UNIVERSE, validate_action(_publish_action()))["definition"]
        done = json.loads(write_graph(target="agent", operation="publish", graph_id=UNIVERSE,
                                      payload_json=json.dumps(doc)))
        assert done["completion"]["listing_id"] == done["agent"]["agent_definition_id"]
        assert done["completion"]["preview_status"] == "ready"
        denied = custom_agents(action="publish_agent", universe_id=BOB_UNIVERSE, payload=doc)
        assert denied["completion"]["preview_status"] == "owner_context_required"
        assert denied["completion"]["preview_image_path"] is None


def test_workflow_publication_has_a_version_and_explicitly_no_picture(home):  # noqa: F811
    from tests.test_command_center_packages import SCOUT

    ask = _ask(OWNER, UNIVERSE, {"type": "publish", "name": "Scout workflow",
                               "branch_ids": [SCOUT], "ui_id": ""})
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["completion"]["listing_id"] == done["agent_definition_id"]
    assert done["completion"]["version"]
    assert done["completion"]["preview_status"] == "no_screen"
    assert done["completion"]["preview_image_path"] is None


def test_public_preview_refuses_private_asset_references():
    with pytest.raises(ui_preview.PreviewUnavailable, match="assets_unsupported"):
        ui_preview.preview_public_component({"assets": {"secret.png": {"sha256": "private"}}})


def test_release_successor_is_an_update_with_immutable_version(home):  # noqa: F811
    from tests.test_command_center_release_surface import action

    first = _ask(OWNER, UNIVERSE, action())
    initial = _answer(OWNER, UNIVERSE, first["request_id"])
    release = initial["release"]
    next_ask = _ask(OWNER, UNIVERSE, {
        **action(series_id=release["series_id"], parent_release_id=release["release_id"]),
        "description": "Updated public screen bundle",
    })
    updated = _answer(OWNER, UNIVERSE, next_ask["request_id"])
    assert updated["completion"]["change_kind"] == "update"
    assert updated["completion"]["version"] != initial["completion"]["version"]


def test_publication_preview_selector_is_owner_scoped(home, monkeypatch):  # noqa: F811
    from tests.engine_authority_helpers import mock_engine_admission, seed_engine_authority
    from tinyassets import engine_mcp_server as server
    from tinyassets.api.app_ui import preview_app_ui

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
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


@pytest.mark.real_browser
def test_real_publish_completion_contains_rendered_public_screen(home, monkeypatch):  # noqa: F811
    from tests.test_in_platform_agent_systems import UI
    from tests.test_ui_preview import _pixel
    from tinyassets.custom_agents import get_app_ui, save_app_ui

    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    screen = {**UI, "markup": "<div>Published village</div>", "script": "",
              "style": "html,body{margin:0;background:rgb(32,80,192)}"}
    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                expected_revision=row["revision"], changes={"ui_library": [screen]})
    monkeypatch.setattr(ui_preview, "_render_spec", _REAL_RENDER)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    receipt = done["completion"]
    assert receipt["listing_id"] == done["agent_definition_id"]
    assert receipt["change_kind"] == "new" and receipt["version"] == 1
    if receipt["preview_status"] == "unavailable" and shutil.which("bwrap") is None:
        # The renderer runs inside the bubblewrap jail. Hosts without it (the
        # bare affected-tests runner) cannot render; real-browser-proof runs
        # this case in the Linux oracle and fails on any skip.
        pytest.skip("preview renderer needs bubblewrap; proven in real-browser-proof; "
                    "owner=Jonnyton expires=2026-11-01")
    assert receipt["preview_status"] == "ready", done
    image = home / UNIVERSE / receipt["preview_image_path"].removeprefix("/u/")
    assert _pixel(image.read_bytes(), 500, 300) == (32, 80, 192)
