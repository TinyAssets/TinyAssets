"""Private per-owner history of models successfully called through broker HTTP."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tinyassets.storage import db_path

_LEGACY_SCHEMA = """CREATE TABLE IF NOT EXISTS learned_model_evidence (
  source_kind TEXT NOT NULL,
  model_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  first_verified_at TEXT NOT NULL,
  PRIMARY KEY(source_kind, model_id, owner_user_id))"""

_SCHEMA = """CREATE TABLE IF NOT EXISTS learned_model_source_evidence (
  source_kind TEXT NOT NULL,
  provider_ref TEXT NOT NULL,
  model_id TEXT NOT NULL,
  -- Whose it is. Named owner_user_id, not something nicer, because account deletion
  -- finds per-user rows BY COLUMN NAME from its PRINCIPAL_KEYS list: a prettier name
  -- is silently skipped by that sweep and leaves a departed owner's rows behind.
  owner_user_id TEXT NOT NULL,
  first_verified_at TEXT NOT NULL,
  PRIMARY KEY(owner_user_id, provider_ref, model_id))"""

#: Every column. The table is per-owner by design, so unlike the retired shared table
#: it is ALLOWED an owner column -- and required to have one, so deletion finds it.
COLUMNS = ("source_kind", "provider_ref", "model_id", "owner_user_id",
           "first_verified_at")

#: Model inference uses the broker HTTP connection path.
LEARNED_SOURCE_KIND = "http"

#: An id THIS owner has made work here. Distinct from a publicly listed id, because
#: "you have run this" and "this is on the public list" are different claims and the
#: picker should not conflate them. Neither is admitted without the owner's access.
OWN_VERIFIED_BASIS = "owner_verified_here"

#: Short by design. Learning is optional and repeatable -- the next successful turn on
#: the same id records it -- so a busy database must never cost a user latency on the
#: reply path. An earlier 30s timeout was measured stalling a reply for 318ms.
_BUSY_WAIT_MS = 250

#: Basic identifier sanity, and only that. A charset cannot decide whether an id is
#: safe to share, and here it does not have to: nothing in this store is shared.
_IDENTIFIER = re.compile(r"\A[\x21-\x7e]{1,200}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class LearnedModel:
    source_kind: str
    provider_ref: str
    model_id: str
    first_verified_at: str


def _clean(value: object, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"invalid learned model {field}")
    text = value.strip()
    if not _IDENTIFIER.match(text):
        raise ValueError(f"invalid learned model {field}")
    return text


def _stamp(now: datetime | None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class OwnModelHistory:
    """One owner's verified model ids. Never shared, never published."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        path = db_path(self.base_path)
        if not create and not path.exists():
            return None
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=_BUSY_WAIT_MS / 1000, isolation_level=None)
        else:
            # Non-creating: an observational read must not bring a database into being.
            # `mode=rw` opens an existing file and refuses to create one, which also
            # closes the gap after the exists() check. NOT `mode=ro`: a WAL database
            # whose -shm file is absent cannot be opened read-only at all, and that is
            # the state of a freshly restarted box.
            conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                   timeout=_BUSY_WAIT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_WAIT_MS}")
        return conn

    def record(self, *, source_kind: str, provider_ref: str, model_id: str,
               owner_user_id: str,
               now: datetime | None = None) -> bool:
        """Record that this owner made this id work. Returns whether it was new.

        Idempotent per owner, so one owner's several universes add one row and nothing
        accumulates -- there is no count of anything here, by design.
        """
        kind = _clean(source_kind, "source kind")
        provider = _clean(provider_ref, "provider ref")
        model = _clean(model_id, "model id")
        owner = _clean(owner_user_id, "owner")
        when = _stamp(now)
        conn = self._connect(create=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_LEGACY_SCHEMA)
            conn.execute(_SCHEMA)
            added = conn.execute(
                "INSERT OR IGNORE INTO learned_model_source_evidence "
                "(source_kind, provider_ref, model_id, owner_user_id, first_verified_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, provider, model, owner, when),
            ).rowcount == 1
            conn.commit()
            return added
        finally:
            conn.close()

    def ids_for(self, source_kind: str, provider_ref: str,
                owner_user_id: str) -> list[LearnedModel]:
        """ONE OWNER's own ids for one source, oldest first.

        Owner-scoped on purpose, and there is deliberately no API that returns another
        owner's rows, a count of them, or anything aggregated across owners.
        """
        kind = _clean(source_kind, "source kind")
        provider = _clean(provider_ref, "provider ref")
        owner = _clean(owner_user_id, "owner")
        conn = self._connect(create=False)
        if conn is None:
            return []
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'learned_model_evidence'"
            ).fetchone():
                return []
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_SCHEMA)
            rows = conn.execute(
                "SELECT source_kind, provider_ref, model_id, first_verified_at "
                "FROM learned_model_source_evidence "
                "WHERE source_kind = ? AND provider_ref = ? AND owner_user_id = ? "
                "ORDER BY first_verified_at, model_id",
                (kind, provider, owner),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
        return [LearnedModel(row["source_kind"], row["provider_ref"], row["model_id"],
                             row["first_verified_at"])
                for row in rows]


def record_verified_model(base_path, *, source_kind, provider_ref, model_id,
                          owner_user_id) -> bool:
    """Best-effort record, for a caller that has already succeeded.

    The one place a failure is swallowed, because the caller is always a turn that has
    already produced a result: recording must not turn a delivered reply into an error.
    Anything that should fail loudly uses ``OwnModelHistory.record`` directly.
    """
    import logging

    try:
        return OwnModelHistory(base_path).record(
            source_kind=source_kind, provider_ref=provider_ref,
            model_id=model_id, owner_user_id=owner_user_id)
    except Exception as exc:  # noqa: BLE001 - never fail a call that already worked
        logging.getLogger("universe_server.learned_models").warning(
            "could not record a verified model id: %s", type(exc).__name__)
        return False
