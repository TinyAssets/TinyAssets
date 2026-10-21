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


@pytest.fixture(scope="module")
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
    threading.Thread(target=httpd.serve_forever,
                     kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/app"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(scope="module")
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
    # enterSignedOut reveals this view at the end of boot. The visible-view
    # wait above is the readiness signal; networkidle adds a fixed 500 ms.
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




















