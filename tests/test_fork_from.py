"""Tests for fork_from lineage tracking on branches.

Spec: openspec/specs/community-patch-loop/spec.md §fork_from — content-addressed lineage tracking.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.branch_versions import publish_branch_version
from tinyassets.daemon_server import (
    initialize_author_server,
    save_branch_definition,
    update_branch_definition,
)

#: The subject `_branch_subject` authenticates as. Seeded branches are authored
#: by it because `patch_branch` author-gates: it needs the request subject to BE
#: the branch author, not merely authenticated. Left at the storage default the
#: author is "anonymous", which `_request_branch_actor` maps to "no subject" —
#: so no caller could ever match it and every patch here would be refused.
_SUBJECT = "tester"


@pytest.fixture(autouse=True)
def _branch_subject(authenticate_request):
    """Bind a request subject for the branch WRITE actions in this module.

    `build_branch` and `patch_branch` resolve the caller through
    `_request_branch_actor()`, which is credential-derived and ignores
    `UNIVERSE_SERVER_USER` by design. Without a subject they short-circuit to
    `Authenticated branch subject required.` before parsing the spec, so the
    fork_from validation these tests exercise never runs. The storage-level
    tests in this module are unaffected by having a subject bound.
    """
    authenticate_request(_SUBJECT)


def _seed_branch(base_path, branch_id: str = "b1", name: str = "Branch") -> dict:
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition

    initialize_author_server(base_path)
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name=name,
        author=_SUBJECT,
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[],
    )
    return save_branch_definition(base_path, branch_def=branch.to_dict())


def _publish(base_path, branch_id: str) -> str:
    from tinyassets.daemon_server import get_branch_definition

    bd = get_branch_definition(base_path, branch_def_id=branch_id)
    v = publish_branch_version(base_path, bd, publisher="alice")
    return v.branch_version_id


class TestForkFromField:
    def test_branch_has_no_fork_from_by_default(self, tmp_path):
        _seed_branch(tmp_path)
        from tinyassets.daemon_server import get_branch_definition

        bd = get_branch_definition(tmp_path, branch_def_id="b1")
        assert bd["fork_from"] is None

    def test_save_branch_with_fork_from(self, tmp_path):
        _seed_branch(tmp_path, "b1")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Branch 2")

        update_branch_definition(tmp_path, branch_def_id="b2", updates={"fork_from": bvid})
        from tinyassets.daemon_server import get_branch_definition

        bd = get_branch_definition(tmp_path, branch_def_id="b2")
        assert bd["fork_from"] == bvid

    def test_fork_from_immutable_after_set(self, tmp_path):
        _seed_branch(tmp_path, "b1")
        bvid1 = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2")
        bvid2 = _publish(tmp_path, "b2")

        _seed_branch(tmp_path, "b3", "Branch 3")
        update_branch_definition(tmp_path, branch_def_id="b3", updates={"fork_from": bvid1})

        with pytest.raises(ValueError, match="immutable"):
            update_branch_definition(tmp_path, branch_def_id="b3", updates={"fork_from": bvid2})

    def test_build_branch_with_fork_from_roundtrips(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_build

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1")
        bvid = _publish(tmp_path, "b1")

        spec = json.dumps({
            "name": "Forked Branch",
            "fork_from": bvid,
            "node_defs": [{"node_id": "n1", "display_name": "N1", "prompt_template": "hi"}],
            "edges": [{"from_node": "n1", "to_node": "END"}],
            "entry_point": "n1",
        })
        result = json.loads(_ext_branch_build({"spec_json": spec, "verbose": "true"}))
        assert result["status"] == "built"
        assert result["branch"]["fork_from"] == bvid

    def test_build_branch_invalid_fork_from_rejected(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_build

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_author_server(tmp_path)

        spec = json.dumps({
            "name": "Bad Fork",
            "fork_from": "not-a-real-version-id",
            "node_defs": [{"node_id": "n1", "display_name": "N1", "prompt_template": "hi"}],
            "edges": [{"from_node": "n1", "to_node": "END"}],
            "entry_point": "n1",
        })
        result = json.loads(_ext_branch_build({"spec_json": spec}))
        # An unresolvable `fork_from` is refused by the readability check in
        # `_ext_branch_build` (branches.py: `_resolve_readable_version` ->
        # `_branch_version_not_found`), which fires before spec validation. The
        # message is deliberately bare: a version that EXISTS but is not
        # readable by this caller must be indistinguishable from one that does
        # not exist. Do not restore the older, chattier "not a known
        # branch_version_id / pass a published id" wording here — it is more
        # helpful and it leaks existence.
        assert result["error"] == "Branch version 'not-a-real-version-id' not found."


class TestSetForkFromPatchOp:
    def test_set_fork_from_op_sets_lineage(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Branch 2")

        result = json.loads(_ext_branch_patch({
            "branch_def_id": "b2",
            "changes_json": json.dumps([
                {"op": "set_fork_from", "branch_version_id": bvid}
            ]),
            "verbose": "true",
        }))
        assert result["status"] == "patched"
        assert result["branch"]["fork_from"] == bvid

    def test_set_fork_from_invalid_version_rejected(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1")

        result = json.loads(_ext_branch_patch({
            "branch_def_id": "b1",
            "changes_json": json.dumps([
                {"op": "set_fork_from", "branch_version_id": "nonexistent-bvid"}
            ]),
        }))
        # Same non-disclosure contract as the build path: an unresolvable
        # version is a bare error, not a `status: rejected` envelope.
        assert result["error"] == "Branch version 'nonexistent-bvid' not found."

    def test_set_fork_from_immutable_after_set(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1")
        bvid1 = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2")
        bvid2 = _publish(tmp_path, "b2")
        _seed_branch(tmp_path, "b3", "Branch 3")

        _ext_branch_patch({
            "branch_def_id": "b3",
            "changes_json": json.dumps([{"op": "set_fork_from", "branch_version_id": bvid1}]),
        })
        result = json.loads(_ext_branch_patch({
            "branch_def_id": "b3",
            "changes_json": json.dumps([{"op": "set_fork_from", "branch_version_id": bvid2}]),
        }))
        assert result["status"] in ("partial", "rejected")


class TestForkTree:
    def test_fork_tree_no_lineage(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _action_fork_tree

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path)
        result = json.loads(_action_fork_tree({"branch_def_id": "b1"}))
        assert result["branch_def_id"] == "b1"
        assert result["ancestors"] == []
        assert result["descendant_count"] == 0

    def test_fork_tree_shows_descendant(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _action_fork_tree

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1", "Root")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Fork")
        update_branch_definition(tmp_path, branch_def_id="b2", updates={"fork_from": bvid})

        result = json.loads(_action_fork_tree({"branch_def_id": "b1"}))
        assert result["descendant_count"] == 1
        assert result["descendants"][0]["branch_def_id"] == "b2"

    def test_fork_tree_shows_ancestor(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _action_fork_tree

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1", "Root")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Fork")
        update_branch_definition(tmp_path, branch_def_id="b2", updates={"fork_from": bvid})

        result = json.loads(_action_fork_tree({"branch_def_id": "b2"}))
        assert len(result["ancestors"]) == 1
        assert result["ancestors"][0]["branch_def_id"] == "b1"

    def test_fork_tree_missing_branch_error(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _action_fork_tree

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_author_server(tmp_path)
        result = json.loads(_action_fork_tree({"branch_def_id": "nonexistent"}))
        assert "error" in result

    def test_fork_tree_missing_branch_def_id_error(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _action_fork_tree

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        initialize_author_server(tmp_path)
        result = json.loads(_action_fork_tree({}))
        assert "error" in result


class TestDescribeBranchLineageEnrichment:
    def test_describe_branch_includes_fork_from(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_describe

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Fork")
        update_branch_definition(tmp_path, branch_def_id="b2", updates={"fork_from": bvid})

        result = json.loads(_ext_branch_describe({"branch_def_id": "b2"}))
        assert result["fork_from"] == bvid

    def test_describe_branch_no_fork_from_is_none(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_describe

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path)

        result = json.loads(_ext_branch_describe({"branch_def_id": "b1"}))
        # `describe_branch` OMITS the key rather than emitting an explicit null
        # (branches.py: `if fork_from is not None`). Read it with `.get` — do
        # not "fix" the response to always carry the key. The same branch also
        # flattens an ancestor the viewer cannot read down to absent, so
        # "no lineage" and "lineage you may not see" are deliberately
        # indistinguishable here.
        assert result.get("fork_from") is None

    def test_describe_branch_includes_fork_descendants(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_describe

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path, "b1", "Root")
        bvid = _publish(tmp_path, "b1")
        _seed_branch(tmp_path, "b2", "Fork")
        update_branch_definition(tmp_path, branch_def_id="b2", updates={"fork_from": bvid})

        result = json.loads(_ext_branch_describe({"branch_def_id": "b1"}))
        assert len(result["fork_descendants"]) == 1
        desc = result["fork_descendants"][0]
        assert desc["branch_def_id"] == "b2"
        assert "author" in desc
        assert "published_versions_count" in desc

    def test_describe_branch_no_descendants_returns_empty_list(self, tmp_path, monkeypatch):
        from tinyassets.api.branches import _ext_branch_describe

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_branch(tmp_path)

        result = json.loads(_ext_branch_describe({"branch_def_id": "b1"}))
        assert result["fork_descendants"] == []
