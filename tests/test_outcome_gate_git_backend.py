"""Outcome gates git-commit path and YAML emitters.

Covers docs/specs/outcome_gates_phase6.md rollout details:
- `save_gate_claim_and_commit` + `retract_gate_claim_and_commit`
  composites on `SqliteOnlyBackend` and `SqliteCachedBackend`.
- YAML round-trip for `gate_ladder` (goals) and gate claims
  (gates/<goal_slug>/<branch_slug>__<rung>.yaml).
- Force + local_edit_conflict pattern on `define_ladder`, `claim`,
  `retract`. Dirty files surface the `_format_dirty_file_conflict`
  envelope via the gates dispatch wrapper.
- Commit message templates: `goals.define_ladder:` / `gates.claim:` /
  `gates.retract:`.

SqliteOnlyBackend tests skip the dirty-check / git-commit assertions
(no git seam). SqliteCachedBackend tests use a real temp git repo via
`git_bridge`-friendly tmp dirs.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


_LADDER = [
    {"rung_key": "draft_complete", "name": "Draft complete",
     "description": "Full draft emitted."},
    {"rung_key": "peer_reviewed", "name": "Peer reviewed",
     "description": "At least 2 reviewers."},
]


# ───────────────────────────────────────────────────────────────────────
# YAML emitters (serializer round-trip)
# ───────────────────────────────────────────────────────────────────────


def test_goal_yaml_roundtrip_with_ladder():
    from tinyassets.catalog.serializer import (
        goal_from_yaml_payload,
        goal_to_yaml_payload,
    )

    original = {
        "goal_id": "g1",
        "name": "Research paper",
        "description": "x",
        "author": "alice",
        "tags": ["ai"],
        "visibility": "public",
        "created_at": 1.0,
        "updated_at": 2.0,
        "gate_ladder": _LADDER,
    }
    payload = goal_to_yaml_payload(original)
    assert payload["gate_ladder"] == _LADDER
    restored = goal_from_yaml_payload(payload)
    assert restored["gate_ladder"] == _LADDER


def test_goal_yaml_omits_empty_ladder():
    from tinyassets.catalog.serializer import goal_to_yaml_payload

    payload = goal_to_yaml_payload({
        "goal_id": "g1", "name": "G", "gate_ladder": [],
    })
    assert "gate_ladder" not in payload


def test_gate_claim_yaml_roundtrip(tmp_path):
    import sqlite3

    from tinyassets.catalog.serializer import (
        gate_claim_from_yaml_payload,
        gate_claim_to_yaml_payload,
    )
    from tinyassets.daemon_server import initialize_author_server

    original = {
        "claim_id": "abc123",
        "branch_def_id": "b1",
        "goal_id": "g1",
        "rung_key": "draft_complete",
        "evidence_url": "https://example.com/x",
        "evidence_note": "first",
        "conformance_pack_id": "publication-readiness",
        "claimed_by": "alice",
        "claimed_at": "2026-05-01T14:22:03Z",
        "retracted_at": None,
        "retracted_reason": "",
    }
    # The fixture is a real gate_claims row: it once predated a column
    # (conformance_pack_id) and failed for a shape the schema cannot produce.
    with sqlite3.connect(initialize_author_server(tmp_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(gate_claims)")}
    assert set(original) == columns
    payload = gate_claim_to_yaml_payload(original)
    restored = gate_claim_from_yaml_payload(payload)
    assert restored == original


def test_gate_claim_yaml_roundtrip_retracted():
    from tinyassets.catalog.serializer import (
        gate_claim_from_yaml_payload,
        gate_claim_to_yaml_payload,
    )

    original = {
        "claim_id": "abc123", "branch_def_id": "b1", "goal_id": "g1",
        "rung_key": "draft_complete",
        "evidence_url": "https://example.com/x", "evidence_note": "",
        "claimed_by": "alice",
        "claimed_at": "2026-05-01T14:22:03Z",
        "retracted_at": "2026-05-02T09:00:00Z",
        "retracted_reason": "evidence 404",
    }
    payload = gate_claim_to_yaml_payload(original)
    assert payload["retracted_at"] == "2026-05-02T09:00:00Z"
    restored = gate_claim_from_yaml_payload(payload)
    assert restored["retracted_at"] == "2026-05-02T09:00:00Z"


# ───────────────────────────────────────────────────────────────────────
# Layout paths
# ───────────────────────────────────────────────────────────────────────


def test_gate_claim_path_shape(tmp_path):
    from tinyassets.catalog.layout import YamlRepoLayout

    layout = YamlRepoLayout(tmp_path)
    path = layout.gate_claim_path("fantasy-novel", "loral-v3", "draft_complete")
    assert path == (
        tmp_path.resolve() / "gates" / "fantasy-novel"
        / "loral-v3__draft_complete.yaml"
    )


# ───────────────────────────────────────────────────────────────────────
# SqliteOnly backend (no git seam)
# ───────────────────────────────────────────────────────────────────────


def test_sqlite_only_save_gate_claim_returns_none_commit(tmp_path):
    """SqliteOnlyBackend.save_gate_claim_and_commit delegates to
    daemon_server.claim_gate and returns ``(saved, None)``.
    """
    from tinyassets.branches import BranchDefinition
    from tinyassets.catalog.backend import SqliteOnlyBackend
    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
        save_goal,
        update_branch_definition,
    )

    base = tmp_path / "out"
    base.mkdir()
    initialize_author_server(base)
    goal = save_goal(base, goal={"name": "G", "author": "alice"})
    branch = save_branch_definition(
        base, branch_def=BranchDefinition(name="B", author="alice").to_dict(),
    )
    update_branch_definition(
        base, branch_def_id=branch["branch_def_id"],
        updates={"goal_id": goal["goal_id"]},
    )
    from tinyassets.daemon_server import set_goal_ladder
    set_goal_ladder(base, goal_id=goal["goal_id"], ladder=_LADDER)

    backend = SqliteOnlyBackend(base)
    saved, commit = backend.save_gate_claim_and_commit(
        branch_def_id=branch["branch_def_id"],
        goal_id=goal["goal_id"],
        rung_key="draft_complete",
        evidence_url="https://example.com/x",
        evidence_note="",
        claimed_by="alice",
        goal_slug="g", branch_slug="b",
        author="alice <a@x>", message="gates.claim: g/b@draft_complete",
    )
    assert commit is None
    assert saved["rung_key"] == "draft_complete"
    assert saved["branch_def_id"] == branch["branch_def_id"]


# ───────────────────────────────────────────────────────────────────────
# SqliteCached backend: reads do not commit
# ───────────────────────────────────────────────────────────────────────


def _init_git_repo(repo: Path) -> None:
    """Initialize a bare git repo at ``repo`` with one empty commit
    so `git_bridge` treats it as enabled.
    """
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("seed\n")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repo, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        capture_output=True,
    )


def _become(user_id: str) -> None:
    """Sign in as ``user_id``.

    These tests used to set ``UNIVERSE_SERVER_USER``, which named the actor by
    environment variable -- authority from a string anybody can set. The
    autouse operator fixture rebinds between tests, so this does not leak.
    """
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity

    _mw._current_identity.set(
        Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=[
                "tinyassets.universe.read",
                "tinyassets.universe.write",
                "tinyassets.universe.admin",
                "tinyassets.extensions.read",
                "tinyassets.extensions.write",
            ],
        )
    )


@pytest.fixture
def cached_gates_env(tmp_path, monkeypatch, authenticate_request):
    """A temp git repo with GATES_ENABLED + sqlite_cached backend."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    base = repo / "output"
    base.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _become("alice")
    monkeypatch.setenv("GATES_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_cached")
    # Branch mutation requires a credential-derived subject. Without one the
    # extensions surface returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die before reaching their own concern. The conftest default grants
    # extensions read/write/admin only, and the scope check is per-family.
    # This file drives `gates` and `goals`: `claim` is `gates.costly`,
    # `define_ladder` / `retract` are `gates.admin`. Nothing here asserts a
    # scope refusal, so granting them costs no assertion strength.
    authenticate_request("alice", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        "tinyassets.gates.read",
        "tinyassets.gates.write",
        "tinyassets.gates.costly",
        "tinyassets.gates.admin",
        "tinyassets.goals.read",
        "tinyassets.goals.write",
    ])
    from tinyassets.catalog import backend as backend_mod
    backend_mod.invalidate_backend_cache()
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, base, repo, monkeypatch
    backend_mod.invalidate_backend_cache()
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    if tool == "gates":
        from tinyassets.api.market import gates as fn
    else:
        fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _seed(us, base):
    """A Goal with ``_LADDER`` and one bound branch. ``build_branch`` binds
    via ``goal_id``; the ladder goes through storage (``define_ladder`` is
    gone)."""
    from tinyassets.daemon_server import set_goal_ladder

    g = _call(us, "goals", "propose", name="Research paper", description="x")
    gid = g["goal"]["goal_id"]
    b = _call(us, "extensions", "build_branch", spec_json=json.dumps({
        "name": "LoRA v3",
        "goal_id": gid,
        "entry_point": "draft",
        "node_defs": [{"node_id": "draft", "display_name": "Draft",
                       "prompt_template": "draft: {topic}"}],
        "edges": [{"from": "START", "to": "draft"},
                  {"from": "draft", "to": "END"}],
        "state_schema": [{"name": "topic", "type": "str"}],
    }))
    assert b["status"] == "built", b
    set_goal_ladder(base, goal_id=gid, ladder=_LADDER)
    return gid, b["branch_def_id"]


def _last_commit_message(repo: Path) -> str:
    out = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    return out.stdout.strip()



def test_get_ladder_no_commit(cached_gates_env):
    """Read-only action MUST NOT emit a commit."""
    us, base, repo, _ = cached_gates_env
    gid, _bid = _seed(us, base)
    msg_before = _last_commit_message(repo)
    _call(us, "gates", "get_ladder", goal_id=gid)
    msg_after = _last_commit_message(repo)
    assert msg_before == msg_after


def test_leaderboard_no_commit(cached_gates_env):
    from tinyassets.daemon_server import claim_gate

    us, base, repo, _ = cached_gates_env
    gid, bid = _seed(us, base)
    claim_gate(base, branch_def_id=bid, goal_id=gid, rung_key="draft_complete",
               evidence_url="https://example.com/x", claimed_by="alice")
    msg_before = _last_commit_message(repo)
    _call(us, "gates", "leaderboard", goal_id=gid)
    assert _last_commit_message(repo) == msg_before
