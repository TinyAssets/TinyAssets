"""connect_compute — owner-scoped registration of an open compute provider.

Covers: owner (admin) gate; api_key_http registration
against a real granted http connection; grant validation (absent/revoked/
cross-universe/cross-owner) fail-closed; NO secret accepted or echoed; anonymous +
non-admin + foreign-admin refusal; the write_graph dispatch; idempotency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity

_GRANT_ID = "http_grant_" + "a" * 32
_CONN_ID = "http_" + "b" * 32


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
            Identity(user_id=user_id, username=user_id,
                     capabilities=["tinyassets.universe.write"])
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


def _make_universe(base: Path, uid: str, *, admin: str = "", write: str = "") -> None:
    from tinyassets.daemon_server import grant_universe_access

    (base / uid).mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(base, universe_id=uid, actor_id=admin,
                              permission="admin", granted_by=admin)
    if write:
        grant_universe_access(base, universe_id=uid, actor_id=write,
                              permission="write", granted_by=admin)


def _seed_grant(base: Path, *, owner: str, universe: str,
                grant_id: str = _GRANT_ID, conn_id: str = _CONN_ID) -> None:
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: owner)
    ledger.create_connection(
        connection_id=conn_id, owner_user_id=owner, connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http",),
        provider="http", destination="compute:x",
        credential_ref="vault://http/compute:x",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(
        grant_id=grant_id, connection_id=conn_id, owner_user_id=owner,
         unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )


def _connect(uid: str, **doc: Any) -> dict[str, Any]:
    from tinyassets.api.compute_connection import connect_compute

    return connect_compute(universe_id=uid, payload=json.dumps(doc))


# --------------------------------------------------------------------------- #
# Happy paths.
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# Grant validation (fail closed).
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# Auth gates.
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# read_compute_providers — the owner-facing listing (read sibling).
# --------------------------------------------------------------------------- #
