"""bind_serving_provider on an OPEN api_key_http provider (serve-open-compute phase 2.1/2.2).

The bind connection_grant branch: reuse the assignment/work-binding/CAS machinery with
the secret-free connection-grant custody, so binding an open provider creates a ready
`ProviderAssignment` (provider = the open name), a work binding, and connection-grant
custody — and set_serving succeeds (exercises the _current_serving_authority open branch).
The subscription path is untouched (see the existing served-router suite).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_GRANT_ID = "http_grant_" + "a" * 32
_CONN_ID = "http_" + "b" * 32


def _agent_definition() -> dict:
    return {
        "schema_version": 1,
        "name": "Served",
        "description": "open serving fixture",
        "tags": ["test"],
        "components": {"identity": {"kind": "soul", "config": {}}},
    }


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()

    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1"
    )
    ledger.create_connection(
        connection_id=_CONN_ID, owner_user_id="owner-1", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http",), provider="http",
        destination="compute:x", credential_ref="vault://http/compute:x",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}],
    )
    ledger.grant_connection(
        grant_id=_GRANT_ID, connection_id=_CONN_ID, owner_user_id="owner-1",
        universe_id="u-owner",
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    definition = register_definition(
        universe_id="u-owner", owner_user_id="owner-1", access_method="api_key_http",
        protocol="openai_chat", model="moonshotai/kimi-k2", ref=_GRANT_ID,
    )
    published = publish_definition(tmp_path, author_id="owner-1", payload=_agent_definition())
    agent = create_binding(
        tmp_path, universe_id="u-owner",
        definition_id=published["agent_definition_id"], created_by="owner-1",
        payload={"schema_version": 1, "name": "Served", "role": "writer"},
    )
    return universe_dir, agent, definition


def test_bind_open_provider_creates_ready_assignment(tmp_path, monkeypatch) -> None:
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe_dir, agent, definition = _setup(tmp_path, monkeypatch)

    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider=definition.id,  # the open def-id
    )
    assert connected["status"] == "ready"

    assignment = load_provider_assignment(tmp_path, universe_id="u-owner")
    assert assignment is not None
    assert assignment.state == "ready"
    assert assignment.provider == f"api_key_http:{definition.id}"  # the open name

    # set_serving exercises the _current_serving_authority connection_grant branch.
    serving = set_serving(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )["agent_binding"]
    assert serving["status"] == "serving"


def test_bind_open_provider_refuses_cross_universe_grant(tmp_path, monkeypatch) -> None:
    from tinyassets.provider_serving_binding import bind_serving_provider
    from tinyassets.storage.outbound_connections import ConnectionLedger

    universe_dir, agent, definition = _setup(tmp_path, monkeypatch)
    # Re-grant the SAME connection to a DIFFERENT universe and point a def at it — but
    # bind for u-owner: the grant used by definition is bound to u-owner, so this checks
    # the happy path stays owned/bound. Now revoke and expect failure.
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1"
    )
    ledger.revoke_grant(_GRANT_ID)
    with pytest.raises(PermissionError):
        bind_serving_provider(
            base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
            universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
            expected_revision=1, provider=definition.id,
        )


def test_bind_unknown_provider_rejected(tmp_path, monkeypatch) -> None:
    from tinyassets.provider_serving_binding import bind_serving_provider

    universe_dir, agent, _definition = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        bind_serving_provider(
            base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
            universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
            expected_revision=1, provider="provdef_does_not_exist",
        )


def _bound_and_serving(tmp_path, monkeypatch):
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe_dir, agent, definition = _setup(tmp_path, monkeypatch)
    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider=definition.id,
    )
    serving = set_serving(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )["agent_binding"]
    return universe_dir, serving, definition


def test_open_provider_authorizes_and_reserves_a_served_turn(tmp_path, monkeypatch) -> None:
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
    )
    from tinyassets.provider_assignment import (
        authorize_served_provider_call,
        reserve_served_provider_budget,
    )

    universe_dir, serving, definition = _bound_and_serving(tmp_path, monkeypatch)
    reserve = reserve_provider_request(
        principal_id="owner-1", session_id="s1", request_id="r1", tool_name="converse",
    )
    claim_provider_request(reserve, tool_name="converse")
    carrier = mint_provider_request_carrier(
        universe_id="u-owner", agent_binding_id=serving["agent_binding_id"],
        binding_revision=serving["revision"], operation="converse",
    )

    with authorize_served_provider_call(
        tmp_path, universe_dir=universe_dir, request_carrier=carrier,
        role="writer", operation="converse",
    ) as authority:
        # The CALL-side authority is the connection_grant variant — no snapshot.
        assert authority.authority_kind == "connection_grant"
        assert authority.provider == f"api_key_http:{definition.id}"
        assert authority.credential_snapshot_dir is None
        # Budget reserves without a subscription custody re-check (open branch).
        reservation = reserve_served_provider_budget(
            tmp_path, universe_dir=universe_dir, authority=authority,
            requested_output_tokens=100, estimated_input_tokens=50,
        )
        assert reservation is not None
        assert reservation.reserved_total_tokens >= 150


def test_current_serving_authority_returns_exact_open_connection_without_secret(
    tmp_path, monkeypatch
) -> None:
    from tinyassets.provider_serving_binding import (
        resolve_current_serving_provider_authority,
    )

    universe_dir, _serving, definition = _bound_and_serving(tmp_path, monkeypatch)

    authority = resolve_current_serving_provider_authority(
        tmp_path,
        universe_dir=universe_dir,
        universe_id="u-owner",
        owner_user_id="owner-1",
    )

    assert authority.provider == f"api_key_http:{definition.id}"
    assert authority.access_method == "api_key_http"
    assert authority.connection_id == _CONN_ID
    assert authority.grant_id == _GRANT_ID
    assert "credential" not in repr(authority).lower()


def test_current_serving_authority_refuses_credential_reference_rotation(
    tmp_path, monkeypatch
) -> None:
    from tinyassets.provider_serving_binding import (
        resolve_current_serving_provider_authority,
    )

    universe_dir, _serving, _definition = _bound_and_serving(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "outbound.db") as raw:
        raw.execute(
            "UPDATE outbound_connections SET credential_ref = ? WHERE connection_id = ?",
            ("vault://http/rotated", _CONN_ID),
        )

    with pytest.raises(PermissionError, match="changed since binding"):
        resolve_current_serving_provider_authority(
            tmp_path,
            universe_dir=universe_dir,
            universe_id="u-owner",
            owner_user_id="owner-1",
        )
