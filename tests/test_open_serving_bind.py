"""bind_serving_provider on an OPEN api_key_http provider (serve-open-compute phase 2.1/2.2).

The bind connection_grant branch: reuse the assignment/work-binding/CAS machinery with
the secret-free connection-grant custody, so binding an open provider creates a ready
`ProviderAssignment` (provider = the open name), a work binding, and connection-grant
custody — and set_serving succeeds (exercises the _current_serving_authority open branch).
The subscription path is untouched (see the existing served-router suite).
"""

from __future__ import annotations

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

    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    set_founder_home(tmp_path, founder_sub="owner-1", universe_id="u-owner",
                     platform_generated=True)

    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1"
    )
    ledger.create_connection(
        connection_id=_CONN_ID, owner_user_id="owner-1", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http", "POST"), provider="http",
        destination="compute:x", credential_ref="vault://http/compute:x",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}],
    )
    ledger.grant_connection(
        grant_id=_GRANT_ID, connection_id=_CONN_ID, owner_user_id="owner-1",

        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    ledger.configure_capability(
        connection_id=_CONN_ID, capability_kind="model_use", enabled=True,
        descriptor={
            "wire": "openai_chat", "billing": "free",
            "models": [{"id": "moonshotai/kimi-k2", "tools": True, "context": 8192}],
        },
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










def _accepted(definition):
    """The owner's accepted model list for the open source: explicit, free-only."""
    from tinyassets.provider_assignment_manifest import ModelAccess

    return {definition.id: ModelAccess("explicit", ("moonshotai/kimi-k2",), None)}


def _bound_and_serving(tmp_path, monkeypatch):
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe_dir, agent, definition = _setup(tmp_path, monkeypatch)
    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider=definition.id, model_access=_accepted(definition),
    )
    serving = set_serving(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )["agent_binding"]
    return universe_dir, serving, definition
