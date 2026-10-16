"""PR-139 slice 8 action-scope registry and checkpoint tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tinyassets.auth.middleware import auth_middleware, require_action_scope, set_provider
from tinyassets.auth.provider import (
    AuthProvider,
    DevAuthProvider,
    Identity,
    PermissionAction,
    build_action_scope_registry,
)


class StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "ok" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "test-client", **metadata}

    def create_authorization(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        return "test-code"

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        return None


@pytest.fixture(autouse=True)
def _reset_auth_provider() -> None:
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def test_action_scope_registry_is_derived_from_internal_dispatch_tables() -> None:
    registry = build_action_scope_registry()

    assert registry["universe.inspect"].oauth_scope == "tinyassets.universe.read"
    assert registry["universe.inspect"].effect == "read"
    assert "UNIVERSE_ACTIONS" in registry["universe.inspect"].source

    assert registry["universe.daemon_banish"].oauth_scope == "tinyassets.universe.admin"
    assert registry["universe.daemon_banish"].effect == "admin"

    assert registry["wiki.read"].oauth_scope == "tinyassets.wiki.read"
    assert registry["wiki.write"].oauth_scope == "tinyassets.wiki.write"

    assert registry["extensions.run_branch"].oauth_scope == "tinyassets.extensions.costly"
    assert registry["gates.list"].oauth_scope == "tinyassets.gates.read"

    assert all("schema" not in row.source.lower() for row in registry.values())


def test_retired_paid_market_actions_are_not_authorizable() -> None:
    registry = build_action_scope_registry()
    assert not any("escrow_" in action for action in registry)
    assert "universe.treasury_status" not in registry
    for action in ("stake_bonus", "unstake_bonus", "release_bonus"):
        assert f"gates.{action}" not in registry


def test_action_scope_status_self_audit_reports_table_and_caveats() -> None:
    from tinyassets.api.extensions import _extensions_impl

    payload = json.loads(_extensions_impl(action="get_action_scope_status"))

    assert payload["source"] == "internal_dispatch_action_registries"
    assert "not raw MCP tool schemas" in payload["scope_derivation"]
    assert "tinyassets.universe.admin" in payload["oauth_scopes"]
    assert payload["counts"]["actions"] >= 80
    assert any(
        row["action_name"] == "universe.daemon_banish"
        and row["oauth_scope"] == "tinyassets.universe.admin"
        for row in payload["actions"]
    )
    assert payload["caveats"]


def test_permission_action_requires_named_scope_when_present() -> None:
    identity = Identity(
        user_id="user::operator",
        username="operator",
        capabilities=["tinyassets.universe.write"],
    )

    verdict = identity.can(
        PermissionAction(
            name="universe.submit_request",
            required_scope="tinyassets.universe.write",
        )
    )
    assert verdict.allowed is True
    assert verdict.required_scope == "tinyassets.universe.write"

    legacy_only = Identity(
        user_id="user::legacy",
        username="legacy",
        capabilities=["universe.submit_request"],
    )
    assert legacy_only.can(
        PermissionAction(
            name="universe.submit_request",
            required_scope="tinyassets.universe.write",
        )
    ).allowed is False


def test_action_scope_checkpoint_enforces_only_when_auth_is_enabled() -> None:
    set_provider(StaticAuthProvider(Identity(
        user_id="user::operator",
        username="operator",
        capabilities=["tinyassets.universe.admin"],
    )))
    auth_middleware("ok")
    require_action_scope("universe", "daemon_banish")

    set_provider(StaticAuthProvider(Identity(
        user_id="user::reader",
        username="reader",
        capabilities=["tinyassets.universe.read"],
    )))
    auth_middleware("ok")
    with pytest.raises(PermissionError, match="tinyassets.universe.admin"):
        require_action_scope("universe", "daemon_banish")

    # Dev mode does not enforce scopes, but it still needs SOMEBODY bound.
    set_provider(DevAuthProvider(user_id="operator"))
    auth_middleware(None)
    with pytest.raises(PermissionError, match="Authentication required"):
        require_action_scope("universe", "daemon_banish")
    auth_middleware("any-bearer")
    require_action_scope("universe", "daemon_banish")
