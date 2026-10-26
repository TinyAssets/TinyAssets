"""A key the far side stopped accepting, and the one card that replaces it.

Live failure, 2026-09-16. A connection's stored credential died on the provider
side (one fine-grained token hit its expiry, another was revoked by its user).
The platform sent it correctly and the far side answered 401 — and then three
things went wrong in a row:

1. the 401 landed in the generic ``external_write_failed``, which is
   ``actionable_by: chatbot`` and whose action says "fix that and run again
   yourself", so the universe retried the same dead key and then narrated
   "please reconnect" in chat prose, raising no card at all;
2. there was no one-step replace — only remove-then-connect re-carrying every
   endpoint and scope — and the owner read "remove" as deletion and dismissed
   three such cards;
3. the connection stayed dead for ten days.

So the tests here are about the two halves of that: the CLASS (which decides
what the agent is told to do) and the CARD (which has to change the key and
nothing else).
"""
from __future__ import annotations

import json
import logging

import pytest

from tests.test_authenticated_external_call_effector import (  # noqa: F401
    _the_owners_rules_allow,  # autouse: the owner's policy allows this call
)
from tests.test_pending_requests import (  # noqa: F401 - fixtures and harness
    _ask,
    _login,
    _make_universe,
    _owner_answer,
    _rail,
    _reset_auth,
)

SECRET = "old-" + "a" * 40
REPLACEMENT = "new-" + "b" * 40


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _summary(status: int, body: str, kind: str = "far_side_error") -> str:
    """One row of a run's ``error``, exactly as the effect layer writes it.

    Built from the real composer rather than hand-typed, so a change to the
    message shape breaks this test instead of silently making the classifier
    unreachable.
    """
    from tinyassets.runs import _external_write_error_summary

    return _external_write_error_summary([{
        "node_id": "call", "sink": "authenticated_external_call",
        "error": f"far side answered HTTP {status}: {body}", "error_kind": kind,
    }]).lower()


def _classify(status: int, body: str, kind: str = "far_side_error") -> str:
    from tinyassets.runs import _classify_external_write

    return _classify_external_write(_summary(status, body, kind))


# --------------------------------------------------------------------------- #
# The class
# --------------------------------------------------------------------------- #


class _Answering:
    """A loopback HTTPS endpoint that answers with the status it is given.

    The effector suite's own loopback always answers 200, and the whole point
    here is the status. Everything else about the path is the real thing: the
    real broker dispatch, the real general vault resolver, the real SSRF driver.
    """

    def __init__(self, status: int, body: bytes):
        import http.server
        import threading

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # noqa: ANN001, ANN002
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _run_against(tmp_path, monkeypatch, *, status, body):
    """One real authenticated call whose far side answers ``status``.

    Returns the run's ``error`` line, built by the same composers the runner
    uses, so what is classified below is what a real run would actually carry.
    """
    from tests.test_authenticated_external_call_effector import (
        _install_inprocess_proxy,
        _install_loopback_driver,
        _setup,
    )
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.effectors.authenticated_external_call import (
        EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        run_authenticated_external_call_effector,
    )
    from tinyassets.runs import (
        _collect_external_write_errors,
        _external_write_error_summary,
    )

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    _data_root, universe_dir, db_path = _setup(tmp_path)
    far_side = _Answering(status, body)
    _install_loopback_driver(monkeypatch, far_side.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir,
        grant_id="grant-http", provider="http",
        destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"method": "POST", "path": "/v1/messages",
                    "body": {"text": "hello"}},
    }
    try:
        with identity_context(Identity(user_id="user-1", username="fixture-owner")):
            evidence = run_authenticated_external_call_effector(
                node_id="call", output_keys=["out"],
                run_state={"out": json.dumps(packet)},
                base_path=str(universe_dir), run_id="r1",
            )
    finally:
        far_side.stop()
    assert evidence["delivered"] is True, evidence
    rows = _collect_external_write_errors(
        {"call": {EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL: evidence}})
    return evidence, rows, _external_write_error_summary(rows)


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
def _deposit(uid, *, secret=SECRET, scopes=(), destination="acme"):
    from tests.support.connection_door import connect_http

    return connect_http(universe_id=uid, payload=json.dumps({
        "destination": destination, "secret": secret, "auth_scheme": "bearer",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
        "scopes": list(scopes),
    }))


def _rotate_through_the_rail(uid, *, secret=REPLACEMENT, destination="acme"):
    ask = _ask(
        uid,
        kind="API",
        title=f"{destination} stopped accepting its key",
        body="The far side answered 401. Paste a new key and I will carry on.",
        fields=[{"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": destination},
    )
    assert ask.get("request_id"), ask
    return ask, _owner_answer(uid, request_id=ask["request_id"],
                        values={"token": secret})


def _policy(base, uid, *, destination="acme", actor="alice"):
    """The connection's stored endpoint + scope JSON, and its grant row."""
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, grant_id = _ids(owner="alice", destination=destination)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: actor)
    return ledger.policy_json(conn_id), ledger.get_grant(grant_id)


def _stored_secret(base, uid, *, destination="acme"):
    """What the NEXT call would present, through the resolver the broker child
    actually runs — not by reading the vault file ourselves."""
    from tinyassets.auth.middleware import current_identity
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    resolver = _GeneralVaultCredentialResolver(
        universe_dir=custody_namespace(base, current_identity().user_id))
    return resolver(f"vault://http/{destination}")


# --------------------------------------------------------------------------- #
# Whose key it is
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:C11')
def test_same_owner_centers_rotate_one_connection_and_foreign_owner_cannot(base):
    from tests.support.connection_door import rotate_http

    for center in ("u-1", "u-2", "u-3"):
        _make_universe(base, center, admin="alice")
    _login("alice")
    assert not _deposit("u-1", secret=SECRET).get("error")
    out = rotate_http(universe_id="u-2", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert out.get("status") == "rotated", out
    assert _stored_secret(base, "u-1") == REPLACEMENT
    assert _stored_secret(base, "u-2") == REPLACEMENT
    assert rotate_http(universe_id="u-3", payload=json.dumps({
        "destination": "acme", "secret": SECRET}))["status"] == "rotated"
    _make_universe(base, "bob-center", admin="bob")
    _login("bob")
    assert rotate_http(universe_id="bob-center", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT})) == {"error": "not_found"}
    _login("alice")
    assert _stored_secret(base, "u-1") == SECRET


def _ledger_connection(base, *, uid, destination, actor="alice", **over):
    """A connection row built straight through the ledger, to reach shapes
    `connect_http` cannot create but a lower-level path can."""
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, grant_id = _ids(owner="alice", destination=destination)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: actor)
    fields = {
        "connection_id": conn_id,
        "owner_user_id": actor,
        "connection_class": "http",
        "connection_type": "http",
        "auth_scheme": "bearer",
        "scopes": ("POST",),
        "provider": "http",
        "destination": destination,
        "credential_ref": f"vault://http/{destination}",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
        **over,
    }
    ledger.create_connection(**fields)
    ledger.grant_connection(grant_id=grant_id, connection_id=conn_id,
                            owner_user_id=actor)
    return conn_id, grant_id


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_card_raised_for_one_deposit_cannot_rotate_a_different_one(base):
    """The tab sat open while the owner removed the key and deposited another.
    Answering it now would replace a key they never saw this card for."""
    _make_universe(base, "u-1", admin="alice")
    # N10: removal takes back alice's broker-held consents, so the box is hers.
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.daemon_server import grant_universe_ownership

    seed_owner_admission(base, principal="alice", center="u-1")
    grant_universe_ownership(base, universe_id="u-1", owner_id="alice")
    _login("alice")
    _deposit("u-1")

    ask = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert ask.get("request_id"), ask

    from tinyassets.api.http_connection import remove_http

    remove_http(universe_id="u-1", payload=json.dumps({"destination": "acme"}))
    _deposit("u-1", secret="third-" + "d" * 40)

    stale = _owner_answer("u-1", request_id=ask["request_id"],
                    values={"token": REPLACEMENT})
    assert stale.get("error") == "connection_changed", stale
    assert stale.get("request_pending") is True
    assert _stored_secret(base, "u-1") == "third-" + "d" * 40


# --------------------------------------------------------------------------- #
# The secret itself
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_the_new_key_is_in_no_result_and_no_log(base, caplog):
    """Every channel at once on the SUCCESS path: the returned envelopes, the
    request row the rail stores, and the log."""
    from tests.support.connection_door import rotate_http
    from tinyassets.api.pending_requests import list_requests

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    caplog.set_level(logging.DEBUG)
    logging.getLogger("tinyassets.api.http_connection").debug("capture-sentinel")

    ask, answered = _rotate_through_the_rail("u-1")
    rail = list_requests(universe_id="u-1")
    direct = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert direct.get("status") == "rotated", direct

    for name, envelope in (("ask", ask), ("answer", answered),
                           ("rail", rail), ("direct", direct)):
        assert REPLACEMENT not in json.dumps(envelope, default=str), name
        # And the reference that resolves to it never rides along either.
        assert "vault://" not in json.dumps(envelope, default=str), name

    messages = [record.getMessage() for record in caplog.records]
    # The sentinel proves this capture is LIVE. Without it, "no record contains
    # the secret" is also what an empty capture says (Codex refute-review, P2 #6).
    assert any("capture-sentinel" in message for message in messages)
    for record in caplog.records:
        assert REPLACEMENT not in record.getMessage(), record.name
        assert REPLACEMENT not in str(record.args or ""), record.name


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_storage_fault_carrying_the_key_leaks_it_nowhere(base, caplog):
    """The exception path, with a fault whose own message holds the secret.

    The earlier version of this only submitted whitespace and then checked that a
    DIFFERENT value was absent, which proves nothing about exception secrecy
    (Codex refute-review, P2 #6). A vault fault really can carry the value it was
    handed — that is the whole of memory `exceptions-carry-more-than-their-
    message` — so the fault is injected carrying it, and the refusal, the log and
    the traceback text are all checked afterwards.
    """
    from tests.support.connection_door import rotate_http
    from tinyassets.broker import vault as credential_vault

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    caplog.set_level(logging.DEBUG)
    logging.getLogger("tinyassets.api.http_connection").debug("capture-sentinel")
    real = credential_vault.write_credential_vault
    try:
        def _explode(*_a, **_k):
            raise RuntimeError(f"disk error while storing {REPLACEMENT}")

        credential_vault.write_credential_vault = _explode
        out = rotate_http(universe_id="u-1", payload=json.dumps({
            "destination": "acme", "secret": REPLACEMENT}))
    finally:
        credential_vault.write_credential_vault = real

    assert out == {"error": "deposit_failed"}, out
    assert REPLACEMENT not in json.dumps(out)
    messages = [record.getMessage() for record in caplog.records]
    assert any("capture-sentinel" in message for message in messages)
    for record in caplog.records:
        assert REPLACEMENT not in record.getMessage(), record.name
        assert REPLACEMENT not in str(record.args or ""), record.name
        # `exc_info` renders the chained exception's own text, which is where the
        # value was planted.
        if record.exc_info:
            import traceback
            rendered = "".join(traceback.format_exception(*record.exc_info))
            assert REPLACEMENT not in rendered, record.name
    # The old key is untouched, because the refusal is the whole outcome.
    assert _stored_secret(base, "u-1") == SECRET


# --------------------------------------------------------------------------- #
# How old the key is
# --------------------------------------------------------------------------- #


from tests.support.egress import admitted_egress  # noqa: E402, F401
