"""Generic OAuth for any connection (openspec change ``generic-oauth-connections``).

Founder, 2026-09-24: "Our generic connector should prefer OAuth when the
provider allows for what the request is trying to accomplish, as that is less
actions for the user." There is no vendor code: every test here runs against
**tasklark**, a provider the platform has never heard of, played by one local
fake server that is at once its API (``api.tasklark.io``) and a standards
authorization server (``auth.tasklark.io``):

* RFC 9728 protected-resource metadata on the API host;
* RFC 8414 metadata, RFC 7591 registration of a public client;
* an authorization-code grant that really checks PKCE S256 and the redirect URI;
* single-use, rotating refresh tokens (reuse is refused, as real servers do).

Everything between the tests and that server is production code: the pending
request rail, discovery, the sign-in ingress and its fixed callback, the vault,
the ledger, and the credential-blind broker (in process, reaching the loopback
through the effector suite's socket seam).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_authenticated_external_call_effector import _install_loopback_driver
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity

OWNER = "owner-1"
UID = "u-owner"
OTHER = "owner-2"
OTHER_UID = "u-other"
API = "api.tasklark.io"
AUTH = "auth.tasklark.io"
TOKEN = "tokens.tasklark.io"  # a separate token host, so consent must name it
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(
    b"=").decode()
RESOURCE = "https://tinyassets.io/mcp"
REDIRECT = "https://tinyassets.io/app/model-callback/connect"


# --------------------------------------------------------------------------- #
# The fake provider: one loopback, routed by Host header.
# --------------------------------------------------------------------------- #


class FakeProvider:
    def __init__(self, *, expires_in=3600, advertise=True, scopes=("tasks.write",),
                 pkce=("S256",), refresh_delay=0.0, iss=False):
        self.expires_in = expires_in
        self.advertise = advertise
        self.scopes = list(scopes)
        self.pkce = list(pkce)
        self.refresh_delay = refresh_delay
        self.iss = iss  # RFC 9207: advertise and send the iss parameter
        self.clients: dict[str, list[str]] = {}
        self.codes: dict[str, dict] = {}
        self.access: dict[str, float] = {}  # token -> expiry (server clock)
        self.refresh_live: set[str] = set()
        self.refresh_spent: set[str] = set()
        self.refresh_calls = 0
        self.reused = 0
        self.api_calls: list[str] = []
        self.lock = threading.Lock()
        provider = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _reply(self, status, doc):
                from types import GeneratorType

                if isinstance(doc, GeneratorType):
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for chunk in doc:
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                            self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # The test deliberately cancelled its streaming client.
                    return
                payload = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
                if status == 202:
                    payload = b""
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if isinstance(doc, bytes)
                                 else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                host = (self.headers.get("Host") or "").split(":")[0]
                status, doc = provider.route(self.command, host, self.path,
                                             dict(self.headers.items()), body)
                self._reply(status, doc)

            do_GET = _handle  # noqa: N815
            do_POST = _handle  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # ---- the "user approves at the provider" step (a browser would do this) --
    def authorize(self, authorize_url: str) -> str:
        """Approve at the provider; returns where it sends the browser back."""
        parts = urlsplit(authorize_url)
        assert parts.hostname == AUTH and parts.path == "/authorize"
        q = dict(parse_qsl(parts.query))
        assert q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256"
        assert q["redirect_uri"] in self.clients.get(q["client_id"], []), "unregistered redirect"
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"],
                            "client_id": q["client_id"]}
        back = {"code": code, "state": q["state"]}
        if self.iss:
            back["iss"] = f"https://{AUTH}"
        return q["redirect_uri"] + "?" + urlencode(back)

    def _issue(self):
        access = "at-" + secrets.token_urlsafe(16)
        refresh = "rt-" + secrets.token_urlsafe(16)
        self.access[access] = time.time() + self.expires_in
        self.refresh_live.add(refresh)
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": self.expires_in, "refresh_token": refresh,
                "scope": " ".join(self.scopes)}

    def revoke_access(self):
        self.access.clear()

    def route(self, method, host, path, headers, body):
        if host == API and path == "/.well-known/oauth-protected-resource":
            if not self.advertise:
                return 404, {"error": "not_found"}
            return 200, {"resource": f"https://{API}", "authorization_servers": [f"https://{AUTH}"]}
        if host == AUTH and path == "/.well-known/oauth-authorization-server":
            return 200, {
                "issuer": f"https://{AUTH}",
                "authorization_endpoint": f"https://{AUTH}/authorize",
                "token_endpoint": f"https://{TOKEN}/token",
                "registration_endpoint": f"https://{AUTH}/register",
                "code_challenge_methods_supported": self.pkce,
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": self.scopes,
                "authorization_response_iss_parameter_supported": self.iss,
            }
        if host == AUTH and path == "/register" and method == "POST":
            doc = json.loads(body)
            client_id = "client-" + secrets.token_hex(4)
            self.clients[client_id] = list(doc["redirect_uris"])
            return 201, {"client_id": client_id, "token_endpoint_auth_method": "none",
                         "redirect_uris": doc["redirect_uris"]}
        if host == TOKEN and path == "/token" and method == "POST":
            form = dict(parse_qsl(body.decode()))
            if form.get("grant_type") == "authorization_code":
                grant = self.codes.pop(form.get("code", ""), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(
                    form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if (grant is None or grant["redirect_uri"] != form.get("redirect_uri")
                        or grant["client_id"] != form.get("client_id")
                        or grant["challenge"] != challenge):
                    return 400, {"error": "invalid_grant"}
                return 200, self._issue()
            if form.get("grant_type") == "refresh_token":
                if self.refresh_delay:
                    time.sleep(self.refresh_delay)
                with self.lock:
                    self.refresh_calls += 1
                    token = form.get("refresh_token", "")
                    if token in self.refresh_spent or token not in self.refresh_live:
                        self.reused += 1
                        return 400, {"error": "invalid_grant",
                                     "error_description": "refresh token already used"}
                    self.refresh_live.discard(token)
                    self.refresh_spent.add(token)
                    return 200, self._issue()
            return 400, {"error": "unsupported_grant_type"}
        if host == API and path == "/v1/tasks":
            auth = headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ")
            self.api_calls.append(token)
            if self.access.get(token, 0) <= time.time():
                return 401, {"error": "invalid_token"}
            return 200, {"ok": True}
        return 404, {"error": "not_found"}


@pytest.fixture
def provider(monkeypatch):
    from tinyassets.connection_oauth import discovery

    fake = FakeProvider()
    _install_loopback_driver(monkeypatch, fake.port)
    monkeypatch.setattr(discovery, "DISCOVERY_ENABLED", True)
    yield fake
    fake.stop()


@pytest.fixture
def universes(tmp_path, monkeypatch):
    """Two owners, each with their own home universe."""
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    base = tmp_path / "data"
    for owner, uid in ((OWNER, UID), (OTHER, OTHER_UID)):
        (base / uid).mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    for owner, uid in ((OWNER, UID), (OTHER, OTHER_UID)):
        grant_universe_access(base, universe_id=uid, actor_id=owner, permission="admin",
                              granted_by=owner)
        set_founder_home(base, founder_sub=owner, universe_id=uid, platform_generated=True)
    return base


def _as(owner):
    return identity_context(Identity(user_id=owner, username=owner,
                                     capabilities=["tinyassets.universe.write", "write"]))


TASKS_ASK = {
    "type": "connect",
    "destination": "tasklark",
    "auth_scheme": "bearer",
    "host": API,
    "path_template": "/v1/tasks",
    "methods": ["POST"],
    "uses": {"call": {}},
    "oauth": {"scopes": ["tasks.write"]},
}
KEY_FIELD = [{"name": "secret", "type": "secret", "label": "API key"}]


def _ask(action=None, fields=None, uid=UID):
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=uid, payload=json.dumps({
        "kind": "API", "title": "Connect tasklark", "body": "", "action": action or TASKS_ASK,
        "fields": KEY_FIELD if fields is None else fields,
    }))


def _post(operation, data, *, home=UID, cookie=None):
    from tests.owner_answer import session_cookie
    from tinyassets import onboarding

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io",
        ) as client:
            proof = (session_cookie() if operation == "oauth_begin" else "")
            return await client.post("/app/model-connect/" + operation, json=data,
                                     headers={"Origin": "https://tinyassets.io",
                                              "Cookie": proof if cookie is None else cookie})
    return asyncio.run(run())


def _get(path):
    from tinyassets import onboarding

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io",
        ) as client:
            return await client.get(path)
    return asyncio.run(run())


@pytest.fixture
def app(monkeypatch, universes):
    from tinyassets import onboarding

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    real = onboarding.app_config
    monkeypatch.setattr(onboarding, "app_config", lambda: {**real(), "resource": RESOURCE})
    homes = {OWNER: UID, OTHER: OTHER_UID}
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity, **kw: homes.get(identity.user_id, ""))
    return universes


def _sign_in(provider, request_id, *, owner=OWNER):
    """Tap Sign in, approve at the provider, come back: the whole owner gesture."""
    with _as(owner):
        begun = _post("oauth_begin", {"request_id": request_id, "code_challenge": CHALLENGE})
        assert begun.status_code == 200, begun.text
        back = urlsplit(provider.authorize(begun.json()["authorize_url"]))
        # The provider returns the browser to the fixed callback: a public shell.
        shell = _get(back.path + "?" + back.query)
        assert shell.status_code == 200
        q = dict(parse_qsl(back.query))
        assert q["state"] == begun.json()["flow"]
        exchange = {"flow": q["state"], "code": q["code"], "code_verifier": VERIFIER}
        if "iss" in q:
            exchange["iss"] = q["iss"]
        done = _post("oauth_exchange", exchange)
    return begun, back, done


def _broker(base, uid, owner, grant_id, runtime):
    from tinyassets.storage.outbound_connections import _build_credential_broker_dispatch

    return _build_credential_broker_dispatch({
        "allow_test_fixtures": False, "allow_http_connections": True,
        "ledger_db_path": str((base / "outbound.db").resolve()),
        "universe_dir": str((base / uid).resolve()),
        "provider": "http", "destination": "tasklark", "connection_type": "http",
        "owner_user_id": owner, "runtime_root": str(runtime.resolve()),
    })


def _call(dispatch, grant_id):
    return dispatch(grant_id, "POST", {"url": f"https://{API}/v1/tasks", "body": {"t": 1}})


def _vault_bundle(base, uid=UID):
    from tinyassets.connection_oauth.tokens import decode
    from tinyassets.credential_vault import load_credential_vault

    records = [r for r in load_credential_vault(base / uid)
               if r.get("destination") == "tasklark"]
    assert len(records) == 1
    return decode(records[0]["token"])


def _connected(provider, app, tmp_path):
    with _as(OWNER):
        asked = _ask()
    assert asked.get("primary") == "sign_in", asked
    _begun, _back, done = _sign_in(provider, asked["request_id"])
    assert done.status_code == 200, done.text
    from tinyassets.api.http_connection import _ids

    _cid, grant_id = _ids(universe_id=UID, destination="tasklark")
    return done, grant_id


# --------------------------------------------------------------------------- #
# 1. Discovery from a fake authorization server.
# --------------------------------------------------------------------------- #


def test_discovery_follows_the_resource_to_its_authorization_server(provider):
    from tinyassets.connection_oauth.discovery import resolve_offer

    offer, reason = resolve_offer({"scopes": ["tasks.write"]}, [API])
    assert reason == ""
    assert offer == {
        "issuer": f"https://{AUTH}", "authorize_url": f"https://{AUTH}/authorize",
        "token_url": f"https://{TOKEN}/token", "client_id": "",
        "registration_url": f"https://{AUTH}/register", "iss_parameter_supported": False,
        "scopes": ["tasks.write"],
        "source": "discovered",
    }


def test_discovery_refuses_what_does_not_cover_the_request(provider):
    from tinyassets.connection_oauth.discovery import resolve_offer

    assert resolve_offer({"scopes": ["billing.admin"]}, [API]) == (None, "scopes_not_offered")
    provider.pkce = ["plain"]
    assert resolve_offer({"scopes": ["tasks.write"]}, [API]) == (None, "pkce_not_offered")
    provider.pkce = ["S256"]
    provider.advertise = False  # no RFC 9728 document, and the API host is no issuer
    offer, reason = resolve_offer({}, [API])
    assert offer is None and reason == "no_authorization_server_metadata"


def test_discovery_refuses_metadata_for_another_issuer(provider, monkeypatch):
    from tinyassets.connection_oauth.discovery import resolve_offer

    original = provider.route

    def lying(method, host, path, headers, body):
        status, doc = original(method, host, path, headers, body)
        if path == "/.well-known/oauth-authorization-server":
            doc = {**doc, "issuer": "https://elsewhere.example"}
        return status, doc
    monkeypatch.setattr(provider, "route", lying)
    assert resolve_offer({}, [API]) == (None, "authorization_server_issuer_mismatch")


@pytest.mark.parametrize("oauth", [
    {"token_url": "https://collector.example.net/token"},
    {"authorize_url": f"https://{AUTH}/authorize",
     "token_url": "https://collector.example.net/token", "client_id": "c"},
    {"issuer": "https://collector.example.net"},
    {"registration_url": "https://collector.example.net/register"},
    {"token_endpoint": "https://collector.example.net/token"},
])
def test_a_requester_cannot_name_sign_in_endpoints(provider, universes, oauth):
    """Round 1 BLOCK: a remixed or injected ask pairing the real sign-in page with
    its own token endpoint would collect the code, the verifier and every
    refresh token. Endpoints come ONLY from discovery on the connection host."""
    with _as(OWNER):
        refused = _ask(action={**TASKS_ASK, "oauth": {**oauth, "scopes": ["tasks.write"]}})
    assert refused["error"] == "request_invalid"
    assert "never supplied" in refused["detail"]
    with pytest.raises(ValueError, match="client secret"):
        from tinyassets.connection_oauth.discovery import validate_request

        validate_request({"client_secret": "x"})


def test_discovery_via_the_connection_host_still_works_with_a_client_id(provider):
    from tinyassets.connection_oauth.discovery import resolve_offer, validate_request

    requested = validate_request({"scopes": "tasks.write", "client_id": "public-client"})
    offer, reason = resolve_offer(requested, [API])
    assert reason == "" and offer["source"] == "discovered"
    assert offer["token_url"] == f"https://{TOKEN}/token"  # from discovery, not the ask
    assert offer["client_id"] == "public-client" and offer["registration_url"] == ""


# --------------------------------------------------------------------------- #
# 2. Preference: OAuth primary when offered, key paste when it is not.
# --------------------------------------------------------------------------- #


def test_oauth_is_the_primary_action_when_the_provider_offers_it(provider, universes):
    with _as(OWNER):
        asked = _ask()
        assert asked["primary"] == "sign_in"
        assert asked["action"]["oauth"]["authorize_url"] == f"https://{AUTH}/authorize"
        assert "Sign in at auth.tasklark.io" in asked["grant_sentence"]
        # Every host the sign-in talks to is shown, not only where the owner clicks.
        assert "Tokens come from tokens.tasklark.io" in asked["grant_sentence"]
        assert "paste a key instead" in asked["grant_sentence"]
        # With sign-in on offer, the ask needs no key field at all.
        bare = _ask(fields=[], action={**TASKS_ASK, "destination": "tasklark-2"})
        assert bare["primary"] == "sign_in" and bare["fields"] == []
        # And that ask cannot be "accepted" with nothing: it is completed by signing in.
        from tests.owner_answer import answer_request

        refused = answer_request(universe_id=UID, payload=json.dumps(
            {"request_id": bare["request_id"], "values": {}}))
        assert refused["error"] == "request_invalid" and "signing in" in refused["detail"]


def test_key_paste_when_the_provider_offers_no_oauth(provider, universes):
    provider.advertise = False
    with _as(OWNER):
        asked = _ask()
        assert "oauth" not in asked["action"] and "primary" not in asked
        assert asked["oauth_unavailable"] == "no_authorization_server_metadata"
        # Without an offer, a key field is required: nothing to sign in with.
        bare = _ask(fields=[], action={**TASKS_ASK, "destination": "tasklark-2"})
        assert bare["error"] == "request_invalid"
        assert "no sign-in is offered" in bare["detail"]


def test_an_agent_cannot_forge_a_discovered_offer(universes):
    with _as(OWNER):
        forged = _ask(action={**TASKS_ASK, "oauth": {
            "authorize_url": "https://x.example/a", "token_url": "https://x.example/t",
            "client_id": "c", "source": "discovered"}})
    assert forged["error"] == "request_invalid" and "never supplied" in forged["detail"]
    with _as(OWNER):
        forged = _ask(action={**TASKS_ASK, "oauth": {"source": "discovered"}})
    assert forged["error"] == "request_invalid" and "unknown fields" in forged["detail"]


# --------------------------------------------------------------------------- #
# 3. The PKCE round trip through the real callback.
# --------------------------------------------------------------------------- #


def test_sign_in_round_trip_through_the_real_callback(provider, app, tmp_path):
    from tinyassets.auth.middleware import _auth_challenge_path

    # The fixed callback is a public shell; exchange stays behind the bearer.
    assert _auth_challenge_path("/app/model-callback/connect") is False
    assert _auth_challenge_path("/app/model-connect/oauth_exchange") is True

    with _as(OWNER):
        asked = _ask()
    begun, back, done = _sign_in(provider, asked["request_id"])
    authorize = dict(parse_qsl(urlsplit(begun.json()["authorize_url"]).query))
    assert authorize["redirect_uri"] == REDIRECT == f"https://{back.netloc}{back.path}"
    assert authorize["scope"] == "tasks.write"
    assert authorize["code_challenge"] == CHALLENGE
    assert len(provider.clients) == 1  # a public client, registered dynamically

    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "answered" and body["signed_in"] is True
    for token in list(provider.access) + list(provider.refresh_live):
        assert token not in done.text  # no token ever crosses back to the app

    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    connection_id, grant_id = _ids(universe_id=UID, destination="tasklark")
    resource = ConnectionLedger(app / "outbound.db")._get_connection_resource(connection_id)
    assert resource.auth_scheme == "oauth2"
    bundle = _vault_bundle(app)
    assert bundle.token_url == f"https://{TOKEN}/token"
    assert bundle.refresh_token in provider.refresh_live

    # The code is spent: replaying the exchange cannot redeem it again.
    q = dict(parse_qsl(back.query))
    with _as(OWNER):
        replay = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                          "code_verifier": VERIFIER})
    assert replay.status_code == 404

    # And the connection works: the broker sends the access token.
    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    assert _call(dispatch, grant_id)["status"] == 200
    assert provider.api_calls[-1] == bundle.access_token


def test_a_wrong_verifier_redeems_nothing(provider, app):
    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                      "code_challenge": CHALLENGE})
        q = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))
        wrong = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                         "code_verifier": "w" * 43})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_pkce_verifier"
    assert provider.access == {}  # the provider was never asked


# --------------------------------------------------------------------------- #
# 4. Refresh before expiry and on 401; rotation persisted; single flight.
# --------------------------------------------------------------------------- #


def _age(base, *, expires_at):
    """Move the stored access token's expiry (the vault's own atomic write)."""
    from dataclasses import replace

    from tinyassets.connection_oauth.tokens import ConnectionTokens

    keeper = ConnectionTokens(universe_dir=base / UID, owner_user_id=OWNER)
    keeper._write("tasklark", replace(_vault_bundle(base), expires_at=expires_at))


def test_refresh_before_expiry_persists_the_rotated_token(provider, app, tmp_path):
    _done, grant_id = _connected(provider, app, tmp_path)
    before = _vault_bundle(app)
    _age(app, expires_at=time.time() + 10)  # inside the refresh window

    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    assert _call(dispatch, grant_id)["status"] == 200
    after = _vault_bundle(app)
    assert provider.refresh_calls == 1
    assert provider.api_calls == [after.access_token]  # refreshed BEFORE sending
    assert after.access_token != before.access_token
    # Rotation persisted: the old refresh token is spent, the new one stored.
    assert before.refresh_token in provider.refresh_spent
    assert after.refresh_token in provider.refresh_live
    assert after.expires_at and after.expires_at > time.time() + 3000

    # The NEXT refresh spends the rotated token, not the old one.
    _age(app, expires_at=time.time() + 10)
    assert _call(dispatch, grant_id)["status"] == 200
    assert provider.refresh_calls == 2 and provider.reused == 0


def test_refresh_on_401_then_one_retry(provider, app, tmp_path):
    _done, grant_id = _connected(provider, app, tmp_path)
    stale = _vault_bundle(app).access_token
    provider.revoke_access()  # the service stops honouring it before its expiry

    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    result = _call(dispatch, grant_id)
    assert result["status"] == 200
    fresh = _vault_bundle(app).access_token
    assert provider.api_calls == [stale, fresh]  # 401, refresh, one retry
    assert provider.refresh_calls == 1


def test_concurrent_calls_refresh_once_and_never_reuse_a_refresh_token(
        provider, app, tmp_path):
    _done, grant_id = _connected(provider, app, tmp_path)
    provider.refresh_delay = 0.3  # widen the race window at the token endpoint
    _age(app, expires_at=time.time() + 5)

    # Separate broker instances, like separate broker children.
    brokers = [_broker(app, UID, OWNER, grant_id, tmp_path / f"rt{i}") for i in range(6)]
    results: list = [None] * len(brokers)
    start = threading.Barrier(len(brokers))

    def run(i):
        start.wait()
        results[i] = _call(brokers[i], grant_id)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(brokers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [r["status"] for r in results] == [200] * len(brokers)
    assert provider.refresh_calls == 1 and provider.reused == 0
    assert set(provider.api_calls) == {_vault_bundle(app).access_token}


def test_a_failed_refresh_is_a_connection_auth_failure_record(provider, app, tmp_path):
    from tinyassets.storage.outbound_connections import ConnectionAuthorizationError

    _done, grant_id = _connected(provider, app, tmp_path)
    provider.refresh_live.clear()  # the provider revoked the grant
    _age(app, expires_at=time.time() + 5)
    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    with pytest.raises(ConnectionAuthorizationError) as caught:
        _call(dispatch, grant_id)
    failure = caught.value.failure
    assert failure["stage"] == "connection" and failure["class"] == "auth"
    assert failure["provider_detail"] == (
        "HTTP 400: invalid_grant - refresh token already used")
    assert provider.api_calls == []  # nothing was sent on a dead authorization


def test_the_failure_record_crosses_the_proxy_boundary_and_maps_to_auth():
    from tinyassets.exceptions import ProviderAuthenticationError
    from tinyassets.storage.outbound_connections import (
        ConnectionAuthorizationError,
        _ProxyChannel,
    )

    class _Wire:
        def __init__(self):
            self.sent = []

        def send_bytes(self, payload):
            self.sent.append(payload)

        def recv_bytes(self, _limit):
            return json.dumps({"ok": False, "error_type": "ConnectionAuthorizationError",
                               "message": "x", "failure": {
                                   "stage": "connection", "class": "auth",
                                   "provider_detail": "HTTP 400: invalid_grant"}}).encode()

    channel = _ProxyChannel(_Wire(), process=None)
    with pytest.raises(ConnectionAuthorizationError) as caught:
        channel.request("POST", {"url": "https://api.example.net/x"})
    assert caught.value.failure == {"stage": "connection", "class": "auth",
                                    "provider_detail": "HTTP 400: invalid_grant"}
    assert ProviderAuthenticationError.failure_class == "auth_invalid"


# --------------------------------------------------------------------------- #
# 5. Custody: tokens never cross MCP, and no other universe can use them.
# --------------------------------------------------------------------------- #


def test_oauth2_cannot_be_pasted_and_a_bundle_is_never_sent_as_a_key(universes):
    from tinyassets.api.http_connection import connect_http
    from tinyassets.connection_oauth.tokens import TokenBundle, encode
    from tinyassets.storage.outbound_connections import (
        SsrfValidationError,
        _build_http_secret_bundle,
    )

    bundle = encode(TokenBundle(access_token="at-1", token_url="https://a.example.net/t",
                                client_id="c", refresh_token="rt-1"))
    with _as(OWNER):
        pasted = connect_http(universe_id=UID, payload=json.dumps({
            "destination": "x1", "secret": bundle, "auth_scheme": "oauth2",
            "allowed_endpoints": [{"host": API, "path_template": "/v1/tasks",
                                   "methods": ["POST"]}]}))
        assert pasted["error"] == "unsupported_auth_scheme"
        smuggled = connect_http(universe_id=UID, payload=json.dumps({
            "destination": "x2", "secret": bundle, "auth_scheme": "bearer",
            "allowed_endpoints": [{"host": API, "path_template": "/v1/tasks",
                                   "methods": ["POST"]}]}))
        assert smuggled["error"] == "connection_setup_invalid"
    for scheme in ("bearer", "header", "basic", "oauth2"):
        with pytest.raises(SsrfValidationError):
            _build_http_secret_bundle(scheme, bundle)


def test_an_oauth_connection_is_never_usable_by_another_universe(provider, app, tmp_path):
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError

    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                      "code_challenge": CHALLENGE})
    q = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))

    with _as(OTHER):
        # Another owner can neither start a sign-in on this request...
        assert _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE}).status_code == 404
        # ...nor redeem this owner's returned code, even with the right verifier.
        stolen = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                          "code_verifier": VERIFIER})
        assert stolen.status_code == 404
    assert provider.access == {}

    with _as(OWNER):
        done = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                        "code_verifier": VERIFIER})
    assert done.status_code == 200, done.text
    connection_id, grant_id = _ids(universe_id=UID, destination="tasklark")

    # The other owner cannot open a proxy on the grant...
    other_ledger = ConnectionLedger(app / "outbound.db",
                                    verify_authenticated_principal=lambda: OTHER)
    with pytest.raises(GrantResolutionError):
        other_ledger.resolve_exact_scoped_proxy(universe_id=OTHER_UID, grant_id=grant_id,
                                                connection_id=connection_id)
    # ...and a broker for the other universe finds no credential for it: the
    # tokens live only in the owner's universe vault.
    from tinyassets.storage.outbound_connections import ProxyRequestError

    foreign = _broker(app, OTHER_UID, OTHER, grant_id, tmp_path / "foreign")
    with pytest.raises(ProxyRequestError):
        _call(foreign, grant_id)
    assert provider.api_calls == []


# --------------------------------------------------------------------------- #
# 6. The rail: sign-in is the primary action (executes the shipped JS).
# --------------------------------------------------------------------------- #


def _run_rail(steps):
    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    source = html[html.index("  const ConnectOAuth={"):
                  html.index("  // End connect OAuth controller.")]
    program = r"""
class El{constructor(tag){this.tag=tag;this.children=[];this.classList={_s:new Set(),
 contains:c=>this.classList._s.has(c)};this.hidden=false;this.style={};this.textContent='';
 this.parentNode=null;}
 set className(v){this.classList._s=new Set(v.split(' '));this._cls=v;}
 get className(){return this._cls;}
 appendChild(c){if(c.parentNode)c.parentNode.children=c.parentNode.children.filter(x=>x!==c);
  c.parentNode=this;this.children.push(c);return c;}
 insertBefore(c,ref){
  if(c.parentNode)c.parentNode.children=c.parentNode.children.filter(x=>x!==c);
  c.parentNode=this;const i=ref?this.children.indexOf(ref):-1;
  if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c;}
 get nextSibling(){const p=this.parentNode;if(!p)return null;const i=p.children.indexOf(this);
  return p.children[i+1]||null;}
 addEventListener(e,h){this['on'+e]=h;}
 querySelector(sel){const all=[];const walk=n=>{n.children.forEach(c=>{all.push(c);walk(c);});};
  walk(this);if(sel==='textarea')return all.find(c=>c.tag==='textarea')||null;
  if(sel==='.btn--primary')return all.find(c=>c.classList.contains('btn--primary'))||null;
  return null;}
}
const document={createElement:t=>new El(t)};
const storage=new Map(),navigations=[],posts=[];
const sessionStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),
 removeItem:k=>storage.delete(k)};
const window={location:{pathname:'/app',search:'',assign:u=>navigations.push(u)}};
const history={replaceState:(a,b,u)=>{window.location.pathname=u;window.location.search='';}};
const NATIVE=false,token=()=>'t',authHeaders=()=>({}),ensureFreshToken=async()=>{};
const randToken=()=> 'v'.repeat(43),challengeFor=async()=> 'c'.repeat(43);
const turns=[];
const appendMessage=()=>{},refreshRail=async()=>{},frameTitle=r=>r.title;
const sendTurn=(...args)=>turns.push(args);
let historyLoaded=true;
const fetch=async(url,o)=>{posts.push({url,body:JSON.parse(o.body)});
 return {ok:true,json:async()=>({flow:'f'.repeat(43),expires_in:600,
  authorize_url:'https://auth.tasklark.io/authorize?state='+'f'.repeat(43)})};};
const setTimeout=()=>{};
__SOURCE__
function body(fields){const box=new El('div');
 fields.forEach(f=>{const l=new El('label');l.className='rtab-label';
  l.appendChild(new El(f));box.appendChild(l);});
 const fb=new El('label');fb.className='rtab-label';fb.appendChild(new El('input'));
 box.appendChild(fb);
 const row=new El('div');row.className='rtab-row';const acc=new El('button');
 acc.className='btn btn--primary';
 row.appendChild(acc);box.appendChild(row);const note=new El('div');box.appendChild(note);
 return {box,row,note,acc};}
(async()=>{__STEPS__})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    node = shutil.which("node")
    assert node, "Node is required to execute browser tests"
    result = subprocess.run([node, "-e", program.replace("__SOURCE__", source)
                             .replace("__STEPS__", steps)],
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_rail_puts_sign_in_first_and_folds_the_key_under_it():
    out = _run_rail("""
const req={request_id:'r1',title:'Connect tasklark',action:{type:'connect',
  oauth:{authorize_url:'https://auth.tasklark.io/authorize'}}};
const b=body(['textarea']);ConnectOAuth.decorate(req,b.box,b.row,b.note);
const first=b.box.children[0];
await first.onclick();
const plain=body(['textarea']);
ConnectOAuth.decorate({request_id:'r2',action:{type:'connect'}},plain.box,plain.row,plain.note);
const only=body([]);ConnectOAuth.decorate(req,only.box,only.row,only.note);
console.log(JSON.stringify({first:first.textContent,folded:b.box.children[1].tag,
 foldedHoldsKey:!!b.box.children[1].querySelector('textarea'),navigations,posts,
 saved:JSON.parse(storage.get('ta_connect_oauth')),plainFirst:plain.box.children[0].tag,
 onlyAcceptHidden:only.acc.hidden,keyAcceptHidden:b.acc.hidden}));
""")
    assert out["first"] == "Sign in with auth.tasklark.io"
    assert out["folded"] == "details" and out["foldedHoldsKey"] is True
    assert out["posts"] == [{"url": "/app/model-connect/oauth_begin",
                             "body": {"request_id": "r1", "code_challenge": "c" * 43}}]
    assert out["navigations"][0].startswith("https://auth.tasklark.io/authorize")
    assert out["saved"]["flow"] == "f" * 43 and out["saved"]["verifier"] == "v" * 43
    assert out["plainFirst"] == "label"  # no offer: the key ask is untouched
    assert out["onlyAcceptHidden"] is True and out["keyAcceptHidden"] is False


@pytest.mark.parametrize("origin,source,turn_count", [("platform", True, 0), ("agent", False, 1)])
def test_rail_sign_in_uses_server_provenance_for_relay(origin, source, turn_count):
    out = _run_rail("""
const req={request_id:'r1',title:'Connect source',origin:__ORIGIN__,action:{type:'connect',
  oauth:{authorize_url:'https://auth.tasklark.io/authorize'}}};
const b=body([]);ConnectOAuth.decorate(req,b.box,b.row,b.note);
await b.box.children[1].onclick();
const saved=JSON.parse(storage.get('ta_connect_oauth'));
window.location.pathname='/app/model-callback/connect';
window.location.search='?code=abc&state='+saved.flow;
ConnectOAuth.pending=ConnectOAuth.takeCallback();
ConnectOAuth.post=async()=>({status:'answered',signed_in:true});
await ConnectOAuth.finish();
console.log(JSON.stringify({saved,turns}));
""".replace("__ORIGIN__", json.dumps(origin)))
    assert out["saved"]["source"] is source
    assert len(out["turns"]) == turn_count


def test_the_callback_is_taken_before_account_sign_in_and_only_once():
    out = _run_rail("""
storage.set('ta_connect_oauth',JSON.stringify({verifier:'v'.repeat(43),flow:'f'.repeat(43),
  title:'Connect tasklark',expires:Date.now()+60000}));
window.location.pathname='/app/model-callback/connect';
window.location.search='?code=abc&state='+'f'.repeat(43);
const taken=ConnectOAuth.takeCallback();
const again=ConnectOAuth.takeCallback();
console.log(JSON.stringify({taken,again,path:window.location.pathname,search:window.location.search,
  left:storage.has('ta_connect_oauth')}));
""")
    assert out["taken"] == {"flow": "f" * 43, "code": "abc", "code_verifier": "v" * 43,
                            "title": "Connect tasklark"}
    assert out["again"] is None  # the URL is stripped; nothing is left to redeem
    assert out["path"] == "/app" and out["search"] == "" and out["left"] is False


def test_no_vendor_names_in_the_oauth_code():
    root = Path(__file__).resolve().parents[1] / "tinyassets" / "connection_oauth"
    files = sorted(root.glob("*.py"))
    assert len(files) >= 5, "the OAuth package must exist for this check to mean anything"
    text = "\n".join(p.read_text(encoding="utf-8").lower() for p in files)
    for name in ("openrouter", "github", "google", "openai", "anthropic", "tasklark"):
        assert name not in text


# --------------------------------------------------------------------------- #
# Round 1 floor: the token URL is pinned, RFC 9207 iss, and lock contention.
# --------------------------------------------------------------------------- #


def test_the_stored_token_url_is_pinned_to_the_discovered_one(provider, app):
    from tinyassets.api.pending_requests import answer_connect_with_token
    from tinyassets.connection_oauth.tokens import TokenBundle, encode

    with _as(OWNER):
        asked = _ask()
        elsewhere = encode(TokenBundle(
            access_token="at-x", refresh_token="rt-x", client_id="c",
            token_url="https://collector.example.net/token"))
        refused = answer_connect_with_token(universe_id=UID, request_id=asked["request_id"],
                                            token=elsewhere, owner_session={"test": "owner"})
    assert refused["error"] == "request_invalid"
    assert "token endpoint" in refused["detail"]
    from tinyassets.credential_vault import load_credential_vault

    assert not [r for r in load_credential_vault(app / UID) if r.get("destination") == "tasklark"]

    _begun, _back, done = _sign_in(provider, asked["request_id"])
    assert done.status_code == 200, done.text
    assert _vault_bundle(app).token_url == asked["action"]["oauth"]["token_url"]


def test_rfc9207_issuer_is_checked_when_the_server_supports_it(provider, app):
    provider.iss = True
    with _as(OWNER):
        asked = _ask()
    assert asked["action"]["oauth"]["iss_parameter_supported"] is True

    def attempt(**extra):
        with _as(OWNER):
            begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                          "code_challenge": CHALLENGE})
            q = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))
            payload = {"flow": q["state"], "code": q["code"], "code_verifier": VERIFIER}
            payload.update({k: v for k, v in extra.items() if v is not None})
            if "iss" not in extra:
                payload["iss"] = q["iss"]
            return _post("oauth_exchange", payload)

    wrong = attempt(iss="https://collector.example.net")
    assert wrong.status_code == 409 and wrong.json()["error"] == "issuer_mismatch"
    missing = attempt(iss=None)
    assert missing.status_code == 409 and missing.json()["error"] == "issuer_missing"
    assert provider.access == {}  # no code was redeemed for either
    right = attempt()
    assert right.status_code == 200, right.text


def test_lock_contention_during_refresh_never_loses_the_rotated_token(
        provider, app, tmp_path, monkeypatch):
    """Windows gives up on the cross-process vault lock after about a second.
    A refresh that spent the single-use refresh token and only THEN failed to
    take that lock would lose the rotated token; the lock is taken first."""
    from contextlib import contextmanager

    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    _done, grant_id = _connected(provider, app, tmp_path)
    before = _vault_bundle(app)
    _age(app, expires_at=time.time() + 5)

    real = ProviderAssignmentAdmission._file_lock
    busy = {"left": 3}

    @contextmanager
    def contended(universe_dir, *, exclusive):
        if exclusive and busy["left"] > 0:
            busy["left"] -= 1
            raise TimeoutError("provider assignment admission lock remained unavailable")
        with real(universe_dir, exclusive=exclusive):
            yield

    monkeypatch.setattr(ProviderAssignmentAdmission, "_file_lock", staticmethod(contended))
    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    assert _call(dispatch, grant_id)["status"] == 200
    after = _vault_bundle(app)
    assert busy["left"] == 0 and provider.refresh_calls == 1 and provider.reused == 0
    assert before.refresh_token in provider.refresh_spent
    assert after.refresh_token in provider.refresh_live  # the rotated token was kept


@pytest.mark.parametrize("cookie", ["", "__Host-ta-owner=forged"])
def test_bearer_cannot_start_owner_bound_oauth(provider, app, cookie):
    from tinyassets.connection_oauth import pkce

    with _as(OWNER):
        asked = _ask()
        refused = _post("oauth_begin", {"request_id": asked["request_id"],
                                       "code_challenge": CHALLENGE}, cookie=cookie)
    assert refused.status_code == 403
    assert refused.json()["error"] == "interactive_approval_required"
    with pkce.flows_db(app) as (conn, _):
        assert conn.execute("SELECT COUNT(*) FROM connection_oauth_flows").fetchone()[0] == 0
    assert provider.access == {}


def test_unbound_legacy_oauth_flow_cannot_be_upgraded_at_completion(provider, app):
    from tests.owner_answer import session_cookie
    from tinyassets.connection_oauth import pkce

    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE}).json()
        back = dict(parse_qsl(urlsplit(provider.authorize(begun["authorize_url"])).query))
        with pkce.flows_db(app) as (conn, _):
            conn.execute("UPDATE connection_oauth_flows SET approved_owner=''")
        for cookie in ("", session_cookie()):
            result = _post("oauth_exchange", {"flow": back["state"], "code": back["code"],
                                             "code_verifier": VERIFIER}, cookie=cookie)
            assert result.status_code == 403
            assert result.json()["error"] == "interactive_approval_required"
    assert provider.access == {}
