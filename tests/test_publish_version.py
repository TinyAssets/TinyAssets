"""Tests for publish_version — content-addressed branch snapshots.

Spec: docs/vetted-specs.md §publish_version.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.branch_versions import (
    BranchVersion,
    compute_content_hash,
    get_branch_version,
    initialize_branch_versions_db,
    list_branch_versions,
    publish_branch_version,
)
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition


def _make_branch_dict(branch_id: str = "b1", name: str = "Test") -> dict:
    """Build a minimal branch dict suitable for publish_branch_version."""
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name=name,
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[{"name": "output", "type": "str"}],
        skills=[{
            "skill_id": "branch-rubric",
            "name": "Branch Rubric",
            "body": "Use the branch-specific rubric.",
        }],
    )
    return branch.to_dict()


# ─── compute_content_hash ─────────────────────────────────────────────────────

class TestComputeContentHash:
    def test_deterministic_for_same_input(self):
        d = _make_branch_dict()
        from tinyassets.branch_versions import _canonical_snapshot
        snap = _canonical_snapshot(d)
        assert compute_content_hash(snap) == compute_content_hash(snap)

    def test_different_topology_gives_different_hash(self):
        d1 = _make_branch_dict()
        d2 = _make_branch_dict()
        d2["entry_point"] = "n2"
        from tinyassets.branch_versions import _canonical_snapshot
        h1 = compute_content_hash(_canonical_snapshot(d1))
        h2 = compute_content_hash(_canonical_snapshot(d2))
        assert h1 != h2

    def test_metadata_change_does_not_affect_hash(self):
        """Only topology fields contribute to the hash, not name/author."""
        d1 = _make_branch_dict(name="Version Alpha")
        d2 = _make_branch_dict(name="Version Beta")
        from tinyassets.branch_versions import _canonical_snapshot
        h1 = compute_content_hash(_canonical_snapshot(d1))
        h2 = compute_content_hash(_canonical_snapshot(d2))
        assert h1 == h2

    def test_hash_is_64_hex_chars(self):
        d = _make_branch_dict()
        from tinyassets.branch_versions import _canonical_snapshot
        h = compute_content_hash(_canonical_snapshot(d))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ─── publish_branch_version ───────────────────────────────────────────────────

class TestPublishBranchVersion:
    def test_mints_new_branch_version_id(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d, publisher="alice")
        assert v.branch_version_id.startswith("b1@")
        assert len(v.branch_version_id) > 3

    def test_returns_branch_version_object(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d, publisher="alice")
        assert isinstance(v, BranchVersion)
        assert v.branch_def_id == "b1"
        assert v.publisher == "alice"
        assert v.content_hash

    def test_republishing_same_content_returns_same_id(self, tmp_path):
        d = _make_branch_dict()
        v1 = publish_branch_version(tmp_path, d)
        v2 = publish_branch_version(tmp_path, d)
        assert v1.branch_version_id == v2.branch_version_id
        assert v1.content_hash == v2.content_hash

    def test_different_topology_gives_different_version_id(self, tmp_path):
        d1 = _make_branch_dict()
        d2 = _make_branch_dict()
        d2["state_schema"] = [{"name": "extra", "type": "int"}]
        v1 = publish_branch_version(tmp_path, d1)
        v2 = publish_branch_version(tmp_path, d2)
        assert v1.branch_version_id != v2.branch_version_id

    def test_stores_notes(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d, notes="First stable release")
        assert v.notes == "First stable release"

    def test_missing_branch_def_id_raises(self, tmp_path):
        d = _make_branch_dict()
        d["branch_def_id"] = ""
        with pytest.raises(ValueError, match="branch_def_id"):
            publish_branch_version(tmp_path, d)

    def test_invalid_parent_version_id_raises(self, tmp_path):
        d = _make_branch_dict()
        with pytest.raises(KeyError, match="not found"):
            publish_branch_version(tmp_path, d, parent_version_id="nonexistent@abc12345")

    def test_valid_parent_version_id_accepted(self, tmp_path):
        d = _make_branch_dict()
        parent = publish_branch_version(tmp_path, d)
        d2 = _make_branch_dict()
        d2["state_schema"] = [{"name": "v2", "type": "str"}]
        child = publish_branch_version(tmp_path, d2, parent_version_id=parent.branch_version_id)
        assert child.parent_version_id == parent.branch_version_id

    def test_snapshot_contains_topology_fields(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d)
        assert "node_defs" in v.snapshot
        assert "edges" in v.snapshot
        assert "entry_point" in v.snapshot
        assert "state_schema" in v.snapshot
        assert v.snapshot["skills"] == d["skills"]

    def test_skill_change_gives_different_version_id(self, tmp_path):
        d1 = _make_branch_dict()
        d2 = _make_branch_dict()
        d2["skills"] = [{
            "skill_id": "branch-rubric",
            "name": "Branch Rubric",
            "body": "Use a changed rubric.",
        }]
        v1 = publish_branch_version(tmp_path, d1)
        v2 = publish_branch_version(tmp_path, d2)
        assert v1.branch_version_id != v2.branch_version_id

    def test_db_row_graph_shape_preserves_topology(self, tmp_path):
        """Publishing a live DB-row branch must not drop graph topology."""
        d = _make_branch_dict()
        flat_version = publish_branch_version(tmp_path, d)
        db_row_shape = {
            k: v
            for k, v in d.items()
            if k not in {"graph_nodes", "edges", "conditional_edges"}
        }
        db_row_shape["graph"] = {
            "nodes": d["graph_nodes"],
            "edges": d["edges"],
            "conditional_edges": d["conditional_edges"],
            "entry_point": d["entry_point"],
        }

        row_version = publish_branch_version(tmp_path, db_row_shape)

        assert row_version.branch_version_id == flat_version.branch_version_id
        assert row_version.snapshot["graph_nodes"] == d["graph_nodes"]
        assert row_version.snapshot["edges"] == d["edges"]

    def test_snapshot_excludes_mutable_metadata_but_freezes_access_authority(
        self, tmp_path
    ):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d)
        assert "name" not in v.snapshot
        # An unowned legacy definition stays unowned; publishing must never
        # synthesize a shared authority-bearing identity.
        assert v.snapshot["author"] == ""
        assert v.snapshot["visibility"] == "public"
        assert "stats" not in v.snapshot


# ─── get_branch_version ───────────────────────────────────────────────────────

class TestGetBranchVersion:
    def test_retrieves_published_version(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d)
        fetched = get_branch_version(tmp_path, v.branch_version_id)
        assert fetched is not None
        assert fetched.branch_version_id == v.branch_version_id

    def test_returns_none_for_missing_version(self, tmp_path):
        initialize_branch_versions_db(tmp_path)
        result = get_branch_version(tmp_path, "nonexistent@00000000")
        assert result is None

    def test_round_trips_snapshot(self, tmp_path):
        d = _make_branch_dict()
        v = publish_branch_version(tmp_path, d)
        fetched = get_branch_version(tmp_path, v.branch_version_id)
        assert fetched.snapshot == v.snapshot


# ─── list_branch_versions ────────────────────────────────────────────────────

class TestListBranchVersions:
    def test_lists_versions_for_branch(self, tmp_path):
        d1 = _make_branch_dict()
        d2 = _make_branch_dict()
        d2["state_schema"] = [{"name": "v2", "type": "str"}]
        publish_branch_version(tmp_path, d1)
        publish_branch_version(tmp_path, d2)
        versions = list_branch_versions(tmp_path, "b1")
        assert len(versions) == 2

    def test_does_not_list_other_branches(self, tmp_path):
        d1 = _make_branch_dict(branch_id="branch-X")
        d2 = _make_branch_dict(branch_id="branch-Y")
        publish_branch_version(tmp_path, d1)
        publish_branch_version(tmp_path, d2)
        versions = list_branch_versions(tmp_path, "branch-X")
        assert all(v.branch_def_id == "branch-X" for v in versions)

    def test_returns_empty_for_unpublished_branch(self, tmp_path):
        initialize_branch_versions_db(tmp_path)
        versions = list_branch_versions(tmp_path, "unheard-of-branch")
        assert versions == []

    def test_limit_is_respected(self, tmp_path):
        for i in range(5):
            d = _make_branch_dict()
            d["state_schema"] = [{"name": f"field{i}", "type": "str"}]
            publish_branch_version(tmp_path, d)
        versions = list_branch_versions(tmp_path, "b1", limit=3)
        assert len(versions) <= 3


# ─── BranchDefinition.fork_from field ────────────────────────────────────────

class TestBranchDefinitionForkFrom:
    def test_fork_from_defaults_to_none(self):
        branch = BranchDefinition(branch_def_id="b1", name="Test")
        assert branch.fork_from is None

    def test_fork_from_in_to_dict(self):
        branch = BranchDefinition(branch_def_id="b1", name="Test", fork_from="b0@abc12345")
        d = branch.to_dict()
        assert d["fork_from"] == "b0@abc12345"

    def test_fork_from_roundtrips_via_from_dict(self):
        branch = BranchDefinition(branch_def_id="b1", name="Test", fork_from="b0@abc12345")
        d = branch.to_dict()
        branch2 = BranchDefinition.from_dict(d)
        assert branch2.fork_from == "b0@abc12345"

    def test_fork_from_none_roundtrips(self):
        branch = BranchDefinition(branch_def_id="b1", name="Test")
        d = branch.to_dict()
        branch2 = BranchDefinition.from_dict(d)
        assert branch2.fork_from is None


# ─── MCP actions ─────────────────────────────────────────────────────────────

class TestPublishVersionMcpActions:
    def _seed_branch(self, base_path: Path, branch_id: str = "b1") -> None:
        from tinyassets.daemon_server import initialize_author_server, save_branch_definition
        initialize_author_server(base_path)
        nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
        branch = BranchDefinition(
            branch_def_id=branch_id,
            name="Test Branch",
            author="test-publisher",
            graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
            edges=[EdgeDefinition(from_node="n1", to_node="END")],
            entry_point="n1",
            node_defs=[nd],
            state_schema=[{"name": "output", "type": "str"}],
        )
        save_branch_definition(base_path, branch_def=branch.to_dict())

    def test_publish_version_action(self, tmp_path, monkeypatch, authenticate_request):
        authenticate_request("test-publisher")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        self._seed_branch(tmp_path)
        from tinyassets.api.extensions import _extensions_impl as extensions
        result = json.loads(extensions(action="publish_version", branch_def_id="b1"))
        assert "branch_version_id" in result
        assert "content_hash" in result
        assert result["branch_version_id"].startswith("b1@")

    def test_publish_version_missing_branch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        from tinyassets.api.extensions import _extensions_impl as extensions
        from tinyassets.daemon_server import initialize_author_server
        initialize_author_server(tmp_path)
        result = json.loads(extensions(action="publish_version", branch_def_id="nonexistent"))
        assert "error" in result

    def test_publish_version_is_idempotent(
        self, tmp_path, monkeypatch, authenticate_request
    ):
        authenticate_request("test-publisher")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        self._seed_branch(tmp_path)
        from tinyassets.api.extensions import _extensions_impl as extensions
        r1 = json.loads(extensions(action="publish_version", branch_def_id="b1"))
        r2 = json.loads(extensions(action="publish_version", branch_def_id="b1"))
        assert r1["branch_version_id"] == r2["branch_version_id"]
