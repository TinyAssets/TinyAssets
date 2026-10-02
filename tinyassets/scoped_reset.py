"""Host-operator-only safety primitives for exact-founder-home reset.

Servers import only its writer-barrier primitive.  Planning and mutation stay
unregistered operator-only CLI functions: scoped reset is an offline
maintenance operation with an explicit reviewed plan, never a public deletion
surface.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Callable, Mapping

from tinyassets.storage import DB_FILENAME

INVENTORY_REVISION = "scoped-reset-inventory-v4-2026-07-25"

# Classification is location-specific: these are the only reviewed tables
# allowed in the root .tinyassets.db.  A migration adding any other table must
# update this inventory before scoped planning can resume.
MAIN_DB_TABLE_CLASSIFICATIONS = MappingProxyType({
    # Exact candidate-home mutable state.
    "universes": "reset_home",
    "universe_rules": "reset_home",
    "universe_notes": "reset_home",
    "universe_work_targets": "reset_home",
    "universe_hard_priorities": "reset_home",
    "universe_snapshots": "reset_home",
    "branches": "reset_home",
    "branch_heads": "reset_home",
    "user_requests": "reset_home_or_block",
    "vote_windows": "reset_home_or_block",
    "vote_ballots": "reset_home_or_block",
    # Ownership and access are distinct.
    "founder_home": "reset_binding",
    # Account-deletion tombstone: cleared with the binding, because a scoped
    # reset deliberately KEEPS the login and must leave the identity able to
    # birth a fresh home. Leaving it would make a reset test identity unusable.
    "deleted_principals": "reset_binding",
    "universe_acl": "reset_subject_grants",
    # Global identity/auth/commons/audit records are preserved.
    "user_accounts": "preserve",
    "user_sessions": "preserve",
    "capability_grants": "preserve",
    "author_definitions": "preserve",
    "author_forks": "preserve",
    "author_runtime_instances": "preserve_or_block",
    "action_records": "preserve",
    "branch_definitions": "preserve",
    "goals": "preserve",
    "gate_claims": "preserve",
    "canonical_bindings": "preserve",
    "goal_canonicals": "preserve",
    "unreconciled_writes": "block_matching",
    # Queue Epoch 2 is authority/audit-bearing.  Terminal rows are preserved;
    # active or ambiguous rows block instead of being cascaded away.
    "request_admissions": "preserve",
    "request_admission_events": "preserve",
    "branch_tasks_v2": "preserve_or_block",
    "agent_turns": "preserve_or_block",
    "agent_turn_rounds": "preserve_or_block",
    "agent_turn_tools": "preserve_or_block",
    "branch_tasks_v2_quarantine": "block_matching",
    "branch_tasks_v2_maintenance_state": "preserve",
    "request_admission_rollouts": "preserve_or_block",
    # Financial stores are immutable/shared and never reset.
    "escrow_locks": "preserve_or_block",
    "staker_escrow_budget": "preserve_or_block",
    "payout_wallet": "preserve",
    "escrow_balance": "preserve",
    "pending_settlement": "preserve_or_block",
    "settlement_batch": "preserve_or_block",
    "transaction_log": "preserve",
    "treasury_balance": "preserve",
    "bounty_pool_balance": "preserve_or_block",
    "royalty_payout": "preserve_or_block",
    "take_rate_log": "preserve",
    # Scoped reset's content-free coordination state.
    "scoped_reset_leases": "preserve",
    "scoped_reset_operations": "preserve",
    # One owner's own verified model ids. PRESERVED by a scoped reset and removed by
    # ACCOUNT DELETION through its owner_user_id column (already a detected principal
    # key, so it needs no entry in that map). Classified explicitly because the table
    # is created lazily on a first verified turn, so the unclassified-table gate would
    # otherwise fire in production rather than in CI.
    "learned_model_evidence": "preserve",
    # Retired fleet-era cloud-automation and background-branch stores (plan C1,
    # 2026-09-28). Nothing creates or reads these rows any more, but production
    # still holds them until a host-action drops the tables, and an unclassified
    # table blocks every scoped reset. Preserved: they are inert history.
    "cloud_automation_controls": "preserve",
    "cloud_automation_slice_triggers": "preserve",
    "cloud_automation_terminal_receipts": "preserve",
    "cloud_automation_continuations": "preserve",
    "cloud_execution_continuations": "preserve",
    "background_branch_bindings": "preserve",
    "background_branch_attempts": "preserve",
    "background_branch_authority_owners": "preserve",
    # Created alongside the background-branch store. Still read by the agent
    # runtime until that retires (plan B2/B3); preserved like the fleet history,
    # since a scoped reset never needed to rewrite it.
    "automation_activations": "preserve",
    # Which account a universe's storage and seats are charged to
    # (account-storage-quota). PRESERVED: a scoped reset keeps the login, and the
    # fresh home it later births gets its own owner row at creation. The reset
    # home's row names a universe id whose directory is gone, which measures as
    # zero bytes; it is never re-used, because universe ids are never re-issued.
    "universe_owner": "preserve",
})

FAULT_POINTS = (
    "after_journal",
    "after_prepare",
    "before_rename",
    "after_rename",
    "before_commit",
    "after_commit",
    "before_cleanup",
    "after_cleanup",
    "after_complete",
)
_WINDOWS_BARRIER_SLOTS = 256

_ACTIVE_DAEMON_STATES = frozenset({
    "active",
    "claimed",
    "pending",
    "queued",
    "running",
    "starting",
})
_ACTIVE_REQUEST_STATES = frozenset({
    "active",
    "claimed",
    "open",
    "pending",
    "queued",
    "running",
})
_ACTIVE_TASK_STATES = frozenset({
    "cancel_requested",
    "pending",
    "running",
})
_ACTIVE_ROLLOUT_STATES = frozenset({"canary", "enabled", "readers_only"})
_CREDENTIAL_NAMES = frozenset({
    ".credential-vault.json",
    ".credentials",
    "auth.json",
})
_HOME_AUDIT_PREFIXES = (
    ".external_write_receipts.db",
    ".idempotency.db",
    ".runs.db",
    "auto_ship_attempts.jsonl",
    "bid_execution_log.json",
)
_HOME_OPERATIONAL_NAMES = frozenset({
    ".effector_consents.db",
    ".external_write_receipts.db",
    ".idempotency.db",
    ".langgraph_runs.db",
    ".runs.db",
    "checkpoints.db",
    "knowledge.db",
    "story.db",
})
_HOME_OPERATIONAL_DIRECTORIES = frozenset({
    ".git",
    ".credentials",
    ".lance",
    "checkpoints",
    "lance",
})
_HOME_RESETTABLE_CONTENT_SUFFIXES = frozenset({
    ".bat",
    ".bmp",
    ".css",
    ".csv",
    ".docx",
    ".gif",
    ".html",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ps1",
    ".py",
    ".rst",
    ".sh",
    ".svg",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".wav",
    ".webp",
    ".xlsx",
    ".yaml",
    ".yml",
    ".zip",
})
_KNOWN_ROOT_DATABASES = frozenset({
    ".auth.db",
    ".external_write_receipts.db",
    ".idempotency.db",
    ".langgraph_runs.db",
    ".node_eval.db",
    ".project_memory.db",
    ".runs.db",
    DB_FILENAME,
    "checkpoints.db",
    "daemon_brain.db",
    "knowledge.db",
    "story.db",
    "wiki_trigger_attempts.db",
})
_KNOWN_ROOT_NON_DATABASE_FILES = frozenset({
    ".active_universe",
    ".node_registry.json",
    # The live server's in-flight run recovery lock and its pid sidecar.
    ".run_recovery.lock",
    ".run_recovery.lock.pid",
    ".scoped-reset.barrier",
    "ledger.json",
})
_KNOWN_ROOT_RUN_TABLES = frozenset({
    "attribution_credit",
    "attribution_edge",
    "branch_schedules",
    "branch_subscriptions",
    "branch_versions",
    # One-time data-migration markers (name, time, counts): no subject data.
    "branch_versions_migrations",
    "conformance_pack",
    "contribution_events",
    "conversation_run_admissions",
    "gate_event",
    "gate_event_cite",
    "node_edit_audit",
    "outcome_event",
    "run_cancels",
    "run_child_attachments",
    "run_events",
    "run_file_allocations",
    "run_file_bindings",
    "run_file_cleanup",
    "run_file_objects",
    "run_file_operations",
    "run_input_admissions",
    "run_judgments",
    "run_lineage",
    "run_receipts",
    "run_terminal_outbox",
    "runs",
    "scheduler_delivered_events",
    "teammate_messages",
})
_ROOT_OPERATIONAL_BLOCKERS = frozenset({
    ".external_write_receipts.db",
    ".idempotency.db",
    ".langgraph_runs.db",
    ".node_eval.db",
    ".project_memory.db",
    "checkpoints.db",
    "daemon_brain.db",
    "knowledge.db",
    "story.db",
})
_ROOT_OPERATIONAL_DIRECTORIES = frozenset({
    ".lance",
    "checkpoints",
    "knowledge",
    "lance",
    "story",
})

# Exact schema facts for every table whose rows may be deleted.  Table-name
# classification alone is insufficient: a new column, key, or trigger can add
# deletion authority without adding a table.
_RESETTABLE_TABLE_COLUMNS = MappingProxyType({
    "universes": (
        ("universe_id", "TEXT", 0, None, 1),
        ("display_name", "TEXT", 1, None, 0),
        ("host_path", "TEXT", 1, None, 0),
        ("created_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "universe_rules": (
        ("universe_id", "TEXT", 0, None, 1),
        ("public_read", "INTEGER", 1, "1", 0),
        ("public_fork", "INTEGER", 1, "1", 0),
        ("branch_mode", "TEXT", 1, "'no_fixed_mainline'", 0),
        ("quick_vote_seconds", "INTEGER", 1, "300", 0),
        ("updated_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "universe_notes": (
        ("note_id", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("source", "TEXT", 1, None, 0),
        ("text", "TEXT", 1, None, 0),
        ("category", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0),
        ("target", "TEXT", 0, None, 0),
        ("clearly_wrong", "INTEGER", 1, "0", 0),
        ("quoted_passage", "TEXT", 1, "''", 0),
        ("tags_json", "TEXT", 1, "'[]'", 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
        ("timestamp", "REAL", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
    ),
    "universe_work_targets": (
        ("universe_id", "TEXT", 1, None, 1),
        ("target_id", "TEXT", 1, None, 2),
        ("payload_json", "TEXT", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
    ),
    "universe_hard_priorities": (
        ("universe_id", "TEXT", 1, None, 1),
        ("priority_id", "TEXT", 1, None, 2),
        ("payload_json", "TEXT", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
    ),
    "universe_snapshots": (
        ("snapshot_id", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("branch_id", "TEXT", 1, None, 0),
        ("label", "TEXT", 1, None, 0),
        ("artifact_ref", "TEXT", 1, "''", 0),
        ("created_by", "TEXT", 1, None, 0),
        ("created_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "branches": (
        ("branch_id", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("name", "TEXT", 1, None, 0),
        ("parent_branch_id", "TEXT", 0, None, 0),
        ("is_public", "INTEGER", 1, "1", 0),
        ("status", "TEXT", 1, "'active'", 0),
        ("created_by", "TEXT", 1, None, 0),
        ("created_at", "REAL", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "branch_heads": (
        ("branch_id", "TEXT", 0, None, 1),
        ("snapshot_id", "TEXT", 0, None, 0),
        ("updated_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "user_requests": (
        ("request_id", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("branch_id", "TEXT", 0, None, 0),
        ("user_id", "TEXT", 1, None, 0),
        ("request_type", "TEXT", 1, None, 0),
        ("text", "TEXT", 1, None, 0),
        ("preferred_author_id", "TEXT", 0, None, 0),
        ("status", "TEXT", 1, "'open'", 0),
        ("created_at", "REAL", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0),
    ),
    "vote_windows": (
        ("vote_id", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("vote_type", "TEXT", 1, None, 0),
        ("subject_type", "TEXT", 1, None, 0),
        ("subject_id", "TEXT", 1, None, 0),
        ("created_by", "TEXT", 1, None, 0),
        ("opens_at", "REAL", 1, None, 0),
        ("closes_at", "REAL", 1, None, 0),
        ("status", "TEXT", 1, "'open'", 0),
        ("payload_json", "TEXT", 1, "'{}'", 0),
        ("result_json", "TEXT", 1, "'{}'", 0),
    ),
    "vote_ballots": (
        ("vote_id", "TEXT", 1, None, 1),
        ("user_id", "TEXT", 1, None, 2),
        ("choice", "TEXT", 1, None, 0),
        ("comment", "TEXT", 1, "''", 0),
        ("created_at", "REAL", 1, None, 0),
    ),
    "founder_home": (
        ("founder_sub", "TEXT", 0, None, 1),
        ("universe_id", "TEXT", 1, None, 0),
        ("created_at", "REAL", 1, None, 0),
        ("platform_generated", "INTEGER", 1, "0", 0),
    ),
    "universe_acl": (
        ("universe_id", "TEXT", 1, None, 1),
        ("actor_id", "TEXT", 1, None, 2),
        ("permission", "TEXT", 1, None, 0),
        ("granted_at", "REAL", 1, None, 0),
        ("granted_by", "TEXT", 1, "''", 0),
    ),
})
_RESETTABLE_FOREIGN_KEYS = MappingProxyType({
    "universe_rules": (
        ("universe_id", "universes", "universe_id", "NO ACTION", "CASCADE", "NONE"),
    ),
})

_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_reset_leases (
    principal_fingerprint TEXT PRIMARY KEY,
    fence INTEGER NOT NULL CHECK (fence >= 1),
    plan_id TEXT NOT NULL,
    home_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('active', 'released')),
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scoped_reset_operations (
    plan_id TEXT PRIMARY KEY,
    principal_fingerprint TEXT NOT NULL,
    roster_revision TEXT NOT NULL,
    inventory_revision TEXT NOT NULL,
    home_id TEXT,
    fence INTEGER NOT NULL CHECK (fence >= 1),
    state TEXT NOT NULL CHECK (
        state IN (
            'prepared', 'staged', 'committed', 'completed', 'rolled_back'
        )
    ),
    source_path TEXT NOT NULL,
    staging_path TEXT NOT NULL,
    journal_path TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    commit_witness INTEGER NOT NULL DEFAULT 0 CHECK (commit_witness IN (0, 1)),
    receipt_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class ScopedResetError(RuntimeError):
    """Base error for scoped-reset safety failures."""


class ScopedResetSchemaError(ScopedResetError):
    """The current store inventory differs from the reviewed inventory."""


class ScopedResetLeaseBusy(ScopedResetError):
    """A writer or reset operation currently owns the maintenance barrier."""


class ScopedResetBlocked(ScopedResetError):
    """Reviewed scope contains state that must close through its normal lifecycle."""


class ScopedResetPlanChanged(ScopedResetError):
    """Apply no longer matches the exact plan reviewed by the operator."""


class ScopedResetRecoveryError(ScopedResetError):
    """Journal, witness, and filesystem state cannot be reconciled safely."""


@dataclass(frozen=True)
class ScopeInventory:
    """Read-only, content-free inventory for one exact founder binding."""

    principal: str
    home_id: str | None
    home_path: Path | None
    schema_tables: frozenset[str]
    subject_grants: tuple[tuple[str, str], ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class TestIdentityRoster:
    """Restricted operator input; aliases are safe to emit, subjects are not."""

    revision: str
    aliases: Mapping[str, str]
    allowlisted_subjects: AbstractSet[str]

    def __post_init__(self) -> None:
        revision = self.revision.strip()
        if not revision:
            raise ValueError("roster revision must be non-empty")
        normalized: dict[str, str] = {}
        for raw_alias, raw_subject in self.aliases.items():
            alias = str(raw_alias).strip()
            subject = str(raw_subject).strip()
            if not alias or not subject:
                raise ValueError("roster aliases and subjects must be non-empty")
            from tinyassets.principals import has_named_principal

            if not has_named_principal(subject):
                raise ValueError("an unbound subject cannot be a test identity")
            if alias in normalized:
                raise ValueError(f"duplicate test identity alias: {alias}")
            normalized[alias] = subject
        if len(set(normalized.values())) != len(normalized):
            raise ValueError("test identity subjects must be unique")
        allowed = frozenset(
            str(value).strip()
            for value in self.allowlisted_subjects
            if str(value).strip()
        )
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "aliases", MappingProxyType(normalized))
        object.__setattr__(self, "allowlisted_subjects", allowed)


@dataclass
class MaintenanceBarrier:
    """Cross-process shared/exclusive byte-range lock."""

    fd: int
    path: Path
    exclusive: bool
    offset: int
    length: int
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        _unlock_fd(self.fd, offset=self.offset, length=self.length)
        os.close(self.fd)
        self._released = True

    def __enter__(self) -> MaintenanceBarrier:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def _lock_fd(
    fd: int,
    *,
    exclusive: bool,
) -> tuple[int, int] | None:
    if sys.platform == "win32":
        import msvcrt

        if exclusive:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, _WINDOWS_BARRIER_SLOTS)
            except OSError:
                return None
            return 0, _WINDOWS_BARRIER_SLOTS
        for offset in range(_WINDOWS_BARRIER_SLOTS):
            os.lseek(fd, offset, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                continue
            return offset, 1
        return None

    import fcntl

    os.lseek(fd, 0, os.SEEK_SET)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except OSError:
        return None
    return 0, 1


def _unlock_fd(fd: int, *, offset: int, length: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, offset, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, length)
        except OSError:
            pass
        return

    import fcntl

    os.lseek(fd, 0, os.SEEK_SET)
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def acquire_maintenance_barrier(
    data_dir: Path,
    *,
    exclusive: bool,
    timeout: float = 0.0,
) -> MaintenanceBarrier:
    """Acquire the process-shared writer/reset barrier.

    Normal service writers hold a shared lease; scoped apply/recovery requires
    exclusive ownership.  Contention is explicit and never treated as success.
    """

    root = _validated_root(data_dir)
    path = root / ".scoped-reset.barrier"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(
            str(path),
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
        )
    except FileExistsError:
        try:
            fd = os.open(str(path), os.O_RDWR | nofollow)
        except OSError as exc:
            raise ScopedResetBlocked(
                "scoped-reset barrier file cannot be opened safely"
            ) from exc
    try:
        barrier_stat = os.fstat(fd)
        if (
            not stat.S_ISREG(barrier_stat.st_mode)
            or barrier_stat.st_nlink != 1
            or barrier_stat.st_dev != root.stat().st_dev
            or _is_link_or_reparse(path)
        ):
            raise ScopedResetBlocked(
                "scoped-reset barrier file is linked, non-regular, "
                "or outside the data-root filesystem"
            )
        if barrier_stat.st_size < _WINDOWS_BARRIER_SLOTS:
            os.ftruncate(fd, _WINDOWS_BARRIER_SLOTS)
    except Exception:
        os.close(fd)
        raise
    deadline = time.monotonic() + max(0.0, timeout)
    lock_range = _lock_fd(fd, exclusive=exclusive)
    while lock_range is None:
        if time.monotonic() >= deadline:
            os.close(fd)
            mode = "exclusive reset" if exclusive else "shared writer"
            raise ScopedResetLeaseBusy(f"{mode} maintenance barrier is busy")
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        lock_range = _lock_fd(fd, exclusive=exclusive)
    return MaintenanceBarrier(
        fd=fd,
        path=path,
        exclusive=exclusive,
        offset=lock_range[0],
        length=lock_range[1],
    )


def _table_names(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )


def _validate_resettable_schema(conn: sqlite3.Connection) -> None:
    for table, expected in _RESETTABLE_TABLE_COLUMNS.items():
        xinfo = tuple(conn.execute(f'PRAGMA table_xinfo("{table}")'))
        actual = tuple(
            (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
            for row in xinfo
        )
        if actual != expected or any(int(row[6]) != 0 for row in xinfo):
            raise ScopedResetSchemaError(
                f"column manifest changed for resettable table: {table}"
            )
        foreign_keys = tuple(
            (
                str(row[3]),
                str(row[2]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        )
        if foreign_keys != _RESETTABLE_FOREIGN_KEYS.get(table, ()):
            raise ScopedResetSchemaError(
                f"foreign-key manifest changed for resettable table: {table}"
            )
    authority_objects = tuple(
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('trigger', 'view') ORDER BY type, name"
        )
    )
    if authority_objects:
        kind, name = authority_objects[0]
        raise ScopedResetSchemaError(
            f"{kind} authority is not reviewed for scoped reset: {name}"
        )
    approved_foreign_keys = {
        (
            "universe_rules",
            "universe_id",
            "universes",
            "universe_id",
            "NO ACTION",
            "CASCADE",
            "NONE",
        ),
        (
            "request_admissions",
            "request_id",
            "user_requests",
            "request_id",
            "NO ACTION",
            "CASCADE",
            "NONE",
        ),
        (
            "request_admission_events",
            "request_id",
            "user_requests",
            "request_id",
            "NO ACTION",
            "CASCADE",
            "NONE",
        ),
        (
            "branch_tasks_v2",
            "request_id",
            "user_requests",
            "request_id",
            "NO ACTION",
            "CASCADE",
            "NONE",
        ),
    }
    actual_reset_foreign_keys: set[tuple[str, ...]] = set()
    for table in _table_names(conn):
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            if (
                table in _RESETTABLE_TABLE_COLUMNS
                or str(row[2]) in _RESETTABLE_TABLE_COLUMNS
            ):
                actual_reset_foreign_keys.add((
                    table,
                    str(row[3]),
                    str(row[2]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    str(row[7]),
                ))
    if actual_reset_foreign_keys != approved_foreign_keys:
        raise ScopedResetSchemaError(
            "foreign-key deletion authority changed for scoped reset"
        )


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _validated_root(data_dir: Path, *, strict: bool = True) -> Path:
    raw = Path(data_dir).absolute()
    if strict and not raw.is_dir():
        raise ScopedResetBlocked("scoped-reset data root is missing")
    if raw.exists() and _is_link_or_reparse(raw):
        raise ScopedResetBlocked(
            "scoped-reset data root is a link or reparse point"
        )
    root = raw.resolve(strict=strict)
    if root != raw:
        raise ScopedResetBlocked(
            "scoped-reset data root traverses a link or reparse point"
        )
    return root


def _validated_main_database(root: Path) -> Path:
    db_file = root / DB_FILENAME
    if not db_file.is_file():
        return db_file
    if _is_link_or_reparse(db_file):
        raise ScopedResetBlocked(
            "main database is a link or reparse point"
        )
    resolved = db_file.resolve(strict=True)
    database_stat = resolved.stat()
    if (
        resolved.parent != root
        or database_stat.st_dev != root.stat().st_dev
        or database_stat.st_nlink != 1
    ):
        raise ScopedResetBlocked(
            "main database escapes the root or has multiple filesystem links"
        )
    for suffix in ("-journal", "-shm", "-wal"):
        sidecar = Path(f"{db_file}{suffix}")
        if sidecar.exists():
            sidecar_stat = sidecar.stat()
            if (
                _is_link_or_reparse(sidecar)
                or sidecar_stat.st_nlink != 1
                or sidecar_stat.st_dev != root.stat().st_dev
            ):
                raise ScopedResetBlocked(
                    "main database sidecar is linked or crosses filesystems: "
                    f"{sidecar.name}"
                )
    return db_file


def _connect_read_only(db_file: Path) -> sqlite3.Connection:
    """Open a stable SQLite snapshot without creating WAL/SHM sidecars."""

    journal = Path(f"{db_file}-journal")
    if journal.is_file() and journal.stat().st_size:
        raise ScopedResetBlocked(
            f"database has a hot SQLite sidecar: {journal.name}"
        )
    uri = db_file.resolve(strict=True).as_uri()
    wal = Path(f"{db_file}-wal")
    if wal.is_file() and wal.stat().st_size:
        shm = Path(f"{db_file}-shm")
        if not shm.is_file():
            raise ScopedResetBlocked(
                f"database WAL has no readable shared-memory index: {wal.name}"
            )
        return sqlite3.connect(f"{uri}?mode=ro", uri=True)
    return sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _home_filesystem_identity(home: Path) -> dict[str, object]:
    """Bind a reviewed home to its directory object and exact entry tree."""

    if _is_link_or_reparse(home):
        raise ScopedResetBlocked("founder-home path is a link or reparse point")
    root_stat = home.stat()
    entries: list[dict[str, object]] = []
    pending = [home]
    while pending:
        current = pending.pop()
        for entry in sorted(os.scandir(current), key=lambda item: item.name):
            path = Path(entry.path)
            relative = path.relative_to(home).as_posix()
            if entry.is_symlink() or _is_link_or_reparse(path):
                raise ScopedResetBlocked(
                    f"home contains link or reparse point: {relative}"
                )
            entry_stat = path.stat(follow_symlinks=False)
            if entry_stat.st_dev != root_stat.st_dev:
                raise ScopedResetBlocked(
                    f"home crosses a nested mount boundary: {relative}"
                )
            common = {
                "path": relative,
                "device": int(entry_stat.st_dev),
                "inode": int(entry_stat.st_ino),
            }
            if entry.is_dir(follow_symlinks=False):
                entries.append({**common, "kind": "directory"})
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                entries.append({
                    **common,
                    "kind": "file",
                    "bytes": int(entry_stat.st_size),
                    "content_sha256": _sha256_file(path),
                })
            else:
                raise ScopedResetBlocked(
                    f"home contains unsupported filesystem entry: {relative}"
                )
    entry_digest = hashlib.sha256(
        json.dumps(
            entries,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "device": int(root_stat.st_dev),
        "inode": int(root_stat.st_ino),
        "entry_digest": f"sha256:{entry_digest}",
    }


def _assert_home_filesystem_identity(
    home: Path,
    planned_identity: object,
) -> None:
    if not isinstance(planned_identity, dict):
        raise ScopedResetPlanChanged(
            "reviewed home filesystem identity is missing"
        )
    if _home_filesystem_identity(home) != planned_identity:
        raise ScopedResetPlanChanged(
            "candidate home filesystem identity changed after review"
        )


_AGENT_WORKSPACE_DIR = ".agent-workspace"


def _walk_home_without_following(home: Path) -> tuple[str, ...]:
    blockers: list[str] = []
    pending = [home]
    home_device = home.stat().st_dev
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            blockers.append(f"home path cannot be inspected: {exc.__class__.__name__}")
            continue
        for entry in entries:
            path = Path(entry.path)
            normalized_name = entry.name.casefold()
            if entry.is_symlink() or _is_link_or_reparse(path):
                blockers.append(
                    f"home contains link or reparse point: {path.relative_to(home)}"
                )
                continue
            if path.stat().st_dev != home_device:
                blockers.append(
                    f"home crosses a nested mount boundary: {path.relative_to(home)}"
                )
                continue
            if normalized_name in _CREDENTIAL_NAMES:
                blockers.append(
                    f"home contains credential artifact: {path.relative_to(home)}"
                )
                continue
            if normalized_name.startswith(_HOME_AUDIT_PREFIXES):
                blockers.append(
                    f"home-local audit or receipt store requires archival: "
                    f"{path.relative_to(home)}"
                )
                continue
            if (current == home and entry.name == _AGENT_WORKSPACE_DIR
                    and entry.is_dir(follow_symlinks=False)):
                # The agent's own workspace (harness W2) is owner content of any
                # shape; it goes with the home (stage_then_remove_home, which
                # never follows a link), so it is not classified file by file.
                continue
            if entry.is_dir(follow_symlinks=False):
                if normalized_name in _HOME_OPERATIONAL_DIRECTORIES:
                    blockers.append(
                        "home operational directory has no scoped-reset "
                        f"adapter: {path.relative_to(home)}"
                    )
                    continue
                if entry.name.startswith("."):
                    blockers.append(
                        "unclassified home operational directory: "
                        f"{path.relative_to(home)}"
                    )
                    continue
                pending.append(path)
            else:
                base_name = normalized_name
                for suffix in ("-journal", "-shm", "-wal"):
                    if base_name.endswith(suffix):
                        base_name = base_name.removesuffix(suffix)
                        break
                if (
                    base_name in _HOME_OPERATIONAL_NAMES
                    or base_name.endswith(".db")
                ):
                    blockers.append(
                        "home operational store has no scoped-reset adapter: "
                        f"{path.relative_to(home)}"
                    )
                    continue
                if path.suffix.casefold() not in _HOME_RESETTABLE_CONTENT_SUFFIXES:
                    blockers.append(
                        "unclassified home operational store: "
                        f"{path.relative_to(home)}"
                    )
    return tuple(blockers)


def _reset_state_digest(
    database_actions: object,
    filesystem_actions: object,
    root_history_actions: object = (),
) -> str:
    state_payload = {
        "database_actions": database_actions,
        "filesystem_actions": filesystem_actions,
    }
    if root_history_actions:
        state_payload["root_history_actions"] = root_history_actions
    return "sha256:" + hashlib.sha256(
        json.dumps(
            state_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _matching_count(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...],
) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _inspect_database(
    conn: sqlite3.Connection,
    *,
    principal: str,
    home_id: str | None,
) -> list[str]:
    if home_id is None:
        return []
    blockers: list[str] = []
    foreign_bindings = _matching_count(
        conn,
        "SELECT COUNT(*) FROM founder_home "
        "WHERE universe_id = ? AND founder_sub <> ?",
        (home_id, principal),
    )
    from tinyassets.storage.agent_turn_journal import reset_blockers

    blockers.extend(reset_blockers(conn, principal, home_id))
    if foreign_bindings:
        blockers.append(f"foreign binding references exact home ({foreign_bindings})")

    foreign_grants = _matching_count(
        conn,
        "SELECT COUNT(*) FROM universe_acl "
        "WHERE universe_id = ? AND actor_id <> ?",
        (home_id, principal),
    )
    if foreign_grants:
        blockers.append(f"foreign grant references exact home ({foreign_grants})")

    foreign_actor_checks = (
        (
            "user_requests",
            "SELECT COUNT(*) FROM user_requests "
            "WHERE universe_id = ? AND user_id <> ?",
            (home_id, principal),
        ),
        (
            "branches",
            "SELECT COUNT(*) FROM branches "
            "WHERE universe_id = ? AND created_by NOT IN (?, 'system')",
            (home_id, principal),
        ),
        (
            "universe_snapshots",
            "SELECT COUNT(*) FROM universe_snapshots "
            "WHERE universe_id = ? AND created_by NOT IN (?, 'system')",
            (home_id, principal),
        ),
        (
            "vote_windows",
            "SELECT COUNT(*) FROM vote_windows "
            "WHERE universe_id = ? AND created_by NOT IN (?, 'system')",
            (home_id, principal),
        ),
        (
            "vote_ballots",
            "SELECT COUNT(*) FROM vote_ballots AS ballot "
            "JOIN vote_windows AS vote ON vote.vote_id = ballot.vote_id "
            "WHERE vote.universe_id = ? AND ballot.user_id <> ?",
            (home_id, principal),
        ),
    )
    for table, sql, params in foreign_actor_checks:
        if _matching_count(conn, sql, params):
            blockers.append(
                f"foreign actor owns terminal reset state in {table}"
            )

    preserved_request_checks = (
        (
            "request_admissions",
            "SELECT COUNT(*) FROM request_admissions AS preserved "
            "JOIN user_requests AS request "
            "ON request.request_id = preserved.request_id "
            "WHERE request.universe_id = ?",
        ),
        (
            "request_admission_events",
            "SELECT COUNT(*) FROM request_admission_events AS preserved "
            "JOIN user_requests AS request "
            "ON request.request_id = preserved.request_id "
            "WHERE request.universe_id = ?",
        ),
        (
            "branch_tasks_v2",
            "SELECT COUNT(*) FROM branch_tasks_v2 AS preserved "
            "JOIN user_requests AS request "
            "ON request.request_id = preserved.request_id "
            "WHERE request.universe_id = ?",
        ),
    )
    for table, sql in preserved_request_checks:
        if _matching_count(conn, sql, (home_id,)):
            blockers.append(
                f"preserved {table} rows depend on candidate user_requests"
            )

    active_checks: tuple[
        tuple[str, str, tuple[object, ...]],
        ...,
    ] = (
        (
            "daemon",
            "SELECT COUNT(*) FROM author_runtime_instances "
            f"WHERE universe_id = ? AND lower(status) IN "
            f"({','.join('?' for _ in _ACTIVE_DAEMON_STATES)})",
            (home_id, *sorted(_ACTIVE_DAEMON_STATES)),
        ),
        (
            "request",
            "SELECT COUNT(*) FROM user_requests "
            f"WHERE universe_id = ? AND lower(status) IN "
            f"({','.join('?' for _ in _ACTIVE_REQUEST_STATES)})",
            (home_id, *sorted(_ACTIVE_REQUEST_STATES)),
        ),
        (
            "vote",
            "SELECT COUNT(*) FROM vote_windows "
            "WHERE universe_id = ? AND lower(status) = 'open'",
            (home_id,),
        ),
        (
            "epoch2",
            "SELECT COUNT(*) FROM branch_tasks_v2 "
            f"WHERE universe_id = ? AND lower(status) IN "
            f"({','.join('?' for _ in _ACTIVE_TASK_STATES)})",
            (home_id, *sorted(_ACTIVE_TASK_STATES)),
        ),
        (
            "epoch2 quarantine",
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine "
            "WHERE universe_id = ?",
            (home_id,),
        ),
        (
            "epoch2 rollout",
            "SELECT COUNT(*) FROM request_admission_rollouts "
            f"WHERE universe_id = ? AND lower(state) IN "
            f"({','.join('?' for _ in _ACTIVE_ROLLOUT_STATES)})",
            (home_id, *sorted(_ACTIVE_ROLLOUT_STATES)),
        ),
    )
    tables = _table_names(conn)
    for label, sql, params in active_checks:
        table = sql.split("FROM ", 1)[1].split(" ", 1)[0]
        if table in tables and _matching_count(conn, sql, params):
            blockers.append(f"active {label} state references exact home")

    market_checks: tuple[tuple[str, str, tuple[object, ...]], ...] = (
        (
            "escrow_locks",
            "SELECT COUNT(*) FROM escrow_locks "
            "WHERE lower(status) = 'locked' "
            "AND (staker_id = ? OR recipient_id = ?)",
            (principal, principal),
        ),
        (
            "staker_escrow_budget",
            "SELECT COUNT(*) FROM staker_escrow_budget "
            "WHERE staker_id = ? AND reserved_amount > 0",
            (principal,),
        ),
        (
            "escrow_balance",
            "SELECT COUNT(*) FROM escrow_balance "
            "WHERE staker_id = ? AND lower(status) IN ('locked', 'partial')",
            (principal,),
        ),
        (
            "pending_settlement",
            "SELECT COUNT(*) FROM pending_settlement "
            "WHERE recipient_id = ? AND lower(status) IN ('pending', 'batched')",
            (principal,),
        ),
        (
            "settlement_batch",
            "SELECT COUNT(*) FROM settlement_batch "
            "WHERE recipient_id = ? "
            "AND lower(status) IN ('open', 'submitted', 'in_doubt')",
            (principal,),
        ),
        (
            "royalty_payout",
            "SELECT COUNT(*) FROM royalty_payout "
            "WHERE designer_id = ? AND lower(status) = 'pending'",
            (principal,),
        ),
    )
    for table, sql, params in market_checks:
        if table in tables and _matching_count(conn, sql, params):
            blockers.append(f"active market obligation references test principal: {table}")
    return blockers


def _inspect_root_runs(
    root: Path,
    *,
    principal: str,
) -> tuple[str, ...]:
    runs_path = root / ".runs.db"
    if not runs_path.is_file():
        return ()
    if _is_link_or_reparse(runs_path) or runs_path.stat().st_nlink != 1:
        return ("root run history is linked and cannot be classified",)
    try:
        conn = _connect_read_only(runs_path)
        tables = _table_names(conn)
        blockers: list[str] = []
        unknown_tables = sorted(tables - _KNOWN_ROOT_RUN_TABLES)
        if unknown_tables:
            blockers.append(
                "unclassified root run-history table: "
                + ", ".join(unknown_tables)
            )
        if "runs" in tables and _matching_count(
            conn,
            "SELECT COUNT(*) FROM runs "
            "WHERE lower(status) IN ('queued', 'running') "
            "AND (actor = ? OR owner_user_id = ?)",
            (principal, principal),
        ):
            blockers.append("active root run references test principal")
        if "branch_schedules" in tables and _matching_count(
            conn,
            "SELECT COUNT(*) FROM branch_schedules "
            "WHERE owner_actor = ? AND active = 1 AND paused = 0",
            (principal,),
        ):
            blockers.append("active schedule references test principal")
        if "branch_subscriptions" in tables and _matching_count(
            conn,
            "SELECT COUNT(*) FROM branch_subscriptions "
            "WHERE owner_actor = ? AND active = 1",
            (principal,),
        ):
            blockers.append("active subscription references test principal")
        return tuple(blockers)
    except sqlite3.DatabaseError as exc:
        return (f"root run history cannot be classified: {exc.__class__.__name__}",)
    finally:
        if "conn" in locals():
            conn.close()


def _inspect_root_operational_files(root: Path) -> tuple[str, ...]:
    blockers: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir() and entry.name in _ROOT_OPERATIONAL_DIRECTORIES:
            blockers.append(
                "root operational directory has no scoped-reset adapter: "
                f"{entry.name}"
            )
            continue
        if not entry.is_file():
            continue
        name = entry.name
        base_name = name
        for suffix in ("-journal", "-shm", "-wal"):
            if base_name.endswith(suffix):
                base_name = base_name.removesuffix(suffix)
                break
        if base_name in _ROOT_OPERATIONAL_BLOCKERS:
            blockers.append(
                f"root operational store has no scoped-reset adapter: {name}"
            )
            continue
        if (
            name in _KNOWN_ROOT_NON_DATABASE_FILES
            or base_name in _KNOWN_ROOT_DATABASES
        ):
            continue
        blockers.append(f"unclassified root operational store: {name}")
    return tuple(blockers)


def inspect_reset_scope(data_dir: Path, *, principal: str) -> ScopeInventory:
    """Freeze the read-only reviewed scope for one exact founder binding."""

    subject = principal.strip()
    if not subject:
        raise ValueError("principal must be non-empty")
    root = _validated_root(data_dir)
    db_file = _validated_main_database(root)
    if not db_file.is_file():
        return ScopeInventory(
            principal=subject,
            home_id=None,
            home_path=None,
            schema_tables=frozenset(),
            subject_grants=(),
            blockers=(),
        )

    conn = _connect_read_only(db_file)
    try:
        conn.row_factory = sqlite3.Row
        tables = _table_names(conn)
        unknown = sorted(tables - MAIN_DB_TABLE_CLASSIFICATIONS.keys())
        if unknown:
            raise ScopedResetSchemaError(
                "unclassified tables block scoped reset: " + ", ".join(unknown)
            )
        _validate_resettable_schema(conn)
        row = conn.execute(
            "SELECT universe_id FROM founder_home WHERE founder_sub = ?",
            (subject,),
        ).fetchone()
        home_id = str(row[0]) if row and row[0] else None
        grants = tuple(
            (str(grant[0]), str(grant[1]))
            for grant in conn.execute(
                "SELECT universe_id, permission FROM universe_acl "
                "WHERE actor_id = ? ORDER BY universe_id",
                (subject,),
            )
        )
        blockers = _inspect_database(
            conn,
            principal=subject,
            home_id=home_id,
        )
        if home_id is not None:
            registered = conn.execute(
                "SELECT host_path FROM universes WHERE universe_id = ?",
                (home_id,),
            ).fetchone()
            home_path = root / home_id
            expected = home_path.resolve(strict=False)
            if expected.parent != root:
                blockers.append("founder-home path escapes the data root")
            if registered is None:
                blockers.append("founder-home binding has no command center row")
            else:
                registered_path = Path(str(registered[0])).resolve(strict=False)
                if registered_path != expected:
                    blockers.append("founder-home path disagrees with command center index")
            if not home_path.is_dir():
                blockers.append("founder-home directory is missing")
            elif _is_link_or_reparse(home_path):
                blockers.append("founder-home path is a link or reparse point")
            elif home_path.stat().st_dev != root.stat().st_dev:
                blockers.append("founder-home path crosses a mount boundary")
            else:
                blockers.extend(_walk_home_without_following(home_path))
        else:
            home_path = None
    finally:
        conn.close()

    marker = root / ".active_universe"
    if home_id is not None and marker.is_file():
        if marker.read_text(encoding="utf-8").strip() == home_id:
            blockers.append("active command center marker targets exact home")
    offer = root / "founder_offers" / f"{subject}.json"
    if offer.exists():
        blockers.append("enabled founder market offer must be disabled normally")
    blockers.extend(_inspect_root_runs(root, principal=subject))
    if home_id is not None:
        blockers.extend(_inspect_root_operational_files(root))

    return ScopeInventory(
        principal=subject,
        home_id=home_id,
        home_path=home_path,
        schema_tables=tables,
        subject_grants=grants,
        blockers=tuple(sorted(set(blockers))),
    )


def _principal_digest(principal: str) -> str:
    payload = f"tinyassets-scoped-reset-principal-v1\0{principal}".encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return {
            "blob_sha256": hashlib.sha256(value).hexdigest(),
            "bytes": len(value),
        }
    return value


def _row_digest(row: sqlite3.Row) -> str:
    payload = {
        key: _json_value(row[key])
        for key in row.keys()
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _primary_key_columns(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[str, ...]:
    columns = [
        (int(row[5]), str(row[1]))
        for row in conn.execute(f'PRAGMA table_info("{table}")')
        if int(row[5]) > 0
    ]
    return tuple(name for _, name in sorted(columns))


def _row_action(
    conn: sqlite3.Connection,
    *,
    table: str,
    row: sqlite3.Row,
    alias: str,
) -> dict[str, object]:
    keys = _primary_key_columns(conn, table)
    if not keys:
        raise ScopedResetSchemaError(
            f"resettable table {table!r} has no primary key"
        )
    safe_keys: dict[str, object] = {}
    for key in keys:
        if table == "founder_home" and key == "founder_sub":
            safe_keys["identity_alias"] = alias
        elif table == "universe_acl" and key == "actor_id":
            safe_keys["identity_alias"] = alias
        else:
            safe_keys[key] = _json_value(row[key])
    return {
        "table": table,
        "action": "delete_exact_row",
        "keys": safe_keys,
        "row_digest": _row_digest(row),
    }


def _select_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    where: str,
    params: tuple[object, ...],
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            f'SELECT * FROM "{table}" WHERE {where} ORDER BY rowid',
            params,
        )
    )


def _database_actions(
    conn: sqlite3.Connection,
    *,
    principal: str,
    alias: str,
    home_id: str | None,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if home_id is not None:
        founder_rows = _select_rows(
            conn,
            table="founder_home",
            where="founder_sub = ? AND universe_id = ?",
            params=(principal, home_id),
        )
        if any(
            str(row["founder_sub"]) != principal
            or str(row["universe_id"]) != home_id
            for row in founder_rows
        ):
            raise ScopedResetPlanChanged(
                "founder-home selection escaped the exact reviewed identity"
            )
        for row in founder_rows:
            actions.append(
                _row_action(
                    conn,
                    table="founder_home",
                    row=row,
                    alias=alias,
                )
            )
    for row in _select_rows(
        conn,
        table="universe_acl",
        where="actor_id = ?",
        params=(principal,),
    ):
        actions.append(
            _row_action(conn, table="universe_acl", row=row, alias=alias)
        )
    for row in _select_rows(
        conn,
        table="deleted_principals",
        where="founder_sub = ?",
        params=(principal,),
    ):
        actions.append(
            _row_action(conn, table="deleted_principals", row=row, alias=alias)
        )
    if home_id is None:
        return actions

    home_tables = (
        "universe_hard_priorities",
        "universe_notes",
        "universe_rules",
        "universe_snapshots",
        "universe_work_targets",
        "user_requests",
        "universes",
    )
    for table in home_tables:
        for row in _select_rows(
            conn,
            table=table,
            where="universe_id = ?",
            params=(home_id,),
        ):
            actions.append(
                _row_action(conn, table=table, row=row, alias=alias)
            )

    branch_rows = _select_rows(
        conn,
        table="branches",
        where="universe_id = ?",
        params=(home_id,),
    )
    branch_ids = [str(row["branch_id"]) for row in branch_rows]
    for branch_id in branch_ids:
        for row in _select_rows(
            conn,
            table="branch_heads",
            where="branch_id = ?",
            params=(branch_id,),
        ):
            actions.append(
                _row_action(conn, table="branch_heads", row=row, alias=alias)
            )
    for row in branch_rows:
        actions.append(_row_action(conn, table="branches", row=row, alias=alias))

    vote_rows = _select_rows(
        conn,
        table="vote_windows",
        where="universe_id = ?",
        params=(home_id,),
    )
    for vote in vote_rows:
        for row in _select_rows(
            conn,
            table="vote_ballots",
            where="vote_id = ?",
            params=(vote["vote_id"],),
        ):
            actions.append(
                _row_action(conn, table="vote_ballots", row=row, alias=alias)
            )
    for row in vote_rows:
        actions.append(
            _row_action(conn, table="vote_windows", row=row, alias=alias)
        )
    return sorted(
        actions,
        key=lambda action: (
            str(action["table"]),
            json.dumps(action["keys"], sort_keys=True),
        ),
    )


def plan_test_identity_reset(
    data_dir: Path,
    *,
    alias: str,
    roster: TestIdentityRoster,
) -> dict[str, object]:
    """Return a stable, content-free, read-only plan for one allowlisted alias."""

    identity_alias = alias.strip()
    principal = roster.aliases.get(identity_alias)
    if principal is None:
        raise PermissionError(f"unknown test identity alias: {identity_alias!r}")
    if principal not in roster.allowlisted_subjects:
        raise PermissionError(
            f"subject for alias {identity_alias!r} is not allowlisted"
        )

    scope = inspect_reset_scope(data_dir, principal=principal)
    root = _validated_root(data_dir)
    db_file = _validated_main_database(root)
    actions: list[dict[str, object]] = []
    if db_file.is_file():
        conn = _connect_read_only(db_file)
        try:
            conn.row_factory = sqlite3.Row
            actions = _database_actions(
                conn,
                principal=principal,
                alias=identity_alias,
                home_id=scope.home_id,
            )
        finally:
            conn.close()

    filesystem_actions: list[dict[str, object]] = []
    if scope.home_path is not None:
        filesystem_actions.append({
            "action": "stage_then_remove_home",
            "path": str(scope.home_path.resolve(strict=False)),
            "owner_principal_fingerprint": _principal_digest(principal),
            "home_filesystem_identity": (
                _home_filesystem_identity(scope.home_path)
                if not scope.blockers
                else None
            ),
        })

    from tinyassets.storage.conversation_reset import plan_action

    root_history_actions = plan_action(
        root, fingerprint=_principal_digest(principal), home_id=scope.home_id,
    )
    state_digest = _reset_state_digest(actions, filesystem_actions, root_history_actions)
    plan_inputs = {
        "inventory_revision": INVENTORY_REVISION,
        "roster_revision": roster.revision,
        "principal_fingerprint": _principal_digest(principal),
        "home_id": scope.home_id,
        "resolved_data_dir": str(root),
        "state_digest": state_digest,
        "blockers": list(scope.blockers),
    }
    plan_id = "sha256:" + hashlib.sha256(
        json.dumps(
            plan_inputs,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "plan_id": plan_id,
        **plan_inputs,
        "identity_alias": identity_alias,
        "database_actions": actions,
        "filesystem_actions": filesystem_actions,
        "root_history_actions": root_history_actions,
        "preserved": [
            "all other founder homes and command center content",
            "commons, wiki, root run history, audit, market, and billing state",
            "global daemon identities and all credentials",
        ],
        "noop": not actions and not filesystem_actions,
    }


def _windows_roster_acl_is_private(path: Path) -> bool:
    """Return whether every readable ACE is limited to the operator/system."""

    acl_environment = os.environ.copy()
    acl_environment["TINYASSETS_ROSTER_ACL_PATH"] = str(path)
    owner_result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-Acl -LiteralPath "
            "$env:TINYASSETS_ROSTER_ACL_PATH).Owner",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=acl_environment,
    )
    operator = getpass.getuser().casefold()
    if owner_result.returncode != 0:
        raise PermissionError(
            "test identity roster Windows owner could not be inspected"
        )
    owner_account = owner_result.stdout.strip().casefold().rsplit("\\", 1)[-1]
    if owner_account not in {operator, "administrators"}:
        return False
    result = subprocess.run(
        ["icacls", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise PermissionError(
            "test identity roster Windows ACL could not be inspected"
        )
    allowed_accounts = {operator, "system", "administrators"}
    saw_operator = False
    for line in result.stdout.splitlines():
        if ":" not in line or "(" not in line:
            continue
        acl_prefix = line.rsplit(":", 1)[0].strip()
        identity = acl_prefix.rsplit(maxsplit=1)[-1].casefold()
        account = identity.rsplit("\\", 1)[-1]
        if account == operator:
            saw_operator = True
            continue
        if account in allowed_accounts:
            continue
        return False
    return saw_operator


def load_test_identity_roster(path: Path) -> TestIdentityRoster:
    """Load the credential-free operator roster from an explicit local file."""

    raw_roster_path = Path(path).absolute()
    if _is_link_or_reparse(raw_roster_path):
        raise PermissionError(
            "test identity roster must not be a link or reparse point"
        )
    roster_path = raw_roster_path.resolve(strict=True)
    if not roster_path.is_file():
        raise ValueError("test identity roster must be a regular file")
    if sys.platform == "win32":
        if not _windows_roster_acl_is_private(roster_path):
            raise PermissionError(
                "test identity roster Windows ACL permits another principal"
            )
    else:
        permissions = stat.S_IMODE(roster_path.stat().st_mode)
        if permissions & 0o077:
            raise PermissionError(
                "test identity roster must not be group/world accessible"
            )
    payload = json.loads(roster_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("test identity roster must be a JSON object")
    allowed_fields = {"aliases", "allowlisted_subjects", "revision"}
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise ValueError(
            "unexpected roster fields: " + ", ".join(unexpected)
        )
    aliases = payload.get("aliases")
    allowlisted = payload.get("allowlisted_subjects")
    if not isinstance(aliases, dict):
        raise ValueError("roster aliases must be a JSON object")
    if not isinstance(allowlisted, list):
        raise ValueError("roster allowlisted_subjects must be a JSON array")
    return TestIdentityRoster(
        revision=str(payload.get("revision") or ""),
        aliases={
            str(alias): str(subject)
            for alias, subject in aliases.items()
        },
        allowlisted_subjects=frozenset(str(value) for value in allowlisted),
    )


def read_completed_plan_receipt(
    data_dir: Path,
    *,
    plan_id: str,
) -> dict[str, object] | None:
    """Return the immutable receipt for a witnessed completed plan, if any."""

    root = _validated_root(data_dir)
    db_file = _validated_main_database(root)
    if not db_file.is_file():
        return None
    conn = _connect_read_only(db_file)
    try:
        if "scoped_reset_operations" not in _table_names(conn):
            return None
        row = conn.execute(
            "SELECT receipt_json FROM scoped_reset_operations "
            "WHERE plan_id = ? AND state = 'completed' AND commit_witness = 1",
            (plan_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    receipt = json.loads(str(row[0]))
    if not isinstance(receipt, dict):
        raise ScopedResetSchemaError(
            "completed scoped-reset receipt must be a JSON object"
        )
    return {str(key): value for key, value in receipt.items()}


def _connect_control(data_dir: Path) -> sqlite3.Connection:
    root = _validated_root(data_dir)
    db_file = _validated_main_database(root)
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_control_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CONTROL_SCHEMA)
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(scoped_reset_operations)")
    }
    if "plan_json" not in columns:
        conn.execute(
            "ALTER TABLE scoped_reset_operations "
            "ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}'"
        )
        conn.commit()


def _resolve_roster_principal(
    *,
    alias: str,
    roster: TestIdentityRoster,
) -> tuple[str, str]:
    identity_alias = alias.strip()
    principal = roster.aliases.get(identity_alias)
    if principal is None:
        raise PermissionError(f"unknown test identity alias: {identity_alias!r}")
    if principal not in roster.allowlisted_subjects:
        raise PermissionError(
            f"subject for alias {identity_alias!r} is not allowlisted"
        )
    return identity_alias, principal


def _completed_receipt_for_principal(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    principal_fingerprint: str,
) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT receipt_json FROM scoped_reset_operations "
        "WHERE plan_id = ? AND principal_fingerprint = ? "
        "AND state = 'completed' AND commit_witness = 1",
        (plan_id, principal_fingerprint),
    ).fetchone()
    if row is None:
        return None
    receipt = json.loads(str(row[0]))
    if not isinstance(receipt, dict):
        raise ScopedResetRecoveryError("completed receipt is not a JSON object")
    return {str(key): value for key, value in receipt.items()}


def _safe_operation_paths(
    root: Path,
    *,
    plan_id: str,
    home_path: Path | None,
) -> tuple[Path | None, Path | None, Path]:
    if (
        not plan_id.startswith("sha256:")
        or len(plan_id) != 71
        or any(character not in "0123456789abcdef" for character in plan_id[7:])
    ):
        raise ScopedResetBlocked("scoped-reset plan id is not canonical")
    operation_id = plan_id.removeprefix("sha256:")
    journal_root = root / ".scoped-reset-journal"
    staging_root = root / ".scoped-reset-staging" / operation_id
    for path in (journal_root, staging_root.parent):
        if path.exists() and _is_link_or_reparse(path):
            raise ScopedResetBlocked(
                f"scoped-reset operational path is a link or reparse point: {path}"
            )
        path.mkdir(parents=True, exist_ok=True)
        if path.stat().st_dev != root.stat().st_dev:
            raise ScopedResetBlocked(
                "scoped-reset operational path crosses a mount boundary: "
                f"{path}"
            )
    journal_path = journal_root / f"{operation_id}.json"
    if home_path is None:
        return None, None, journal_path
    source = home_path.resolve(strict=True)
    if (
        source.parent != root
        or source != root / source.name
        or _is_link_or_reparse(source)
    ):
        raise ScopedResetBlocked("candidate home is outside the approved data root")
    staging_root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(staging_root):
        raise ScopedResetBlocked("reset staging path is a link or reparse point")
    if source.stat().st_dev != staging_root.stat().st_dev:
        raise ScopedResetBlocked("candidate home and staging are not on one filesystem")
    staging = staging_root / "home"
    if staging.exists():
        raise ScopedResetRecoveryError(
            f"reset staging already exists for reviewed plan: {plan_id}"
        )
    return source, staging, journal_path


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _flush_windows_directory(path: Path) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateFileW(
        str(path),
        0x40000000,  # GENERIC_WRITE is required to flush a directory handle.
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise OSError(
            ctypes.get_last_error(),
            f"cannot open directory for durable flush: {path}",
        )
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(
                ctypes.get_last_error(),
                f"cannot durably flush directory: {path}",
            )
    finally:
        kernel32.CloseHandle(handle)


def _fsync_directory(path: Path) -> None:
    if sys.platform == "win32":
        _flush_windows_directory(path)
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_journal(
    path: Path,
    *,
    plan_id: str,
    home_id: str | None,
    fence: int,
    principal_fingerprint: str,
    source_path: Path | None,
    staging_path: Path | None,
    home_filesystem_identity: object,
) -> None:
    payload = {
        "version": 1,
        "plan_id": plan_id,
        "inventory_revision": INVENTORY_REVISION,
        "home_id": home_id,
        "fence": fence,
        "principal_fingerprint": principal_fingerprint,
        "source_path": str(source_path) if source_path else "",
        "staging_path": str(staging_path) if staging_path else "",
        "home_filesystem_identity": home_filesystem_identity,
    }
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_file(path)
    _fsync_directory(path.parent)


def _expected_operation_evidence(
    root: Path,
    *,
    plan_id: str,
    home_id: str | None,
) -> tuple[Path | None, Path | None, Path]:
    if (
        not plan_id.startswith("sha256:")
        or len(plan_id) != 71
        or any(character not in "0123456789abcdef" for character in plan_id[7:])
    ):
        raise ScopedResetRecoveryError("incomplete reset has invalid plan id")
    operation_id = plan_id[7:]
    journal = root / ".scoped-reset-journal" / f"{operation_id}.json"
    if home_id is None:
        return None, None, journal
    if (
        not home_id
        or Path(home_id).name != home_id
        or "/" in home_id
        or "\\" in home_id
    ):
        raise ScopedResetRecoveryError("incomplete reset has invalid home id")
    return (
        root / home_id,
        root / ".scoped-reset-staging" / operation_id / "home",
        journal,
    )


def _validated_journal(
    path: Path,
    *,
    plan_id: str,
    home_id: str | None,
    fence: int,
    principal_fingerprint: str,
    source: Path | None,
    staging: Path | None,
    home_filesystem_identity: object,
) -> dict[str, object]:
    if _is_link_or_reparse(path) or not path.is_file():
        raise ScopedResetRecoveryError(
            f"incomplete reset is missing a safe journal: {plan_id}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopedResetRecoveryError(
            f"incomplete reset journal is invalid: {plan_id}"
        ) from exc
    if (staging is None) != (home_filesystem_identity is None) or (
        staging is not None and not isinstance(home_filesystem_identity, dict)
    ):
        raise ScopedResetRecoveryError(
            f"incomplete reset journal filesystem identity is invalid: {plan_id}"
        )
    expected = {
        "version": 1,
        "plan_id": plan_id,
        "inventory_revision": INVENTORY_REVISION,
        "home_id": home_id,
        "fence": fence,
        "principal_fingerprint": principal_fingerprint,
        "source_path": str(source) if source else "",
        "staging_path": str(staging) if staging else "",
        "home_filesystem_identity": home_filesystem_identity,
    }
    if payload != expected:
        raise ScopedResetRecoveryError(
            f"incomplete reset journal evidence disagrees with paths: {plan_id}"
        )
    return payload


def _remove_journal(path: Path) -> None:
    if path.is_file():
        path.unlink()
        _fsync_directory(path.parent)


def _acquire_durable_fence(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    principal_fingerprint: str,
    home_id: str | None,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT fence FROM scoped_reset_leases "
            "WHERE principal_fingerprint = ?",
            (principal_fingerprint,),
        ).fetchone()
        fence = (int(row[0]) if row else 0) + 1
        conn.execute(
            """
            INSERT INTO scoped_reset_leases (
                principal_fingerprint, fence, plan_id, home_id, state, updated_at
            ) VALUES (?, ?, ?, ?, 'active', ?)
            ON CONFLICT(principal_fingerprint) DO UPDATE SET
                fence = excluded.fence,
                plan_id = excluded.plan_id,
                home_id = excluded.home_id,
                state = 'active',
                updated_at = excluded.updated_at
            """,
            (
                principal_fingerprint,
                fence,
                plan_id,
                home_id,
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return fence


def _prepare_operation(
    conn: sqlite3.Connection,
    *,
    plan: Mapping[str, object],
    fence: int,
    source_path: Path | None,
    staging_path: Path | None,
    journal_path: Path,
) -> None:
    now = time.time()
    plan_json = json.dumps(
        {
            "database_actions": plan["database_actions"],
            "filesystem_actions": plan["filesystem_actions"],
            "root_history_actions": plan.get("root_history_actions", []),
            "state_digest": plan["state_digest"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute(
        """
        INSERT INTO scoped_reset_operations (
            plan_id, principal_fingerprint, roster_revision,
            inventory_revision, home_id, fence, state, source_path,
            staging_path, journal_path, plan_json, commit_witness, receipt_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, 0, '{}', ?, ?)
        ON CONFLICT(plan_id) DO UPDATE SET
            principal_fingerprint = excluded.principal_fingerprint,
            roster_revision = excluded.roster_revision,
            inventory_revision = excluded.inventory_revision,
            home_id = excluded.home_id,
            fence = excluded.fence,
            state = 'prepared',
            source_path = excluded.source_path,
            staging_path = excluded.staging_path,
            journal_path = excluded.journal_path,
            plan_json = excluded.plan_json,
            commit_witness = 0,
            receipt_json = '{}',
            updated_at = excluded.updated_at
        """,
        (
            plan["plan_id"],
            plan["principal_fingerprint"],
            plan["roster_revision"],
            plan["inventory_revision"],
            plan["home_id"],
            fence,
            str(source_path) if source_path else "",
            str(staging_path) if staging_path else "",
            str(journal_path),
            plan_json,
            now,
            now,
        ),
    )
    conn.commit()


def _action_key_values(
    action: Mapping[str, object],
    *,
    principal: str,
) -> dict[str, object]:
    raw_keys = action["keys"]
    if not isinstance(raw_keys, dict):
        raise ScopedResetPlanChanged("planned row keys are invalid")
    table = str(action["table"])
    keys = dict(raw_keys)
    if table == "founder_home":
        keys = {"founder_sub": principal}
    elif table == "universe_acl":
        keys.pop("identity_alias", None)
        keys["actor_id"] = principal
    return keys


def _delete_planned_rows(
    conn: sqlite3.Connection,
    *,
    plan: Mapping[str, object],
    principal: str,
) -> None:
    raw_actions = plan["database_actions"]
    if not isinstance(raw_actions, list):
        raise ScopedResetPlanChanged("planned database actions are invalid")
    priority = {
        "branch_heads": 0,
        "vote_ballots": 0,
        "universe_hard_priorities": 1,
        "universe_notes": 1,
        "universe_snapshots": 1,
        "universe_work_targets": 1,
        "user_requests": 1,
        "branches": 2,
        "vote_windows": 2,
        "founder_home": 3,
        "universe_acl": 3,
        "deleted_principals": 3,
        "universe_rules": 4,
        "universes": 5,
    }
    actions = sorted(
        raw_actions,
        key=lambda action: priority.get(str(action["table"]), 99),
    )
    for action in actions:
        table = str(action["table"])
        if MAIN_DB_TABLE_CLASSIFICATIONS.get(table) not in {
            "reset_binding",
            "reset_home",
            "reset_home_or_block",
            "reset_subject_grants",
        }:
            raise ScopedResetPlanChanged(
                f"plan attempts an unapproved table action: {table}"
            )
        keys = _action_key_values(action, principal=principal)
        primary_key = set(_primary_key_columns(conn, table))
        if set(keys) != primary_key:
            raise ScopedResetPlanChanged(
                f"planned delete must use the exact primary key for {table}"
            )
        where = " AND ".join(f'"{key}" = ?' for key in keys)
        values = tuple(keys.values())
        row = conn.execute(
            f'SELECT * FROM "{table}" WHERE {where}',
            values,
        ).fetchone()
        if row is None or _row_digest(row) != action["row_digest"]:
            raise ScopedResetPlanChanged(
                f"planned row changed before apply: {table}"
            )
        conn.execute(f'DELETE FROM "{table}" WHERE {where}', values)


def _assert_recovery_filesystem_identity(
    path: Path,
    planned_identity: object,
) -> None:
    if not isinstance(planned_identity, dict):
        raise ScopedResetRecoveryError(
            "reset recovery filesystem identity is missing from the reviewed plan"
        )
    try:
        current_identity = _home_filesystem_identity(path)
    except (OSError, ScopedResetBlocked) as exc:
        raise ScopedResetRecoveryError(
            "reset recovery filesystem identity cannot be verified"
        ) from exc
    if current_identity != planned_identity:
        raise ScopedResetRecoveryError(
            "reset recovery filesystem identity changed after review"
        )


def _safe_cleanup_staging(
    root: Path,
    staging: Path | None,
    *,
    planned_identity: object,
) -> None:
    if staging is None or not staging.exists():
        return
    resolved = staging.resolve(strict=True)
    expected_parent = root / ".scoped-reset-staging"
    if (
        resolved != staging
        or expected_parent not in resolved.parents
        or _is_link_or_reparse(resolved)
    ):
        raise ScopedResetRecoveryError("refusing unsafe reset staging cleanup")
    _assert_recovery_filesystem_identity(resolved, planned_identity)
    shutil.rmtree(resolved)
    operation_dir = resolved.parent
    if operation_dir.is_dir() and not any(operation_dir.iterdir()):
        operation_dir.rmdir()
    _fsync_directory(expected_parent)


def _complete_operation(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    principal_fingerprint: str,
) -> None:
    now = time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE scoped_reset_operations "
            "SET state = 'completed', updated_at = ? WHERE plan_id = ?",
            (now, plan_id),
        )
        conn.execute(
            "UPDATE scoped_reset_leases "
            "SET state = 'released', updated_at = ? "
            "WHERE principal_fingerprint = ?",
            (now, principal_fingerprint),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _fault(
    fault_injector: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


def apply_test_identity_reset(
    data_dir: Path,
    *,
    alias: str,
    roster: TestIdentityRoster,
    plan_id: str,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Apply one exact reviewed plan under the exclusive maintenance barrier."""

    identity_alias, principal = _resolve_roster_principal(
        alias=alias,
        roster=roster,
    )
    principal_fingerprint = _principal_digest(principal)
    root = _validated_root(data_dir)
    db_file = _validated_main_database(root)
    if db_file.is_file():
        receipt_conn = _connect_read_only(db_file)
        try:
            receipt_conn.row_factory = sqlite3.Row
            if "scoped_reset_operations" in _table_names(receipt_conn):
                existing = _completed_receipt_for_principal(
                    receipt_conn,
                    plan_id=plan_id,
                    principal_fingerprint=principal_fingerprint,
                )
                if existing is not None:
                    return existing
        finally:
            receipt_conn.close()
    preflight_plan = plan_test_identity_reset(
        root,
        alias=identity_alias,
        roster=roster,
    )
    if preflight_plan["blockers"]:
        raise ScopedResetBlocked("; ".join(preflight_plan["blockers"]))
    if preflight_plan["noop"]:
        with acquire_maintenance_barrier(root, exclusive=True, timeout=0.0):
            db_file = _validated_main_database(root)
            if db_file.is_file():
                conn = _connect_control(root)
                try:
                    if "scoped_reset_operations" in _table_names(conn):
                        existing = _completed_receipt_for_principal(
                            conn,
                            plan_id=plan_id,
                            principal_fingerprint=principal_fingerprint,
                        )
                        if existing is None:
                            _recover_locked(root, conn)
                            existing = _completed_receipt_for_principal(
                                conn,
                                plan_id=plan_id,
                                principal_fingerprint=principal_fingerprint,
                            )
                        if existing is not None:
                            return existing
                finally:
                    conn.close()
            preflight_plan = plan_test_identity_reset(
                root,
                alias=identity_alias,
                roster=roster,
            )
            if preflight_plan["blockers"]:
                raise ScopedResetBlocked("; ".join(preflight_plan["blockers"]))
            if (
                not preflight_plan["noop"]
                or preflight_plan["plan_id"] != plan_id
            ):
                raise ScopedResetPlanChanged(
                    "current state does not match the reviewed plan"
                )
        return {
            "plan_id": plan_id,
            "status": "noop",
            "identity_alias": identity_alias,
            "home_id": None,
            "fence": 0,
            "inventory_revision": INVENTORY_REVISION,
        }
    with acquire_maintenance_barrier(root, exclusive=True, timeout=0.0):
        conn = _connect_control(root)
        try:
            _ensure_control_schema(conn)
            existing = _completed_receipt_for_principal(
                conn,
                plan_id=plan_id,
                principal_fingerprint=principal_fingerprint,
            )
            if existing is not None:
                return existing
            _recover_locked(root, conn)
            existing = _completed_receipt_for_principal(
                conn,
                plan_id=plan_id,
                principal_fingerprint=principal_fingerprint,
            )
            if existing is not None:
                return existing
            plan = plan_test_identity_reset(
                root,
                alias=identity_alias,
                roster=roster,
            )
            if plan["blockers"]:
                raise ScopedResetBlocked("; ".join(plan["blockers"]))
            if plan["plan_id"] != plan_id:
                raise ScopedResetPlanChanged(
                    "current state does not match the reviewed plan"
                )
            filesystem_actions = plan["filesystem_actions"]
            filesystem_action = (
                filesystem_actions[0]
                if filesystem_actions
                else None
            )
            if (
                filesystem_action is not None
                and filesystem_action.get("owner_principal_fingerprint")
                != principal_fingerprint
            ):
                raise ScopedResetPlanChanged(
                    "reviewed home is not bound to the roster principal"
                )
            source, staging, journal = _safe_operation_paths(
                root,
                plan_id=plan_id,
                home_path=(
                    Path(str(filesystem_action["path"]))
                    if filesystem_action is not None
                    else None
                ),
            )
            if source is not None and filesystem_action is not None:
                _assert_home_filesystem_identity(
                    source,
                    filesystem_action.get("home_filesystem_identity"),
                )
            fence = _acquire_durable_fence(
                conn,
                plan_id=plan_id,
                principal_fingerprint=principal_fingerprint,
                home_id=plan["home_id"],
            )
            _write_journal(
                journal,
                plan_id=plan_id,
                home_id=plan["home_id"],
                fence=fence,
                principal_fingerprint=principal_fingerprint,
                source_path=source,
                staging_path=staging,
                home_filesystem_identity=(
                    filesystem_action.get("home_filesystem_identity")
                    if filesystem_action is not None
                    else None
                ),
            )
            _fault(fault_injector, "after_journal")
            _prepare_operation(
                conn,
                plan=plan,
                fence=fence,
                source_path=source,
                staging_path=staging,
                journal_path=journal,
            )
            _fault(fault_injector, "after_prepare")
            _fault(fault_injector, "before_rename")
            if source is not None and staging is not None:
                _assert_home_filesystem_identity(
                    source,
                    filesystem_action.get("home_filesystem_identity"),
                )
                os.replace(source, staging)
                _fsync_directory(root)
                _fsync_directory(staging.parent)
            conn.execute(
                "UPDATE scoped_reset_operations "
                "SET state = 'staged', updated_at = ? WHERE plan_id = ?",
                (time.time(), plan_id),
            )
            conn.commit()
            _fault(fault_injector, "after_rename")
            _fault(fault_injector, "before_commit")

            receipt = {
                "plan_id": plan_id,
                "status": "completed",
                "identity_alias": identity_alias,
                "home_id": plan["home_id"],
                "fence": fence,
                "inventory_revision": INVENTORY_REVISION,
            }
            conn.execute("BEGIN IMMEDIATE")
            try:
                _delete_planned_rows(
                    conn,
                    plan=plan,
                    principal=principal,
                )
                conn.execute(
                    "UPDATE scoped_reset_operations "
                    "SET state = 'committed', commit_witness = 1, "
                    "receipt_json = ?, updated_at = ? WHERE plan_id = ?",
                    (
                        json.dumps(
                            receipt,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        time.time(),
                        plan_id,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            _fault(fault_injector, "after_commit")
            operation = conn.execute(
                "SELECT * FROM scoped_reset_operations WHERE plan_id=?", (plan_id,),
            ).fetchone()
            from tinyassets.storage.conversation_reset import expire_committed

            expire_committed(root, operation, plan.get("root_history_actions", []))
            _fault(fault_injector, "after_conversation_expiry")
            _fault(fault_injector, "before_cleanup")
            _safe_cleanup_staging(
                root,
                staging,
                planned_identity=(
                    filesystem_action.get("home_filesystem_identity")
                    if filesystem_action is not None
                    else None
                ),
            )
            _fault(fault_injector, "after_cleanup")
            _complete_operation(
                conn,
                plan_id=plan_id,
                principal_fingerprint=principal_fingerprint,
            )
            _fault(fault_injector, "after_complete")
            _remove_journal(journal)
            return receipt
        finally:
            conn.close()


def _operation_plan_evidence(
    operation: sqlite3.Row,
) -> dict[str, object]:
    try:
        plan = json.loads(str(operation["plan_json"]))
        database_actions = plan["database_actions"]
        filesystem_actions = plan["filesystem_actions"]
        root_history_actions = plan.get("root_history_actions", [])
        state_digest = plan["state_digest"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ScopedResetRecoveryError(
            "incomplete reset has invalid content-free plan evidence"
        ) from exc
    if (
        not isinstance(plan, dict)
        or not isinstance(database_actions, list)
        or not isinstance(filesystem_actions, list)
        or not isinstance(root_history_actions, list)
        or state_digest
        != _reset_state_digest(database_actions, filesystem_actions, root_history_actions)
    ):
        raise ScopedResetRecoveryError(
            "incomplete reset has invalid content-free plan evidence"
        )
    return {str(key): value for key, value in plan.items()}


def _operation_home_filesystem_identity(
    operation: sqlite3.Row,
    *,
    source: Path | None,
    staging: Path | None,
) -> object:
    plan = _operation_plan_evidence(operation)
    filesystem_actions = plan["filesystem_actions"]
    if staging is None:
        if filesystem_actions:
            raise ScopedResetRecoveryError(
                "incomplete reset filesystem evidence disagrees with scope"
            )
        return None
    if (
        source is None
        or len(filesystem_actions) != 1
        or not isinstance(filesystem_actions[0], dict)
    ):
        raise ScopedResetRecoveryError(
            "incomplete reset filesystem identity is invalid"
        )
    action = filesystem_actions[0]
    if (
        action.get("action") != "stage_then_remove_home"
        or action.get("path") != str(source)
        or action.get("owner_principal_fingerprint")
        != str(operation["principal_fingerprint"])
        or not isinstance(action.get("home_filesystem_identity"), dict)
    ):
        raise ScopedResetRecoveryError(
            "incomplete reset filesystem identity is invalid"
        )
    return action["home_filesystem_identity"]


def _operation_evidence_from_row(
    root: Path,
    row: sqlite3.Row,
) -> tuple[Path | None, Path | None, Path, object]:
    plan_id = str(row["plan_id"])
    home_id = str(row["home_id"]) if row["home_id"] else None
    source, staging, journal = _expected_operation_evidence(
        root,
        plan_id=plan_id,
        home_id=home_id,
    )
    stored = (
        str(row["source_path"]),
        str(row["staging_path"]),
        str(row["journal_path"]),
    )
    expected = (
        str(source) if source else "",
        str(staging) if staging else "",
        str(journal),
    )
    if stored != expected:
        raise ScopedResetRecoveryError(
            f"incomplete reset path evidence disagrees with scope: {plan_id}"
        )
    candidates = [source, staging, journal.parent]
    if staging is not None:
        candidates.extend((staging.parent, staging.parent.parent))
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            if (
                _is_link_or_reparse(candidate)
                or candidate.resolve(strict=True) != candidate
            ):
                raise ScopedResetRecoveryError(
                    f"incomplete reset path evidence is linked: {plan_id}"
                )
            if candidate.stat().st_dev != root.stat().st_dev:
                raise ScopedResetRecoveryError(
                    f"incomplete reset path evidence crossed filesystems: {plan_id}"
                )
    planned_identity = _operation_home_filesystem_identity(
        row,
        source=source,
        staging=staging,
    )
    _validated_journal(
        journal,
        plan_id=plan_id,
        home_id=home_id,
        fence=int(row["fence"]),
        principal_fingerprint=str(row["principal_fingerprint"]),
        source=source,
        staging=staging,
        home_filesystem_identity=planned_identity,
    )
    return source, staging, journal, planned_identity


def _sweep_terminal_or_orphan_journals(
    root: Path,
    conn: sqlite3.Connection,
) -> None:
    journal_root = root / ".scoped-reset-journal"
    if not journal_root.exists():
        return
    if _is_link_or_reparse(journal_root):
        raise ScopedResetRecoveryError(
            "scoped-reset journal root is a link or reparse point"
        )
    for temporary in sorted(journal_root.glob("*.tmp")):
        operation_id = temporary.stem
        plan_id = f"sha256:{operation_id}"
        if (
            len(operation_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in operation_id
            )
            or _is_link_or_reparse(temporary)
        ):
            raise ScopedResetRecoveryError(
                f"unsafe scoped-reset temporary journal: {temporary.name}"
            )
        row = conn.execute(
            "SELECT 1 FROM scoped_reset_operations WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is not None:
            raise ScopedResetRecoveryError(
                f"operation has only a temporary journal: {plan_id}"
            )
        conn.execute(
            "UPDATE scoped_reset_leases SET state = 'released', updated_at = ? "
            "WHERE plan_id = ?",
            (time.time(), plan_id),
        )
        conn.commit()
        temporary.unlink()
        _fsync_directory(journal_root)
    for journal in sorted(journal_root.glob("*.json")):
        try:
            payload = json.loads(journal.read_text(encoding="utf-8"))
            plan_id = str(payload["plan_id"])
        except (KeyError, OSError, json.JSONDecodeError) as exc:
            raise ScopedResetRecoveryError(
                f"unreadable scoped-reset journal: {journal.name}"
            ) from exc
        row = conn.execute(
            "SELECT * FROM scoped_reset_operations WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is not None:
            if str(row["state"]) in {"completed", "rolled_back"}:
                _operation_evidence_from_row(root, row)
                _remove_journal(journal)
            continue
        home_id = str(payload["home_id"]) if payload.get("home_id") else None
        source, staging, expected_journal = _expected_operation_evidence(
            root,
            plan_id=plan_id,
            home_id=home_id,
        )
        if journal != expected_journal:
            raise ScopedResetRecoveryError(
                f"orphan journal path evidence disagrees with scope: {plan_id}"
            )
        _validated_journal(
            journal,
            plan_id=plan_id,
            home_id=home_id,
            fence=int(payload.get("fence", 0)),
            principal_fingerprint=str(payload.get("principal_fingerprint", "")),
            source=source,
            staging=staging,
            home_filesystem_identity=payload.get("home_filesystem_identity"),
        )
        if staging is not None and staging.exists():
            raise ScopedResetRecoveryError(
                f"orphan journal has staged filesystem state: {plan_id}"
            )
        conn.execute(
            "UPDATE scoped_reset_leases SET state = 'released', updated_at = ? "
            "WHERE plan_id = ?",
            (time.time(), plan_id),
        )
        conn.commit()
        _remove_journal(journal)


def _recover_locked(root: Path, conn: sqlite3.Connection) -> None:
    _ensure_control_schema(conn)
    _sweep_terminal_or_orphan_journals(root, conn)
    rows = conn.execute(
        "SELECT * FROM scoped_reset_operations "
        "WHERE state NOT IN ('completed', 'rolled_back') ORDER BY created_at"
    ).fetchall()
    for row in rows:
        plan_id = str(row["plan_id"])
        source, staging, journal, planned_identity = _operation_evidence_from_row(
            root,
            row,
        )
        witness = bool(row["commit_witness"])
        if witness:
            from tinyassets.storage.conversation_reset import expire_committed

            expire_committed(
                root, row, _operation_plan_evidence(row).get("root_history_actions", []),
            )
            _safe_cleanup_staging(
                root,
                staging,
                planned_identity=planned_identity,
            )
            _complete_operation(
                conn,
                plan_id=plan_id,
                principal_fingerprint=str(row["principal_fingerprint"]),
            )
            _remove_journal(journal)
            continue
        _verify_precommit_database_state(conn, row)
        if staging is not None:
            if source is None:
                raise ScopedResetRecoveryError(
                    f"staged reset has no source path: {plan_id}"
                )
            source_exists = source.exists()
            staging_exists = staging.exists()
            if source_exists == staging_exists:
                raise ScopedResetRecoveryError(
                    f"pre-commit reset filesystem state is ambiguous: {plan_id}"
                )
            if staging_exists:
                _assert_recovery_filesystem_identity(staging, planned_identity)
                try:
                    os.replace(staging, source)
                except OSError as exc:
                    raise ScopedResetRecoveryError(
                        f"rollback rename failed for plan {plan_id}"
                    ) from exc
                _fsync_directory(root)
                _fsync_directory(staging.parent)
            else:
                _assert_recovery_filesystem_identity(source, planned_identity)
            operation_dir = staging.parent
            if operation_dir.is_dir() and not any(operation_dir.iterdir()):
                operation_dir.rmdir()
                _fsync_directory(operation_dir.parent)
        now = time.time()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE scoped_reset_operations "
                "SET state = 'rolled_back', updated_at = ? WHERE plan_id = ?",
                (now, plan_id),
            )
            conn.execute(
                "UPDATE scoped_reset_leases "
                "SET state = 'released', updated_at = ? "
                "WHERE principal_fingerprint = ?",
                (now, row["principal_fingerprint"]),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _remove_journal(journal)


def _verify_precommit_database_state(
    conn: sqlite3.Connection,
    operation: sqlite3.Row,
) -> None:
    actions = _operation_plan_evidence(operation)["database_actions"]
    for action in actions:
        if not isinstance(action, dict):
            raise ScopedResetRecoveryError(
                "incomplete reset has invalid database action evidence"
            )
        table = str(action.get("table") or "")
        expected_digest = str(action.get("row_digest") or "")
        if table not in MAIN_DB_TABLE_CLASSIFICATIONS or not expected_digest:
            raise ScopedResetRecoveryError(
                "incomplete reset has unclassified database action evidence"
            )
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        if not any(_row_digest(row) == expected_digest for row in rows):
            raise ScopedResetRecoveryError(
                f"pre-commit database state changed for plan "
                f"{operation['plan_id']}: {table}"
            )


def recover_scoped_resets(data_dir: Path) -> None:
    """Converge every incomplete operation before affected writers resume."""

    root = _validated_root(data_dir)
    with acquire_maintenance_barrier(root, exclusive=True, timeout=0.0):
        conn = _connect_control(root)
        try:
            _recover_locked(root, conn)
        finally:
            conn.close()


def _assert_recovery_state_is_clean(root: Path) -> None:
    journal_root = root / ".scoped-reset-journal"
    if journal_root.exists():
        if _is_link_or_reparse(journal_root):
            raise ScopedResetRecoveryError(
                "scoped-reset journal root is a link or reparse point"
            )
        if any(journal_root.glob("*.json")) or any(journal_root.glob("*.tmp")):
            raise ScopedResetRecoveryError(
                "writer cannot join while scoped-reset recovery is pending"
            )
    db_file = _validated_main_database(root)
    if not db_file.is_file():
        return
    conn = _connect_read_only(db_file)
    try:
        if "scoped_reset_operations" not in _table_names(conn):
            return
        pending = conn.execute(
            "SELECT COUNT(*) FROM scoped_reset_operations "
            "WHERE state NOT IN ('completed', 'rolled_back')"
        ).fetchone()
        if pending and int(pending[0]):
            raise ScopedResetRecoveryError(
                "writer cannot join while scoped-reset recovery is pending"
            )
    finally:
        conn.close()


def prepare_service_writer_barrier(
    data_dir: Path,
    *,
    timeout: float = 30.0,
) -> MaintenanceBarrier:
    """Recover interrupted reset state, then admit one service writer process."""

    raw_root = Path(data_dir).absolute()
    if raw_root.exists() and _is_link_or_reparse(raw_root):
        raise ScopedResetBlocked(
            "scoped-reset data root is a link or reparse point"
        )
    existing_ancestor = raw_root
    while not existing_ancestor.exists():
        existing_ancestor = existing_ancestor.parent
    if (
        _is_link_or_reparse(existing_ancestor)
        or existing_ancestor.resolve(strict=True) != existing_ancestor
    ):
        raise ScopedResetBlocked(
            "scoped-reset data-root ancestry traverses a link or reparse point"
        )
    raw_root.mkdir(parents=True, exist_ok=True)
    root = raw_root.resolve(strict=True)
    if root != raw_root or _is_link_or_reparse(root):
        raise ScopedResetBlocked(
            "scoped-reset data root traverses a link or reparse point"
        )
    try:
        recovery_barrier = acquire_maintenance_barrier(
            root,
            exclusive=True,
            timeout=0.0,
        )
    except ScopedResetLeaseBusy:
        writer_barrier = acquire_maintenance_barrier(
            root,
            exclusive=False,
            timeout=timeout,
        )
        try:
            _assert_recovery_state_is_clean(root)
        except Exception:
            writer_barrier.release()
            raise
        return writer_barrier

    try:
        conn = _connect_control(root)
        try:
            _recover_locked(root, conn)
        finally:
            conn.close()
    finally:
        recovery_barrier.release()

    writer_barrier = acquire_maintenance_barrier(
        root,
        exclusive=False,
        timeout=timeout,
    )
    try:
        _assert_recovery_state_is_clean(root)
    except Exception:
        writer_barrier.release()
        raise
    return writer_barrier


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tinyassets.scoped_reset",
        description="Operator-only exact test-identity reset maintenance",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="print a read-only reset plan")
    plan.add_argument("--data-dir", required=True, type=Path)
    plan.add_argument("--roster", required=True, type=Path)
    plan.add_argument("--identity", required=True)
    apply = commands.add_parser("apply", help="apply an exact reviewed plan")
    apply.add_argument("--data-dir", required=True, type=Path)
    apply.add_argument("--roster", required=True, type=Path)
    apply.add_argument("--identity", required=True)
    apply.add_argument("--plan-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the operator-only maintenance CLI."""

    args = _build_parser().parse_args(argv)
    if args.command == "plan":
        roster = load_test_identity_roster(args.roster)
        plan = plan_test_identity_reset(
            args.data_dir,
            alias=args.identity,
            roster=roster,
        )
        print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "apply":
        roster = load_test_identity_roster(args.roster)
        receipt = apply_test_identity_reset(
            args.data_dir,
            alias=args.identity,
            roster=roster,
            plan_id=args.plan_id,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    raise AssertionError(f"unhandled scoped reset command: {args.command}")


__all__ = [
    "FAULT_POINTS",
    "INVENTORY_REVISION",
    "MAIN_DB_TABLE_CLASSIFICATIONS",
    "MaintenanceBarrier",
    "ScopeInventory",
    "ScopedResetError",
    "ScopedResetBlocked",
    "ScopedResetLeaseBusy",
    "ScopedResetPlanChanged",
    "ScopedResetRecoveryError",
    "ScopedResetSchemaError",
    "TestIdentityRoster",
    "acquire_maintenance_barrier",
    "apply_test_identity_reset",
    "inspect_reset_scope",
    "load_test_identity_roster",
    "main",
    "plan_test_identity_reset",
    "prepare_service_writer_barrier",
    "read_completed_plan_receipt",
    "recover_scoped_resets",
]


if __name__ == "__main__":
    raise SystemExit(main())
