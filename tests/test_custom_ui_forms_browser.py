"""A custom UI's <form> submits to its own script, and nowhere else.

Live, 2026-10-01: the founder's village UI "Post quest" did nothing. Chrome said
"Blocked form submission to '' because the form's frame is sandboxed and the
'allow-forms' permission is not set": the submit event never fired, so the
bundle's handler never ran. Run in a real Chromium against the SHIPPED sandbox
attribute (``AppUI.SANDBOX``) and the shipped frame document and headers.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

# Run, with a skip counted as a failure, by .github/workflows/real-browser-proof.yml.
pytestmark = pytest.mark.real_browser

APP_UI = (Path(__file__).resolve().parents[1] / "tinyassets" / "onboarding"
          / "app_ui.js").read_text(encoding="utf-8")
SANDBOX = re.search(r'SANDBOX:"([^"]*)"', APP_UI).group(1)

BUNDLE = {
    "markup": (
        '<form id="quest"><input name="title" value="Find the dragon">'
        '<button id="post" type="submit">Post quest</button></form>'
        '<form id="leak" method="get" action="EXFIL"><input name="secret" value="s3">'
        '<button id="send" type="submit">Send</button></form>'
    ),
    "script": (
        "document.addEventListener('securitypolicyviolation', function (e) {"
        "  tinyassets.call('violation', {directive: e.violatedDirective}); });"
        "document.getElementById('quest').addEventListener('submit', function (e) {"
        "  e.preventDefault();"
        "  tinyassets.call('submitted', {title: e.target.title.value}); });"
        "document.getElementById('leak').addEventListener('submit', function () {"
        "  tinyassets.call('leak_submit_fired', {}); });"
    ),
}

PARENT = """<!doctype html><html><body>
<iframe id="f" sandbox="SANDBOX" src="/app/ui-frame"></iframe>
<script>
window.__calls = [];
var frame = document.getElementById('f');
window.addEventListener('message', function (event) {
  if (event.source !== frame.contentWindow) return;
  var m = event.data || {};
  if (m.type === 'ready') {
    frame.contentWindow.postMessage({ta_ui: 1, type: 'bundle', bundle: BUNDLE}, '*');
  } else if (m.type === 'call') {
    window.__calls.push({action: m.action, params: m.params});
    frame.contentWindow.postMessage(
      {ta_ui: 1, type: 'result', id: m.id, ok: true, result: {}}, '*');
  }
});
</script></body></html>"""


@pytest.fixture
def server():
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.startswith("/exfil"):
                hits.append(self.path)
                body, headers = b"leaked", {}
            elif self.path == "/app/ui-frame":
                body, headers = BOOTSTRAP_HTML.encode("utf-8"), FRAME_HEADERS
            elif self.path == "/parent":
                bundle = dict(BUNDLE)
                bundle["markup"] = bundle["markup"].replace(
                    "EXFIL", f"http://127.0.0.1:{self.server.server_port}/exfil")
                page = PARENT.replace("SANDBOX", SANDBOX).replace("BUNDLE", json.dumps(bundle))
                body, headers = page.encode("utf-8"), {}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, hits
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser binary on this host
            pytest.skip(f"Chromium is not available here: {exc.__class__.__name__}")
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _calls(page, action: str, timeout_ms: int = 5000) -> list[dict]:
    page.wait_for_function(
        f"window.__calls.some(c => c.action === {json.dumps(action)})", timeout=timeout_ms)
    return [c for c in page.evaluate("window.__calls") if c["action"] == action]


def test_a_bundles_form_submit_reaches_its_own_handler(server, page):
    port, _hits = server
    page.goto(f"http://127.0.0.1:{port}/parent")
    frame = page.frame_locator("#f")

    frame.locator("#post").click()

    [call] = _calls(page, "submitted")
    assert call["params"] == {"title": "Find the dragon"}


def test_a_real_form_submission_goes_nowhere(server, page):
    port, hits = server
    page.goto(f"http://127.0.0.1:{port}/parent")
    frame = page.frame_locator("#f")
    frame.locator("#post").wait_for()

    frame.locator("#send").click()

    _calls(page, "leak_submit_fired")
    [violation] = _calls(page, "violation")
    assert violation["params"]["directive"] == "form-action"
    page.wait_for_timeout(500)
    assert hits == []
    assert page.frame_locator("#f").locator("#post").count() == 1   # still the UI, not navigated
