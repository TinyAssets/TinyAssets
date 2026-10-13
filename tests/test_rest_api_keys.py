"""REST API keys are confined outside clients; the REST door takes nothing else."""
import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.test_inline_owner_sessions import setup as owner_setup  # noqa: F401
from tinyassets.api_keys import ApiKeyStore
from tinyassets.outside_authority import OutsideClientAuthority


def scope(capability, universe="home", agent="main"):
    return {"universe": universe, "agent": agent, "capability": capability}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    # The data dir by env, never by patching ``_base_path``: a module first
    # imported under such a patch keeps the stale function for the session.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("tinyassets.daemon_server.get_founder_home", lambda *a: "home")
    keys, authority = ApiKeyStore(tmp_path), OutsideClientAuthority(tmp_path)
    monkeypatch.setattr("tinyassets.api_keys.current_store", lambda: keys)
    monkeypatch.setattr("tinyassets.outside_authority.current_store", lambda: authority)
    authority.set_enabled(True)
    return keys, authority


@pytest.fixture
def client(stores, monkeypatch):
    from tinyassets.auth.middleware import AuthContextMiddleware
    from tinyassets.rest_api import rest_api_routes

    calls = []

    def fake_read(**arguments):
        from tinyassets.auth.middleware import current_identity

        identity = current_identity()
        calls.append(("read_graph", arguments, identity.user_id,
                      identity.metadata.get("outside_resource")))
        return json.dumps({"target": arguments["target"], "graph_id": arguments["graph_id"]})

    def fake_run(**arguments):
        calls.append(("run_graph", arguments))
        return json.dumps({"run_id": "r1", "status": "queued"})

    # raising=True (the default): a route whose handle function is missing or
    # renamed fails here instead of passing against a stub of nothing.
    monkeypatch.setattr("tinyassets.universe_server.read_graph", fake_read)
    monkeypatch.setattr("tinyassets.universe_server.run_graph", fake_run)
    app = AuthContextMiddleware(Starlette(routes=rest_api_routes()))
    return TestClient(app), calls


def mint(keys, authority, *scopes, owner="alice"):
    return keys.mint(owner, name="ci bot", scopes=list(scopes), authority=authority)["key"]


def test_secret_is_shown_once_stored_hashed_and_never_listed(stores):
    keys, authority = stores
    minted = keys.mint("alice", name="bot", scopes=[scope("read_graph")], authority=authority)
    assert minted["key"].startswith("ta_key_")
    from tinyassets.api_keys import _KEY_RE

    secret = _KEY_RE.match(minted["key"]).group(2)  # the secret's alphabet includes "_"
    assert secret.encode() not in keys.path.read_bytes()
    listed = keys.list("alice", authority=authority)["keys"]
    assert [k["key_id"] for k in listed] == [minted["key_id"]]
    assert "key" not in listed[0] and secret not in json.dumps(listed)
    assert listed[0]["scopes"] == [scope("read_graph")]
    assert keys.list("bob", authority=authority) == {"keys": []}


@pytest.mark.parametrize("bad", [[], [scope("write_graph")], [scope("*")],
                                 [{"universe": "home", "capability": "read_graph"}]])
def test_mint_refuses_unknown_or_wildcard_scopes(stores, bad):
    keys, authority = stores
    with pytest.raises(ValueError):
        keys.mint("alice", name="bot", scopes=bad, authority=authority)
    assert keys.list("alice", authority=authority) == {"keys": []}


def test_reads_run_as_owner_confined_to_the_granted_universe(client, stores):
    http, calls = client
    key = mint(*stores, scope("read_graph"))
    auth = {"authorization": f"Bearer {key}"}
    response = http.get("/api/command-centers/home/status", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"target": "status", "graph_id": "home"}
    assert calls[-1][2:] == ("alice", scope("read_graph"))
    assert http.get("/api/command-centers/home/connections", headers=auth).status_code == 200
    assert http.get("/api/command-centers?limit=500", headers=auth).status_code == 400
    assert http.get("/api/command-centers/home/runs?status=failed",
                    headers=auth).json()["target"] == "runs"
    refused = http.get("/api/command-centers/elsewhere/status", headers=auth)
    assert refused.status_code == 403
    assert all(call[1]["graph_id"] != "elsewhere" for call in calls)


def test_actions_need_their_own_capability(client, stores):
    http, calls = client
    reader = {"authorization": f"Bearer {mint(*stores, scope('read_graph'))}"}
    body = {"branch_def_id": "b1", "inputs": {"topic": "x"}}
    assert http.post("/api/command-centers/home/runs", headers=reader,
                     json=body).status_code == 403
    assert http.post("/api/command-centers/home/messages", headers=reader,
                     json={"message": "hi"}).status_code == 403
    assert not [c for c in calls if c[0] == "run_graph"]
    runner = {"authorization": f"Bearer {mint(*stores, scope('run_graph'))}"}
    started = http.post("/api/command-centers/home/runs", headers=runner, json=body)
    assert started.status_code == 200 and started.json()["run_id"] == "r1"
    assert calls[-1] == ("run_graph", {"branch_def_id": "b1", "inputs_json": '{"topic": "x"}',
                                       "run_name": "", "graph_id": "home"})
    assert http.post("/api/command-centers/home/runs", headers=runner,
                     json={"branch_def_id": "b1", "extra": 1}).status_code == 400


@pytest.mark.parametrize("header", [None, "Bearer ", "Bearer not-a-key",
                                    "Bearer ta_key_0123456789abcdef_" + "x" * 43,
                                    "Basic dXNlcjpwYXNz"])
def test_missing_forged_or_oauth_credentials_get_401(client, header):
    http, calls = client
    headers = {"authorization": header} if header else {}
    response = http.get("/api/command-centers/home/status", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="tinyassets-api"'
    assert calls == []


def test_revocation_and_kill_switch_close_the_door(client, stores):
    http, _ = client
    keys, authority = stores
    minted = keys.mint("alice", name="bot", scopes=[scope("read_graph")], authority=authority)
    auth = {"authorization": f"Bearer {minted['key']}"}
    path = "/api/command-centers/home/status"
    assert http.get(path, headers=auth).status_code == 200
    authority.set_enabled(False)
    assert http.get(path, headers=auth).status_code == 401
    authority.set_enabled(True)
    with pytest.raises(PermissionError):
        keys.revoke("bob", minted["key_id"], authority=authority)
    keys.revoke("alice", minted["key_id"], authority=authority)
    assert http.get(path, headers=auth).status_code == 401
    assert authority.inspect("alice")["clients"][0]["revoked"] == 1


def test_rest_rate_bounds_key_and_owner_and_resets(client, stores, monkeypatch):
    from tinyassets import api_keys, usage_policy

    monkeypatch.setattr(usage_policy, "API_KEY_REQUESTS_PER_WINDOW", 2)
    monkeypatch.setattr(usage_policy, "API_OWNER_REQUESTS_PER_WINDOW", 3)
    clock = [1200.0]
    monkeypatch.setattr(api_keys.time, "time", lambda: clock[0])
    http, calls = client
    keys, authority = stores
    path = "/api/command-centers/home/status"
    first = {"authorization": f"Bearer {mint(keys, authority, scope('read_graph'))}"}
    second = {"authorization": f"Bearer {mint(keys, authority, scope('read_graph'))}"}
    other = {"authorization": f"Bearer {mint(keys, authority, scope('read_graph'), owner='bob')}"}

    assert http.get(path, headers=first).status_code == 200
    assert http.get(path, headers=first).status_code == 200
    count = len(calls)
    limited = http.get(path, headers=first)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert len(calls) == count
    assert http.get(path, headers=second).status_code == 200
    count = len(calls)
    limited = http.get(path, headers=second)
    assert limited.status_code == 429
    assert len(calls) == count
    assert http.get(path, headers=other).status_code == 200

    clock[0] += 60
    assert http.get(path, headers=first).status_code == 200


def test_a_key_never_reaches_the_oauth_provider(stores, monkeypatch):
    from tinyassets.auth import middleware

    seen = []

    class Provider:
        def resolve_token(self, token):
            seen.append(token)

    monkeypatch.setattr(middleware, "_get_provider", lambda: Provider())
    key = mint(*stores, scope("read_graph"))
    assert middleware.auth_middleware(key) is None
    assert seen == []



@pytest.mark.parametrize("method,path", [("POST", "/mcp"), ("GET", "/mcp/pulse"),
                                         ("GET", "/app"), ("GET", "/app/me")])
def test_a_key_is_refused_on_mcp_and_app_through_the_whole_middleware(
        stores, monkeypatch, method, path):
    """Spec: a key SHALL NOT authenticate /mcp or /app -- end to end, not only the
    resolver: the handler never runs and the OAuth provider never sees the key."""
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from tinyassets.auth import middleware

    seen, ran = [], []

    class Provider:
        def resolve_token(self, token):
            seen.append(token)

    def handler(request):
        ran.append((request.url.path, middleware.current_identity_or_none()))
        return PlainTextResponse("handler ran")

    monkeypatch.setattr(middleware, "_get_provider", lambda: Provider())
    routes = [Route(p, handler, methods=["GET", "POST"])
              for p in ("/mcp", "/mcp/pulse", "/app", "/app/me")]
    http = TestClient(middleware.AuthContextMiddleware(Starlette(routes=routes)))
    key = mint(*stores, scope("read_graph"), scope("run_graph"), scope("converse"))
    response = http.request(method, path, headers={"authorization": f"Bearer {key}"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                            if method == "POST" else None)
    assert seen == []
    assert ran == [] or all(identity is None for _, identity in ran), ran
    assert response.status_code in (200, 401), response.text
    if response.status_code == 200:  # the linking challenge, never the handler
        assert response.text != "handler ran"


@pytest.mark.usefixtures("owner_setup")
def test_keys_are_managed_only_from_the_protected_owner_session(stores, monkeypatch):
    from tests.test_inline_owner_sessions import request
    from tinyassets import onboarding
    from tinyassets.onboarding.api_keys import handle

    keys, authority = stores
    document = {}

    async def read(*args, **kwargs):
        return document

    monkeypatch.setattr(onboarding, "_read_small_json", read)
    document.update(operation="create", name="bot", scopes=[scope("read_graph")])
    assert asyncio.run(handle(request(cookie=""))).status_code == 403
    assert asyncio.run(handle(request(origin="https://other.example"))).status_code == 403
    created = asyncio.run(handle(request()))
    assert created.status_code == 200
    key_id = json.loads(created.body)["key_id"]
    document["scopes"] = [scope("read_graph", universe="someone-elses")]
    assert asyncio.run(handle(request())).status_code == 403
    document.clear()
    document.update(operation="list")
    listed = json.loads(asyncio.run(handle(request())).body)["keys"]
    assert [k["key_id"] for k in listed] == [key_id]
    document.clear()
    document.update(operation="revoke", key_id=key_id)
    assert asyncio.run(handle(request())).status_code == 200
    assert keys.list("alice", authority=authority)["keys"][0]["revoked_at"] is not None


def test_message_runs_converse_with_one_live_provider_authority(client, stores, monkeypatch):
    from tinyassets.auth import middleware

    http, _ = client
    seen = {}

    def fake_converse(**arguments):
        capability = middleware.provider_request_capability()
        seen.update(arguments, capability=capability,
                    owner=middleware.current_identity().user_id)
        return json.dumps({"reply": "hello"})

    monkeypatch.setattr("tinyassets.universe_server.converse", fake_converse)
    auth = {"authorization": f"Bearer {mint(*stores, scope('converse'))}"}
    response = http.post("/api/command-centers/home/messages", headers=auth,
                         json={"message": "status?"})
    assert response.status_code == 200 and response.json() == {"reply": "hello"}
    assert seen["owner"] == "alice" and seen["graph_id"] == "home"
    assert seen["capability"].tool_name == "converse"
    with pytest.raises(PermissionError):  # revoked once the turn returns
        middleware._active_provider_request(seen["capability"])
    other_agent = http.post("/api/command-centers/home/messages", headers=auth,
                            json={"message": "x", "agent_id": "private"})
    assert other_agent.status_code == 403


def test_cancel_runs_the_handle_cancel_under_the_run_graph_grant(client, stores):
    http, calls = client
    reader = {"authorization": f"Bearer {mint(*stores, scope('read_graph'))}"}
    assert http.post("/api/command-centers/home/runs/r9/cancel",
                     headers=reader).status_code == 403
    runner = {"authorization": f"Bearer {mint(*stores, scope('run_graph'))}"}
    assert http.post("/api/command-centers/home/runs/r9/cancel",
                     headers=runner).status_code == 200
    assert calls[-1] == ("run_graph", {"operation": "cancel", "run_id": "r9", "graph_id": "home"})


@pytest.mark.parametrize(
    "raw", [b"{not json", b"[1, 2]", b'"text"', b"{" + b" " * 300000 + b"}"],
    ids=["malformed", "array", "scalar", "oversized"],
)
def test_malformed_or_oversized_bodies_are_400_never_500(client, stores, raw):
    http, calls = client
    keys, authority = stores
    key = mint(keys, authority, scope("run_graph"), scope("converse"))
    auth = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    for path in ("/api/command-centers/home/runs", "/api/command-centers/home/messages"):
        response = http.post(path, headers=auth, content=raw)
        assert response.status_code == 400, (path, response.text)
        assert response.json()["error"] == "invalid_request"
    assert not [c for c in calls if c[0] == "run_graph"]


def test_routes_call_the_exact_functions_the_mcp_handles_run():
    """Each registered MCP tool wraps exactly the function its REST route imports,
    so a gate added to the handle's function is a gate on REST too."""
    import inspect

    from tinyassets import universe_server

    for name in ("read_graph", "run_graph", "converse"):
        tool = asyncio.run(universe_server.mcp.get_tool(name))
        assert tool.fn.__wrapped__ is getattr(universe_server, name), name
    assert {"target", "graph_id", "run_id", "limit", "run_status"} <= set(
        inspect.signature(universe_server.read_graph).parameters)
    assert {"branch_def_id", "inputs_json", "run_name", "graph_id", "operation", "run_id"} <= set(
        inspect.signature(universe_server.run_graph).parameters)
    assert {"message", "graph_id", "agent_id", "input_method"} <= set(
        inspect.signature(universe_server.converse).parameters)


# --- Against the real read and run stores, no fakes ------------------------------

@pytest.fixture
def real(tmp_path, monkeypatch):
    """Three real command centers: alice owns home and work, bob owns bobs (public)."""
    from tinyassets.api import visibility
    from tinyassets.auth.middleware import AuthContextMiddleware
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
        set_founder_home,
    )
    from tinyassets.rest_api import rest_api_routes

    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
    for uid, owner in (("home", "alice"), ("work", "alice"), ("bobs", "bob")):
        udir = base / uid
        udir.mkdir()
        (udir / "soul.md").write_text(f"# {uid}\n", encoding="utf-8")
        ensure_universe_registered(base, universe_id=uid, universe_path=udir)
        grant_universe_access(base, universe_id=uid, actor_id=owner, permission="admin",
                              granted_by=owner)
        visibility.set_universe_visibility(uid, "public", source="owner")
    set_founder_home(base, founder_sub="alice", universe_id="home")
    keys, authority = ApiKeyStore(base), OutsideClientAuthority(base)
    authority.set_enabled(True)
    app = AuthContextMiddleware(Starlette(routes=rest_api_routes()))
    return base, keys, authority, TestClient(app)


def test_listing_shows_only_the_command_centers_the_key_is_granted(real):
    _, keys, authority, http = real

    def listed(*scopes):
        auth = {"authorization": f"Bearer {mint(keys, authority, *scopes)}"}
        response = http.get("/api/command-centers?limit=10", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == len(body["universes"])
        return sorted(row["id"] for row in body["universes"])

    # Sanity: the owner's own listing really holds more than any one grant.
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.universe_server import read_graph

    with identity_context(Identity("alice", "alice")):
        everything = {row["id"] for row in json.loads(read_graph(target="graphs"))["universes"]}
    assert {"home", "work"} <= everything

    assert listed(scope("read_graph", universe="work")) == ["work"]
    assert listed(scope("read_graph", universe="home")) == ["home"]
    assert listed(scope("read_graph", universe="home"),
                  scope("read_graph", universe="work")) == ["home", "work"]
    # A grant to act is not a grant to read, and public bobs never leaks in.
    assert listed(scope("run_graph", universe="work")) == []
    reader = {"authorization": f"Bearer {mint(keys, authority, scope('read_graph'))}"}
    assert http.get("/api/command-centers?limit=0", headers=reader).status_code == 400


def test_a_run_in_another_command_center_is_not_found_under_this_one(real):
    from tinyassets.runs import create_run, get_run, is_cancel_requested

    base, keys, authority, http = real
    run_id = create_run(base, branch_def_id="b", thread_id="", inputs={}, actor="alice",
                        owner_user_id="alice", queue_universe_id="work")
    home_key = {"authorization": "Bearer " + mint(
        keys, authority, scope("read_graph"), scope("run_graph"))}
    read = http.get(f"/api/command-centers/home/runs/{run_id}", headers=home_key)
    assert read.status_code == 404, read.text
    cancel = http.post(f"/api/command-centers/home/runs/{run_id}/cancel", headers=home_key)
    assert cancel.status_code == 404, cancel.text
    assert not is_cancel_requested(base, run_id)
    # Naming the run's real command center needs a grant there, which this key lacks.
    assert http.get(f"/api/command-centers/work/runs/{run_id}",
                    headers=home_key).status_code == 403
    work_key = {"authorization": "Bearer " + mint(
        keys, authority, scope("read_graph", universe="work"))}
    allowed = http.get(f"/api/command-centers/work/runs/{run_id}", headers=work_key)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["run_id"] == run_id
    assert get_run(base, run_id)["status"] != "cancelled"


def test_listing_finds_a_grant_past_the_first_hundred_command_centers(real):
    """The grant filter runs over the owner's whole listing, never a truncated page:
    the only granted command center sorts after 100 others and is still listed."""
    from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access

    base, keys, authority, http = real
    for n in range(101):
        uid = f"a{n:03d}"
        udir = base / uid
        udir.mkdir()
        (udir / "soul.md").write_text(f"# {uid}\n", encoding="utf-8")
        ensure_universe_registered(base, universe_id=uid, universe_path=udir)
        grant_universe_access(base, universe_id=uid, actor_id="alice", permission="admin",
                              granted_by="alice")
    auth = {"authorization": "Bearer " + mint(
        keys, authority, scope("read_graph", universe="work"))}
    response = http.get("/api/command-centers?limit=1", headers=auth)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["universes"]] == ["work"]


def test_a_busy_key_store_never_turns_a_valid_key_into_a_401(stores, monkeypatch):
    """last_used_at is best-effort and throttled: a failed write keeps the key
    valid, and a second call inside the interval does not write at all."""
    import sqlite3

    from tinyassets import api_keys

    keys, authority = stores
    key = mint(keys, authority, scope("read_graph"))
    writes = []
    real_db = keys.db

    def db(**kwargs):
        if kwargs.get("timeout") == api_keys.TOUCH_TIMEOUT:
            writes.append(kwargs)
            raise sqlite3.OperationalError("database is locked")
        return real_db(**kwargs)

    monkeypatch.setattr(keys, "db", db)
    assert keys.resolve(key) is not None
    assert len(writes) == 1
    monkeypatch.setattr(keys, "db", real_db)
    assert keys.resolve(key) is not None  # records use now
    monkeypatch.setattr(keys, "db", db)
    assert keys.resolve(key) is not None
    assert len(writes) == 1  # throttled: no second write inside the interval
