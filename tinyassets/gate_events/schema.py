"""Gate events schema — DDL and dataclasses for real-world outcome attestation.


Two tables:
  gate_event       — append-only attestation records (immutable once created)
  gate_event_cite  — N:M linking gate events to branch versions / runs

Attribution language invariant (load-bearing):
  "This branch's output was cited in this gate event" — never "caused".
  Causality is for historians; the record is evidence of citation only.

Verification status flow:
  attested → verified (by different user than attester)
  attested → disputed (by any user with rationale)
  attested/verified/disputed → retracted (audit record preserved)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# ── Status literals ───────────────────────────────────────────────────────────

VerificationStatus = str  # "attested" | "verified" | "disputed" | "retracted"

VERIFICATION_STATUSES = frozenset({"attested", "verified", "disputed", "retracted"})

# ── DDL ───────────────────────────────────────────────────────────────────────

GATE_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS gate_event (
    event_id            TEXT PRIMARY KEY,
    goal_id             TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    event_date          TEXT NOT NULL,
    attested_by         TEXT NOT NULL,
    attested_at         TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'attested'
                            CHECK (verification_status IN (
                                'attested','verified','disputed','retracted'
                            )),
    verified_by         TEXT,
    verified_at         TEXT,
    disputed_by         TEXT,
    disputed_at         TEXT,
    dispute_reason      TEXT NOT NULL DEFAULT '',
    retracted_by        TEXT,
    retracted_at        TEXT,
    retraction_note     TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_gate_event_goal
    ON gate_event(goal_id);

CREATE INDEX IF NOT EXISTS idx_gate_event_type
    ON gate_event(event_type);

CREATE INDEX IF NOT EXISTS idx_gate_event_status
    ON gate_event(verification_status);

CREATE TABLE IF NOT EXISTS gate_event_cite (
    cite_id              TEXT PRIMARY KEY,
    event_id             TEXT NOT NULL REFERENCES gate_event(event_id),
    branch_version_id    TEXT NOT NULL,
    run_id               TEXT,
    contribution_summary TEXT NOT NULL DEFAULT '',
    cited_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cite_event
    ON gate_event_cite(event_id);

CREATE INDEX IF NOT EXISTS idx_cite_branch_version
    ON gate_event_cite(branch_version_id);
"""

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class GateEventCite:
    """One branch version's citation in a gate event."""

    cite_id: str
    event_id: str
    branch_version_id: str
    cited_at: str
    run_id: str | None = None
    contribution_summary: str = ""

    @classmethod
    def from_row(cls, row: dict) -> GateEventCite:
        return cls(
            cite_id=row["cite_id"],
            event_id=row["event_id"],
            branch_version_id=row["branch_version_id"],
            cited_at=row["cited_at"],
            run_id=row.get("run_id"),
            contribution_summary=row.get("contribution_summary") or "",
        )


@dataclass
class GateEvent:
    """One real-world outcome attestation record. Append-only."""

    event_id: str
    goal_id: str
    event_type: str
    event_date: str
    attested_by: str
    attested_at: str
    verification_status: VerificationStatus
    notes: str = ""
    verified_by: str | None = None
    verified_at: str | None = None
    disputed_by: str | None = None
    disputed_at: str | None = None
    dispute_reason: str = ""
    retracted_by: str | None = None
    retracted_at: str | None = None
    retraction_note: str = ""
    cites: list[GateEventCite] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verification_status not in VERIFICATION_STATUSES:
            raise ValueError(
                f"verification_status must be one of {sorted(VERIFICATION_STATUSES)}, "
                f"got {self.verification_status!r}"
            )

    @property
    def is_self_verified(self) -> bool:
        """True if verified_by == attested_by (invalid — raises on verify)."""
        return self.verified_by is not None and self.verified_by == self.attested_by

    @property
    def is_retracted(self) -> bool:
        return self.verification_status == "retracted"

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "verified"

    @property
    def is_disputed(self) -> bool:
        return self.verification_status == "disputed"

    @property
    def cite_count(self) -> int:
        return len(self.cites)

    def verify(self, *, verifier_id: str, verified_at: str) -> GateEvent:
        """Return a new GateEvent with status=verified.

        Raises ValueError if verifier_id == attested_by (self-verify).
        Raises ValueError if not in 'attested' status.
        """
        if verifier_id == self.attested_by:
            raise ValueError(
                f"Verifier ({verifier_id!r}) cannot be the same as attester ({self.attested_by!r})"
            )
        if self.verification_status != "attested":
            raise ValueError(
                f"Cannot verify a gate event in status {self.verification_status!r} "
                f"(expected 'attested')"
            )
        import dataclasses
        return dataclasses.replace(
            self,
            verification_status="verified",
            verified_by=verifier_id,
            verified_at=verified_at,
        )

    def dispute(self, *, disputed_by: str, disputed_at: str, reason: str) -> GateEvent:
        """Return a new GateEvent with status=disputed."""
        if self.is_retracted:
            raise ValueError("Cannot dispute a retracted gate event")
        import dataclasses
        return dataclasses.replace(
            self,
            verification_status="disputed",
            disputed_by=disputed_by,
            disputed_at=disputed_at,
            dispute_reason=reason,
        )

    def retract(self, *, retracted_by: str, retracted_at: str, note: str) -> GateEvent:
        """Return a new GateEvent with status=retracted. Audit trail preserved."""
        import dataclasses
        return dataclasses.replace(
            self,
            verification_status="retracted",
            retracted_by=retracted_by,
            retracted_at=retracted_at,
            retraction_note=note,
        )

    def evidence_urls_list(self, payload: str) -> list[str]:
        """Parse evidence_urls from JSON payload string."""
        try:
            parsed = json.loads(payload)
            return parsed.get("evidence_urls", []) if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, AttributeError):
            return []

    @classmethod
    def from_row(cls, row: dict) -> GateEvent:
        return cls(
            event_id=row["event_id"],
            goal_id=row["goal_id"],
            event_type=row["event_type"],
            event_date=row["event_date"],
            attested_by=row["attested_by"],
            attested_at=row["attested_at"],
            verification_status=row.get("verification_status") or "attested",
            notes=row.get("notes") or "",
            verified_by=row.get("verified_by"),
            verified_at=row.get("verified_at"),
            disputed_by=row.get("disputed_by"),
            disputed_at=row.get("disputed_at"),
            dispute_reason=row.get("dispute_reason") or "",
            retracted_by=row.get("retracted_by"),
            retracted_at=row.get("retracted_at"),
            retraction_note=row.get("retraction_note") or "",
        )


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_gate_event_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Create gate event tables if absent. Idempotent."""
    conn.executescript(GATE_EVENT_SCHEMA)
