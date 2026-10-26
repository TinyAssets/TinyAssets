"""Idempotency helpers for code-node side effects.

Code nodes that perform side effects (wiki writes, external HTTP calls,
durable tool effects) must key those effects by ``(run_id, step_id)``
so retry on resume is safe. This module provides the ``@idempotent_by_step``
decorator and the low-level ``IdempotencyStore`` it uses.

Usage in a code node::

    from tinyassets.idempotency import idempotent_by_step

    @idempotent_by_step
    def write_to_external(run_id: str, step_id: str, *, payload: dict) -> dict:
        # Only called once per (run_id, step_id) pair.
        return _do_the_write(payload)

The decorator injects ``run_id`` and ``step_id`` from the caller's ``state``
dict (keys ``_run_id`` and ``_step_id``). If the (run_id, step_id) pair has
already been executed the stored result is returned without calling the
wrapped function again.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL = timedelta(days=30)
EFFECT_IDENTITY_MODE_ENV = "TINYASSETS_EFFECT_IDENTITY_MODE"


def derive_effect_key(
    *,
    goal_id: str,
    schedule_period: str,
    item_fingerprint: str,
) -> str:
    """Derive the stable outbound effect identity from durable system fields."""
    identity = {
        "goal_id": goal_id.strip(),
        "schedule_period": schedule_period.strip(),
        "item_fingerprint": item_fingerprint.strip(),
    }
    for field, value in identity.items():
        if not value:
            raise ValueError(f"{field} must be non-empty")
    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"effect:v1:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True)
class EffectorIdentity:
    mode: str
    active_key: str
    caller_hint: str
    system_key: str | None
    parity_recorded: bool


def resolve_effector_identity(
    packet: dict[str, Any],
    *,
    sink: str,
    universe_dir: str | Path | None,
    mode: str | None = None,
) -> EffectorIdentity:
    """Select legacy/dual/system receipt identity behind one migration flag."""
    selected_mode = (
        mode
        if mode is not None
        else os.environ.get(EFFECT_IDENTITY_MODE_ENV, "legacy")
    ).strip().lower()
    if selected_mode not in {"legacy", "dual", "system"}:
        raise ValueError(
            f"{EFFECT_IDENTITY_MODE_ENV} must be legacy, dual, or system"
        )
    caller_hint = ""
    for field in ("idempotency_hint", "idempotency_key"):
        value = packet.get(field)
        if isinstance(value, str) and value.strip():
            caller_hint = value.strip()
            break
    if selected_mode == "legacy":
        return EffectorIdentity(
            mode=selected_mode,
            active_key=caller_hint,
            caller_hint=caller_hint,
            system_key=None,
            parity_recorded=False,
        )

    system_key = derive_effect_key(
        goal_id=str(packet.get("goal_id") or ""),
        schedule_period=str(packet.get("schedule_period") or ""),
        item_fingerprint=str(packet.get("item_fingerprint") or ""),
    )
    parity_recorded = False
    if caller_hint and selected_mode == "dual":
        if universe_dir is None:
            raise ValueError("universe_dir is required for identity parity")
        from tinyassets.storage.external_write_receipts import (
            record_identity_alias,
        )

        record_identity_alias(
            universe_dir,
            caller_hint=caller_hint,
            sink=sink,
            system_effect_key=system_key,
        )
        parity_recorded = True
    elif caller_hint and selected_mode == "system":
        if universe_dir is None:
            raise ValueError("universe_dir is required for identity parity")
        from tinyassets.storage.external_write_receipts import (
            identity_sink_has_parity,
        )

        if not identity_sink_has_parity(universe_dir, sink=sink):
            raise ValueError(
                "system identity mode requires proven dual-write parity"
            )
        parity_recorded = True
    elif selected_mode == "dual":
        raise ValueError("dual identity mode requires a caller hint for parity")
    return EffectorIdentity(
        mode=selected_mode,
        active_key=caller_hint if selected_mode == "dual" else system_key,
        caller_hint=caller_hint,
        system_key=system_key,
        parity_recorded=parity_recorded,
    )


class IdempotencyStore:
    """SQLite-backed store for (run_id, step_id) -> result deduplication.

    Each universe has one store at ``<base_path>/.idempotency.db``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._path), timeout=30.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _expiry_cutoff(self) -> str:
        return (datetime.now(timezone.utc) - _IDEMPOTENCY_TTL).isoformat()

    def _prune_expired(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM idempotent_results WHERE accessed_at < ?",
            (self._expiry_cutoff(),),
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotent_results (
                    run_id      TEXT NOT NULL,
                    step_id     TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, step_id)
                )
                """
            )

            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(idempotent_results)")
            }
            now = self._now()
            if "accessed_at" not in columns:
                conn.execute(
                    "ALTER TABLE idempotent_results ADD COLUMN accessed_at TEXT"
                )
                conn.execute(
                    "UPDATE idempotent_results SET accessed_at = COALESCE(recorded_at, ?)",
                    (now,),
                )
            conn.execute(
                "UPDATE idempotent_results SET accessed_at = COALESCE(accessed_at, recorded_at, ?)",
                (now,),
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idempotent_results_accessed_at "
                "ON idempotent_results(accessed_at)"
            )
            self._prune_expired(conn)

    def get(self, run_id: str, step_id: str) -> Any | None:
        """Return stored result for (run_id, step_id), or None if not found."""
        with self._connect() as conn:
            self._prune_expired(conn)
            row = conn.execute(
                "SELECT result_json FROM idempotent_results WHERE run_id = ? AND step_id = ?",
                (run_id, step_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE idempotent_results SET accessed_at = ? WHERE run_id = ? AND step_id = ?",
                (self._now(), run_id, step_id),
            )
        return json.loads(row["result_json"])

    def set(self, run_id: str, step_id: str, result: Any) -> None:
        """Store result for (run_id, step_id). Ignores conflicts (idempotent)."""
        now = self._now()
        with self._connect() as conn:
            self._prune_expired(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO idempotent_results
                    (run_id, step_id, result_json, recorded_at, accessed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, step_id, json.dumps(result, default=str), now, now),
            )

    def has(self, run_id: str, step_id: str) -> bool:
        return self.get(run_id, step_id) is not None


# Module-level singleton — lazy-init on first use.
_store: IdempotencyStore | None = None


def _get_store(base_path: str | Path | None = None) -> IdempotencyStore:
    global _store
    if _store is not None:
        return _store
    if base_path is None:
        try:
            from tinyassets.storage import data_dir
            base_path = data_dir()
        except Exception:
            base_path = Path.home() / ".tinyassets"
    _store = IdempotencyStore(Path(base_path) / ".idempotency.db")
    return _store


def idempotent_by_step(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: run the function at most once per (run_id, step_id) pair.

    The wrapped function must accept ``run_id: str`` and ``step_id: str``
    as its first two positional arguments. On the second call with the same
    (run_id, step_id) the stored result is returned without re-executing.

    Designed for code-node side effects that must tolerate SqliteSaver
    resume (spec: In-flight run recovery part 2).
    """
    @functools.wraps(fn)
    def wrapper(run_id: str, step_id: str, *args: Any, **kwargs: Any) -> Any:
        store = _get_store()
        existing = store.get(run_id, step_id)
        if existing is not None:
            logger.debug(
                "idempotent_by_step: returning cached result for %s/%s",
                run_id, step_id,
            )
            return existing
        result = fn(run_id, step_id, *args, **kwargs)
        store.set(run_id, step_id, result)
        return result

    wrapper._idempotent_by_step = True  # type: ignore[attr-defined]
    return wrapper


_CHECKPOINT_MARKER_KEY = "__checkpoint__"


def checkpoint(checkpoint_id: str, *, state: dict) -> dict:
    """Signal a checkpoint milestone from within a code node's run() function.

    Code nodes that declare checkpoints in their NodeDefinition can call
    this helper to mark a checkpoint as reached. Returns a state delta dict
    that the run() function should merge into its own return value.

    Usage in a code node::

        from tinyassets.idempotency import checkpoint

        def run(state):
            # ... do first half of work ...
            delta = checkpoint("halfway", state=state)
            # ... do second half ...
            return {"output_key": result, **delta}

    The checkpoint_id must match a checkpoint_id declared in the node's
    NodeDefinition.checkpoints list. The runtime (_wrap_with_checkpoints in
    graph_compiler) reads ``__checkpoint__`` keys and fires the corresponding
    checkpoint_reached event.

    Multiple checkpoints from one run() call::

        def run(state):
            d1 = checkpoint("first", state=state)
            d2 = checkpoint("second", state=state)
            return {"output": result, **d1, **d2}

    Note: the graph_compiler also evaluates reached_when predicates after
    node completion, so declarative checkpoints fire automatically without
    calling this helper. This helper is for code nodes that need to emit
    a checkpoint in the middle of their execution rather than based on
    output state predicates.
    """
    existing: list[str] = state.get(_CHECKPOINT_MARKER_KEY) or []
    return {_CHECKPOINT_MARKER_KEY: existing + [checkpoint_id]}
