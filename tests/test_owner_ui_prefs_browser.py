"""In a real Chromium: a device with no local copy lays the chat cloud out from
the owner's record, and a placement made there is written back.

The shipped page is served with its own CSP; ``/app/ui-prefs`` answers like the
route does (``{"prefs": ...}`` for GET, ``{"saved": true}`` for POST).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tinyassets.onboarding import render_app_html

# Run, with a skip counted as a failure, by .github/workflows/real-browser-proof.yml.
pytestmark = pytest.mark.real_browser

RECORD = {"v": 1, "mode": "open", "open": {"x": 200, "y": 60, "w": 520, "h": 420},
          "bubble": {"x": 30, "y": 30}}


@pytest.fixture
def server():
    html, csp = render_app_html()
    posts: list[dict] = []
    delay = {"seconds": 0.0, "records": {}, "reads": [], "read_started": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, body: bytes, ctype: str, extra=None):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?")[0] == "/app":
                self._send(html.encode("utf-8"), "text/html; charset=utf-8",
                           {"Content-Security-Policy": csp})
            elif self.path.startswith("/app/ui-prefs?"):
                viewport = parse_qs(urlsplit(self.path).query)["viewport"][0]
                owner = self.headers.get("Authorization", "owner-1")
                delay["reads"].append((owner, viewport))
                record = delay["records"].get((owner, viewport), RECORD)
                delay["read_started"].set()
                time.sleep(delay["seconds"])
                self._send(json.dumps({"prefs": {"chat_cloud": record}}).encode(),
                           "application/json")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/app/ui-prefs":
                length = int(self.headers.get("content-length") or 0)
                doc = json.loads(self.rfile.read(length))
                owner = self.headers.get("Authorization", "owner-1")
                delay["records"][(owner, doc["viewport"])] = doc["value"]
                posts.append({**doc, "test_owner": owner})
                self._send(b'{"saved": true}', "application/json")
            else:
                self.send_response(404)
                self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever,
                     kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/app", posts, delay
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def page():
    sync_api = pytest.importorskip(
        "playwright.sync_api",
        reason="owner=owner-ui-prefs runs-in=real-browser-proof; Playwright required",
    )
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser binary on this host
            pytest.skip(
                "owner=owner-ui-prefs runs-in=real-browser-proof; "
                f"Chromium is not available here: {exc.__class__.__name__}"
            )
        try:
            yield browser.new_page(viewport={"width": 1280, "height": 800})
        finally:
            browser.close()


def _wait_for_placement(page, mode, x):
    # Locator assertions poll observable layout without evaluating a predicate
    # string in the app's CSP-protected execution context.
    from playwright.sync_api import expect

    selector = "#chat-cloud-bubble" if mode == "bubble" else "#chat-cloud"
    expect(page.locator(selector)).to_be_visible()
    expect(page.locator(selector)).to_have_css("left", f"{x}px")
    assert page.evaluate("mode => cloudState[mode].x", mode) == x


def test_a_new_device_takes_the_owners_record_and_writes_back_its_placement(server, page):
    url, posts, _delay = server
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    page.wait_for_load_state("networkidle")
    page.evaluate("() => { setQueueOwner('owner-1'); showView('chat'); refreshChatCloud(); }")

    _wait_for_placement(page, "open", 200)
    box = page.locator("#chat-cloud").bounding_box()
    stage = page.locator("#chat-stage").bounding_box()
    assert box["x"] - stage["x"] == pytest.approx(200, abs=2)
    assert box["width"] == pytest.approx(520, abs=2)
    cached = "JSON.parse(localStorage.getItem('app.chatCloud.v1:owner-1:main:wide')).open.x"
    assert page.evaluate(cached) == 200

    page.click("#btn-cloud-shrink")
    page.locator("#chat-cloud-bubble").wait_for(state="visible")
    page.wait_for_timeout(300)
    assert posts and posts[-1]["key"] == "chat_cloud" and posts[-1]["value"]["mode"] == "bubble"
    assert posts[-1]["agent"] == "main" and posts[-1]["viewport"] == "wide"


def test_a_record_arriving_mid_drag_does_not_move_the_cloud(server, page):
    url, posts, delay = server
    delay["seconds"] = 1.5
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    page.evaluate("() => { setQueueOwner('owner-1'); showView('chat'); refreshChatCloud(); }")
    page.locator("#chat-cloud").wait_for(state="visible")
    assert page.evaluate("cloudState !== null")
    corner = page.locator("#chat-cloud-resize").bounding_box()

    page.mouse.move(corner["x"] + 9, corner["y"] + 9)
    page.mouse.down()
    page.mouse.move(corner["x"] - 600, corner["y"] - 300, steps=10)
    page.wait_for_timeout(2000)                       # the record lands mid-drag
    page.mouse.up()
    page.wait_for_timeout(300)

    width = page.evaluate("cloudState.open.w")
    assert width != 520                               # not the record's width
    assert posts and posts[-1]["value"]["open"]["w"] == width


def _synthetic_login(page, owner, home="home-test"):
    """Drive the app lifecycle with fixture-only identities; no OAuth or live account."""
    page.evaluate("""({owner,home}) => {
      enterSignedOut();
      sessionStorage.setItem(TOKEN_KEY,owner);
      sessionStorage.setItem(EXP_KEY,String(Math.floor(Date.now()/1000)+3600));
      setQueueScope(home); setQueueOwner(owner);
      showView('chat'); refreshChatCloud();
    }""", {"owner": owner, "home": home})


def test_placement_follows_owner_to_a_fresh_browser_context(server, page):
    url, posts, _state = server
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    _synthetic_login(page, "synthetic-alice")
    _wait_for_placement(page, "open", 200)
    page.locator("#chat-cloud-bar").focus()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    _wait_for_placement(page, "open", 168)
    page.evaluate("() => cloudPrefs.writeTail")
    assert posts[-1]["test_owner"] == "Bearer synthetic-alice"
    assert posts[-1]["value"]["open"]["x"] == 168

    other = page.context.browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        other.goto(url)
        other.wait_for_selector("#view-signin", state="visible")
        assert other.evaluate("localStorage.length") == 0
        _synthetic_login(other, "synthetic-alice")
        _wait_for_placement(other, "open", 168)
        box = other.locator("#chat-cloud").bounding_box()
        stage = other.locator("#chat-stage").bounding_box()
        assert box["x"] - stage["x"] == pytest.approx(168, abs=2)
    finally:
        other.close()


def test_phone_placement_and_repeated_bubble_interaction_are_separate_from_desktop(server, page):
    url, posts, state = server
    state["records"][("Bearer synthetic-alice", "phone")] = {
        **RECORD, "mode": "bubble", "bubble": {"x": 50, "y": 70}}
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    _synthetic_login(page, "synthetic-alice")
    _wait_for_placement(page, "bubble", 50)
    for _ in range(3):
        page.click("#chat-cloud-bubble")
        assert page.evaluate("document.activeElement.id") == "composer-input"
        box = page.locator("#chat-cloud").bounding_box()
        stage = page.locator("#chat-stage").bounding_box()
        assert box["width"] == pytest.approx(stage["width"], abs=2)
        page.click("#btn-cloud-shrink")
    page.evaluate("() => cloudPrefs.writeTail")
    assert posts and all(p["viewport"] == "phone" for p in posts)
    assert posts[-1]["value"]["mode"] == "bubble"
    assert ("Bearer synthetic-alice", "wide") not in state["records"]


def test_late_account_and_home_reads_cannot_replace_current_owners_placement(server, page):
    url, posts, state = server
    alice_key = ("Bearer synthetic-alice", "wide")
    bob_key = ("Bearer synthetic-bob", "wide")
    state["records"][bob_key] = {**RECORD, "open": {**RECORD["open"], "x": 80}}
    state["seconds"] = 0.5
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    with page.expect_request(lambda r: "/app/ui-prefs?" in r.url):
        _synthetic_login(page, "synthetic-alice")
    _synthetic_login(page, "synthetic-bob")
    _wait_for_placement(page, "open", 80)
    page.wait_for_timeout(600)
    assert page.evaluate("cloudState.open.x") == 80
    local_key = "app.chatCloud.v1:synthetic-alice:main:wide"
    assert page.evaluate("key => localStorage.getItem(key)", local_key) is None
    assert posts == []

    state["records"][alice_key] = {**RECORD, "open": {**RECORD["open"], "x": 100}}
    state["read_started"].clear()
    with page.expect_request(lambda r: "/app/ui-prefs?" in r.url):
        _synthetic_login(page, "synthetic-alice", "home-old")
    assert state["read_started"].wait(timeout=5)
    # Change the home while its old response is outstanding. The preferences
    # remain account-scoped, but the old lifecycle is no longer allowed to paint.
    state["records"][alice_key] = {**RECORD, "open": {**RECORD["open"], "x": 300}}
    page.evaluate("setQueueScope('home-new')")
    _wait_for_placement(page, "open", 300)
    page.wait_for_timeout(600)
    assert page.evaluate("cloudState.open.x") == 300
    assert page.evaluate("queueScope") == "home-new"
    assert posts == []


def test_delayed_saved_bubble_keeps_the_composer_draft_selection_and_focus(server, page):
    url, _posts, state = server
    state["seconds"] = 0.5
    state["records"][("Bearer synthetic-alice", "wide")] = {**RECORD, "mode": "bubble"}
    page.goto(url)
    page.wait_for_selector("#view-signin", state="visible")
    _synthetic_login(page, "synthetic-alice")
    page.fill("#composer-input", "Keep this draft")
    page.locator("#composer-input").evaluate("el => el.setSelectionRange(5,9)")
    page.wait_for_timeout(800)
    assert page.locator("#composer-input").is_visible()
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "Keep this draft"
    assert page.locator("#composer-input").evaluate(
        "el => [el.selectionStart,el.selectionEnd]") == [5, 9]
    page.locator("#chat-cloud-bar").focus()
    page.wait_for_selector("#chat-cloud-bubble", state="visible")
    page.click("#chat-cloud-bubble")
    assert page.input_value("#composer-input") == "Keep this draft"
