"""Conformance-pack evidence records for outcome gate claims.

Conformance packs are standards/readiness evidence bundles. The substrate is
discipline-neutral; domain-specific checks live in pack data or evaluator
branches. ``research-publication-v0`` is only the first built-in pack instance.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.sqlite_connection import ClosingConnection

RESEARCH_PUBLICATION_STANDARD_ID = "research-publication-v0"
VALID_STATUSES = {
    "ready",
    "blocked",
    "partially-satisfied",
    "requires-human-review",
}

CONFORMANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS conformance_pack (
    pack_id                    TEXT PRIMARY KEY,
    goal_id                    TEXT NOT NULL,
    branch_def_id              TEXT NOT NULL DEFAULT '',
    target_rung                TEXT NOT NULL DEFAULT '',
    standard_id                TEXT NOT NULL,
    standard_version           TEXT NOT NULL DEFAULT '',
    jurisdiction               TEXT NOT NULL DEFAULT '',
    evidence_requirements_json TEXT NOT NULL DEFAULT '{}',
    vocab_refs_json            TEXT NOT NULL DEFAULT '[]',
    checklist_json             TEXT NOT NULL DEFAULT '[]',
    evidence_receipt_refs_json TEXT NOT NULL DEFAULT '[]',
    evaluator_branch_version_id TEXT NOT NULL DEFAULT '',
    migration_from_pack_id     TEXT NOT NULL DEFAULT '',
    status                     TEXT NOT NULL CHECK (
        status IN (
            'ready',
            'blocked',
            'partially-satisfied',
            'requires-human-review'
        )
    ),
    blockers_json              TEXT NOT NULL DEFAULT '[]',
    created_by                 TEXT NOT NULL,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conformance_pack_goal
    ON conformance_pack(goal_id);
CREATE INDEX IF NOT EXISTS idx_conformance_pack_branch
    ON conformance_pack(branch_def_id);
CREATE INDEX IF NOT EXISTS idx_conformance_pack_standard
    ON conformance_pack(standard_id);
"""


@dataclass(frozen=True)
class ConformancePack:
    pack_id: str
    goal_id: str
    branch_def_id: str
    target_rung: str
    standard_id: str
    standard_version: str
    jurisdiction: str
    evidence_requirements: dict[str, Any]
    vocab_refs: list[Any]
    checklist: list[Any]
    evidence_receipt_refs: list[Any]
    evaluator_branch_version_id: str
    migration_from_pack_id: str
    status: str
    blockers: list[str]
    created_by: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "goal_id": self.goal_id,
            "branch_def_id": self.branch_def_id,
            "target_rung": self.target_rung,
            "standard_id": self.standard_id,
            "standard_version": self.standard_version,
            "jurisdiction": self.jurisdiction,
            "evidence_requirements": self.evidence_requirements,
            "vocab_refs": self.vocab_refs,
            "checklist": self.checklist,
            "evidence_receipt_refs": self.evidence_receipt_refs,
            "evaluator_branch_version_id": self.evaluator_branch_version_id,
            "migration_from_pack_id": self.migration_from_pack_id,
            "status": self.status,
            "blockers": self.blockers,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_db(base_path: str | Path) -> Path:
    from tinyassets.runs import runs_db_path

    return runs_db_path(base_path)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate_conformance_pack_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CONFORMANCE_SCHEMA)


def _ensure_schema(base_path: str | Path) -> Path:
    db = _runs_db(base_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db) as conn:
        migrate_conformance_pack_schema(conn)
    return db


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _string_field(pack: dict[str, Any], field_name: str) -> str:
    return str(pack.get(field_name) or "").strip()


def _default_validator_for_research_publication_v0(
    evidence_requirements: dict[str, Any],
) -> list[str]:
    """Return blockers for the first research-publication pack instance."""
    blockers: list[str] = []
    disclosures = evidence_requirements.get("disclosures")
    disclosures = disclosures if isinstance(disclosures, dict) else {}
    required_paths = (
        ("target_venue", evidence_requirements.get("target_venue")),
        (
            "policy_requirements",
            evidence_requirements.get("policy_requirements"),
        ),
        ("artifact_manifest", evidence_requirements.get("artifact_manifest")),
        ("code_data_release", evidence_requirements.get("code_data_release")),
        (
            "reproducibility_checks",
            evidence_requirements.get("reproducibility_checks"),
        ),
        (
            "empirical_anchor_status",
            evidence_requirements.get("empirical_anchor_status"),
        ),
        ("disclosures.author_contributor", disclosures.get("author_contributor")),
        ("disclosures.ai_use", disclosures.get("ai_use")),
    )
    for name, value in required_paths:
        if not _non_empty(value):
            blockers.append(f"missing:{name}")

    checks = _as_list(evidence_requirements.get("reproducibility_checks"))
    for idx, check in enumerate(checks):
        if not isinstance(check, dict):
            blockers.append(f"invalid:reproducibility_checks[{idx}]")
            continue
        status = str(check.get("status") or "").strip().lower()
        if status not in {"pass", "passed", "complete", "completed"}:
            blockers.append(f"failing:reproducibility_checks[{idx}]")

    return blockers


def _explicit_blockers(pack: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for blocker in _as_list(pack.get("blockers")):
        text = str(blocker).strip()
        if text:
            blockers.append(text)
    return blockers


def _validate_status(raw_status: Any) -> str:
    status = str(raw_status or "").strip()
    if not status:
        return ""
    if status not in VALID_STATUSES:
        raise ValueError(
            "status must be one of: " + ", ".join(sorted(VALID_STATUSES))
        )
    return status


def _derive_status(
    *,
    standard_id: str,
    pack: dict[str, Any],
    blockers: list[str],
) -> str:
    explicit_status = _validate_status(pack.get("status"))
    if blockers:
        return "blocked"
    if explicit_status:
        return explicit_status
    if standard_id == RESEARCH_PUBLICATION_STANDARD_ID:
        return "ready"
    return "requires-human-review"


def _validate_pack(
    *,
    standard_id: str,
    evidence_requirements: dict[str, Any],
) -> list[str]:
    if standard_id == RESEARCH_PUBLICATION_STANDARD_ID:
        return _default_validator_for_research_publication_v0(
            evidence_requirements
        )
    return []


def _from_row(row: sqlite3.Row) -> ConformancePack:
    return ConformancePack(
        pack_id=row["pack_id"],
        goal_id=row["goal_id"],
        branch_def_id=row["branch_def_id"],
        target_rung=row["target_rung"],
        standard_id=row["standard_id"],
        standard_version=row["standard_version"],
        jurisdiction=row["jurisdiction"],
        evidence_requirements=json.loads(
            row["evidence_requirements_json"] or "{}"
        ),
        vocab_refs=list(json.loads(row["vocab_refs_json"] or "[]")),
        checklist=list(json.loads(row["checklist_json"] or "[]")),
        evidence_receipt_refs=list(
            json.loads(row["evidence_receipt_refs_json"] or "[]")
        ),
        evaluator_branch_version_id=row["evaluator_branch_version_id"],
        migration_from_pack_id=row["migration_from_pack_id"],
        status=row["status"],
        blockers=list(json.loads(row["blockers_json"] or "[]")),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def record_conformance_pack(
    base_path: str | Path,
    *,
    goal_id: str,
    pack: dict[str, Any],
    created_by: str,
    branch_def_id: str = "",
    target_rung: str = "",
) -> ConformancePack:
    if not goal_id:
        raise ValueError("goal_id is required")
    if not isinstance(pack, dict):
        raise ValueError("conformance_pack_json must be a JSON object")
    standard_id = _string_field(pack, "standard_id")
    if not standard_id:
        raise ValueError("standard_id is required")
    evidence_requirements = _as_dict(
        pack.get("evidence_requirements"),
        "evidence_requirements",
    )
    target_rung = (target_rung or _string_field(pack, "target_rung")).strip()
    blockers = [
        *_validate_pack(
            standard_id=standard_id,
            evidence_requirements=evidence_requirements,
        ),
        *_explicit_blockers(pack),
    ]
    status = _derive_status(
        standard_id=standard_id,
        pack=pack,
        blockers=blockers,
    )
    now = _now()
    pack_id = uuid.uuid4().hex[:16]
    db = _ensure_schema(base_path)
    with _connect(db) as conn:
        conn.execute(
            """
            INSERT INTO conformance_pack (
                pack_id, goal_id, branch_def_id, target_rung, standard_id,
                standard_version, jurisdiction, evidence_requirements_json,
                vocab_refs_json, checklist_json, evidence_receipt_refs_json,
                evaluator_branch_version_id, migration_from_pack_id, status,
                blockers_json, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                goal_id,
                branch_def_id,
                target_rung,
                standard_id,
                _string_field(pack, "standard_version"),
                _string_field(pack, "jurisdiction"),
                json.dumps(evidence_requirements, sort_keys=True),
                json.dumps(_as_list(pack.get("vocab_refs")), sort_keys=True),
                json.dumps(_as_list(pack.get("checklist")), sort_keys=True),
                json.dumps(
                    _as_list(pack.get("evidence_receipt_refs")),
                    sort_keys=True,
                ),
                _string_field(pack, "evaluator_branch_version_id"),
                _string_field(pack, "migration_from_pack_id"),
                status,
                json.dumps(blockers),
                created_by,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM conformance_pack WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    return _from_row(row)


def get_conformance_pack(
    base_path: str | Path,
    pack_id: str,
) -> ConformancePack | None:
    if not pack_id:
        return None
    db = _ensure_schema(base_path)
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM conformance_pack WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    return _from_row(row) if row is not None else None


def list_conformance_packs(
    base_path: str | Path,
    *,
    goal_id: str = "",
    branch_def_id: str = "",
    standard_id: str = "",
    public_goal_ids: set[str] | None = None,
    limit: int = 50,
) -> list[ConformancePack]:
    db = _ensure_schema(base_path)
    clauses: list[str] = []
    params: list[Any] = []
    if goal_id:
        clauses.append("goal_id = ?")
        params.append(goal_id)
    if branch_def_id:
        clauses.append("branch_def_id = ?")
        params.append(branch_def_id)
    if standard_id:
        clauses.append("standard_id = ?")
        params.append(standard_id)
    if public_goal_ids is not None:
        clauses.append(
            "goal_id IN (SELECT value FROM json_each(?))"
        )
        params.append(json.dumps(sorted(public_goal_ids)))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db) as conn:
        rows = conn.execute(
            f"SELECT * FROM conformance_pack {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            (*params, min(max(1, int(limit)), 500)),
        ).fetchall()
    return [_from_row(row) for row in rows]


def required_standard_id_for_rung(
    rung_key: str,
    ladder: list[dict[str, Any]],
) -> str | None:
    for rung in ladder:
        if rung.get("rung_key") != rung_key:
            continue
        requirement = (
            rung.get("requires_conformance_pack")
            or rung.get("conformance_pack_required")
        )
        if not requirement:
            return None
        if isinstance(requirement, str):
            return requirement.strip()
        if isinstance(requirement, dict):
            return str(requirement.get("standard_id") or "").strip()
        return str(
            rung.get("required_standard_id") or rung.get("standard_id") or ""
        ).strip()
    return None


def rung_requires_conformance_pack(
    rung_key: str,
    ladder: list[dict[str, Any]],
    *,
    standard_id: str = "",
) -> bool:
    required = required_standard_id_for_rung(rung_key, ladder)
    if required is None:
        return False
    return not standard_id or not required or required == standard_id
