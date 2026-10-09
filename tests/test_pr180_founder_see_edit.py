"""PR-180: a founder can SEE and EDIT their own graph via the live /mcp
connector — within the canonical seven-handle invariant (no new handles; the
capability is added as new *targets* on read_graph / write_graph).

SEE half:  read_graph(target="run", run_id=...) -> get_run snapshot
           (terminal status + result + structured failure reason).
EDIT half: write_graph(target="branch", branch_id=..., changes_json=...) ->
           the existing transactional, author-gated patch_branch handler.

Precedent: write_graph already carries a non-first-class target (persona),
so adding targets preserves the exact canonical handle set and the
``mcp_public_canary --assert-handles`` guard stays green.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import pytest

CANONICAL_HANDLES = {
    "read_graph",
    "write_graph",
    "run_graph",
    "read_page",
    "write_page",
    "converse",
    "get_status",
}

_BASIC_SPEC = {
    "name": "Founder branch",
    "entry_point": "ready",
    "node_defs": [{
        "node_id": "ready",
        "display_name": "Ready",
        "prompt_template": "Do the work.",
    }],
    "edges": [
        {"from": "START", "to": "ready"},
        {"from": "ready", "to": "END"},
    ],
    "state_schema": [{"name": "x", "type": "str"}],
}


@pytest.fixture
def server_env(tmp_path, monkeypatch, authenticate_request):
    """Live /mcp surface (tinyassets.universe_server) on an isolated data dir."""
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


# ── PR-178 invariant: the new targets add NO new handles ────────────────────

def test_new_targets_preserve_canonical_handle_set(server_env):
    """PR-180 targets must not change the canonical seven-handle catalog."""
    us = server_env
    advertised = {t.name for t in asyncio.run(us.mcp.list_tools(run_middleware=True))}
    assert advertised == CANONICAL_HANDLES


# ── SEE half: read_graph target=run ─────────────────────────────────────────

def test_read_graph_run_routes_to_get_run(server_env):
    """target=run reaches the get_run handler (not unknown_target)."""
    us = server_env
    payload = json.loads(us.read_graph(target="run", run_id="no-such-run"))
    assert payload.get("error") != "unknown_target"
    # get_run's own not-found error proves the route reached the handler.
    assert "not found" in payload.get("error", "").lower()


def test_read_graph_run_listed_in_allowed_targets(server_env):
    us = server_env
    payload = json.loads(us.read_graph(target="bogus"))
    assert payload["error"] == "unknown_target"
    assert "run" in payload["allowed_targets"]
    assert "runs" in payload["allowed_targets"]


# ── EDIT half: write_graph target=branch ────────────────────────────────────

def test_write_graph_branch_routes_to_patch_branch(server_env):
    """target=branch reaches the patch_branch handler (not unknown_target)."""
    us = server_env
    payload = json.loads(us.write_graph(target="branch", branch_id="", changes_json=""))
    assert payload.get("error") != "unknown_target"
    # patch_branch's own field validation proves the route reached the handler
    # (it rejects on a missing required field rather than unknown_target).
    assert "required" in payload.get("error", "")


def test_write_graph_branch_listed_in_allowed_targets(server_env):
    us = server_env
    payload = json.loads(us.write_graph(target="bogus"))
    assert payload["error"] == "unknown_target"
    assert "branch" in payload["allowed_targets"]


def test_founder_edits_own_branch_round_trip(server_env):
    """End-to-end: build a branch, then EDIT it via the connector handle as
    the founder (author == UNIVERSE_SERVER_USER), and confirm the edit lands.
    """
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]

    patched = json.loads(us.write_graph(
        target="branch",
        branch_id=bid,
        changes_json=json.dumps([{"op": "set_published", "published": True}]),
    ))
    assert "error" not in patched, patched
    assert patched.get("status") != "rejected", patched

    # The edit persisted on the founder's own branch.
    listing = json.loads(us._extensions_impl(action="list_branches", scope="all"))
    summary = next(b for b in listing["branches"] if b["branch_def_id"] == bid)
    assert summary["published"] is True


def test_patch_branch_is_author_gated_via_connector(server_env, monkeypatch, authenticate_request):
    """A non-author cannot edit someone else's branch through the handle
    (inherits patch_branch's BUG-081 author gate). force=true is required to
    bypass, so the default connector path stays safe."""
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_BASIC_SPEC),
    ))
    bid = built["branch_def_id"]

    # Switch identity to a different user. The env var does NOT do this:
    # the author gate reads `_request_branch_actor()`, which is
    # credential-derived, so setting it alone left the caller as the
    # author and this gate assertion was never exercised.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "intruder")
    importlib.reload(us)
    authenticate_request("intruder")

    blocked = json.loads(us.write_graph(
        target="branch",
        branch_id=bid,
        changes_json=json.dumps([{"op": "set_published", "published": True}]),
    ))
    # Author gate fires: either an error or a rejected status, never a clean apply.
    assert blocked.get("status") == "rejected" or "error" in blocked, blocked
