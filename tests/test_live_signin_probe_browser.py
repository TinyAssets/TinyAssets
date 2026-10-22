"""Real Chromium against a loopback-only hosted form, never production."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest

from scripts.live_signin_probe import Probe, Refused

pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize("refusal", [False, True])
def test_hosted_form_redirect_cannot_run_the_app(refusal):
    from playwright.sync_api import sync_playwright

    probe = Probe(
        "probe@example.test", "synthetic-password", "user_probe", base="https://app.example.test"
    )
    seen = []

    class Hosted(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never print request bodies or authorization query strings.

        def do_GET(self):
            path = urlsplit(self.path).path
            seen.append(("GET", path))
            if path == "/oauth2/authorize":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", "login=started; Path=/")
                self.end_headers()
                return
            assert path == "/login"
            assert "login=started" in self.headers.get("Cookie", "")
            self.html(
                '<form action="/email" method="post"><input type="email" name="email">'
                '<button type="submit">Continue</button></form>'
            )

        def do_POST(self):
            path = urlsplit(self.path).path
            seen.append(("POST", path))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if path == "/email":
                assert b"probe%40example.test" in body
                self.html(
                    '<form action="/password" method="post">'
                    '<input type="password" name="password">'
                    '<button type="submit">Sign in</button></form>'
                )
            else:
                assert path == "/password"
                assert b"synthetic-password" in body
                if refusal:
                    self.html('<div role="alert">Invalid email or password</div>')
                else:
                    self.send_response(302)
                    self.send_header("Location", probe.redirect + "?code=fake&state=" + probe.state)
                    self.end_headers()

        def html(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hosted)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_port}"
    cfg = {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/oauth2/authorize",
        "client_id": "fake",
        "resource": probe.base + "/mcp",
        "scopes": "openid",
    }
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # Only explicit route.fetch calls can reach the loopback server.
                # Any escaped redirect/resource request fails with offline mode;
                # app.example.test is reserved and never points at production.
                context = browser.new_context(offline=True, service_workers="block")
                page = context.new_page()
                page.set_default_timeout(3000)
                if refusal:
                    with pytest.raises(Refused, match="Invalid email or password"):
                        probe.hosted_login(page, cfg, timeout=5)
                else:
                    result = probe.hosted_login(page, cfg, timeout=5)
                    assert probe.callback_code(result) == "fake"
                assert seen == [
                    ("GET", "/oauth2/authorize"),
                    ("GET", "/login"),
                    ("POST", "/email"),
                    ("POST", "/password"),
                ]
                assert urlsplit(page.url).hostname == "127.0.0.1"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
