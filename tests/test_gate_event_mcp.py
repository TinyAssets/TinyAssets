"""Tests for gate_event MCP wiring in extensions action group.

Spec: docs/vetted-specs.md §gate_event — real-world outcome attestation primitive.
Implementation: tinyassets/universe_server.py _action_*_gate_event functions.
"""

from __future__ import annotations

import json

from tinyassets.branch_versions import publish_branch_version
from tinyassets.daemon_server import (
    initialize_author_server,
    save_branch_definition,
    save_goal,
)
from tinyassets.runs import initialize_runs_db


def _seed_bv(base_path, branch_id: str = "b1", publisher: str = "alice") -> str:
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition

    initialize_author_server(base_path)
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name=f"Branch {branch_id}",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[],
    )
    save_branch_definition(base_path, branch_def=branch.to_dict())
    v = publish_branch_version(base_path, branch.to_dict(), publisher=publisher)
    return v.branch_version_id


def _seed_public_goal(base_path, goal_id: str = "g1") -> None:
    save_goal(
        base_path,
        goal={
            "goal_id": goal_id,
            "name": "Public gate-event Goal",
            "visibility": "public",
        },
    )


class TestAttestGateEvent:
    def test_attest_returns_event_id(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_attest_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        _seed_public_goal(tmp_path)
        initialize_runs_db(tmp_path)

        result = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": json.dumps([{"branch_version_id": bvid}]),
        }))
        assert result["status"] == "attested"
        assert result["event_id"]
        assert result["goal_id"] == "g1"
        assert result["cite_count"] == 1

    def test_attest_missing_goal_id_errors(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_attest_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_runs_db(tmp_path)

        result = json.loads(_action_attest_gate_event({
            "goal_id": "",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": "[]",
        }))
        assert "error" in result

    def test_attest_invalid_cites_json_errors(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_attest_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_runs_db(tmp_path)

        result = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": "not-json",
        }))
        assert "error" in result


class TestVerifyGateEvent:
    def test_verify_changes_status(self, tmp_path, monkeypatch):
        from tinyassets.api.market import (
            _action_attest_gate_event,
            _action_verify_gate_event,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        _seed_public_goal(tmp_path)
        initialize_runs_db(tmp_path)

        att = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": json.dumps([{"branch_version_id": bvid}]),
        }))
        eid = att["event_id"]

        result = json.loads(_action_verify_gate_event({
            "event_id": eid,
            "verifier_id": "bob",
        }))
        assert result["status"] == "verified"
        assert result["event_id"] == eid
        assert result["verification_status"] == "verified"

    def test_verify_missing_event_id_errors(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_verify_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_runs_db(tmp_path)

        result = json.loads(_action_verify_gate_event({"event_id": "", "verifier_id": "bob"}))
        assert "error" in result

    def test_same_actor_cannot_verify_own_attestation(self, tmp_path, monkeypatch):
        from tinyassets.api.market import (
            _action_attest_gate_event,
            _action_verify_gate_event,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        initialize_runs_db(tmp_path)

        att = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": json.dumps([{"branch_version_id": bvid}]),
        }))
        result = json.loads(_action_verify_gate_event({
            "event_id": att["event_id"],
            "verifier_id": "alice",
        }))
        assert "error" in result


class TestDisputeRetractGateEvent:
    def _attest(self, tmp_path, monkeypatch, bvid: str) -> str:
        from tinyassets.api.market import _action_attest_gate_event

        att = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "publication",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": json.dumps([{"branch_version_id": bvid}]),
        }))
        return att["event_id"]

    def test_dispute_changes_status(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_dispute_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        initialize_runs_db(tmp_path)
        eid = self._attest(tmp_path, monkeypatch, bvid)

        result = json.loads(_action_dispute_gate_event({
            "event_id": eid,
            "disputed_by": "bob",
            "reason": "incorrect citation",
        }))
        assert result["status"] == "disputed"
        assert result["verification_status"] == "disputed"

    def test_retract_changes_status(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_retract_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        initialize_runs_db(tmp_path)
        eid = self._attest(tmp_path, monkeypatch, bvid)

        result = json.loads(_action_retract_gate_event({
            "event_id": eid,
            "retracted_by": "alice",
            "note": "filed in error",
        }))
        assert result["status"] == "retracted"
        assert result["verification_status"] == "retracted"

    def test_dispute_missing_event_id(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_dispute_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_runs_db(tmp_path)
        result = json.loads(
            _action_dispute_gate_event({"event_id": "", "disputed_by": "bob", "reason": "x"})
        )
        assert "error" in result


class TestGetListGateEvents:
    def test_get_gate_event_roundtrip(self, tmp_path, monkeypatch):
        from tinyassets.api.market import (
            _action_attest_gate_event,
            _action_get_gate_event,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        _seed_public_goal(tmp_path)
        initialize_runs_db(tmp_path)

        att = json.loads(_action_attest_gate_event({
            "goal_id": "g1",
            "event_type": "peer_review_accepted",
            "event_date": "2026-04-25",
            "attested_by": "alice",
            "cites_json": json.dumps([{"branch_version_id": bvid}]),
        }))
        eid = att["event_id"]

        result = json.loads(_action_get_gate_event({"event_id": eid}))
        assert result["event_id"] == eid
        assert result["event_type"] == "peer_review_accepted"
        assert len(result["cites"]) == 1

    def test_get_gate_event_not_found(self, tmp_path, monkeypatch):
        from tinyassets.api.market import _action_get_gate_event

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_runs_db(tmp_path)
        result = json.loads(_action_get_gate_event({"event_id": "no-such-id"}))
        assert "error" in result

    def test_list_gate_events_by_goal(self, tmp_path, monkeypatch):
        from tinyassets.api.market import (
            _action_attest_gate_event,
            _action_list_gate_events,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        bvid = _seed_bv(tmp_path)
        _seed_public_goal(tmp_path)
        initialize_runs_db(tmp_path)

        for _ in range(3):
            _action_attest_gate_event({
                "goal_id": "g1",
                "event_type": "publication",
                "event_date": "2026-04-25",
                "attested_by": "alice",
                "cites_json": json.dumps([{"branch_version_id": bvid}]),
            })

        result = json.loads(_action_list_gate_events({"goal_id": "g1", "limit": 50}))
        assert result["goal_id"] == "g1"
        assert result["count"] == 3
