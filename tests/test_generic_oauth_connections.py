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
                payload = b"" if status == 202 else json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in getattr(provider, "response_headers", {}).items():
                    self.send_header(key, value)
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
            if method == "GET":  # unauthenticated discovery probe, not a task call
                return 401, {"error": "invalid_token"}
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
    from tinyassets.connection_oauth import model_discovery

    def probe(url, **kwargs):
        parts = urlsplit(url)
        response = httpx.get(f"http://127.0.0.1:{fake.port}" + parts.path,
                             headers={"Host": parts.hostname}, trust_env=False)
        return response.status_code, response.json(), dict(response.headers)

    monkeypatch.setattr(model_discovery, "transport_fetch", probe)
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
    from tests.support.broker_double import seed_owner_admission

    for owner, uid in ((OWNER, UID), (OTHER, OTHER_UID)):
        seed_owner_admission(base, principal=owner, center=uid)
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
    if operation == "oauth_begin" and "request_id" in data:
        data = {**data}
        data["request_ref"] = data.pop("request_id")
        from tinyassets.api.helpers import _universe_dir
        from tinyassets.storage.pending_requests import get_request

        row = get_request(_universe_dir(home), data["request_ref"])
        action = row["action"] if row else TASKS_ASK
        offer = action.get("oauth") or {}
        data["definition"] = {"destination": action["destination"],
            "endpoints": action.get("endpoints") or [
                {"host": action["host"], "path_template": action["path_template"],
                 "methods": action["methods"]}],
            "oauth": {k: v for k, v in offer.items()
                      if k in {"scopes", "client_id", "use"}}}
        if offer.get("registration_method") == "existing" and offer.get("client_id"):
            data["definition"]["oauth"]["client_id"] = offer["client_id"]
    from tests.owner_answer import session_cookie
    from tinyassets import onboarding

    if operation in {"oauth_begin", "source_sign_in"}:
        from tinyassets.api.helpers import _base_path
        from tinyassets.auth.middleware import current_identity
        from tinyassets.connection_oauth.flow import FlowError
        from tinyassets.onboarding.connection_definition import oauth_definition, sign_in_definition

        try:
            definition = (sign_in_definition(data["preset_id"], RESOURCE)
                          if operation == "source_sign_in" else data["definition"])
            prepared = oauth_definition(_base_path(), owner=current_identity().user_id,
                center=home, definition=definition, public_resource=RESOURCE)
        except FlowError as exc:
            return httpx.Response(exc.status, json={"error": exc.code})
        data = {"code_challenge": data["code_challenge"], "prepared": prepared,
                **({"request_ref": data["request_ref"]} if "request_ref" in data else {})}
        operation = "oauth_begin"

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io",
        ) as client:
            proof = (session_cookie() if operation in {"oauth_begin", "source_sign_in"} else "")
            return await client.post("/app/model-connect/" + operation,
                                     json={k: v for k, v in data.items() if k != "center"},
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
    monkeypatch.setattr(
        onboarding, "app_config",
        lambda build=None: {**real(build=build), "resource": RESOURCE},
    )
    homes = {OWNER: UID, OTHER: OTHER_UID}
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity, **kw: homes.get(identity.user_id, ""))
    return universes


def _sign_in(provider, request_id, *, owner=OWNER):
    """Tap Sign in, approve at the provider, come back: the whole owner gesture."""
    with _as(owner):
        begun = _post("oauth_begin", {"request_id": request_id, "code_challenge": CHALLENGE},
                      home=UID if owner == OWNER else OTHER_UID)
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
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: owner)
    grant = ledger.require_active_grant(grant_id)

    def dispatch(request_grant, verb, request):
        assert request_grant == grant_id
        proxy = ledger.resolve_exact_scoped_proxy(
             grant_id=grant_id, connection_id=grant.connection_id)
        try:
            return proxy.request(verb, request)
        finally:
            proxy.close()

    return dispatch


def _call(dispatch, grant_id):
    return dispatch(grant_id, "POST", {"url": f"https://{API}/v1/tasks", "body": {"t": 1}})


def _vault_bundle(base, uid=UID):
    from tinyassets.connection_oauth.tokens import decode
    from tinyassets.credential_vault import custody_namespace, load_credential_vault

    namespace = custody_namespace(base, OWNER if uid == UID else OTHER)
    records = [r for r in load_credential_vault(namespace)
               if r.get("destination") == "tasklark"]
    assert len(records) == 1
    return decode(records[0]["token"])


def _connected(provider, app, tmp_path):
    with _as(OWNER):
        asked = _ask()
    assert asked.get("primary") == "connect", asked
    _begun, _back, done = _sign_in(provider, asked["request_id"])
    assert done.status_code == 200, done.text
    from tinyassets.api.http_connection import _ids

    _cid, grant_id = _ids(owner=OWNER, destination="tasklark")
    return done, grant_id


# --------------------------------------------------------------------------- #
# 1. Discovery from a fake authorization server.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 2. Preference: OAuth primary when offered, key paste when it is not.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 3. The PKCE round trip through the real callback.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 4. Refresh before expiry and on 401; rotation persisted; single flight.
# --------------------------------------------------------------------------- #


def _age(base, *, expires_at):
    """Move the stored access token's expiry (the vault's own atomic write)."""
    from dataclasses import replace

    from tinyassets.broker.tokens import ConnectionTokens
    from tinyassets.credential_vault import custody_namespace
    keeper = ConnectionTokens(universe_dir=custody_namespace(base, OWNER), owner_user_id=OWNER)
    keeper._write("tasklark", replace(_vault_bundle(base), expires_at=expires_at))


@pytest.mark.verifies('owner-daemon-in-box:T6')
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


# --------------------------------------------------------------------------- #
# 5. Custody: tokens never cross MCP, and no other universe can use them.
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_an_oauth_connection_is_never_usable_by_another_owner(provider, app, tmp_path):
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError

    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                      "code_challenge": CHALLENGE})
    q = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))

    with _as(OTHER):
        # A request reference is correlation, never authority. Another owner
        # can start their own flow for the same public connection definition.
        own = _post("oauth_begin", {"request_id": asked["request_id"],
                                    "code_challenge": CHALLENGE}, home=OTHER_UID)
        assert own.status_code == 200
        assert own.json()["flow"] != begun.json()["flow"]
        # They still cannot redeem this owner's flow with the right verifier.
        stolen = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                          "code_verifier": VERIFIER})
        assert stolen.status_code == 404
    assert provider.access == {}

    with _as(OWNER):
        done = _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                        "code_verifier": VERIFIER})
    assert done.status_code == 200, done.text
    connection_id, grant_id = _ids(owner=OWNER, destination="tasklark")

    # The other owner cannot open a proxy on the grant...
    other_ledger = ConnectionLedger(app / ".broker" / "outbound.db", data_root=app,
                                    verify_authenticated_principal=lambda: OTHER)
    with pytest.raises(GrantResolutionError):
        other_ledger.resolve_exact_scoped_proxy( grant_id=grant_id,
                                                connection_id=connection_id)
    # The shared broker channel also refuses the foreign grant before dispatch.
    foreign = _broker(app, OTHER_UID, OTHER, grant_id, tmp_path / "foreign")
    with pytest.raises(GrantResolutionError, match="outbound connection grant identity mismatch"):
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
const MCP={_loginEpoch:0,_assertLogin:epoch=>{if(epoch!==0)throw Error('Account changed');}};
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


# --------------------------------------------------------------------------- #
# Round 1 floor: the token URL is pinned, RFC 9207 iss, and lock contention.
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_rfc9207_issuer_is_checked_when_the_server_supports_it(provider, app):
    provider.iss = True
    with _as(OWNER):
        asked = _ask()
    assert "iss_parameter_supported" not in asked["action"]["oauth"]

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


@pytest.mark.verifies('owner-daemon-in-box:T2')
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


@pytest.mark.verifies('owner-daemon-in-box:T2')
def test_generic_exchange_refuses_cookieless_replay_and_foreign_handle(provider, app):
    from tinyassets.connection_oauth import pkce

    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE}).json()
        back = dict(parse_qsl(urlsplit(provider.authorize(begun["authorize_url"])).query))
    payload = {"flow": back["state"], "code": back["code"], "code_verifier": VERIFIER}
    with _as(OTHER):
        refused = _post("oauth_exchange", payload, cookie="")
    assert refused.status_code == 404
    assert refused.json()["error"] == "unknown_sign_in"
    assert provider.access == {}
    with pkce.flows_db(app) as (conn, _):
        assert conn.execute("SELECT COUNT(*) FROM connection_oauth_flows").fetchone()[0] == 1
    with _as(OWNER):
        assert _post("oauth_exchange", payload, cookie="").status_code == 200
        issued = dict(provider.access)
        replay = _post("oauth_exchange", payload, cookie="")
    assert replay.status_code == 404
    assert replay.json()["error"] == "unknown_sign_in"
    assert provider.access == issued


@pytest.mark.verifies('owner-daemon-in-box:T2')
def test_owner_revocation_prevents_outstanding_generic_exchange(provider, app):
    from tinyassets.onboarding.owner_sessions import revoke_owner

    with _as(OWNER):
        asked = _ask()
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE}).json()
        back = dict(parse_qsl(urlsplit(provider.authorize(begun["authorize_url"])).query))
        revoke_owner(OWNER)
        result = _post("oauth_exchange", {"flow": back["state"], "code": back["code"],
                                         "code_verifier": VERIFIER}, cookie="")
    assert result.status_code == 404
    assert provider.access == {}

from tests.support.egress import admitted_egress  # noqa: E402, F401
