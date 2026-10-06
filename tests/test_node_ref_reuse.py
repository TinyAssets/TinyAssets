"""#66: cross-branch node reuse via explicit node_ref + copy intent.

Before: ``extensions action=add_node`` silently created a hollow node
whenever the caller's ``node_id`` collided with an existing standalone
registered node. No error, no warning — the hollow clone replaced the
canonical body. That made #62 (cross-branch node-reuse discovery)
architecturally pointless: even if the bot found a rigor_checker to
reuse, saying "add_node node_id=rigor_checker" just made a new empty
node_def, not a copy of the canonical one.

After: add_node / build_branch / patch_branch's add_node op refuse the
shadow and point the caller at ``node_ref_json`` (for atomic add_node)
or a ``node_ref`` field inside the spec/ops (for composite paths).
``intent="copy"`` is the explicit consent override for "I know this
collides and I want the existing body".
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def ext_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch mutation requires a credential-derived subject. Without one
    # `extensions build_branch` returns
    # `{"error": "Authenticated branch subject required."}` and every test
    # here dies on `KeyError: 'status'` before reaching its own concern.
    # The conftest default (extensions read/write/admin) is exactly what
    # this file needs; `extensions.costly` is deliberately NOT granted.
    authenticate_request("tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool: str, action: str, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


# ─────────────────────────────────────────────────────────────────────────────
# Silent shadowing is refused
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Explicit reuse works
# ─────────────────────────────────────────────────────────────────────────────


class TestExplicitNodeRefCopiesCanonicalBody:
    """node_ref_json / node_ref in spec copies the canonical body."""

    def test_build_branch_node_ref_copies_from_other_branch(self, ext_env):
        us, base = ext_env
        # Seed source branch with a custom node.
        source_spec = {
            "name": "source-branch",
            "entry_point": "shared_audit",
            "node_defs": [{
                "node_id": "shared_audit",
                "display_name": "Shared Audit",
                "prompt_template": "audit: {x}",
                "description": "canonical audit node",
            }],
            "edges": [
                {"from": "START", "to": "shared_audit"},
                {"from": "shared_audit", "to": "END"},
            ],
            "state_schema": [{"name": "x", "type": "str"}],
        }
        source = _call(us, "extensions", "build_branch",
                       spec_json=json.dumps(source_spec))
        assert source["status"] == "built"
        source_bid = source["branch_def_id"]

        # Target branch reuses shared_audit via node_ref.
        target_spec = {
            "name": "target-branch",
            "entry_point": "shared_audit",
            "node_defs": [{
                "node_id": "shared_audit",
                "display_name": "",
                "node_ref": {
                    "source": source_bid,
                    "node_id": "shared_audit",
                },
            }],
            "edges": [
                {"from": "START", "to": "shared_audit"},
                {"from": "shared_audit", "to": "END"},
            ],
            "state_schema": [{"name": "x", "type": "str"}],
        }
        target = _call(us, "extensions", "build_branch",
                       spec_json=json.dumps(target_spec))
        assert target["status"] == "built", target
        from tinyassets.daemon_server import get_branch_definition
        branch = get_branch_definition(base, branch_def_id=target["branch_def_id"])
        nd = next(
            n for n in branch["node_defs"]
            if n["node_id"] == "shared_audit"
        )
        assert nd["prompt_template"] == "audit: {x}"
        assert nd["description"] == "canonical audit node"

    def test_build_branch_raw_approved_field_cannot_bypass_approval(self, ext_env):
        us, base = ext_env
        spec = {
            "name": "approval-bypass-attempt",
            "entry_point": "unsafe_recipe",
            "node_defs": [{
                "node_id": "unsafe_recipe",
                "display_name": "Unsafe Recipe",
                "source_code": "def run(state): return {'manifest': 'ok'}\n",
                "approved": True,
            }],
            "edges": [
                {"from": "START", "to": "unsafe_recipe"},
                {"from": "unsafe_recipe", "to": "END"},
            ],
            "state_schema": [{"name": "manifest", "type": "str"}],
        }
        built = _call(us, "extensions", "build_branch",
                      spec_json=json.dumps(spec))
        assert built["status"] == "built", built

        from tinyassets.daemon_server import get_branch_definition
        branch = get_branch_definition(base, branch_def_id=built["branch_def_id"])
        nd = next(
            n for n in branch["node_defs"]
            if n["node_id"] == "unsafe_recipe"
        )
        assert nd["approved"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Intent edge cases
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY: approval provenance must follow the executable content
# (Codex ADAPT review, PR #1349)
# ─────────────────────────────────────────────────────────────────────────────


def _approve_standalone(us, authenticate_request, node_id: str):
    """Approve a standalone node as a DISTINCT actor (the gate rejects
    self-approval), then restore the original actor.

    Switches actor by re-authenticating, NOT by setting
    `UNIVERSE_SERVER_USER`: `_current_actor` prefers the request identity
    and only falls back to the env var when there is none. Once the fixture
    authenticates `tester`, the env switch is silently ignored and this
    becomes a SELF-approval, which the gate correctly refuses — that is why
    these tests were quarantined, not flakiness.
    """
    authenticate_request("host-operator")
    try:
        result = _call(us, "extensions", "approve", node_id=node_id)
    finally:
        authenticate_request("tester")
    return result


class TestNodeRefSourceOverrideCannotForgeApproval:
    """A caller must not be able to node_ref an approved source_code node,
    override ``source_code`` with different code, and keep ``approved=True``.

    Codex flagged this as the live bypass: approval provenance was checked
    only as a boolean, so forged/stale approval could authorize code the
    approver never reviewed. Approval must be bound to the source hash at
    both authoring time (the persisted node comes out unapproved) and run
    time (the compiler refuses to execute a hash-mismatched node).
    """

    APPROVED_SRC = "def run(state): return {'manifest': 'approved'}\n"
    # Different executable body than what was approved — the forged-approval
    # surface. Marker string lets us assert the override actually landed.
    MALICIOUS_SRC = "def run(state): return {'manifest': 'forged-by-pwned'}\n"

    def _approved_standalone(self, us, authenticate_request):
        # input/output keys must match the branch state_schema below.
        _call(
            us, "extensions", "register",
            node_id="approved_recipe",
            display_name="Approved Recipe",
            description="Approved Recipe",
            phase="custom",
            input_keys="manifest",
            output_keys="manifest",
            source_code=self.APPROVED_SRC,
        )
        approved = _approve_standalone(
            us, authenticate_request, "approved_recipe",
        )
        assert approved["approved"] is True, approved
        assert approved["approved_source_hash"], approved
        return approved["approved_source_hash"]

    def test_runtime_gate_rejects_hash_mismatch_directly(self):
        """Unit-level: an ``approved=True`` node whose hash does not match its
        source is not TRUSTED by the hash - but since `sandboxed-code-node`
        the hash gates nothing at compile; authorship does.
        """
        from tinyassets.api.branches import _source_code_hash
        from tinyassets.branches import (
            BranchDefinition,
            EdgeDefinition,
            GraphNodeRef,
            NodeDefinition,
        )
        from tinyassets.graph_compiler import (
            BranchExecutionContext,
            ForeignCodeError,
            compile_branch,
        )

        approved_src = "def run(state): return {}\n"
        running_src = "def run(state): return {'x': 1}\n"  # different body
        b = BranchDefinition(name="forged", entry_point="only")
        b.node_defs = [NodeDefinition(
            node_id="only", display_name="Only",
            source_code=running_src,
            approved=True,
            approved_source_hash=_source_code_hash(approved_src),
        )]
        b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
        b.edges = [
            EdgeDefinition(from_node="START", to_node="only"),
            EdgeDefinition(from_node="only", to_node="END"),
        ]
        # D2: a stale hash is provenance, not a gate - the caller's own compile
        # runs the node; authorship is what refuses.
        compile_branch(b)
        with pytest.raises(ForeignCodeError, match="did not author"):
            compile_branch(b, execution_context=BranchExecutionContext(
                actor="stranger", universe_id="u", caller_provenance="public-foreign",
            ))
