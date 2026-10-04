"""Shipped browser surfaces -> HTTP -> real two-owner component-copy handlers.

Only authentication and MCP transport are synthetic. Owner reads, publish,
preview, trusted approval, private copies and UI persistence use real stores.
No live model: the recipient's copied workflow runs through a counting provider.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_app_two_surfaces_browser import browser as _browser
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    SCOUT,
    SCRIBE,
    UI,
    UNIVERSE,
    _as,
    _bob_files,
    _bobs_branches,
    _pin_data_dir,  # noqa: F401
    _real_providers,
)
from tests.test_command_center_packages import home as home  # noqa: F401
from tests.test_command_center_system_copy import _legacy
from tinyassets.api.app_ui import change_app_ui, write_app_ui
from tinyassets.api.graph_reads import read_graph
from tinyassets.api.pending_requests import answer_request, try_package
from tinyassets.automations import STATE_PAUSED, AutomationStore
from tinyassets.custom_agents import get_app_ui, get_definition, save_app_ui
from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

browser = _browser
pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def system_server(home):
    from urllib.parse import parse_qs, urlsplit

    from tinyassets.storage.owner_ui_prefs import read_prefs, write_pref

    calls, failures = [], []
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, body, *, content_type="application/json", headers=None, status=200):
            raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/app":
                self.reply(html, content_type="text/html; charset=utf-8",
                           headers={"Content-Security-Policy": csp})
            elif path == "/app/ui-frame":
                self.reply(BOOTSTRAP_HTML, content_type="text/html; charset=utf-8",
                           headers=FRAME_HEADERS)
            elif path == "/fixture/identity":
                self.reply({"principal_id": BOB, "universe_id": BOB_UNIVERSE,
                            "setup": "connected"})
            elif path == "/app/ui-prefs":
                assert self.headers.get("Authorization") == "Bearer synthetic-bob"
                query = parse_qs(urlsplit(self.path).query)
                with _as(BOB):
                    prefs = read_prefs(home, owner_user_id=BOB,
                                       agent_id=query.get("agent", ["main"])[0],
                                       viewport=query.get("viewport", [""])[0])
                self.reply({"prefs": prefs})
            else:
                self.reply({"error": "not_found"}, status=404)

        def do_POST(self):
            if self.path == "/app/token":
                # Normal signed-out boot probes its absent refresh cookie.
                self.reply({"error": "authentication_required"}, status=401)
                return
            try:
                assert self.headers.get("Authorization") == "Bearer synthetic-bob"
                args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # Thread-local actor binding for EVERY RPC, never a process-wide actor.
                with _as(BOB):
                    if self.path == "/app/api/read":
                        assert args.get("graph_id", BOB_UNIVERSE) == BOB_UNIVERSE
                        args.setdefault("graph_id", BOB_UNIVERSE)
                        result = json.loads(read_graph(**args))
                        operation = "read:" + args["target"]
                    elif self.path == "/app/ui-prefs":
                        write_pref(home, owner_user_id=BOB,
                                   agent_id=args.get("agent", "main"),
                                   viewport=args.get("viewport", ""),
                                   key=args.get("key", ""), value=args.get("value"))
                        result = {"saved": True}
                        operation = "save:ui_prefs"
                    elif self.path == "/fixture/mcp":
                        assert args["name"] == "write_graph"
                        args = args["args"]
                        assert args.get("graph_id", BOB_UNIVERSE) == BOB_UNIVERSE
                        operation = args["operation"]
                        payload = args.get("payload_json", "{}")
                        if args["target"] == "connection":
                            handler = {"try_package": try_package,
                                       "answer_request": answer_request}[operation]
                            result = handler(universe_id=BOB_UNIVERSE, payload=payload)
                        elif args["target"] == "app_ui":
                            if operation == "save":
                                result = write_app_ui(universe_id=BOB_UNIVERSE, payload=payload,
                                                      expected_revision=args["expected_revision"])
                            else:
                                result = change_app_ui(universe_id=BOB_UNIVERSE,
                                                       operation=operation, payload=payload)
                        else:
                            raise AssertionError(f"unexpected write target: {args['target']}")
                    else:
                        raise AssertionError(f"unexpected POST: {self.path}")
                calls.append((operation, result))
                self.reply(result)
            except Exception as exc:
                failures.append(repr(exc))
                self.reply({"error": "fixture_transport_failure", "detail": str(exc)}, status=500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _rpc(origin, path, args):
    request = Request(origin + path, data=json.dumps(args).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer synthetic-bob"})
    with urlopen(request, timeout=15) as response:  # hermetic-ok: loopback fixture only
        return json.load(response)


def _enter(page, origin):
    page.goto(origin + "/app")
    page.wait_for_selector("#view-signin", state="visible")
    page.evaluate("""async ({owner,home}) => {
      sessionStorage.setItem(TOKEN_KEY,'synthetic-bob');
      sessionStorage.setItem(EXP_KEY,String(Date.now()+3600*1000));
      fetchMe=async()=> (await fetch('/fixture/identity')).json();
      MCP.callTool=async(name,args)=>{
        const r=await fetch('/fixture/mcp',{method:'POST',
          headers:{'Content-Type':'application/json','Authorization':'Bearer synthetic-bob'},
          body:JSON.stringify({name,args})});
        if(!r.ok) throw new Error('fixture MCP transport failed');
        return r.json();
      };
      window.acceptRelays=[];
      sendTurn=(...args)=>{window.acceptRelays.push(args);};
      setQueueScope(home);setQueueOwner(owner);showView('chat');refreshChatCloud();
      AppUI.enabled=true;AppUI.home=home;AppUI.principal=owner;
      document.getElementById('btn-ui-switch').hidden=false;
      await AppUI.load();
    }""", {"owner": BOB, "home": BOB_UNIVERSE})


def _open_switcher(page):
    # This command lives inside the real cloud menu, which starts collapsed.
    if page.locator("#chat-cloud-bubble").is_visible():
        page.locator("#chat-cloud-bubble").click()
    if not page.locator("#btn-ui-switch").is_visible():
        page.locator("#btn-cloud-menu").click()
    page.locator("#btn-ui-switch").click()


def _assert_copy_and_run(home, definition_id, source, alice_ui, alice_automations, before):
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.api.automations import automations
    from tinyassets.automations import run_due_automation
    from tinyassets.runs import get_run, wait_for

    copies = _bobs_branches(home)
    ids = {row["branch_def_id"] for row in copies}
    assert len(ids) == 2 and ids.isdisjoint({SCOUT, SCRIBE})
    assert all(row["visibility"] == "private" and row["author"] == BOB for row in copies)
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert len(library) == 2 and library[0]["ui_id"] == "my-own"
    assert set(library[1]["workflow_refs"].values()) == ids
    rows = {row.name: row for row in AutomationStore(home).list(universe_id=BOB_UNIVERSE)}
    assert len(rows) == 2
    assert all(row.desired_state == STATE_PAUSED and row.owner_principal_id == BOB
               and row.branch_def_id in ids and row.inputs == {} for row in rows.values())
    assert rows["scribe follows"].event_filter["branch_def_id"] == (
        rows["scout heartbeat"].branch_def_id)
    assert _bob_files(home) == before  # component-only: never an invented file import
    assert get_definition(home, definition_id) == source
    assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE) == alice_ui
    assert AutomationStore(home).list(universe_id=UNIVERSE) == alice_automations
    beat = rows["scout heartbeat"]
    with _as(BOB):
        resumed = automations(action="resume", universe_id=BOB_UNIVERSE,
                              automation_id=beat.automation_id, expected_revision=beat.revision)
    assert not resumed.get("error"), resumed
    beat = AutomationStore(home).get(beat.automation_id)
    fake = _CountingProvider()
    with _real_providers(codex=fake):
        outcome = run_due_automation(home, beat, "2026-10-01T12:05:00+00:00")
        run_id = str(outcome).rsplit(":", 1)[-1]
        wait_for(run_id, timeout=30)
    run = get_run(home, run_id) or {}
    assert run.get("status") == "completed", (outcome, run)
    assert run["branch_def_id"] == beat.branch_def_id and fake.calls
    assert get_definition(home, definition_id) == source
    assert AutomationStore(home).list(universe_id=UNIVERSE) == alice_automations


def _seed_own(home):
    own = {**UI, "ui_id": "my-own", "name": "Bob's own", "script": ""}
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                expected_revision=0, changes={"ui_library": [own]})


def test_loopback_transport_reaches_real_owner_handlers_and_runs_private_copy(home, system_server):
    """Nonbrowser control: the browser fixture cannot return canned install success."""
    origin, calls, failures = system_server
    placement = {"v": 1, "mode": "open", "open": {"x": 20, "y": 30, "w": 400, "h": 500},
                 "bubble": {"x": 40, "y": 50}}
    saved_pref = _rpc(origin, "/app/ui-prefs", {"agent": "main", "viewport": "wide",
                                               "key": "chat_cloud", "value": placement})
    assert saved_pref == {"saved": True}
    request = Request(origin + "/app/ui-prefs?agent=main&viewport=wide",
                      headers={"Authorization": "Bearer synthetic-bob"})
    with urlopen(request, timeout=15) as response:  # hermetic-ok: loopback fixture only
        assert json.load(response)["prefs"]["chat_cloud"] == placement
    _seed_own(home)
    definition_id = _legacy(home)
    source = get_definition(home, definition_id)
    alice_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    alice_automations = AutomationStore(home).list(universe_id=UNIVERSE)
    before = _bob_files(home)
    catalogue = _rpc(origin, "/app/api/read", {"target": "command_center_packages"})
    assert catalogue["packages"] == []
    assert catalogue["systems"][0]["agent_definition_id"] == definition_id
    ask = _rpc(origin, "/fixture/mcp", {"name": "write_graph", "args": {
        "target": "connection", "operation": "try_package",
        "payload_json": json.dumps({"agent_definition_id": definition_id})}})
    assert "request_id" in ask and not _bobs_branches(home)
    rail = _rpc(origin, "/app/api/read", {"target": "pending_requests"})
    assert ask["request_id"] in json.dumps(rail)
    own_row = _rpc(origin, "/app/api/read", {"target": "app_ui"})["app_ui"]
    assert own_row["ui_library"][0]["ui_id"] == "my-own"
    assert own_row["platform_default"]["ui_id"] == "platform:blank"
    bindings = _rpc(origin, "/app/api/read", {"target": "agent_bindings", "limit": 100})
    assert "bindings" in bindings
    done = _rpc(origin, "/fixture/mcp", {"name": "write_graph", "args": {
        "target": "connection", "operation": "answer_request",
        "payload_json": json.dumps({"request_id": ask["request_id"], "values": {}})}})
    assert done.get("installed"), done
    _assert_copy_and_run(home, definition_id, source, alice_ui, alice_automations, before)
    row = _rpc(origin, "/app/api/read", {"target": "app_ui"})["app_ui"]
    saved = _rpc(origin, "/fixture/mcp", {"name": "write_graph", "args": {
        "target": "app_ui", "operation": "save", "expected_revision": row["revision"],
        "payload_json": json.dumps({"ui_selection": {
            "version": 1, "state": "active", "ui_id": "village"}})}})
    assert saved["status"] == "saved", saved
    reread = _rpc(origin, "/app/api/read", {"target": "app_ui"})["app_ui"]
    assert reread["ui_selection"]["ui_id"] == "village"
    assert not failures and any(op == "answer_request" for op, _ in calls)


@pytest.mark.real_browser
def test_shipped_frame_previews_system_trusted_rail_copies_and_navigation_persists(
    home, system_server, browser,
):
    from playwright.sync_api import expect

    origin, calls, failures = system_server
    _seed_own(home)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _enter(page, origin)
        frame = page.frame_locator("#ui-frame")
        frame.get_by_role("button", name="Try someone else's", exact=True).click()
        expect(frame.locator("#packages")).to_contain_text("No shared command centers")
        definition_id = _legacy(home)
        source = get_definition(home, definition_id)
        assert "package" not in source["components"]
        alice_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
        alice_automations = AutomationStore(home).list(universe_id=UNIVERSE)
        before = _bob_files(home)
        page.evaluate("async()=>{await AppUI.load();}")
        frame.get_by_role("button", name="Try someone else's", exact=True).click()
        expect(frame.locator("#packages")).to_contain_text("Components only; no files")
        frame.get_by_role("button", name="Preview copy", exact=True).click()
        expect(page.locator("#ui-preview")).to_contain_text("Visual preview")
        assert not any(op == "try_package" for op, _ in calls)
        page.get_by_role("button", name="Copy into my command center", exact=True).click()
        assert not _bobs_branches(home) and _bob_files(home) == before
        assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
        page.evaluate("async()=>{await refreshRail();}")
        # Copy opens this exact request. Await its visible consent instead of
        # racing the outgoing bubble animation with a second toggle.
        tab = page.locator("#rail-items .rtab").filter(has_text="GTM Village")
        expect(tab).to_have_count(1)
        accept = tab.get_by_role("button", name="Accept", exact=True)
        expect(accept).to_be_visible()
        expect(tab).to_contain_text("Component-only copy")
        accept.click()  # actual trusted parent-document confirmation, never frame approval
        expect(tab).to_have_count(0)
        assert any(op == "answer_request" and result.get("installed") for op, result in calls)
        _assert_copy_and_run(home, definition_id, source, alice_ui, alice_automations, before)
        page.evaluate("async()=>{await AppUI.load();}")
        for name, expected_id in [("Bob's own", "my-own"), ("Village", "village")]:
            _open_switcher(page)
            page.get_by_role("button", name="Use " + name, exact=True).click()
            expect(page.locator("#ui-status")).to_contain_text("Now using " + name + ".")
            page.locator("#btn-ui-close").click()
            # Real reload and rebind the same synthetic owner; stored choice is read again.
            page.evaluate("sessionStorage.clear()")
            _enter(page, origin)
            assert page.evaluate("AppUI.active.ui_id") == expected_id
            _open_switcher(page)
            page.locator("#ui-dialog").get_by_role(
                "button", name="Try someone else's", exact=True).click()
            expect(page.locator("#ui-dialog")).to_contain_text("GTM Village")
            page.locator("#btn-ui-close").click()
        from tests.test_command_center_packages import _published

        _published(home)  # Explicit second publish: a real file package beside the legacy system.
        assert "package" not in get_definition(home, definition_id)["components"]
        _open_switcher(page)
        page.locator("#ui-dialog").get_by_role(
            "button", name="Try someone else's", exact=True).click()
        expect(page.locator("#ui-dialog")).to_contain_text("File package")
        expect(page.locator("#ui-dialog")).to_contain_text("Public system")
        page.get_by_role("button", name="Blank command center", exact=True).click()
        expect(page.locator("#ui-status")).to_contain_text("Default chat restored.")
        page.evaluate("sessionStorage.clear()")
        _enter(page, origin)
        assert page.evaluate("AppUI.isPlatformDefault()") is True
        assert len(_bobs_branches(home)) == 2
        assert not failures
    finally:
        page.close()


@pytest.mark.real_browser
def test_visual_preview_has_no_owner_bridge_and_copy_requires_visible_consent(
    home, system_server, browser, tmp_path,
):
    import sqlite3
    from copy import deepcopy

    from playwright.sync_api import expect

    from tinyassets.custom_agents import publish_definition
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.storage import db_path

    _seed_own(home)
    original = _legacy(home)
    components = deepcopy(get_definition(home, original)["components"])
    ui = next(c for c in components.values() if c["kind"] == "tinyassets.app-ui.v1")
    ui["markup"] = '<h1>Village skyline</h1><p id="preview-proof">Loading</p>'
    ui["script"] = """(async()=>{
      const agents=await tinyassets.listAgents();
      const failures=[];
      if(agents.preview){
        for(const action of ['send_message','emit','read_file','packages.try']){
          try{await tinyassets.call(action,{text:'never send',name:'never run'});}
          catch(error){failures.push(action);}
        }
        let isolated=false;try{parent.document.body;}catch(error){isolated=true;}
        document.getElementById('preview-proof').textContent=
          'preview agents '+agents.agents.length+'; refused '+failures.length+
          '; isolated '+isolated;
      }else document.getElementById('preview-proof').textContent='Your copied screen';
    })();"""
    publication = publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Visual Village", "description": "A village of your own",
        "tags": ["tinyassets.system.v1"], "components": components})
    source = get_definition(home, publication["agent_definition_id"])
    publisher_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    with sqlite3.connect(db_path(home)) as db:
        db.execute("DELETE FROM provider_assignments WHERE universe_id = ?", (BOB_UNIVERSE,))
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
    before_files = _bob_files(home)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _enter(page, origin)
        page.evaluate("()=>{MCP.converse=async()=>{throw Error('NO MODEL IS AVAILABLE');};}")
        # The collapsed bubble has its own trusted navigation, independent of the frame.
        page.evaluate("setChatCloudMode('bubble')")
        page.get_by_role("button", name="Browse / Switch", exact=True).click()
        expect(page.locator("#ui-dialog")).to_contain_text("A village of your own")
        page.get_by_role("button", name="Preview Visual Village", exact=True).click()
        preview = page.frame_locator("#ui-preview-frame")
        expect(preview.get_by_role("heading", name="Village skyline")).to_be_visible()
        expect(preview.locator("#preview-proof")).to_have_text(
            "preview agents 0; refused 4; isolated true")
        box = page.locator(".ui-preview-viewport").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] * .8, box["y"] + 60)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * .2, box["y"] + 60, steps=8)
        page.mouse.up()
        expect(page.locator("#ui-preview").get_by_text("GTM Village", exact=True)).to_be_visible()
        page.get_by_role("button", name="Next design", exact=True).click()
        expect(preview.get_by_role("heading", name="Village skyline")).to_be_visible()
        page.screenshot(path=str(tmp_path / "visual-preview.png"))
        assert not any(op == "try_package" for op, _ in calls)
        assert not _bobs_branches(home)
        assert page.evaluate("window.acceptRelays") == []
        assert _bob_files(home) == before_files
        page.get_by_role("button", name="Copy into my command center", exact=True).click()
        tab = page.locator("#rail-items .rtab").filter(has_text="Visual Village")
        try:
            expect(tab.get_by_role("button", name="Accept", exact=True)).to_be_visible()
        except AssertionError:
            print(page.locator("body").inner_text())
            print([(op, result.get("error"), result.get("detail"), result.get("request_id"))
                   for op, result in calls])
            raise
        assert not _bobs_branches(home)
        tab.get_by_role("button", name="Accept", exact=True).click()
        expect(page.get_by_role("button", name="Open copied screen", exact=True)).to_be_visible()
        assert page.evaluate("window.acceptRelays") == []
        assert len(_bobs_branches(home)) == 2
        assert all(row.desired_state == STATE_PAUSED for row in AutomationStore(home).list(
            universe_id=BOB_UNIVERSE))
        assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE) == publisher_ui
        assert get_definition(home, publication["agent_definition_id"]) == source
        assert _bob_files(home) == before_files
        page.get_by_role("button", name="Open copied screen", exact=True).click()
        expect(page.frame_locator("#ui-frame").get_by_role(
            "heading", name="Village skyline")).to_be_visible()
        expect(page.get_by_role("button", name="Browse / Switch command centers",
                                exact=True)).to_be_visible()
        assert any(c["ui_id"] == "my-own" for c in get_app_ui(
            home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"])
        page.evaluate("sessionStorage.clear()")
        _enter(page, origin)
        expect(page.frame_locator("#ui-frame").get_by_role(
            "heading", name="Village skyline")).to_be_visible()
        page.evaluate("setChatCloudMode('bubble')")
        page.get_by_role("button", name="Browse / Switch", exact=True).click()
        page.get_by_role("button", name="Preview Visual Village", exact=True).click()
        expect(page.locator("#ui-preview-frame")).to_be_visible()
        # A synthetic account transition must destroy the old preview, not retain its bridge.
        page.evaluate("AppUI.reset();AppUI.enabled=true;AppUI.home='other-home';AppUI.principal='other-owner';AppUI.paint();")
        expect(page.locator("#ui-preview-frame")).to_have_count(0)
        expect(page.get_by_role("button", name="Build your own", exact=True)).to_be_enabled()
        assert not failures
    finally:
        page.close()
