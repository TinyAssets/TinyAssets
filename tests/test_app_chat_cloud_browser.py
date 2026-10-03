"""The chat cloud in a real Chromium: drag, resize, shrink, restore, stay on screen.

The real app page (``render_app_html``, with its own CSP) is served locally,
signed-in state is entered the way the page itself does it (``showView``), and
every gesture is a real mouse or keyboard input.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tinyassets.onboarding import render_app_html

# Run, with a skip counted as a failure, by .github/workflows/real-browser-proof.yml.
pytestmark = pytest.mark.real_browser


@pytest.fixture
def app_url():
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.split("?")[0] != "/app":
                self.send_response(404)
                self.end_headers()
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(404)
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/app"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser binary on this host
            pytest.skip(f"Chromium is not available here: {exc.__class__.__name__}")
        try:
            yield chromium
        finally:
            chromium.close()


def _enter_chat(page, url, *, layout=False):
    page.goto(url)
    # Let the page finish its own boot first: with no session it lands on the
    # sign-in view, and a boot that finished later would hide the chat again.
    page.wait_for_selector("#view-signin", state="visible")
    page.wait_for_load_state("networkidle")
    page.evaluate("""(layout) => {
        setQueueOwner('owner-1');
        if (layout) document.getElementById('view-chat').classList.add('ui-custom-active');
        showView('chat'); refreshChatCloud();
    }""", layout)
    page.wait_for_function("cloudState !== null")


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _drag(page, selector, dx, dy, *, at=(0.5, 0.5)):
    box = _box(page, selector)
    x, y = box["x"] + box["width"] * at[0], box["y"] + box["height"] * at[1]
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=10)
    page.mouse.up()


def test_it_starts_medium_and_can_be_dragged_resized_and_remembered(app_url, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    _enter_chat(page, app_url)
    stage = _box(page, "#chat-stage")
    big = _box(page, "#chat-cloud")
    assert big["width"] == 440 and big["height"] == 620
    assert big["x"] == stage["width"] - 452

    _drag(page, "#chat-cloud-resize", -100, -200)
    resized = _box(page, "#chat-cloud")
    assert resized["width"] == pytest.approx(big["width"] - 100, abs=2)
    _drag(page, "#chat-cloud-bar", -200, -120, at=(0.8, 0.5))
    moved = _box(page, "#chat-cloud")
    assert moved["x"] == pytest.approx(resized["x"] - 200, abs=2)
    assert moved["y"] == pytest.approx(resized["y"] - 120, abs=2)

    # A reload restores exactly where the owner left it.
    page.reload()
    _enter_chat(page, app_url)
    again = _box(page, "#chat-cloud")
    assert (again["x"], again["y"], again["width"]) == pytest.approx(
        (moved["x"], moved["y"], moved["width"]), abs=2)
    context.close()


def test_shrink_to_a_bubble_that_drags_without_opening_and_opens_on_click(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)

    page.click("#btn-cloud-shrink")
    assert page.locator("#chat-cloud").is_hidden()
    assert page.locator("#chat-cloud-bubble").is_visible()
    # Focus left the chat. There is no in-document stand-in to name any more:
    # the command center is a mounted bundle, and with none mounted here the
    # point is simply that the shrunk chat does not keep the keyboard.
    assert page.evaluate("document.activeElement.closest('#chat-cloud') === null")

    before = _box(page, "#chat-cloud-bubble")
    _drag(page, "#chat-cloud-bubble", -300, -200)
    after = _box(page, "#chat-cloud-bubble")
    assert after["x"] == pytest.approx(before["x"] - 300, abs=2)
    assert page.locator("#chat-cloud").is_hidden()        # a drag is not a click

    page.click("#chat-cloud-bubble")
    assert page.locator("#chat-cloud").is_visible()
    assert page.evaluate("document.activeElement.id") == "composer-input"


def test_a_command_center_layout_starts_it_small(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url, layout=True)

    assert page.locator("#chat-cloud").is_hidden()
    bubble = _box(page, "#chat-cloud-bubble")
    stage = _box(page, "#chat-stage")
    assert bubble["x"] + bubble["width"] <= stage["x"] + stage["width"]
    assert bubble["y"] + bubble["height"] <= stage["y"] + stage["height"]


def test_it_stays_on_screen_when_the_window_shrinks(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    _drag(page, "#chat-cloud-resize", -700, -400)
    _drag(page, "#chat-cloud-bar", 600, 300, at=(0.3, 0.5))

    page.set_viewport_size({"width": 800, "height": 600})
    page.wait_for_timeout(200)
    cloud, stage = _box(page, "#chat-cloud"), _box(page, "#chat-stage")
    assert cloud["x"] >= stage["x"] and cloud["y"] >= stage["y"]
    assert cloud["x"] + cloud["width"] <= stage["x"] + stage["width"] + 1
    assert cloud["y"] + cloud["height"] <= stage["y"] + stage["height"] + 1


def test_keyboard_moves_the_window(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    _drag(page, "#chat-cloud-resize", -700, -400)
    before = _box(page, "#chat-cloud")

    page.focus("#chat-cloud-bar")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Shift+ArrowRight")
    after = _box(page, "#chat-cloud")

    assert after["x"] == pytest.approx(before["x"] + 16, abs=1)
    assert after["y"] == pytest.approx(before["y"] + 16, abs=1)
    assert after["width"] == pytest.approx(before["width"] + 16, abs=1)


def test_the_bubble_shows_that_the_agent_is_working(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.click("#btn-cloud-shrink")

    page.evaluate("document.getElementById('btn-stop').hidden = false")
    page.wait_for_function("document.getElementById('chat-cloud-bubble').classList.contains('is-thinking')")
    assert "working" in page.get_attribute("#chat-cloud-bubble", "aria-label")


def test_a_narrow_cloud_keeps_send_inside_it(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    _drag(page, "#chat-cloud-resize", -2000, -2000)        # down to the minimum
    # Every composer control a web owner can have, so the widest row is measured.
    page.evaluate("""() => { for (const id of ['btn-voice', 'voice-output-select'])
        document.getElementById(id).hidden = false; }""")

    cloud, send = _box(page, "#chat-cloud"), _box(page, "#btn-send")
    assert cloud["width"] == pytest.approx(320, abs=2)
    assert send["x"] + send["width"] <= cloud["x"] + cloud["width"]
    assert send["y"] + send["height"] <= cloud["y"] + cloud["height"]


def test_a_cancelled_bubble_drag_does_not_swallow_the_next_click(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.click("#btn-cloud-shrink")
    page.evaluate("""() => {
        const b = document.getElementById('chat-cloud-bubble'), r = b.getBoundingClientRect();
        const at = (type, dx) => b.dispatchEvent(new PointerEvent(type, {bubbles: true,
            pointerId: 7, button: 0, clientX: r.x + 20 + dx, clientY: r.y + 20}));
        at('pointerdown', 0); at('pointermove', -60); at('pointercancel', -60);
    }""")

    page.click("#chat-cloud-bubble")

    assert page.locator("#chat-cloud").is_visible()


def test_auto_shrink_preserves_typing_until_focus_leaves_the_composer(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.fill("#composer-input", "Keep this draft")
    page.locator("#composer-input").evaluate("e => e.setSelectionRange(5, 9)")

    page.evaluate("""() => {
        document.getElementById('view-chat').classList.add('ui-custom-active');
        refreshChatCloud();
    }""")

    assert page.locator("#chat-cloud").is_visible()
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "Keep this draft"
    assert page.locator("#composer-input").evaluate(
        "e => [e.selectionStart, e.selectionEnd]") == [5, 9]

    # Focus leaves the composer by a real gesture. What this test is about is
    # the TYPING hold, not the command-center handoff: this file's server does
    # not serve /app/ui-frame, so no bundle can mount here and
    # focusCommandCenter would have nothing to hand the keyboard to. The
    # handoff itself is exercised in test_app_two_surfaces_browser.py, which
    # does serve the frame (gpt-6-astra on #4358 was right that the earlier
    # `blur()` here proved nothing).
    page.focus("#btn-cloud-menu")
    page.evaluate("refreshChatCloud()")
    assert page.locator("#chat-cloud").is_hidden()
    assert page.evaluate(
        "document.activeElement.closest('#chat-cloud') === null"), "not the chat's any more"
    page.click("#chat-cloud-bubble")
    assert page.input_value("#composer-input") == "Keep this draft"
