"""read_graph target=branch — the SEE-for-branches primitive.

Completes the read/edit symmetry started by PR-180: PR-180 added
read_graph target=run (SEE runs) + write_graph target=branch (EDIT branches);
this adds read_graph target=branch (SEE branches) so a founder can inspect a
branch's full node configs (timeout_seconds, model_hint, prompt_template,
edges, state schema) before editing — informed edits, not blind ones.

Within the canonical seven-handle invariant: a new *target*, no new handle.
"""
from __future__ import annotations

import importlib
import json

import pytest

_BASIC_SPEC = {
    "name": "Inspectable branch",
    "entry_point": "ready",
    "node_defs": [{
        "node_id": "ready",
        "display_name": "Ready",
        "prompt_template": "Do the work.",
        "timeout_seconds": 450.0,
    }],
    "edges": [
        {"from": "START", "to": "ready"},
        {"from": "ready", "to": "END"},
    ],
    "state_schema": [{"name": "x", "type": "str"}],
}


@pytest.fixture
def server_env(tmp_path, monkeypatch, authenticate_request):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "founder")
    # `UNIVERSE_SERVER_USER` alone does not reach branch WRITE actions:
    # they resolve the caller via `_request_branch_actor()`, which is
    # credential-derived and documented "never an env actor". Without a
    # bound request subject they return "Authenticated branch subject
    # required." before parsing anything, and every later assertion dies on
    # a KeyError that hides the real cause.
    authenticate_request("founder")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us
    importlib.reload(us)


def test_read_graph_branch_routes_to_get_branch(server_env):
    """target=branch reaches the get_branch handler (not unknown_target)."""
    us = server_env
    payload = json.loads(us.read_graph(target="branch", branch_id="no-such-branch"))
    assert payload.get("error") != "unknown_target"
    assert "not found" in payload.get("error", "").lower()


def test_read_graph_branch_in_allowed_targets(server_env):
    us = server_env
    payload = json.loads(us.read_graph(target="bogus"))
    assert payload["error"] == "unknown_target"
    assert "branch" in payload["allowed_targets"]


def test_read_graph_branch_returns_node_configs(server_env):
    """The whole point: reading a branch surfaces editable node configs."""
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]

    branch = json.loads(us.read_graph(target="branch", branch_id=bid))
    assert "error" not in branch, branch
    node_ids = {n["node_id"] for n in branch.get("node_defs", [])}
    assert "ready" in node_ids
    ready = next(n for n in branch["node_defs"] if n["node_id"] == "ready")
    # The config a founder needs to make an informed timeout edit is visible.
    assert "timeout_seconds" in ready


def test_read_graph_branch_falls_back_to_graph_id(server_env):
    """branch_id omitted -> graph_id is used (lenient, matches target=run)."""
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]
    branch = json.loads(us.read_graph(target="branch", graph_id=bid))
    assert branch.get("branch_def_id") == bid


# ── Visibility model (commons-first) — Codex review of #1404 required proof ──
# Public BranchDefinitions are a global remix commons (readable cross-user);
# private branches are author-gated with a not-found envelope. These two tests
# pin that boundary so the cross-universe read is a deliberate decision, not a
# silent leak.

def test_public_branch_is_commons_readable_cross_user(server_env, monkeypatch):
    """A PUBLIC branch is readable by a different user (commons / remix model)."""
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "stranger")
    importlib.reload(us)
    branch = json.loads(us.read_graph(target="branch", branch_id=bid))
    assert "error" not in branch, branch
    assert branch.get("branch_def_id") == bid


def test_private_branch_hidden_from_non_author(server_env, monkeypatch, authenticate_request):
    """A PRIVATE branch is author-gated: the author reads it, a stranger gets
    the same not-found envelope (existence is not leaked)."""
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]
    # Make it private via the patch_branch primitive (set_visibility op).
    patched = json.loads(us.write_graph(
        target="branch",
        branch_id=bid,
        changes_json=json.dumps([{"op": "set_visibility", "visibility": "private"}]),
    ))
    assert "error" not in patched, patched

    # Author can read their own private branch.
    own = json.loads(us.read_graph(target="branch", branch_id=bid))
    assert own.get("branch_def_id") == bid, own

    # A different user cannot — and cannot even confirm it exists.
    # The identity switch MUST rebind the request subject:
    # `UNIVERSE_SERVER_USER` does not reach the reader, which resolves the
    # caller from the credential. Setting the env var alone left this test
    # still reading as the author, so the non-disclosure assertion below
    # was passing on the author's own view rather than a stranger's.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "stranger")
    importlib.reload(us)
    authenticate_request("stranger")
    blocked = json.loads(us.read_graph(target="branch", branch_id=bid))
    assert "not found" in blocked.get("error", "").lower(), blocked
