"""The real app renderer, bundled Mermaid and CSP in Chromium, with no CDN."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_app_chat_cloud_browser import browser  # noqa: F401
from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.app_modules import MODULE_DIR, module_url

pytestmark = pytest.mark.real_browser


@pytest.fixture
def page(browser):  # noqa: F811 - shared pytest fixture
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/app":
                body, media = html.encode(), "text/html"
            elif self.path == module_url("mermaid_vendor.js"):
                body, media = (MODULE_DIR / "mermaid_vendor.js").read_bytes(), "text/javascript"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(401)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    context = browser.new_context()
    tab = context.new_page()
    tab.goto(f"http://127.0.0.1:{server.server_port}/app")
    tab.wait_for_selector("#view-signin", state="visible")
    tab.evaluate("""() => {setQueueOwner('owner');setQueueScope('home');showView('chat');}""")
    try:
        yield tab
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_real_reply_links_diagram_chart_and_fallback_under_csp(page):
    source = (
        "[link](https://example.org) http://example.org [bad](javascript:alert(1))\n"
        "```mermaid\ngraph TD; A[Start]-->B[End]\n```\n"
        '```chart\n{"type":"bar","labels":["A","B"],'
        '"series":[{"name":"Count","values":[2,4]}]}\n```\n'
        "```chart\nnot json\n```"
    )
    page.evaluate("source => appendMessage('universe',source)", source)
    body = page.locator(".msg-body").last
    assert body.locator("a").count() == 2
    assert body.locator("a").first.get_attribute("target") == "_blank"
    assert body.locator("a").first.get_attribute("rel") == "noopener noreferrer"
    page.wait_for_selector(".chat-diagram", timeout=20000)
    assert body.locator("svg").count() == 1
    assert body.locator("pre").inner_text() == "not json\n"
    assert page.locator(".chat-diagram").evaluate("img => img.naturalWidth > 0")
    svg_source = page.locator(".chat-diagram").evaluate(
        "img => decodeURIComponent(img.src.split(',')[1])"
    )
    assert "Start" in svg_source and "End" in svg_source
    assert "foreignObject" not in svg_source and "<script" not in svg_source
    page.route("https://example.org/**", lambda route: route.fulfill(body="linked"))
    with page.expect_popup() as popup:
        body.locator("a").first.click()
    assert popup.value.url.startswith("https://example.org")


def test_mermaid_parse_error_and_blocked_library_keep_code(page):
    page.route("**/mermaid_vendor.js", lambda route: route.abort())
    page.evaluate(r"""() => appendMessage('universe','```mermaid\ngraph TD; A-->B\n```')""")
    assert page.locator(".msg-body pre").inner_text() == "graph TD; A-->B\n"
    assert page.locator(".chat-diagram").count() == 0


def test_invalid_mermaid_and_agent_html_stay_inert(page):
    page.evaluate(
        "source => appendMessage('universe',source)",
        '<img src=x onerror="window.pwned=1">\n```mermaid\nnot a diagram\n```',
    )
    page.evaluate("""async () => {
      if(window.mermaid) return;
      await new Promise(resolve => document.querySelector('script[src*="mermaid_vendor"]')
        .addEventListener('load', resolve, {once:true}));
    }""")
    assert page.locator(".msg-body pre").inner_text() == "not a diagram\n"
    assert page.locator(".msg-body img").count() == 0
    assert page.evaluate("window.pwned === undefined")


def test_file_click_uses_owner_session_and_downloads_bytes(page):
    page.evaluate(r"""() => {
      ensureFreshToken=async()=>{};token=()=> 'owner-token';
      appendMessage('universe','```file\n{"path":"exports/report.csv","name":"report.csv"}\n```');
    }""")
    requests = []

    def serve(route):
        requests.append(route.request)
        route.fulfill(status=200, content_type="application/octet-stream", body="a,b\r\n1,2\r\n")

    page.route("**/app/api/file", serve)
    with page.expect_download() as download:
        page.get_by_role("button", name="Download report.csv").click()
    assert download.value.suggested_filename == "report.csv"
    assert requests[0].post_data_json == {"graph_id": "home", "path": "exports/report.csv"}
    assert requests[0].headers["authorization"] == "Bearer owner-token"


def test_account_change_during_body_read_prevents_download(page):
    result = page.evaluate("""async() => {
      ensureFreshToken=async()=>{};token=()=> 'owner-token';
      const download=chatFileDownloader();
      const original=fetch;
      fetch=async()=>({ok:true,blob:async()=>{MCP._loginEpoch++;return new Blob(['private']);}});
      try{await download('exports/x.csv','x.csv');return false;}
      catch(_){return true;}finally{fetch=original;}
    }""")
    assert result is True
