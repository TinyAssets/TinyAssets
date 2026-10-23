"""Owner-local workflow outcome evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path


def _outcome_db_path(base_path: "Path") -> "Path":
    return base_path / ".runs.db"


def _outcome_connect(base_path: "Path") -> Any:
    import sqlite3 as _sqlite3

    from tinyassets.outcomes.schema import migrate_outcome_schema
    db = _outcome_db_path(base_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    migrate_outcome_schema(conn)
    conn.commit()
    return conn


def _outcome_row_to_dict(
    row: Any,
    *,
    evidence: Any | None = None,
    transitions: list[Any] | None = None,
) -> dict:
    result = {
        "outcome_id": row["outcome_id"],
        "run_id": row["run_id"],
        "outcome_type": row["outcome_type"],
        "evidence_url": row["evidence_url"],
        "verified_at": row["verified_at"],
        "verified_by": row["verified_by"],
        "claim_run_id": row["claim_run_id"],
        "payload": json.loads(row["payload"] or "{}"),
        "recorded_at": row["recorded_at"],
        "note": row["note"] or "",
    }
    if evidence is not None:
        result.update({
            "account_id": evidence["account_id"],
            "branch_def_id": evidence["branch_def_id"],
            "branch_version_id": evidence["branch_version_id"],
            "content_hash": evidence["content_hash"],
            "output_field": evidence["output_field"],
            "output_sha256": evidence["output_sha256"],
            "handoff_id": evidence["handoff_id"],
            "effect_key": evidence["effect_key"],
            "sink": evidence["sink"],
            "evidence_source": evidence["evidence_source"],
            "evidence_level": evidence["evidence_level"],
            "external_id": evidence["external_id"],
            "normalized_external_ref": evidence["normalized_external_ref"],
            "attested_by": evidence["attested_by"],
            "evidence_updated_at": evidence["updated_at"],
            "evidence_transitions": [
                {
                    "seq": item["seq"],
                    "from_level": item["from_level"],
                    "to_level": item["to_level"],
                    "evidence_source": item["evidence_source"],
                    "actor_id": item["actor_id"],
                    "evidence": json.loads(item["evidence_json"] or "{}"),
                    "recorded_at": item["recorded_at"],
                }
                for item in (transitions or [])
            ],
        })
    return result


def _outcome_lifecycle(conn: Any, outcome_id: str) -> tuple[Any | None, list[Any]]:
    evidence = conn.execute(
        "SELECT * FROM outcome_evidence WHERE outcome_id = ?",
        (outcome_id,),
    ).fetchone()
    transitions = conn.execute(
        """
        SELECT *
          FROM outcome_evidence_transition
         WHERE outcome_id = ?
         ORDER BY seq
        """,
        (outcome_id,),
    ).fetchall()
    return evidence, transitions


def _outcome_run_owner(run: dict[str, Any]) -> str:
    owner = str(run.get("owner_user_id") or "").strip()
    if owner:
        return owner
    from tinyassets.principals import named_principal

    return named_principal(run.get("actor"))


def _action_record_outcome(kwargs: dict[str, Any]) -> str:
    import uuid as _uuid
    from datetime import datetime, timezone

    from tinyassets.outcomes.schema import (
        OUTCOME_TYPES,
        record_user_attested_outcome_evidence,
    )
    from tinyassets.principals import named_principal
    from tinyassets.runs import get_run

    run_id = (kwargs.get("run_id") or "").strip()
    actor_id = named_principal(kwargs.get("actor_id"))
    outcome_type = (kwargs.get("outcome_type") or "").strip()
    if not run_id:
        return json.dumps({"error": "run_id is required."})
    if not outcome_type:
        return json.dumps({"error": "outcome_type is required."})
    if outcome_type not in OUTCOME_TYPES:
        return json.dumps({
            "error": f"Unknown outcome_type '{outcome_type}'.",
            "valid": sorted(OUTCOME_TYPES),
        })
    source_run = get_run(_base_path(), run_id)
    if (
        not actor_id
        or source_run is None
        or _outcome_run_owner(source_run) != actor_id
    ):
        return json.dumps({
            "error": f"run {run_id!r} is not available to this account",
            "code": "handoff_authority_required",
        })
    evidence_url = (kwargs.get("evidence_url") or "").strip() or None
    gate_event_id = (kwargs.get("gate_event_id") or "").strip() or None
    note = (kwargs.get("note") or "").strip()
    raw_payload = kwargs.get("payload_json") or "{}"
    try:
        payload = json.dumps(
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
    except (json.JSONDecodeError, TypeError):
        payload = "{}"
    outcome_id = str(_uuid.uuid4())
    recorded_at = datetime.now(timezone.utc).isoformat()
    base = _base_path()
    with _outcome_connect(base) as conn:
        conn.execute(
            """
            INSERT INTO outcome_event
                (outcome_id, run_id, outcome_type, evidence_url,
                 claim_run_id, payload, recorded_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, run_id, outcome_type, evidence_url,
             gate_event_id, payload, recorded_at, note),
        )
        record_user_attested_outcome_evidence(
            conn,
            outcome_id=outcome_id,
            account_id=actor_id,
            actor_id=actor_id,
        )
    return json.dumps({
        "status": "recorded",
        "outcome_id": outcome_id,
        "run_id": run_id,
        "outcome_type": outcome_type,
        "recorded_at": recorded_at,
    })


def _action_list_outcomes(kwargs: dict[str, Any]) -> str:
    branch_def_id = (kwargs.get("branch_def_id") or "").strip()
    run_id = (kwargs.get("run_id") or "").strip()
    outcome_type = (kwargs.get("outcome_type") or "").strip()
    try:
        limit = max(1, min(int(kwargs.get("limit") or 50), 200))
    except (TypeError, ValueError):
        limit = 50

    base = _base_path()

    # If filtering by branch_def_id, resolve matching run_ids first.
    if branch_def_id and not run_id:
        try:
            from tinyassets.runs import initialize_runs_db, query_runs
            initialize_runs_db(base)
            runs = query_runs(base, branch_def_id=branch_def_id, limit=1000)
            run_ids = [r["run_id"] for r in runs]
            if not run_ids:
                return json.dumps({"outcomes": [], "count": 0})
        except Exception:
            return json.dumps({"outcomes": [], "count": 0})
    else:
        run_ids = [run_id] if run_id else []

    with _outcome_connect(base) as conn:
        clauses: list[str] = []
        params: list[Any] = []
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            clauses.append(f"run_id IN ({placeholders})")
            params.extend(run_ids)
        if outcome_type:
            clauses.append("outcome_type = ?")
            params.append(outcome_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM outcome_event {where} ORDER BY recorded_at DESC LIMIT ?",
            params,
        ).fetchall()
        outcomes = []
        for row in rows:
            evidence, transitions = _outcome_lifecycle(conn, row["outcome_id"])
            outcomes.append(
                _outcome_row_to_dict(
                    row,
                    evidence=evidence,
                    transitions=transitions,
                )
            )
    return json.dumps({"outcomes": outcomes, "count": len(outcomes)})


def _action_get_outcome(kwargs: dict[str, Any]) -> str:
    outcome_id = (kwargs.get("outcome_id") or "").strip()
    if not outcome_id:
        return json.dumps({"error": "outcome_id is required."})
    base = _base_path()
    with _outcome_connect(base) as conn:
        row = conn.execute(
            "SELECT * FROM outcome_event WHERE outcome_id = ?",
            (outcome_id,),
        ).fetchone()
        evidence, transitions = (
            _outcome_lifecycle(conn, outcome_id)
            if row is not None
            else (None, [])
        )
    if row is None:
        return json.dumps({"error": f"outcome_id '{outcome_id}' not found."})
    return json.dumps(
        _outcome_row_to_dict(row, evidence=evidence, transitions=transitions)
    )


_OUTCOME_ACTIONS: dict[str, Any] = {
    "record_outcome": _action_record_outcome,
    "list_outcomes": _action_list_outcomes,
    "get_outcome": _action_get_outcome,
}
