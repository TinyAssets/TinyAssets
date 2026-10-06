"""Deploy failures through the rendered shell, CSP and sandboxed frame in Chromium."""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

pytestmark = pytest.mark.real_browser


@pytest.fixture
def server():
    state = {"build": "before", "frames": 0, "pages": 0, "fault": "", "me_fail": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("X-TinyAssets-Build", state["build"])
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0]
            headers = {}
            status = 200
            if path == "/app":
                state["pages"] += 1
                body, csp = render_app_html(build=state["build"])
                if state.pop("parse_once", False):
                    body = body.replace('const CFG =', 'const = broken; const CFG =', 1)
                headers["Content-Security-Policy"] = csp
            elif path == "/app/ui-frame":
                state["frames"] += 1
                headers.update(FRAME_HEADERS)
                body = BOOTSTRAP_HTML
                fault = state["fault"]
                state["fault"] = "" if fault != "permanent" else fault
                if fault in ("503", "permanent"):
                    status, body = 503, "Temporarily unavailable"
                elif fault == "exception":
                    body = body.replace('var started = false;',
                                        'var started = false; throw Error("deploy boot");')
            else:
                status, body = 404, "Not found"
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/app", state
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def page(server):
    from playwright.sync_api import sync_playwright

    url, state = server
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM"))
        context = browser.new_context(viewport={"width": 1280, "height": 850})
        # Scripted HTTP responses, not replacements for the app's recovery/boot.
        context.add_init_script("""sessionStorage.setItem('ta_access_token','test');
            sessionStorage.setItem('ta_access_exp',String(Date.now()+3600000));""")

        def api(route):
            path = route.request.url.split("?", 1)[0]
            if path.endswith("/app/me"):
                route.fulfill(status=503 if state["me_fail"] else 200, json={
                    "principal_id": "owner-1",
                    "universe_id": "" if state.get("degraded") else "home-1",
                    "setup": "unavailable" if state.get("degraded") else "connected"})
            elif path.endswith("/app/api/read"):
                route.fulfill(json={"app_ui": {"universe_id": "home-1", "revision": 1,
                    "ui_library": [], "ui_selection": None, "platform_default": {
                        "kind": "tinyassets.app-ui.v1", "version": 1,
                        "ui_id": "platform:blank", "name": "Test center",
                        "markup": "<p id='working'>Working command center</p>",
                        "style": "", "script": ""}}, "bindings": []})
            elif path.endswith("/app") or path.endswith("/app/ui-frame") or "/app/m/" in path:
                route.continue_()
            else:
                route.fulfill(json={"pending": [], "requests": [], "turns": []})

        context.route("**/*", api)
        page = context.new_page()
        page.goto(url)
        yield page
        context.close()
        browser.close()


def healthy(page):
    page.frame_locator("#ui-frame").locator("#working").wait_for(timeout=40000)


@pytest.mark.parametrize("width,height", [(1280, 850), (390, 844)])
def test_healthy_recovery_controls_do_not_cover_content(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    healthy(page)
    page.locator('[data-app-recovery="chat"]').click()
    controls = page.locator("#app-recovery-controls").bounding_box()
    stage = page.locator(".chat-stage").bounding_box()
    assert controls["y"] >= stage["y"] + stage["height"]
    assert controls["y"] + controls["height"] <= height
    page.locator("#composer-input").click()
    assert page.locator("#composer-input").evaluate("el => el === document.activeElement")


def test_missing_frame_recovers_and_controls_are_clickable(page, server):
    from playwright.sync_api import expect

    healthy(page)
    page.evaluate("document.getElementById('ui-frame').remove()")
    page.locator('[data-app-recovery="chat"]').click()
    expect(page.locator("#composer-input")).to_be_visible()
    page.locator("#composer-input").fill("keep this draft")
    page.locator('#app-recovery-controls [data-app-recovery="browse"]').click()
    expect(page.locator("#ui-dialog")).to_be_visible()
    page.locator("#btn-ui-close").click()
    healthy(page)  # watchdog notices the detached frame and remounts it
    assert server[1]["frames"] >= 2
    assert page.evaluate("AppUI.isPlatformDefault()") is True
    assert page.input_value("#composer-input") == "keep this draft"
    page.locator('[data-app-recovery="refresh"]').click()
    healthy(page)
    assert page.evaluate("AppUI.isPlatformDefault()") is True
    assert server[1]["pages"] == 1


@pytest.mark.parametrize("failure", ["exception", "503"])
def test_frame_boot_failure_reloads_frame_first(page, server, failure):
    healthy(page)
    server[1]["fault"] = failure
    page.evaluate("AppUI.mount(AppUI.active)")
    page.wait_for_function("!AppUI.ready")
    healthy(page)
    assert server[1]["frames"] >= 3
    assert server[1]["pages"] == 1


def test_deploy_mismatch_ignores_live_turn_and_preserves_draft(page, server):
    healthy(page)
    page.locator('[data-app-recovery="chat"]').click()
    page.locator("#composer-input").fill("unsent during deploy")
    page.evaluate("document.getElementById('btn-send').disabled=true;turnStartedAt=Date.now()")
    page.evaluate("document.getElementById('ui-frame').remove()")
    server[1]["build"] = "after"
    page.evaluate("AppRecovery.checkBuild()")
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert page.input_value("#composer-input") == "unsent during deploy"
    assert server[1]["pages"] == 2


def test_session_503_does_not_remove_existing_frame(page, server):
    healthy(page)
    server[1]["me_fail"] = True
    page.evaluate("enterSignedIn()")
    assert page.locator("#ui-frame").count() == 1
    assert page.evaluate("AppUI.enabled") is True
    server[1]["me_fail"] = False
    healthy(page)


def test_old_module_404_recovers_when_main_script_never_starts(page, server):
    healthy(page)
    # Reproduce the old-deployment response during a module hash transition.
    # The module's error event must reach the separate recovery script.
    page.evaluate("""() => {
        const script=document.createElement('script');script.type='module';
        script.nonce=document.querySelector('script[nonce]').nonce;
        script.src='/app/m/0123456789abcdef/main.js';document.body.append(script);
        AppUI.reset();
    }""")
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert server[1]["pages"] == 2


def test_retry_exhaustion_has_visible_reload_and_no_loop(page, server):
    from playwright.sync_api import expect

    healthy(page)
    server[1]["fault"] = "permanent"
    page.evaluate("AppUI.mount(AppUI.active)")
    expect(page.locator("#app-recovery-failed")).to_be_visible(timeout=25000)
    page.wait_for_function(
        "() => JSON.parse(sessionStorage.getItem('ta_app_recovery')||'{}').attempts===2",
        timeout=90000)
    page.wait_for_function("() => location.search.includes('_ta_recover')")
    # Advance timers instead of sleeping through the remaining retries.
    page.clock.install()
    page.clock.run_for(60000)
    assert server[1]["pages"] <= 3
    expect(page.locator('[data-app-recovery="reload"]')).to_be_visible()
    server[1]["fault"] = ""
    page.locator('[data-app-recovery="reload"]').click()
    healthy(page)


def test_recovery_draft_is_not_restored_to_another_owner(page):
    healthy(page)
    page.evaluate("""() => {
        sessionStorage.setItem('ta_recovery_draft',JSON.stringify({owner:'someone-else',
            home:'home-1',agent:'main',text:'private draft'}));AppRecovery.restoreDraft();
    }""")
    assert page.input_value("#composer-input") == ""
    assert "private draft" not in page.locator("body").inner_text()


def test_main_script_parse_failure_recovers_independently(page, server):
    healthy(page)
    url, state = server
    original_pages = state["pages"]

    state["parse_once"] = True
    page.goto(url + "?parse-failure=1")
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert state["pages"] == original_pages + 2


def test_first_boot_503_recovers_without_click(page, server):
    healthy(page)
    server[1]["me_fail"] = True
    page.reload()
    page.locator("#app-recovery-failed").wait_for()
    server[1]["me_fail"] = False
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert server[1]["pages"] == 3


def test_bubble_browse_reloads_when_ui_is_disabled(page):
    healthy(page)
    page.evaluate("AppUI.reset();cloudState.mode='bubble';cloudState.userSet=true;applyChatCloud()")
    page.locator("#btn-bubble-browse").click()
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)


def test_healthy_turn_holds_version_update(page, server):
    healthy(page)
    page.evaluate("document.getElementById('btn-send').disabled=true;turnStartedAt=Date.now()")
    server[1]["build"] = "after"
    page.evaluate("AppRecovery.checkBuild()")
    assert page.evaluate("sessionStorage.getItem('ta_app_recovery')") is None
    assert server[1]["pages"] == 1
    assert server[1]["frames"] == 1


def test_runtime_error_does_not_remount_working_bundle(page, server):
    healthy(page)
    page.frame_locator("#ui-frame").locator("#working").evaluate(
        "() => window.dispatchEvent(new ErrorEvent('error',{message:'runtime action refused'}))")
    assert page.evaluate("AppUI.bootFault") is False
    assert server[1]["frames"] == 1
    assert page.locator("#app-recovery-failed").is_hidden()


def test_disconnected_bubble_is_not_a_boot_failure(page, server):
    healthy(page)
    page.evaluate("""() => {
        AppUI.reset();engineConnected=false;
        cloudState.mode='bubble';cloudState.userSet=true;applyChatCloud();
    }""")
    page.clock.install()
    page.clock.run_for(40000)
    assert page.locator("#app-recovery-failed").is_hidden()
    assert server[1]["pages"] == 1
    page.locator('[data-app-recovery="chat"]').click()
    assert page.locator("#composer-input").is_visible()


def test_degraded_session_reply_does_not_disable_ui(page, server):
    healthy(page)
    server[1]["degraded"] = True
    page.evaluate("enterSignedIn()")
    assert page.evaluate("AppUI.enabled") is True
    assert page.evaluate("AppUI.home") == "home-1"
    healthy(page)


def test_draft_waits_for_its_addressed_agent(page):
    from playwright.sync_api import expect

    healthy(page)
    page.evaluate("""() => {
        sessionStorage.setItem('ta_recovery_draft',JSON.stringify({owner:'owner-1',
            home:'home-1',agent:'helper',text:'helper draft'}));AppRecovery.restoreDraft();
    }""")
    assert page.input_value("#composer-input") == ""
    page.evaluate("addressAgent({agent_id:'helper',name:'Helper'})")
    expect(page.locator("#composer-input")).to_have_value("helper draft")
    assert page.evaluate("sessionStorage.getItem('ta_recovery_draft')") is None


def test_failed_asset_boot_retries_then_recovers_page(page, server):
    healthy(page)
    page.route("**/app/api/ui-asset", lambda route: route.fulfill(status=503, body="deploy"))
    page.evaluate("AppUI.mount({...AppUI.active,libraries:['three']})")
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert server[1]["pages"] == 2
    assert page.evaluate("AppUI.bootComplete") is True


def test_bundle_exception_is_reported_before_boot_success(page, server):
    healthy(page)
    page.evaluate("AppUI.mount({...AppUI.active,script:'throw Error(\"boot broke\")'})")
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    assert server[1]["pages"] == 2
    assert page.evaluate("AppUI.bootComplete && !AppUI.bootFault") is True
