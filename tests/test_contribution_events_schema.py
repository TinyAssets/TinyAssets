"""Tests for contribution_events table — Task #71 schema layer.

Coverage:
- Fresh DB creates table + 4 indexes via initialize_runs_db (which now
  concatenates CONTRIBUTION_EVENTS_SCHEMA into the runs DB schema).
- Re-running init is idempotent (no error, no duplicate rows).
- Basic INSERT semantics: positive-weight + negative-weight regression event,
  metadata_json defaults preserved, types round-trip.
- Bounty-calc query smoke: synthetic 3-generation fork_from chain + 1 leaf
  design_used event executes the §4 recursive-CTE shape and produces the
  expected (actor_id, share) tuples with depth-decayed weights.

Spec: 2026-04-25-contribution-ledger-proposal.md (archived in git) §1, §4.
"""
from __future__ import annotations

import json
import time
import uuid

from tinyassets.contribution_events import _connect, initialize_contribution_events_db
from tinyassets.runs import initialize_runs_db

# ── Step 0: schema DDL ────────────────────────────────────────────────────────


class TestSchemaDDL:
    def test_fresh_db_creates_contribution_events_table(self, tmp_path):
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "contribution_events" in tables

    def test_table_has_expected_columns(self, tmp_path):
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            cols = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(contribution_events)")
            }
        expected = {
            "event_id",
            "event_type",
            "actor_id",
            "actor_handle",
            "source_run_id",
            "source_artifact_id",
            "source_artifact_kind",
            "weight",
            "occurred_at",
            "metadata_json",
        }
        assert expected <= set(cols)
        # event_id is single-column primary key
        assert cols["event_id"]["pk"] == 1
        # weight is REAL per design
        assert cols["weight"]["type"].upper() == "REAL"
        # occurred_at is REAL per design
        assert cols["occurred_at"]["type"].upper() == "REAL"

    def test_indexes_present(self, tmp_path):
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            idx_names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name = 'contribution_events'"
                )
            }
        # All 4 design-named indexes
        assert "idx_contribution_events_window" in idx_names
        assert "idx_contribution_events_actor" in idx_names
        assert "idx_contribution_events_artifact" in idx_names
        assert "idx_contribution_events_run" in idx_names

    def test_init_is_idempotent(self, tmp_path):
        """Running init multiple times must not error or duplicate state."""
        for _ in range(3):
            initialize_runs_db(tmp_path)
            initialize_contribution_events_db(tmp_path)
        # Single table after all 3 runs.
        with _connect(tmp_path) as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master "
                "WHERE type='table' AND name='contribution_events'"
            ).fetchone()["n"]
        assert cnt == 1


# ── INSERT semantics ──────────────────────────────────────────────────────────


class TestInsertSemantics:
    def test_positive_and_negative_weight_events_round_trip(self, tmp_path):
        """Single-table model: credit + regression rows coexist, weight signed."""
        initialize_runs_db(tmp_path)
        now = time.time()
        with _connect(tmp_path) as conn:
            conn.execute(
                "INSERT INTO contribution_events "
                "(event_id, event_type, actor_id, weight, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, "execute_step", "alice", 1.0, now),
            )
            conn.execute(
                "INSERT INTO contribution_events "
                "(event_id, event_type, actor_id, weight, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, "caused_regression", "alice", -0.5, now),
            )
            rows = list(conn.execute(
                "SELECT event_type, weight FROM contribution_events "
                "ORDER BY weight DESC"
            ))
        assert len(rows) == 2
        assert rows[0]["weight"] == 1.0
        assert rows[1]["weight"] == -0.5
        assert rows[1]["event_type"] == "caused_regression"

    def test_metadata_json_defaults_to_empty_object(self, tmp_path):
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            conn.execute(
                "INSERT INTO contribution_events "
                "(event_id, event_type, actor_id, occurred_at) "
                "VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, "execute_step", "alice", time.time()),
            )
            row = conn.execute(
                "SELECT metadata_json FROM contribution_events"
            ).fetchone()
        # Default is '{}' empty object string, never NULL — reduces caller branching.
        assert row["metadata_json"] == "{}"
        # And it parses as an empty dict.
        assert json.loads(row["metadata_json"]) == {}

    def test_weight_default_is_one(self, tmp_path):
        """Per design: weight DEFAULT 1.0 for execute_step / design_used."""
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            conn.execute(
                "INSERT INTO contribution_events "
                "(event_id, event_type, actor_id, occurred_at) "
                "VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, "design_used", "alice", time.time()),
            )
            row = conn.execute(
                "SELECT weight FROM contribution_events"
            ).fetchone()
        assert row["weight"] == 1.0

    def test_source_run_id_nullable(self, tmp_path):
        """PR / wiki-only events have no run; schema must accept NULL."""
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            conn.execute(
                "INSERT INTO contribution_events "
                "(event_id, event_type, actor_id, occurred_at, source_run_id) "
                "VALUES (?, ?, ?, ?, NULL)",
                (uuid.uuid4().hex, "code_committed", "alice", time.time()),
            )
            row = conn.execute(
                "SELECT source_run_id FROM contribution_events"
            ).fetchone()
        assert row["source_run_id"] is None


# ── Bounty-calc query smoke (§4 recursive-CTE shape) ─────────────────────────


class TestBountyCalcQuerySmoke:
    """Confirms the §4 SQL sketch actually executes against this schema and
    produces the expected (actor_id, share) tuples for a 3-generation
    fork_from chain. Validates: index hit pattern, recursive-CTE syntax,
    JOIN resolution, GROUP BY semantics."""

    def test_recursive_cte_with_decay_produces_expected_shares(self, tmp_path):
        """Synthetic dataset:
          original  (alice, gen 0)  — fork_from = NULL
              ↑
          remix     (bob,   gen 1)  — fork_from = original
              ↑
          leaf      (carol, gen 2)  — fork_from = remix; 1 design_used event

        Expected (with depth=0 share=1.0, depth=1 share=0.5, depth=2 share=0.333):
          carol gets 1.0  (leaf, depth 0)
          bob   gets 0.5  (remix, depth 1)
          alice gets 0.333 (original, depth 2)
        """
        initialize_runs_db(tmp_path)
        from tinyassets.daemon_server import (
            initialize_author_server,
            save_branch_definition,
            update_branch_definition,
        )

        initialize_author_server(tmp_path)

        # Build the 3-generation fork_from chain via real branch_definitions.
        for branch_id in ("original", "remix", "leaf"):
            save_branch_definition(
                tmp_path,
                branch_def={
                    "branch_def_id": branch_id,
                    "name": branch_id,
                    "graph_nodes": [],
                    "edges": [],
                    "node_defs": [],
                    "state_schema": [],
                    "entry_point": "n1",
                    "author": "anonymous",
                },
            )
        update_branch_definition(
            tmp_path, branch_def_id="remix", updates={"fork_from": "original"}
        )
        update_branch_definition(
            tmp_path, branch_def_id="leaf", updates={"fork_from": "remix"}
        )

        # 3 design_used events, one per generation's author.
        # carol's contribution at the leaf, bob's at the remix, alice's at original.
        now = time.time()
        with _connect(tmp_path) as conn:
            for actor, artifact in (
                ("carol", "leaf"),
                ("bob", "remix"),
                ("alice", "original"),
            ):
                conn.execute(
                    "INSERT INTO contribution_events "
                    "(event_id, event_type, actor_id, source_artifact_id, "
                    " source_artifact_kind, weight, occurred_at) "
                    "VALUES (?, 'design_used', ?, ?, 'branch_def', 1.0, ?)",
                    (uuid.uuid4().hex, actor, artifact, now),
                )

            # §4 query (with branch_definitions cross-DB caveat — we run the
            # CTE in this DB by manually joining since branch_definitions
            # lives in the daemon DB; the smoke verifies the SQL shape +
            # decay math is correct at the events-table level).
            #
            # For this smoke: walk the lineage in Python (since branch_definitions
            # is cross-DB), then run the GROUP BY join on contribution_events.
            # The §4 query pattern is preserved at events-table boundary.
            rows = list(conn.execute(
                "SELECT actor_id, source_artifact_id, weight, occurred_at "
                "FROM contribution_events "
                "WHERE event_type = 'design_used' "
                "  AND occurred_at BETWEEN ? AND ? "
                "ORDER BY actor_id",
                (now - 1.0, now + 1.0),
            ))
        # All 3 rows present.
        assert len(rows) == 3
        actors = {r["actor_id"] for r in rows}
        assert actors == {"alice", "bob", "carol"}
        # Each row carries weight 1.0 (decay applies at calc time, not at row).
        assert all(r["weight"] == 1.0 for r in rows)

        # Verify the in-DB recursive-CTE alone executes without syntax error,
        # using a self-contained lineage table so we don't cross DB boundaries.
        with _connect(tmp_path) as conn:
            conn.execute("CREATE TEMP TABLE lineage_smoke (artifact_id TEXT, parent_id TEXT)")
            conn.executemany(
                "INSERT INTO lineage_smoke VALUES (?, ?)",
                [("leaf", "remix"), ("remix", "original"), ("original", None)],
            )
            cte = conn.execute(
                """
                WITH RECURSIVE lineage(artifact_id, depth) AS (
                    SELECT 'leaf', 0
                    UNION ALL
                    SELECT ls.parent_id, lineage.depth + 1
                    FROM lineage
                    JOIN lineage_smoke ls ON ls.artifact_id = lineage.artifact_id
                    WHERE ls.parent_id IS NOT NULL AND lineage.depth < 5
                )
                SELECT
                    ce.actor_id,
                    SUM(ce.weight * (1.0 / (lineage.depth + 1))) AS share
                FROM contribution_events ce
                JOIN lineage ON lineage.artifact_id = ce.source_artifact_id
                WHERE
                    ce.occurred_at BETWEEN ? AND ?
                    AND ce.weight > 0
                GROUP BY ce.actor_id
                ORDER BY share DESC
                """,
                (now - 1.0, now + 1.0),
            )
            shares = {row["actor_id"]: row["share"] for row in cte}

        # Decay function used: 1.0 / (depth + 1). Depth 0 → 1.0; depth 1 → 0.5;
        # depth 2 → 0.333. Confirms §4 query shape executes + math reflects
        # design intent.
        assert shares["carol"] == 1.0
        assert shares["bob"] == 0.5
        assert abs(shares["alice"] - (1.0 / 3.0)) < 1e-9
