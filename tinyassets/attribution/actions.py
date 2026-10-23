"""Owner-local remix provenance."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path


def _attribution_connect(base_path: "Path") -> Any:
    import sqlite3 as _sqlite3

    from tinyassets.attribution.schema import migrate_attribution_schema

    db = base_path / ".runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    migrate_attribution_schema(conn)
    conn.commit()
    return conn


_VALID_CONTRIBUTION_KINDS = frozenset({"original", "remix", "patch", "template"})


def _action_record_remix(kwargs: dict[str, Any]) -> str:
    import uuid as _uuid
    from datetime import datetime, timezone

    parent_id = (kwargs.get("parent_branch_def_id") or "").strip()
    child_id = (kwargs.get("child_branch_def_id") or "").strip()
    if not parent_id:
        return json.dumps({"error": "parent_branch_def_id is required."})
    if not child_id:
        return json.dumps({"error": "child_branch_def_id is required."})
    if parent_id == child_id:
        return json.dumps({"error": "parent_branch_def_id and child_branch_def_id must differ."})

    contribution_kind = (kwargs.get("contribution_kind") or "remix").strip()
    if contribution_kind not in _VALID_CONTRIBUTION_KINDS:
        return json.dumps({
            "error": f"Unknown contribution_kind '{contribution_kind}'.",
            "valid": sorted(_VALID_CONTRIBUTION_KINDS),
        })

    try:
        credit_share = float(kwargs.get("credit_share") or 0.0)
    except (TypeError, ValueError):
        credit_share = 0.0
    credit_share = max(0.0, min(1.0, credit_share))

    from tinyassets.principals import named_principal

    actor_id = named_principal(kwargs.get("actor_id"))
    owner_user_id = (kwargs.get("owner_user_id") or "").strip()
    daemon_id = (kwargs.get("daemon_id") or "").strip()
    runtime_instance_id = (kwargs.get("runtime_instance_id") or "").strip()
    worker_id = (kwargs.get("worker_id") or "").strip()
    base = _base_path()

    with _attribution_connect(base) as conn:
        # Cycle guard: reject if child already appears as an ancestor of parent.
        # Walk attribution_edge parents of parent_id up to 50 hops.
        ancestors: set[str] = set()
        frontier = [parent_id]
        for _ in range(50):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT parent_id FROM attribution_edge WHERE child_id IN ({placeholders})",
                frontier,
            ).fetchall()
            frontier = []
            for r in rows:
                pid = r["parent_id"]
                if pid not in ancestors:
                    ancestors.add(pid)
                    frontier.append(pid)
        if child_id in ancestors or child_id == parent_id:
            return json.dumps({
                "error": "Cycle detected: child is already an ancestor of parent."
            })

        # Compute generation_depth = max depth of parent + 1 (minimum 1).
        row = conn.execute(
            "SELECT MAX(generation_depth) AS d FROM attribution_edge WHERE child_id = ?",
            (parent_id,),
        ).fetchone()
        parent_depth = int(row["d"] or 0) if row and row["d"] is not None else 0
        generation_depth = parent_depth + 1

        edge_id = str(_uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """
                INSERT INTO attribution_edge
                    (edge_id, parent_id, child_id, parent_kind, child_kind,
                     generation_depth, contribution_kind, created_at)
                VALUES (?, ?, ?, 'branch', 'branch', ?, ?, ?)
                """,
                (edge_id, parent_id, child_id, generation_depth, contribution_kind, created_at),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return json.dumps({"error": f"Edge {parent_id} → {child_id} already exists."})
            raise

        # Record credit for the remixing actor on the child artifact.
        if credit_share > 0.0:
            credit_id = str(_uuid.uuid4())
            conn.execute(
                """
                INSERT OR IGNORE INTO attribution_credit
                    (credit_id, artifact_id, artifact_kind, actor_id,
                     owner_user_id, daemon_id, runtime_instance_id, worker_id,
                     credit_share, royalty_share, generation_depth,
                     contribution_kind, recorded_at)
                VALUES (?, ?, 'branch', ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?)
                """,
                (
                    credit_id, child_id, actor_id, owner_user_id, daemon_id,
                    runtime_instance_id, worker_id, credit_share,
                    generation_depth, contribution_kind, created_at,
                ),
            )

    return json.dumps({
        "status": "recorded",
        "edge_id": edge_id,
        "parent_branch_def_id": parent_id,
        "child_branch_def_id": child_id,
        "contribution_kind": contribution_kind,
        "generation_depth": generation_depth,
        "credit_share": credit_share,
        "actor_id": actor_id,
        "created_at": created_at,
    })


def _action_get_provenance(kwargs: dict[str, Any]) -> str:
    child_id = (kwargs.get("child_branch_def_id") or "").strip()
    if not child_id:
        return json.dumps({"error": "child_branch_def_id is required."})
    try:
        max_depth = max(1, min(50, int(kwargs.get("max_depth") or 10)))
    except (TypeError, ValueError):
        max_depth = 10

    base = _base_path()
    chain: list[dict[str, Any]] = []

    with _attribution_connect(base) as conn:
        frontier = [child_id]
        visited: set[str] = {child_id}
        depth = 0
        while frontier and depth < max_depth:
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"""
                SELECT e.parent_id, e.child_id, e.generation_depth,
                       e.contribution_kind, e.edge_id, e.created_at,
                       c.actor_id, c.credit_share
                FROM attribution_edge e
                LEFT JOIN attribution_credit c
                    ON c.artifact_id = e.child_id AND c.artifact_kind = 'branch'
                WHERE e.child_id IN ({placeholders})
                ORDER BY e.generation_depth ASC
                """,
                frontier,
            ).fetchall()
            next_frontier: list[str] = []
            for r in rows:
                chain.append({
                    "edge_id": r["edge_id"],
                    "parent_branch_def_id": r["parent_id"],
                    "child_branch_def_id": r["child_id"],
                    "generation_depth": r["generation_depth"],
                    "contribution_kind": r["contribution_kind"],
                    "actor_id": r["actor_id"],
                    "credit_share": r["credit_share"],
                    "created_at": r["created_at"],
                })
                pid = r["parent_id"]
                if pid not in visited:
                    visited.add(pid)
                    next_frontier.append(pid)
            frontier = next_frontier
            depth += 1

    return json.dumps({
        "child_branch_def_id": child_id,
        "chain": chain,
        "count": len(chain),
    })


_ATTRIBUTION_ACTIONS: dict[str, Any] = {
    "record_remix": _action_record_remix,
    "get_provenance": _action_get_provenance,
}
