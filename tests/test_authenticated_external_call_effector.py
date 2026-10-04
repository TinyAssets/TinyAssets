"""The ONE generic channel primitive — proof the shape is right.

A universe wires ANY external service by creating an ``http`` connection + a
grant + a node that emits an ``authenticated_external_call`` packet. The
platform holds ZERO per-channel code: a brand-new API works identically with no
platform change. These tests prove:

  * a generic call succeeds with the EXACT wire request the packet described;
  * the credential is applied INSIDE the broker worker (resolved from the vault),
    never carried by the packet or the effector — credential-blindness;
  * an out-of-allowlist path is refused before any socket opens;
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
from pathlib import Path

import pytest

from tinyassets import agent_review
from tinyassets.credential_vault import write_credential_vault
from tinyassets.effectors import authenticated_external_call as aec
from tinyassets.effectors.authenticated_external_call import (
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
    apply_body_transforms,
    run_authenticated_external_call_effector,
)
from tinyassets.storage import outbound_connections as _oc
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    _build_credential_broker_dispatch,
    _SsrfHardenedHttpDriver,
)

_HTTP_FLAG = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"


def _approve(prompt, system, role="writer"):
    return '{"verdict": "proceed", "reason": "test reviewer"}'


@pytest.fixture(autouse=True)
def _a_reviewer_that_approves():
    """A bound reviewer grants no authority and does not opt the owner in."""
    with agent_review.bound(_approve, active=True):
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
    universe_dir = data_root / universe_id
    write_credential_vault(
        universe_dir,
        [{"credential_type": "http", "destination": vault_name, "token": token}],
    )
    db_path = data_root / "outbound.db"
    ledger = ConnectionLedger(
        db_path, verify_authenticated_principal=lambda: "user-1"
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
        universe_id=grant_universe or universe_id,
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
        "universe_dir": str(Path(universe_dir).resolve()),
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

        def request(self, verb, request):
            return dispatch(grant_id, verb, request)

        def close(self):
            self.closed = True

    proxy = _InProcessProxy()
    monkeypatch.setattr(aec, "_open_connection_proxy", lambda **_k: proxy)
    return proxy


def _read_audit(data_root, grant_id):
    runtime_id = hashlib.sha256(grant_id.encode("utf-8")).hexdigest()
    path = Path(data_root) / ".outbound-proxy" / runtime_id / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# (a) + (b) success with exact wire request + worker-applied credential
# --------------------------------------------------------------------------- #
def test_generic_call_succeeds_with_exact_wire_request_and_worker_applied_credential(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    proxy = _install_inprocess_proxy(
        monkeypatch,
        db_path=db_path,
        universe_dir=universe_dir,
        grant_id="grant-http",
        provider="http",
        destination="api.example.com",
        runtime_root=tmp_path / "rt1",
    )

    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {"text": "hello world"},
        },
    }
    run_state = {"out": json.dumps(packet)}
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="n1",
            output_keys=["out"],
            run_state=run_state,
            base_path=str(universe_dir),
            run_id="r1",
        )
    finally:
        loop.stop()

    assert evidence["delivered"] is True
    assert evidence["response"]["status"] == 200
    assert evidence["url"] == "https://api.example.com/v1/messages"

    # (a) the EXACT wire request the packet described reached the destination.
    assert len(loop.recorded) == 1
    rec = loop.recorded[0]
    assert rec["method"] == "POST"
    assert rec["path"] == "/v1/messages"
    assert json.loads(rec["body"]) == {"text": "hello world"}

    # (b) the credential was applied BY THE WORKER, resolved from the vault —
    # the packet and the effector evidence never carried it.
    assert rec["headers"]["Authorization"] == "Bearer real-vault-http-token"
    assert "real-vault-http-token" not in json.dumps(packet)
    assert "real-vault-http-token" not in json.dumps(evidence)
    assert proxy.closed is True  # the effector always tears the proxy down


def test_owner_allowed_issue_post_reaches_broker_without_a_review(tmp_path, monkeypatch):
    from tinyassets import agent_rules

    monkeypatch.setenv(_HTTP_FLAG, "1")
    path = "/repos/owner/project/issues"
    _, universe, db = _setup(tmp_path, destination="api.github.com", endpoints=[{
        "host": "api.github.com", "path_template": path, "methods": ["POST"]}])
    agent_rules.set_rule(universe, "app.write", agent_rules.DO,
                         connection="conn-http", operation="POST")
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=universe,
                             grant_id="grant-http", provider="http",
                             destination="api.github.com", runtime_root=tmp_path / "rt")
    packet = {"sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
              "connection_id": "conn-http", "grant_id": "grant-http", "verb": "POST",
              "request": {"path": path, "body": {"title": "Owner's issue"}}}
    try:
        with agent_review.bound(None, active=False):
            result = run_authenticated_external_call_effector(
                node_id="n", output_keys=["out"], run_state={"out": json.dumps(packet)},
                base_path=str(universe), run_id="r")
    finally:
        loop.stop()
    assert result.get("delivered"), result
    assert len(loop.recorded) == 1 and loop.recorded[0]["path"] == path


@pytest.mark.parametrize("origin", ["live", "session", "unknown", "other-universe", "bad-session"])
def test_custom_agent_review_holds_external_post(tmp_path, monkeypatch, origin):
    """An owner opt-in must survive the real effector, including lost provenance."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from tinyassets import engine_steering, turn_interrupt
    from tinyassets.auth import middleware

    _, universe, _ = _setup(tmp_path, grant_consent_for=False)
    agent_review.set_review(universe, "app.write", True, agent="researcher")
    # No custom rule row: the fallback must also find review-only agent settings.
    # Packet/state fields cannot impersonate main to evade researcher's check.
    packet = _packet({"message": "hello"})
    packet["agent_id"] = "main"
    session = {"session": "thread:agent:researcher:principal:user-1",
               "bad-session": "thread:agent:main:principal:another-owner"}.get(origin, "")
    monkeypatch.setattr(engine_steering, "_session_key", lambda: session)
    monkeypatch.setattr(middleware, "current_identity_or_none",
                        lambda: SimpleNamespace(user_id="user-1"))
    context = (turn_interrupt.interactive_turn(
        "user-1", "another-universe" if origin == "other-universe" else universe.name,
        agent_id="researcher" if origin == "live" else "main")
        if origin in {"live", "other-universe"} else nullcontext())
    with context, agent_review.bound(None, active=False):
        result = run_authenticated_external_call_effector(
            node_id="n", output_keys=["out"], run_state={"out": packet, "agent_id": "main"},
            base_path=universe, run_id="r")
    assert result.get("error_kind") == "auto_review_unavailable", result


@pytest.mark.parametrize("setting", ["review", "rule"])
def test_custom_agent_settings_do_not_leak_from_main(tmp_path, monkeypatch, setting):
    from tinyassets import agent_rules, turn_interrupt

    _, universe, _ = _setup(tmp_path, grant_consent_for=False)
    if setting == "review":
        agent_review.set_review(universe, "app.write", True)
    else:
        agent_rules.set_rule(universe, "app.write", agent_rules.ASK_FIRST)
    with turn_interrupt.interactive_turn("user-1", universe.name, agent_id="researcher"), \
            agent_review.bound(None, active=False):
        result = run_authenticated_external_call_effector(
            node_id="n", output_keys=["out"], run_state={"out": _packet({})},
            base_path=universe, run_id="r")
    assert result.get("error_kind") == "missing_consent", result


@pytest.mark.parametrize("agent", ["researcher", None])
def test_custom_agent_rule_holds_external_post(tmp_path, agent):
    from tinyassets import agent_rules, turn_interrupt

    _, universe, _ = _setup(tmp_path, grant_consent_for=False)
    agent_rules.set_rule(universe, "app.write", agent_rules.ASK_FIRST, agent="researcher")
    live = turn_interrupt.LiveTurn("user-1", universe.name, agent) if agent else None
    with turn_interrupt.bound(live):
        result = run_authenticated_external_call_effector(
            node_id="n", output_keys=["out"], run_state={"out": _packet({})},
            base_path=universe, run_id="r")
    assert result.get("error_kind") == "rule_ask_first", result


def test_owner_door_opt_in_holds_researcher_post(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from tests.test_agent_rules import _Request
    from tinyassets import addressed_agents, onboarding, turn_interrupt
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    _, universe, _ = _setup(tmp_path, grant_consent_for=False)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="user-1"))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **_kw: universe.name)

    def resolve(_base, *, universe_id, owner, agent_id):
        assert (universe_id, owner, agent_id) == (universe.name, "user-1", "researcher")
        return SimpleNamespace(agent_id="researcher")

    monkeypatch.setattr(addressed_agents, "resolve", resolve)
    response = asyncio.run(onboarding._handle_rules(_Request("POST", {
        "agent_id": "researcher", "review": {"action_class": "app.write", "enabled": True}})))
    assert response.status_code == 200
    assert json.loads(response.body)["review_on"] == ["app.write"]
    assert agent_review.switched_on(universe, "main") == set()
    with turn_interrupt.interactive_turn("user-1", universe.name, agent_id="researcher"), \
            agent_review.bound(None, active=False):
        result = run_authenticated_external_call_effector(
            node_id="n", output_keys=["out"], run_state={"out": _packet({})},
            base_path=universe, run_id="r")
    assert result.get("error_kind") == "auto_review_unavailable", result


# --------------------------------------------------------------------------- #
# (c) out-of-allowlist path is refused BEFORE any socket
# --------------------------------------------------------------------------- #
def test_out_of_allowlist_path_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)  # allowlist: only /v1/messages
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch,
        db_path=db_path,
        universe_dir=universe_dir,
        grant_id="grant-http",
        provider="http",
        destination="api.example.com",
        runtime_root=tmp_path / "rt3",
    )

    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"path": "/v1/secret-exfil", "body": {"x": 1}},
    }
    run_state = {"out": json.dumps(packet)}
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="n",
            output_keys=["out"],
            run_state=run_state,
            base_path=str(universe_dir),
        )
    finally:
        loop.stop()

    assert evidence.get("delivered") is not True
    assert evidence["error_kind"] == "outbound_request_failed"
    # Refused at the allowlist BEFORE any socket — nothing reached the endpoint.
    assert loop.recorded == []
    assert "real-vault-http-token" not in json.dumps(evidence)


# --------------------------------------------------------------------------- #
# Plug-and-play: a SECOND arbitrary service with NO new platform code
# --------------------------------------------------------------------------- #
def test_second_arbitrary_channel_works_with_zero_platform_code(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    # A service the platform has never heard of — different host, different auth
    # scheme (custom header instead of bearer). No module code changes.
    data_root, universe_dir, db_path = _setup(
        tmp_path,
        provider="http",
        destination="api.brand-new-service.test",
        auth_scheme="header",
        token="other-secret",
        vault_name="other-conn",
        endpoints=[
            {
                "host": "api.brand-new-service.test",
                "path_template": "/send",
                "methods": ["POST"],
            }
        ],
    )
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch,
        db_path=db_path,
        universe_dir=universe_dir,
        grant_id="grant-http",
        provider="http",
        destination="api.brand-new-service.test",
        runtime_root=tmp_path / "rt2",
    )

    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {
            "path": "/send",
            "header_name": "X-Api-Key",
            "body": {"msg": "hi"},
        },
    }
    run_state = {"o": json.dumps(packet)}
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="n",
            output_keys=["o"],
            run_state=run_state,
            base_path=str(universe_dir),
        )
    finally:
        loop.stop()

    assert evidence["delivered"] is True
    rec = loop.recorded[0]
    assert rec["method"] == "POST"
    assert rec["path"] == "/send"
    # The DIFFERENT auth scheme applied the credential in the DIFFERENT header.
    assert rec["headers"]["X-Api-Key"] == "other-secret"
    assert "Authorization" not in rec["headers"]
    assert "other-secret" not in json.dumps(evidence)


# --------------------------------------------------------------------------- #
# Real spawned worker: credential resolved by the worker; effector stays blind
# --------------------------------------------------------------------------- #
def test_real_spawned_worker_resolves_credential_and_stays_blind(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)

    # A non-allowlisted host: the REAL spawned worker RESOLVES the credential from
    # the vault, then the allowlist refuses the destination — proving the worker
    # (not the effector) applied the credential. NO _open_connection_proxy or
    # driver monkeypatch: this is the genuine spawned-worker path.
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"host": "not-allowed.example", "path": "/v1/messages", "body": {"x": 1}},
    }
    run_state = {"out": json.dumps(packet)}
    evidence = run_authenticated_external_call_effector(
        node_id="n",
        output_keys=["out"],
        run_state=run_state,
        base_path=str(universe_dir),
    )

    assert evidence["error_kind"] == "outbound_request_failed"
    assert "real-vault-http-token" not in json.dumps(evidence)

    audit = _read_audit(data_root, "grant-http")
    # "destination request failed" (reached the driver) NOT "credential
    # unavailable" — the worker DID resolve the vault credential.
    assert [r["reason"] for r in audit] == ["destination request failed"]
    assert "real-vault-http-token" not in json.dumps(audit)


# --------------------------------------------------------------------------- #
# Isolation: a grant bound to another universe cannot be borrowed
# --------------------------------------------------------------------------- #
def test_grant_from_another_universe_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(
        tmp_path, universe_id="universe-1", grant_universe="universe-1"
    )
    other_dir = data_root / "universe-2"
    other_dir.mkdir(exist_ok=True)

    def _must_not_open(**_k):
        raise AssertionError("proxy must not open for a cross-universe grant")

    monkeypatch.setattr(aec, "_open_connection_proxy", _must_not_open)

    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"path": "/v1/messages", "body": {"x": 1}},
    }
    run_state = {"out": json.dumps(packet)}
    evidence = run_authenticated_external_call_effector(
        node_id="n",
        output_keys=["out"],
        run_state=run_state,
        base_path=str(other_dir),  # running universe-2; grant is bound to universe-1
    )
    assert evidence["error_kind"] == "grant_not_for_universe"


# --------------------------------------------------------------------------- #
# Shape guards + declaration + static "no channel" proof
# --------------------------------------------------------------------------- #
def test_missing_packet_returns_no_matching_packet(tmp_path):
    evidence = run_authenticated_external_call_effector(
        node_id="n",
        output_keys=["out"],
        run_state={"out": "not a packet"},
        base_path=str(tmp_path / "universe-1"),
    )
    assert evidence["error_kind"] == "no_matching_packet"


def test_method_that_disagrees_with_verb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)

    monkeypatch.setattr(
        aec, "_open_connection_proxy", lambda **_k: (_ for _ in ()).throw(AssertionError)
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"method": "GET", "path": "/v1/messages"},
    }
    evidence = run_authenticated_external_call_effector(
        node_id="n",
        output_keys=["out"],
        run_state={"out": json.dumps(packet)},
        base_path=str(universe_dir),
    )
    assert evidence["error_kind"] == "method_mismatch"


def test_ambiguous_host_without_explicit_host_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(
        tmp_path,
        endpoints=[
            {"host": "api-a.test", "path_template": "/v1/messages", "methods": ["POST"]},
            {"host": "api-b.test", "path_template": "/v1/messages", "methods": ["POST"]},
        ],
    )
    monkeypatch.setattr(
        aec, "_open_connection_proxy", lambda **_k: (_ for _ in ()).throw(AssertionError)
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"path": "/v1/messages"},  # no host, and two are allowlisted
    }
    evidence = run_authenticated_external_call_effector(
        node_id="n",
        output_keys=["out"],
        run_state={"out": json.dumps(packet)},
        base_path=str(universe_dir),
    )
    assert evidence["error_kind"] == "host_ambiguous"


def test_sink_is_registered_and_dispatchable_from_a_branch():
    # A node that declares effects=[the sink] routes to THIS effector via the
    # channel-agnostic dispatch map in effectors/__init__ — the "declaration"
    # mechanism (no per-channel allowlist, no github registry).
    from types import SimpleNamespace

    from tinyassets import effectors as effectors_pkg

    assert effectors_pkg.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL == (
        EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL
    )
    assert hasattr(effectors_pkg, "run_authenticated_external_call_effector")

    # A branch whose node declares the sink routes to the generic effector and
    # comes back with per-sink evidence. With no packet/connection set up the
    # effector returns a structured error — but NOT "unknown_sink", which proves
    # the dispatch matched the sink to the generic effector (routing, not success).
    branch = SimpleNamespace(
        node_defs=[
            SimpleNamespace(
                node_id="n1",
                output_keys=["out"],
                effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
            )
        ]
    )
    evidence = effectors_pkg.run_effects_for_branch(
        review_provider=_approve,
        branch=branch,
        run_state={},
        base_path=None,
        run_id="r1",
    )
    assert EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL in evidence["n1"]
    assert (
        evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL].get("error_kind")
        != "unknown_sink"
    )


def test_module_names_no_channel():
    src = Path(aec.__file__).read_text(encoding="utf-8").lower()
    for banned in (
        "github",
        "slack",
        "twitter",
        "x.com",
        "tweet",
        "pull_request",
        "postmessage",
        "anthropic",
        "openai",
    ):
        assert banned not in src, f"channel-specific token {banned!r} leaked in"


# --------------------------------------------------------------------------- #
# Authorization gates (Codex reject 2026-08-20): a connection grant ALONE must
# NOT fire an effect — soul authority must not DENY, and effector consent for
# the destination must be active. These prove the generic effector keeps the
# gates the deleted per-channel effectors enforced.
# --------------------------------------------------------------------------- #
def test_missing_consent_refuses_before_any_egress(tmp_path, monkeypatch):
    # A connection grant WITHOUT an effector-consent grant is refused at the
    # authorization gate — BEFORE the worker opens, so nothing reaches the wire.
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path, grant_consent_for=False)
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch,
        db_path=db_path,
        universe_dir=universe_dir,
        grant_id="grant-http",
        provider="http",
        destination="api.example.com",
        runtime_root=tmp_path / "rt-noconsent",
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"method": "POST", "path": "/v1/messages", "body": {"text": "x"}},
    }
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="n1",
            output_keys=["out"],
            run_state={"out": json.dumps(packet)},
            base_path=str(universe_dir),
            run_id="r1",
        )
    finally:
        loop.stop()
    assert evidence["error_kind"] == "missing_consent"
    assert evidence.get("dry_run") is True
    assert evidence.get("delivered") is not True
    # Decisive proof: NOTHING reached the destination.
    assert loop.recorded == []


def test_revoked_consent_refuses_the_call(tmp_path, monkeypatch):
    # Consent granted then revoked must close the gate — an active connection
    # grant must NOT bypass a revoked destination consent.
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path)  # grants consent
    from tinyassets.storage.effector_consents import revoke_consent

    revoke_consent(
        universe_dir,
        sink=EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        destination="api.example.com",
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"method": "POST", "path": "/v1/messages", "body": {"text": "x"}},
    }
    evidence = run_authenticated_external_call_effector(
        node_id="n1",
        output_keys=["out"],
        run_state={"out": json.dumps(packet)},
        base_path=str(universe_dir),
        run_id="r1",
    )
    assert evidence["error_kind"] == "missing_consent"


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


def test_base64_transform_is_encoded_by_the_effector_never_the_model(tmp_path, monkeypatch):
    text = "line one\nline two \u2014 with unicode\n"
    packet = _packet({"message": "m", "content": {"$ta.base64": text}, "branch": "b"})
    evidence, recorded = _wire(tmp_path, monkeypatch, {"out": json.dumps(packet)})
    assert evidence["delivered"] is True
    sent = json.loads(recorded[0]["body"])
    encoded = _b64.b64encode(text.encode()).decode()
    assert sent == {"message": "m", "content": encoded, "branch": "b"}
    assert "$ta." not in recorded[0]["body"].decode()


def test_a_users_own_dollar_keys_pass_through_untouched(tmp_path, monkeypatch):
    """JSON Schema's `$ref`, Mongo's `$set`: not ours, never touched (P1)."""
    body = {"schema": {"$ref": "#/$defs/X"}, "update": {"$set": {"a": 1}}, "$comment": "hi"}
    out, err = apply_body_transforms(body, {})
    assert err is None and out is body
    packet = _packet(body)
    evidence, recorded = _wire(tmp_path, monkeypatch, {"out": json.dumps(packet)})
    assert evidence["delivered"] is True
    assert json.loads(recorded[0]["body"]) == body


def test_ref_is_fenced_to_the_nodes_declared_keys():
    """A packet reads only what its node was allowed to see (P0): declared
    input_keys plus schema-defaulted keys, never the whole final state."""
    state = {"private": "UNDECLARED", "note": json.dumps({"k": "v"})}
    leak = {"leak": {"$ta.ref": "private"}}
    out, err = apply_body_transforms(leak, state, allowed_state_keys=["note"])
    assert out is None and "not among the node's declared input_keys" in err
    assert "UNDECLARED" not in err
    ok = {"x": {"$ta.ref": "note.k"}}
    out, err = apply_body_transforms(ok, state, allowed_state_keys=["note"])
    assert (out, err) == ({"x": "v"}, None)
    # No fence given at all (a legacy caller): nothing is readable.
    out, err = apply_body_transforms({"x": {"$ta.ref": "note.k"}}, state)
    assert out is None and "declares no readable state keys" in err


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


def test_append_one_line_chains_a_fetch_effect_into_the_write_in_one_run(tmp_path, monkeypatch):
    """THE live case (P0): two nodes in one branch. `fetch` GETs the file;
    `write` PUTs it back with one appended line, referencing the fetch's
    response through `$ta.effect`. The model authored only the new line, and
    the bytes never passed through a model or a second run_graph."""
    import types

    from tinyassets import effectors as effectors_pkg

    original = "# Title\n\n**Bold** para with `code`, an email a.b@c.d and \\Scripts\\path.\n"
    original += "".join(
        f"line {i} of a file longer than the evidence preview\n" for i in range(200)
    )
    new_line = "Patches by this universe are opened through the request rail.\n"
    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path, scopes=("GET", "PUT"), endpoints=[
        {"host": "api.example.com", "path_template": "/v1/messages", "methods": ["GET", "PUT"]},
    ])
    loop = _Loopback(_contents_responder(original, "blob-sha-1"))
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir, grant_id="grant-http",
        provider="http", destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    fetch_packet = _packet(None, verb="GET", path="/v1/messages")
    del fetch_packet["request"]["body"]
    write_packet = _packet({
        "message": "docs: append",
        "sha": {"$ta.effect": "fetch.response.body.sha"},
        "content": {"$ta.base64": {"$ta.concat": [
            {"$ta.from_base64": {"$ta.effect": "fetch.response.body.content"}}, new_line,
        ]}},
    }, verb="PUT", path="/v1/messages")
    node = types.SimpleNamespace
    branch = node(
        node_defs=[
            node(node_id="fetch", effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
                 output_keys=["fetch_packet"], input_keys=[]),
            node(node_id="write", effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
                 output_keys=["write_packet"], input_keys=[]),
        ],
        state_schema=None,
    )
    run_state = {"fetch_packet": json.dumps(fetch_packet), "write_packet": json.dumps(write_packet)}
    try:
        evidence = effectors_pkg.run_effects_for_branch(
            review_provider=_approve,
            branch=branch, run_state=run_state, base_path=str(universe_dir), run_id="r1",
        )
    finally:
        loop.stop()
    sink = EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL
    assert evidence["fetch"][sink]["delivered"] is True, evidence["fetch"]
    assert evidence["write"][sink]["delivered"] is True, evidence["write"]
    assert [r["method"] for r in loop.recorded] == ["GET", "PUT"]
    sent = json.loads(loop.recorded[1]["body"])
    assert sent["sha"] == "blob-sha-1"
    assert _b64.b64decode(sent["content"]).decode() == original + new_line   # exact bytes
    assert "$ta." not in loop.recorded[1]["body"].decode()
    # The PERSISTED fetch evidence is a bounded preview (what read_graph shows
    # a model later), while the chain above saw the full body (Codex round 2).
    persisted = evidence["fetch"][sink]["response"]
    assert persisted["body_truncated"] is True and len(persisted["body"]) == 4096
    assert persisted["body_chars"] > 4096 and len(persisted["body_sha256"]) == 64
    # ...and the preview says how to use the whole body, naming THIS node
    # (live 2026-08-30: a universe met the preview and looped on it).
    assert '{"$ta.effect": "fetch.response.body"}' in persisted["body_hint"]
    assert "one run" in persisted["body_hint"] and "run_graph inputs" in persisted["body_hint"]


def test_effect_reference_sees_only_earlier_nodes(tmp_path, monkeypatch):
    """`write` listed BEFORE `fetch` cannot reference it: effects fire in node
    order and a reference to a later node is refused, nothing sent."""
    import types

    from tinyassets import effectors as effectors_pkg

    monkeypatch.setenv(_HTTP_FLAG, "1")
    data_root, universe_dir, db_path = _setup(tmp_path, scopes=("GET", "PUT"), endpoints=[
        {"host": "api.example.com", "path_template": "/v1/messages", "methods": ["GET", "PUT"]},
    ])
    loop = _Loopback(_contents_responder("x\n", "s"))
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir, grant_id="grant-http",
        provider="http", destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    write_packet = _packet({"sha": {"$ta.effect": "fetch.response.body.sha"}}, verb="PUT")
    fetch_packet = _packet(None, verb="GET")
    del fetch_packet["request"]["body"]
    node = types.SimpleNamespace
    branch = node(node_defs=[
        node(node_id="write", effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
             output_keys=["write_packet"], input_keys=[]),
        node(node_id="fetch", effects=[EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL],
             output_keys=["fetch_packet"], input_keys=[]),
    ], state_schema=None)
    run_state = {"fetch_packet": json.dumps(fetch_packet), "write_packet": json.dumps(write_packet)}
    try:
        evidence = effectors_pkg.run_effects_for_branch(
            review_provider=_approve,
            branch=branch, run_state=run_state, base_path=str(universe_dir), run_id="r1",
        )
    finally:
        loop.stop()
    sink = EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL
    assert evidence["write"][sink]["error_kind"] == "invalid_body_transform"
    assert "no earlier node 'fetch'" in evidence["write"][sink]["error"]
    assert [r["method"] for r in loop.recorded] == ["GET"]           # only the fetch went out


def test_malformed_transforms_are_refused_and_nothing_is_sent(tmp_path, monkeypatch):
    cases = {
        "not text": {"content": {"$ta.base64": 42}},
        "not base64": {"content": {"$ta.from_base64": "this is not base64!!"}},
        "missing ref": {"content": {"$ta.ref": "fetched.missing"}},
        "concat not list": {"content": {"$ta.concat": "x"}},
        "ref into text": {"content": {"$ta.ref": "note.deeper"}},
        "unknown effect": {"content": {"$ta.effect": "nobody.response"}},
    }
    for index, (label, body) in enumerate(cases.items()):
        run_state = {
            "fetched": json.dumps({"content": "aGk="}), "note": "plain",
            "out": json.dumps(_packet(body)),
        }
        case_root = tmp_path / f"case{index}"
        case_root.mkdir()
        evidence, recorded = _wire(
            case_root, monkeypatch, run_state, allowed=["fetched", "note"],
        )
        assert evidence.get("error_kind") == "invalid_body_transform", (label, evidence)
        assert recorded == [], label
        assert "aGk=" not in json.dumps(evidence), label   # no values in the error


def test_bodies_without_transforms_are_the_same_object():
    body = {"text": "hello", "nested": {"k": ["v", 1, None]}, "$notatransform": {"a": 1, "b": 2}}
    out, err = apply_body_transforms(body, {})
    assert err is None and out is body
    assert apply_body_transforms("raw string", {}) == ("raw string", None)
    assert apply_body_transforms(None, {}) == (None, None)


def test_ref_traverses_json_strings_lists_and_nested_transforms():
    state = {"resp": json.dumps({"items": [{"id": "a"}, {"id": "b"}], "content": "aGVsbG8="})}
    allowed = ["resp"]
    picked = apply_body_transforms(
        {"$ta.ref": "resp.items.1.id"}, state, allowed_state_keys=allowed,
    )
    assert picked == ("b", None)
    decoded = apply_body_transforms(
        {"$ta.from_base64": {"$ta.ref": "resp.content"}}, state, allowed_state_keys=allowed,
    )
    assert decoded == ("hello", None)
    assert apply_body_transforms(
        {"$ta.base64": {"$ta.concat": [{"$ta.from_base64": {"$ta.ref": "resp.content"}}, "!"]}},
        state, allowed_state_keys=allowed,
    ) == (_b64.b64encode(b"hello!").decode(), None)
    _out, err = apply_body_transforms(
        {"$ta.ref": "resp.items.9.id"}, state, allowed_state_keys=allowed,
    )
    assert err and "out of range" in err


def test_depth_and_size_bounds_are_stated_and_enforced():
    from tinyassets.effectors import authenticated_external_call as mod

    nested = "x"
    for _ in range(mod._MAX_TRANSFORM_DEPTH + 2):
        nested = {"$ta.concat": [nested]}
    _out, err = apply_body_transforms({"c": nested}, {})
    assert err and "nested deeper than" in err
    big = "a" * (mod._MAX_TRANSFORMED_BODY_BYTES + 10)
    _out, err = apply_body_transforms({"content": {"$ta.base64": big}}, {})
    assert err and "the bound is" in err
    small = apply_body_transforms({"content": {"$ta.base64": "ok"}}, {})
    assert small == ({"content": _b64.b64encode(b"ok").decode()}, None)


def test_effect_references_cannot_reach_headers(tmp_path, monkeypatch):
    """Codex round 2 (P0): the worker returns every response header; a
    `set-cookie` must not be forwardable through `$ta.effect`."""
    prior = {"fetch": {"delivered": True, "response": {
        "status": 200, "headers": {"set-cookie": "session=rotated-secret"},
        "body": json.dumps({"sha": "s", "content": "aGk="}),
    }}}
    out, err = apply_body_transforms(
        {"c": {"$ta.effect": "fetch.response.headers.set-cookie"}}, {}, prior_effects=prior,
    )
    assert out is None and "only response.body and response.status" in err
    assert "rotated-secret" not in err
    ok = apply_body_transforms(
        {"s": {"$ta.effect": "fetch.response.status"},
         "h": {"$ta.effect": "fetch.response.body.sha"}},
        {}, prior_effects=prior,
    )
    assert ok == ({"s": 200, "h": "s"}, None)
    _out, err = apply_body_transforms(
        {"d": {"$ta.effect": "fetch.delivered"}}, {}, prior_effects=prior,
    )
    assert err and "only response.body and response.status" in err


def test_unknown_reserved_spellings_are_refused_not_sent():
    out, err = apply_body_transforms({"content": {"$ta.bas64": "typo"}}, {})
    assert out is None and "unknown transform '$ta.bas64'" in err
    # ...even when buried and even without any real transform present
    out, err = apply_body_transforms({"a": [{"b": {"$ta.nope": 1}}]}, {})
    assert out is None and "unknown transform" in err


def test_deep_plain_bodies_are_refused_not_crashed():
    """Codex round 2 (P1): 1,100 nested plain dicts used to raise RecursionError
    in the scan, surfacing as effector_crashed instead of a refusal."""
    body = {"leaf": 1}
    for _ in range(1100):
        body = {"wrap": body}
    out, err = apply_body_transforms(body, {})
    assert out is None and "nested deeper than 32" in err


def test_the_working_set_budget_refuses_a_repeated_reference_early(monkeypatch):
    """Codex round 2 (P0): a hundred references to a 5 MiB blob allocated
    ~1 GiB before the old size check. Charged as produced, the copies are
    refused as soon as the charges cross the budget. Scaled: budget 1000
    bytes, blob 400 bytes, referenced 3x -> refused at the third charge."""
    from tinyassets.effectors import authenticated_external_call as mod

    monkeypatch.setattr(mod, "_MAX_TRANSFORM_WORK_BYTES", 1000)
    blob = "x" * 400
    prior = {"fetch": {"response": {"status": 200, "body": json.dumps({"content": blob})}}}
    one = {"$ta.effect": "fetch.response.body.content"}
    out, err = apply_body_transforms(
        {"c": {"$ta.concat": [one, one, one]}}, {}, prior_effects=prior,
    )
    assert out is None and "more than 1000 bytes" in err
    out, err = apply_body_transforms({"c": {"$ta.concat": [one, "!"]}}, {}, prior_effects=prior)
    assert out == {"c": blob + "!"} and err is None


def test_bounded_evidence_previews_only_long_bodies():
    from tinyassets.effectors.authenticated_external_call import bounded_evidence

    short = {"delivered": True, "response": {"status": 200, "body": "hi"}}
    assert bounded_evidence(short) is short
    long = {"delivered": True, "response": {"status": 200, "body": "y" * 5000, "headers": {}}}
    b = bounded_evidence(long)
    assert b["response"]["body"] == "y" * 4096 and b["response"]["body_truncated"] is True
    assert b["response"]["body_chars"] == 5000 and b["response"]["status"] == 200
    assert long["response"]["body"] == "y" * 5000          # the original is untouched


def test_the_budget_counts_bytes_and_referenced_objects(monkeypatch):
    """Codex round 3 (P0): only strings were charged, in characters. A
    referenced object is serialized into the body later, so it counts now;
    multibyte text counts as its UTF-8 bytes."""
    from tinyassets.effectors import authenticated_external_call as mod

    monkeypatch.setattr(mod, "_MAX_TRANSFORM_WORK_BYTES", 300)
    state = {"payload": {"blob": "x" * 400}}
    _out, err = apply_body_transforms(
        {"p": {"$ta.ref": "payload"}}, state, allowed_state_keys=["payload"],
    )
    assert err and "more than 300 bytes" in err
    emoji = "\U0001F600" * 100                       # 100 chars, 400 UTF-8 bytes
    _out, err = apply_body_transforms({"c": {"$ta.base64": emoji}}, {})
    assert err and "more than 300 bytes" in err
    ok = apply_body_transforms({"c": {"$ta.base64": "\U0001F600" * 30}}, {})
    assert ok[1] is None


def test_a_reference_to_a_value_json_cannot_carry_is_refused_not_crashed():
    """Codex round 3 (P1): bytes in state raised TypeError at the final dump,
    surfacing as effector_crashed."""
    _out, err = apply_body_transforms(
        {"b": {"$ta.ref": "raw"}}, {"raw": b"abc"}, allowed_state_keys=["raw"],
    )
    assert err and "cannot be sent as JSON" in err


def test_the_reserved_namespace_is_refused_beside_other_keys():
    """Codex round 3 (P2): `{"$ta.base64": ..., "other": 1}` used to pass
    through untouched."""
    _out, err = apply_body_transforms({"x": {"$ta.bas64": "typo", "other": 1}}, {})
    assert err and "unknown transform '$ta.bas64'" in err
    _out, err = apply_body_transforms({"x": {"$ta.base64": "t", "other": 1}}, {})
    assert err and "must be the only key" in err


def test_bounded_evidence_never_persists_header_values():
    """Codex round 3 (P1): the worker returns every response header and the
    run record is forever; a rotated cookie must not survive into it."""
    from tinyassets.effectors.authenticated_external_call import bounded_evidence

    result = {"delivered": True, "response": {
        "status": 200, "body": "hi", "headers": {"set-cookie": "session=rotated-secret",
                                                 "content-type": "application/json"},
    }}
    b = bounded_evidence(result)
    assert "headers" not in b["response"]
    assert b["response"]["header_names"] == ["content-type", "set-cookie"]
    assert "rotated-secret" not in json.dumps(b)
    # the in-memory original (what a later node's $ta.effect sees) is intact
    assert result["response"]["headers"]["set-cookie"] == "session=rotated-secret"


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
            review_provider=_approve,
            branch=branch, run_state={"pkt": json.dumps(packet)},
            base_path=str(universe_dir), run_id=run_id,
        )
    finally:
        loop.stop()
    return evidence, loop


def test_a_run_that_only_reads_settles_its_engine_admission_as_a_read(tmp_path, monkeypatch):
    """docs/concerns/2026-08-29-run-rate-cap-stalls-a-normal-github-job.md:
    the GET half of a GitHub job must not spend the engine's write budget."""
    db = _seed_engine_admission(tmp_path, monkeypatch, "run-get")
    evidence, loop = _run_one(tmp_path, monkeypatch, verb="GET", run_id="run-get")
    assert evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]["delivered"] is True
    assert [r["method"] for r in loop.recorded] == ["GET"]
    assert _admission_kind(db, "run-get") == "read"


def test_a_run_that_writes_keeps_its_engine_admission_a_write(tmp_path, monkeypatch):
    db = _seed_engine_admission(tmp_path, monkeypatch, "run-put")
    evidence, loop = _run_one(tmp_path, monkeypatch, verb="PUT", run_id="run-put",
                              packet_body={"content": "x"})
    assert evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]["delivered"] is True
    assert [r["method"] for r in loop.recorded] == ["PUT"]
    assert _admission_kind(db, "run-put") == "write"


def test_a_read_refused_before_the_wire_still_settles_as_a_read(tmp_path, monkeypatch):
    """No consent grant: the adapter refuses before sending. The packet declared
    GET, so nothing could have been written - the admission is a read."""
    db = _seed_engine_admission(tmp_path, monkeypatch, "run-refused")
    evidence, loop = _run_one(tmp_path, monkeypatch, verb="GET", run_id="run-refused",
                              refuse_grant=True)
    refused = evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]
    assert refused["error_kind"] == "missing_consent" and refused["dry_run"] is True
    assert loop.recorded == []
    assert _admission_kind(db, "run-refused") == "read"


def test_a_run_with_no_effect_nodes_settles_as_a_read(tmp_path, monkeypatch):
    import types

    from tinyassets import effectors as effectors_pkg

    db = _seed_engine_admission(tmp_path, monkeypatch, "run-compute")
    node = types.SimpleNamespace
    branch = node(node_defs=[node(node_id="think", effects=[], output_keys=["o"], input_keys=[])],
                  state_schema=None)
    assert effectors_pkg.run_effects_for_branch(
        review_provider=_approve,
        branch=branch, run_state={"o": "text"}, base_path=str(tmp_path / "universe-1"),
        run_id="run-compute") == {}
    assert _admission_kind(db, "run-compute") == "read"


def test_a_run_without_an_engine_admission_settles_nothing(tmp_path, monkeypatch):
    """Browser/scheduled runs have no admission row: the settle is a no-op and
    the ledger is not created for them."""
    from tinyassets import engine_admissions as adm

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _run_one(tmp_path, monkeypatch, verb="GET", run_id="browser-run")
    assert not (tmp_path / adm.LEDGER_NAME).exists()


def test_a_verb_that_disagrees_with_the_method_settles_as_a_write(tmp_path, monkeypatch):
    """Codex round 1 (P2): the method_mismatch refusal echoes the DECLARED
    verb (GET), which used to settle the run as a read. Nothing fired, but
    the packet's intent is unknown - fail closed."""
    db = _seed_engine_admission(tmp_path, monkeypatch, "run-mismatch")
    evidence, loop = _run_one(tmp_path, monkeypatch, verb="GET", run_id="run-mismatch",
                              request_method="PUT")
    assert evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]["error_kind"] == "method_mismatch"
    assert loop.recorded == []
    assert _admission_kind(db, "run-mismatch") == "write"


def test_an_unknown_sink_settles_as_a_write(tmp_path, monkeypatch):
    """Codex round 2 (P2): an unknown sink was skipped before the dispatcher
    recorded it, so the run settled as a read. What it would have done is
    unknown - it stays a write."""
    import types

    from tinyassets import effectors as effectors_pkg

    db = _seed_engine_admission(tmp_path, monkeypatch, "run-unknown")
    node = types.SimpleNamespace
    branch = node(node_defs=[node(node_id="n1", effects=["github_pull_request"],
                                  output_keys=["o"], input_keys=[])], state_schema=None)
    evidence = effectors_pkg.run_effects_for_branch(
        review_provider=_approve,
        branch=branch, run_state={"o": "x"}, base_path=str(tmp_path / "universe-1"),
        run_id="run-unknown")
    assert evidence["n1"]["github_pull_request"]["error_kind"] == "unknown_sink"
    assert _admission_kind(db, "run-unknown") == "write"


def test_a_run_that_wrote_and_then_failed_stays_a_write(tmp_path, monkeypatch):
    """Codex round 3 (P1): effects fire on success, but provider-authority
    release can fail afterwards and rewrite the status to FAILED; the
    failure hook must not downgrade a real write."""
    from tinyassets import runs as runs_module

    db = _seed_engine_admission(tmp_path, monkeypatch, "run-put-then-fail")
    evidence, loop = _run_one(tmp_path, monkeypatch, verb="PUT", run_id="run-put-then-fail",
                              packet_body={"content": "x"})
    assert evidence["n1"][EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]["delivered"] is True
    assert _admission_kind(db, "run-put-then-fail") == "write"
    runs_module.initialize_runs_db(tmp_path)
    runs_module.update_run_status(tmp_path, "run-put-then-fail",
                                  status=runs_module.RUN_STATUS_FAILED, error="release failed")
    assert _admission_kind(db, "run-put-then-fail") == "write"    # final


def test_an_effect_reference_resolves_a_dotted_node_id():
    """Codex on the evidence hint: `fetch.v1` is a valid node id, and the hint
    names `fetch.v1.response.body`; the resolver used to split at the first
    dot and report no earlier node 'fetch'."""
    import pytest

    from tinyassets.effectors.authenticated_external_call import _TransformContext, _TransformError

    prior = {"fetch.v1": {"response": {"status": 200, "body": '{"sha": "abc", "content": "aGk="}'}},
             "fetch": {"response": {"status": 200, "body": '{"sha": "other"}'}}}
    ctx = _TransformContext({}, None, prior)
    assert ctx.effect("fetch.v1.response.body.sha") == "abc"      # longest id wins
    assert ctx.effect("fetch.response.body.sha") == "other"
    assert ctx.effect("fetch.v1.response.status") == 200
    with pytest.raises(_TransformError):
        ctx.effect("fetch.v2.response.body.sha")                    # still refused


def test_replace_swaps_one_exact_line_and_refuses_ambiguity():
    """Live 2026-08-30 (PR #2714, closed): asked to change one README line,
    the universe re-typed the file through a model and lost every blank line
    and a backslash. `$ta.replace` edits inside the fetched text; the model
    authors only the old and the new line."""
    from tinyassets.effectors.authenticated_external_call import apply_body_transforms

    doc = "# T\n\nline one\n\nPatches by this universe are opened through the request rail.\n"
    encoded = _b64.b64encode(doc.encode()).decode()
    prior = {"fetch": {"response": {"status": 200, "body": '{"content": "' + encoded + '"}'}}}
    body = {"content": {"$ta.base64": {"$ta.replace": {
        "in": {"$ta.from_base64": {"$ta.effect": "fetch.response.body.content"}},
        "old": "Patches by this universe are opened through the request rail.\n",
        "new": "Patches by this universe are opened through the request rail. (2026-08-29)\n",
    }}}}
    out, why = apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects=prior)
    assert why is None
    assert _b64.b64decode(out["content"]).decode() == doc.replace(
        "request rail.\n", "request rail. (2026-08-29)\n")   # blank lines intact, exact bytes
    # absent old -> refused, nothing sent
    body["content"]["$ta.base64"]["$ta.replace"]["old"] = "not in the file\n"
    out, why = apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects=prior)
    assert out is None and "not found" in why
    # ambiguous old -> refused unless count says so
    body["content"]["$ta.base64"]["$ta.replace"]["old"] = "\n"
    out, why = apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects=prior)
    assert out is None and "occurs" in why
    body["content"]["$ta.base64"]["$ta.replace"].update(
        {"old": "line one", "new": "line 1", "count": 1})
    out, why = apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects=prior)
    assert why is None and "line 1" in _b64.b64decode(out["content"]).decode()
    # malformed shapes are refused
    for bad in ({"$ta.replace": {"in": "x", "old": "x"}},
                {"$ta.replace": {"in": "x", "old": "", "new": "y"}},
                {"$ta.replace": {"in": "x", "old": "x", "new": "y", "count": 0}}):
        out, why = apply_body_transforms({"c": bad}, {}, allowed_state_keys=[], prior_effects=prior)
        assert out is None and why


def test_replace_refuses_unknown_keys_takes_count_from_state_and_charges_bytes(monkeypatch):
    """Codex round 1: a typo'd key ran with the default; `count` was the one
    operand not resolved through the transforms; a multibyte text was charged
    in characters, which let ~51 MB of replacement output through the 32 MiB
    working set."""
    from tinyassets.effectors import authenticated_external_call as aec

    body = {"c": {"$ta.replace": {"in": "abc", "old": "b", "new": "B", "countt": 2}}}
    out, why = aec.apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects={})
    assert out is None and "countt" in why
    body = {"c": {"$ta.replace": {"in": "aXaXa", "old": "X", "new": "-",
                                  "count": {"$ta.ref": "n"}}}}
    out, why = aec.apply_body_transforms(body, {"n": 2}, allowed_state_keys=["n"], prior_effects={})
    assert why is None and out["c"] == "a-a-a"
    monkeypatch.setattr(aec, "_MAX_TRANSFORM_WORK_BYTES", 100)
    text = "\u00e9" * 60   # 60 characters, 120 UTF-8 bytes
    body = {"c": {"$ta.replace": {"in": text, "old": "\u00e9", "new": "e", "count": 60}}}
    out, why = aec.apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects={})
    assert out is None and why
    body = {"c": {"$ta.replace": {"in": text[:30], "old": "\u00e9", "new": "e", "count": 30}}}
    out, why = aec.apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects={})
    assert why is None and out["c"] == "e" * 30


def test_replace_refusal_shows_the_input_where_old_stops_matching():
    """Live 2026-08-30 (runs 6cb4d9f48a9949be, 7a7a91c14b0d4b8b): the file
    ended `rail.\n (2026-08-29)` - a space before the date - and the universe,
    which cannot see the bytes, guessed `rail.\n(2026-08-29)` twice. The
    refusal now shows the input around the closest partial match with the
    whitespace visible, so the next attempt can be exact."""
    from tinyassets.effectors import authenticated_external_call as aec

    doc = "# T\n\nPatches by this universe are opened through the request rail.\n (2026-08-29)"
    body = {"c": {"$ta.replace": {
        "in": doc,
        "old": "Patches by this universe are opened through the request rail.\n(2026-08-29)",
        "new": "Patches by this universe are opened through the request rail. (2026-08-29)\n",
    }}}
    out, why = aec.apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects={})
    assert out is None
    assert "closest partial match" in why
    assert repr("request rail.\n (2026-08-29)")[1:-1] in why, why   # the space is visible
    # nothing in common -> no window, the plain advice
    body["c"]["$ta.replace"]["old"] = "zzzz"
    out, why = aec.apply_body_transforms(body, {}, allowed_state_keys=[], prior_effects={})
    assert out is None and "fetch the file" in why and "closest" not in why
    assert aec._nearest_window("abc", "abcdef") is None      # under the 8-char floor
    assert aec._nearest_window("x" * 50 + "needle-here-yes", "needle-here-no").startswith("x")
