"""Security regressions for the serve-open-compute Codex reject folds.

Each test pins one of the 5 Critical holes the exact-diff review caught:
#1/#2/#3 grant-rotation / stale-digest bypass -> verify_open_grant_custody;
#4 same-name substitution via a tampered definition -> get_definition id integrity;
#5 authority_kind / snapshot discriminator confusion -> reserve consistency gate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tinyassets.credential_vault import adopt_connection_grant_custody
from tinyassets.providers.definition import (
    ProviderDefinitionError,
    get_definition,
    register_definition,
)
from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger


def _seed_grant(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "u").mkdir(exist_ok=True)
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "o"
    )
    ledger.create_connection(
        connection_id="http_c", owner_user_id="o", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http",), provider="http",
        destination="d", credential_ref="vault://http/d",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}],
    )
    ledger.grant_connection(
        grant_id="http_grant_x", connection_id="http_c", owner_user_id="o",
        universe_id="u", unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    d = register_definition(
        universe_id="u", owner_user_id="o", access_method="api_key_http",
        protocol="openai_chat", model="m", ref="http_grant_x",
    )
    return d.id


# --- #1/#2/#3: live-grant digest revalidation ------------------------------- #


def test_verify_open_grant_custody_happy_then_rejects_stale_digest(tmp_path, monkeypatch):
    from tinyassets.provider_serving_binding import verify_open_grant_custody

    def_id = _seed_grant(tmp_path, monkeypatch)
    provider_name = f"api_key_http:{def_id}"

    conn = sqlite3.connect(":memory:")
    conn.execute("BEGIN")
    real = adopt_connection_grant_custody(
        conn, owner_user_id="o", universe_id="u", grant_id="http_grant_x",
        connection_id="http_c", credential_ref="vault://http/d",
    )
    conn.close()

    # Happy: the live grant matches the custody digest -> returns the connection_id.
    assert verify_open_grant_custody(tmp_path, "u", "o", provider_name, real) == "http_c"

    # Stale/rotated: a custody whose record digest no longer matches the live grant
    # (simulating a rotated grant/credential_ref that kept the connection_id) is
    # rejected — a stale-digest compare alone would have let it pass.
    stale = replace(real, _record_digest="sha256:" + "0" * 64)
    with pytest.raises(PermissionError):
        verify_open_grant_custody(tmp_path, "u", "o", provider_name, stale)


def test_verify_open_grant_custody_rejects_foreign_owner(tmp_path, monkeypatch):
    from tinyassets.provider_serving_binding import verify_open_grant_custody

    def_id = _seed_grant(tmp_path, monkeypatch)
    provider_name = f"api_key_http:{def_id}"
    conn = sqlite3.connect(":memory:")
    conn.execute("BEGIN")
    real = adopt_connection_grant_custody(
        conn, owner_user_id="o", universe_id="u", grant_id="http_grant_x",
        connection_id="http_c", credential_ref="vault://http/d",
    )
    conn.close()
    # A different caller (not the grant owner) is refused — independent caller check.
    with pytest.raises(PermissionError):
        verify_open_grant_custody(tmp_path, "u", "intruder", provider_name, real)


# --- #4: definition id integrity (substitution) ----------------------------- #


def test_get_definition_rejects_tampered_ref(tmp_path, monkeypatch):
    def_id = _seed_grant(tmp_path, monkeypatch)
    store = tmp_path / "u" / "provider_definitions.json"
    rows = json.loads(store.read_text(encoding="utf-8"))
    # Swap the grant ref while keeping the id — the id no longer content-addresses
    # its fields, so a substituted grant could serve under a trusted id. Fail closed.
    for row in rows:
        if row["id"] == def_id:
            row["ref"] = "http_grant_evil00000000000000000000000"
    store.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ProviderDefinitionError):
        get_definition("u", def_id)


# --- #5: authority_kind / snapshot discriminator consistency ---------------- #


def _authority(**over):
    from tinyassets.provider_assignment import ServedProviderAuthority

    base = dict(
        authority_kind="connection_grant", provider="api_key_http:provdef_x",
        max_invocations=10, request_max_invocations=2, max_tokens=1000,
        max_cost_microunits=1000, owner_user_id="o", universe_id="u",
        agent_binding_id="b", binding_revision=1, binding_id="bid",
        binding_generation=1, binding_digest="d", credential_reference_id="c",
        credential_reference_generation=1, credential_reference_digest="cd",
        credential_service="http", credential_snapshot_dir=None, request_capability=None,
    )
    base.update(over)
    return ServedProviderAuthority(**base)


@pytest.mark.parametrize(
    "authority",
    [
        # connection_grant kind but a snapshot dir present (inconsistent).
        _authority(credential_snapshot_dir=Path("x")),
        # connection_grant kind but a subscription provider name (inconsistent).
        _authority(provider="codex"),
        # subscription_snapshot kind but an open provider name (inconsistent).
        _authority(authority_kind="subscription_snapshot", provider="api_key_http:provdef_x",
                   credential_snapshot_dir=Path("x")),
        # unknown kind.
        _authority(authority_kind="sdk_direct"),
    ],
)
def test_reserve_rejects_inconsistent_authority_kind(tmp_path, authority):
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import reserve_served_provider_budget

    (tmp_path / "u").mkdir(exist_ok=True)
    with pytest.raises(ProviderAuthorityHeldError):
        reserve_served_provider_budget(
            tmp_path, universe_dir=tmp_path / "u", authority=authority,
            requested_output_tokens=100, estimated_input_tokens=50,
        )
