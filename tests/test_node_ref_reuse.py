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


def _seed_standalone(node_id: str, display_name: str,
                     source: str = "def run(state): return state\n"):
    """Put a node in the standalone registry through storage.

    The registry's own writer (``extensions register``) is no longer
    reachable from the public surface, but the shadow guard still reads
    the registry, so the tests seed it directly.
    """
    from tinyassets.api.extensions import (
        NodeRegistration,
        _load_nodes,
        _save_nodes,
    )

    nodes = _load_nodes()
    nodes.append(NodeRegistration(
        node_id=node_id,
        display_name=display_name,
        description=f"Standalone {display_name}",
        phase="custom",
        input_keys=["state"],
        output_keys=["state"],
        source_code=source,
        author="tester",
        enabled=True,
    ).to_dict())
    _save_nodes(nodes)


def _build_empty_branch(us, name: str = "b") -> str:
    spec = {
        "name": name,
        "entry_point": "seed",
        "node_defs": [{
            "node_id": "seed",
            "display_name": "Seed",
            "prompt_template": "start: {x}",
        }],
        "edges": [
            {"from": "START", "to": "seed"},
            {"from": "seed", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    result = _call(us, "extensions", "build_branch",
                   spec_json=json.dumps(spec))
    assert result["status"] == "built", result
    return result["branch_def_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Silent shadowing is refused
# ─────────────────────────────────────────────────────────────────────────────


class TestHollowNodeShadowRefused:
    """Bare node_id collision must loudly reject, not silently hollow-clone."""

    def test_build_branch_with_colliding_node_id_errors(self, ext_env):
        us, _ = ext_env
        _seed_standalone("rigor_checker", "Rigor Checker")
        spec = {
            "name": "shadow-attempt",
            "entry_point": "rigor_checker",
            "node_defs": [{
                "node_id": "rigor_checker",
                "display_name": "Silent Clone",
                "prompt_template": "x",
            }],
            "edges": [
                {"from": "START", "to": "rigor_checker"},
                {"from": "rigor_checker", "to": "END"},
            ],
            "state_schema": [{"name": "y", "type": "str"}],
        }
        result = _call(us, "extensions", "build_branch",
                       spec_json=json.dumps(spec))
        assert result["status"] == "rejected"
        combined = " ".join(result.get("errors") or []).lower()
        assert "standalone" in combined
        assert "node_ref" in combined or "intent" in combined

    def test_patch_branch_add_node_colliding_id_errors(self, ext_env):
        us, _ = ext_env
        _seed_standalone("rigor_checker", "Rigor Checker")
        bid = _build_empty_branch(us)
        ops = [{
            "op": "add_node",
            "node_id": "rigor_checker",
            "display_name": "Silent Clone",
        }]
        result = _call(us, "extensions", "patch_branch",
                       branch_def_id=bid,
                       changes_json=json.dumps(ops))
        assert result.get("status") == "rejected"
        joined = json.dumps(result).lower()
        assert "standalone" in joined


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

    def test_build_branch_node_ref_preserves_source_approval(self, ext_env):
        us, base = ext_env
        source = _seed_approved_source_branch(
            base, "def run(state): return {'manifest': 'ok'}\n",
        )

        spec = {
            "name": "approved-node-ref",
            "entry_point": "approved_recipe",
            "node_defs": [{
                "node_id": "approved_recipe",
                "display_name": "",
                "node_ref": {
                    "source": source,
                    "node_id": "approved_recipe",
                },
            }],
            "edges": [
                {"from": "START", "to": "approved_recipe"},
                {"from": "approved_recipe", "to": "END"},
            ],
            "state_schema": [
                {"name": "manifest", "type": "str"},
                {"name": "state", "type": "dict"},
            ],
        }
        built = _call(us, "extensions", "build_branch",
                      spec_json=json.dumps(spec))
        assert built["status"] == "built", built

        from tinyassets.daemon_server import get_branch_definition
        branch = get_branch_definition(base, branch_def_id=built["branch_def_id"])
        nd = next(
            n for n in branch["node_defs"]
            if n["node_id"] == "approved_recipe"
        )
        assert nd["approved"] is True

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
# SECURITY: approval provenance must follow the executable content
# (Codex ADAPT review, PR #1349)
# ─────────────────────────────────────────────────────────────────────────────


def _seed_approved_source_branch(base: Path, source_code: str) -> str:
    """Persist a branch whose code node carries a genuine, hash-backed
    approval by a distinct actor, straight through branch storage, and
    return its id. A ``node_ref`` naming a readable branch is the live copy
    path (``write_graph target=branch create``); the registry ``approve``
    action that used to set this up is not.
    """
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )

    initialize_author_server(base)
    nd = NodeDefinition(
        node_id="approved_recipe",
        display_name="Approved Recipe",
        description="Approved Recipe",
        input_keys=["manifest"],
        output_keys=["manifest"],
        source_code=source_code,
    ).mark_approved(approved_by="host-operator")
    branch = BranchDefinition(
        branch_def_id="approved-source",
        name="approved-source",
        author="tester",
        entry_point="approved_recipe",
        node_defs=[nd],
        graph_nodes=[GraphNodeRef(id="approved_recipe", node_def_id="approved_recipe")],
        edges=[
            EdgeDefinition(from_node="START", to_node="approved_recipe"),
            EdgeDefinition(from_node="approved_recipe", to_node="END"),
        ],
        state_schema=[{"name": "manifest", "type": "str"}],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    return branch.branch_def_id


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

    def test_node_ref_with_source_override_comes_out_unapproved(
        self, ext_env,
    ):
        us, base = ext_env
        source = _seed_approved_source_branch(base, self.APPROVED_SRC)

        # node_ref the approved node but OVERRIDE its source_code. The
        # inherited approved=True must NOT survive the content change.
        spec = {
            "name": "approval-forge-attempt",
            "entry_point": "approved_recipe",
            "node_defs": [{
                "node_id": "approved_recipe",
                "display_name": "",
                "node_ref": {
                    "source": source,
                    "node_id": "approved_recipe",
                },
                "source_code": self.MALICIOUS_SRC,
            }],
            "edges": [
                {"from": "START", "to": "approved_recipe"},
                {"from": "approved_recipe", "to": "END"},
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
            if n["node_id"] == "approved_recipe"
        )
        # The overridden body landed, but approval did NOT carry over.
        assert "forged-by-pwned" in nd["source_code"]
        assert nd["approved"] is False, nd
        assert not nd.get("approved_source_hash"), nd

    def test_node_ref_with_source_override_fails_execution_gate(
        self, ext_env,
    ):
        us, base = ext_env
        source = _seed_approved_source_branch(base, self.APPROVED_SRC)
        spec = {
            "name": "approval-forge-run-attempt",
            "entry_point": "approved_recipe",
            "node_defs": [{
                "node_id": "approved_recipe",
                "display_name": "",
                "node_ref": {
                    "source": source,
                    "node_id": "approved_recipe",
                },
                "source_code": self.MALICIOUS_SRC,
            }],
            "edges": [
                {"from": "START", "to": "approved_recipe"},
                {"from": "approved_recipe", "to": "END"},
            ],
            "state_schema": [{"name": "manifest", "type": "str"}],
        }
        built = _call(us, "extensions", "build_branch",
                      spec_json=json.dumps(spec))
        assert built["status"] == "built", built

        from tinyassets.branches import BranchDefinition
        from tinyassets.daemon_server import get_branch_definition
        from tinyassets.graph_compiler import (
            BranchExecutionContext,
            ForeignCodeError,
            compile_branch,
        )

        branch = get_branch_definition(base, branch_def_id=built["branch_def_id"])
        bdef = BranchDefinition.from_dict(branch)
        # The override demoted the approval to provenance (D2): the node is
        # UNAPPROVED, the caller's own compile still runs it, and a run that did
        # not author it refuses by authorship - the hash never decides.
        assert all(not nd.approved for nd in bdef.node_defs if nd.source_code), [
            (nd.node_id, nd.approved) for nd in bdef.node_defs
        ]
        compile_branch(bdef)
        with pytest.raises(ForeignCodeError, match="did not author"):
            compile_branch(bdef, execution_context=BranchExecutionContext(
                actor="stranger", universe_id="u", caller_provenance="public-foreign",
            ))

    def test_clean_node_ref_copy_stays_approved_and_runs(
        self, ext_env,
    ):
        """Guard the legit path: a node_ref copy with NO source override
        must keep approval (hash still matches) and compile cleanly.
        """
        us, base = ext_env
        source = _seed_approved_source_branch(base, self.APPROVED_SRC)
        spec = {
            "name": "approval-clean-copy",
            "entry_point": "approved_recipe",
            "node_defs": [{
                "node_id": "approved_recipe",
                "display_name": "",
                "node_ref": {
                    "source": source,
                    "node_id": "approved_recipe",
                },
            }],
            "edges": [
                {"from": "START", "to": "approved_recipe"},
                {"from": "approved_recipe", "to": "END"},
            ],
            "state_schema": [{"name": "manifest", "type": "str"}],
        }
        built = _call(us, "extensions", "build_branch",
                      spec_json=json.dumps(spec))
        assert built["status"] == "built", built

        from tinyassets.branches import BranchDefinition
        from tinyassets.daemon_server import get_branch_definition
        from tinyassets.graph_compiler import compile_branch

        branch = get_branch_definition(base, branch_def_id=built["branch_def_id"])
        nd = next(
            n for n in branch["node_defs"]
            if n["node_id"] == "approved_recipe"
        )
        assert nd["approved"] is True, nd
        assert nd["approved_source_hash"], nd
        # Clean copy compiles without raising — provenance hash matches.
        compile_branch(BranchDefinition.from_dict(branch))

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
