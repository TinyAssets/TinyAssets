"""An app is a command center: one manifest, no second store, a launch list.

The projection reads the command center the account already has. It does not
rename it, rewrite its screen, or move its owner. The launch list is the
signed-in account's own apps, and Open returns to the command center the app
already shows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tinyassets.app_object import BLANK_SCREEN, KIND_COMMAND_CENTER, list_owned_apps
from tinyassets.app_ui_store import get_app_ui, save_app_ui
from tinyassets.daemon_server import (
    _connect,
    ensure_universe_registered,
    get_universe,
    set_founder_home,
    set_universe_display_name,
)
from tinyassets.universe_owner import owner_of, record_creation

ALICE, BOB = "alice", "bob"
HOME, OTHER = "u-alice", "u-bob"


def _bundle(ui_id: str = "office") -> dict:
    return {
        "kind": "tinyassets.app-ui.v1",
        "version": 1,
        "ui_id": ui_id,
        "name": "Office",
        "markup": "<div id=lobby>Lobby</div>",
        "style": "#lobby{color:red}",
        "script": "tinyassets.whoami()",
    }


def _own(base: Path, owner: str, uid: str, *, name: str = "", description: str = "") -> None:
    directory = base / uid
    directory.mkdir()
    ensure_universe_registered(
        base,
        universe_id=uid,
        universe_path=directory,
        display_name=name or uid,
        metadata={"description": description} if description else {},
    )
    with _connect(base) as conn:
        record_creation(conn, universe_id=uid, owner_id=owner)


def _rows(base: Path) -> dict:
    with _connect(base) as conn:
        universes = [
            tuple(row)
            for row in conn.execute(
                "SELECT universe_id, display_name, metadata_json FROM universes ORDER BY 1"
            )
        ]
        screens = [
            tuple(row)
            for row in conn.execute(
                "SELECT owner_user_id, universe_id, ui_library_json, ui_selection_json, revision "
                "FROM universe_app_ui ORDER BY 1, 2"
            )
        ]
        owners = [
            tuple(row)
            for row in conn.execute(
                "SELECT universe_id, owner_id, source FROM universe_owner ORDER BY 1"
            )
        ]
    return {"universes": universes, "screens": screens, "owners": owners}


def test_a_command_center_is_an_app_and_listing_it_changes_nothing(tmp_path: Path) -> None:
    _own(tmp_path, ALICE, HOME, name="Garden", description="Tracks the garden")
    set_founder_home(tmp_path, founder_sub=ALICE, universe_id=HOME)
    saved = save_app_ui(
        tmp_path,
        owner_user_id=ALICE,
        universe_id=HOME,
        expected_revision=0,
        changes={"ui_library": [_bundle()]},
    )
    chosen = save_app_ui(
        tmp_path,
        owner_user_id=ALICE,
        universe_id=HOME,
        expected_revision=saved["revision"],
        changes={"ui_selection": {"version": 1, "state": "active", "ui_id": "office"}},
    )
    before_ui = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)
    before_rows = _rows(tmp_path)
    before_name = get_universe(tmp_path, universe_id=HOME)["display_name"]

    listed = list_owned_apps(tmp_path, ALICE)

    assert listed["apps"] == [
        {
            "id": HOME,
            "home": True,
            "manifest": {
                "name": "Garden",
                "description": "Tracks the garden",
                "kind": KIND_COMMAND_CENTER,
                "default_screen": {"ui_id": "office", "name": "Office"},
                "owner": ALICE,
                "author": ALICE,
                "version": chosen["revision"],
            },
            "open": {
                "method": "signed_in_command_center",
                "href": "/app",
                "screen": {"ui_id": "office", "name": "Office"},
            },
        }
    ]
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == before_ui
    assert get_universe(tmp_path, universe_id=HOME)["display_name"] == before_name
    assert _rows(tmp_path) == before_rows


def test_an_empty_command_center_opens_the_blank_screen(tmp_path: Path) -> None:
    _own(tmp_path, ALICE, HOME)
    set_founder_home(tmp_path, founder_sub=ALICE, universe_id=HOME)
    before = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)

    manifest = list_owned_apps(tmp_path, ALICE)["apps"][0]["manifest"]

    assert manifest["name"] == ""
    assert manifest["description"] == ""
    assert manifest["kind"] == KIND_COMMAND_CENTER
    assert manifest["default_screen"] == BLANK_SCREEN
    assert manifest["version"] == 0
    assert manifest["owner"] == ALICE and manifest["author"] == ALICE
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == before


def test_a_missing_screen_still_opens_blank_and_keeps_the_choice(tmp_path: Path) -> None:
    _own(tmp_path, ALICE, HOME, name="Garden")
    save_app_ui(
        tmp_path,
        owner_user_id=ALICE,
        universe_id=HOME,
        expected_revision=0,
        changes={"ui_selection": {"version": 1, "state": "active", "ui_id": "gone"}},
    )
    before = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)

    app = list_owned_apps(tmp_path, ALICE)["apps"][0]

    assert app["manifest"]["default_screen"] == BLANK_SCREEN
    assert app["manifest"]["version"] == before["revision"]
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == before
    assert before["ui_selection"]["ui_id"] == "gone"


def test_a_name_that_lives_only_in_the_command_center_file_is_kept(tmp_path: Path) -> None:
    directory = tmp_path / HOME
    directory.mkdir()
    (directory / "universe.json").write_text(
        json.dumps({"id": HOME, "name": "Garden", "description": "From the file"}) + "\n",
        encoding="utf-8",
    )
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)
    with _connect(tmp_path) as conn:
        record_creation(conn, universe_id=HOME, owner_id=ALICE)
    raw = (directory / "universe.json").read_bytes()

    manifest = list_owned_apps(tmp_path, ALICE)["apps"][0]["manifest"]

    assert manifest["name"] == "Garden"
    assert manifest["description"] == "From the file"
    assert (directory / "universe.json").read_bytes() == raw


def test_another_accounts_command_center_is_not_an_app_here(tmp_path: Path) -> None:
    _own(tmp_path, ALICE, HOME, name="Garden")
    _own(tmp_path, BOB, OTHER, name="Bob's place")
    set_founder_home(tmp_path, founder_sub=ALICE, universe_id=OTHER)
    set_universe_display_name(tmp_path, universe_id=HOME, display_name="Garden")

    listed = list_owned_apps(tmp_path, ALICE)

    assert [app["id"] for app in listed["apps"]] == [HOME]
    assert listed["apps"][0]["home"] is False
    assert listed["apps"][0]["open"]["method"] == "unavailable"
    assert OTHER not in json.dumps(listed)
    assert owner_of(tmp_path, OTHER) == BOB


def test_an_unattributed_home_is_listed_without_claiming_a_new_owner(tmp_path: Path) -> None:
    directory = tmp_path / HOME
    directory.mkdir()
    ensure_universe_registered(
        tmp_path,
        universe_id=HOME,
        universe_path=directory,
        display_name="Garden",
    )
    set_founder_home(tmp_path, founder_sub=ALICE, universe_id=HOME)
    assert owner_of(tmp_path, HOME) is None

    listed = list_owned_apps(tmp_path, ALICE)

    assert [app["id"] for app in listed["apps"]] == [HOME]
    assert listed["apps"][0]["home"] is True
    assert listed["apps"][0]["manifest"]["owner"] == ALICE
    assert owner_of(tmp_path, HOME) is None


def test_apps_route_is_the_signed_in_accounts_list(tmp_path, monkeypatch) -> None:
    from tests.test_onboarding_openai_device import _drive_get, _user

    _own(tmp_path, ALICE, HOME, name="Garden", description="Tracks the garden")
    _own(tmp_path, BOB, OTHER, name="Elsewhere")
    set_founder_home(tmp_path, founder_sub=ALICE, universe_id=HOME)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    assert _drive_get("/app/apps", monkeypatch=monkeypatch)[0] == 401
    status, document = _drive_get("/app/apps", identity=_user(ALICE), monkeypatch=monkeypatch)

    assert status == 200
    assert [app["id"] for app in document["apps"]] == [HOME]
    assert document["apps"][0]["manifest"]["name"] == "Garden"
    assert document["apps"][0]["open"]["href"] == "/app"
    assert "host_path" not in json.dumps(document)
    assert OTHER not in json.dumps(document)


def test_apps_route_is_absent_while_the_app_is_dark(monkeypatch) -> None:
    import asyncio

    from starlette.requests import Request

    import tinyassets.onboarding as onboarding
    from tests.test_onboarding_openai_device import _user
    from tinyassets.auth.middleware import identity_context

    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    route = next(item for item in onboarding.onboarding_routes() if item.path == "/app/apps")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/app/apps",
        "headers": [],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run():
        with identity_context(_user(ALICE)):
            return await route.endpoint(Request(scope, receive))

    response = asyncio.run(run())
    assert response.status_code == 404
    assert response.body == b"Not Found"


def test_the_launch_surface_opens_the_signed_in_app_on_its_screen() -> None:
    from tests.test_app_browser_notifications import run_js
    from tests.test_onboarding_app import _js_function
    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    assert 'id="btn-apps"' in html
    assert 'id="view-apps"' in html
    assert 'fetch("/app/apps"' in _js_function(html, "showApps")
    assert "innerHTML" not in _js_function(html, "paintApps")
    source = (
        "let shown=''; function showView(name){ shown=name; }\n"
        + _js_function(html, "appCards")
        + "\n"
        + _js_function(html, "openListedApp")
        + """
        const doc={apps:[{
          id:'u-secret',
          home:true,
          manifest:{name:'Garden', description:'Tracks the garden', kind:'command_center',
            default_screen:{ui_id:'office', name:'Office'}, owner:'alice',
            author:'alice', version:2},
          open:{method:'signed_in_command_center', href:'/app',
            screen:{ui_id:'office', name:'Office'}}
        },{
          id:'u-other',
          home:false,
          manifest:{name:'', description:'', kind:'command_center',
            default_screen:{ui_id:'platform:blank', name:'Blank command center'},
            owner:'alice', author:'alice', version:0},
          open:{method:'unavailable', reason:'Not the signed-in command center.'}
        }]};
        const cards=appCards(doc);
        const opened=openListedApp(cards[0]);
        const afterOpen=shown;
        shown='';
        const refused=openListedApp(cards[1]);
        const leaked=JSON.stringify(cards).includes('u-secret');
        console.log(JSON.stringify({cards, opened, refused, afterOpen, shown, leaked}));
        """
    )
    out = run_js(source)
    assert out["cards"][0]["name"] == "Garden"
    assert out["cards"][0]["kind"] == "Command center"
    assert out["cards"][0]["version"] == 2
    assert out["cards"][0]["screen"] == "Office"
    assert out["cards"][0]["openable"] is True
    assert out["cards"][1]["name"] == "Command center"
    assert out["cards"][1]["openable"] is False
    assert out["opened"] == {"opened": True, "screen": "Office"}
    assert out["refused"]["opened"] is False
    assert "Not the signed-in command center." in out["refused"]["reason"]
    assert out["afterOpen"] == "chat"
    assert out["shown"] == ""
    assert out["leaked"] is False


def test_app_manifest_outside_its_module_is_a_second_path() -> None:
    from scripts import check_shapes

    pattern = check_shapes.ENTRY_SYMBOLS["N11"]
    assert re.search(pattern, "def app_manifest(")
    assert check_shapes.registered_path(
        "tinyassets/app_object.py", check_shapes.SHAPES["N11"].paths[0]
    )
    assert not check_shapes.registered_path(
        "tinyassets/apps.py", check_shapes.SHAPES["N11"].paths[0]
    )
    rows = check_shapes.registry_rows(
        (check_shapes.ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    )
    assert rows["N11"] == "converged"
