"""Differential proof of the shared execution/readiness authority validator."""
from __future__ import annotations

import sqlite3
from dataclasses import fields

import pytest

from tests.served_authority_baseline import legacy_authorize_served_provider_call
from tests.test_open_serving_bind import _CONN_ID, _GRANT_ID, _bound_and_serving
from tests.test_provider_served_router import _fresh_served_request, _served_context
from tinyassets.auth.middleware import revoke_provider_request
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.provider_assignment import authorize_served_provider_call
from tinyassets.provider_serving_binding import set_serving
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


def _observe(authorize, root, universe, context, *, role="writer", body_error=False):
    snapshot = None
    try:
        with authorize(
            root, universe_dir=universe, request_carrier=context.provider_request,
            role=role, operation="converse",
        ) as authority:
            snapshot = authority.credential_snapshot_dir
            if snapshot is not None:
                assert snapshot.exists()
            if body_error:
                raise LookupError("provider-body-error")
            return {
                field.name: getattr(authority, field.name)
                for field in fields(authority) if field.compare
            }
    except ProviderAuthorityHeldError:
        return "held"
    except LookupError as exc:
        assert str(exc) == "provider-body-error"
        return "body-error"
    finally:
        if snapshot is not None:
            assert not snapshot.exists(), "credential snapshot leaked after authorization"


@pytest.mark.parametrize("provider", ["subscription", "http"])
@pytest.mark.parametrize("condition", [
    "ready", "wrong-role", "revoked-carrier", "disabled-agent", "body-error",
    "pending", "failed", "unassigned", "digest-tamper", "binding-tamper",
])
def test_execution_matches_legacy_chain(tmp_path, monkeypatch, provider, condition):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    if provider == "subscription":
        universe, serving, capability, context = _served_context(tmp_path)
    else:
        universe, serving, _ = _bound_and_serving(tmp_path, monkeypatch)
        capability, context = _fresh_served_request(universe, serving, request_id="diff")
    try:
        if condition == "revoked-carrier":
            revoke_provider_request(capability)
        elif condition == "disabled-agent":
            set_serving(
                base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
                universe_id="u-owner", agent_binding_id=serving["agent_binding_id"],
                expected_revision=serving["revision"], enabled=False,
            )
        elif condition in {"pending", "failed", "unassigned", "digest-tamper", "binding-tamper"}:
            with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
                if condition == "digest-tamper":
                    conn.execute("UPDATE provider_assignments SET assignment_digest = 'tampered'")
                elif condition == "binding-tamper":
                    conn.execute("UPDATE provider_assignments SET binding_digest = 'tampered'")
                else:
                    conn.execute("UPDATE provider_assignments SET state = ?", (condition,))
                conn.commit()
        arguments = dict(
            role="reviewer" if condition == "wrong-role" else "writer",
            body_error=condition == "body-error",
        )
        before = _observe(
            legacy_authorize_served_provider_call, tmp_path, universe, context, **arguments,
        )
        after = _observe(authorize_served_provider_call, tmp_path, universe, context, **arguments)
        assert after == before
        if condition not in {"ready", "body-error"}:
            assert after == "held"
        elif condition == "body-error":
            assert after == "body-error"
        else:
            assert after["provider"]
    finally:
        revoke_provider_request(capability)


@pytest.mark.parametrize("condition", ["revoked-grant", "rotated-credential"])
def test_live_http_custody_matches_legacy(tmp_path, monkeypatch, condition):
    universe, serving, _ = _bound_and_serving(tmp_path, monkeypatch)
    capability, context = _fresh_served_request(universe, serving, request_id="custody-diff")
    try:
        if condition == "revoked-grant":
            ConnectionLedger(
                tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
                verify_authenticated_principal=lambda: "owner-1",
            ).revoke_grant(_GRANT_ID)
        else:
            with sqlite3.connect(tmp_path / ".broker" / "outbound.db") as conn:
                conn.execute(
                    "UPDATE outbound_connections SET credential_ref = ? WHERE connection_id = ?",
                    ("vault://http/rotated", _CONN_ID),
                )
        for authorize in (legacy_authorize_served_provider_call, authorize_served_provider_call):
            assert _observe(authorize, tmp_path, universe, context) == "held"
    finally:
        revoke_provider_request(capability)


def test_dispatch_uses_shared_validator_with_explicit_root(tmp_path, monkeypatch):
    import tinyassets.provider_serving_binding as serving_module

    universe, serving, _ = _bound_and_serving(tmp_path, monkeypatch)
    capability, context = _fresh_served_request(universe, serving, request_id="shared-chain")
    shared = serving_module._current_serving_authority
    roots = []

    def recording(*args, **kwargs):
        roots.append(kwargs["base_path"])
        return shared(*args, **kwargs)

    monkeypatch.setattr(serving_module, "_current_serving_authority", recording)
    try:
        result = _observe(authorize_served_provider_call, tmp_path, universe, context)
        assert isinstance(result, dict)
        assert roots == [tmp_path]
    finally:
        revoke_provider_request(capability)
