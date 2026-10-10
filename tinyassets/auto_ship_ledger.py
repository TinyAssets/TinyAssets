"""Auto-ship attempts ledger — PR #198 §8 (option-2 Slice A).

Append-only structured store for ship attempts. Every call into
``tinyassets.auto_ship.validate_ship_request`` that produces a final
ship_decision should be recorded here so that:

1. The loop's release_safety_gate has an audit trail of every packet it
   accepted/rejected.
2. The post-merge observation gate (PR #198 §5.2 + §10 Phase 4 — Slice
   B / ``observation_gate_result``) can join PR opens back to the
   original validation that approved them.
3. Rollback decisions (Slice C) can locate the rollback_handle that was
   recorded when the attempt was approved.

Storage: ``<universe_path>/auto_ship_attempts.jsonl`` — one
``ShipAttempt`` per line. Append-on-create, full read-modify-write on
update. This matches the ``branch_tasks.json`` precedent: small file,
race-safe via sidecar ``.lock``, no schema migration cost. We can
graduate to sqlite later if attempt volume warrants.

Spec source:
- ``docs/milestones/auto-ship-canary-v0.md`` §8 Evidence record (field
  list + suggested artifact paths)
- ``docs/milestones/auto-ship-canary-v0.md`` §10 Phase 1/2/3 (which
  ship_status transitions are valid in which phase)

Phase 1 contract: a successful ``validate_ship_request`` records an
attempt with ``ship_status="skipped"`` and ``would_open_pr=true``. A
blocked validation records ``ship_status="blocked"`` and the violations
in ``error_message``. No PR is ever opened from this module — Phase 2
will mutate ``ship_status`` to ``"opened"`` once it lands, then to
``"merged"`` or ``"failed"`` depending on CI + merge result.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

LEDGER_FILENAME = "auto_ship_attempts.jsonl"
# Dotted: a platform coordination file, never owner content
# (command_center_layout.PLATFORM_LOCK_NAMES).
LOCK_FILENAME = ".auto_ship_attempts.jsonl.lock"

#: Valid ``ship_status`` values across Phase 1-3 (§10).
VALID_SHIP_STATUSES: frozenset[str] = frozenset({
    "skipped",   # Phase 1 — validator passed but no PR open requested
    "blocked",   # validator blocked — error_class+error_message set
    "opened",    # Phase 2 — PR opened, awaiting CI/merge
    "merged",    # Phase 3 — PR merged, observation window starts
    "failed",    # PR opened but CI failed or merge rejected
    "rolled_back",  # Slice C — observation gate triggered a revert PR
})

#: Mutable fields ``update_attempt`` is allowed to change after creation.
#: ``ship_attempt_id`` and ``created_at`` are immutable; everything else
#: in the schema may evolve as the attempt advances through phases.
MUTABLE_FIELDS: frozenset[str] = frozenset({
    "ship_status",
    "pr_url",
    "commit_sha",
    "ci_status",
    "rollback_handle",
    "stable_evidence_handle",
    "updated_at",
    "error_class",
    "error_message",
    "observation_status",
    "observation_status_at",
    "rollback_pr_number",  # Slice C — set when rollback PR opens
    "rollback_pr_url",     # Slice C — full URL of rollback PR
})


@dataclass
class ShipAttempt:
    """One row in the auto-ship attempts ledger.

    Field naming matches ``docs/milestones/auto-ship-canary-v0.md`` §8
    exactly so committed-artifact JSON (the per-attempt evidence file)
    can be derived from this row without renames.

    A few extension fields beyond the §8 list are present so Slice B
    (observation gate) and Slice C (rollback) don't have to widen the
    schema later:

    - ``observation_status`` / ``observation_status_at`` — the post-merge
      health verdict from the observation gate (§5.2 Phase 4).
    - ``would_open_pr`` — the validator's Phase 1 verdict, distinct from
      ``ship_status`` so a Phase 1 ``skipped`` row still records whether
      it WOULD have opened a PR if Phase 2 had been live.

    Defaults are ``""`` for strings and ``None`` for optional sub-fields
    so empty-row JSON has stable shape for diff review.
    """

    ship_attempt_id: str
    created_at: str
    updated_at: str
    ship_status: str
    request_id: str = ""
    parent_run_id: str = ""
    child_run_id: str = ""
    branch_def_id: str = ""
    release_gate_result: str = ""
    ship_class: str = ""
    pr_url: str = ""
    commit_sha: str = ""
    changed_paths_json: str = ""
    ci_status: str = ""
    rollback_handle: str = ""
    stable_evidence_handle: str = ""
    error_class: str = ""
    error_message: str = ""
    would_open_pr: bool = False
    observation_status: str = ""
    observation_status_at: str = ""
    # Slice C — rollback PR identity (PR #227 spec; Codex review on 2026-05-03).
    # Populated by ``record_rollback_decision`` per docs/specs/auto-ship-rollback-v0.md.
    # Empty string when no rollback has been issued. Forward-compat-safe: from_dict
    # ignores unknown keys, and the wider chain (status surface, validators) treats
    # empty as "no rollback PR yet."
    rollback_pr_number: str = ""
    rollback_pr_url: str = ""
    # Rubric warn-mode observability: JSON list of rubric-only warnings recorded
    # on a PASSED row when TINYASSETS_AUTO_SHIP_RUBRIC_MODE=warn (see auto_ship.py).
    # Forward-compat-safe like the Slice C fields above — from_dict ignores
    # unknown keys, so older runtimes still read newer rows and vice versa.
    rubric_warnings_json: str = ""
    # Trajectory warn-mode observability: JSON list of path-quality warnings
    # (one per failing trajectory check) recorded when
    # TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE=warn (see auto_ship.py S3 wiring).
    # SEPARATE channel from rubric_warnings_json. Forward-compat-safe.
    trajectory_warnings_json: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict) -> "ShipAttempt":
        # Forward-compat: ignore unknown keys so an older runtime can
        # still read newer rows.
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in row.items() if k in known}
        return cls(**clean)


# ── ID generation ─────────────────────────────────────────────────────────


def new_attempt_id(*, now: datetime | None = None) -> str:
    """Stable ``ship_<YYYYMMDD>_<8hex>`` identifier matching §8 example.

    The hex tail is from ``secrets.token_hex(4)`` so two attempts
    minted in the same UTC second do not collide. Test code can
    inject ``now`` to make IDs deterministic.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return f"ship_{now.strftime('%Y%m%d')}_{secrets.token_hex(4)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Paths ────────────────────────────────────────────────────────────────


def ledger_path(universe_path: Path) -> Path:
    return Path(universe_path) / LEDGER_FILENAME


def _lock_path(universe_path: Path) -> Path:
    return Path(universe_path) / LOCK_FILENAME


# ── File lock (mirrors tinyassets.branch_tasks._file_lock) ──────────────────


@contextlib.contextmanager
def _file_lock(universe_path: Path) -> Iterator[None]:
    """Cross-platform exclusive lock on a sidecar ``.lock`` file.

    Same primitive as ``tinyassets.branch_tasks._file_lock`` — kept as a
    private mirror rather than importing it because the lock is
    namespaced to ``.auto_ship_attempts.jsonl.lock``, not to the queue
    file. Two concurrent ledger writes serialize; ledger writes do
    NOT serialize against branch_tasks writes (they touch different
    files anyway). Opened link-free through ``universe_files.open_lock_file``.
    """
    from tinyassets.universe_files import open_lock_file

    Path(universe_path).mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(universe_path, LOCK_FILENAME)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


# ── Read / write primitives ──────────────────────────────────────────────


def _read_raw(lp: Path) -> list[dict]:
    """Read all rows from the JSONL file. Returns [] if missing or empty.

    Skips blank lines silently. Raises ``RuntimeError`` on a malformed
    JSON line — Hard Rule 8 (no silent fallback on corrupt data).
    """
    if not lp.exists():
        return []
    rows: list[dict] = []
    with lp.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Corrupt ledger row at {lp}:{lineno}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise RuntimeError(
                    f"Ledger row at {lp}:{lineno} is not a JSON object"
                )
            rows.append(row)
    return rows


def _write_raw(lp: Path, rows: list[dict]) -> None:
    """Atomic temp+rename rewrite of the entire ledger.

    Used by ``update_attempt``. ``record_attempt`` uses a true append
    instead so it does not have to re-serialize the whole file.
    """
    lp.parent.mkdir(parents=True, exist_ok=True)
    tmp = lp.with_suffix(lp.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str))
            fh.write("\n")
    os.replace(tmp, lp)


# ── Public API ───────────────────────────────────────────────────────────


def read_attempts(
    universe_path: Path,
    *,
    limit: int | None = None,
    ship_status: str | None = None,
) -> list[ShipAttempt]:
    """File-locked read. Returns [] on missing file.

    ``limit`` returns the most recent N attempts (tail); ``ship_status``
    filters to that status only. Both filters apply if both are given;
    ``limit`` is applied AFTER ``ship_status`` so callers can ask for
    "the last 10 merged attempts" cleanly.
    """
    lp = ledger_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(lp)
    attempts = [ShipAttempt.from_dict(row) for row in raw]
    if ship_status is not None:
        attempts = [a for a in attempts if a.ship_status == ship_status]
    if limit is not None:
        attempts = attempts[-limit:] if limit > 0 else []
    return attempts


def find_attempt(
    universe_path: Path, ship_attempt_id: str,
) -> ShipAttempt | None:
    """File-locked lookup by ship_attempt_id. Returns None if not found."""
    lp = ledger_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(lp)
    for row in raw:
        if row.get("ship_attempt_id") == ship_attempt_id:
            return ShipAttempt.from_dict(row)
    return None


def summarize_rubric_warnings(
    universe_path: Path, *, limit: int | None = None,
) -> dict:
    """Read-only observability for the rubric warn-mode period.

    Aggregates ``rubric_warnings_json`` across recorded attempts so the host can
    see the real rubric failure rate BEFORE flipping
    ``TINYASSETS_AUTO_SHIP_RUBRIC_MODE=enforce`` (see
    docs/design-notes/2026-06-24-coding-loop-eval-gate-wiring.md S2). Returns the
    total attempts scanned, how many carried rubric warnings, and per-``rule_id``
    counts (descending). Reads via the standard locked read path; does not mutate
    any ledger row. Robust to hand-edited/corrupt rows (non-list or unparseable
    ``rubric_warnings_json`` is skipped, never raises).
    """
    if not ledger_path(universe_path).exists():
        return {"total_attempts": 0, "attempts_with_warnings": 0, "rule_id_counts": {}}
    attempts = read_attempts(universe_path, limit=limit)
    rule_counts: dict[str, int] = {}
    warned = 0
    for attempt in attempts:
        raw = attempt.rubric_warnings_json or ""
        if not raw:
            continue
        try:
            warns = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(warns, list) or not warns:
            continue
        warned += 1
        for w in warns:
            rid = w.get("rule_id", "?") if isinstance(w, dict) else "?"
            rule_counts[rid] = rule_counts.get(rid, 0) + 1
    return {
        "total_attempts": len(attempts),
        "attempts_with_warnings": warned,
        "rule_id_counts": dict(
            sorted(rule_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
    }


def summarize_trajectory_warnings(
    universe_path: Path, *, limit: int | None = None,
) -> dict:
    """Read-only observability for the trajectory warn-mode period.

    Aggregates ``trajectory_warnings_json`` across recorded attempts so the host
    can see the real path-quality failure rate BEFORE any decision to gate on
    trajectory (see docs/design-notes/2026-06-24-coding-loop-eval-gate-wiring.md
    S3). Returns total attempts scanned, how many carried trajectory warnings,
    and per-``check`` counts (descending). Reads via the standard locked read
    path; does not mutate any ledger row. Robust to hand-edited/corrupt rows
    (non-list or unparseable ``trajectory_warnings_json`` is skipped, never
    raises).
    """
    if not ledger_path(universe_path).exists():
        return {"total_attempts": 0, "attempts_with_warnings": 0, "check_counts": {}}
    attempts = read_attempts(universe_path, limit=limit)
    check_counts: dict[str, int] = {}
    warned = 0
    for attempt in attempts:
        raw = attempt.trajectory_warnings_json or ""
        if not raw:
            continue
        try:
            warns = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(warns, list) or not warns:
            continue
        warned += 1
        for w in warns:
            check = w.get("check", "?") if isinstance(w, dict) else "?"
            check_counts[check] = check_counts.get(check, 0) + 1
    return {
        "total_attempts": len(attempts),
        "attempts_with_warnings": warned,
        "check_counts": dict(
            sorted(check_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
    }


def record_attempt(universe_path: Path, attempt: ShipAttempt) -> None:
    """File-locked append. Validates ``ship_status``; stamps timestamps
    if missing.

    Raises ``ValueError`` on invalid ``ship_status`` or duplicate
    ``ship_attempt_id``. Duplicate detection is by full read so the
    ledger can be modest (~1k rows) before the read becomes a
    bottleneck; at that point graduate to sqlite.
    """
    if attempt.ship_status not in VALID_SHIP_STATUSES:
        raise ValueError(
            f"Invalid ship_status {attempt.ship_status!r}; allowed: "
            f"{', '.join(sorted(VALID_SHIP_STATUSES))}"
        )
    if not attempt.ship_attempt_id:
        raise ValueError("ship_attempt_id is required")
    if not attempt.created_at:
        attempt.created_at = _now_iso()
    if not attempt.updated_at:
        attempt.updated_at = attempt.created_at

    lp = ledger_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(lp)
        for row in raw:
            if row.get("ship_attempt_id") == attempt.ship_attempt_id:
                raise ValueError(
                    f"ship_attempt_id {attempt.ship_attempt_id!r} already "
                    f"recorded — use update_attempt to mutate"
                )
        # True append, no full rewrite — JSONL's main ergonomic win.
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(attempt.to_dict(), default=str))
            fh.write("\n")


def update_attempt(
    universe_path: Path,
    ship_attempt_id: str,
    **fields,
) -> ShipAttempt:
    """File-locked read-modify-write update of one row.

    ``fields`` may only contain keys in ``MUTABLE_FIELDS``. ``updated_at``
    is auto-stamped to the current time on every successful update;
    callers do not need to pass it.

    Raises ``KeyError`` if no row with that ``ship_attempt_id`` exists.
    Raises ``ValueError`` on attempt to mutate an immutable field or
    set ``ship_status`` to an invalid value.
    """
    illegal = set(fields) - MUTABLE_FIELDS
    if illegal:
        raise ValueError(
            f"Cannot mutate immutable field(s): {', '.join(sorted(illegal))}"
        )
    if "ship_status" in fields and fields["ship_status"] not in VALID_SHIP_STATUSES:
        raise ValueError(
            f"Invalid ship_status {fields['ship_status']!r}; allowed: "
            f"{', '.join(sorted(VALID_SHIP_STATUSES))}"
        )

    lp = ledger_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(lp)
        for idx, row in enumerate(raw):
            if row.get("ship_attempt_id") == ship_attempt_id:
                row.update({k: v for k, v in fields.items() if k != "updated_at"})
                row["updated_at"] = fields.get("updated_at") or _now_iso()
                raw[idx] = row
                _write_raw(lp, raw)
                return ShipAttempt.from_dict(row)
        raise KeyError(
            f"ship_attempt_id {ship_attempt_id!r} not found in ledger at {lp}"
        )


# ── Convenience constructor for the common Phase 1 path ──────────────────


def attempt_from_decision(
    *,
    decision: dict,
    request_id: str = "",
    parent_run_id: str = "",
    child_run_id: str = "",
    branch_def_id: str = "",
    release_gate_result: str = "",
    ship_class: str = "",
    changed_paths: list[str] | None = None,
    stable_evidence_handle: str = "",
    now: datetime | None = None,
) -> ShipAttempt:
    """Build a ``ShipAttempt`` row from a ``validate_ship_request``
    decision plus the call-site context the validator does not see.

    Phase 1 caller expectation:

    >>> dec = validate_ship_request(packet)
    >>> row = attempt_from_decision(
    ...     decision=dec,
    ...     request_id=packet.get("request_id", ""),
    ...     parent_run_id=packet.get("parent_run_id", ""),
    ...     ship_class=packet.get("ship_class", ""),
    ...     changed_paths=packet.get("changed_paths", []),
    ...     release_gate_result=packet.get("release_gate_result", ""),
    ...     stable_evidence_handle=packet.get("stable_evidence_handle", ""),
    ... )
    >>> record_attempt(universe_path, row)

    Phase 2 then calls ``update_attempt`` to fill ``pr_url`` and flip
    ``ship_status`` to ``"opened"``.
    """
    ts = (now or datetime.now(timezone.utc)).isoformat()
    if decision.get("validation_result") == "passed":
        ship_status = "skipped"
        error_class = ""
        error_message = ""
    else:
        ship_status = "blocked"
        violations = decision.get("violations", [])
        error_class = (
            ",".join(sorted({v.get("rule_id", "") for v in violations}))
            if violations else "blocked"
        )
        error_message = json.dumps(violations, default=str)
    return ShipAttempt(
        ship_attempt_id=new_attempt_id(now=now),
        created_at=ts,
        updated_at=ts,
        ship_status=ship_status,
        request_id=request_id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        branch_def_id=branch_def_id,
        release_gate_result=release_gate_result,
        ship_class=ship_class,
        changed_paths_json=json.dumps(changed_paths or []),
        rollback_handle=decision.get("rollback_handle", "") or "",
        stable_evidence_handle=stable_evidence_handle,
        would_open_pr=bool(decision.get("would_open_pr")),
        error_class=error_class,
        error_message=error_message,
        rubric_warnings_json=json.dumps(
            decision.get("rubric_warnings") or [], default=str
        ),
        trajectory_warnings_json=json.dumps(
            decision.get("trajectory_warnings") or [], default=str
        ),
    )
