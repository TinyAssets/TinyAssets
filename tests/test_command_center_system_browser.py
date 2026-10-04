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
from tinyassets.api.status import get_status
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
            try:
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                # Real reload/account transitions cancel in-flight responses.
                # Handler errors still reach failures; a closed client does not.
                return

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
            if (self.path in {"/app/api/read", "/app/api/status", "/app/turn/pending",
                              "/app/ui-prefs"}
                    and not self.headers.get("Authorization")):
                # Background reads can finish during the real signed-out reload.
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
                    elif self.path == "/app/turn/pending":
                        from tinyassets import addressed_agents, agent_steering

                        assert args["universe_id"] == BOB_UNIVERSE and "claim" not in args
                        addressed = addressed_agents.resolve(
                            home, universe_id=BOB_UNIVERSE, owner=BOB,
                            agent_id=args.get("agent_id"))
                        session = addressed_agents.memory_session(
                            BOB, addressed.agent_id if addressed else addressed_agents.MAIN_AGENT)
                        key = "thread:" + session
                        result = {"pending": [
                            {"id": row.id, "text": row.text, "state": row.state,
                             "created_at": row.created_at}
                            for row in agent_steering.pending(home / BOB_UNIVERSE, key)],
                            "active": agent_steering.active(home / BOB_UNIVERSE, key)}
                        operation = "pending:" + args.get("agent_id", "main")
                    elif self.path == "/app/api/status":
                        result = json.loads(get_status(universe_id=BOB_UNIVERSE, **args))
                        operation = "status:" + args.get("conversation_agent", "main")
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
                        if (args["target"] == "connection" and operation in {
                                "preview_center_update", "answer_center_update",
                                "preview_center_policy", "answer_center_policy",
                                "register_center_copy"}):
                            from tinyassets.api.command_center_update_surface import write_update

                            result = write_update(universe_id=BOB_UNIVERSE,
                                                  operation=operation, payload=payload)
                        elif args["target"] == "connection":
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
        expect(frame.locator("#packages")).to_contain_text("0 public agent templates")
        frame.get_by_role("button", name="Preview copy", exact=True).click()
        expect(page.locator("#ui-preview")).to_contain_text("Visual preview")
        assert not any(op == "try_package" for op, _ in calls)
        page.get_by_role("button", name="Copy into my command center", exact=True).click()
        assert not _bobs_branches(home) and _bob_files(home) == before
        assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
        page.evaluate("async()=>{await refreshRail();}")
        # Copy opens its exact request; wait for that state rather than racing
        # the outgoing bubble animation with a second toggle.
        tab = page.locator("#rail-items .rtab").filter(has_text="GTM Village")
        expect(tab).to_have_count(1)
        accept = tab.get_by_role("button", name="Accept", exact=True)
        expect(accept).to_be_visible()
        expect(tab).to_contain_text("Component-only copy")
        expect(tab).to_contain_text("No public chat-agent templates are included")
        expect(tab).to_contain_text("no chat agents will be copied")
        expect(tab).to_contain_text("shows only your own agents")
        expect(tab).to_contain_text("republish")
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
            expect(page.locator("#ui-dialog")).to_contain_text("No conversation agents included")
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
    home, system_server, browser, tmp_path, monkeypatch,
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
    from tinyassets.api import command_center_updates

    def unavailable_history(**_kwargs):
        raise ValueError("synthetic provenance storage unavailable")

    # Installation succeeds even if its separate history registration fails.
    monkeypatch.setattr(command_center_updates, "record_install", unavailable_history)
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
        expect(page.locator("#ui-install-receipt")).to_contain_text("update history is unavailable")
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


@pytest.mark.real_browser
def test_public_instruction_template_copies_private_agent_and_opens_its_chat_without_model(
    home, system_server, browser,
):
    import sqlite3

    from playwright.sync_api import expect

    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tests.test_command_center_agent_templates import _agent
    from tests.test_command_center_packages import _answer, _ask, _publish_action
    from tinyassets.custom_agents import get_binding, list_bindings
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.storage import db_path

    _seed_own(home)
    definition, source_binding = _agent(home, config={"provider_policy_id": "publisher-private"})
    _unselected_definition, unselected = _agent(home, name="Unselected agent")
    _existing_definition, existing = _agent(home, BOB, BOB_UNIVERSE, "Bob's existing agent")
    source_ui = {**UI, "agent_refs": {"scout": source_binding["agent_binding_id"]},
                 "markup": '<h1>Agent village</h1><button id="scout">Talk to Scout</button>'
                           '<p id="opened"></p>',
                 "script": """(async()=>{
                   const identity=await tinyassets.whoami();
                   document.getElementById('scout').onclick=async()=>{
                     const opened=await tinyassets.openChat(identity.agent_refs.scout);
                     document.getElementById('opened').textContent=opened.agent_id;
                   };
                 })();"""}
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                expected_revision=row["revision"], changes={"ui_library": [source_ui]})
    action = _publish_action()
    del action["package"]
    action["agent_templates"] = {"village-scout": source_binding["agent_binding_id"]}
    asked = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in asked, asked
    published = _answer(OWNER, UNIVERSE, asked["request_id"])
    assert published.get("published"), published
    source = get_definition(home, published["agent_definition_id"])
    assert source["components"]["ui"]["agent_refs"] == {"scout": "village-scout"}
    assert (source["components"]["village-scout"]["agent_definition_id"]
            == definition["agent_definition_id"])
    assert source_binding["agent_binding_id"] not in json.dumps(source)
    assert unselected["agent_binding_id"] not in json.dumps(source)
    publisher_bindings = list_bindings(home, universe_id=UNIVERSE, limit=None)
    recipient_bindings = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    publisher_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    with sqlite3.connect(db_path(home)) as db:
        db.execute("DELETE FROM provider_assignments WHERE universe_id = ?", (BOB_UNIVERSE,))
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
    origin, calls, failures = system_server
    provider = _CountingProvider()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        with _real_providers(codex=provider):
            _enter(page, origin)
            page.evaluate("setChatCloudMode('bubble')")
            page.get_by_role("button", name="Browse / Switch", exact=True).click()
            expect(page.frame_locator("#ui-preview-frame").get_by_role(
                "heading", name="Agent village")).to_be_visible()
            assert not any(op == "try_package" for op, _ in calls)
            assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == recipient_bindings
            page.get_by_role("button", name="Copy into my command center", exact=True).click()
            tab = page.locator("#rail-items .rtab").filter(has_text="GTM Village")
            expect(tab).to_contain_text("Chat agents, as new private bindings")
            expect(tab).to_contain_text("Scout")
            expect(tab).to_contain_text(
                "No model assignments, access grants, conversations or private settings")
            assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == recipient_bindings
            tab.get_by_role("button", name="Accept", exact=True).click()
            expect(page.get_by_role(
                "button", name="Open copied screen", exact=True)).to_be_visible()
            receipt = next(result for op, result in calls
                           if op == "answer_request" and result.get("installed"))
            assert set(receipt["agents"]) == {"village-scout"}
            target = receipt["agents"]["village-scout"]
            assert target != source_binding["agent_binding_id"]
            binding = get_binding(home, universe_id=BOB_UNIVERSE, binding_id=target)
            assert binding["created_by"] == BOB and binding["status"] == "configured"
            assert binding["configuration"] == {"schema_version": 1, "name": "Scout"}
            assert (len(list_bindings(home, universe_id=BOB_UNIVERSE, limit=None))
                    == len(recipient_bindings) + 1)
            copied_ui = get_app_ui(
                home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"][-1]
            assert copied_ui["agent_refs"] == {"scout": target}
            page.get_by_role("button", name="Open copied screen", exact=True).click()
            frame = page.frame_locator("#ui-frame")
            expect(frame.get_by_role("heading", name="Agent village")).to_be_visible()
            frame.get_by_role("button", name="Talk to Scout", exact=True).click()
            expect(frame.locator("#opened")).to_have_text(target)
            expect(page.locator("#chat-cloud")).to_have_attribute("data-agent", target)
            expect(page.locator("#composer-input")).to_have_attribute("aria-label", "Message Scout")
            assert page.evaluate("addressedAgentId()") == target
            assert any(op == "status:" + target for op, _ in calls)
            assert not any(op == "status:" + source_binding["agent_binding_id"] for op, _ in calls)
            assert page.evaluate("window.acceptRelays") == []
            assert provider.calls == []
            assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
            assert list_bindings(home, universe_id=UNIVERSE, limit=None) == publisher_bindings
            assert get_binding(home, universe_id=BOB_UNIVERSE,
                               binding_id=existing["agent_binding_id"]) == existing
            assert get_definition(home, published["agent_definition_id"]) == source
            assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE) == publisher_ui
            assert not failures
    finally:
        page.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("initial_screen", ["my-own", "village"])
def test_manual_screen_replacement_needs_consent_retains_components_and_refuses_private_edits(
    home, system_server, browser, initial_screen,
):
    import sqlite3
    from copy import deepcopy

    from playwright.sync_api import expect

    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tests.test_command_center_agent_templates import _agent
    from tests.test_command_center_packages import _answer
    from tests.test_command_center_system_copy import _preview
    from tinyassets.custom_agents import list_bindings, publish_definition
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.storage import db_path

    _seed_own(home)
    _agent(home, BOB, BOB_UNIVERSE, "Bob's existing agent")
    source_id = _legacy(home)
    source = get_definition(home, source_id)
    with sqlite3.connect(db_path(home)) as db:
        db.execute("DELETE FROM provider_assignments WHERE universe_id = ?", (BOB_UNIVERSE,))
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
    provider = _CountingProvider()
    with _real_providers(codex=provider):
        ask = _preview(source_id)
        installed = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert installed.get("installed"), installed
    assert not provider.calls
    from tinyassets.command_center_update_registry import connect

    with connect(home) as conn:
        conn.execute("DELETE FROM command_center_adoptions WHERE owner_id=? AND universe_id=?",
                     (BOB, BOB_UNIVERSE))
        conn.commit()
    # Exercise both an unrelated selection and an already active old screen.
    row = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                expected_revision=row["revision"], changes={"ui_selection": {
                    "version": 1, "state": "active", "ui_id": initial_screen}})
    components = deepcopy(source["components"])
    ui = next(c for c in components.values() if c["kind"] == "tinyassets.app-ui.v1")
    ui["markup"] = '<h1>Village replacement</h1><p id="updated-boot"></p>'
    ui["script"] = "document.getElementById('updated-boot').textContent='New screen code ran';"
    proposed = publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Village replacement", "description": "A new layout",
        "tags": ["tinyassets.system.v1"], "components": components})
    target = proposed["agent_definition_id"]
    source_proposed = get_definition(home, target)
    publisher_ui = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    branches = _bobs_branches(home)
    bindings = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    autos = AutomationStore(home).list(universe_id=BOB_UNIVERSE)
    assert autos and all(row.desired_state == STATE_PAUSED for row in autos)
    before = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        with _real_providers(codex=provider):
            _enter(page, origin)
            _open_switcher(page)
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            panel = page.locator("#ui-shared-updates")
            expect(panel).to_contain_text(
                "Automatic presentation updates are off unless you explicitly opt in")
            expect(panel).to_contain_text(source_id)
            expect(panel).to_contain_text("Earlier copy:")
            with connect(home) as conn:
                count = conn.execute("SELECT count(*) FROM command_center_adoptions").fetchone()[0]
                assert count == 0
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
            page.get_by_role("button", name="Verify earlier copy", exact=True).click()
            expect(page.get_by_role(
                "button", name="Verify earlier copy", exact=True)).to_have_count(0)
            with connect(home) as conn:
                count = conn.execute("SELECT count(*) FROM command_center_adoptions").fetchone()[0]
                assert count == 1
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
            review = page.get_by_role(
                "button", name="Review screen replacement: Village replacement", exact=True)
            review.click()
            confirmation = page.locator("#ui-update-confirmation")
            expect(confirmation).to_contain_text("Replace screen only")
            expect(confirmation).to_contain_text(target)
            expect(confirmation).to_contain_text("paused/running state stay unchanged")
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
            assert not any(op == "answer_center_update" for op, _ in calls)
            confirmation.get_by_role("button", name="Keep current", exact=True).click()
            expect(panel).to_contain_text("Nothing was replaced")
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
            review.click()
            confirmation.get_by_role("button", name="Replace screen", exact=True).click()
            expect(page.locator("#ui-status")).to_contain_text("Screen saved")
            after = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
            assert after["ui_selection"] == before["ui_selection"]
            assert after["ui_library"][0] == before["ui_library"][0]
            updated = next(b for b in after["ui_library"] if b["ui_id"] == "village")
            assert updated["markup"] == ui["markup"]
            assert page.evaluate("AppUI.active.ui_id") == initial_screen
            expect(page.frame_locator("#ui-frame").locator("#updated-boot")).to_have_count(0)
            assert page.evaluate("AppUI.active.script") != ui["script"]
            assert page.evaluate("window.acceptRelays") == [] and not provider.calls
            assert _bobs_branches(home) == branches
            assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == bindings
            assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == autos
            if initial_screen == "village":
                # An unrelated library edit and ordinary same-session refresh
                # must not accidentally boot the deferred new screen either.
                unrelated = deepcopy(after["ui_library"])
                unrelated[0]["style"] += "\n/* Bob edited his other screen */"
                save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                            expected_revision=after["revision"], changes={"ui_library": unrelated})
                page.evaluate("async()=>{await AppUI.load();}")
                expect(page.frame_locator("#ui-frame").locator("#updated-boot")).to_have_count(0)
                assert page.evaluate("AppUI.active.script") != ui["script"]
                assert not provider.calls and page.evaluate("window.acceptRelays") == []
                expect(page.get_by_role("button", name="Use Village", exact=True)).to_be_enabled()
                # The explicit Open click, not approval, boots the saved script.
                page.get_by_role("button", name="Open updated screen", exact=True).click()
                expect(page.frame_locator("#ui-frame").locator("#updated-boot")).to_have_text(
                    "New screen code ran")
                # choose() mounts immediately, then persists the selection. The
                # frame alone cannot prove the revision is ready to snapshot.
                expect(page.locator("#ui-status")).to_have_text(
                    f"Now using {updated['name']}.")
                after = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
                assert after["ui_selection"] == before["ui_selection"]
            page.locator("#btn-ui-close").click()
            page.evaluate("sessionStorage.clear()")
            _enter(page, origin)
            assert page.evaluate("AppUI.active.ui_id") == initial_screen
            _open_switcher(page)
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            expect(panel).to_contain_text("Screen source: " + target)
            expect(panel).to_contain_text("Retained component source: " + source_id)
            # A second explicit selection is still fenced against an owner edit
            # made AFTER preview, so the visible confirmation cannot overwrite it.
            original_name = source["name"]
            page.get_by_role("button", name="Review screen replacement: " + original_name,
                             exact=True).click()
            expect(confirmation).to_be_visible()
            edited = deepcopy(after["ui_library"])
            next(b for b in edited if b["ui_id"] == "village")["markup"] = "My private layout"
            save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                        expected_revision=after["revision"], changes={"ui_library": edited})
            private = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
            confirmation.get_by_role("button", name="Replace screen", exact=True).click()
            expect(panel).to_contain_text("edited or deleted")
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == private
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            expect(panel).to_contain_text("Private edits detected")
            expect(page.get_by_role("button", name="Review screen replacement: " + original_name,
                                    exact=True)).to_be_disabled()
            assert get_definition(home, source_id) == source
            assert get_definition(home, target) == source_proposed
            assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE) == publisher_ui
            assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == autos
            assert _bobs_branches(home) == branches
            assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == bindings
            assert not provider.calls and page.evaluate("window.acceptRelays") == []
            assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
            assert not failures
    finally:
        page.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("transition", ["selection", "account"])
def test_manual_update_read_drops_old_selection_or_account_response(
    home, system_server, browser, transition,
):
    from playwright.sync_api import expect

    from tests.test_command_center_packages import _answer
    from tests.test_command_center_system_copy import _preview

    _seed_own(home)
    source_id = _legacy(home)
    asked = _preview(source_id)
    assert _answer(BOB, BOB_UNIVERSE, asked["request_id"]).get("installed")
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _enter(page, origin)
        _open_switcher(page)
        # Delay delivery AFTER the authenticated request reached the real store.
        # This changes timing only; no adoption or consent response is invented.
        page.evaluate("""()=>{
          const read=Owner.read.bind(Owner);
          document.documentElement.dataset.updateReadReached="false";
          document.documentElement.dataset.updateReadReleased="false";
          Owner.read=async(args)=>{
            const doc=await read(args);
            if(args.target==='command_center_updates'){
              document.documentElement.dataset.updateReadReached="true";
              await new Promise(resolve=>{window.releaseUpdateRead=resolve;});
              document.documentElement.dataset.updateReadReleased="true";
            }
            return doc;
          };
        }""")
        page.get_by_role("button", name="Manage shared copies", exact=True).click()
        expect(page.locator("html")).to_have_attribute("data-update-read-reached", "true")
        assert any(op == "read:command_center_updates" and result["adoptions"]
                   for op, result in calls)
        if transition == "selection":
            page.get_by_role("button", name="Use Bob's own", exact=True).click()
            expect(page.locator("#ui-status")).to_contain_text("Now using Bob's own")
        else:
            page.evaluate("""()=>{
              AppUI.reset();AppUI.enabled=true;
              AppUI.home='different-home';AppUI.principal='different-owner';AppUI.paint();
            }""")
        page.evaluate("()=>{window.releaseUpdateRead();}")
        expect(page.locator("html")).to_have_attribute("data-update-read-released", "true")
        expect(page.locator("#ui-shared-updates")).to_have_count(0)
        expect(page.locator("#ui-update-confirmation")).to_have_count(0)
        assert not any(op in {"preview_center_update", "answer_center_update"}
                       for op, _ in calls)
        assert page.evaluate("window.acceptRelays") == []
        assert not failures
    finally:
        page.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("window", [
    "commit_reply", "closed_dialog", "readback", "lost_reply", "account_reset",
])
def test_pending_manual_update_cannot_boot_saved_script_during_refresh(
    home, system_server, browser, monkeypatch, window,
):
    from copy import deepcopy

    from playwright.sync_api import expect

    from tests.test_command_center_packages import _answer
    from tests.test_command_center_system_copy import _preview
    from tinyassets.api import command_center_update_surface
    from tinyassets.custom_agents import publish_definition

    source = _legacy(home)
    ask = _preview(source)
    assert _answer(BOB, BOB_UNIVERSE, ask["request_id"]).get("installed")
    row = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                expected_revision=row["revision"], changes={"ui_selection": {
                    "version": 1, "state": "active", "ui_id": "village"}})
    components = deepcopy(get_definition(home, source)["components"])
    ui = next(c for c in components.values() if c["kind"] == "tinyassets.app-ui.v1")
    ui["markup"] = '<h1>Replacement code</h1><p id="race-boot"></p>'
    ui["script"] = "document.getElementById('race-boot').textContent='New code ran';"
    publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Deferred replacement", "description": "Same dependencies",
        "tags": ["tinyassets.system.v1"], "components": components})
    committed, release = threading.Event(), threading.Event()
    real_write = command_center_update_surface.write_update

    def delayed_write(**kwargs):
        result = real_write(**kwargs)
        if kwargs["operation"] == "answer_center_update" and result.get("applied"):
            committed.set()
            if window != "readback":
                assert release.wait(15), "browser did not release committed response"
        return result

    monkeypatch.setattr(command_center_update_surface, "write_update", delayed_write)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _enter(page, origin)
        _open_switcher(page)
        page.get_by_role("button", name="Manage shared copies", exact=True).click()
        page.get_by_role("button", name="Review screen replacement: Deferred replacement",
                         exact=True).click()
        expect(page.locator("#ui-update-confirmation")).to_be_visible()
        page.evaluate("""mode=>{
          const call=MCP.callTool.bind(MCP);
          MCP.callTool=async(name,args)=>{
            const doc=await call(name,args);
            if(args.operation==='answer_center_update'){
              document.documentElement.dataset.acceptReply='received';
              if(mode==='lost_reply')throw Error('synthetic reply lost after actual commit');
            }
            return doc;
          };
          if(mode==='readback'){
            const fetchRow=AppUI.fetchRow.bind(AppUI);let first=true;
            AppUI.fetchRow=async()=>{
              const row=await fetchRow();
              if(first){first=false;
                document.documentElement.dataset.readback='waiting';
                await new Promise(resolve=>{window.releaseReadback=resolve;});
              }
              return row;
            };
          }
        }""", window)
        page.get_by_role("button", name="Replace screen", exact=True).click()
        assert committed.wait(5), "actual update handler did not commit"
        if window == "readback":
            expect(page.locator("html")).to_have_attribute("data-readback", "waiting")
        if window == "closed_dialog":
            page.locator("#btn-ui-close").click()
        if window == "account_reset":
            page.evaluate("""()=>{
              AppUI.reset();AppUI.enabled=true;AppUI.home='new-home';
              AppUI.principal='new-owner';AppUI.paint();
            }""")
            release.set()
            expect(page.locator("html")).to_have_attribute("data-accept-reply", "received")
            assert page.evaluate("AppUI.deferredUpdates.size") == 0
            expect(page.locator("#ui-frame")).to_have_count(0)
            expect(page.locator("#ui-update-confirmation")).to_have_count(0)
            assert page.evaluate("window.acceptRelays") == []
            assert not failures
            return
        saved = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
        assert saved["ui_library"][0]["script"] == ui["script"]
        # A second real owner read sees the committed code while answer/readback
        # is suspended. It must not mount that code or run it as a side effect.
        page.evaluate("async()=>{await AppUI.load();}")
        expect(page.frame_locator("#ui-frame").locator("#race-boot")).to_have_count(0)
        assert page.evaluate("AppUI.active.script") != ui["script"]
        if window == "readback":
            page.evaluate("()=>{window.releaseReadback();}")
        else:
            release.set()
        expect(page.locator("html")).to_have_attribute("data-accept-reply", "received")
        page.evaluate("async()=>{await AppUI.load();}")
        expect(page.frame_locator("#ui-frame").locator("#race-boot")).to_have_count(0)
        if window == "closed_dialog":
            _open_switcher(page)
        page.get_by_role("button", name="Use Village", exact=True).click()
        if window == "lost_reply":
            expect(page.locator("#ui-status")).to_contain_text("outcome is not confirmed")
            expect(page.frame_locator("#ui-frame").locator("#race-boot")).to_have_count(0)
            # Reload is a new explicit user action and clears session-only holds.
            page.evaluate("sessionStorage.clear()")
            _enter(page, origin)
        expect(page.frame_locator("#ui-frame").locator("#race-boot")).to_have_text("New code ran")
        assert page.evaluate("window.acceptRelays") == []
        assert not failures
    finally:
        release.set()
        page.close()


def _release_copy(home):
    """Actual publisher approval and recipient installation establish the series."""
    from tests.test_command_center_packages import _answer, _ask
    from tests.test_command_center_release_surface import action
    from tests.test_command_center_system_copy import _preview

    asked = _ask(OWNER, UNIVERSE, action())
    first = _answer(OWNER, UNIVERSE, asked["request_id"])
    assert first.get("published") and first.get("release"), first
    copied = _preview(first["agent_definition_id"])
    installed = _answer(BOB, BOB_UNIVERSE, copied["request_id"])
    assert installed.get("installed") and installed.get("adoption"), installed
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    ui = {**row["ui_library"][0], "style": "body { color: #123456; }"}
    save_app_ui(
        home,
        owner_user_id=OWNER,
        universe_id=UNIVERSE,
        expected_revision=row["revision"],
        changes={"ui_library": [ui]},
    )
    asked = _ask(
        OWNER,
        UNIVERSE,
        {
            **action(
                series_id=first["release"]["series_id"],
                parent_release_id=first["release"]["release_id"],
                summary="A calmer village palette",
            ),
            "name": "Village palette release",
        },
    )
    second = _answer(OWNER, UNIVERSE, asked["request_id"])
    assert second.get("published") and second.get("release"), second
    return first, second, installed["adoption"]


@pytest.mark.real_browser
def test_release_history_and_policy_consent_are_exact_owner_choices_without_inference(
    home,
    system_server,
    browser,
):
    import sqlite3

    from playwright.sync_api import expect

    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.api.command_center_update_surface import write_update
    from tinyassets.command_center_update_policy import inspect_policy
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.storage import db_path

    with sqlite3.connect(db_path(home)) as db:
        db.execute("DELETE FROM provider_assignments WHERE universe_id=?", (BOB_UNIVERSE,))
    provider = _CountingProvider()
    with _real_providers(codex=provider):
        first, second, adoption = _release_copy(home)
    assert load_provider_assignment(home, universe_id=BOB_UNIVERSE) is None
    before = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        with _real_providers(codex=provider):
            _enter(page, origin)
            _open_switcher(page)
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            panel = page.locator("#ui-shared-updates")
            history = page.get_by_role("region", name="Published version history", exact=True)
            expect(history).to_contain_text("Version 1 · Installed screen source")
            expect(history).to_contain_text(first["release"]["release_id"])
            expect(history).to_contain_text("Version 2")
            expect(history).to_contain_text("A calmer village palette")
            expect(panel).to_contain_text("Automatic presentation update preference: Off")
            assert not any(op.startswith("preview_center") for op, _ in calls)
            page.get_by_role("button", name="Review version 2", exact=True).click()
            confirmation = page.locator("#ui-update-confirmation")
            expect(confirmation).to_contain_text(second["agent_definition_id"])
            confirmation.get_by_role("button", name="Keep current", exact=True).click()
            page.get_by_role(
                "button", name="Review automatic presentation updates", exact=True
            ).click()
            consent = page.locator("#ui-policy-confirmation")
            expect(consent).to_contain_text(first["agent_definition_id"])
            expect(consent).to_contain_text("name/style")
            expect(consent).to_contain_text("conflicts require a decision")
            with _as(BOB):
                assert not inspect_policy(
                    universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"]
                )["enabled"]
            preview = next(
                result for op, result in reversed(calls) if op == "preview_center_policy"
            )
            with _as(OWNER):
                denied = write_update(
                    universe_id=BOB_UNIVERSE,
                    operation="answer_center_policy",
                    payload={
                        "request_id": preview["request_id"],
                        "plan_digest": preview["plan_digest"],
                        "decision": "accepted",
                    },
                )
            assert denied.get("error"), denied
            consent.get_by_role("button", name="Keep current preference", exact=True).click()
            expect(panel).to_contain_text("Kept your current update preference")
            with _as(BOB):
                assert not inspect_policy(
                    universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"]
                )["enabled"]
            page.get_by_role(
                "button", name="Review automatic presentation updates", exact=True
            ).click()
            consent.get_by_role("button", name="Allow presentation updates", exact=True).click()
            expect(panel).to_contain_text("Automatic presentation update preference: On")
            with _as(BOB):
                policy = inspect_policy(
                    universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"]
                )
            assert (
                policy["enabled"]
                and policy["installed_release_id"] == first["release"]["release_id"]
            )
            page.get_by_role(
                "button", name="Review turning automatic updates off", exact=True
            ).click()
            expect(consent).to_contain_text("Turn automatic presentation updates off")
            consent.get_by_role("button", name="Turn automatic updates off", exact=True).click()
            expect(panel).to_contain_text("Automatic presentation update preference: Off")
            with _as(BOB):
                assert not inspect_policy(
                    universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"]
                )["enabled"]
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
            assert not provider.calls and page.evaluate("window.acceptRelays") == []
            assert not failures
    finally:
        page.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("operation", ["preview_center_policy", "answer_center_policy"])
@pytest.mark.parametrize("transition", ["selection", "account"])
def test_policy_reply_is_dropped_after_selection_or_account_change(
    home,
    system_server,
    browser,
    operation,
    transition,
):
    from playwright.sync_api import expect

    from tinyassets.command_center_update_policy import inspect_policy

    _seed_own(home)
    _first, _second, adoption = _release_copy(home)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _enter(page, origin)
        _open_switcher(page)
        page.get_by_role("button", name="Manage shared copies", exact=True).click()
        if operation == "answer_center_policy":
            page.get_by_role(
                "button", name="Review automatic presentation updates", exact=True
            ).click()
            expect(page.locator("#ui-policy-confirmation")).to_be_visible()
        page.evaluate(
            """operation=>{
          const call=MCP.callTool.bind(MCP);
          document.documentElement.dataset.policyReached='false';
          document.documentElement.dataset.policyReleased='false';
          MCP.callTool=async(name,args)=>{
            const doc=await call(name,args);
            if(args.operation===operation){
              document.documentElement.dataset.policyReached='true';
              await new Promise(resolve=>{window.releasePolicyReply=resolve;});
              document.documentElement.dataset.policyReleased='true';
            }
            return doc;
          };
        }""",
            operation,
        )
        page.get_by_role(
            "button",
            name=(
                "Allow presentation updates"
                if operation == "answer_center_policy"
                else "Review automatic presentation updates"
            ),
            exact=True,
        ).click()
        expect(page.locator("html")).to_have_attribute("data-policy-reached", "true")
        if transition == "selection":
            page.get_by_role("button", name="Use Bob's own", exact=True).click()
            expect(page.locator("#ui-status")).to_contain_text("Now using Bob's own")
        else:
            page.evaluate("""()=>{
              AppUI.reset();AppUI.enabled=true;AppUI.home='other-home';
              AppUI.principal='other-owner';AppUI.paint();
            }""")
        page.evaluate("()=>window.releasePolicyReply()")
        expect(page.locator("html")).to_have_attribute("data-policy-released", "true")
        expect(page.locator("#ui-policy-confirmation")).to_have_count(0)
        expect(page.locator("#ui-shared-updates")).to_have_count(0)
        with _as(BOB):
            policy = inspect_policy(universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"])
        assert policy["enabled"] == (operation == "answer_center_policy")
        assert sum(op == "answer_center_policy" for op, _ in calls) == (
            operation == "answer_center_policy"
        )
        assert page.evaluate("window.acceptRelays") == [] and not failures
    finally:
        page.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("blocked", ["withdrawn_source", "private_edit"])
def test_release_policy_controls_refuse_unavailable_source_or_recipient_edits(
    home,
    system_server,
    browser,
    blocked,
):
    from playwright.sync_api import expect

    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    _release_copy(home)
    if blocked == "withdrawn_source":
        branch = get_branch_definition(home, branch_def_id=SCOUT)
        branch["visibility"] = "private"
        save_branch_definition(home, branch_def=branch)
    else:
        row = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
        edited = [{**entry, "name": "My private design name"} for entry in row["ui_library"]]
        save_app_ui(
            home,
            owner_user_id=BOB,
            universe_id=BOB_UNIVERSE,
            expected_revision=row["revision"],
            changes={"ui_library": edited},
        )
    before = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _enter(page, origin)
        _open_switcher(page)
        page.get_by_role("button", name="Manage shared copies", exact=True).click()
        history = page.get_by_role("region", name="Published version history", exact=True)
        expect(history).to_contain_text("A calmer village palette")
        expect(history.get_by_role("button", name="Review version 2", exact=True)).to_be_disabled()
        enable = history.get_by_role(
            "button", name="Review automatic presentation updates", exact=True
        )
        if blocked == "withdrawn_source":
            expect(history).to_contain_text(
                "Unavailable: this published source cannot currently be adopted"
            )
            expect(enable).to_have_count(0)
        else:
            expect(page.locator("#ui-shared-updates")).to_contain_text("Private edits detected")
            expect(enable).to_be_disabled()
        assert not any(
            op in {"preview_center_update", "preview_center_policy", "answer_center_policy"}
            for op, _ in calls
        )
        assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == before
        assert page.evaluate("window.acceptRelays") == [] and not failures
    finally:
        page.close()


@pytest.mark.real_browser
def test_opted_in_service_update_survives_browser_reload_without_inference(
    home, system_server, browser,
):
    from playwright.sync_api import expect

    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets import command_center_update_maintenance as maintenance
    from tinyassets.command_center_update_policy import inspect_policy
    from tinyassets.custom_agents import _agent_connect
    from tinyassets.universe_owner import record_creation

    provider = _CountingProvider()
    with _real_providers(codex=provider):
        _first, second, adoption = _release_copy(home)
    with _agent_connect(home) as conn:
        record_creation(conn, universe_id=BOB_UNIVERSE, owner_id=BOB)
    before = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    origin, calls, failures = system_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        with _real_providers(codex=provider):
            _enter(page, origin)
            _open_switcher(page)
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            panel = page.locator("#ui-shared-updates")
            expect(panel).to_contain_text("waiting for the service maintenance worker")
            page.get_by_role(
                "button", name="Review automatic presentation updates", exact=True,
            ).click()
            page.locator("#ui-policy-confirmation").get_by_role(
                "button", name="Allow presentation updates", exact=True,
            ).click()
            expect(panel).to_contain_text("Automatic presentation update preference: On")
            assert maintenance.tick(home)["state"] == "ready"
            after = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
            assert after["revision"] == before["revision"] + 1
            assert after["ui_library"][0]["style"] == "body { color: #123456; }"
            assert after["ui_library"][0]["script"] == before["ui_library"][0]["script"]
            page.evaluate("sessionStorage.clear()")
            _enter(page, origin)
            _open_switcher(page)
            page.get_by_role("button", name="Manage shared copies", exact=True).click()
            expect(panel).to_contain_text("An eligible presentation update was saved")
            expect(panel).to_contain_text(second["release"]["release_id"])
            expect(panel).to_contain_text("Automatic presentation update preference: On")
            with _as(BOB):
                policy = inspect_policy(
                    universe_id=BOB_UNIVERSE, adoption_id=adoption["adoption_id"],
                )
            assert policy["installed_release_id"] == second["release"]["release_id"]
            assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == after
            assert not provider.calls and page.evaluate("window.acceptRelays") == []
            assert sum(op == "answer_center_policy" for op, _ in calls) == 1
            assert not failures
    finally:
        page.close()
