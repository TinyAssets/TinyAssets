"""Owner-scoped effect journal for the private interactive agent executor.

Every mutation commits before returning. A winning start is necessary but never
sufficient authority to dispatch: the executor must supply fresh live authority.
No timer, loader or stale worker can turn an uncertain effect back into a plan.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from mcp.types import CallToolResult

from tinyassets import owner_lease
from tinyassets.providers.agent_chat_codec import AgentReply, ToolRequest
from tinyassets.storage import agent_turn_records as records
from tinyassets.storage import db_path
from tinyassets.storage.agent_native_records import NativeInput, NativeTerminal
from tinyassets.storage.agent_turn_boot import BOOT, BootTurns
from tinyassets.storage.agent_turn_records import (
    RoundInput,
    RoundSnapshot,
    ToolSnapshot,
    Transition,
    TurnSnapshot,
)
from tinyassets.storage.current_home import check_current_home
from tinyassets.storage.owner_fence import check_fence, ensure_fence_table
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

_SCOPE = "owner_user_id = ? AND universe_id = ? AND turn_id = ?"
# States in which a turn is still PROGRESSING: created, inferring, or waiting on
# its own tool calls. Deliberately narrower than what blocks an offline reset --
# a ``held_*`` turn is stopped, not working, so it is not something a surface may
# report as activity. The reset blocker set below is this plus the two ambiguous
# holds, expressed as the union so neither question has a second literal list.
WORKING_STATES = frozenset({"ready", "inference_started", "native_started", "tools_pending"})
_AMBIGUOUS_STATES = frozenset({"held_native_unknown", "held_tool_unknown"})
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS agent_turns (
      owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL, turn_id TEXT NOT NULL,
      version INTEGER NOT NULL CHECK(version = 1),
      generation INTEGER NOT NULL CHECK(generation > 0),
      state TEXT NOT NULL, round_ordinal INTEGER NOT NULL CHECK(round_ordinal >= 0),
      input_json TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(owner_user_id, universe_id, turn_id))""",
    """CREATE TABLE IF NOT EXISTS agent_turn_rounds (
      owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL, turn_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL CHECK(ordinal > 0), version INTEGER NOT NULL CHECK(version = 1),
      state TEXT NOT NULL, candidate_json TEXT NOT NULL, reply_json TEXT,
      cost_microusd INTEGER CHECK(cost_microusd >= 0),
      PRIMARY KEY(owner_user_id, universe_id, turn_id, ordinal),
      FOREIGN KEY(owner_user_id, universe_id, turn_id)
        REFERENCES agent_turns(owner_user_id, universe_id, turn_id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS agent_turn_tools (
      owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL, turn_id TEXT NOT NULL,
      round_ordinal INTEGER NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal > 0),
      version INTEGER NOT NULL CHECK(version = 1), call_id TEXT NOT NULL, name TEXT NOT NULL,
      arguments_json TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT,
      content_kind TEXT, is_error INTEGER CHECK(is_error IN (0, 1)),
      PRIMARY KEY(owner_user_id, universe_id, turn_id, round_ordinal, ordinal),
      FOREIGN KEY(owner_user_id, universe_id, turn_id, round_ordinal)
        REFERENCES agent_turn_rounds(owner_user_id, universe_id, turn_id, ordinal)
        ON DELETE CASCADE)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS agent_turn_one_inference
      ON agent_turn_rounds(owner_user_id, universe_id, turn_id)
      WHERE state = 'inference_started'""",
)


class JournalUnavailable(RuntimeError):
    """Unreadable progress cannot be replaced with an executable default."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise JournalUnavailable("agent turn schema requires an idle connection")
    for statement in _SCHEMA:
        conn.execute(statement)
    ensure_fence_table(conn)
    # Additive and idempotent (change execution-owner-lease, B1): the generation
    # of the owner that created the row. Rows from before the lease existed are
    # generation 0 -- below every real generation (the first acquisition is 1) --
    # so the first leased boot reads them as an earlier owner's and settles them,
    # exactly what the boot rule this replaces did for a pre-deploy leftover.
    # Additive only, and atomic: re-checked under the write lock, so two
    # processes cannot race it. A rebuild or rename would need the startup
    # migration boundary instead (owner_stores.MIGRATES_ON_OPEN_BEFORE_C2).
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    if "owner_generation" not in columns:
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
            if "owner_generation" not in columns:
                conn.execute(
                    "ALTER TABLE agent_turns ADD COLUMN owner_generation "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    # Which agent ran the turn (harness §4.18); rows from before are main's.
    # Checked again under the write lock: another process may add it first.
    if "agent_id" not in {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if "agent_id" not in {
                row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")
            }:
                conn.execute(
                    "ALTER TABLE agent_turns ADD COLUMN agent_id TEXT NOT NULL DEFAULT 'main'")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _scope(owner: str, universe: str, turn: str) -> tuple[str, str, str]:
    return tuple(records.identity(value) for value in (owner, universe, turn))


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise JournalUnavailable("agent turn mutation requires a transaction")


def _read(conn: sqlite3.Connection, scope: tuple[str, str, str]) -> TurnSnapshot | None:
    row = conn.execute(f"SELECT * FROM agent_turns WHERE {_SCOPE}", scope).fetchone()
    if row is None:
        return None
    try:
        if row["version"] != 1 or row["state"] not in records.STATES:
            raise records.invalid()
        generation = records.integer(row["generation"], minimum=1)
        frontier = records.integer(row["round_ordinal"])
        value = records.document(row["input_json"])
        names = {"version", "prompt", "system", "policy_generation"}
        header_version = value.get("version")
        if type(header_version) is not int or header_version not in {1, 2, 3}:
            raise records.invalid()
        if header_version >= 2:
            names.add("policy_source")
        if header_version == 3:
            names.update(("authority_kind", "work_receipt_id"))
        records.fields(
            value, names,
            version=header_version,
        )
        authority_kind = value.get("authority_kind", "served_request")
        work_receipt_id = value.get("work_receipt_id", "")
        if records.work_lineage(authority_kind, work_receipt_id) != (header_version == 3):
            raise records.invalid()
        if not isinstance(value["prompt"], str) or not isinstance(value["system"], str):
            raise records.invalid()
        if value["policy_generation"] is not None:
            records.integer(value["policy_generation"])
        source = records.policy_source(
            value.get("policy_source", "unknown"), value["policy_generation"]
        )
        timestamp = row["created_at"]
        if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
            raise records.invalid()
        datetime.fromisoformat(timestamp[:-1] + "+00:00")
        rounds = []
        for ordinal, rr in enumerate(
            conn.execute(
                f"SELECT * FROM agent_turn_rounds WHERE {_SCOPE} ORDER BY ordinal",
                scope,
            ),
            1,
        ):
            # SQL version 1 is the unchanged container. Candidate/reply payloads
            # carry their own version; old HTTP rows are neither migrated nor rewritten.
            candidate_document = records.document(rr["candidate_json"])
            if candidate_document.get("version") == 2 or (
                candidate_document.get("version") == 3
                and candidate_document.get("kind") == "native_agent"
            ):
                rounds.append(_read_native_round(conn, scope, ordinal, rr))
                continue
            if (
                rr["version"] != 1
                or rr["ordinal"] != ordinal
                or rr["state"]
                not in {
                    "inference_started",
                    "received",
                    "failed",
                }
            ):
                raise records.invalid()
            candidate = RoundInput.from_json(rr["candidate_json"])
            reply = (
                None
                if rr["reply_json"] is None
                else records.load_reply(rr["reply_json"], candidate)
            )
            if (reply is not None) != (rr["state"] == "received"):
                raise records.invalid()
            cost = rr["cost_microusd"]
            if cost is not None:
                records.integer(cost)
                if reply is None:
                    raise records.invalid()
            tools = []
            for call_ordinal, tr in enumerate(
                conn.execute(
                    f"SELECT * FROM agent_turn_tools WHERE {_SCOPE} "
                    "AND round_ordinal = ? ORDER BY ordinal",
                    (*scope, ordinal),
                ),
                1,
            ):
                if (
                    tr["version"] != 1
                    or tr["ordinal"] != call_ordinal
                    or tr["state"]
                    not in {
                        "planned",
                        "started",
                        "completed",
                        "not_sent",
                        "unknown",
                    }
                ):
                    raise records.invalid()
                request = ToolRequest(tr["call_id"], tr["name"], tr["arguments_json"])
                if (
                    reply is None
                    or call_ordinal > len(reply.tool_requests)
                    or (reply.tool_requests[call_ordinal - 1] != request)
                ):
                    raise records.invalid()
                if tr["state"] == "completed":
                    _, kind, error = records.load_result(tr["result_json"])
                    if (
                        tr["content_kind"] != kind
                        or type(tr["is_error"]) is not int
                        or (tr["is_error"] != int(error))
                    ):
                        raise records.invalid()
                else:
                    kind = error = None
                    if any(
                        tr[key] is not None for key in ("result_json", "content_kind", "is_error")
                    ):
                        raise records.invalid()
                tools.append(
                    ToolSnapshot(call_ordinal, request, tr["state"], tr["result_json"], kind, error)
                )
            if len(tools) != (len(reply.tool_requests) if reply is not None else 0):
                raise records.invalid()
            # Only a completed prefix, then at most one started/held row, then plans.
            pending = False
            for tool in tools:
                if pending and tool.state != "planned":
                    raise records.invalid()
                if tool.state != "completed" or tool.content_kind == "non_text":
                    pending = True
            rounds.append(RoundSnapshot(ordinal, candidate, rr["state"], reply, tuple(tools), cost))
        if any((item.candidate.authority_kind, item.candidate.work_receipt_id)
               != (authority_kind, work_receipt_id) for item in rounds):
            raise records.invalid()
        if len(rounds) != frontier:
            raise records.invalid()
        for completed_round in rounds[:-1]:
            if _frontier(completed_round) != "ready" and not (
                type(completed_round.candidate) is RoundInput
                and completed_round.state == "failed"
                and completed_round.reply is None
                and not completed_round.tools
            ) and not (
                type(completed_round.candidate) is NativeInput
                and type(completed_round.reply) is NativeTerminal
                and completed_round.reply.status == "capacity_no_effects"
            ):
                raise records.invalid()
        expected_state = _frontier(rounds[-1]) if rounds else "ready"
        # Closing a quiescent frontier preserves all known progress. A stored
        # terminal label must never hide an in-flight or ambiguous effect.
        if row["state"] == "abandoned" and expected_state == "ready":
            expected_state = "abandoned"
        if row["state"] != expected_state:
            raise records.invalid()
        return TurnSnapshot(
            scope[2],
            generation,
            row["state"],
            value["prompt"],
            value["system"],
            value["policy_generation"],
            timestamp,
            tuple(rounds),
            source,
            authority_kind,
            work_receipt_id,
        )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise JournalUnavailable("agent turn record unavailable") from None


def _read_native_round(conn, scope, ordinal, row) -> RoundSnapshot:
    if (row["version"] != 1 or row["ordinal"] != ordinal
            or row["state"] not in {"native_started", "native_received"}):
        raise records.invalid()
    candidate = NativeInput.from_json(row["candidate_json"])
    reply = (None if row["reply_json"] is None
             else NativeTerminal.from_json(row["reply_json"], candidate))
    if (reply is not None) != (row["state"] == "native_received"):
        raise records.invalid()
    cost = row["cost_microusd"]
    if cost is not None:
        records.integer(cost)
        if reply is None or reply.status != "completed":
            raise records.invalid()
    if conn.execute(
        f"SELECT 1 FROM agent_turn_tools WHERE {_SCOPE} AND round_ordinal = ? LIMIT 1",
        (*scope, ordinal),
    ).fetchone() is not None:
        raise records.invalid()
    return RoundSnapshot(ordinal, candidate, row["state"], reply, (), cost)


def _frontier(round_snapshot: RoundSnapshot) -> str:
    if type(round_snapshot.candidate) is NativeInput:
        if round_snapshot.state == "native_started":
            return "native_started"
        if type(round_snapshot.reply) is not NativeTerminal:
            raise records.invalid()
        return {
            "completed": "completed",
            "capacity_no_effects": "held_native_capacity",
            "indeterminate": "held_native_unknown",
        }[round_snapshot.reply.status]
    if round_snapshot.state == "inference_started":
        return "inference_started"
    if round_snapshot.state == "failed":
        return "held_transport"
    reply = round_snapshot.reply
    if reply is None:
        raise records.invalid()
    if reply.stop != "tool_requests":
        return records.STOP_STATE[reply.stop]
    if not round_snapshot.tools:
        raise records.invalid()
    for tool in round_snapshot.tools:
        if tool.state == "unknown":
            return "held_tool_unknown"
        if tool.state == "not_sent":
            return "held_tool_not_sent"
        if tool.content_kind == "non_text":
            return "held_unsupported_result"
        if tool.state != "completed":
            return "tools_pending"
    return "ready"


def _advance(conn, scope, current, state, *, ordinal=None):
    _require_transaction(conn)
    if current.generation == records.MAX_INT:
        raise JournalUnavailable("agent turn generation exhausted")
    changed = conn.execute(
        f"UPDATE agent_turns SET generation = generation + 1, state = ?, round_ordinal = ? "
        f"WHERE {_SCOPE} AND generation = ?",
        (state, len(current.rounds) if ordinal is None else ordinal, *scope, current.generation),
    ).rowcount
    if changed != 1:
        raise JournalUnavailable("agent turn transition unavailable")
    return Transition("applied", _read(conn, scope))


def reset_blockers(conn: sqlite3.Connection, owner: str, universe: str) -> list[str]:
    """Preserve effect evidence; an offline reset cannot abandon executable/ambiguous work."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"agent_turns", "agent_turn_rounds", "agent_turn_tools"}
    if not tables & expected:
        return []
    if not expected <= tables:
        return ["incomplete agent turn journal blocks scoped reset"]
    try:
        for row in conn.execute(
            "SELECT turn_id FROM agent_turns WHERE owner_user_id = ? AND universe_id = ?",
            (owner, universe),
        ):
            turn = _read(conn, (owner, universe, row[0]))
            if turn is not None and turn.state in (WORKING_STATES | _AMBIGUOUS_STATES):
                return ["active or ambiguous agent turn references exact home"]
        return []
    except (JournalUnavailable, sqlite3.DatabaseError):
        return ["unreadable agent turn journal references exact home"]


class AgentTurnJournal:
    """Private persistence, not authentication. No callbacks run inside its transactions."""

    def __init__(self, base_path: str | Path) -> None:
        self._ledger = SQLiteProviderWorkAuthorityStore(base_path)

    @contextmanager
    def _transaction(self, universe: str):
        """One owner mutation: the command center's lease, then ONE fenced write
        transaction (change execution-owner-lease D3). Yields (conn, lease)."""
        with self._ledger.connection() as conn:
            ensure_schema(conn)
            lease = self._lease(universe)
            conn.execute("BEGIN IMMEDIATE")
            try:
                check_fence(conn, lease)
                yield conn, lease
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _lease(self, universe: str):
        base = self._ledger.base_path
        owner_lease.register_store(base, db_path(base), "agent_turn_journal")
        return owner_lease.acquire(base, owner_lease.key_for(records.identity(universe)))

    def create(
        self,
        owner: str,
        universe: str,
        *,
        prompt: str,
        system: str,
        policy_generation: int | None = None,
        policy_source: str = "unknown",
        authority_kind: str = "served_request",
        work_receipt_id: str = "",
        agent_id: str = "main",
    ) -> TurnSnapshot:
        scope = _scope(owner, universe, uuid.uuid4().hex)
        agent_id = records.identity(agent_id or "main")
        if not isinstance(prompt, str) or not isinstance(system, str):
            raise records.invalid()
        if policy_generation is not None:
            records.integer(policy_generation)
        records.policy_source(policy_source, policy_generation)
        work = records.work_lineage(authority_kind, work_receipt_id)
        raw = records.dump(
            {
                "version": 3 if work else 2,
                "prompt": prompt,
                "system": system,
                "policy_generation": policy_generation,
                "policy_source": policy_source,
                **({"authority_kind": authority_kind, "work_receipt_id": work_receipt_id}
                   if work else {}),
            }
        )
        with self._transaction(universe) as (conn, lease):
            check_current_home(conn, owner, universe)
            conn.execute(
                "INSERT INTO agent_turns (owner_user_id, universe_id, turn_id, version, "
                "generation, state, round_ordinal, input_json, created_at, "
                "owner_generation, agent_id) "
                "VALUES (?, ?, ?, 1, 1, 'ready', 0, ?, ?, ?, ?)",
                (*scope, raw, self._ledger.timestamp(), lease.generation, agent_id),
            )
            snapshot = _read(conn, scope)
        # Creating the row IS this boot taking the turn on: both adapters reach a
        # turn only through here, and the caller is about to execute it. Claimed
        # after the commit, so a rolled-back create claims nothing.
        BOOT.claim(scope[1], scope[2])
        return snapshot

    def get(self, owner: str, universe: str, turn_id: str) -> TurnSnapshot | None:
        scope = _scope(owner, universe, turn_id)
        with self._ledger.connection() as conn:
            ensure_schema(conn)
            conn.execute("BEGIN")  # consistent root/round/tool snapshot; no claim or retry
            return _read(conn, scope)

    def universe_working_turn(
        self, universe: str, *, now: datetime, max_age_s: float, boot: BootTurns = BOOT,
    ) -> dict[str, object] | None:
        """The newest still-progressing turn for ONE universe, or ``None``.

        Universe-scoped on purpose. The question a surface asks is "is this
        universe working", which is answered by the universe's own rows; the
        journal's ``owner_user_id`` is a provider-capability principal and is
        NOT the same identifier a served request's caller presents, so matching
        on it would silently answer "idle" during a live turn.

        Genuinely observational: it opens its OWN connection in sqlite's
        non-creating ``mode=rw`` and runs no DDL, so it creates no database, no
        directory and no table. The ledger store's ``connection()`` is not used
        here -- it runs ``executescript(_SCHEMA)`` on every open, and Codex
        reproduced a read against a database lacking those tables CREATING five
        of them (#4020). A missing database, or one with no turn table, has no
        turn to report.

        A row older than ``max_age_s`` is returned with ``stale`` true rather
        than dropped. A served turn is wrapped in ``asyncio.timeout`` by the
        coordinator, so an older progressing row is one a killed process left
        behind -- a caller must not paint it as activity, and hiding it would
        make a wedged row unobservable.

        A progressing row no live owner is running is skipped entirely, which is
        not the silent fallback the stale bound avoids: the age bound is a guess
        about whether a turn is still going, while ownership is a positive
        determination. A row is a live owner's only while its
        ``owner_generation`` equals the generation its command center's key is
        held at AND that owner tree is alive (change execution-owner-lease D2),
        and it was not stopped by a cancelled task in this process (``boot``).
        Belt and braces behind ``agent_turn_reconcile``, which settles such a row
        at startup; if that failed, this still refuses to report a dead
        container's leftover as thinking (founder, 2026-09-26: a deploy killed a
        turn and the indicator ran for 35 minutes on the row it left behind).
        Read-only: it never acquires a lease or creates a store.
        """
        uid = records.identity(universe)
        if max_age_s <= 0:
            raise ValueError("max_age_s must be positive")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        path = db_path(self._ledger.base_path)
        if not path.exists():
            return None
        # ``mode=rw`` opens an existing database and REFUSES to create one, which
        # closes the window between the check above and the open. Read-write, not
        # ``mode=ro``: a WAL database whose -shm file is absent cannot be opened
        # read-only at all, and that is the state a freshly restarted box is in.
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                               timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            if not conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_turns'"
            ).fetchone():
                return None
            columns = {r[1] for r in conn.execute("PRAGMA table_info(agent_turns)")}
            generation = "owner_generation" if "owner_generation" in columns else "0"
            rows = conn.execute(
                f"SELECT turn_id, state, created_at, {generation} AS owner_generation "
                "FROM agent_turns WHERE universe_id = ? ORDER BY created_at DESC",
                (uid,),
            ).fetchall()
        finally:
            conn.close()
        held = owner_lease.held_generation(self._ledger.base_path, owner_lease.key_for(uid))
        live_owner = held is not None and owner_lease.tree_alive(self._ledger.base_path, held[1])
        newest: dict[str, object] | None = None
        for row in rows:
            if row["state"] not in WORKING_STATES:
                boot.forget(uid, row["turn_id"])  # settled: nothing left to remember
                continue
            started = row["created_at"]
            if not isinstance(started, str) or not started.endswith("Z"):
                continue
            if not live_owner or row["owner_generation"] != held[0]:
                continue
            if boot.stopped(uid, row["turn_id"]):
                continue
            try:
                when = datetime.fromisoformat(started[:-1] + "+00:00")
            except ValueError:
                continue
            age = (now - when).total_seconds()
            observed = {
                "turn_id": row["turn_id"],
                "state": row["state"],
                "started_at": started,
                "age_s": age,
                "stale": age > max_age_s,
            }
            # Which step, on which model, for how long: a long wait on one
            # model request must not read as a hang.
            progress = boot.progress(uid, row["turn_id"])
            if progress is not None:
                observed["round"], observed["model"] = progress[0], progress[1]
                observed["round_age_s"] = max((now - progress[2]).total_seconds(), 0.0)
            # Fresh beats stale whatever the order; among equals the newest row
            # wins, which is the one the DESC scan reached first.
            if newest is None or (newest["stale"] and not observed["stale"]):
                newest = observed
            if not observed["stale"]:
                break
        return newest

    @contextmanager
    def _mutation(self, owner, universe, turn_id, expected_generation):
        scope = _scope(owner, universe, turn_id)
        records.integer(expected_generation, minimum=1)
        with self._transaction(universe) as (conn, _lease):
            check_current_home(conn, owner, universe)
            current = _read(conn, scope)
            if current is None:
                raise JournalUnavailable("agent turn unavailable")
            yield conn, scope, current

    def begin_round(
        self,
        owner,
        universe,
        turn_id,
        *,
        expected_generation: int,
        candidate: RoundInput | NativeInput,
        after_failed_inference: bool = False,
    ) -> Transition:
        if (type(candidate) not in (RoundInput, NativeInput)
                or type(after_failed_inference) is not bool):
            raise records.invalid()
        raw = candidate.canonical_json()
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn,
            scope,
            current,
        ):
            if (candidate.authority_kind, candidate.work_receipt_id) != (
                current.authority_kind, current.work_receipt_id,
            ):
                raise records.invalid()
            retryable = (
                after_failed_inference
                and current.state == "held_transport"
                and current.rounds
                and current.rounds[-1].state == "failed"
                and current.rounds[-1].reply is None
                and not current.rounds[-1].tools
            ) or (
                after_failed_inference
                and current.state == "held_native_capacity"
                and current.rounds
                and type(current.rounds[-1].candidate) is NativeInput
                and type(current.rounds[-1].reply) is NativeTerminal
                and current.rounds[-1].reply.status == "capacity_no_effects"
            )
            admissible = retryable if after_failed_inference else current.state == "ready"
            if current.generation != expected_generation or not admissible:
                return Transition("conflict", current)
            ordinal = len(current.rounds) + 1
            state = "native_started" if type(candidate) is NativeInput else "inference_started"
            conn.execute(
                "INSERT INTO agent_turn_rounds (owner_user_id, universe_id, turn_id, ordinal, "
                "version, "
                "state, candidate_json, reply_json, cost_microusd) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, NULL, NULL)",
                (*scope, ordinal, state, raw),
            )
            return _advance(conn, scope, current, state, ordinal=ordinal)

    def abandon(self, owner, universe, turn_id, *, expected_generation: int) -> Transition:
        """Close a quiescent root, retaining every settled inference and effect."""
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn,
            scope,
            current,
        ):
            if current.state == "abandoned":
                return Transition("already_applied", current)
            if (
                current.generation != expected_generation
                or current.state != "ready"
            ):
                return Transition("conflict", current)
            return _advance(conn, scope, current, "abandoned")

    def finish_inference(
        self,
        owner,
        universe,
        turn_id,
        *,
        expected_generation: int,
        ordinal: int,
        reply: AgentReply | None,
        cost_microusd: int | None = None,
    ) -> Transition:
        records.integer(ordinal, minimum=1)
        if cost_microusd is not None:
            records.integer(cost_microusd)
            if reply is None:
                raise records.invalid()
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn,
            scope,
            current,
        ):
            if ordinal > len(current.rounds):
                return Transition("conflict", current)
            target = current.rounds[ordinal - 1]
            if type(target.candidate) is not RoundInput:
                return Transition("conflict", current)
            raw = None if reply is None else records.reply_json(reply, target.candidate)
            existing = (
                None if target.reply is None else records.reply_json(target.reply, target.candidate)
            )
            if target.state != "inference_started":
                same = (raw, cost_microusd) == (existing, target.cost_microusd)
                return Transition("already_applied" if same else "conflict", current)
            if current.generation != expected_generation or ordinal != len(current.rounds):
                return Transition("conflict", current)
            conn.execute(
                f"UPDATE agent_turn_rounds SET state = ?, reply_json = ?, cost_microusd = ? "
                f"WHERE {_SCOPE} AND ordinal = ?",
                ("failed" if reply is None else "received", raw, cost_microusd, *scope, ordinal),
            )
            for call_ordinal, request in enumerate(() if reply is None else reply.tool_requests, 1):
                conn.execute(
                    "INSERT INTO agent_turn_tools (owner_user_id, universe_id, turn_id, "
                    "round_ordinal, "
                    "ordinal, version, call_id, name, arguments_json, state, result_json, "
                    "content_kind, is_error) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, 'planned', NULL, NULL, NULL)",
                    (
                        *scope,
                        ordinal,
                        call_ordinal,
                        request.call_id,
                        request.name,
                        request.arguments_json,
                    ),
                )
            state = "held_transport" if reply is None else records.STOP_STATE[reply.stop]
            return _advance(conn, scope, current, state)

    def finish_native(
        self, owner, universe, turn_id, *, expected_generation: int,
        ordinal: int, terminal: NativeTerminal, cost_microusd: int | None = None,
    ) -> Transition:
        """Persist a native terminal union; missing effects never mean no effects."""
        records.integer(ordinal, minimum=1)
        if type(terminal) is not NativeTerminal:
            raise records.invalid()
        if cost_microusd is not None:
            records.integer(cost_microusd)
            if terminal.status != "completed":
                raise records.invalid()
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn, scope, current,
        ):
            if ordinal > len(current.rounds):
                return Transition("conflict", current)
            target = current.rounds[ordinal - 1]
            if type(target.candidate) is not NativeInput:
                return Transition("conflict", current)
            raw = terminal.canonical_json(target.candidate)
            if target.state != "native_started":
                same = (terminal, cost_microusd) == (target.reply, target.cost_microusd)
                return Transition("already_applied" if same else "conflict", current)
            if current.generation != expected_generation or ordinal != len(current.rounds):
                return Transition("conflict", current)
            conn.execute(
                f"UPDATE agent_turn_rounds SET state = 'native_received', reply_json = ?, "
                f"cost_microusd = ? WHERE {_SCOPE} AND ordinal = ?",
                (raw, cost_microusd, *scope, ordinal),
            )
            state = _frontier(RoundSnapshot(
                ordinal, target.candidate, "native_received", terminal, (), cost_microusd,
            ))
            return _advance(conn, scope, current, state)

    def start_tool(
        self, owner, universe, turn_id, *, expected_generation: int, ordinal: int, call_ordinal: int
    ) -> Transition:
        records.integer(ordinal, minimum=1)
        records.integer(call_ordinal, minimum=1)
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn,
            scope,
            current,
        ):
            if (
                current.generation != expected_generation
                or current.state != "tools_pending"
                or (ordinal != len(current.rounds))
            ):
                return Transition("conflict", current)
            tools = current.rounds[-1].tools
            if (
                call_ordinal > len(tools)
                or tools[call_ordinal - 1].state != "planned"
                or any(tool.state != "completed" for tool in tools[: call_ordinal - 1])
            ):
                return Transition("conflict", current)
            conn.execute(
                f"UPDATE agent_turn_tools SET state = 'started' WHERE {_SCOPE} "
                "AND round_ordinal = ? AND ordinal = ?",
                (*scope, ordinal, call_ordinal),
            )
            return _advance(conn, scope, current, "tools_pending")

    def finish_tool(
        self,
        owner,
        universe,
        turn_id,
        *,
        expected_generation: int,
        ordinal: int,
        call_ordinal: int,
        request: ToolRequest,
        result: CallToolResult | None = None,
        failure: str | None = None,
    ) -> Transition:
        records.integer(ordinal, minimum=1)
        records.integer(call_ordinal, minimum=1)
        if (
            type(request) is not ToolRequest
            or (result is None) == (failure is None)
            or (failure is not None and failure not in {"not_sent", "unknown"})
        ):
            raise records.invalid()
        raw, kind, error = (None, None, None) if result is None else records.result_json(result)
        state = failure or "completed"
        with self._mutation(owner, universe, turn_id, expected_generation) as (
            conn,
            scope,
            current,
        ):
            if ordinal > len(current.rounds) or call_ordinal > len(
                current.rounds[ordinal - 1].tools
            ):
                return Transition("conflict", current)
            target = current.rounds[ordinal - 1].tools[call_ordinal - 1]
            if target.request != request:
                return Transition("conflict", current)
            if target.state in {"completed", "not_sent", "unknown"}:
                same = (target.state, target.result_json, target.content_kind, target.is_error) == (
                    state,
                    raw,
                    kind,
                    error,
                )
                return Transition("already_applied" if same else "conflict", current)
            if (
                current.generation != expected_generation
                or target.state != "started"
                or (current.state != "tools_pending" or ordinal != len(current.rounds))
            ):
                return Transition("conflict", current)
            conn.execute(
                f"UPDATE agent_turn_tools SET state = ?, result_json = ?, "
                f"content_kind = ?, is_error = ? WHERE {_SCOPE} "
                "AND round_ordinal = ? AND ordinal = ?",
                (
                    state,
                    raw,
                    kind,
                    None if error is None else int(error),
                    *scope,
                    ordinal,
                    call_ordinal,
                ),
            )
            frontier = (
                "held_tool_" + failure
                if failure
                else "held_unsupported_result"
                if kind == "non_text"
                else "ready"
                if call_ordinal == len(current.rounds[-1].tools)
                else "tools_pending"
            )
            return _advance(conn, scope, current, frontier)
