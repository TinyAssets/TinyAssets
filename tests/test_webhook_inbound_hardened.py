"""End-to-end adversarial tests for the hardened inbound webhook path (Codex round 2).

These use the REAL surfaces — real authenticated principals, real authored branches, real
universes, the real ``run_graph`` op dispatch, and the real enqueue path writing to the real
runs DB — NOT mocked ownership or spied enqueue. Each test reproduces a specific finding:

  #1 author-gate bypass       — a caller cannot mint a hook for a branch they did not author
  #2 dark flag is a boundary  — flag off => route absent AND no run is enqueued
  #3 revocation               — a revoked token triggers no run
  #4 replay                   — same/altered delivery fires once; a genuinely new body fires again
  #5 no execution refusal    — a busy universe is NEVER dropped; every delivery enqueues and
                              queues for an agent seat (founder 2026-09-30)
  #6 header/credential leak    — only allowlisted headers reach durable run input; no token stored
  #7 uniform response          — every non-deliverable state answers 404 (no 500 usability leak)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import tinyassets.webhook_inbound as wh
from tinyassets.storage import webhook_hooks

# ── Real fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path: Path, monkeypatch, authenticate_request):
    """A real data dir with inbound ENABLED, the author server initialized, and the
    credential-subject auth provider wired (via the shared ``authenticate_request``)."""
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.runs import initialize_runs_db

    initialize_author_server(base)
    initialize_runs_db(base)          # scheduler tables, as the daemon does at boot
    webhook_hooks._initialized.clear()
    return base, authenticate_request


#: OAuth scopes a founder needs to create a universe and drive the inbound ops.
_FOUNDER_CAPS = [
    "tinyassets.universe.costly",
    "tinyassets.extensions.read",
    "tinyassets.extensions.write",
    "tinyassets.extensions.admin",
    "tinyassets.extensions.costly",
]


def _create_universe(sub: str, authenticate) -> str:
    """Authenticate as ``sub`` and create a REAL universe they own (admin ACL)."""
    from tinyassets.api import universe as universe_api

    authenticate(sub, _FOUNDER_CAPS)
    out = json.loads(universe_api._universe_impl(action="create_universe"))
    assert out.get("error") is None, out
    return out["universe_id"]


def _seed_branch(base: Path, *, bid: str, author: str) -> None:
    """Persist a REAL, structurally-valid, runnable branch authored by ``author``."""
    from tinyassets.daemon_server import save_branch_definition

    src = "def run(state):\n    return {'out': 'ok'}\n"
    save_branch_definition(base, branch_def={
        "branch_def_id": bid,
        "name": bid,
        "author": author,
        "domain_id": "workflow",
        "visibility": "public",
        "node_defs": [{
            "node_id": "only",
            "display_name": "Only",
            "phase": "custom",
            "input_keys": [],
            "output_keys": ["out"],
            "source_code": src,
            "approved": True,
            "approved_source_hash": hashlib.sha256(src.encode()).hexdigest(),
            "tools_allowed": [],
        }],
        "graph_nodes": [{"id": "only", "node_def_id": "only", "position": 0}],
        "edges": [
            {"from_node": "START", "to_node": "only"},
            {"from_node": "only", "to_node": "END"},
        ],
        "conditional_edges": [],
        "state_schema": [{"name": "out", "type": "str"}],
        "entry_point": "only",
    })


def _runs_for_universe(base: Path, uid: str) -> list[dict]:
    """Every run row (any status) enqueued for ``uid`` — read straight from the runs DB."""
    from tinyassets.runs import initialize_runs_db

    initialize_runs_db(base)
    conn = sqlite3.connect(base / ".runs.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id, actor, inputs_json, queue_universe_id FROM runs "
            "WHERE queue_universe_id = ?",
            (uid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _author_branch_via_op(name: str) -> str:
    """Create a branch through the PUBLIC authoring op as the currently-authenticated actor,
    so its ``author`` is set by the real path (not a direct seed). Returns the branch_def_id."""
    from tinyassets.api.extensions import _extensions_impl

    out = json.loads(_extensions_impl(action="create_branch", name=name))
    assert out.get("error") is None, out
    return out["branch_def_id"]


def _mint(uid: str, bid: str) -> dict:
    """Mint through the REAL run_graph op dispatch (author-gate + owner-scope both fire)."""
    from tinyassets.universe_server import run_graph

    return json.loads(run_graph(webhook_op="mint", branch_def_id=bid, graph_id=uid))


def _source_op(op: str, uid: str, **kw) -> dict:
    from tinyassets.universe_server import run_graph

    return json.loads(run_graph(source_op=op, graph_id=uid, **kw))


# ── #1 Author-gate bypass ────────────────────────────────────────────────────────

def test_a_caller_cannot_mint_a_hook_for_a_branch_they_did_not_author(env):
    base, authenticate = env
    _create_universe("founder-a", authenticate)
    victim_bid = _author_branch_via_op("victim-branch")     # authored by founder-a via the op

    # Attacker owns their OWN universe and targets it, but the branch is founder-a's.
    uid_b = _create_universe("attacker-b", authenticate)
    out = _mint(uid_b, victim_bid)
    assert "error" in out                                   # rejected: not the author
    assert webhook_hooks.list_for_universe(base, universe_id=uid_b) == []  # nothing minted


def test_the_author_can_mint_for_their_own_branch(env):
    base, authenticate = env
    uid_a = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="mine", author="founder-a")
    out = _mint(uid_a, "mine")
    assert out.get("token") and out["url"].endswith(out["token"])
    # The binding also records WHO minted it -- a webhook token is somebody's.
    # An exact-dict assertion written when nothing owned one would hide that.
    from tinyassets.api import permissions

    binding = webhook_hooks.resolve(base, token=out["token"])
    assert binding["universe_id"] == uid_a
    assert binding["branch_def_id"] == "mine"
    assert binding["source_id"] is None
    assert binding["owner_principal_id"] == permissions.current_actor_id()
    assert binding["owner_principal_id"], "a token minted by nobody"


# ── Source ops end-to-end (owner-scoped, run_graph-reachable) ────────────────────

def test_create_source_mints_a_source_hook_and_registers_a_trigger(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    out = _source_op("create", uid, branch_def_id="b")
    source_id = out["source_id"]
    binding = webhook_hooks.resolve(base, token=out["token"])
    assert binding["source_id"] == source_id and binding["universe_id"] == uid
    from tinyassets.control_plane.events import list_scheduler_subscriptions
    subs = list_scheduler_subscriptions(
        base, owner_actor=f"universe:{uid}", event_type=f"source:{source_id}",
    )
    assert len(subs) == 1 and subs[0]["branch_def_id"] == "b"


def test_create_source_is_author_gated(env):
    base, authenticate = env
    _create_universe("founder-a", authenticate)
    victim_bid = _author_branch_via_op("victim")            # authored by founder-a via the op
    uid_b = _create_universe("attacker-b", authenticate)
    out = _source_op("create", uid_b, branch_def_id=victim_bid)
    assert "error" in out                          # cannot source a branch you didn't author


def test_revoke_source_tears_down_both_hook_and_trigger(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    created = _source_op("create", uid, branch_def_id="b")
    revoked = _source_op("revoke", uid, source_id=created["source_id"])
    assert revoked["revoked"] is True
    assert webhook_hooks.resolve(base, token=created["token"]) is None
    from tinyassets.control_plane.events import list_scheduler_subscriptions
    assert list_scheduler_subscriptions(
        base, owner_actor=f"universe:{uid}", event_type=f"source:{created['source_id']}",
        active_only=True,
    ) == []


def test_a_source_delivery_flows_through_the_real_bus_to_a_run(env):
    # End-to-end: create a Source, POST to its /hooks token, and let the
    # execution owner's tick dispatch the real event into a real run.
    import time as _time

    from tinyassets.control_plane.events import (
        get_or_create_scheduler,
        shutdown_scheduler,
        tick_events,
    )
    from tinyassets.universe_server import _inbound_event_run_fn

    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _source_op("create", uid, branch_def_id="b")["token"]

    shutdown_scheduler()
    get_or_create_scheduler(base, _inbound_event_run_fn)
    try:
        status, payload = wh.handle_hook(token=token, body=b'{"e":1}', headers={}, base_path=base)
        assert status == 202 and payload.get("via") == "event"
        assert tick_events() == 1
        # the event loop fires the run asynchronously — poll the REAL runs DB for it
        deadline = _time.time() + 5
        while _time.time() < deadline and not _runs_for_universe(base, uid):
            _time.sleep(0.02)
        runs = _runs_for_universe(base, uid)
        assert len(runs) == 1 and runs[0]["actor"] == f"universe:{uid}"
        # the private reservation key never reached the branch inputs
        assert "__inbound_reservation__" not in runs[0]["inputs_json"]
        # drain: let the real background run finish so it doesn't leak into other tests
        from tinyassets.runs import get_run
        run_id = runs[0]["run_id"]
        terminal = {"completed", "failed", "cancelled", "interrupted"}
        d2 = _time.time() + 5
        while _time.time() < d2 and (get_run(base, run_id) or {}).get("status") not in terminal:
            _time.sleep(0.02)
    finally:
        shutdown_scheduler()


def test_a_non_owner_cannot_revoke_anothers_source(env):
    base, authenticate = env
    uid_a = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    created = _source_op("create", uid_a, branch_def_id="b")
    # Attacker targets their OWN universe with founder-a's source_id -> indistinct no-op.
    uid_b = _create_universe("attacker-b", authenticate)
    out = _source_op("revoke", uid_b, source_id=created["source_id"])
    assert out.get("revoked") is False
    assert webhook_hooks.resolve(base, token=created["token"]) is not None   # untouched


# ── #2 Dark flag is a real boundary ──────────────────────────────────────────────

def test_flag_off_means_no_route_and_no_enqueue(env, monkeypatch):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    # Now turn the master flag OFF and rebuild the app.
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "0")
    from tinyassets.universe_server import create_streamable_http_app

    app = create_streamable_http_app()
    paths = _route_paths(app)
    assert not any("/hooks/inbound/" in p for p in paths)           # route not mounted

    # And even a direct call refuses + enqueues nothing (defense in depth).
    status, _ = wh.handle_hook(token=token, body=b"{}", headers={}, base_path=base)
    assert status == 404
    assert _runs_for_universe(base, uid) == []


def test_disabled_execution_owner_refuses_delivery_without_claim(env, monkeypatch):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _source_op("create", uid, branch_def_id="b")["token"]
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "0")

    assert wh.handle_hook(token=token, body=b'{}', headers={}, base_path=base) == (
        404, {"error": "not_found"},
    )
    assert _runs_for_universe(base, uid) == []

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    from tinyassets.control_plane.events import get_or_create_scheduler, shutdown_scheduler
    from tinyassets.universe_server import _inbound_event_run_fn

    shutdown_scheduler()
    get_or_create_scheduler(base, _inbound_event_run_fn)
    try:
        status, payload = wh.handle_hook(token=token, body=b'{}', headers={}, base_path=base)
        assert status == 202 and payload.get("via") == "event"
    finally:
        shutdown_scheduler()


def _route_paths(app) -> list[str]:
    # Unwrap middleware to reach the Starlette route table.
    inner = app
    for _ in range(6):
        if hasattr(inner, "routes"):
            break
        inner = getattr(inner, "app", None) or getattr(inner, "_app", None)
        if inner is None:
            return []
    return [getattr(r, "path", "") for r in getattr(inner, "routes", [])]


# ── #3 Revocation ────────────────────────────────────────────────────────────────

def test_a_revoked_token_triggers_no_run(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]
    assert webhook_hooks.revoke(base, token=token) is True

    status, _ = wh.handle_hook(token=token, body=b"{}", headers={}, base_path=base)
    assert status == 404
    assert _runs_for_universe(base, uid) == []


# ── #4 Replay (server-side key; caller header cannot influence it) ────────────────

def test_replay_fires_once_even_when_the_delivery_header_changes(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    body = b'{"event":"push"}'
    s1, _ = wh.handle_hook(token=token, body=body,
                           headers={"X-GitHub-Delivery": "d-1"}, base_path=base)
    # Same body, ATTACKER-ALTERED delivery header — must still be treated as a replay.
    s2, _ = wh.handle_hook(token=token, body=body,
                           headers={"X-GitHub-Delivery": "d-2-different"}, base_path=base)
    assert s1 == 202 and s2 == 202
    assert len(_runs_for_universe(base, uid)) == 1          # exactly one real run enqueued

    # A genuinely different body IS a new delivery.
    s3, _ = wh.handle_hook(token=token, body=b'{"event":"other"}', headers={}, base_path=base)
    assert s3 == 202
    assert len(_runs_for_universe(base, uid)) == 2


def test_concurrent_identical_deliveries_enqueue_exactly_one(env):
    # The atomic claim-first dedupe: N identical concurrent deliveries -> exactly ONE run,
    # regardless of scheduling (the losers get 202-deduped without enqueuing).
    from concurrent.futures import ThreadPoolExecutor

    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    def _fire(_i):
        return wh.handle_hook(token=token, body=b'{"same":1}', headers={}, base_path=base)[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(_fire, range(8)))
    assert all(c == 202 for c in codes)
    assert len(_runs_for_universe(base, uid)) == 1          # exactly one real run across 8 racers


# ── #5 No execution refusal: a busy universe queues ─────────────────────────────

def test_a_universe_with_many_runs_in_flight_is_still_delivered(env):
    """The 503 "busy" drop is gone: delivery 4, 40 and 400 all enqueue.

    ``_MAX_INFLIGHT_PER_UNIVERSE = 20`` used to answer 503 and throw the delivery
    away once a universe had 20 inbound-triggered runs queued or running. Founder,
    2026-09-30: over the seat count work WAITS and is never refused -- and a
    webhook the sending channel will not retry is exactly the thing that must not
    be dropped. The run is enqueued and waits for a seat.
    """
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    # 30 real reservations held open, well past the old ceiling of 20.
    for _ in range(30):
        rid, status = webhook_hooks.reserve_dispatch(
            base, token=token, universe_id=uid, ttl_s=wh._RESERVATION_TTL_S)
        assert status == "ok" and rid

    status, payload = wh.handle_hook(token=token, body=b"{}", headers={}, base_path=base)
    assert status == 202 and payload.get("queued") is True
    assert len(_runs_for_universe(base, uid)) == 1, "the run was enqueued, not dropped"


def test_no_module_carries_an_inflight_or_per_universe_ceiling():
    """Mutation guard. Re-adding either name is the regression."""
    for name in ("_MAX_INFLIGHT_PER_UNIVERSE", "_UNIVERSE_RATE_MAX"):
        assert not hasattr(wh, name), f"{name} came back"
    import inspect

    src = inspect.getsource(wh._handle_hook_inner)
    assert '"busy"' not in src and "503" not in src, (
        "the inbound path must have no saturation refusal"
    )


def test_a_burst_of_concurrent_deliveries_all_enqueue(env):
    """N concurrent DISTINCT deliveries: every one is admitted, each exactly once.

    This replaced a test asserting 6 of 10 got a 503. What still has to hold is
    that the atomic reserve + dedupe never double-fires and never loses one.
    """
    import threading

    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    lock = threading.Lock()
    enqueued: list[str] = []
    statuses: list[int] = []

    def _recording_enqueue(b, *, universe_id, branch_def_id, inputs, principal_id):
        with lock:
            enqueued.append(str(inputs["webhook"]["payload"]))
        return "run-x"

    # A worker thread starts with an EMPTY context: contextvars do not cross a
    # `threading.Thread` boundary, so the bound identity is not there and every
    # request refuses before it can reserve a slot. In production the ASGI
    # middleware binds per request on whatever thread serves it, so each firing
    # thread binds the same caller here -- one Context cannot be shared, since
    # `Context.run` refuses to be entered twice at once.
    from tinyassets.auth import middleware as _mw

    caller = _mw.current_identity_or_none()

    def _fire(i):
        _mw._current_identity.set(caller)
        st, _ = wh.handle_hook(token=token, body=f'{{"i":{i}}}'.encode(),
                               headers={}, base_path=base,
                               enqueue=_recording_enqueue)
        with lock:
            statuses.append(st)

    threads = [threading.Thread(target=_fire, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses == [202] * 10, f"a delivery was refused: {sorted(statuses)}"
    assert len(enqueued) == 10 and len(set(enqueued)) == 10


# ── #6 Header / credential persistence ───────────────────────────────────────────

def test_credential_headers_never_reach_durable_run_input(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    status, _ = wh.handle_hook(
        token=token, body=b"{}", base_path=base,
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "s=1",
            "CF-Access-Client-Secret": "cf-secret",
            "CF-Access-Jwt-Assertion": "jwt",
            "X-Api-Key": "key-123",
            "X-GitHub-Event": "push",          # allowlisted -> forwarded
        },
    )
    assert status == 202
    runs = _runs_for_universe(base, uid)
    assert len(runs) == 1
    fwd = json.loads(runs[0]["inputs_json"])["webhook"]["headers"]
    assert fwd == {"X-GitHub-Event": "push"}   # ONLY the allowlisted header survived
    for leaked in ("Authorization", "Cookie", "CF-Access-Client-Secret",
                   "CF-Access-Jwt-Assertion", "X-Api-Key"):
        assert leaked not in fwd
    # And the raw token is nowhere in the stored run input.
    assert token not in runs[0]["inputs_json"]


# ── #7 Uniform non-deliverable response (no 500 usability leak) ───────────────────

def test_every_non_deliverable_state_answers_404(env, monkeypatch):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")

    # unknown token
    assert wh.handle_hook(token="nope", body=b"{}", headers={}, base_path=base)[0] == 404

    # a source hook whose event bus is off -> 404 (NOT 500), and no run
    from tinyassets.control_plane.events import shutdown_scheduler
    shutdown_scheduler()
    src_token = webhook_hooks.mint(
        base,
        universe_id=uid,
        branch_def_id="b",
        source_id="s1",
        owner_principal_id="owner-test",
    )
    assert wh.handle_hook(token=src_token, body=b"{}", headers={}, base_path=base)[0] == 404

    # a valid token whose branch has vanished -> 404 (uniform), not 500
    plain = webhook_hooks.mint(
        base,
        universe_id=uid,
        branch_def_id="ghost-branch",
        owner_principal_id="owner-test",
    )
    assert wh.handle_hook(token=plain, body=b"{}", headers={}, base_path=base)[0] == 404
    assert _runs_for_universe(base, uid) == []


def test_inbound_hook_path_is_exact(monkeypatch):
    """Only one non-empty token segment bypasses foreign bearer interpretation."""
    from tinyassets.auth.middleware import _auth_challenge_path, _is_inbound_hook_path

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    assert _is_inbound_hook_path("/hooks/inbound/abc123") is True
    for path in ("/hooks/inbound/a/b", "/hooks/inbound/", "/hooks/inbound",
                 "/mcp/hooks/abc123"):
        assert _is_inbound_hook_path(path) is False
    assert _auth_challenge_path("/mcp/hooks/abc123") is True
    assert _auth_challenge_path("/mcp/tools") is True
    assert _auth_challenge_path("/mcp") is True
