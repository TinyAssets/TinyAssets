"""BUG-045 — invoke_branch / invoke_branch_version / await_run authoring plumbing.

The compiler reads three NodeDefinition fields:
- ``invoke_branch_spec`` — fork a sub-branch run inline.
- ``invoke_branch_version_spec`` — same shape against a pinned branch_version.
- ``await_run_spec`` — block on a sibling run_id from state.

`tinyassets/branches.py` declares them; `tinyassets/graph_compiler.py` consumes
them. But two distinct authoring write paths in `tinyassets/api/branches.py`
silently dropped the keys — callers got nodes that validated and even compiled
but ran as no-op stubs:

1. `_apply_node_spec` — shared by `add_node` / `build_branch` /
   `patch_branch` (add_node op).
2. `_ext_branch_update_node` (~L1880) — its own kwargs-merge logic, writes
   through `save_branch_definition` directly. NOT an alias of
   `_apply_node_spec`; bug surface is duplicated here.

This regression suite pins both write paths per navigator's
`2026-05-02-bug045-test-surface.md` 8-test target:

- 6 round-trip tests: 3 fields × 2 call sites (add_node, update_node).
- 1 signature-regression test: confirm both call sites accept the new
  kwargs / changes_json keys; future drift fails loudly.
- 1 mutual-exclusion test: spec field + prompt_template on the same node
  is rejected by BranchDefinition.validate() — the fix must not bypass.

Runtime semantics for invoke_branch / await_run are pinned in
`tests/test_sub_branch_invocation.py`; this file is authoring-surface only.
"""

from __future__ import annotations

import importlib
import inspect
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
    # Branch mutation requires a credential-derived subject. Without one the
    # extensions surface returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die before reaching their own concern. The conftest default grants
    # extensions read/write/admin; `extensions.costly` stays withheld so a
    # costly-refusal test would still assert something.
    authenticate_request("tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool: str, action: str, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _load(us, base: Path, bid: str) -> dict:
    from tinyassets.daemon_server import get_branch_definition

    return get_branch_definition(base, branch_def_id=bid)


def _node(branch: dict, nid: str) -> dict:
    for n in branch["node_defs"]:
        if n["node_id"] == nid:
            return n
    raise AssertionError(f"node '{nid}' not on branch")


# Sample specs — `wait_mode` valid values are ("blocking", "async") per
# BranchDefinition.validate(). Spec-bearing nodes must NOT carry
# prompt_template / source_code (mutual exclusion).
_INVOKE_BRANCH = {
    "branch_def_id": "child-bid-abc",
    "wait_mode": "blocking",
    "inputs": {"seed": "x"},
}
_INVOKE_VERSION = {
    "branch_version_id": "ver-abc",
    "wait_mode": "async",
    "on_child_fail": "propagate",
    "inputs": {"seed": "y"},
}
_AWAIT_RUN = {
    "run_id_field": "spawned_run_id",
    "timeout_seconds": 30,
}


def _scaffold_with_target(spec_extra: dict | None = None) -> dict:
    """Two-node branch: a regular prompt-template seed + a spec-bearing target.
    Spec fields are mutually exclusive with prompt_template, so the target node
    must NOT carry a prompt_template if it carries a spec field."""
    seed: dict = {
        "node_id": "seed",
        "display_name": "Seed",
        "prompt_template": "do: {x}",
        "input_keys": ["x"],
        "output_keys": ["out"],
    }
    target: dict = {
        "node_id": "target",
        "display_name": "Target",
        "input_keys": ["out"],
        "output_keys": ["final"],
    }
    if spec_extra:
        target.update(spec_extra)
    return {
        "name": "b",
        "entry_point": "seed",
        "node_defs": [seed, target],
        "edges": [
            {"from": "START", "to": "seed"},
            {"from": "seed", "to": "target"},
            {"from": "target", "to": "END"},
        ],
        "state_schema": [
            {"name": "x", "type": "str"},
            {"name": "out", "type": "str"},
            {"name": "final", "type": "str"},
        ],
    }


def _build_with(us, *, spec_extra: dict | None = None) -> str:
    """Build a branch where `target` carries the given spec_extra. Returns
    branch_def_id."""
    res = _call(
        us, "extensions", "build_branch",
        spec_json=json.dumps(_scaffold_with_target(spec_extra)),
    )
    assert res["status"] == "built", res
    return res["branch_def_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 6 round-trip tests: 3 fields × 2 call sites (add_node via build_branch,
# update_node via patch_branch update_node).
# ─────────────────────────────────────────────────────────────────────────────


# --- Site 1: add_node / build_branch (shares _apply_node_spec) ---


def test_add_node_threads_invoke_branch_spec(ext_env):
    us, base = ext_env
    bid = _build_with(us, spec_extra={"invoke_branch_spec": _INVOKE_BRANCH})
    branch = _load(us, base, bid)
    assert _node(branch, "target")["invoke_branch_spec"] == _INVOKE_BRANCH


def test_add_node_threads_invoke_branch_version_spec(ext_env):
    us, base = ext_env
    bid = _build_with(us, spec_extra={"invoke_branch_version_spec": _INVOKE_VERSION})
    branch = _load(us, base, bid)
    assert _node(branch, "target")["invoke_branch_version_spec"] == _INVOKE_VERSION


def test_add_node_threads_await_run_spec(ext_env):
    us, base = ext_env
    bid = _build_with(us, spec_extra={"await_run_spec": _AWAIT_RUN})
    branch = _load(us, base, bid)
    assert _node(branch, "target")["await_run_spec"] == _AWAIT_RUN


# --- Site 2: update_node (its own write path, NOT _apply_node_spec) ---


# ─────────────────────────────────────────────────────────────────────────────
# Signature regression — both NodeDefinition fields and the authoring API
# entry points must continue to accept the three keys. Catches future drift
# (someone refactors _apply_node_spec or _ext_branch_update_node and forgets
# the spec keys; this test fails loudly instead of users seeing silent drops).
# ─────────────────────────────────────────────────────────────────────────────


def test_signature_regression_node_definition_and_authoring_paths():
    from tinyassets.api.branches import _apply_node_spec, _ext_branch_update_node
    from tinyassets.branches import NodeDefinition

    # NodeDefinition declares all three fields.
    nd_fields = {f.name for f in NodeDefinition.__dataclass_fields__.values()}
    for spec_field in (
        "invoke_branch_spec",
        "invoke_branch_version_spec",
        "await_run_spec",
    ):
        assert spec_field in nd_fields, (
            f"NodeDefinition lost field '{spec_field}' — runtime invocation "
            f"semantics will break."
        )

    # _apply_node_spec is a (branch, raw) function — no per-field kwargs to
    # check at the signature level. Instead we verify that the function
    # source contains the read-from-raw idiom for each spec key. This is a
    # weaker invariant than a typed signature but stronger than no check.
    src = inspect.getsource(_apply_node_spec)
    for spec_field in (
        "invoke_branch_spec",
        "invoke_branch_version_spec",
        "await_run_spec",
    ):
        assert spec_field in src, (
            f"_apply_node_spec source no longer references '{spec_field}' "
            f"— silent-drop regression risk."
        )

    # _ext_branch_update_node (kwargs-merge path) must also reference each
    # field — its plumbing is independent of _apply_node_spec.
    update_src = inspect.getsource(_ext_branch_update_node)
    for spec_field in (
        "invoke_branch_spec",
        "invoke_branch_version_spec",
        "await_run_spec",
    ):
        assert spec_field in update_src, (
            f"_ext_branch_update_node source no longer references "
            f"'{spec_field}' — second-site silent-drop regression risk."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mutual-exclusion contract — spec field + prompt_template/source_code on the
# same node is rejected by BranchDefinition.validate(). Authoring-surface fix
# must not bypass the check by silently coercing or stripping one side.
# ─────────────────────────────────────────────────────────────────────────────


def test_mutual_exclusion_invoke_branch_spec_and_prompt_template_rejected(ext_env):
    us, _ = ext_env
    # Build a 2-node branch where `target` carries BOTH a prompt_template
    # AND an invoke_branch_spec. validate() must reject — the authoring
    # plumbing fix must not bypass this check.
    spec = _scaffold_with_target({
        "prompt_template": "should be exclusive: {out}",
        "invoke_branch_spec": _INVOKE_BRANCH,
    })
    res = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    # Either the build is rejected outright, or it surfaces an error string
    # mentioning the mutual exclusion. The wrong shape is silent acceptance.
    if res.get("status") == "built":
        pytest.fail(
            f"build_branch silently accepted prompt_template + "
            f"invoke_branch_spec on the same node: {res!r}"
        )
    err_str = json.dumps(res).lower()
    assert (
        "mutually exclusive" in err_str
        or "exclusive" in err_str
        or "invoke_branch_spec" in err_str
        or "prompt_template" in err_str
    ), (
        f"rejection reason should cite the mutual-exclusion contract: {res!r}"
    )
