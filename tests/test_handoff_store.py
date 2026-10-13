from __future__ import annotations

import sqlite3

import pytest

from tinyassets.handoffs.models import (
    HandoffAccessError,
    HandoffConflictError,
    HandoffRecord,
)
from tinyassets.handoffs.store import HandoffStore
from tinyassets.outcomes.schema import OUTCOME_EVIDENCE_LEVELS, migrate_outcome_schema


def _record(**overrides: str) -> HandoffRecord:
    values = {
        "handoff_id": "ho_1",
        "owner_id": "account-alice",
        "effect_key": "effect:v1:one",
        "sink": "handoff:test",
        "adapter_action": "submit",
        "destination": "provider:destination",
        "branch_def_id": "branch-1",
        "branch_version_id": "branch-1@v1",
        "content_hash": "a" * 64,
        "run_id": "run-1",
        "output_field": "submission",
        "output_sha256": "b" * 64,
        "effect_class": "irreversible",
        "outcome_kind": "published_paper",
        "state": "reserved",
        "created_at": "2026-07-26T00:00:00+00:00",
        "updated_at": "2026-07-26T00:00:00+00:00",
    }
    values.update(overrides)
    return HandoffRecord(**values)


def test_outcome_schema_owner_creates_evidence_lifecycle_tables() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        migrate_outcome_schema(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()

    assert {
        "outcome_event",
        "outcome_evidence",
        "outcome_evidence_transition",
        "outcome_artifact",
        "outcome_artifact_source",
    } <= tables
    assert OUTCOME_EVIDENCE_LEVELS == {
        "user_attested",
        "submitted",
        "accepted",
        "externally_verified",
        "disputed",
        "rejected",
        "orphaned",
        "retracted",
    }


def test_outcome_schema_backfills_existing_events_as_user_attested() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            """
            CREATE TABLE outcome_event (
                outcome_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                outcome_type TEXT NOT NULL,
                evidence_url TEXT,
                verified_at TEXT,
                verified_by TEXT,
                claim_run_id TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                recorded_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO outcome_event (
                outcome_id, run_id, outcome_type, evidence_url, verified_by,
                payload, recorded_at, note
            ) VALUES (
                'legacy-1', 'run-legacy', 'merged_pr',
                'https://github.com/acme/repo/pull/1', 'account-alice',
                '{"pr": 1}', '2026-07-20T00:00:00Z', 'original note'
            );
            """
        )

        migrate_outcome_schema(conn)

        evidence = conn.execute(
            "SELECT * FROM outcome_evidence WHERE outcome_id = 'legacy-1'"
        ).fetchone()
        transition = conn.execute(
            """
            SELECT * FROM outcome_evidence_transition
             WHERE outcome_id = 'legacy-1'
            """
        ).fetchone()
        original = conn.execute(
            "SELECT * FROM outcome_event WHERE outcome_id = 'legacy-1'"
        ).fetchone()
    finally:
        conn.close()

    assert evidence is not None
    # The legacy row did not persist its attester. ``verified_by`` names a
    # verifier, not the claimant, so migration must not manufacture authority
    # from it. The evidence lifecycle is backfilled while actor fields remain
    # explicitly unknown until authenticated records can bind them.
    assert evidence["account_id"] == ""
    assert evidence["attested_by"] == ""
    assert evidence["run_id"] == "run-legacy"
    assert evidence["outcome_kind"] == "merged_pr"
    assert evidence["evidence_level"] == "user_attested"
    assert transition["from_level"] == ""
    assert transition["to_level"] == "user_attested"
    assert original["payload"] == '{"pr": 1}'
    assert original["note"] == "original note"


def test_handoff_store_scopes_reads_and_deduplicates_effect_identity(
    tmp_path,
) -> None:
    store = HandoffStore(tmp_path)
    store.initialize()
    created = store.create_handoff(
        _record(),
        evidence_source="receipt_reservation",
    )

    assert store.get_handoff(
        created.handoff_id,
        actor_id="account-alice",
    ) == created
    with pytest.raises(HandoffAccessError, match="not found"):
        store.get_handoff(created.handoff_id, actor_id="account-mallory")
    with pytest.raises(HandoffConflictError, match="already exists"):
        store.create_handoff(
            _record(handoff_id="ho_2"),
            evidence_source="receipt_reservation",
        )


def test_handoff_store_appends_compare_and_swap_transitions(tmp_path) -> None:
    store = HandoffStore(tmp_path)
    store.initialize()
    store.create_handoff(
        _record(),
        evidence_source="receipt_reservation",
    )

    advanced = store.advance_handoff(
        "ho_1",
        actor_id="account-alice",
        expected_state="reserved",
        to_state="submitted",
        evidence_source="provider_response",
        evidence={"provider_event_id": "evt-1"},
    )
    transitions = store.list_transitions(
        "ho_1",
        actor_id="account-alice",
    )

    assert advanced.state == "submitted"
    assert [item.seq for item in transitions] == [1, 2]
    assert transitions[1].evidence == {"provider_event_id": "evt-1"}
    with pytest.raises(HandoffConflictError, match="no longer"):
        store.advance_handoff(
            "ho_1",
            actor_id="account-alice",
            expected_state="reserved",
            to_state="submitted",
            evidence_source="stale_writer",
        )
