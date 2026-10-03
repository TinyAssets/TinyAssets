"""Transactional request-admission and queue-epoch-2 persistence.

This module is intentionally storage-only. Public request parsing, authority
composition, rollout gates, dispatcher selection, and external execution
authority live in later layers. The writer remains unreachable until those
layers explicitly call :class:`RequestAdmissionStore`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tinyassets.execution_subject import ExecutionSubjectKind
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    AutomationActivation,
    AutomationActivationState,
    AutomationActivationStore,
)

logger = logging.getLogger(__name__)

PRIORITY_WEIGHT_CAP = 100
QUEUE_EPOCH = 2
QUEUE_PROTOCOL_VERSION = 2
OPERATOR_CAPABILITY = "operator_request_v1"
# Queue ownership must outlive the longest supported provider call (15m)
# between node-boundary heartbeats. Worker descriptors remain independently
# capped at 90 seconds and are revalidated for every new claim/resume.
MAX_LEASE_SECONDS = 1800
MAX_QUARANTINE_SCAN_ROWS = 1000
MAX_OPERATIONAL_SCAN_ROWS = 1000
TERMINAL_STATUSES = frozenset({"cancelled", "succeeded", "failed"})
QUARANTINE_REASONS = frozenset({
    "unsupported_protocol",
    "incomplete",
    "invalid_operator_admission",
})
# An admission's idempotency hash is typed by where its identity comes from,
# and both the writer boundary and `branch_tasks_v2` read that type from here
# so they cannot drift apart.
#
# A caller-supplied key is low entropy and guessable, so it is keyed: without
# the HMAC anyone could probe whether a given idempotency key was ever used.
# A server-derived automation identity has no such exposure — its seed is the
# continuation/activation ids — and it must stay *key-independent*, because
# the replay lookup is by hash alone while the admission and task ids are
# derived deterministically from the same seed. Key the automation hash and a
# retry after a key rotation misses replay and then collides on the
# unchanged primary keys instead of returning the original aggregate.
USER_KEYED_IDEMPOTENCY_HASH_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
SERVER_DERIVED_IDEMPOTENCY_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDEMPOTENCY_HMAC_ENV = "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"


def expected_idempotency_hash_re(*, server_derived: bool) -> re.Pattern[str]:
    """Return the only hash shape valid for this admission's provenance."""

    return (
        SERVER_DERIVED_IDEMPOTENCY_HASH_RE
        if server_derived
        else USER_KEYED_IDEMPOTENCY_HASH_RE
    )
_AUTHORITATIVE_UNIVERSE_SCOPE_SQL = """
COALESCE(
    NULLIF(a.universe_id, ''),
    NULLIF(r.universe_id, '')
) = ?
"""
_UNSCOPED_UNIVERSE_SQL = """
COALESCE(
    NULLIF(a.universe_id, ''),
    NULLIF(r.universe_id, '')
) IS NULL
"""


def mint_idempotency_key_hash(raw_key: str) -> str:
    """Key a caller-supplied idempotency key. Requires the HMAC secret."""

    secret = os.environ.get(IDEMPOTENCY_HMAC_ENV, "").encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("request admission HMAC key is not configured")
    digest = hmac.new(secret, raw_key.encode("utf-8"), hashlib.sha256)
    return f"hmac-sha256:{digest.hexdigest()}"


def canonical_content_digest(value: object) -> str:
    """Digest a server-derived identity. Deliberately unkeyed.

    Cloud workers execute user-supplied node source in-process, so they are
    not given the admission HMAC secret (`deploy/compose.yml`). They are
    nonetheless the process that commits automation admissions, so the
    identity they mint has to be derivable without it — and stable across
    key rotation, so replay keeps working.
    """

    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone() is not None


_ACTIVE_CLAIM_COLUMNS = frozenset({
    "claimed_by",
    "disabled",
    "lease_expires_at",
    "status",
    "universe_id",
})


def _active_claim_schema_ready(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "branch_tasks_v2"):
        return False
    columns = {
        str(row["name"])
        for row in conn.execute(
            "PRAGMA table_info(branch_tasks_v2)"
        ).fetchall()
    }
    return _ACTIVE_CLAIM_COLUMNS <= columns


_RECOVERY_COLUMNS = _ACTIVE_CLAIM_COLUMNS | frozenset({
    "admission_id",
    "branch_task_id",
    "claimed_at",
    "heartbeat_at",
    "queued_at",
    "request_id",
    "terminal_at",
})


def _recovery_schema_ready(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "branch_tasks_v2"):
        return False
    if any(
        not _table_exists(conn, table_name)
        for table_name in (
            "request_admissions",
            "request_admission_events",
            "user_requests",
        )
    ):
        return False
    columns = {
        str(row["name"])
        for row in conn.execute(
            "PRAGMA table_info(branch_tasks_v2)"
        ).fetchall()
    }
    return _RECOVERY_COLUMNS <= columns


# Fault-injection checkpoints across every precommit transaction phase.
COMMIT_STEPS = (
    "access_checked",
    "replay_checked",
    "authority_checked",
    "ids_allocated",
    "request_inserted",
    "admission_inserted",
    "task_inserted",
    "event_inserted",
    "before_commit",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_admissions (
    admission_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    branch_task_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    idempotency_key_hash TEXT NOT NULL,
    body_digest TEXT NOT NULL,
    body_digest_version TEXT NOT NULL,
    trigger_source TEXT NOT NULL
        CHECK (trigger_source IN (
            'operator_request', 'user_request', 'owner_queued'
        )),
    accepted_priority_weight REAL NOT NULL
        CHECK (
            accepted_priority_weight = accepted_priority_weight
            AND accepted_priority_weight >= 0
            AND accepted_priority_weight <= 100
        ),
    priority_policy_version TEXT NOT NULL,
    grant_generation INTEGER NOT NULL DEFAULT 0
        CHECK (grant_generation >= 0),
    receipt_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'committed'
        CHECK (state IN ('committed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    compacted_at TEXT,
    UNIQUE (
        tenant_id, actor_id, universe_id, idempotency_key_hash
    ),
    FOREIGN KEY(request_id) REFERENCES user_requests(request_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(branch_task_id) REFERENCES branch_tasks_v2(branch_task_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS branch_tasks_v2 (
    branch_task_id TEXT PRIMARY KEY,
    admission_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    universe_id TEXT NOT NULL,
    branch_def_id TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    trigger_source TEXT NOT NULL
        CHECK (trigger_source IN (
            'operator_request', 'user_request', 'owner_queued'
        )),
    priority_weight REAL NOT NULL
        CHECK (
            priority_weight = priority_weight
            AND priority_weight >= 0
            AND priority_weight <= 100
        ),
    directed_daemon_id TEXT NOT NULL DEFAULT '',
    automation_id TEXT,
    automation_activation_epoch INTEGER
        CHECK (
            automation_activation_epoch IS NULL
            OR automation_activation_epoch >= 0
        ),
    automation_executor_class TEXT
        CHECK (
            automation_executor_class IS NULL
            OR automation_executor_class IN ('tray', 'cloud')
        ),
    automation_subject_kind TEXT
        CHECK (
            automation_subject_kind IS NULL
            OR automation_subject_kind IN ('branch_version', 'agent_runtime_manifest')
        ),
    automation_subject_ref TEXT,
    automation_subject_digest TEXT,
    automation_branch_version TEXT,
    automation_lease_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'running', 'cancel_requested', 'cancelled',
            'succeeded', 'failed'
        )),
    queue_epoch INTEGER NOT NULL DEFAULT 2 CHECK (queue_epoch = 2),
    protocol_version INTEGER NOT NULL DEFAULT 2
        CHECK (protocol_version = 2),
    claimed_by TEXT NOT NULL DEFAULT '',
    queued_at TEXT NOT NULL,
    claimed_at TEXT,
    heartbeat_at TEXT,
    lease_expires_at TEXT,
    terminal_at TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    quarantine_reason TEXT NOT NULL DEFAULT '',
    CHECK (
        (
            automation_id IS NULL
            AND automation_activation_epoch IS NULL
            AND automation_executor_class IS NULL
            AND automation_subject_kind IS NULL
            AND automation_subject_ref IS NULL
            AND automation_subject_digest IS NULL
            AND automation_branch_version IS NULL
            AND automation_lease_id IS NULL
        )
        OR
        (
            automation_id IS NOT NULL
            AND automation_activation_epoch IS NOT NULL
            AND automation_executor_class IS NOT NULL
            AND automation_subject_kind IS NOT NULL
            AND automation_subject_ref IS NOT NULL
            AND automation_subject_digest IS NOT NULL
            AND automation_branch_version IS NOT NULL
            AND automation_lease_id IS NOT NULL
        )
    ),
    FOREIGN KEY(admission_id) REFERENCES request_admissions(admission_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(request_id) REFERENCES user_requests(request_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS request_admission_events (
    event_id TEXT PRIMARY KEY,
    admission_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    branch_task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(admission_id) REFERENCES request_admissions(admission_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(request_id) REFERENCES user_requests(request_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(branch_task_id) REFERENCES branch_tasks_v2(branch_task_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS branch_tasks_v2_quarantine (
    row_digest TEXT PRIMARY KEY,
    branch_task_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    row_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1 CHECK (seen_count >= 1),
    FOREIGN KEY(branch_task_id) REFERENCES branch_tasks_v2(branch_task_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS branch_tasks_v2_maintenance_state (
    maintenance_name TEXT PRIMARY KEY,
    last_source_rowid INTEGER NOT NULL DEFAULT 0
        CHECK (last_source_rowid >= 0),
    cycle_high_rowid INTEGER NOT NULL DEFAULT 0
        CHECK (cycle_high_rowid >= 0)
);

CREATE TABLE IF NOT EXISTS request_admission_rollouts (
    universe_id TEXT PRIMARY KEY,
    rollout_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL
        CHECK (state IN (
            'disabled', 'readers_only', 'canary', 'enabled', 'rollback'
        )),
    queue_epoch INTEGER NOT NULL DEFAULT 2 CHECK (queue_epoch = 2),
    required_capability TEXT NOT NULL DEFAULT 'operator_request_v1',
    allowed_reader_shas_json TEXT NOT NULL DEFAULT '[]',
    allowed_server_shas_json TEXT NOT NULL DEFAULT '[]',
    config_hash TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    activated_at TEXT,
    expires_at TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_request_admissions_scope
    ON request_admissions(
        tenant_id, actor_id, universe_id, idempotency_key_hash
    );
CREATE INDEX IF NOT EXISTS idx_request_admissions_universe
    ON request_admissions(universe_id, state, created_at);
CREATE INDEX IF NOT EXISTS idx_branch_tasks_v2_pickable
    ON branch_tasks_v2(status, disabled, queued_at);
CREATE INDEX IF NOT EXISTS idx_branch_tasks_v2_universe
    ON branch_tasks_v2(universe_id, status, disabled, queued_at);
CREATE INDEX IF NOT EXISTS idx_request_admission_events_admission
    ON request_admission_events(admission_id, event_at);
CREATE INDEX IF NOT EXISTS idx_request_admission_events_task
    ON request_admission_events(branch_task_id, event_at);
CREATE INDEX IF NOT EXISTS idx_branch_tasks_v2_quarantine_task
    ON branch_tasks_v2_quarantine(branch_task_id);
"""


class IdempotencyKeyBodyConflict(ValueError):
    """A scoped idempotency key was reused for a different canonical body."""


class RequestAdmissionStore:
    """SQLite implementation of the backend-neutral admission aggregate."""

    def __init__(
        self,
        base_path: str | Path,
        *,
        id_factory: Callable[[str], str] | None = None,
        busy_timeout_ms: int = 30_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_path = Path(base_path)
        self._id_factory = id_factory or _random_id
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._clock = clock or _utc_now
        if self._busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open a WAL/foreign-key/busy-timeout connection.

        Lock errors deliberately propagate. Treating a lock as an idempotency
        miss would permit duplicate effects.
        """

        path = db_path(self.base_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            yield conn
        finally:
            conn.close()

    def commit_admission(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        universe_id: str,
        idempotency_key_hash: str,
        body_digest: str,
        body_digest_version: str,
        request_type: str,
        text: str,
        branch_id: str,
        branch_def_id: str,
        trigger_source: str,
        accepted_priority_weight: float,
        policy_version: str,
        grant_generation: int,
        receipt: Mapping[str, Any],
        directed_daemon_id: str,
        created_at: str,
        pickup_incentive: str = "",
        directed_daemon_instruction: str = "",
        automation_activation: AutomationActivation | None = None,
        access_check: Callable[[sqlite3.Connection], Any] | None = None,
        authority_check: Callable[[sqlite3.Connection], Any] | None = None,
        fault_injector: (
            Callable[[str, sqlite3.Connection], Any] | None
        ) = None,
    ) -> dict[str, Any]:
        """Atomically create Request, Admission, v2 task, and committed event."""

        scope = (
            _required(tenant_id, "tenant_id"),
            _required(actor_id, "actor_id"),
            _required(universe_id, "universe_id"),
            _required(idempotency_key_hash, "idempotency_key_hash"),
        )
        # Reject here rather than let the row land and be quarantined far
        # away under a state name that explains nothing. Fail loudly at the
        # boundary that knows what it is looking at. `automation_activation`
        # is a store-validated object, not a caller-supplied string, so it is
        # a safe discriminator: a user request cannot claim to be
        # server-derived to reach the unkeyed shape.
        expected_hash_re = expected_idempotency_hash_re(
            server_derived=automation_activation is not None,
        )
        if expected_hash_re.fullmatch(scope[3]) is None:
            raise ValueError(
                "idempotency_key_hash does not match the shape required for "
                f"this admission's provenance ({expected_hash_re.pattern}); "
                f"got {scope[3][:24]!r}. Mint it with "
                "mint_idempotency_key_hash() for a caller-supplied key or "
                "canonical_content_digest() for a server-derived identity."
            )
        _required(body_digest, "body_digest")
        _required(body_digest_version, "body_digest_version")
        clean_branch_def_id = _required(branch_def_id, "branch_def_id")
        _required(created_at, "created_at")
        activation_values: tuple[object, ...] = (
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        if automation_activation is not None:
            if not isinstance(
                automation_activation,
                AutomationActivation,
            ):
                raise ValueError(
                    "automation_activation must be an AutomationActivation"
                )
            if (
                automation_activation.state
                is not AutomationActivationState.ACTIVE
            ):
                raise ValueError("automation_activation must be active")
            if automation_activation.universe_id != scope[2]:
                raise ValueError(
                    "automation_activation command center does not match admission"
                )
            assert automation_activation.executor_class is not None
            assert automation_activation.subject is not None
            if (
                automation_activation.subject.kind
                is not ExecutionSubjectKind.BRANCH_VERSION
            ):
                raise ValueError(
                    "branch task admission requires a branch_version subject"
                )
            activation_values = (
                automation_activation.automation_id,
                automation_activation.epoch,
                automation_activation.executor_class.value,
                automation_activation.subject.kind.value,
                automation_activation.subject.ref,
                automation_activation.subject.digest,
                automation_activation.immutable_branch_version,
                automation_activation.lease_id,
            )
        stored_receipt = dict(receipt)
        stored_receipt.pop("_automation_activation", None)
        if automation_activation is not None:
            stored_receipt["_automation_activation"] = {
                "automation_id": automation_activation.automation_id,
                "epoch": automation_activation.epoch,
                "executor_class": (
                    automation_activation.executor_class.value
                ),
                "subject": automation_activation.subject.to_dict(),
                "lease_id": automation_activation.lease_id,
            }
        stored_receipt["branch_def_id"] = clean_branch_def_id
        accepted_weight = float(accepted_priority_weight)
        if (
            not math.isfinite(accepted_weight)
            or accepted_weight < 0
            or accepted_weight > PRIORITY_WEIGHT_CAP
        ):
            raise ValueError(
                "accepted_priority_weight must be finite and within [0, 100]"
            )

        for attempt in range(5):
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if access_check is not None:
                        access_check(conn)
                    _inject(fault_injector, "access_checked", conn)

                    replay = self._lookup_replay_conn(
                        conn,
                        tenant_id=scope[0],
                        actor_id=scope[1],
                        universe_id=scope[2],
                        idempotency_key_hash=scope[3],
                        body_digest=body_digest,
                        body_digest_version=body_digest_version,
                    )
                    _inject(fault_injector, "replay_checked", conn)
                    if replay is not None:
                        conn.commit()
                        return replay
                    if authority_check is not None:
                        authority_check(conn)
                    if (
                        automation_activation is not None
                        and not AutomationActivationStore
                        .validate_claim_in_transaction(
                            conn,
                            universe_id=automation_activation.universe_id,
                            automation_id=(
                                automation_activation.automation_id
                            ),
                            epoch=automation_activation.epoch,
                            executor_class=(
                                automation_activation.executor_class
                            ),
                            subject=automation_activation.subject,
                            lease_id=automation_activation.lease_id,
                        )
                    ):
                        raise PermissionError(
                            "automation_activation_not_current"
                        )
                    _inject(fault_injector, "authority_checked", conn)

                    request_id = self._id_factory("req")
                    admission_id = self._id_factory("adm")
                    branch_task_id = self._id_factory("bt2")
                    event_id = self._id_factory("evt")
                    _inject(fault_injector, "ids_allocated", conn)
                    result = _public_result(
                        universe_id=scope[2],
                        admission_id=admission_id,
                        request_id=request_id,
                        branch_task_id=branch_task_id,
                        trigger_source=trigger_source,
                        accepted_priority_weight=accepted_weight,
                        policy_version=policy_version,
                        directed_daemon_id=directed_daemon_id,
                        idempotent_replay=False,
                    )

                    conn.execute(
                        """
                        INSERT INTO user_requests (
                            request_id, universe_id, branch_id, user_id,
                            request_type, text, preferred_author_id, status,
                            created_at, updated_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                        """,
                        (
                            request_id,
                            scope[2],
                            branch_id or None,
                            scope[1],
                            request_type,
                            text,
                            directed_daemon_id or None,
                            _epoch_seconds(created_at),
                            _epoch_seconds(created_at),
                            _json({
                                "tenant_id": scope[0],
                                "admission_id": admission_id,
                                "queue_epoch": QUEUE_EPOCH,
                                "branch_def_id": clean_branch_def_id,
                                "pickup_incentive": pickup_incentive,
                                "directed_daemon_instruction": (
                                    directed_daemon_instruction
                                ),
                            }),
                        ),
                    )
                    _inject(fault_injector, "request_inserted", conn)

                    conn.execute(
                        """
                        INSERT INTO request_admissions (
                            admission_id, request_id, branch_task_id,
                            tenant_id, actor_id, universe_id,
                            idempotency_key_hash, body_digest,
                            body_digest_version, trigger_source,
                            accepted_priority_weight,
                            priority_policy_version, grant_generation,
                            receipt_json, result_json, state,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'committed', ?, ?
                        )
                        """,
                        (
                            admission_id,
                            request_id,
                            branch_task_id,
                            scope[0],
                            scope[1],
                            scope[2],
                            scope[3],
                            body_digest,
                            body_digest_version,
                            trigger_source,
                            accepted_weight,
                            policy_version,
                            grant_generation,
                            _json(stored_receipt),
                            _json(result),
                            created_at,
                            created_at,
                        ),
                    )
                    _inject(fault_injector, "admission_inserted", conn)

                    conn.execute(
                        """
                        INSERT INTO branch_tasks_v2 (
                            branch_task_id, admission_id, request_id,
                            universe_id, branch_def_id, inputs_json,
                            trigger_source, priority_weight,
                            directed_daemon_id, automation_id,
                            automation_activation_epoch,
                            automation_executor_class,
                            automation_subject_kind,
                            automation_subject_ref,
                            automation_subject_digest,
                            automation_branch_version,
                            automation_lease_id, status, queue_epoch,
                            protocol_version, queued_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'pending', 2, 2, ?
                        )
                        """,
                        (
                            branch_task_id,
                            admission_id,
                            request_id,
                            scope[2],
                            clean_branch_def_id,
                            _json({
                                "request_id": request_id,
                                "request_type": request_type,
                                "branch_id": branch_id,
                                "pickup_incentive": pickup_incentive,
                                "directed_daemon_instruction": (
                                    directed_daemon_instruction
                                ),
                            }),
                            trigger_source,
                            accepted_weight,
                            directed_daemon_id,
                            *activation_values,
                            created_at,
                        ),
                    )
                    _inject(fault_injector, "task_inserted", conn)

                    conn.execute(
                        """
                        INSERT INTO request_admission_events (
                            event_id, admission_id, request_id,
                            branch_task_id, event_type, event_at, detail_json
                        ) VALUES (?, ?, ?, ?, 'committed', ?, ?)
                        """,
                        (
                            event_id,
                            admission_id,
                            request_id,
                            branch_task_id,
                            created_at,
                            _json({
                                "queue_epoch": QUEUE_EPOCH,
                                "trigger_source": trigger_source,
                            }),
                        ),
                    )
                    _inject(fault_injector, "event_inserted", conn)
                    _inject(fault_injector, "before_commit", conn)
                    conn.commit()
                    return result
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    if _looks_like_random_id_collision(exc) and attempt < 4:
                        continue
                    raise
                except Exception:
                    conn.rollback()
                    raise
        raise RuntimeError("request admission ID allocation exhausted")

    def lookup_replay(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        universe_id: str,
        idempotency_key_hash: str,
        body_digest: str,
        body_digest_version: str,
        access_check: Callable[[sqlite3.Connection], Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            conn.execute("BEGIN")
            try:
                if access_check is not None:
                    access_check(conn)
                replay = self._lookup_replay_conn(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    universe_id=universe_id,
                    idempotency_key_hash=idempotency_key_hash,
                    body_digest=body_digest,
                    body_digest_version=body_digest_version,
                )
                conn.commit()
                return replay
            except BaseException:
                conn.rollback()
                raise

    def _lookup_replay_conn(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        actor_id: str,
        universe_id: str,
        idempotency_key_hash: str,
        body_digest: str,
        body_digest_version: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT body_digest, body_digest_version, result_json
            FROM request_admissions
            WHERE tenant_id = ? AND actor_id = ? AND universe_id = ?
              AND idempotency_key_hash = ?
            """,
            (
                tenant_id,
                actor_id,
                universe_id,
                idempotency_key_hash,
            ),
        ).fetchone()
        if row is None:
            return None
        if (
            row["body_digest"] != body_digest
            or row["body_digest_version"] != body_digest_version
        ):
            raise IdempotencyKeyBodyConflict(
                "idempotency_key_body_conflict"
            )
        result = json.loads(row["result_json"])
        result["idempotent_replay"] = True
        return result

    def list_v2_candidates(
        self,
        *,
        universe_id: str = "",
        limit: int = 1000,
        integrity_check: Callable[[Mapping[str, Any]], bool],
    ) -> list[dict[str, Any]]:
        with self.connection() as conn:
            cursor = self._v2_integrity_cursor(
                conn,
                universe_id=universe_id,
                pending_only=True,
                exclude_active_automation=True,
            )
            candidates: list[dict[str, Any]] = []
            requested = max(1, int(limit))
            while len(candidates) < requested:
                rows = cursor.fetchmany(min(128, requested))
                if not rows:
                    break
                for row in rows:
                    raw = dict(row)
                    if not integrity_check(raw):
                        continue
                    task_only = {
                        key: value
                        for key, value in raw.items()
                        if not key.startswith("linked_")
                        and key != "source_rowid"
                    }
                    candidates.append(_task_row(task_only))
                    if len(candidates) >= requested:
                        break
        return candidates

    def list_live_claimed_v2_requests(
        self,
        *,
        universe_id: str,
        worker_id: str,
        limit: int = 10,
        integrity_check: Callable[[Mapping[str, Any]], bool],
    ) -> list[dict[str, Any]]:
        """Return bounded canonical Request/task views for live running claims.

        This is a read model only.  It cannot claim, renew, transition, or
        project epoch-2 work into the legacy JSON queue.  A
        ``cancel_requested`` task is intentionally excluded so cancellation
        cannot start new materialized work; lifecycle finalization belongs to
        the queue owner.
        """
        clean_universe_id = _required(universe_id, "universe_id")
        clean_worker_id = _required(worker_id, "worker_id")
        requested = min(
            max(1, int(limit)),
            MAX_OPERATIONAL_SCAN_ROWS,
        )
        records: list[dict[str, Any]] = []
        scanned = 0
        with self.connection() as conn:
            cursor = self._v2_integrity_cursor(
                conn,
                universe_id=clean_universe_id,
                pending_only=False,
                live_claimed_by=clean_worker_id,
                live_claim_at=_clock_iso(self._clock),
                include_linked_universe_scope=True,
            )
            while (
                len(records) < requested
                and scanned < MAX_OPERATIONAL_SCAN_ROWS
            ):
                rows = cursor.fetchmany(
                    min(128, MAX_OPERATIONAL_SCAN_ROWS - scanned)
                )
                if not rows:
                    break
                scanned += len(rows)
                for row in rows:
                    raw = dict(row)
                    if not integrity_check(raw):
                        continue
                    inputs = json.loads(str(raw["inputs_json"]))
                    records.append({
                        "request_id": raw["request_id"],
                        "admission_id": raw["admission_id"],
                        "branch_task_id": raw["branch_task_id"],
                        "universe_id": raw["universe_id"],
                        "branch_def_id": raw["branch_def_id"],
                        "request_type": raw["linked_request_type"],
                        "text": raw["linked_request_text"],
                        "branch_id": raw["linked_request_branch_id"] or "",
                        "actor_id": raw["linked_admission_actor_id"],
                        "trigger_source": raw["trigger_source"],
                        "accepted_priority_weight": raw["priority_weight"],
                        "directed_daemon_id": (
                            raw["directed_daemon_id"] or ""
                        ),
                        "pickup_incentive": (
                            inputs.get("pickup_incentive") or ""
                        ),
                        "directed_daemon_instruction": (
                            inputs.get("directed_daemon_instruction") or ""
                        ),
                        "claimed_by": raw["claimed_by"],
                        "claimed_at": raw["claimed_at"],
                        "lease_expires_at": raw["lease_expires_at"],
                        "queued_at": raw["queued_at"],
                    })
                    if len(records) >= requested:
                        break
        if (
            scanned >= MAX_OPERATIONAL_SCAN_ROWS
            and len(records) < requested
        ):
            logger.warning(
                "epoch-2 live-claim integrity scan reached the %s-row "
                "operational bound for universe=%s worker=%s with %s/%s "
                "valid results",
                MAX_OPERATIONAL_SCAN_ROWS,
                clean_universe_id,
                clean_worker_id,
                len(records),
                requested,
            )
        return records

    def list_live_owned_v2_tasks(
        self,
        *,
        universe_id: str,
        worker_id: str,
        limit: int,
        integrity_check: Callable[[Mapping[str, Any]], bool],
    ) -> list[dict[str, Any]]:
        """Return running or cancel-requested tasks owned by one worker."""
        requested = min(max(1, int(limit)), MAX_OPERATIONAL_SCAN_ROWS)
        with self.connection() as conn:
            rows = self._v2_integrity_cursor(
                conn,
                universe_id=_required(universe_id, "universe_id"),
                pending_only=False,
                include_linked_universe_scope=True,
                live_owned_by=_required(worker_id, "worker_id"),
                live_owned_at=_clock_iso(self._clock),
                limit=requested,
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            if not integrity_check(raw):
                continue
            task_only = {
                key: value
                for key, value in raw.items()
                if not key.startswith("linked_")
                and key != "source_rowid"
            }
            tasks.append(_task_row(task_only))
        return tasks

    def read_live_v2_task_for_resume(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        resume_check: Callable[
            [sqlite3.Connection, Mapping[str, Any], str],
            bool,
        ],
    ) -> dict[str, Any] | None:
        """Revalidate one live claim and its authority in one transaction."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                at = _clock_iso(self._clock)
                row = self._v2_integrity_cursor(
                    conn,
                    pending_only=False,
                    branch_task_id=_required(
                        branch_task_id,
                        "branch_task_id",
                    ),
                    live_claimed_by=_required(worker_id, "worker_id"),
                    live_claim_at=at,
                    include_linked_universe_scope=True,
                    limit=1,
                ).fetchone()
                if row is None or not resume_check(conn, dict(row), at):
                    conn.commit()
                    return None
                task_only = {
                    key: value
                    for key, value in dict(row).items()
                    if not key.startswith("linked_")
                    and key != "source_rowid"
                }
                conn.commit()
                return _task_row(task_only)
            except Exception:
                conn.rollback()
                raise

    def has_active_v2_claim(
        self,
        *,
        universe_id: str,
        worker_id: str,
    ) -> bool:
        """Return whether this worker owns running epoch-2 lifecycle work."""
        clean_universe_id = _required(universe_id, "universe_id")
        clean_worker_id = _required(worker_id, "worker_id")
        with self.connection() as conn:
            if not _table_exists(conn, "branch_tasks_v2"):
                return False
            if not _active_claim_schema_ready(conn):
                raise sqlite3.OperationalError(
                    "branch_tasks_v2 claim schema incomplete"
                )
            row = conn.execute(
                """
                SELECT 1
                FROM branch_tasks_v2
                WHERE universe_id = ?
                  AND claimed_by = ?
                  AND status IN ('running', 'cancel_requested')
                  AND disabled = 0
                  AND lease_expires_at IS NOT NULL
                  AND julianday(lease_expires_at) > julianday(?)
                LIMIT 1
                """,
                (
                    clean_universe_id,
                    clean_worker_id,
                    _clock_iso(self._clock),
                ),
            ).fetchone()
        return row is not None

    def read_v2_operational_data(
        self,
        *,
        universe_id: str = "",
        include_unscoped_invalid: bool = False,
        snapshot_hook: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Return bounded active rows plus exact lifecycle aggregates."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            try:
                scope_sql = (
                    _AUTHORITATIVE_UNIVERSE_SCOPE_SQL
                    if universe_id
                    else "1 = 1"
                )
                scope_params = (universe_id,) if universe_id else ()
                lifecycle_rows = conn.execute(
                    """
                    SELECT
                        CASE
                            WHEN t.status IN (
                                'pending', 'running', 'cancel_requested',
                                'cancelled', 'succeeded', 'failed'
                            )
                            THEN t.status
                            ELSE 'unknown'
                        END AS lifecycle_status,
                        COUNT(*) AS row_count,
                        MIN(t.queued_at) AS oldest_queued_at
                    FROM branch_tasks_v2 AS t
                    LEFT JOIN request_admissions AS a
                        ON a.admission_id = t.admission_id
                    LEFT JOIN user_requests AS r
                        ON r.request_id = t.request_id
                    WHERE
                    """
                    + scope_sql
                    + """
                    GROUP BY lifecycle_status
                    """,
                    scope_params,
                ).fetchall()
                unscoped_query = (
                    "SELECT COUNT(*)"
                    if include_unscoped_invalid
                    else "SELECT 1"
                )
                unscoped_row = conn.execute(
                    unscoped_query
                    + """
                    FROM branch_tasks_v2 AS t
                    LEFT JOIN request_admissions AS a
                        ON a.admission_id = t.admission_id
                    LEFT JOIN user_requests AS r
                        ON r.request_id = t.request_id
                    WHERE
                    """
                    + _UNSCOPED_UNIVERSE_SQL
                    + ("" if include_unscoped_invalid else " LIMIT 1")
                ).fetchone()
                unscoped_invalid_count = (
                    int(unscoped_row[0])
                    if include_unscoped_invalid
                    else None
                )
                unscoped_invalid_present = (
                    bool(unscoped_invalid_count)
                    if include_unscoped_invalid
                    else unscoped_row is not None
                )
                if snapshot_hook is not None:
                    snapshot_hook()
                rows = self._v2_integrity_cursor(
                    conn,
                    universe_id=universe_id,
                    pending_only=False,
                    active_only=True,
                    include_disabled=True,
                    include_linked_universe_scope=True,
                    limit=MAX_OPERATIONAL_SCAN_ROWS + 1,
                    rowid_order=True,
                ).fetchall()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        overflow = len(rows) > MAX_OPERATIONAL_SCAN_ROWS
        return {
            "lifecycle": {
                str(row["lifecycle_status"]): {
                    "count": int(row["row_count"]),
                    "oldest_queued_at": str(
                        row["oldest_queued_at"] or ""
                    ),
                }
                for row in lifecycle_rows
            },
            "active_rows": [
                dict(row) for row in rows[:MAX_OPERATIONAL_SCAN_ROWS]
            ],
            "active_scan_limit": MAX_OPERATIONAL_SCAN_ROWS,
            "active_scan_overflow": overflow,
            "integrity_scope_complete": not unscoped_invalid_present,
            **(
                {
                    "unscoped_invalid_count": int(
                        unscoped_invalid_count or 0
                    ),
                }
                if include_unscoped_invalid
                else {}
            ),
        }

    def _v2_integrity_cursor(
        self,
        conn: sqlite3.Connection,
        *,
        universe_id: str = "",
        pending_only: bool,
        exclude_active_automation: bool = False,
        active_only: bool = False,
        include_disabled: bool = False,
        include_linked_universe_scope: bool = False,
        branch_task_id: str = "",
        after_source_rowid: int = 0,
        through_source_rowid: int = 0,
        terminal_before: str = "",
        terminal_only: bool = False,
        uncompacted_only: bool = False,
        live_claimed_by: str = "",
        live_claim_at: str = "",
        live_owned_by: str = "",
        live_owned_at: str = "",
        limit: int | None = None,
        rowid_order: bool = False,
    ) -> sqlite3.Cursor:
        clauses = [] if include_disabled else ["t.disabled = 0"]
        params: list[Any] = []
        if pending_only:
            clauses.append("t.status = 'pending'")
        if exclude_active_automation:
            clauses.append(
                "(t.automation_id IS NULL OR NOT EXISTS ("
                "SELECT 1 FROM branch_tasks_v2 AS active "
                "WHERE active.universe_id = t.universe_id "
                "AND active.automation_id = t.automation_id "
                "AND active.status IN ('running', 'cancel_requested') "
                "AND active.disabled = 0))"
            )
        if active_only:
            clauses.append(
                "t.status NOT IN ('cancelled', 'succeeded', 'failed')"
            )
        if universe_id:
            if include_linked_universe_scope:
                clauses.append(_AUTHORITATIVE_UNIVERSE_SCOPE_SQL)
                params.append(universe_id)
            else:
                clauses.append("t.universe_id = ?")
                params.append(universe_id)
        if branch_task_id:
            clauses.append("t.branch_task_id = ?")
            params.append(branch_task_id)
        if after_source_rowid:
            clauses.append("t.rowid > ?")
            params.append(after_source_rowid)
        if through_source_rowid:
            clauses.append("t.rowid <= ?")
            params.append(through_source_rowid)
        if terminal_only:
            clauses.append(
                "t.status IN ('cancelled', 'succeeded', 'failed')"
            )
        if uncompacted_only:
            clauses.append("a.compacted_at IS NULL")
        if live_claimed_by:
            if not live_claim_at:
                raise ValueError(
                    "live_claim_at is required with live_claimed_by"
                )
            clauses.extend([
                "t.status = 'running'",
                "t.claimed_by = ?",
                "t.lease_expires_at IS NOT NULL",
                "julianday(t.lease_expires_at) > julianday(?)",
            ])
            params.extend([live_claimed_by, live_claim_at])
        if live_owned_by:
            if not live_owned_at:
                raise ValueError(
                    "live_owned_at is required with live_owned_by"
                )
            clauses.extend([
                "t.status IN ('running', 'cancel_requested')",
                "t.claimed_by = ?",
                "t.lease_expires_at IS NOT NULL",
                "julianday(t.lease_expires_at) > julianday(?)",
            ])
            params.extend([live_owned_by, live_owned_at])
        if terminal_before:
            clauses.append("a.terminal_at IS NOT NULL")
            clauses.append("a.terminal_at < ?")
            params.append(terminal_before)
        order = (
            "t.rowid ASC"
            if rowid_order
            else "t.queued_at ASC, t.rowid ASC"
        )
        suffix = ""
        if limit is not None:
            suffix = " LIMIT ?"
            params.append(max(1, int(limit)))
        return conn.execute(
            """
            SELECT
                t.rowid AS source_rowid,
                t.*,
                a.admission_id AS linked_admission_id,
                a.request_id AS linked_admission_request_id,
                a.branch_task_id AS linked_admission_task_id,
                a.universe_id AS linked_admission_universe_id,
                a.trigger_source AS linked_admission_trigger_source,
                a.accepted_priority_weight
                    AS linked_admission_priority_weight,
                a.tenant_id AS linked_admission_tenant_id,
                a.actor_id AS linked_admission_actor_id,
                a.state AS linked_admission_state,
                a.idempotency_key_hash AS linked_admission_key_hash,
                a.body_digest AS linked_admission_body_digest,
                a.body_digest_version AS linked_admission_body_digest_version,
                a.priority_policy_version AS linked_admission_policy_version,
                a.grant_generation AS linked_admission_grant_generation,
                a.receipt_json AS linked_admission_receipt_json,
                a.result_json AS linked_admission_result_json,
                a.terminal_at AS linked_admission_terminal_at,
                a.compacted_at AS linked_admission_compacted_at,
                r.request_id AS linked_request_id,
                r.universe_id AS linked_request_universe_id,
                r.branch_id AS linked_request_branch_id,
                r.user_id AS linked_request_user_id,
                r.request_type AS linked_request_type,
                r.text AS linked_request_text,
                r.preferred_author_id AS linked_request_preferred_author_id,
                r.status AS linked_request_status,
                r.metadata_json AS linked_request_metadata_json,
                d.soul_hash AS linked_directed_daemon_soul_hash,
                d.metadata_json AS linked_directed_daemon_metadata_json,
                EXISTS (
                    SELECT 1
                    FROM branch_tasks_v2_quarantine AS q
                    WHERE q.branch_task_id IS t.branch_task_id
                ) AS linked_quarantine_receipt_exists,
                COALESCE((
                    SELECT q.row_digest
                    FROM branch_tasks_v2_quarantine AS q
                    WHERE q.branch_task_id IS t.branch_task_id
                    ORDER BY q.first_seen_at ASC, q.row_digest ASC
                    LIMIT 1
                ), '') AS linked_quarantine_row_digest,
                COALESCE((
                    SELECT q.reason
                    FROM branch_tasks_v2_quarantine AS q
                    WHERE q.branch_task_id IS t.branch_task_id
                    ORDER BY q.first_seen_at ASC, q.row_digest ASC
                    LIMIT 1
                ), '') AS linked_quarantine_reason,
                COALESCE((
                    SELECT q.first_seen_at
                    FROM branch_tasks_v2_quarantine AS q
                    WHERE q.branch_task_id IS t.branch_task_id
                    ORDER BY q.first_seen_at ASC, q.row_digest ASC
                    LIMIT 1
                ), '') AS linked_quarantine_first_seen_at,
                COALESCE((
                    SELECT q.last_seen_at
                    FROM branch_tasks_v2_quarantine AS q
                    WHERE q.branch_task_id IS t.branch_task_id
                    ORDER BY q.first_seen_at ASC, q.row_digest ASC
                    LIMIT 1
                ), '') AS linked_quarantine_last_seen_at
            FROM branch_tasks_v2 AS t
            LEFT JOIN request_admissions AS a
                ON a.admission_id = t.admission_id
            LEFT JOIN user_requests AS r
                ON r.request_id = t.request_id
            LEFT JOIN author_definitions AS d
                ON d.author_id = CASE
                    WHEN t.directed_daemon_id LIKE 'daemon::%'
                    THEN 'author::' || substr(
                        t.directed_daemon_id,
                        length('daemon::') + 1
                    )
                    ELSE t.directed_daemon_id
                END
            WHERE
            """
            + (" AND ".join(clauses) if clauses else "1 = 1")
            + " ORDER BY "
            + order
            + suffix,
            params,
        )

    def get_v2_task(self, branch_task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM branch_tasks_v2 WHERE branch_task_id = ?",
                (branch_task_id,),
            ).fetchone()
        return _task_row(row) if row is not None else None

    def get_v2_task_actor_id(self, branch_task_id: str) -> str:
        """Return the immutable admission actor bound to a queue task."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT a.actor_id
                FROM branch_tasks_v2 AS t
                JOIN request_admissions AS a
                  ON a.admission_id = t.admission_id
                 AND a.branch_task_id = t.branch_task_id
                WHERE t.branch_task_id = ?
                LIMIT 1
                """,
                (branch_task_id,),
            ).fetchone()
        return str(row["actor_id"] or "") if row is not None else ""

    def claim_v2_task(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        queue_protocol_version: int,
        capabilities: Iterable[str],
        lease_seconds: int = 90,
        claim_check: Callable[
            [sqlite3.Connection, Mapping[str, Any], str],
            bool,
        ],
    ) -> dict[str, Any] | None:
        if queue_protocol_version != QUEUE_PROTOCOL_VERSION:
            return None
        if OPERATOR_CAPABILITY not in set(capabilities):
            return None
        lease_seconds = _bounded_lease_seconds(lease_seconds)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                claimed_at = _clock_iso(self._clock)
                lease_expires_at = _add_seconds(
                    claimed_at,
                    lease_seconds,
                )
                row = self._v2_integrity_cursor(
                    conn,
                    pending_only=True,
                    branch_task_id=branch_task_id,
                    limit=1,
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                if (
                    row["queue_epoch"] != QUEUE_EPOCH
                    or row["protocol_version"] != QUEUE_PROTOCOL_VERSION
                ):
                    conn.commit()
                    return None
                if not claim_check(conn, dict(row), claimed_at):
                    conn.commit()
                    return None
                cursor = conn.execute(
                    """
                    UPDATE branch_tasks_v2
                    SET status = 'running', claimed_by = ?,
                        claimed_at = ?, heartbeat_at = ?,
                        lease_expires_at = ?
                    WHERE branch_task_id = ?
                      AND status = 'pending' AND disabled = 0
                    """,
                    (
                        _required(worker_id, "worker_id"),
                        claimed_at,
                        claimed_at,
                        lease_expires_at or claimed_at,
                        branch_task_id,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return None
                conn.execute(
                    """
                    UPDATE user_requests
                    SET status = 'running', updated_at = ?
                    WHERE request_id = ?
                    """,
                    (_epoch_seconds(claimed_at), row["request_id"]),
                )
                self._append_task_event(
                    conn,
                    row,
                    event_type="claimed",
                    event_at=claimed_at,
                    detail={"worker_id": worker_id},
                )
                claimed = conn.execute(
                    "SELECT * FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                conn.commit()
                return _task_row(claimed)
            except Exception:
                conn.rollback()
                raise

    def heartbeat_v2_task(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 90,
    ) -> dict[str, Any] | None:
        lease_seconds = _bounded_lease_seconds(lease_seconds)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                at = _clock_iso(self._clock)
                lease_expires_at = _add_seconds(at, lease_seconds)
                cursor = conn.execute(
                    """
                    UPDATE branch_tasks_v2
                    SET heartbeat_at = ?, lease_expires_at = ?
                    WHERE branch_task_id = ?
                      AND status IN ('running', 'cancel_requested')
                      AND claimed_by = ? AND disabled = 0
                      AND julianday(lease_expires_at) > julianday(?)
                    """,
                    (
                        _required(at, "at"),
                        _required(lease_expires_at, "lease_expires_at"),
                        branch_task_id,
                        _required(worker_id, "worker_id"),
                        at,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.commit()
                    return None
                updated = conn.execute(
                    "SELECT * FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                conn.commit()
                return _task_row(updated)
            except Exception:
                conn.rollback()
                raise

    def release_v2_task_claim(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        claimed_at: str,
        reason: str,
    ) -> bool:
        """Return an exact live claim to pending without changing attempt identity."""

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                at = _clock_iso(self._clock)
                row = conn.execute(
                    "SELECT * FROM branch_tasks_v2 WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return False
                cursor = conn.execute(
                    """
                    UPDATE branch_tasks_v2
                    SET status = 'pending', claimed_by = '', claimed_at = NULL,
                        heartbeat_at = NULL, lease_expires_at = NULL
                    WHERE branch_task_id = ? AND status = 'running'
                      AND claimed_by = ? AND claimed_at = ?
                      AND disabled = 0
                    """,
                    (branch_task_id, worker_id, claimed_at),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute(
                    "UPDATE user_requests SET status = 'pending', updated_at = ? "
                    "WHERE request_id = ?",
                    (_epoch_seconds(at), row["request_id"]),
                )
                self._append_task_event(
                    conn,
                    row,
                    event_type="authority_held",
                    event_at=at,
                    detail={"reason": _required(reason, "reason")},
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def request_v2_cancel(
        self,
        branch_task_id: str,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(branch_task_id)
                updated = self._request_v2_cancel_locked(conn, row)
                conn.commit()
                return updated
            except Exception:
                conn.rollback()
                raise

    def cancel_pending_v2_task_if_stale(
        self,
        branch_task_id: str,
        *,
        cutoff: datetime,
        expected_queued_at: str,
        expected_grant_generation: int,
        expected_body_digest: str,
        expected_row_digest: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Cancel one exact reviewed stale task under a write-lock CAS."""
        if reason != "stale_awaiting_compatible_capacity_retired_fleet":
            raise ValueError("unsupported stale task cancellation reason")
        cutoff_value = cutoff
        if cutoff_value.tzinfo is None:
            cutoff_value = cutoff_value.replace(tzinfo=timezone.utc)
        cutoff_value = cutoff_value.astimezone(timezone.utc)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._v2_integrity_cursor(
                    conn,
                    pending_only=False,
                    include_disabled=True,
                    branch_task_id=branch_task_id,
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                evidence = dict(row)
                queued_at = _parse_iso_datetime(
                    expected_queued_at,
                    name="expected_queued_at",
                )
                if not all((
                    evidence["status"] == "pending",
                    evidence["disabled"] == 0,
                    evidence["queued_at"] == expected_queued_at,
                    int(evidence["linked_admission_grant_generation"])
                    == expected_grant_generation,
                    evidence["linked_admission_body_digest"]
                    == expected_body_digest,
                    _stale_task_integrity_digest(evidence)
                    == expected_row_digest,
                    queued_at <= cutoff_value,
                )):
                    conn.commit()
                    return None
                updated = self._request_v2_cancel_locked(
                    conn,
                    row,
                    detail={"reason": reason},
                )
                conn.commit()
                return updated
            except Exception:
                conn.rollback()
                raise

    def _request_v2_cancel_locked(
        self,
        conn: sqlite3.Connection,
        row: Mapping[str, Any],
        *,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        at = _clock_iso(self._clock)
        if row["status"] in TERMINAL_STATUSES:
            return _task_row(row)
        if row["status"] == "cancel_requested":
            return _task_row(row)
        new_status = (
            "cancelled" if row["status"] == "pending" else "cancel_requested"
        )
        terminal_at = at if new_status == "cancelled" else None
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET status = ?, terminal_at = COALESCE(?, terminal_at)
            WHERE branch_task_id = ?
            """,
            (new_status, terminal_at, row["branch_task_id"]),
        )
        if terminal_at is not None:
            conn.execute(
                """
                UPDATE request_admissions
                SET terminal_at = ?, updated_at = ?
                WHERE admission_id = ?
                """,
                (terminal_at, at, row["admission_id"]),
            )
        conn.execute(
            """
            UPDATE user_requests
            SET status = ?, updated_at = ?
            WHERE request_id = ?
            """,
            (new_status, _epoch_seconds(at), row["request_id"]),
        )
        self._append_task_event(
            conn,
            row,
            event_type=new_status,
            event_at=at,
            detail=dict(detail or {}),
        )
        updated = conn.execute(
            "SELECT * FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (row["branch_task_id"],),
        ).fetchone()
        return _task_row(updated)

    def list_expired_v2_tasks(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if not _table_exists(conn, "branch_tasks_v2"):
                return []
            if not _recovery_schema_ready(conn):
                raise sqlite3.OperationalError(
                    "branch_tasks_v2 recovery schema incomplete"
                )
            now = _clock_iso(self._clock)
            rows = conn.execute(
                """
                SELECT * FROM branch_tasks_v2
                WHERE status IN ('running', 'cancel_requested')
                  AND disabled = 0
                  AND lease_expires_at IS NOT NULL
                  AND julianday(lease_expires_at) <= julianday(?)
                ORDER BY queued_at, branch_task_id
                """,
                (now,),
            ).fetchall()
        # Keep the guard snapshot byte-for-byte comparable with the row read
        # again under BEGIN IMMEDIATE.  Normalizing JSON/bools here makes the
        # later CAS reject every unchanged row.
        return [dict(row) for row in rows]

    def recover_expired_v2_tasks(
        self,
        *,
        approved_rows: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if not _table_exists(conn, "branch_tasks_v2"):
                return []
            if not _recovery_schema_ready(conn):
                raise sqlite3.OperationalError(
                    "branch_tasks_v2 recovery schema incomplete"
                )
            now = _clock_iso(self._clock)
            expired = conn.execute(
                """
                SELECT 1
                FROM branch_tasks_v2
                WHERE status IN ('running', 'cancel_requested')
                  AND disabled = 0
                  AND lease_expires_at IS NOT NULL
                  AND julianday(lease_expires_at) <= julianday(?)
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if expired is None:
                return []
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not _recovery_schema_ready(conn):
                    raise sqlite3.OperationalError(
                        "branch_tasks_v2 recovery schema incomplete"
                    )
                now = _clock_iso(self._clock)
                rows = conn.execute(
                    """
                    SELECT * FROM branch_tasks_v2
                    WHERE status IN ('running', 'cancel_requested')
                      AND disabled = 0
                      AND lease_expires_at IS NOT NULL
                      AND julianday(lease_expires_at) <= julianday(?)
                    ORDER BY queued_at, branch_task_id
                    """,
                    (now,),
                ).fetchall()
                recovered: list[dict[str, Any]] = []
                for row in rows:
                    if approved_rows is not None:
                        expected = approved_rows.get(
                            str(row["branch_task_id"])
                        )
                        if expected is None or dict(row) != dict(expected):
                            continue
                    cancelled = row["status"] == "cancel_requested"
                    new_status = "cancelled" if cancelled else "pending"
                    cursor = conn.execute(
                        """
                        UPDATE branch_tasks_v2
                        SET status = ?, claimed_by = '',
                            claimed_at = NULL, heartbeat_at = NULL,
                            lease_expires_at = NULL,
                            terminal_at = COALESCE(?, terminal_at)
                        WHERE branch_task_id = ? AND status = ?
                        """,
                        (
                            new_status,
                            now if cancelled else None,
                            row["branch_task_id"],
                            row["status"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                    if cancelled:
                        conn.execute(
                            """
                            UPDATE request_admissions
                            SET terminal_at = ?, updated_at = ?
                            WHERE admission_id = ?
                            """,
                            (now, now, row["admission_id"]),
                        )
                    conn.execute(
                        """
                        UPDATE user_requests
                        SET status = ?, updated_at = ?
                        WHERE request_id = ?
                        """,
                        (
                            new_status,
                            _epoch_seconds(now),
                            row["request_id"],
                        ),
                    )
                    self._append_task_event(
                        conn,
                        row,
                        event_type=(
                            "cancelled" if cancelled else "recovered"
                        ),
                        event_at=now,
                        detail={
                            "reason": (
                                "lease_expired_after_cancel"
                                if cancelled
                                else "lease_expired"
                            )
                        },
                    )
                    updated = conn.execute(
                        "SELECT * FROM branch_tasks_v2 "
                        "WHERE branch_task_id = ?",
                        (row["branch_task_id"],),
                    ).fetchone()
                    recovered.append(_task_row(updated))
                conn.commit()
                return recovered
            except Exception:
                conn.rollback()
                raise

    def transition_task(
        self,
        branch_task_id: str,
        *,
        expected_statuses: set[str],
        new_status: str,
        at: str = "",
        detail: Mapping[str, Any] | None = None,
        worker_id: str = "",
        cancel_wins: bool = False,
    ) -> dict[str, Any]:
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        if new_status not in {
            "pending",
            "running",
            "cancel_requested",
            "cancelled",
            "succeeded",
            "failed",
        }:
            raise ValueError(f"invalid v2 task status: {new_status}")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                effective_at = (
                    _clock_iso(self._clock)
                    if worker_id
                    else _required(at, "at")
                )
                row = conn.execute(
                    "SELECT * FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(branch_task_id)
                if row["status"] not in expected_statuses:
                    raise ValueError(
                        f"expected {sorted(expected_statuses)}, "
                        f"found {row['status']}"
                    )
                if worker_id and row["claimed_by"] != worker_id:
                    raise PermissionError("branch_task_claim_owner_mismatch")
                if (
                    worker_id
                    and row["lease_expires_at"]
                    and _epoch_seconds(effective_at)
                    >= _epoch_seconds(row["lease_expires_at"])
                ):
                    raise PermissionError("branch_task_lease_expired")
                effective_status = new_status
                effective_detail = dict(detail or {})
                if cancel_wins and row["status"] == "cancel_requested":
                    effective_status = "cancelled"
                    effective_detail["reason"] = "cancel_requested"
                terminal_at = (
                    effective_at
                    if effective_status in TERMINAL_STATUSES
                    else None
                )
                conn.execute(
                    """
                    UPDATE branch_tasks_v2
                    SET status = ?, terminal_at = COALESCE(?, terminal_at),
                        detail_json = ?
                    WHERE branch_task_id = ?
                    """,
                    (
                        effective_status,
                        terminal_at,
                        _json(effective_detail),
                        branch_task_id,
                    ),
                )
                if terminal_at is not None:
                    conn.execute(
                        """
                        UPDATE request_admissions
                        SET terminal_at = ?, updated_at = ?
                        WHERE admission_id = ?
                        """,
                        (terminal_at, effective_at, row["admission_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE user_requests
                        SET status = ?, updated_at = ?
                        WHERE request_id = ?
                        """,
                        (
                            effective_status,
                            _epoch_seconds(effective_at),
                            row["request_id"],
                        ),
                    )
                self._append_task_event(
                    conn,
                    row,
                    event_type=effective_status,
                    event_at=effective_at,
                    detail=effective_detail,
                )
                updated = conn.execute(
                    "SELECT * FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                conn.commit()
                return _task_row(updated)
            except Exception:
                conn.rollback()
                raise

    def quarantine_task(
        self,
        branch_task_id: str,
        *,
        reason: str,
        observed_at: str,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT rowid AS source_rowid, * "
                    "FROM branch_tasks_v2 "
                    "WHERE branch_task_id = ?",
                    (branch_task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(branch_task_id)
                receipt = self._quarantine_task_in_transaction(
                    conn,
                    row,
                    reason=reason,
                    observed_at=observed_at,
                )
                conn.commit()
                return receipt
            except Exception:
                conn.rollback()
                raise

    def maintain_v2_quarantine(
        self,
        *,
        classifier: Callable[[Mapping[str, Any]], str | None],
        limit: int = 1000,
        fault_injector: Callable[[str, sqlite3.Connection], None] | None = None,
    ) -> dict[str, Any]:
        """Classify and quarantine invalid rows in one maintenance transaction."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                observed_at = _clock_iso(self._clock)
                scan_limit = min(
                    MAX_QUARANTINE_SCAN_ROWS,
                    max(1, int(limit)),
                )
                state = conn.execute(
                    """
                    SELECT last_source_rowid, cycle_high_rowid
                    FROM branch_tasks_v2_maintenance_state
                    WHERE maintenance_name = 'quarantine'
                    """
                ).fetchone()
                after_source_rowid = (
                    int(state["last_source_rowid"])
                    if state is not None
                    else 0
                )
                cycle_high_rowid = (
                    int(state["cycle_high_rowid"])
                    if state is not None
                    else 0
                )
                if cycle_high_rowid <= 0:
                    high_row = conn.execute(
                        """
                        SELECT COALESCE(MAX(rowid), 0) AS high_rowid
                        FROM branch_tasks_v2
                        """
                    ).fetchone()
                    cycle_high_rowid = int(high_row["high_rowid"])
                    after_source_rowid = 0
                rows = self._v2_integrity_cursor(
                    conn,
                    pending_only=False,
                    include_disabled=True,
                    after_source_rowid=after_source_rowid,
                    through_source_rowid=cycle_high_rowid,
                    limit=scan_limit,
                    rowid_order=True,
                ).fetchall()
                scanned = 0
                receipts: list[dict[str, Any]] = []
                for integrity_row in rows:
                    scanned += 1
                    if (
                        integrity_row["disabled"]
                        and integrity_row[
                            "linked_quarantine_receipt_exists"
                        ]
                    ):
                        continue
                    reason = classifier(dict(integrity_row))
                    if not reason:
                        continue
                    source = conn.execute(
                        """
                        SELECT rowid AS source_rowid, *
                        FROM branch_tasks_v2
                        WHERE rowid = ?
                        """,
                        (integrity_row["source_rowid"],),
                    ).fetchone()
                    if source is None:  # pragma: no cover - same write txn
                        raise RuntimeError("quarantine_source_missing")
                    receipts.append(
                        self._quarantine_task_in_transaction(
                            conn,
                            source,
                            reason=reason,
                            observed_at=observed_at,
                            fault_injector=fault_injector,
                        )
                    )
                cycle_complete = (
                    not rows
                    or len(rows) < scan_limit
                    or int(rows[-1]["source_rowid"]) >= cycle_high_rowid
                )
                last_source_rowid = (
                    0
                    if cycle_complete
                    else int(rows[-1]["source_rowid"])
                )
                next_cycle_high_rowid = (
                    0 if cycle_complete else cycle_high_rowid
                )
                conn.execute(
                    """
                    INSERT INTO branch_tasks_v2_maintenance_state (
                        maintenance_name, last_source_rowid,
                        cycle_high_rowid
                    ) VALUES ('quarantine', ?, ?)
                    ON CONFLICT(maintenance_name) DO UPDATE SET
                        last_source_rowid = excluded.last_source_rowid,
                        cycle_high_rowid = excluded.cycle_high_rowid
                    """,
                    (last_source_rowid, next_cycle_high_rowid),
                )
                conn.commit()
                return {
                    "scanned": scanned,
                    "receipts": receipts,
                }
            except Exception:
                conn.rollback()
                raise

    def _quarantine_task_in_transaction(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        reason: str,
        observed_at: str,
        fault_injector: Callable[[str, sqlite3.Connection], None] | None = None,
    ) -> dict[str, Any]:
        clean_reason = _required(reason, "reason")
        if clean_reason not in QUARANTINE_REASONS:
            raise ValueError("unsupported quarantine reason")
        snapshot = dict(row)
        existing_for_source = None
        if snapshot.get("disabled") == 1:
            existing_for_source = conn.execute(
                """
                SELECT
                    row_digest, branch_task_id, reason,
                    first_seen_at, last_seen_at
                FROM branch_tasks_v2_quarantine
                WHERE branch_task_id IS ?
                ORDER BY first_seen_at ASC
                LIMIT 1
                """,
                (snapshot.get("branch_task_id"),),
            ).fetchone()
        if existing_for_source is not None:
            conn.execute(
                """
                UPDATE branch_tasks_v2_quarantine
                SET last_seen_at = ?, seen_count = seen_count + 1
                WHERE row_digest = ?
                """,
                (observed_at, existing_for_source["row_digest"]),
            )
            replayed = dict(existing_for_source)
            replayed["last_seen_at"] = observed_at
            return replayed
        digest = _quarantine_row_digest(snapshot)
        source_rowid = int(snapshot.pop("source_rowid"))
        branch_task_id = _quarantine_task_identifier(
            snapshot.get("branch_task_id"),
            digest=digest,
        )
        if branch_task_id != snapshot.get("branch_task_id"):
            original_task_id = snapshot.get("branch_task_id")
            conn.execute(
                """
                UPDATE branch_tasks_v2
                SET branch_task_id = ?
                WHERE rowid = ?
                """,
                (branch_task_id, source_rowid),
            )
            conn.execute(
                """
                UPDATE request_admissions
                SET branch_task_id = ?
                WHERE branch_task_id IS ?
                """,
                (branch_task_id, original_task_id),
            )
            conn.execute(
                """
                UPDATE request_admission_events
                SET branch_task_id = ?
                WHERE branch_task_id IS ?
                """,
                (branch_task_id, original_task_id),
            )
            snapshot["branch_task_id"] = branch_task_id
        receipt_universe_id = _quarantine_universe_identifier(
            snapshot.get("universe_id"),
            digest=digest,
        )
        receipt_snapshot = _quarantine_receipt_snapshot(
            snapshot,
            digest=digest,
        )
        existing_receipt = conn.execute(
            """
            SELECT row_digest
            FROM branch_tasks_v2_quarantine
            WHERE row_digest = ?
            """,
            (digest,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO branch_tasks_v2_quarantine (
                row_digest, branch_task_id, universe_id, reason,
                row_json, first_seen_at, last_seen_at, seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(row_digest) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                seen_count = branch_tasks_v2_quarantine.seen_count + 1
            """,
            (
                digest,
                branch_task_id,
                receipt_universe_id,
                clean_reason,
                _json(receipt_snapshot),
                observed_at,
                observed_at,
            ),
        )
        _inject(fault_injector, "quarantine_receipt_written", conn)
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET disabled = 1, quarantine_reason = ?
            WHERE rowid = ?
            """,
            (clean_reason, source_rowid),
        )
        _inject(fault_injector, "quarantine_source_disabled", conn)
        linked = conn.execute(
            """
            SELECT 1
            FROM request_admissions AS a
            JOIN user_requests AS r
              ON r.request_id = a.request_id
             AND r.universe_id = a.universe_id
            WHERE a.admission_id = ?
              AND a.request_id = ?
              AND a.branch_task_id = ?
              AND a.universe_id = ?
            """,
            (
                snapshot["admission_id"],
                snapshot["request_id"],
                snapshot["branch_task_id"],
                snapshot["universe_id"],
            ),
        ).fetchone()
        if (
            existing_receipt is None
            and linked is not None
            and _quarantine_event_identity_is_safe(snapshot)
        ):
            self._append_task_event(
                conn,
                snapshot,
                event_type="quarantined",
                event_at=observed_at,
                detail={"reason": clean_reason, "row_digest": digest},
            )
        receipt = conn.execute(
            """
            SELECT
                row_digest, branch_task_id, reason,
                first_seen_at, last_seen_at
            FROM branch_tasks_v2_quarantine
            WHERE row_digest = ?
            """,
            (digest,),
        ).fetchone()
        if receipt is None:  # pragma: no cover - guarded by the insert
            raise RuntimeError("quarantine_receipt_missing")
        return dict(receipt)

    def compact_terminal_details(
        self,
        *,
        terminal_before: str,
        compacted_at: str,
        classifier: Callable[[Mapping[str, Any]], str | None],
        limit: int = MAX_QUARANTINE_SCAN_ROWS,
    ) -> int:
        """Compact terminal private detail while retaining replay tombstones."""

        compaction_time = _parse_iso_datetime(
            compacted_at,
            name="compacted_at",
        )
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._v2_integrity_cursor(
                    conn,
                    pending_only=False,
                    include_disabled=True,
                    terminal_before=terminal_before,
                    terminal_only=True,
                    uncompacted_only=True,
                    limit=min(
                        MAX_QUARANTINE_SCAN_ROWS,
                        max(1, int(limit)),
                    ),
                    rowid_order=True,
                ).fetchall()
                compacted = 0
                observed_at = _clock_iso(self._clock)
                for row in rows:
                    has_quarantine_receipt = bool(
                        row["disabled"]
                        and row["linked_quarantine_receipt_exists"]
                    )
                    reason = None
                    if not has_quarantine_receipt:
                        reason = classifier(dict(row))
                    try:
                        task_terminal = _parse_iso_datetime(
                            row["terminal_at"],
                            name="terminal_at",
                        )
                        admission_terminal = _parse_iso_datetime(
                            row["linked_admission_terminal_at"],
                            name="admission terminal_at",
                        )
                    except ValueError:
                        reason = reason or "invalid_operator_admission"
                        task_terminal = admission_terminal = None
                    if (
                        reason is None
                        and task_terminal != admission_terminal
                    ):
                        reason = "invalid_operator_admission"
                    if reason:
                        source = conn.execute(
                            """
                            SELECT rowid AS source_rowid, *
                            FROM branch_tasks_v2
                            WHERE rowid = ?
                            """,
                            (row["source_rowid"],),
                        ).fetchone()
                        if source is None:  # pragma: no cover - same txn
                            raise RuntimeError("compaction_source_missing")
                        self._quarantine_task_in_transaction(
                            conn,
                            source,
                            reason=reason,
                            observed_at=observed_at,
                        )
                        continue
                    if (
                        task_terminal is None
                        or admission_terminal is None
                        or compaction_time < task_terminal
                        or compaction_time < admission_terminal
                    ):
                        raise ValueError(
                            "compacted_at must not precede terminal_at"
                        )
                    tombstone = {
                        "admission_id": row["admission_id"],
                        "branch_task_id": row["branch_task_id"],
                        "request_id": row["request_id"],
                        "request_status": row["status"],
                        "universe_id": row["universe_id"],
                    }
                    conn.execute(
                        """
                        UPDATE user_requests
                        SET text = '', preferred_author_id = NULL,
                            metadata_json = ?
                        WHERE request_id = ?
                        """,
                        (
                            _json({
                                "compacted": True,
                                "queue_epoch": QUEUE_EPOCH,
                            }),
                            row["request_id"],
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE request_admissions
                        SET receipt_json = '{}', result_json = ?,
                            compacted_at = ?, updated_at = ?
                        WHERE admission_id = ?
                        """,
                        (
                            _json(tombstone),
                            compacted_at,
                            compacted_at,
                            row["admission_id"],
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE request_admission_events
                        SET detail_json = '{}'
                        WHERE admission_id = ?
                        """,
                        (row["admission_id"],),
                    )
                    conn.execute(
                        """
                        UPDATE branch_tasks_v2
                        SET inputs_json = '{}', detail_json = '{}',
                            directed_daemon_id = '', claimed_by = ''
                        WHERE branch_task_id = ?
                        """,
                        (row["branch_task_id"],),
                    )
                    conn.execute(
                        """
                        UPDATE branch_tasks_v2_quarantine
                        SET row_json = '{}'
                        WHERE branch_task_id = ?
                        """,
                        (row["branch_task_id"],),
                    )
                    compacted += 1
                conn.commit()
                return compacted
            except Exception:
                conn.rollback()
                raise

    def delete_universe(self, universe_id: str) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM user_requests "
                        "WHERE universe_id = ?",
                        (universe_id,),
                    ).fetchone()[0]
                )
                conn.execute(
                    "DELETE FROM user_requests WHERE universe_id = ?",
                    (universe_id,),
                )
                conn.execute(
                    "DELETE FROM request_admission_rollouts "
                    "WHERE universe_id = ?",
                    (universe_id,),
                )
                conn.commit()
                return count
            except Exception:
                conn.rollback()
                raise

    def _append_task_event(
        self,
        conn: sqlite3.Connection,
        task_row: sqlite3.Row,
        *,
        event_type: str,
        event_at: str,
        detail: Mapping[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO request_admission_events (
                event_id, admission_id, request_id, branch_task_id,
                event_type, event_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._id_factory("evt"),
                task_row["admission_id"],
                task_row["request_id"],
                task_row["branch_task_id"],
                event_type,
                event_at,
                _json(dict(detail)),
            ),
        )


def migrate_request_admission_schema(conn: sqlite3.Connection) -> None:
    """Create the epoch-2 schema on the active pre-traffic DB connection."""

    conn.executescript(_SCHEMA)
    # The shared epoch-2 claim guard (_transaction_allows_epoch2_lifecycle) reads
    # background_branch_authority_owners on THIS connection to avoid double-claiming a
    # task the assigned-queue consumer holds. That table is created LAZILY by the
    # consumer's own store only when the consumer is enabled; the guard is
    # missing-table-safe (treats a missing table as "no owner"), so we deliberately do
    # NOT create the background-authority schema here — a dark consumer leaves ZERO
    # schema behind (Codex #6, #2516).
    task_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(branch_tasks_v2)")
    }
    if "lease_expires_at" not in task_columns:
        conn.execute(
            "ALTER TABLE branch_tasks_v2 ADD COLUMN lease_expires_at TEXT"
        )
    for column, declaration in (
        ("automation_id", "TEXT"),
        (
            "automation_activation_epoch",
            "INTEGER CHECK (automation_activation_epoch IS NULL "
            "OR automation_activation_epoch >= 0)",
        ),
        (
            "automation_executor_class",
            "TEXT CHECK (automation_executor_class IS NULL "
            "OR automation_executor_class IN ('tray', 'cloud'))",
        ),
        (
            "automation_subject_kind",
            "TEXT CHECK (automation_subject_kind IS NULL OR "
            "automation_subject_kind IN ('branch_version', 'agent_runtime_manifest'))",
        ),
        ("automation_subject_ref", "TEXT"),
        ("automation_subject_digest", "TEXT"),
        ("automation_branch_version", "TEXT"),
        ("automation_lease_id", "TEXT"),
    ):
        if column not in task_columns:
            conn.execute(
                f"ALTER TABLE branch_tasks_v2 ADD COLUMN {column} "
                f"{declaration}"
            )
    maintenance_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(branch_tasks_v2_maintenance_state)"
        )
    }
    if "cycle_high_rowid" not in maintenance_columns:
        conn.execute(
            "ALTER TABLE branch_tasks_v2_maintenance_state "
            "ADD COLUMN cycle_high_rowid INTEGER NOT NULL DEFAULT 0 "
            "CHECK (cycle_high_rowid >= 0)"
        )


def _public_result(
    *,
    universe_id: str,
    admission_id: str,
    request_id: str,
    branch_task_id: str,
    trigger_source: str,
    accepted_priority_weight: float,
    policy_version: str,
    directed_daemon_id: str,
    idempotent_replay: bool,
) -> dict[str, Any]:
    return {
        "universe_id": universe_id,
        "admission_id": admission_id,
        "admission_state": "committed",
        "request_id": request_id,
        "branch_task_id": branch_task_id,
        "request_status": "pending",
        "trigger_source": trigger_source,
        "accepted_priority_weight": float(accepted_priority_weight),
        "priority_weight_cap": PRIORITY_WEIGHT_CAP,
        "priority_policy_version": policy_version,
        "idempotent_replay": idempotent_replay,
        "directed_daemon_id": directed_daemon_id,
    }


def _task_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["inputs"] = json.loads(result.pop("inputs_json"))
    result["detail"] = json.loads(result.pop("detail_json"))
    result["disabled"] = bool(result["disabled"])
    return result


_BRANCH_TASK_ID_RE = re.compile(
    r"^(?:bt2_[0-9a-f]{32}|quarantined-task-[0-9a-f]{32})$"
)
_ADMISSION_ID_RE = re.compile(r"^adm_[0-9a-f]{32}$")
_REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{32}$")
_UNIVERSE_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_TRIGGER_SOURCES = frozenset({
    "operator_request",
    "user_request",
    "owner_queued",
})
_TASK_STATUSES = frozenset({
    "pending",
    "running",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
})


def _quarantine_row_digest(row: Mapping[str, Any]) -> str:
    """Hash the canonical observed row without mutable quarantine metadata."""
    digest_snapshot = {
        key: value
        for key, value in row.items()
        if not str(key).startswith("linked_")
    }
    digest_snapshot.pop("disabled", None)
    digest_snapshot.pop("quarantine_reason", None)
    digest_snapshot.pop("source_rowid", None)
    canonical = _canonicalize_quarantine_value(digest_snapshot)
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _stale_task_integrity_digest(row: Mapping[str, Any]) -> str:
    """Bind a stale-task review to the complete joined integrity evidence."""
    digest_snapshot = dict(row)
    digest_snapshot.pop("source_rowid", None)
    canonical = _canonicalize_quarantine_value(digest_snapshot)
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _quarantine_receipt_snapshot(
    task: Mapping[str, Any],
    *,
    digest: str,
) -> dict[str, Any]:
    """Retain only non-private fields needed to diagnose queue compatibility."""

    return {
        "branch_task_id": _quarantine_task_identifier(
            task.get("branch_task_id"),
            digest=digest,
        ),
        "universe_id": _quarantine_universe_identifier(
            task.get("universe_id"),
            digest=digest,
        ),
        "trigger_source": (
            task["trigger_source"]
            if task.get("trigger_source") in _TRIGGER_SOURCES
            else _opaque_quarantine_diagnostic(task.get("trigger_source"))
        ),
        "status": (
            task["status"]
            if task.get("status") in _TASK_STATUSES
            else _opaque_quarantine_diagnostic(task.get("status"))
        ),
        "queue_epoch": _bounded_quarantine_integer(
            task.get("queue_epoch")
        ),
        "protocol_version": _bounded_quarantine_integer(
            task.get("protocol_version")
        ),
    }


def _canonicalize_quarantine_value(value: Any) -> Any:
    """Totalize raw SQLite values without persisting opaque corrupt bytes."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            label = "nan"
        else:
            label = "positive_infinity" if value > 0 else "negative_infinity"
        return {"storage_class": "real", "non_finite": label}
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "storage_class": "blob",
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_quarantine_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_quarantine_value(item) for item in value]
    type_name = type(value).__name__
    return {
        "storage_class": "unsupported",
        "type_sha256": hashlib.sha256(
            type_name.encode("utf-8")
        ).hexdigest(),
    }


def _quarantine_task_identifier(value: Any, *, digest: str) -> str:
    if isinstance(value, str) and _BRANCH_TASK_ID_RE.fullmatch(value):
        return value
    return f"quarantined-task-{digest[:32]}"


def _quarantine_universe_identifier(value: Any, *, digest: str) -> str:
    if isinstance(value, str) and _UNIVERSE_ID_RE.fullmatch(value):
        return value
    return f"unknown-universe-{digest[:32]}"


def _opaque_quarantine_diagnostic(value: Any) -> Any:
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="surrogatepass")
        return {
            "storage_class": "text",
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return _canonicalize_quarantine_value(value)


def _bounded_quarantine_integer(value: Any) -> Any:
    if type(value) is int and -(2**63) <= value < 2**63:
        return value
    return _opaque_quarantine_diagnostic(value)


def _quarantine_event_identity_is_safe(task: Mapping[str, Any]) -> bool:
    return bool(
        isinstance(task.get("admission_id"), str)
        and _ADMISSION_ID_RE.fullmatch(task["admission_id"])
        and isinstance(task.get("request_id"), str)
        and _REQUEST_ID_RE.fullmatch(task["request_id"])
        and isinstance(task.get("branch_task_id"), str)
        and _BRANCH_TASK_ID_RE.fullmatch(task["branch_task_id"])
        and isinstance(task.get("universe_id"), str)
        and _UNIVERSE_ID_RE.fullmatch(task["universe_id"])
    )


def _random_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clock_iso(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_lease_seconds(value: int) -> int:
    seconds = int(value)
    if not 1 <= seconds <= MAX_LEASE_SECONDS:
        raise ValueError(
            f"lease_seconds must be between 1 and {MAX_LEASE_SECONDS}"
        )
    return seconds


def _add_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _epoch_seconds(timestamp: str) -> float:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _inject(
    injector: Callable[[str, sqlite3.Connection], Any] | None,
    step: str,
    conn: sqlite3.Connection,
) -> None:
    if injector is not None:
        injector(step, conn)


def _looks_like_random_id_collision(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc).lower()
    return (
        "primary key" in message
        or "request_admissions.admission_id" in message
        or "branch_tasks_v2.branch_task_id" in message
        or "request_admission_events.event_id" in message
        or "user_requests.request_id" in message
    )
