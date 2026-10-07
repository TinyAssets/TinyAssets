from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.runs import (
    _connect,
    create_run,
    initialize_runs_db,
    list_run_receipts,
    record_run_receipt,
)


def _seed_run(base: Path) -> str:
    initialize_runs_db(base)
    return create_run(
        base,
        branch_def_id="branch-1",
        thread_id="thread-1",
        inputs={},
        run_name="receipt test",
        actor="tester",
    )


def test_initialize_runs_db_creates_run_receipts_table(tmp_path: Path) -> None:
    initialize_runs_db(tmp_path)

    with _connect(tmp_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

    assert "run_receipts" in tables
    assert "idx_run_receipts_run" in indexes
    assert "idx_run_receipts_type" in indexes
    assert "idx_run_receipts_subject" in indexes


def test_record_source_acquisition_receipt_normalizes_flags(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    receipt = record_run_receipt(
        tmp_path,
        run_id=run_id,
        receipt_type="source_acquisition_receipt",
        node_id="search-node",
        payload={
            "source": "https://example.test/paper",
            "search_scope": "title+abstract",
            "fetched": True,
            "snapshot_hash": "sha256:abc",
        },
    )

    payload = receipt["payload"]
    assert receipt["subject_id"] == "https://example.test/paper"
    assert receipt["node_id"] == "search-node"
    assert payload["source_ref"] == "https://example.test/paper"
    assert payload["fetched"] is True
    assert payload["viewed"] is False
    assert payload["verified"] is False
    assert payload["snapshotted"] is False
    assert payload["unavailable"] is False
    assert payload["not_searched"] is False
    assert payload["snapshot_hash"] == "sha256:abc"
    assert payload["retrieval_timestamp"]


def test_record_claim_lineage_receipt_preserves_counter_evidence(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    record_run_receipt(
        tmp_path,
        run_id=run_id,
        receipt_type="claim_lineage_receipt",
        payload={
            "claim_id": "claim-7",
            "evidence_refs": ["receipt-source-1"],
            "imported_prior_run_claims": ["old-run:claim-3"],
            "counter_evidence_refs": ["receipt-source-2"],
            "changed_claims": ["claim-2"],
            "confidence": "medium",
            "status": "contested",
            "rationale": "New source disagrees with prior summary.",
        },
    )

    receipts = list_run_receipts(
        tmp_path,
        receipt_type="claim_lineage_receipt",
        subject_id="claim-7",
    )
    assert len(receipts) == 1
    payload = receipts[0]["payload"]
    assert payload["claim_id"] == "claim-7"
    assert payload["evidence_refs"] == ["receipt-source-1"]
    assert payload["counter_evidence_refs"] == ["receipt-source-2"]
    assert payload["status"] == "contested"


def test_record_revision_receipt_identifies_rerun_targets(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    receipt = record_run_receipt(
        tmp_path,
        run_id=run_id,
        receipt_type="revision_receipt",
        payload={
            "old_run_id": "prior-run",
            "new_evidence_refs": ["source-receipt-9"],
            "changed_status": "superseded",
            "affected_outputs": ["report.md"],
            "recommended_reruns": ["branch-1"],
            "rationale": "Snapshot hash changed.",
        },
    )

    assert receipt["subject_id"] == "prior-run"
    assert receipt["payload"]["new_evidence_refs"] == ["source-receipt-9"]
    assert receipt["payload"]["recommended_reruns"] == ["branch-1"]


def test_receipt_preserves_unknown_keys_and_extensions(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    record_run_receipt(
        tmp_path,
        run_id=run_id,
        receipt_type="claim_lineage_receipt",
        payload={
            "claim_id": "claim-standard-1",
            "status": "needs-review",
            "confidence_score": 0.83,
            "extensions": {
                "standard_refs": ["FHIR-R4", "CONSORT-2010"],
                "conformance_profile": "community/clinical-trial-v1",
            },
        },
    )

    receipts = list_run_receipts(tmp_path, subject_id="claim-standard-1")

    payload = receipts[0]["payload"]
    assert payload["confidence_score"] == 0.83
    assert payload["extensions"] == {
        "standard_refs": ["FHIR-R4", "CONSORT-2010"],
        "conformance_profile": "community/clinical-trial-v1",
    }


def test_receipt_payload_size_is_capped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = _seed_run(tmp_path)
    monkeypatch.setenv("TINYASSETS_RECEIPT_PAYLOAD_MAX_BYTES", "128")

    with pytest.raises(ValueError, match="payload exceeds max 128 bytes"):
        record_run_receipt(
            tmp_path,
            run_id=run_id,
            receipt_type="claim_lineage_receipt",
            payload={
                "claim_id": "claim-large",
                "rationale": "x" * 512,
            },
        )


def test_receipt_validation_rejects_missing_claim_id(tmp_path: Path) -> None:
    run_id = _seed_run(tmp_path)

    try:
        record_run_receipt(
            tmp_path,
            run_id=run_id,
            receipt_type="claim_lineage_receipt",
            payload={"evidence_refs": []},
        )
    except ValueError as exc:
        assert "claim_id" in str(exc)
    else:
        raise AssertionError("missing claim_id should be rejected")


def test_source_receipt_flags_must_be_booleans(tmp_path: Path) -> None:
    run_id = _seed_run(tmp_path)

    try:
        record_run_receipt(
            tmp_path,
            run_id=run_id,
            receipt_type="source_acquisition_receipt",
            payload={"source_ref": "source-a", "fetched": "false"},
        )
    except ValueError as exc:
        assert "fetched must be a boolean" in str(exc)
    else:
        raise AssertionError("string source flags should be rejected")


def test_source_receipt_rejects_contradictory_not_searched_state(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    try:
        record_run_receipt(
            tmp_path,
            run_id=run_id,
            receipt_type="source_acquisition_receipt",
            payload={
                "source_ref": "source-a",
                "not_searched": True,
                "fetched": True,
            },
        )
    except ValueError as exc:
        assert "not_searched cannot be combined" in str(exc)
    else:
        raise AssertionError("contradictory source flags should be rejected")


def test_source_receipt_rejects_not_searched_and_unavailable(
    tmp_path: Path,
) -> None:
    run_id = _seed_run(tmp_path)

    try:
        record_run_receipt(
            tmp_path,
            run_id=run_id,
            receipt_type="source_acquisition_receipt",
            payload={
                "source_ref": "source-a",
                "not_searched": True,
                "unavailable": True,
            },
        )
    except ValueError as exc:
        assert "not_searched cannot be combined with unavailable" in str(exc)
    else:
        raise AssertionError("not_searched and unavailable should conflict")


def test_list_run_receipts_filters_by_run_and_type(tmp_path: Path) -> None:
    run_a = _seed_run(tmp_path)
    run_b = create_run(
        tmp_path,
        branch_def_id="branch-2",
        thread_id="thread-2",
        inputs={},
        actor="universe:u-test",
    )
    record_run_receipt(
        tmp_path,
        run_id=run_a,
        receipt_type="source_acquisition_receipt",
        payload={"source_ref": "source-a", "viewed": True},
    )
    record_run_receipt(
        tmp_path,
        run_id=run_b,
        receipt_type="revision_receipt",
        payload={"old_claim_id": "claim-a", "affected_outputs": ["out"]},
    )

    rows = list_run_receipts(
        tmp_path,
        run_id=run_a,
        receipt_type="source_acquisition_receipt",
    )

    assert len(rows) == 1
    assert rows[0]["run_id"] == run_a
    assert rows[0]["receipt_type"] == "source_acquisition_receipt"
