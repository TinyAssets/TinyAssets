"""C27: the owner's agent can see, change and give back what it may do.

Change `agent-access-controls`. Every test drives the SERVED handle the
universe agent actually has (``engine_mcp_server``), except where the API is
the only honest place to stand as a different user. Readbacks come from the
enforcement stores, never from the write's own reply.
"""
from __future__ import annotations

import json

import pytest

from tests.test_pending_requests import _login, _logout, _make_universe

_SINK = "authenticated_external_call"
_DEST = "hooks.example"
_FIELDS = [{"name": "note", "type": "text", "label": "Which file?"}]


@pytest.fixture(autouse=True)
def _reset_auth():
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    _make_universe(root, "u-1", admin="founder")
    _make_universe(root, "u-2", admin="mallory")
    return root


def _served(monkeypatch, *, actor="founder", graph="u-1"):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    from tests.engine_authority_helpers import seed_bound_engine

    seed_bound_engine(monkeypatch)
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    return s


def _read_access(s) -> dict:
    fn = getattr(s.read_graph, "fn", s.read_graph)
    return json.loads(fn(target="access"))


def _channel(s, action, sink=_SINK, destination=_DEST) -> dict:
    fn = getattr(s.source_channel, "fn", s.source_channel)
    return json.loads(fn(action=action, payload=json.dumps(
        {"channel_type": sink, "destination": destination})))


def _write(s, **kw) -> dict:
    fn = getattr(s.write_graph, "fn", s.write_graph)
    return json.loads(fn(**kw))


def _ask(s, title="GitHub key for the theme file", **extra) -> dict:
    return _write(s, target="pending_request", operation="ask",
                  payload_json=json.dumps({
                      "kind": "API", "title": title, "body": "",
                      "fields": _FIELDS, "action": {"type": "answer"}, **extra,
                  }))


def _withdraw(s, request_id, reason="the platform changed; not needed") -> dict:
    return _write(s, target="pending_request", operation="withdraw",
                  payload_json=json.dumps({"request_id": request_id,
                                           "reason": reason}))


def _consent_active(base, uid, sink=_SINK, destination=_DEST) -> bool:
    from tinyassets.storage.effector_consents import is_consent_active

    return is_consent_active(base / uid, sink=sink, destination=destination)


# ── read what it holds ──────────────────────────────────────────────────────


def test_the_agent_reads_what_it_holds_in_one_call(monkeypatch, base):
    s = _served(monkeypatch)
    assert _channel(s, "approve")["status"] == "granted"
    asked = _ask(s)

    held = _read_access(s)

    assert held.get("universe_id") == "u-1", held
    consents = [(c["sink"], c["destination"]) for c in held["channel_consents"]]
    assert (_SINK, _DEST) in consents
    assert held["channels"] == []  # nothing deposited in this fixture
    assert "workspace_consents" in held
    assert held["spend_allowances"]["sources"] == []
    waiting = {r["request_id"]: r for r in held["waiting_requests"]}
    assert waiting[asked["request_id"]]["withdrawable"] is True
    assert waiting[asked["request_id"]]["origin"] == "agent"
    assert "standing_decisions" in held
    assert "source_channel action=\"revoke\"" in held["how_to_change"]["revoke_channel"]


def test_the_readback_carries_no_secret(monkeypatch, base):
    s = _served(monkeypatch)
    _channel(s, "approve")
    held = _read_access(s)
    assert held["channel_consents"], held
    text = json.dumps(held)
    for leak in ("credential_ref", "secret_value", "vault://"):
        assert leak not in text


def test_access_is_a_served_read_target():
    from tinyassets import engine_mcp_server as s

    assert "access" in s._PINNED_READ_TARGETS


# ── change one channel ──────────────────────────────────────────────────────


def test_revoke_takes_a_consent_back_and_the_readback_matches(monkeypatch, base):
    s = _served(monkeypatch)
    _channel(s, "approve")
    assert _consent_active(base, "u-1")

    out = _channel(s, "revoke")

    assert out["status"] == "revoked", out
    assert out["active"] is False
    # Read from the store the effector consults, not from the reply.
    assert not _consent_active(base, "u-1")
    consents = [(c["sink"], c["destination"])
                for c in _read_access(s)["channel_consents"]]
    assert (_SINK, _DEST) not in consents


def test_revoking_what_is_not_held_says_so(monkeypatch, base):
    s = _served(monkeypatch)
    out = _channel(s, "revoke")
    assert out["status"] == "not_held", out
    assert out["active"] is False


def test_regrant_after_revoke_reads_back_active(monkeypatch, base):
    s = _served(monkeypatch)
    _channel(s, "approve")
    assert _channel(s, "revoke")["status"] == "revoked"
    assert not _consent_active(base, "u-1")
    _channel(s, "approve")
    assert _consent_active(base, "u-1")


def test_the_agent_may_give_back_workspace_consent_it_cannot_grant(monkeypatch, base):
    from tinyassets.storage.effector_consents import grant_consent

    s = _served(monkeypatch)
    dest = "checkout:conn-1:github.com/o/r"
    grant_consent(base / "u-1", sink="workspace", destination=dest,
                  granted_by="founder")
    assert "error" in _channel(s, "approve", sink="workspace", destination=dest)

    out = _channel(s, "revoke", sink="workspace", destination=dest)

    assert out["status"] == "revoked", out
    assert not _consent_active(base, "u-1", sink="workspace", destination=dest)


def test_revoke_refuses_source_code(monkeypatch, base):
    s = _served(monkeypatch)
    out = _channel(s, "revoke", sink="source_code", destination="x")
    assert "not a consent" in out.get("error", ""), out


def test_policy_verbs_stay_off_the_served_surface(monkeypatch, base):
    """The policy store has no reader (design D3): serving it would be a
    control that changes nothing."""
    s = _served(monkeypatch)
    for dead in ("set_policy", "get_policy"):
        assert "error" in _channel(s, dead)


# ── withdraw a stale request ────────────────────────────────────────────────


def test_the_agent_withdraws_its_own_stale_ask(monkeypatch, base):
    from tests.owner_answer import answer_request
    from tinyassets.api.pending_requests import list_requests

    s = _served(monkeypatch)
    rid = _ask(s)["request_id"]

    out = _withdraw(s, rid)

    assert out["status"] == "withdrawn", out
    assert out["still_on_rail"] is False
    _login("founder")
    rail = list_requests(universe_id="u-1")
    assert rid not in {r["request_id"] for r in rail["pending"]}
    gone = {r["request_id"]: r for r in rail["recently_answered"]}[rid]
    assert gone["status"] == "withdrawn"
    assert gone["feedback"] == "the platform changed; not needed"
    late = answer_request(universe_id="u-1", payload={"request_id": rid,
                                                       "dismiss": True})
    assert late == {"error": "already_resolved", "status": "withdrawn"}


def test_withdrawal_is_not_a_standing_decision(monkeypatch, base):
    s = _served(monkeypatch)
    first = _ask(s)["request_id"]
    _withdraw(s, first)
    again = _ask(s)
    assert again.get("status") == "pending", again
    assert again["request_id"] != first


def test_the_agent_cannot_withdraw_what_the_owner_answered(monkeypatch, base):
    from tests.owner_answer import answer_request

    s = _served(monkeypatch)
    rid = _ask(s)["request_id"]
    _login("founder")
    answer_request(universe_id="u-1", payload={"request_id": rid, "dismiss": True})
    _logout()

    out = _withdraw(s, rid)

    assert out.get("error") == "already_resolved", out
    assert out["status"] == "dismissed"


def test_the_agent_cannot_withdraw_a_platform_ask(monkeypatch, base):
    from tinyassets.api.pending_requests import request_from_user

    _login("founder")
    row = request_from_user(universe_id="u-1", origin="platform", payload={
        "kind": "Models", "title": "Power your universe", "body": "",
        "fields": _FIELDS, "action": {"type": "answer"},
    })
    _logout()
    s = _served(monkeypatch)

    out = _withdraw(s, row["request_id"])

    assert out.get("error") == "not_withdrawable", out
    held = {r["request_id"]: r for r in _read_access(s)["waiting_requests"]}
    assert held[row["request_id"]]["withdrawable"] is False


def test_the_asker_cannot_choose_its_origin(monkeypatch, base):
    s = _served(monkeypatch)
    row = _ask(s, origin="platform")
    held = {r["request_id"]: r for r in _read_access(s)["waiting_requests"]}
    assert held[row["request_id"]]["origin"] == "agent"


def test_the_model_connection_entry_is_not_withdrawable(monkeypatch, base):
    s = _served(monkeypatch)
    out = _withdraw(s, "sys_connect_llm")
    assert out.get("error") == "not_withdrawable", out


def test_withdraw_needs_a_request_id(monkeypatch, base):
    s = _served(monkeypatch)
    out = _write(s, target="pending_request", operation="withdraw",
                 payload_json="{}")
    assert out.get("error") == "request_invalid", out


def test_an_old_database_gains_the_origin_column(base):
    import sqlite3

    from tinyassets.storage.pending_requests import create_request, get_request

    udir = base / "u-1"
    with sqlite3.connect(udir / ".pending_requests.db") as conn:
        conn.execute(
            "CREATE TABLE pending_requests (request_id TEXT PRIMARY KEY, "
            "kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, "
            "fields_json TEXT NOT NULL, action_json TEXT NOT NULL, "
            "dedupe_key TEXT NOT NULL, status TEXT NOT NULL, answer_json TEXT, "
            "feedback TEXT, created_at REAL NOT NULL, resolved_at REAL)")
        conn.execute(
            "INSERT INTO pending_requests VALUES ('req_old','API','t','',"
            "'[]','{}','k','pending',NULL,NULL,1.0,NULL)")
    assert get_request(udir, "req_old")["origin"] == "agent"
    row = create_request(udir, kind="API", title="n", body="", fields=[],
                         action={"type": "answer"}, dedupe_key="k2",
                         origin="platform")
    assert row["origin"] == "platform"


# ── cross-user: another user's universe is never read or changed ────────────


def test_another_user_cannot_read_my_access(base):
    from tinyassets.api.agent_access import read_access
    from tinyassets.storage.effector_consents import grant_consent

    grant_consent(base / "u-1", sink=_SINK, destination=_DEST, granted_by="founder")
    _login("mallory")
    out = read_access(universe_id="u-1")
    assert out.get("error") == "not_found", out
    assert "channel_consents" not in out


def test_another_user_cannot_revoke_my_consent(base):
    from tinyassets.api.source_channel import source_channel
    from tinyassets.storage.effector_consents import grant_consent

    grant_consent(base / "u-1", sink=_SINK, destination=_DEST, granted_by="founder")
    _login("mallory")
    out = json.loads(source_channel(action="revoke", universe_id="u-1", payload={
        "channel_type": _SINK, "destination": _DEST}))
    assert out.get("error") == "auth_failed", out
    assert _consent_active(base, "u-1")
    # Control: the same call as the owner succeeds, so the refusal above is
    # the ownership gate and not a missing verb.
    _login("founder")
    mine = json.loads(source_channel(action="revoke", universe_id="u-1", payload={
        "channel_type": _SINK, "destination": _DEST}))
    assert mine["status"] == "revoked", mine


def test_another_user_cannot_withdraw_my_request(base):
    from tinyassets.api.pending_requests import request_from_user, withdraw_request
    from tinyassets.storage.pending_requests import get_request

    _login("founder")
    rid = request_from_user(universe_id="u-1", payload={
        "kind": "API", "title": "t", "body": "", "fields": _FIELDS,
        "action": {"type": "answer"}})["request_id"]
    _login("mallory")
    out = withdraw_request(universe_id="u-1", payload={"request_id": rid})
    assert out.get("error") == "not_found", out
    assert get_request(base / "u-1", rid)["status"] == "pending"


def test_a_served_surface_pinned_to_my_universe_refuses_another_actor(monkeypatch, base):
    """The engine bound to mallory but pinned to u-1 holds no serving authority
    there, so every C27 verb refuses before touching u-1."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.storage.effector_consents import grant_consent

    grant_consent(base / "u-1", sink=_SINK, destination=_DEST, granted_by="founder")
    monkeypatch.setattr(s, "_ACTOR_ID", "mallory")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-1")
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")

    assert "error" in _read_access(s)
    assert "error" in _channel(s, "revoke")
    assert "error" in _withdraw(s, "req_anything")
    assert _consent_active(base, "u-1")
    # Control: bound as the owner, the same revoke goes through.
    owner = _served(monkeypatch)
    assert _channel(owner, "revoke")["status"] == "revoked"


# ── connector parity: the owner's chatbot gets the same verbs ───────────────


def test_the_connector_reads_access_and_withdraws(base):
    import importlib

    from tinyassets import universe_server as us

    _login("founder")
    importlib.reload(us)
    try:
        raw = us.write_graph(target="connection", operation="request_from_user",
                             graph_id="u-1", payload_json=json.dumps({
                                 "kind": "API", "title": "t", "body": "",
                                 "fields": _FIELDS, "action": {"type": "answer"}}))
        rid = json.loads(raw)["request_id"]
        held = json.loads(us.read_graph(target="access", graph_id="u-1"))
        assert rid in {r["request_id"] for r in held["waiting_requests"]}
        # The connector is told the verbs IT has, not the served ones.
        assert 'target="source_channel" operation="revoke"' in (
            held["how_to_change"]["revoke_channel"])

        done = json.loads(us.write_graph(
            target="connection", operation="withdraw_request", graph_id="u-1",
            payload_json=json.dumps({"request_id": rid})))
        assert done["status"] == "withdrawn", done

        us.write_graph(target="source_channel", operation="approve", graph_id="u-1",
                       payload_json=json.dumps({"channel_type": _SINK,
                                                "destination": _DEST}))
        gone = json.loads(us.write_graph(
            target="source_channel", operation="revoke", graph_id="u-1",
            payload_json=json.dumps({"channel_type": _SINK, "destination": _DEST})))
        assert gone["status"] == "revoked", gone
        assert not _consent_active(base, "u-1")
    finally:
        importlib.reload(us)


def _deposit(base, *, owner, uid, destination, access_mode):
    """A connection as the deposit path leaves it: ledger row + grant."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: owner)
    ledger.create_connection(
        connection_id=f"conn-{owner}", owner_user_id=owner,
        connection_class="http", connection_type="http", auth_scheme="bearer",
        scopes=("GET",), provider="http", destination=destination,
        credential_ref=f"vault://http/{owner}/{destination}",
        allowed_endpoints=[{"host": "api.github.com", "path_template": "/{path+}",
                            "methods": ["GET"],
                            "param_patterns": {"path": "[A-Za-z0-9._/-]{1,200}"}}],
        access_mode=access_mode,
    )
    ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                            owner_user_id=owner, universe_id=uid)


def test_the_readback_shows_full_channel_access_and_only_mine(monkeypatch, base):
    _deposit(base, owner="founder", uid="u-1", destination="github",
             access_mode="full")
    _deposit(base, owner="mallory", uid="u-2", destination="mallory-slack",
             access_mode="exact")
    s = _served(monkeypatch)

    held = _read_access(s)

    channels = {c["destination"]: c for c in held["channels"]}
    assert set(channels) == {"github"}, held["channels"]
    assert channels["github"]["access"] == "full"
    text = json.dumps(held)
    assert "vault://" not in text and "credential_ref" not in text
    assert "mallory" not in text
