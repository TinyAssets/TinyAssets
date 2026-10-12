"""Tests for tinyassets/gate_events/ — real-world outcome attestation.

Spec: openspec/specs/paid-market-economy/spec.md §gate_event.

Attribution language invariant: tests assert "cited by" / "contributed to"
semantics, never causal. Status flow: attested → verified | disputed → retracted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.branch_versions import publish_branch_version
from tinyassets.gate_events import (
    attest_gate_event,
    dispute_gate_event,
    get_gate_event,
    list_gate_events,
    retract_gate_event,
    verify_gate_event,
)
from tinyassets.runs import initialize_runs_db


def _seed_branch_version(base_path: Path, branch_id: str = "b1") -> str:
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(base_path)
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name="Test Branch",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[],
    )
    save_branch_definition(base_path, branch_def=branch.to_dict())
    v = publish_branch_version(base_path, branch.to_dict(), publisher="alice")
    return v.branch_version_id


# ─── GateEvent dataclass / status flow ───────────────────────────────────────

class TestGateEventDataclass:
    def test_initial_status_is_attested(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="peer_review_accepted",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[],
        )
        assert evt.verification_status == "attested"
        assert evt.is_verified is False
        assert evt.is_disputed is False
        assert evt.is_retracted is False

    def test_event_id_is_server_generated(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="publication",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[],
        )
        assert evt.event_id
        assert len(evt.event_id) > 0

    def test_two_attestations_get_different_ids(self, tmp_path):
        initialize_runs_db(tmp_path)
        e1 = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        e2 = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        assert e1.event_id != e2.event_id

    def test_verify_transitions_status(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        verified = verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="bob")
        assert verified.verification_status == "verified"
        assert verified.verified_by == "bob"
        assert verified.is_verified is True

    def test_verify_same_user_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        with pytest.raises(ValueError, match="same as attester"):
            verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="alice")

    def test_dispute_transitions_status(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        disputed = dispute_gate_event(
            tmp_path, event_id=evt.event_id,
            disputed_by="carol", reason="Insufficient evidence",
        )
        assert disputed.verification_status == "disputed"
        assert disputed.disputed_by == "carol"
        assert disputed.dispute_reason == "Insufficient evidence"
        assert disputed.is_disputed is True

    def test_retract_transitions_status(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        retracted = retract_gate_event(
            tmp_path, event_id=evt.event_id,
            retracted_by="alice", note="duplicate event",
        )
        assert retracted.verification_status == "retracted"
        assert retracted.retracted_by == "alice"
        assert retracted.retraction_note == "duplicate event"
        assert retracted.is_retracted is True

    def test_retracted_event_cannot_be_disputed(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        retract_gate_event(tmp_path, event_id=evt.event_id, retracted_by="alice")
        with pytest.raises(ValueError):
            dispute_gate_event(
                tmp_path, event_id=evt.event_id, disputed_by="bob", reason="test"
            )

    def test_verify_already_verified_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="bob")
        with pytest.raises(ValueError, match="attested"):
            verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="carol")


# ─── Citations ────────────────────────────────────────────────────────────────

class TestGateEventCitations:
    def test_attest_with_valid_branch_version_succeeds(self, tmp_path):
        initialize_runs_db(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        evt = attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="pub",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[{"branch_version_id": bvid, "contribution_summary": "wrote chapter 1"}],
        )
        assert evt.cite_count == 1
        assert evt.cites[0].branch_version_id == bvid
        assert evt.cites[0].contribution_summary == "wrote chapter 1"

    def test_attest_with_invalid_branch_version_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError, match="not found"):
            attest_gate_event(
                tmp_path,
                goal_id="g1",
                event_type="pub",
                event_date="2026-04-24",
                attested_by="alice",
                cites=[{"branch_version_id": "nonexistent@00000000"}],
            )

    def test_attest_with_empty_cites_succeeds(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        assert evt.cite_count == 0

    def test_attest_multiple_cites(self, tmp_path):
        initialize_runs_db(tmp_path)
        bvid1 = _seed_branch_version(tmp_path, "b1")
        bvid2 = _seed_branch_version(tmp_path, "b2")
        evt = attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="pub",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[
                {"branch_version_id": bvid1, "contribution_summary": "chapter 1"},
                {"branch_version_id": bvid2, "contribution_summary": "chapter 2"},
            ],
        )
        assert evt.cite_count == 2


# ─── Fetch + list ─────────────────────────────────────────────────────────────

class TestGateEventFetchAndList:
    def test_get_returns_event(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        fetched = get_gate_event(tmp_path, evt.event_id)
        assert fetched is not None
        assert fetched.event_id == evt.event_id
        assert fetched.goal_id == "g1"

    def test_get_returns_none_for_missing(self, tmp_path):
        initialize_runs_db(tmp_path)
        result = get_gate_event(tmp_path, "nonexistent-id")
        assert result is None

    def test_list_by_goal_id_filters_correctly(self, tmp_path):
        initialize_runs_db(tmp_path)
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        attest_gate_event(
            tmp_path, goal_id="g2", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        g1_events = list_gate_events(tmp_path, goal_id="g1")
        assert all(e.goal_id == "g1" for e in g1_events)
        assert len(g1_events) == 1

    def test_list_by_branch_version_shows_attribution(self, tmp_path):
        initialize_runs_db(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="pub",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[{"branch_version_id": bvid}],
        )
        by_bvid = list_gate_events(tmp_path, branch_version_id=bvid)
        assert len(by_bvid) == 1
        assert by_bvid[0].goal_id == "g1"

    def test_retracted_event_still_listable(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        retract_gate_event(tmp_path, event_id=evt.event_id, retracted_by="alice")
        all_events = list_gate_events(tmp_path, goal_id="g1", include_retracted=True)
        assert any(e.event_id == evt.event_id for e in all_events)

    def test_list_exclude_retracted(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        retract_gate_event(tmp_path, event_id=evt.event_id, retracted_by="alice")
        non_retracted = list_gate_events(
            tmp_path, goal_id="g1", include_retracted=False
        )
        assert not any(e.event_id == evt.event_id for e in non_retracted)


# ─── Invariants ───────────────────────────────────────────────────────────────

class TestGateEventInvariants:
    def test_requires_goal_id(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="goal_id"):
            attest_gate_event(
                tmp_path, goal_id="", event_type="pub",
                event_date="2026-04-24", attested_by="alice", cites=[],
            )

    def test_requires_event_type(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="event_type"):
            attest_gate_event(
                tmp_path, goal_id="g1", event_type="",
                event_date="2026-04-24", attested_by="alice", cites=[],
            )

    def test_requires_event_date(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="event_date"):
            attest_gate_event(
                tmp_path, goal_id="g1", event_type="pub",
                event_date="", attested_by="alice", cites=[],
            )

    def test_requires_attested_by(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="attested_by"):
            attest_gate_event(
                tmp_path, goal_id="g1", event_type="pub",
                event_date="2026-04-24", attested_by="", cites=[],
            )

    def test_retired_unowned_marker_is_never_an_actor(self, tmp_path):
        initialize_runs_db(tmp_path)
        retired = bytes.fromhex("616e6f6e796d6f7573").decode("ascii")

        with pytest.raises(ValueError, match="attested_by"):
            attest_gate_event(
                tmp_path, goal_id="g1", event_type="pub",
                event_date="2026-04-24", attested_by=retired, cites=[],
            )
        with pytest.raises(ValueError, match="verifier_id"):
            verify_gate_event(tmp_path, event_id="unused", verifier_id=retired)
        with pytest.raises(ValueError, match="disputed_by"):
            dispute_gate_event(
                tmp_path, event_id="unused", disputed_by=retired, reason="test",
            )
        with pytest.raises(ValueError, match="retracted_by"):
            retract_gate_event(tmp_path, event_id="unused", retracted_by=retired)

    def test_verify_nonexistent_raises_key_error(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError):
            verify_gate_event(tmp_path, event_id="nonexistent-id", verifier_id="bob")

    def test_dispute_nonexistent_raises_key_error(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError):
            dispute_gate_event(
                tmp_path, event_id="nonexistent-id",
                disputed_by="bob", reason="test"
            )

    def test_retract_nonexistent_raises_key_error(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError):
            retract_gate_event(
                tmp_path, event_id="nonexistent-id", retracted_by="alice"
            )

    def test_verified_status_persists_on_reload(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="pub",
            event_date="2026-04-24", attested_by="alice", cites=[],
        )
        verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="bob")
        reloaded = get_gate_event(tmp_path, evt.event_id)
        assert reloaded is not None
        assert reloaded.verification_status == "verified"
        assert reloaded.verified_by == "bob"

    def test_notes_field_persists(self, tmp_path):
        initialize_runs_db(tmp_path)
        evt = attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="pub",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[],
            notes="Cited in chapter 3 of published work",
        )
        reloaded = get_gate_event(tmp_path, evt.event_id)
        assert reloaded is not None
        assert "chapter 3" in reloaded.notes
