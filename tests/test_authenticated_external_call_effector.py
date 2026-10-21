"""The ONE generic channel primitive — proof the shape is right.

A universe wires ANY external service by creating an ``http`` connection + a
grant + a node that emits an ``authenticated_external_call`` packet. The
platform holds ZERO per-channel code: a brand-new API works identically with no
platform change. These tests prove:

  * a generic call succeeds with the EXACT wire request the packet described;
  * the credential is applied INSIDE the broker worker (resolved from the vault),
    never carried by the packet or the effector — credential-blindness;
  * an endpoint outside credential scope never receives the credential;
  * a SECOND, arbitrary service (different host + a different auth scheme) works
    with NO new module code — plug-and-play;
  * a grant bound to another universe is refused (isolation);
  * the module names NO channel.

Test seams mirror the project's own split (``test_outbound_connection_ledger``):
the credential-blind SPAWNED-worker path is exercised for real to prove the
credential is resolved by the worker, while the successful loopback WIRE-REQUEST
assertions run the REAL broker dispatch + REAL general vault resolver + REAL
SSRF driver in-process with an injected loopback socket — because the spawned
child re-imports with production SSRF seams (https-only, global-address-only,
TLS-verified), a monkeypatch cannot cross the spawn boundary, so a loopback can
never be reached through it. The two together cover the whole contract.
"""

from __future__ import annotations

import base64 as _b64
import hashlib
import http.server
import json
import socket
import ssl
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from tinyassets.broker.vault import write_credential_vault
from tinyassets.credential_vault import custody_namespace
from tinyassets.effectors import authenticated_external_call as aec
from tinyassets.effectors.authenticated_external_call import (
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
    run_authenticated_external_call_effector,
)
from tinyassets.storage import outbound_connections as _oc
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    _build_credential_broker_dispatch,
    _SsrfHardenedHttpDriver,
)

_HTTP_FLAG = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"


@pytest.fixture(autouse=True)
def _the_owners_rules_allow(_signed_in_operator, monkeypatch):
    """Bind the connection fixture's owner; the rules decide, nothing reviews."""
    from tinyassets import policy_rules
    from tinyassets.auth.middleware import identity_context

    monkeypatch.setattr(policy_rules, "configured_agents", lambda _home: {"main"})
    monkeypatch.setattr(policy_rules, "classify", lambda *_: ("app.write", "POST"))
    monkeypatch.setattr(policy_rules, "decide", lambda *_a, **_kw:
                        policy_rules.Decision(policy_rules.DO, None, "test_policy"))

    with identity_context(replace(_signed_in_operator, user_id="user-1")):
        yield


# --------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------- #
def _setup(
    tmp_path,
    *,
    universe_id="universe-1",
    provider="http",
    destination="api.example.com",
    auth_scheme="bearer",
    token="real-vault-http-token",
    vault_name="conn-secret",
    endpoints=None,
    scopes=("POST",),
    grant_universe=None,
    grant_consent_for=True,
):
    """REAL vault http record + http connection + per-universe grant."""
    data_root = tmp_path
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(data_root, principal="user-1", center=universe_id)
    from tinyassets.daemon_server import grant_universe_ownership

    grant_universe_ownership(data_root, universe_id=universe_id, owner_id="user-1")
    universe_dir = data_root / universe_id
    write_credential_vault(
        custody_namespace(data_root, "user-1"),
        [{"credential_type": "http", "destination": vault_name, "token": token}],
        owner_user_id="user-1",
    )
    db_path = data_root / ".broker" / "outbound.db"
    ledger = ConnectionLedger(
        db_path, data_root=data_root, verify_authenticated_principal=lambda: "user-1"
    )
    ledger.create_connection(
        connection_id="conn-http",
        owner_user_id="user-1",
        connection_class="outbound-http",
        scopes=scopes,
        provider=provider,
        destination=destination,
        credential_ref=f"vault://http/{vault_name}",
        connection_type="http",
        auth_scheme=auth_scheme,
        allowed_endpoints=endpoints
        or [{"host": destination, "path_template": "/v1/messages", "methods": ["POST"]}],
    )
    ledger.grant_connection(
        grant_id="grant-http",
        connection_id="conn-http",
        owner_user_id="user-1",

    )
    if grant_consent_for:
        # A connection grant alone is NOT sufficient to fire an effect; a live
        # authenticated_external_call also requires an active effector-consent
        # grant for the destination (soul authority is UNDECLARED here, which
        # passes the soul gate). Negative tests pass grant_consent_for=False.
        from tinyassets.storage.effector_consents import grant_consent

        grant_consent(
            universe_dir,
            sink=EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
            destination=destination,
            granted_by="test",
        )
    return data_root, universe_dir, db_path


class _Loopback:
    """A recording loopback HTTP endpoint the injected driver connects to."""

    def __init__(self, responder=None):
        self.recorded: list[dict] = []
        recorded = self.recorded
        responder = responder or (lambda method, path, body: b'{"ok": true}')

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # noqa: ANN001, ANN002
                return

            def _handle(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                recorded.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k: v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                payload = responder(self.command, self.path, body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle  # noqa: N815
            do_POST = _handle  # noqa: N815
            do_PUT = _handle  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _install_loopback_driver(monkeypatch, port):
    """Point the (module-global) SSRF driver at the loopback socket."""

    class _PassThroughTLS:
        def __init__(self):
            self.verify_mode = ssl.CERT_NONE
            self.check_hostname = False

        def wrap_socket(self, sock, server_hostname=None):  # noqa: ANN001
            return sock

    def open_socket(_address, timeout, _src):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    def factory(*_a, **_k):
        return _SsrfHardenedHttpDriver(
            resolver=lambda _h, _p: ["127.0.0.1"],
            validator=lambda addr: addr,
            open_socket=open_socket,
            ssl_context=_PassThroughTLS(),
            allowed_ports=frozenset({443}),
        )

    monkeypatch.setattr(_oc, "_SsrfHardenedHttpDriver", factory)


def _install_inprocess_proxy(
    monkeypatch, *, db_path, universe_dir, grant_id, provider, destination, runtime_root
):
    """Substitute the effector's proxy seam with an in-process REAL broker.

    Builds the exact production dispatch (``_build_credential_broker_dispatch``)
    the spawned worker runs — REAL general vault resolver + REAL SSRF driver
    (loopback socket injected via :func:`_install_loopback_driver`, which MUST be
    installed first). Only the process spawn is elided so the loopback is
    reachable and the exact wire request can be asserted.
    """
    config = {
        "allow_test_fixtures": False,
        "allow_http_connections": True,
        "ledger_db_path": str(Path(db_path).resolve()),
        "universe_dir": str(custody_namespace(Path(universe_dir).parent, "user-1")),
        "provider": provider,
        "destination": destination,
        "connection_type": "http",
        "owner_user_id": "user-1",
        "runtime_root": str(Path(runtime_root).resolve()),
    }
    dispatch = _build_credential_broker_dispatch(config)

    class _InProcessProxy:
        def __init__(self):
            self.closed = False

        def request(self, verb, request, **_policy):
            return dispatch(grant_id, verb, request)

        def close(self):
            self.closed = True

    proxy = _InProcessProxy()
    monkeypatch.setattr(aec, "_open_connection_proxy", lambda **_k: proxy)
    return proxy


def _read_audit(data_root, grant_id):
    runtime_id = hashlib.sha256(grant_id.encode("utf-8")).hexdigest()
    path = Path(data_root) / ".broker" / ".outbound-proxy" / runtime_id / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# (a) + (b) success with exact wire request + worker-applied credential
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Plug-and-play: a SECOND arbitrary service with NO new platform code
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Served broker: credential resolved by the broker; effector stays blind
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Isolation: a grant bound to another universe cannot be borrowed
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Shape guards + declaration + static "no channel" proof
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Authorization gates (Codex reject 2026-08-20): a connection grant ALONE must
# NOT fire an effect — soul authority must not DENY, and effector consent for
# the destination must be active. These prove the generic effector keeps the
# gates the deleted per-channel effectors enforced.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Body transforms (openspec: external-call-body-encoding). Live 2026-08-29 a
# model-generated base64 came back `422 not valid Base64`, then as valid base64
# of a transcribed file (README +2/-87), then a "repair" that re-typed the file
# (+22/-14). The effector now owns the encoding and the byte-moving: a node
# writes text, references its own declared inputs, and references an EARLIER
# node's effect response in the same run. Operators are namespaced (`$ta.*`)
# so a user's own `$ref`/`$set` payloads are never hijacked (Codex round 1).
# --------------------------------------------------------------------------- #


def _packet(body, *, verb="POST", path="/v1/messages"):
    return {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": verb,
        "request": {"method": verb, "path": path, "body": body},
    }


def _wire(tmp_path, monkeypatch, run_state, *, responder=None, allowed=None, prior=None):
    """Dispatch through the REAL broker + SSRF driver to a loopback; return
    (evidence, recorded requests)."""
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)
    loop = _Loopback(responder)
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir, grant_id="grant-http",
        provider="http", destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="n1", output_keys=["out"], run_state=run_state,
            base_path=str(universe_dir), run_id="r1",
            allowed_state_keys=allowed, prior_effects=prior,
        )
    finally:
        loop.stop()
    return evidence, loop.recorded


def _contents_responder(original: str, sha: str):
    """A contents-API-shaped GET reply: 60-column-wrapped base64, as such APIs send."""
    wrapped = _b64.b64encode(original.encode()).decode()
    wrapped = "\n".join(wrapped[i:i + 60] for i in range(0, len(wrapped), 60)) + "\n"

    def responder(method, path, body):
        if method == "GET":
            return json.dumps({"path": "README.md", "sha": sha, "content": wrapped,
                               "encoding": "base64"}).encode()
        return b'{"ok": true}'

    return responder


def _seed_engine_admission(tmp_path, monkeypatch, run_id, universe_id="universe-1"):
    from tinyassets import engine_admissions as adm

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    db = tmp_path / adm.LEDGER_NAME
    ticket = adm.admit(universe_id, db=db)
    assert adm._is_ticket(ticket)
    assert adm.attach_run(ticket, run_id, db=db)
    return db


def _admission_kind(db, run_id):
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT kind FROM admissions WHERE run_id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


def _run_one(tmp_path, monkeypatch, *, verb, run_id, packet_body=None, refuse_grant=False,
             request_method=None):
    import types

    from tinyassets import effectors as effectors_pkg

    monkeypatch.setenv(_HTTP_FLAG, "1")
    _, universe_dir, db_path = _setup(
        tmp_path, scopes=("GET", "PUT"), grant_consent_for=not refuse_grant, endpoints=[
            {"host": "api.example.com", "path_template": "/v1/messages", "methods": ["GET", "PUT"]},
        ])
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir, grant_id="grant-http",
        provider="http", destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    packet = _packet(packet_body, verb=verb, path="/v1/messages")
    if packet_body is None:
        del packet["request"]["body"]
    if request_method:
        packet["request"]["method"] = request_method
    node = types.SimpleNamespace
    branch = node(node_defs=[node(node_id="n1", effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
                                  output_keys=["pkt"], input_keys=[])], state_schema=None)
    try:
        evidence = effectors_pkg.run_effects_for_branch(
            branch=branch, run_state={"pkt": json.dumps(packet)},
            base_path=str(universe_dir), run_id=run_id,
        )
    finally:
        loop.stop()
    return evidence, loop


from tests.support.egress import admitted_egress  # noqa: E402, F401
