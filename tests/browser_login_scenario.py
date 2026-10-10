"""Executed after the production bootstrap has retired all roles in the image."""


def browser_proof(root, owner, home, other, other_home):
    import asyncio
    import base64
    import http.client
    import http.server
    import json
    import secrets
    import threading
    import time
    from urllib.parse import parse_qs, urlsplit

    from starlette.requests import Request

    from tinyassets import (
        browser_egress,
        browser_sessions,
        request_continuations,
        ta_cli,
        turn_interrupt,
    )
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.browser_vault import operation
    from tinyassets.onboarding import app_config
    from tinyassets.onboarding.browser_login import handle
    from tinyassets.onboarding.owner_sessions import COOKIE, hashed, store
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    actions = []
    hops = []
    password = "oracle-password-only"
    cookie = "oracle-cookie-" + secrets.token_hex(16)
    expiry_redirect = False
    form = """<style>input,button,a{position:absolute;left:20px;width:240px;height:40px}
    #user{top:20px}#pass{top:80px}button{top:140px}a{top:210px}</style>
    <form method="post" action="/login"><input id="user" name="user" aria-label="User">
    <input id="pass" name="password" type="password" aria-label="Password">
    <button>Sign in</button></form>
    <a href="https://identity.example/start">Sign in with Example</a>
    <a style="top:280px" href="/start">Reload sign-in</a>"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body="", **headers):
            hops.append((self.headers.get("X-Fixture-Host"), self.path, status))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            if self.path == '/blocked':
                self.reply(403, 'Remote browser access denied')
            elif self.path == "/start":
                self.reply(200, form)
            elif self.path == "/callback":
                self.reply(
                    302,
                    Location="https://site.example/",
                    Set_Cookie="session=" + cookie + "; Secure; HttpOnly; Path=/",
                )
            elif "session=" + cookie in self.headers.get("Cookie", ""):
                self.reply(
                    200,
                    '<h1 id=account>Account</h1><form action="/action" method="post">'
                    '<button id="act">Perform action</button></form><p>Saved: '
                    + str(len(actions))
                    + "</p><p>"
                    + cookie
                    + "</p>",
                )
            else:
                if expiry_redirect and self.headers['X-Fixture-Host'] == 'site.example':
                    self.reply(302, Location='https://identity.example/start')
                else:
                    self.reply(200, form)

        def do_POST(self):
            data = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            if self.path == "/login" and data.get("password") == [password]:
                if self.headers["X-Fixture-Host"] == "identity.example":
                    self.reply(302, Location="https://site.example/callback")
                else:
                    self.reply(
                        302,
                        Location="https://site.example/",
                        Set_Cookie="session=" + cookie + "; Secure; HttpOnly; Path=/",
                    )
            elif self.path == "/action" and "session=" + cookie in self.headers.get("Cookie", ""):
                actions.append("done")
                self.reply(302, Location="https://site.example/")
            else:
                self.reply(403, "Refused")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_fetch = browser_egress.fetch

    def fixture_fetch(document):
        url = urlsplit(document["url"])
        assert url.hostname in {"site.example", "identity.example"}
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            headers = {
                k: v
                for k, v in document["headers"].items()
                if k.lower() not in {"content-length", "host"}
            }
            headers["X-Fixture-Host"] = url.hostname
            connection.request(
                document["method"], url.path or "/", base64.b64decode(document["body"]), headers
            )
            response = connection.getresponse()
            return dict(
                status=response.status,
                headers=dict(response.getheaders()),
                body=base64.b64encode(response.read()).decode(),
            )
        finally:
            connection.close()

    owner_cookie = secrets.token_hex(32)
    session = {
        "session_hash": hashed(owner_cookie),
        "expires_at": time.time() + 600,
        "identity_json": json.dumps({"user_id": owner}),
    }
    with store() as conn:
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (session["session_hash"], session["identity_json"], session["expires_at"]),
        )
    browser_egress.fetch = fixture_fetch
    context = ExecutionContext(home, owner, "main")
    resource = urlsplit(app_config()["resource"])
    app_origin = resource.scheme + "://" + resource.netloc
    caps = Capabilities(root / home, context, [], None, lambda: None, capability_grant=["bash"])

    def ta(document):
        return ta_cli.main(
            ["browser", "--json", json.dumps(document)],
            dispatch=lambda message: asyncio.run(caps.dispatch(message)),
            load_extensions=False,
        )

    evidence = []
    try:
        with identity_context(Identity(owner, owner, capabilities=["read", "write", "list"])):
            for redirect in (False, True):
                expiry_redirect = False
                with turn_interrupt.interactive_turn(owner, home):
                    row = ta(
                        {
                            "action": "connect",
                            "url": "https://site.example/",
                            "account": "person",
                            "verify": {"url": "https://site.example/", "selector": "#account"},
                        }
                    )
                assert row["request"].get("request_id"), row
                ident = row["id"]

                visible_origin = "https://site.example"

                def view(action, **kwargs):
                    nonlocal visible_origin
                    if action == "input":
                        kwargs["event"]["origin"] = visible_origin
                    refused = kwargs.pop("refused", False)
                    body = json.dumps(
                        dict(
                            id=ident,
                            action=action,
                            universe_id=home,
                            request_id=row["request"]["request_id"],
                            **kwargs,
                        )
                    ).encode()

                    async def receive():
                        return {"type": "http.request", "body": body, "more_body": False}

                    request = Request(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/app/browser-login",
                            "headers": [
                                (b"origin", app_origin.encode()),
                                (b"content-type", b"application/json"),
                                (b"cookie", (COOKIE + "=" + owner_cookie).encode()),
                            ],
                        },
                        receive,
                    )
                    response = asyncio.run(handle(request))
                    value = json.loads(response.body)
                    if refused:
                        assert response.status_code == 409, value
                    else:
                        assert response.status_code == 200, value
                    if value.get("origin"):
                        visible_origin = value["origin"]
                    return value

                assert view("begin").get("status") == "login"
                frame = view("frame")
                assert base64.b64decode(frame["image"]).startswith(b"\xff\xd8")
                capture = browser_sessions._captures[(str(root), owner, home, ident)]
                proof = capture["cell"].proof
                assert proof["uid"] > 0 and proof["caps"] == "zero"
                assert capture["cell"].call({"action": "steps", "steps": []}).get("error")
                if redirect:
                    view("input", event={"kind": "click", "x": 100, "y": 230})
                    assert view("frame")["origin"] == "https://identity.example"
                view("input", event={"kind": "click", "x": 100, "y": 40})
                view("input", event={"kind": "text", "text": "person"})
                view("input", event={"kind": "key", "key": "Tab"})
                binding = view("frame")["field"]
                assert binding["type"] == "password"
                view("input", event={"kind": "click", "x": 100, "y": 300})
                view("fill_private", token=binding["token"], origin=binding["origin"],
                     value=password, refused=True)
                view("frame")
                view("input", event={"kind": "click", "x": 100, "y": 40})
                view("input", event={"kind": "text", "text": "person"})
                view("input", event={"kind": "key", "key": "Tab"})
                binding = view("frame")["field"]
                view(
                    "fill_private",
                    token=binding["token"],
                    origin="https://wrong.example",
                    value=password,
                    refused=True,
                )
                binding = view("frame")["field"]
                view(
                    "fill_private", token=binding["token"], origin=binding["origin"], value=password
                )
                view("input", event={"kind": "click", "x": 100, "y": 160})
                saved = view("frame")
                for _ in range(10):
                    if saved.get("status") == "connected":
                        break
                    time.sleep(0.35)  # Match the live view's bounded frame-poll cadence.
                    saved = view("frame")
                assert saved.get("status") == "connected", (saved, hops)
                assert (str(root), owner, home, ident) not in browser_sessions._captures
                resumed = []

                def resume(_, outcome):
                    resumed.append(
                        ta(
                            {
                                "action": "steps",
                                "id": ident,
                                "steps": [{"kind": "click", "selector": "#act"}],
                            }
                        )
                    )
                    return {"reply": "Action completed"}

                assert request_continuations.recover(root / home, run=resume) == 1
                assert request_continuations.recover(root / home, run=resume) == 0
                assert len(resumed) == 1
                result = resumed[0]
                remembered = ta(
                    {
                        "action": "connect",
                        "url": "https://site.example/",
                        "account": "person",
                        "verify": {"url": "https://site.example/", "selector": "#account"},
                    }
                )
                assert remembered.get("remembered") and remembered["id"] == ident, remembered
                assert "request" not in remembered
                assert result.get("untrusted") is True, result
                assert "Saved: " + str(len(evidence) + 1) in result["text"], result
                assert cookie not in json.dumps(result) and password not in json.dumps(result)
                try:
                    operation(root, other, other_home, {"action": "read", "id": ident})
                except PermissionError:
                    pass
                else:
                    raise AssertionError("foreign owner read browser custody")
                cookie = "expired-" + secrets.token_hex(16)
                expiry_redirect = redirect
                with turn_interrupt.interactive_turn(owner, home):
                    expired = ta({"action": "steps", "id": ident, "steps": [{"kind": "read"}]})
                assert expired.get("needs_login") and expired["id"] == ident, expired
                row = expired
                assert view("begin").get("status") == "login"
                view("frame")
                view("input", event={"kind": "click", "x": 100, "y": 40})
                view("input", event={"kind": "text", "text": "person"})
                view("input", event={"kind": "key", "key": "Tab"})
                binding = view("frame")["field"]
                view(
                    "fill_private", token=binding["token"], origin=binding["origin"], value=password
                )
                view("input", event={"kind": "click", "x": 100, "y": 160})
                saved = view('frame')
                for _ in range(10):
                    if saved.get('status') == 'connected':
                        break
                    time.sleep(0.35)  # Match the live view's bounded frame-poll cadence.
                    saved = view('frame')
                assert saved.get('status') == 'connected', saved.get('status', list(saved))
                relogin_results = []

                def resume_read(_, outcome):
                    relogin_results.append(ta({"action": "steps", "id": ident,
                                              "steps": [{"kind": "read"}]}))
                    return {"reply": "Session restored"}

                assert request_continuations.recover(root / home, run=resume_read) == 1
                assert relogin_results[0].get('untrusted'), relogin_results
                assert view("revoke")["status"] == "revoked"
                refused = ta({"action": "steps", "id": ident, "steps": [{"kind": "read"}]})
                assert "error" in refused, refused
                evidence.append(
                    {
                        "login": "redirect" if redirect else "password",
                        "uid": proof["uid"],
                        "gid": proof["gid"],
                        "later_action": True,
                        "automatic_continuation": True,
                        "zero_tap_reuse": True,
                        "expiry_relogin": True,
                        "origin_bound_fill": True,
                        "revoked": True,
                        "foreign_refused": True,
                    }
                )
            from contextlib import closing

            from tinyassets import bound_requests
            from tinyassets.storage.pending_requests import get_request

            with turn_interrupt.interactive_turn(owner, home):
                row = ta({'action': 'connect', 'url': 'https://site.example/blocked',
                    'account': 'Blocked', 'verify': {'url': 'https://site.example/blocked',
                                                   'selector': '#account'}})
            ident = row['id']
            assert view('begin').get('blocked')
            requests_before = len(hops)
            duplicate = ta({'action': 'connect', 'url': 'https://site.example/blocked',
                            'account': 'Blocked'})
            assert duplicate['request']['request_id'] == row['request']['request_id']
            assert len(hops) == requests_before  # No hidden retry after the detected block.
            with closing(bound_requests.connect(root / home)) as conn:
                context = json.loads(conn.execute(
                    'SELECT context_json FROM pending_requests WHERE request_id=?',
                    (row['request']['request_id'],)).fetchone()[0])
                assert context['kind'] == 'connection'
                conn.execute("UPDATE activities SET status='paused',"
                             'task_generation=task_generation+1 '
                             'WHERE activity_id=?', (context['task_id'],))
                conn.commit()
            view('begin', refused=True)
            assert len(hops) == requests_before  # Stop refuses before starting a browser.
            assert view('revoke')['status'] == 'revoked'
            assert view('revoke')['status'] == 'revoked'  # Lost-response retry is safe.
            assert get_request(root / home, row['request']['request_id'])['status'] == 'dismissed'
        assert len(actions) == 2
        return {"scenarios": evidence, "actions": len(actions), 'blocked_no_retry': True,
                'stopped_capture_refused': True}
    finally:
        browser_egress.fetch = real_fetch
        server.shutdown()
