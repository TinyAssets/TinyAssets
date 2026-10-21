"""connect_http — provision a generic outbound http connection (Slice 1).

Requirement source:
``openspec/changes/archive/2026-10-06-provision-http-connection-channel/specs/outbound-connection-provisioning/spec.md``.

Covers: owner (admin, not write) gate, the vault http deposit + connection +
grant round-trip, endpoint SSRF pre-validation (nothing mutated on bad input),
idempotency + secret rotation, policy-immutable conflict (endpoint-change is a
conflict, never a silent reuse), inert-self-heal after a mid-provision fault,
cross-owner transfer refusal, response/log redaction, the write_graph dispatch,
and the no-new-advertised-handle invariant.

The primitive is channel-agnostic: these tests use a neutral example host
(``api.example.com``) on purpose. No real service is named anywhere in the code —
the owner supplies the host + secret at build time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity


class _StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "valid" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "test-code"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


def _login(user_id: str) -> None:
    set_provider(
        _StaticAuthProvider(
            Identity(
                user_id=user_id,
                username=user_id,
                capabilities=["tinyassets.universe.write"],
            )
        )
    )
    auth_middleware("valid")


def _logout() -> None:
    """Nobody bound. Resolving a "dev" token binds the LOCAL OPERATOR, which is
    a real principal -- so a logged-out caller was still somebody, and a test
    expecting `authentication_required` got a real answer instead."""
    from tinyassets.auth.middleware import clear_identity

    set_provider(DevAuthProvider())
    clear_identity()


@pytest.fixture(autouse=True)
def _reset_auth() -> Any:
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _make_universe(base: Path, uid: str, *, admin: str = "", write: str = "") -> Path:
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(
            base, universe_id=uid, actor_id=admin, permission="admin", granted_by=admin
        )
    if write:
        grant_universe_access(
            base, universe_id=uid, actor_id=write, permission="write", granted_by=admin
        )
    return udir


# Neutral example host — the primitive names no real service.
_EP = [{"host": "api.example.com", "path_template": "/v1/messages", "methods": ["POST"]}]


def _connect(
    uid: str,
    *,
    destination: str = "webhook:acme",
    secret: str = "sk-SECRET-token",
    endpoints: Any = None,
    auth_scheme: str | None = None,
) -> dict[str, Any]:
    from tests.support.connection_door import connect_http

    doc: dict[str, Any] = {
        "destination": destination,
        "secret": secret,
        "allowed_endpoints": _EP if endpoints is None else endpoints,
    }
    if auth_scheme is not None:
        doc["auth_scheme"] = auth_scheme
    return connect_http(universe_id=uid, payload=json.dumps(doc))


def _ledger(base: Path, actor: str) -> Any:
    from tinyassets.storage.outbound_connections import ConnectionLedger

    return ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: actor
    )


def _http_records(udir: Path) -> list[dict[str, Any]]:
    from tinyassets.credential_vault import custody_namespace, load_credential_vault
    from tinyassets.daemon_server import list_universe_acl

    owner = next(row["actor_id"] for row in list_universe_acl(udir.parent, universe_id=udir.name)
                 if row["permission"] == "admin")
    return [r for r in load_credential_vault(custody_namespace(udir.parent, owner))
            if r.get("credential_type") == "http"]


# --------------------------------------------------------------------------- #
# Positive round-trip.
# --------------------------------------------------------------------------- #






def _seed_legacy_http_connection(
    base: Path,
    uid: str,
    *,
    endpoints: Any = None,
    old_secret: str = "old-secret",
    grant_owner: str | None = None,
) -> tuple[Path, str, str]:
    """Write a PRE-FIX http connection: the legacy ("http",) scope token + an old
    secret + a grant carrying the legacy ``http_requests`` cap (the shape a
    connection provisioned before the #2521 scope fix actually has)."""
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.vault import write_credential_vault
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import ActionCap

    udir = _make_universe(base, uid, admin="founder")
    seed_owner_admission(base, principal="founder", center=uid)
    conn_id, grant_id = _ids(owner="founder", destination="webhook:acme")
    write_credential_vault(
        custody_namespace(base, "founder"),
        [
            {
                "credential_type": "http",
                "service": "webhook:acme",
                "destination": "webhook:acme",
                "token": old_secret,
            }
        ],
        owner_user_id="founder",
        universe_id=uid,
    )
    ledger = _ledger(base, "founder")
    ledger.create_connection(
        connection_id=conn_id,
        owner_user_id="founder",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",),
        provider="http",
        destination="webhook:acme",
        credential_ref="vault://http/webhook:acme",
        allowed_endpoints=_EP if endpoints is None else endpoints,
    )
    ledger.grant_connection(
        grant_id=grant_id,
        connection_id=conn_id,
        owner_user_id="founder",

        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    if grant_owner:
        with ledger._connect() as connection:
            connection.execute("UPDATE outbound_connection_grants SET owner_user_id=? "
                               "WHERE grant_id=?",
                               (grant_owner, grant_id))
    assert tuple(ledger._get_connection_resource(conn_id).scopes) == ("http",)
    return udir, conn_id, grant_id
















# --------------------------------------------------------------------------- #
# Auth gates.
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# Fail-closed input validation (nothing mutated).
# --------------------------------------------------------------------------- #


















# --------------------------------------------------------------------------- #
# Idempotency + rotation + conflict.
# --------------------------------------------------------------------------- #
















# --------------------------------------------------------------------------- #
# Redaction + no advertised handle.
# --------------------------------------------------------------------------- #






# --------------------------------------------------------------------------- #
# Endpoint EXTENSION — a credential is deposited once and grows with the work.
# Founder 2026-08-27: "not for each action with that credential".
# --------------------------------------------------------------------------- #

_MORE = [
    {"host": "api.example.com", "path_template": "/v1/messages", "methods": ["POST"]},
    {"host": "api.example.com", "path_template": "/v1/threads", "methods": ["POST"]},
]






















