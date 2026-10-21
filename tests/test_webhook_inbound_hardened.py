"""End-to-end adversarial tests for the hardened inbound webhook path (Codex round 2).

These use the REAL surfaces — real authenticated principals, real authored branches, real
universes, the real ``run_graph`` op dispatch, and the real enqueue path writing to the real
runs DB — NOT mocked ownership or spied enqueue. Each test reproduces a specific finding:

  #1 author-gate bypass       — a caller cannot mint a hook for a branch they did not author
  #2 dark flag is a boundary  — flag off => route absent AND no run is enqueued
  #3 revocation               — a revoked token triggers no run
  #4 replay                   — same/altered delivery fires once; a genuinely new body fires again
  #5 no execution refusal    — a busy universe is NEVER dropped; every delivery enqueues and
                              queues for an agent seat (founder 2026-09-30)
  #6 header/credential leak    — only allowlisted headers reach durable run input; no token stored
  #7 uniform response          — every non-deliverable state answers 404 (no 500 usability leak)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import tinyassets.webhook_inbound as wh
from tinyassets.storage import webhook_hooks

# ── Real fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path: Path, monkeypatch, authenticate_request):
    """A real data dir with inbound ENABLED, the author server initialized, and the
    credential-subject auth provider wired (via the shared ``authenticate_request``)."""
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.runs import initialize_runs_db

    initialize_author_server(base)
    initialize_runs_db(base)          # scheduler tables, as the daemon does at boot
    webhook_hooks._initialized.clear()
    return base, authenticate_request


#: OAuth scopes a founder needs to create a universe and drive the inbound ops.
_FOUNDER_CAPS = [
    "tinyassets.universe.costly",
    "tinyassets.extensions.read",
    "tinyassets.extensions.write",
    "tinyassets.extensions.admin",
    "tinyassets.extensions.costly",
]


def _create_universe(sub: str, authenticate) -> str:
    """Authenticate as ``sub`` and create a REAL universe they own (admin ACL)."""
    from tinyassets.api import universe as universe_api

    authenticate(sub, _FOUNDER_CAPS)
    out = json.loads(universe_api._universe_impl(action="create_universe"))
    assert out.get("error") is None, out
    return out["universe_id"]


def _seed_branch(base: Path, *, bid: str, author: str) -> None:
    """Persist a REAL, structurally-valid, runnable branch authored by ``author``."""
    from tinyassets.daemon_server import save_branch_definition

    src = "def run(state):\n    return {'out': 'ok'}\n"
    save_branch_definition(base, branch_def={
        "branch_def_id": bid,
        "name": bid,
        "author": author,
        "domain_id": "workflow",
        "visibility": "public",
        "node_defs": [{
            "node_id": "only",
            "display_name": "Only",
            "phase": "custom",
            "input_keys": [],
            "output_keys": ["out"],
            "source_code": src,
            "approved": True,
            "approved_source_hash": hashlib.sha256(src.encode()).hexdigest(),
            "tools_allowed": [],
        }],
        "graph_nodes": [{"id": "only", "node_def_id": "only", "position": 0}],
        "edges": [
            {"from_node": "START", "to_node": "only"},
            {"from_node": "only", "to_node": "END"},
        ],
        "conditional_edges": [],
        "state_schema": [{"name": "out", "type": "str"}],
        "entry_point": "only",
    })


def _runs_for_universe(base: Path, uid: str) -> list[dict]:
    """Every run row (any status) enqueued for ``uid`` — read straight from the runs DB."""
    from tinyassets.runs import initialize_runs_db

    initialize_runs_db(base)
    conn = sqlite3.connect(base / ".runs.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id, actor, inputs_json, queue_universe_id FROM runs "
            "WHERE queue_universe_id = ?",
            (uid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()




def _mint(uid: str, bid: str) -> dict:
    """Mint through the REAL run_graph op dispatch (author-gate + owner-scope both fire)."""
    from tinyassets.universe_server import run_graph

    return json.loads(run_graph(webhook_op="mint", branch_def_id=bid, graph_id=uid))




# ── #1 Author-gate bypass ────────────────────────────────────────────────────────





# ── Source ops end-to-end (owner-scoped, run_graph-reachable) ────────────────────











# ── #2 Dark flag is a real boundary ──────────────────────────────────────────────







# ── #3 Revocation ────────────────────────────────────────────────────────────────



# ── #4 Replay (server-side key; caller header cannot influence it) ────────────────





# ── #5 No execution refusal: a busy universe queues ─────────────────────────────







# ── #6 Header / credential persistence ───────────────────────────────────────────

def test_credential_headers_never_reach_durable_run_input(env):
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")
    token = _mint(uid, "b")["token"]

    status, _ = wh.handle_hook(
        token=token, body=b"{}", base_path=base,
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "s=1",
            "CF-Access-Client-Secret": "cf-secret",
            "CF-Access-Jwt-Assertion": "jwt",
            "X-Api-Key": "key-123",
            "X-GitHub-Event": "push",          # allowlisted -> forwarded
        },
    )
    assert status == 202
    runs = _runs_for_universe(base, uid)
    assert len(runs) == 1
    fwd = json.loads(runs[0]["inputs_json"])["webhook"]["headers"]
    assert fwd == {"X-GitHub-Event": "push"}   # ONLY the allowlisted header survived
    for leaked in ("Authorization", "Cookie", "CF-Access-Client-Secret",
                   "CF-Access-Jwt-Assertion", "X-Api-Key"):
        assert leaked not in fwd
    # And the raw token is nowhere in the stored run input.
    assert token not in runs[0]["inputs_json"]


# ── #7 Uniform non-deliverable response (no 500 usability leak) ───────────────────
