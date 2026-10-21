"""The chat cloud in a real Chromium: drag, resize, shrink, restore, stay on screen.

The real app page (``render_app_html``, with its own CSP) is served locally,
signed-in state is entered the way the page itself does it (``showView``), and
every gesture is a real mouse or keyboard input.
"""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

# Run, with a skip counted as a failure, by .github/workflows/real-browser-proof.yml.
pytestmark = pytest.mark.real_browser


@pytest.fixture(scope="module")
def app_url():
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.split("?")[0] == "/app/ui-frame":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for name, value in FRAME_HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(BOOTSTRAP_HTML.encode("utf-8"))
                return
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
def required_browser():
    """Share Chromium without converting import or launch failures into skips."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        chromium = runtime.chromium.launch(
            executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM") or None
        )
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="owner=codex runs-in=real-browser-proof"
    )
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch(
                executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM") or None
            )
        except Exception as exc:  # noqa: BLE001 - no browser binary on this host
            pytest.skip(
                "owner=codex runs-in=real-browser-proof Chromium is not available here: "
                f"{exc.__class__.__name__}"
            )
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
    page.wait_for_function("() => cloudState !== null")


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _drag(page, selector, dx, dy, *, at=(0.5, 0.5)):
    box = _box(page, selector)
    x, y = box["x"] + box["width"] * at[0], box["y"] + box["height"] * at[1]
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=10)
    page.mouse.up()



# The retired #cc-blank stand-in had a different focus/composer contract.
# The replacement test below mounts and clicks the shipped platform bundle;
# its browser execution is required in real-browser-proof, not inferred from
# the synthetic play UI or the Node bridge tests.
















# A UI that grabs the keyboard the way a real game does (Furry House): a canvas
# it focuses itself, and a keydown handler that swallows everything it sees.
_GRABBER = """() => AppUI.mount({ui_id:'play', name:'Play',
  markup:'<canvas id="scene" tabindex="0"></canvas><input id="say">',
  style:'#scene{width:100%;height:60%;background:#234}#say{width:50%}',
  script:`const scene=document.getElementById('scene');
    scene.focus();
    window.keysSeen=[];
    document.addEventListener('keydown',e=>{
      window.keysSeen.push(e.key);
      e.stopPropagation(); e.preventDefault();   // a game that eats every key
    });`})"""




# The same UI without the key-swallowing handler: this one lets its own input
# receive what it is given, which is the point of the test below.
_TEXT_UI = """() => AppUI.mount({ui_id:'play', name:'Play',
  markup:'<canvas id="scene" tabindex="0"></canvas><input id="say">',
  style:'#scene{width:100%;height:40%;background:#234}#say{width:50%}',
  script:`document.getElementById('scene').focus();`})"""








# All data and frames are local fixtures. Observe browser cancellation/selection,
# never read or assert the host clipboard's contents.
def _shortcut_page(browser, app_url):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.evaluate("""() => AppUI.mount({ui_id:'keys',name:'Keys',
      markup:'<div id="key-probe" tabindex="0">Local frame</div>',
      style:'#key-probe{width:100%;height:100%}',
      script:`window.observedKeys=[];
        for(const phase of ['keydown','keyup']) document.addEventListener(phase,e=>{
          window.observedKeys.push({key:e.key,phase,trusted:e.isTrusted});
        });`})""")
    page.frame_locator('#ui-frame').locator('#key-probe').wait_for()
    page.click('#chat-cloud-bubble')
    page.evaluate("""() => {
      const message=document.createElement('p');
      message.id='selection-probe'; message.textContent='Message text stays selectable';
      document.getElementById('chat-cloud').appendChild(message);
      document.activeElement.blur();
      window.parentKeys=[];
      for(const phase of ['keydown','keyup']) window.addEventListener(phase,e=>{
        window.parentKeys.push({key:e.key,phase,prevented:e.defaultPrevented});
      });
    }""")
    assert page.evaluate('document.activeElement.tagName') == 'BODY'
    return page


def _frame_keys(page):
    return page.frame_locator('#ui-frame').locator('#key-probe').evaluate(
        '() => window.observedKeys')


def _select_message(page):
    page.evaluate("""() => {
      const range=document.createRange();
      range.selectNodeContents(document.getElementById('selection-probe'));
      const selection=window.getSelection(); selection.removeAllRanges();
      selection.addRange(range);
    }""")








