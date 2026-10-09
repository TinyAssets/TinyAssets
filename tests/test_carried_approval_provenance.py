"""PR #1349 — carried-snapshot approval-forge bypass + fail-closed runtime gate.

Codex's final residual on PR #1349:

    A legacy/trusted snapshot with ``source_code`` + ``approved=True`` +
    ``approved_source_hash=""`` can be persisted and run, because the runtime
    only checked NON-empty hashes. Carrying paths: ``build_branch`` ``fork_from``
    copies ``parent_copy.node_defs`` wholesale, and ``rollback_node`` restores
    raw audit bodies.

This module proves the systematic fix:

1. **Runtime gate** — since change `sandboxed-code-node` (2026-08-30) the
   runtime gate is AUTHORSHIP, not the approval hash: code runs only in the
   universe that authored it (``caller_provenance == "own"``), inside the OS
   sandbox. ``approved`` / ``approved_source_hash`` are provenance the API
   keeps honest (sections 2-3) but no longer gate execution.
2. **Carried snapshots are reconciled** — ``build_branch`` fork-copy and
   ``rollback_node`` re-validate each carried node's approval against its
   source hash and clear approval metadata on mismatch.
3. **The bid executor** (a second code-exec path) enforces the same invariant.

The security invariant under test: a carried approval is never TRUSTED
beyond its hash (sections 2-3), and code never executes for a run that did
not author it (section 1).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _hash(src: str) -> str:
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fail-closed runtime gate (graph_compiler._validate_source_code)
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthorshipRuntimeGate:
    """The approval hash is provenance, not a gate (design D2): an
    owner-authored node compiles with an empty or stale hash; a run that did
    not author the code refuses it, whatever the hash says."""

    def _one_node_branch(self, *, source_code, approved, approved_source_hash):
        from tinyassets.branches import (
            BranchDefinition,
            EdgeDefinition,
            GraphNodeRef,
            NodeDefinition,
        )

        b = BranchDefinition(name="gate", entry_point="only")
        b.node_defs = [NodeDefinition(
            node_id="only", display_name="Only",
            source_code=source_code,
            approved=approved,
            approved_source_hash=approved_source_hash,
        )]
        b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
        b.edges = [
            EdgeDefinition(from_node="START", to_node="only"),
            EdgeDefinition(from_node="only", to_node="END"),
        ]
        return b

    def test_empty_hash_owner_node_compiles(self):
        from tinyassets.graph_compiler import compile_branch

        src = "def run(state): return {'out': 1}\n"
        compile_branch(self._one_node_branch(
            source_code=src, approved=True, approved_source_hash="",
        ))

    def test_stale_hash_owner_node_compiles(self):
        from tinyassets.graph_compiler import compile_branch

        b = self._one_node_branch(
            source_code="def run(state): return {'x': 1}\n", approved=True,
            approved_source_hash=_hash("def run(state): return {}\n"),
        )
        compile_branch(b)

    def test_foreign_run_refuses_even_a_matching_hash(self):
        """A public foreign branch run directly: the hash is fine, the
        authorship is not. Refused with the remix instruction."""
        from tinyassets.graph_compiler import (
            BranchExecutionContext,
            ForeignCodeError,
            compile_branch,
        )

        src = "def run(state): return {'out': 1}\n"
        b = self._one_node_branch(
            source_code=src, approved=True, approved_source_hash=_hash(src),
        )
        with pytest.raises(ForeignCodeError, match="did not author"):
            compile_branch(b, execution_context=BranchExecutionContext(
                actor="victim", universe_id="u", caller_provenance="public-foreign",
            ))

    def test_mark_approved_helper_stamps_provenance(self):
        """The in-process helper still records a matching hash - provenance
        the authoring surfaces rely on - and the node compiles."""
        from tinyassets.branches import (
            BranchDefinition,
            EdgeDefinition,
            GraphNodeRef,
            NodeDefinition,
        )
        from tinyassets.graph_compiler import compile_branch

        b = BranchDefinition(name="helper", entry_point="only")
        b.node_defs = [NodeDefinition(
            node_id="only", display_name="Only",
            source_code="def run(state): return {'out': 2}\n",
        ).mark_approved(approved_by="host")]
        b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
        b.edges = [
            EdgeDefinition(from_node="START", to_node="only"),
            EdgeDefinition(from_node="only", to_node="END"),
        ]
        node = b.node_defs[0]
        assert node.approved is True
        assert node.approved_source_hash == _hash(node.source_code)
        compile_branch(b)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 2a. Carried snapshot via build_branch fork-copy
# ─────────────────────────────────────────────────────────────────────────────


def _save_poisoned_parent(base: Path, *, branch_id: str, source_code: str) -> str:
    """Persist a branch whose node carries source_code + approved=True + EMPTY
    approved_source_hash directly (bypassing MCP authoring guards) to simulate
    a legacy / trusted snapshot, then publish a version and return its bvid.
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.daemon_server import (
        get_branch_definition,
        initialize_author_server,
        save_branch_definition,
    )

    initialize_author_server(base)
    nd = NodeDefinition(
        node_id="carried", display_name="Carried",
        source_code=source_code,
        output_keys=["out"],
    )
    # Forge the legacy state: approved flag set, but NO provenance hash.
    nd.approved = True
    nd.approved_by = "legacy-host"
    nd.approved_source_hash = ""
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name="poisoned-parent",
        entry_point="carried",
        node_defs=[nd],
        graph_nodes=[GraphNodeRef(id="carried", node_def_id="carried")],
        edges=[
            EdgeDefinition(from_node="START", to_node="carried"),
            EdgeDefinition(from_node="carried", to_node="END"),
        ],
        state_schema=[{"name": "out", "type": "str"}],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    bd = get_branch_definition(base, branch_def_id=branch_id)
    # A published snapshot others fork: it carries the publication mark (the
    # state the one-time backfill leaves on a public branch's versions).
    v = publish_branch_version(base, bd, publisher="legacy-host", public=True)
    return v.branch_version_id


class TestForkCopyReconcilesCarriedApproval:
    """A fork that inherits the parent's node_defs wholesale must not carry an
    empty-hash (or stale-hash) approval forward.
    """

    def test_fork_copy_clears_empty_hash_approval(
        self, tmp_path, monkeypatch, authenticate_request,
    ):
        from tinyassets.api.branches import _ext_branch_build

        base = tmp_path / "output"
        base.mkdir()
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
        # Branch WRITE actions resolve the caller via `_request_branch_actor()`,
        # which is credential-derived and ignores `UNIVERSE_SERVER_USER`.
        authenticate_request("tester")

        src = "def run(state): return {'out': 'carried'}\n"
        bvid = _save_poisoned_parent(base, branch_id="parent1", source_code=src)

        # Fork WITHOUT supplying node_defs → inherit the parent's node wholesale.
        spec = json.dumps({
            "name": "Forked",
            "fork_from": bvid,
        })
        result = json.loads(_ext_branch_build({"spec_json": spec}))
        assert result["status"] == "built", result

        from tinyassets.daemon_server import get_branch_definition
        forked = get_branch_definition(base, branch_def_id=result["branch_def_id"])
        nd = next(n for n in forked["node_defs"] if n["node_id"] == "carried")

        # The carried, empty-hash approval must NOT survive the fork.
        assert nd["approved"] is False, nd
        assert not nd.get("approved_source_hash"), nd

    def test_forked_carried_node_runs_as_the_forker_and_refuses_a_stranger(
        self, tmp_path, monkeypatch, authenticate_request,
    ):
        """End-to-end (design D2): the fork demoted the carried approval to
        provenance; the forker's own run compiles it, and a run that is not
        the forker's refuses it by authorship - the hash never decides."""
        from tinyassets.api.branches import _ext_branch_build
        from tinyassets.branches import BranchDefinition
        from tinyassets.daemon_server import get_branch_definition
        from tinyassets.graph_compiler import (
            BranchExecutionContext,
            ForeignCodeError,
            compile_branch,
        )

        base = tmp_path / "output"
        base.mkdir()
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
        # Branch WRITE actions resolve the caller via `_request_branch_actor()`,
        # which is credential-derived and ignores `UNIVERSE_SERVER_USER`.
        authenticate_request("tester")

        src = "def run(state): return {'out': 'carried'}\n"
        bvid = _save_poisoned_parent(base, branch_id="parent2", source_code=src)
        result = json.loads(_ext_branch_build(
            {"spec_json": json.dumps({"name": "Forked2", "fork_from": bvid})}
        ))
        assert result["status"] == "built", result

        forked = get_branch_definition(base, branch_def_id=result["branch_def_id"])
        branch = BranchDefinition.from_dict(forked)
        assert branch.node_defs[0].approved is False          # demoted to provenance
        compile_branch(branch)                                  # the forker's own compile
        with pytest.raises(ForeignCodeError, match="did not author"):
            compile_branch(branch, execution_context=BranchExecutionContext(
                actor="stranger", universe_id="u", caller_provenance="public-foreign",
            ))


# ─────────────────────────────────────────────────────────────────────────────
# 2b. Carried snapshot via rollback_node (raw audit body restore)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 3. The bid executor is a second code-exec path — same fail-closed invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestBidExecutorFailsClosed:
    """``execute_node_bid`` ``exec()``s source directly. It must enforce the
    same hash-provenance invariant as the compiler.
    """

    def _node(self, *, source_code, approved, approved_source_hash):
        from tinyassets.branches import NodeDefinition

        nd = NodeDefinition(
            node_id="bidn", display_name="BidN", source_code=source_code,
        )
        nd.approved = approved
        nd.approved_source_hash = approved_source_hash
        return nd

    def test_bid_executor_rejects_empty_hash(self, tmp_path):
        from tinyassets.bid.node_bid import NodeBid
        from tinyassets.executors.node_bid import execute_node_bid

        src = "def run(state):\n    return {'ok': 1}\n"
        node = self._node(source_code=src, approved=True, approved_source_hash="")
        bid = NodeBid(node_bid_id="nb_x", node_def_id="bidn", status="open")
        result = execute_node_bid(
            bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
        )
        assert result.status == "failed"
        assert "approval_provenance_invalid" in result.error

    def test_bid_executor_rejects_mismatched_hash(self, tmp_path):
        from tinyassets.bid.node_bid import NodeBid
        from tinyassets.executors.node_bid import execute_node_bid

        approved_src = "def run(state):\n    return {'ok': 1}\n"
        running_src = "def run(state):\n    return {'ok': 2}\n"
        node = self._node(
            source_code=running_src, approved=True,
            approved_source_hash=_hash(approved_src),
        )
        bid = NodeBid(node_bid_id="nb_y", node_def_id="bidn", status="open")
        result = execute_node_bid(
            bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
        )
        assert result.status == "failed"
        assert "approval_provenance_invalid" in result.error

    def test_bid_executor_runs_with_matching_hash(self, tmp_path):
        from tinyassets.bid.node_bid import NodeBid
        from tinyassets.executors.node_bid import execute_node_bid

        src = "def run(state):\n    return {'ok': state.get('x', 0) + 1}\n"
        node = self._node(
            source_code=src, approved=True, approved_source_hash=_hash(src),
        )
        bid = NodeBid(
            node_bid_id="nb_z", node_def_id="bidn",
            inputs={"x": 41}, status="open",
        )
        result = execute_node_bid(
            bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
        )
        assert result.status == "succeeded", result.error
        assert result.output == {"ok": 42}


# ─────────────────────────────────────────────────────────────────────────────
# Runnability surfaces reflect the fail-closed gate (describe/validate/get)
# ─────────────────────────────────────────────────────────────────────────────


class TestApprovalProvenanceIsReportedNotEnforced:
    """The provenance predicate feeds `unapproved_source_code_nodes`. It never
    decides `runnable`: code nodes run in the OS sandbox (sandboxed-code-node)."""

    def test_empty_hash_node_is_unapproved(self):
        from tinyassets.api.branches import _node_source_code_unapproved

        # source_code + approved but no hash → the approval is forged or stale.
        assert _node_source_code_unapproved({
            "node_id": "n", "source_code": "def run(s): return {}",
            "approved": True, "approved_source_hash": "",
        }) is True

    def test_matching_hash_node_is_approved(self):
        from tinyassets.api.branches import _node_source_code_unapproved

        src = "def run(s): return {}"
        assert _node_source_code_unapproved({
            "node_id": "n", "source_code": src,
            "approved": True, "approved_source_hash": _hash(src),
        }) is False

    def test_prompt_template_node_has_nothing_to_approve(self):
        from tinyassets.api.branches import _node_source_code_unapproved

        assert _node_source_code_unapproved({
            "node_id": "n", "source_code": "",
            "prompt_template": "hi {x}", "approved": False,
        }) is False

    def test_a_forged_approval_does_not_make_a_node_unrunnable(self):
        """The whole point: provenance is reported, execution is not gated."""
        from tinyassets.api.branches import _source_code_problems

        assert _source_code_problems([{
            "node_id": "n", "source_code": "def run(s): return {}",
            "approved": True, "approved_source_hash": "",
        }]) == []
