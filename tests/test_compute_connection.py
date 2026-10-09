"""connect_compute — owner-scoped registration of an open compute provider.

Covers: owner (admin) gate; subscription_cli registration; api_key_http registration
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
        universe_id=universe, unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )


def _connect(uid: str, **doc: Any) -> dict[str, Any]:
    from tinyassets.api.compute_connection import connect_compute

    return connect_compute(universe_id=uid, payload=json.dumps(doc))


# --------------------------------------------------------------------------- #
# Happy paths.
# --------------------------------------------------------------------------- #


def test_subscription_cli_registration(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _login("founder")
    r = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="gpt-5-codex", ref="codex")
    assert r["status"] == "registered"
    assert r["access_method"] == "subscription_cli"
    assert r["definition_id"].startswith("provdef_")


def test_api_key_http_registration_against_real_grant(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _seed_grant(base, owner="founder", universe="u-x")
    _login("founder")
    r = _connect("u-x", access_method="api_key_http", protocol="openai_chat",
                 model="moonshotai/kimi-k2", ref=_GRANT_ID)
    assert r["status"] == "registered"
    assert r["protocol"] == "openai_chat"
    guidance = " ".join(r["next"])
    assert "supported api_key_http models" in guidance
    assert "accepted model_access" in guidance
    assert "fresh tool-capable discovery" in guidance
    assert "enabled engine-tool execution" in guidance
    assert "registration alone does not enable serving or grant spending" in guidance
    assert "api_key_http serves workflow nodes" not in guidance
    # No secret material anywhere in the response.
    assert "secret" not in json.dumps(r).lower()
    assert "vault://" not in json.dumps(r)


def test_idempotent_reregistration(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _login("founder")
    a = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="gpt-5", ref="codex")
    b = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="gpt-5", ref="codex")
    assert a["definition_id"] == b["definition_id"]


def test_routes_through_write_graph(base: Path) -> None:
    import importlib

    from tinyassets import universe_server as us

    importlib.reload(us)
    try:
        _make_universe(base, "u-r", admin="founder")
        _login("founder")
        raw = us.write_graph(
            target="connection", operation="connect_compute", graph_id="u-r",
            payload_json=json.dumps({"access_method": "subscription_cli",
                                     "protocol": "cli:codex", "model": "gpt-5",
                                     "ref": "codex"}),
        )
        assert json.loads(raw)["status"] == "registered"
    finally:
        importlib.reload(us)


# --------------------------------------------------------------------------- #
# Grant validation (fail closed).
# --------------------------------------------------------------------------- #


def test_api_key_http_absent_grant_rejected(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _login("founder")
    r = _connect("u-x", access_method="api_key_http", protocol="openai_chat",
                 model="m", ref="http_grant_" + "z" * 32)
    # A non-empty but inaccessible ref (absent here) returns the SAME uniform
    # not_found as a foreign-universe/owner grant — no existence oracle (Codex adapt
    # #1). Only an EMPTY ref gets the specific connection_setup_invalid usage error.
    assert r["error"] == "not_found"


def test_api_key_http_cross_universe_grant_hidden(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _make_universe(base, "u-other", admin="founder")
    _seed_grant(base, owner="founder", universe="u-other")  # grant bound to u-other
    _login("founder")
    r = _connect("u-x", access_method="api_key_http", protocol="openai_chat",
                 model="m", ref=_GRANT_ID)
    # Isolation: a grant for another universe is not confirmed — uniform not_found.
    assert r == {"error": "not_found", "resource": "connection"}


def test_api_key_http_cross_owner_grant_hidden(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(base, universe_id="u-x", actor_id="coadmin",
                          permission="admin", granted_by="founder")
    _seed_grant(base, owner="founder", universe="u-x")  # grant owned by founder
    _login("coadmin")  # a different admin of the same universe
    r = _connect("u-x", access_method="api_key_http", protocol="openai_chat",
                 model="m", ref=_GRANT_ID)
    assert r == {"error": "not_found", "resource": "connection"}


def test_subscription_cli_bad_ref_rejected(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder")
    _login("founder")
    r = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="m", ref="kimi-cli")
    assert r["error"] == "connection_setup_invalid"


# --------------------------------------------------------------------------- #
# Auth gates.
# --------------------------------------------------------------------------- #


def test_anonymous_refused(base: Path, nobody) -> None:
    _make_universe(base, "u-x", admin="founder")
    r = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="m", ref="codex")
    assert r["error"] == "authentication_required"


def test_write_collaborator_refused(base: Path) -> None:
    _make_universe(base, "u-x", admin="founder", write="collab")
    _login("collab")
    r = _connect("u-x", access_method="subscription_cli", protocol="cli:codex",
                 model="m", ref="codex")
    assert r == {"error": "not_found", "resource": "connection"}


def test_foreign_universe_admin_refused(base: Path) -> None:
    _make_universe(base, "u-a", admin="founder-a")
    _make_universe(base, "u-b", admin="founder-b")
    _login("founder-a")
    r = _connect("u-b", access_method="subscription_cli", protocol="cli:codex",
                 model="m", ref="codex")
    assert r == {"error": "not_found", "resource": "connection"}


# --------------------------------------------------------------------------- #
# read_compute_providers — the owner-facing listing (read sibling).
# --------------------------------------------------------------------------- #


def test_read_compute_providers_lists_owner_definitions(base: Path) -> None:
    from tinyassets.api.compute_connection import read_compute_providers

    _make_universe(base, "u-r", admin="founder")
    _login("founder")
    reg = _connect("u-r", access_method="subscription_cli", protocol="cli:codex",
                   model="gpt-5-codex", ref="codex")
    assert reg["status"] == "registered"

    out = read_compute_providers(universe_id="u-r")
    assert out["count"] == 1
    row = out["providers"][0]
    assert row["definition_id"] == reg["definition_id"]
    assert row["access_method"] == "subscription_cli"
    assert row["protocol"] == "cli:codex"
    assert row["model"] == "gpt-5-codex"
    # No secret / owner leak in the projection.
    assert "owner_user_id" not in row
    for banned in ("secret", "token", "password", "credential", "auth_material"):
        assert banned not in json.dumps(out).lower(), banned


def test_read_compute_providers_owner_gated(base: Path) -> None:
    from tinyassets.api.compute_connection import read_compute_providers

    _make_universe(base, "u-r2", admin="founder", write="collab")
    _login("founder")
    _connect("u-r2", access_method="subscription_cli", protocol="cli:codex",
             model="gpt-5-codex", ref="codex")

    # Anonymous -> authentication_required.
    _logout()
    assert read_compute_providers(universe_id="u-r2").get("error") == "authentication_required"

    # A write collaborator (not admin) -> uniform not_found (owner-gated).
    _login("collab")
    assert read_compute_providers(universe_id="u-r2") == {
        "error": "not_found", "resource": "connection",
    }

    # Owner (admin) -> the listing.
    _login("founder")
    assert read_compute_providers(universe_id="u-r2")["count"] == 1
