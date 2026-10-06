"""Render the editable starter component in the shipped sandbox/frame."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS
from tinyassets.starter_skills import starter_agent_files

pytestmark = pytest.mark.real_browser


@pytest.fixture
def ui_server():
    files = starter_agent_files()
    component = json.loads(files["starter/command-center.json"])
    app_js = (Path(__file__).resolve().parents[1] / "tinyassets/onboarding/app_ui.js")
    sandbox = re.search(r'SANDBOX:"([^"]*)"', app_js.read_text(encoding="utf-8"))[1]
    page = '''<!doctype html><html><body>
<iframe id="f" sandbox="SANDBOX" src="/frame" style="width:100%;height:900px"></iframe>
<script>
window.files = FILES; window.calls = [];
const frame = document.getElementById('f');
window.addEventListener('message', event => {
  if(event.source !== frame.contentWindow) return;
  const m = event.data;
  if(m.type === 'ready') frame.contentWindow.postMessage({ta_ui:1,type:'bundle',bundle:BUNDLE},'*');
  if(m.type !== 'call') return;
  window.calls.push({action:m.action,params:m.params});
  let result = {}, error = '';
  if(m.action === 'read_conversation') result = {turns:[],has_more:false};
  else if(m.action === 'read_file') {
    const text = window.files[m.params.path];
    if(text === undefined) error = 'That file is not available';
    else { const offset = m.params.offset || 0, end = offset + 40;
      result = {encoding:'text',content:text.slice(offset,end),
                next_offset:end < text.length ? end : null}; }
  } else if(m.action === 'list_files')
    result = {entries:[{name:'starter',kind:'dir'}],truncated:false};
  else if(!['send_message','open_chat'].includes(m.action)) error = 'Unsupported bridge call';
  frame.contentWindow.postMessage({ta_ui:1,type:'result',id:m.id,ok:!error,result,error},'*');
});
</script></body></html>'''.replace("SANDBOX", sandbox).replace(
        "FILES", json.dumps(files)).replace("BUNDLE", json.dumps(component))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body, headers = (BOOTSTRAP_HTML, FRAME_HEADERS) if self.path == "/frame" else (page, {})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_all_views_refresh_prefill_send_and_missing_file(ui_server):
    # Imported only inside the real_browser test; missing browser is a failure.
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 1000})
            page.goto(ui_server)
            frame = page.frame_locator("#f")
            expect(frame.get_by_text("What would you like to work on?")).to_be_visible()
            frame.get_by_role("button", name="Feed", exact=True).click()
            expect(frame.locator("pre")).to_contain_text("Interests")
            page.evaluate(
                "window.files['starter/feed.md'] = '<img src=x onerror=alert(1)>New priority'")
            frame.get_by_role("button", name="Refresh").click()
            expect(frame.locator("pre")).to_contain_text("<img src=x")
            expect(frame.locator("#content img")).to_have_count(0)

            frame.get_by_role("button", name="Goals", exact=True).click()
            expect(frame.get_by_text("Tell your agent about a goal", exact=False)).to_be_visible()
            page.evaluate("""window.files['starter/goals.json'] = JSON.stringify({goals:[
                {title:'Finish my song',status:'active',next_step:'Record vocals'}]})""")
            frame.get_by_role("button", name="Refresh").click()
            expect(frame.get_by_role("heading", name="Finish my song")).to_be_visible()
            expect(frame.get_by_text("Record vocals")).to_be_visible()

            frame.get_by_role("button", name="Files", exact=True).click()
            expect(frame.get_by_role("button", name="starter/")).to_be_visible()
            frame.get_by_role("button", name="starter/").click()
            expect(frame.get_by_role("heading", name="starter", exact=True)).to_be_visible()
            frame.get_by_role("button", name="Up one folder").click()
            expect(frame.get_by_role("heading", name="Your files")).to_be_visible()

            frame.get_by_role("button", name="Ideas", exact=True).click()
            frame.get_by_role("button", name="Help me turn my current priority",
                              exact=False).click()
            expect(frame.locator("#message")).to_have_value(
                "Help me turn my current priority into a goal and a next step.")
            assert not page.evaluate("window.calls.some(c=>c.action==='send_message')")
            frame.get_by_role("button", name="Send", exact=True).click()
            page.wait_for_function("window.calls.some(c=>c.action==='send_message')")
            expect(frame.locator("#message")).to_have_value("")

            page.evaluate("delete window.files['starter/goals.json']")
            frame.get_by_role("button", name="Goals", exact=True).click()
            expect(frame.locator("#status")).to_have_text("That file is not available")
            expect(frame.locator("#content")).to_be_empty()
        finally:
            browser.close()
