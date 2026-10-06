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
# SqliteCached backend: commits + YAML + force
# ───────────────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────────────
# Integration: define_ladder → claim → retract → re-claim flow
# ───────────────────────────────────────────────────────────────────────
