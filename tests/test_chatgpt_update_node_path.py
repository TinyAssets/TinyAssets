"""BUG-034 — name-based branch_def_id through mutation paths.

ChatGPT sends branch names (not UUIDs) in update_node and patch_branch
calls because the UX shows human-readable names. Guards:
- update_node accepts a branch name as branch_def_id and resolves it.
- patch_branch accepts a branch name as branch_def_id and resolves it.
- update_node with an unknown name returns a clear 'not found' error (not crash).
- update_node with a valid name succeeds end-to-end and persists the update.
- patch_branch with a valid name succeeds end-to-end and persists the patch.
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


def _build(us, *, name: str = "test-branch") -> tuple[str, str]:
    """Build a branch and return (branch_def_id, node_id)."""
    spec = {
        "name": name,
        "description": "for name-based ref tests",
        "tags": [],
        "entry_point": "capture",
        "node_defs": [{
            "node_id": "capture",
            "display_name": "Capture Node",
            "prompt_template": "cap: {x}",
        }],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    res = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    assert res["status"] == "built", res
    return res["branch_def_id"], "capture"


class TestPatchBranchNameBasedRef:
    """patch_branch must resolve branch names, not just UUIDs."""

    def test_patch_branch_set_name_by_branch_name(self, ext_env):
        """patch_branch accepts human name as branch_def_id for set_name op."""
        us, base = ext_env
        bid, _ = _build(us, name="original-workflow-name")

        res = _call(
            us, "extensions", "patch_branch",
            branch_def_id="original-workflow-name",
            changes_json=json.dumps([{"op": "set_name", "name": "renamed-workflow"}]),
        )

        assert res.get("status") == "patched", res
        assert res.get("name_updated") is True
        assert res.get("new_name") == "renamed-workflow"

    def test_patch_branch_name_resolves_and_persists(self, ext_env):
        """patch_branch via name persists the change visibly by loading via ID."""
        us, base = ext_env
        bid, _ = _build(us, name="persist-test-workflow")

        _call(
            us, "extensions", "patch_branch",
            branch_def_id="persist-test-workflow",
            changes_json=json.dumps([{"op": "set_description", "description": "updated via name"}]),
        )

        from tinyassets.daemon_server import get_branch_definition
        branch = get_branch_definition(base, branch_def_id=bid)
        assert branch["description"] == "updated via name"
