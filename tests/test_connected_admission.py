"""C3 connected-source admission, revocation, and offline M2."""

from __future__ import annotations

import json
import tarfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.migrate_c3_connected_admission import migrate
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.provider_work_authority import (
    ProviderInvocationSelection,
    ProviderUniverseWorkReceipt,
    ProviderUniverseWorkRoot,
    ProviderWorkExecutionClaimRequest,
    ProviderWorkReceiptState,
    _claim_from_request,
    _content_digest,
    provider_work_receipt_id,
)
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
    _current_connected_member,
)

NOW = datetime(2026, 8, 1, 6, tzinfo=timezone.utc)


def _receipt(owner: str, sources: tuple[str, ...], *, version: int = 5,
             run_id: str = "run-one") -> ProviderUniverseWorkReceipt:
    root = ProviderUniverseWorkRoot("run", run_id)
    digest = _content_digest(sources) if version == 5 else "sha256:" + "d" * 64
    candidate = ProviderUniverseWorkReceipt(
        schema_version=version, authority_scope="manifest",
        admitted_sources=sources if version == 5 else None,
        manifest_digest=digest,
        receipt_id=provider_work_receipt_id(universe_id="u", root=root),
        receipt_digest="sha256:" + "0" * 64, generation=1,
        state=ProviderWorkReceiptState.ACTIVE,
        work_item_kind="run", work_item_id=run_id,
        binding_id=None, binding_generation=None, binding_digest=None,
        binding_revocation_generation=None,
        principal_id=owner, actor_id="universe:u", universe_id="u",
        branch_def_id="branch", branch_version_id="branch@abc",
        provider=None, credential_reference_digest=None,
        assignment_generation=1, assignment_digest=digest,
        executor_class="cloud", allowed_operations=("run_graph",),
        allowed_roles=("writer",), max_invocations=2,
        max_tokens=1000, max_cost_microunits=1000,
        expires_at="2026-08-01T07:00:00Z",
        created_at="2026-08-01T06:00:00Z",
        execution_subject=ExecutionSubject(
            kind=ExecutionSubjectKind.BRANCH_VERSION,
            ref="branch@abc", digest="sha256:" + "c" * 64,
        ),
    )
    return replace(candidate, receipt_digest=candidate.expected_digest())


def test_snapshot_is_sorted_and_digest_bound():
    receipt = _receipt("alice", ("api_key_http:source-a", "api_key_http:source-b"))
    assert receipt.admitted_sources == ("api_key_http:source-a", "api_key_http:source-b")
    assert receipt.manifest_digest == _content_digest(receipt.admitted_sources)
    assert ProviderUniverseWorkReceipt.from_dict(receipt.to_dict()) == receipt
    with pytest.raises(ValueError, match="sorted"):
        replace(receipt, admitted_sources=("api_key_http:source-b", "api_key_http:source-a"))
    with pytest.raises(ValueError, match="digest"):
        replace(receipt, manifest_digest="sha256:" + "f" * 64)


def test_attempt_requires_recorded_and_live_owner_membership(tmp_path, monkeypatch):
    from tinyassets import provider_serving_binding
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.providers import served_model_plan

    (tmp_path / "u").mkdir()
    set_founder_home(tmp_path, founder_sub="alice", universe_id="u",
                     platform_generated=True)
    set_founder_home(tmp_path, founder_sub="bob", universe_id="u",
                     platform_generated=True)
    live = {"alice": {"api_key_http:source-b"}, "bob": {"api_key_http:source-a"}}
    monkeypatch.setattr(
        served_model_plan, "connected_sources",
        lambda _base, _universe, owner, *, conn=None: frozenset(live[owner]),
    )
    monkeypatch.setattr(provider_serving_binding, "_resolve_serving_source",
                        lambda base, uid, owner, provider, access: SimpleNamespace(
                            open_grant=("grant", provider, "credential")))
    store = SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW)
    alice = _receipt("alice", ("api_key_http:source-b",))
    with store.connection() as conn:
        conn.execute("BEGIN")
        assert _current_connected_member(
            conn, tmp_path, alice,
            ProviderInvocationSelection.connected("api_key_http:source-b", "")
        ).owner_user_id == "alice"
        live["alice"].add("api_key_http:source-a")
        with pytest.raises(PermissionError, match="not admitted"):
            _current_connected_member(
                conn, tmp_path, alice,
                ProviderInvocationSelection.connected("api_key_http:source-a", ""),
            )
        live["alice"].remove("api_key_http:source-b")
        with pytest.raises(PermissionError, match="not connected"):
            _current_connected_member(
                conn, tmp_path, alice,
                ProviderInvocationSelection.connected("api_key_http:source-b", ""),
            )
        next_run = _receipt("alice", ("api_key_http:source-a",), run_id="run-two")
        assert _current_connected_member(
            conn, tmp_path, next_run,
            ProviderInvocationSelection.connected("api_key_http:source-a", ""),
        ).owner_user_id == "alice"
        bob = _receipt("bob", ("api_key_http:source-b",), run_id="run-bob")
        with pytest.raises(PermissionError, match="not connected"):
            _current_connected_member(
                conn, tmp_path, bob,
                ProviderInvocationSelection.connected("api_key_http:source-b", "")
            )


def test_legacy_active_receipt_is_refused_and_m2_fences_it(tmp_path):
    store = SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW)
    receipt = _receipt("alice", (), version=4)
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO provider_work_receipts (receipt_id, receipt_digest, generation, "
            "state, work_item_kind, work_item_id, universe_id, binding_id, "
            "binding_generation, binding_digest, expires_at, record_json, "
            "authority_scope, manifest_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt.receipt_id, receipt.receipt_digest, 1, "active", "run", "run-one",
             "u", None, None, None, receipt.expires_at,
             json.dumps(receipt.to_dict(), sort_keys=True), "manifest", receipt.manifest_digest),
        )
        request = ProviderWorkExecutionClaimRequest(
            receipt_id=receipt.receipt_id, receipt_digest=receipt.receipt_digest,
            worker_id="worker", runtime_id="runtime",
            claim_nonce_digest="sha256:" + "e" * 64, lease_seconds=60,
        )
        claim = _claim_from_request(
            request, created_at="2026-08-01T06:00:00Z",
            lease_expires_at="2026-08-01T06:01:00Z",
        )
        conn.execute(
            "INSERT INTO provider_work_execution_claims (claim_id, claim_digest, "
            "receipt_id, generation, state, lease_expires_at, record_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (claim.claim_id, claim.claim_digest, claim.receipt_id, 1, "active",
             claim.lease_expires_at, json.dumps(claim.to_dict(), sort_keys=True)),
        )
        conn.commit()
    with pytest.raises(PermissionError, match="stale connected-source admission"):
        store.claim(request)
    preview = migrate(tmp_path)
    assert preview["count"] == 1 and preview["run_ids"] == ["run-one"]
    assert store.get_receipt(receipt.receipt_id).state is ProviderWorkReceiptState.ACTIVE
    archive = Path(tmp_path).parent / f"{tmp_path.name}-backup.tar"
    with tarfile.open(archive, "w") as backup:
        backup.add(tmp_path / ".tinyassets.db", arcname=".tinyassets.db")
    with pytest.raises(PermissionError, match="stopped writers"):
        migrate(tmp_path, archive=archive, apply=True)
    result = migrate(tmp_path, archive=archive, apply=True, writers_stopped=True)
    assert result["count"] == 1 and result["run_ids"] == ["run-one"]
    assert store.get_receipt(receipt.receipt_id).state is ProviderWorkReceiptState.FENCED
    with store.connection() as conn:
        row = conn.execute(
            "SELECT state FROM provider_work_execution_claims WHERE claim_id=?",
            (claim.claim_id,),
        ).fetchone()
    assert row["state"] == "invalidated"
