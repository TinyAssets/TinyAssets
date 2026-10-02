"""Private canonical request/run correlation; never execution-start authority.

Internal storage seams only. The consumer must validate installation, source,
input mapping and preferences before reserving. No public route calls this yet.
Lock order: existing author writer -> runs OR conversation transaction, never
both subordinate writers simultaneously. No provider-admission lock is taken.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tinyassets import conversation_store
from tinyassets.conversation_failure import failure_notice, normalize_turn_failure, turn_failure
from tinyassets.runs import _insert_run_in_transaction, initialize_runs_db, runs_db_path
from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import db_path
from tinyassets.storage.current_home import check_current_home

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_run_admissions (
 admission_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL,
 session_id TEXT NOT NULL, request_key_hash TEXT NOT NULL, intent_digest TEXT NOT NULL,
 intent_json TEXT, context_json TEXT, selection_json TEXT NOT NULL,
 run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id), terminal_json TEXT,
 projection_state TEXT NOT NULL DEFAULT 'pending'
   CHECK(projection_state IN ('pending','committed','held','expired')),
 conversation_turn_no INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(owner_user_id, universe_id, session_id, request_key_hash)
);
"""
_PROJECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_terminal_projections (
 admission_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, terminal_digest TEXT NOT NULL,
 founder_turn_no INTEGER NOT NULL, reply_turn_no INTEGER NOT NULL, committed_at REAL NOT NULL
);
"""


class IntentConflict(ValueError):
    """One scoped client key was reused for different caller intent."""


class TerminalUnavailable(RuntimeError):
    """No truthful terminal projection is available; do not replay effects."""


@dataclass
class _Scope:
    base: Path
    home: Path
    owner: str
    universe: str
    author: sqlite3.Connection
    active: bool = True
    runs_open: bool = False
    borrowed_runs: sqlite3.Connection | None = None

    @property
    def session(self):
        return f"principal:{self.owner}"

    def check(self):
        if not self.active or not self.author.in_transaction:
            raise PermissionError("canonical storage scope is not active")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(value):
    parsed = uuid.UUID(value) if isinstance(value, str) else None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        raise ValueError("request key must be a canonical UUIDv4")
    return _digest(value)


def initialize(base):
    """Deployment/schema setup, never called from the admission transaction."""
    from tinyassets.branch_versions import initialize_branch_versions_db

    initialize_runs_db(base)
    # Source authorization reads branch_versions.public; migrate that table
    # (column + one-time publication-mark backfill) before any admission.
    initialize_branch_versions_db(base)
    with sqlite3.connect(runs_db_path(base), timeout=5, factory=ClosingConnection) as conn:
        conn.executescript(_SCHEMA)


def _plain(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PermissionError("canonical storage path is a link or reparse point")


@contextmanager
def authorized_scope(base, *, owner, universe):
    """Existing reset barrier precedes author writer; no home mkdir or recovery."""
    from tinyassets.scoped_reset import (
        _assert_recovery_state_is_clean,
        acquire_maintenance_barrier,
    )

    base = Path(base).resolve(strict=True)
    with acquire_maintenance_barrier(base, exclusive=False, timeout=5):
        _assert_recovery_state_is_clean(base)
        with _authorized_scope_locked(base, owner=owner, universe=universe) as scope:
            yield scope


@contextmanager
def _authorized_scope_locked(base, *, owner, universe):
    """Stabilize current home/deletion after the existing reset barrier."""
    base = Path(base).resolve(strict=True)
    if not owner or not universe or Path(universe).name != universe:
        raise PermissionError("canonical scope is invalid")
    author_path = db_path(base)
    _plain(author_path)
    conn = sqlite3.connect(author_path.as_uri() + "?mode=rw", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    scope = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        check_current_home(conn, owner, universe)
        row = conn.execute("SELECT permission FROM universe_acl WHERE universe_id=? AND actor_id=?",
                           (universe, owner)).fetchone()
        if row is None or row[0] != "admin":
            raise PermissionError("canonical scope requires current owner admin")
        home = base / universe
        _plain(home)
        if not home.is_dir() or home.resolve(strict=True).parent != base:
            raise PermissionError("canonical home is unavailable")
        scope = _Scope(base, home, owner, universe, conn)
        yield scope
        conn.commit()
    finally:
        if scope is not None:
            scope.active = False
        conn.close()


@contextmanager
def runs_transaction(scope):
    scope.check()
    if scope.runs_open:
        raise RuntimeError("nested runs transaction is forbidden")
    path = runs_db_path(scope.base)
    _plain(path)
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        scope.runs_open = True
        yield conn
        conn.commit()
    finally:
        scope.runs_open = False
        conn.close()


@contextmanager
def scope_from_transactions(base, *, owner, universe, author_conn, runs_conn):
    """Borrow common worker fences without opening/committing any connection.

    Trusted caller already holds the shared maintenance barrier and author ->
    runs writers. This scope cannot project or open another runs transaction.
    It validates current home/source using only the supplied live connections.
    """
    base = Path(base).resolve(strict=True)
    if not owner or not universe or Path(universe).name != universe:
        raise PermissionError("canonical scope is invalid")
    for conn, expected in ((author_conn, db_path(base)), (runs_conn, runs_db_path(base))):
        if not conn.in_transaction:
            raise RuntimeError("canonical borrowed scope requires active transactions")
        actual = conn.execute("PRAGMA database_list").fetchone()[2]
        if Path(actual).resolve() != expected:
            raise PermissionError("canonical borrowed scope has the wrong database")
        _plain(expected)
    check_current_home(author_conn, owner, universe)
    row = author_conn.execute(
        "SELECT permission FROM universe_acl WHERE universe_id=? AND actor_id=?",
        (universe, owner),
    ).fetchone()
    if row is None or row[0] != "admin":
        raise PermissionError("canonical scope requires current owner admin")
    home = base / universe
    _plain(home)
    if not home.is_dir() or home.resolve(strict=True).parent != base:
        raise PermissionError("canonical home is unavailable")
    scope = _Scope(base, home, owner, universe, author_conn,
                   runs_open=True, borrowed_runs=runs_conn)
    try:
        yield scope
    finally:
        scope.active = False


@dataclass(frozen=True)
class _AuthorizedSource:
    scope: _Scope
    version_id: str
    branch_id: str
    content_hash: str


def authorize_source(scope, version_id, content_hash):
    """Metadata lookup, then current author visibility, BEFORE a runs writer.

    The one version read rule (``branch_versions.version_readable_by``, which
    the connector's readers use too), applied to rows this scope already holds
    so no helper opens another author DB connection. Existing compiler
    provenance still decides foreign code execution.
    """
    scope.check()
    if scope.runs_open:
        raise RuntimeError("source authorization must precede the runs writer")
    if (not isinstance(version_id, str) or not version_id
            or not isinstance(content_hash, str) or len(content_hash) != 64):
        raise PermissionError("consumer source unavailable")
    path = runs_db_path(scope.base)
    _plain(path)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        metadata = conn.execute("SELECT branch_def_id, public FROM branch_versions "
                                "WHERE branch_version_id=?", (version_id,)).fetchone()
    finally:
        conn.close()
    return _source_authority(scope, version_id, content_hash, metadata)


def authorize_source_in_transaction(conn, scope, version_id, content_hash):
    """Worker-local source authorization; no new DB call under existing writers."""
    _transaction(conn, scope)
    if scope.borrowed_runs is not conn:
        raise PermissionError("source authorization requires the borrowed worker scope")
    if (not isinstance(version_id, str) or not version_id
            or not isinstance(content_hash, str) or len(content_hash) != 64):
        raise PermissionError("consumer source unavailable")
    metadata = conn.execute(
        "SELECT branch_def_id, public FROM branch_versions WHERE branch_version_id=?",
        (version_id,),
    ).fetchone()
    return _source_authority(scope, version_id, content_hash, metadata)


def _source_authority(scope, version_id, content_hash, metadata):
    if metadata is None:
        raise PermissionError("consumer source unavailable")
    author = scope.author.execute("SELECT author,visibility FROM branch_definitions "
                                  "WHERE branch_def_id=?", (metadata[0],)).fetchone()
    from tinyassets.branch_versions import version_readable_by

    if author is None or not version_readable_by(
        scope.owner, author=author[0], visibility=author[1], public=metadata[1],
    ):
        raise PermissionError("consumer source unavailable")
    return _AuthorizedSource(scope, version_id, metadata[0], content_hash)


def load_source_in_transaction(conn, scope, source):
    """Current runs-local pin check; never an author DB call under runs writer."""
    from tinyassets.branch_versions import compute_content_hash

    _transaction(conn, scope)
    if not isinstance(source, _AuthorizedSource) or source.scope is not scope:
        raise PermissionError("consumer source unavailable")
    row = conn.execute("SELECT snapshot_json FROM branch_versions WHERE branch_version_id=? "
                       "AND branch_def_id=? AND content_hash=? AND status='active'",
                       (source.version_id, source.branch_id, source.content_hash)).fetchone()
    if row is None:
        raise PermissionError("consumer source unavailable")
    try:
        snapshot = json.loads(row[0])
        if (not isinstance(snapshot, dict) or snapshot.get("branch_def_id") != source.branch_id
                or compute_content_hash(snapshot) != source.content_hash
                or not isinstance(snapshot.get("node_defs"), list)):
            raise ValueError("snapshot mismatch")
    except (ValueError, TypeError, RecursionError) as exc:
        raise PermissionError("consumer source unavailable") from exc
    for node in snapshot["node_defs"]:
        if not isinstance(node, dict):
            raise PermissionError("consumer source unavailable")
        if any(node.get(key) is not None for key in
               ("invoke_branch_spec", "invoke_branch_version_spec", "node_ref")):
            raise ValueError("consumer nested executable references are unsupported")
    return snapshot


def _transaction(conn, scope):
    scope.check()
    if scope.borrowed_runs is not None and scope.borrowed_runs is not conn:
        raise PermissionError("canonical operation changed the borrowed runs connection")
    if not conn.in_transaction:
        raise RuntimeError("canonical operation requires a runs transaction")
    actual = conn.execute("PRAGMA database_list").fetchone()[2]
    if Path(actual).resolve() != runs_db_path(scope.base):
        raise PermissionError("canonical operation has the wrong runs database")


def _read(conn, scope, admission_id):
    _transaction(conn, scope)
    row = conn.execute("SELECT a.* FROM conversation_run_admissions a JOIN runs r "
                       "ON r.run_id=a.run_id WHERE a.admission_id=? AND a.owner_user_id=? "
                       "AND a.universe_id=? AND a.session_id=? AND r.owner_user_id=? "
                       "AND r.queue_universe_id=? AND r.actor=?",
                       (admission_id, scope.owner, scope.universe, scope.session,
                        scope.owner, scope.universe, f"universe:{scope.universe}")).fetchone()
    if row is None:
        raise PermissionError("canonical request unavailable")
    return dict(row)


def _intent_digest(scope, intent):
    expected = {"version", "message", "input_method", "model_choice", "binding_id",
                "binding_revision"}
    if (not isinstance(intent, dict) or set(intent) != expected
            or type(intent["version"]) is not int or intent["version"] != 1
            or not isinstance(intent["message"], str) or not intent["message"].strip()
            or intent["input_method"] not in {"typed", "spoken", "app_action", "unknown"}
            or not isinstance(intent["binding_id"], str) or not intent["binding_id"]
            or type(intent["binding_revision"]) is not int or intent["binding_revision"] < 1
            or (intent["model_choice"] is not None
                and not isinstance(intent["model_choice"], dict))):
        raise ValueError("invalid canonical caller intent")
    return _digest(_json({"universe_id": scope.universe, "intent": intent}))


def find_intent_in_transaction(conn, scope, *, request_key, intent):
    """Read original correlation before consulting mutable preferences/selection."""
    _transaction(conn, scope)
    key_hash = _key(request_key)
    digest = _intent_digest(scope, intent)
    existing = conn.execute("SELECT admission_id,intent_digest FROM conversation_run_admissions "
                            "WHERE owner_user_id=? AND universe_id=? AND session_id=? "
                            "AND request_key_hash=?", (scope.owner, scope.universe, scope.session,
                                                      key_hash)).fetchone()
    if existing is not None:
        if existing["intent_digest"] != digest:
            raise IntentConflict("consumer_request_conflict")
        return _read(conn, scope, existing["admission_id"])
    return None


def reserve_in_transaction(conn, scope, *, request_key, intent, context, selection, inputs):
    """Reserve request + run atomically. Caller then attaches the shared run envelope.

    Source/selection validation is an upstream trusted requirement, not delegated
    to caller-authored JSON. This function does not submit or claim execution.
    """
    existing = find_intent_in_transaction(conn, scope, request_key=request_key, intent=intent)
    if existing is not None:
        return existing
    key_hash = _key(request_key)
    encoded, digest = _json(intent), _intent_digest(scope, intent)
    if (not isinstance(context, dict) or context.get("version") != 1
            or not isinstance(selection, dict) or selection.get("version") != 1
            or not isinstance(selection.get("branch_def_id"), str)
            or not selection["branch_def_id"] or not isinstance(selection.get("reply_key"), str)
            or not selection["reply_key"] or not isinstance(inputs, dict)):
        raise ValueError("invalid prepared canonical context")
    context_json, selection_json = _json(context), _json(selection)
    _json(inputs)  # refuse non-JSON/NaN before the legacy run serializer
    run_id, admission_id = uuid.uuid4().hex[:16], uuid.uuid4().hex
    _insert_run_in_transaction(
        conn, run_id=run_id, branch_def_id=selection["branch_def_id"], thread_id=run_id,
        inputs=inputs, actor=f"universe:{scope.universe}", owner_user_id=scope.owner,
        branch_version_id=selection.get("branch_version_id"), queue_universe_id=scope.universe,
    )
    now = time.time()
    conn.execute("INSERT INTO conversation_run_admissions (admission_id,owner_user_id,universe_id,"
                 "session_id,request_key_hash,intent_digest,intent_json,context_json,selection_json,"
                 "run_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (admission_id, scope.owner, scope.universe, scope.session, key_hash, digest,
                  encoded, context_json, selection_json, run_id, now, now))
    return _read(conn, scope, admission_id)


def _freeze(conn, scope, admission_id):
    row = _read(conn, scope, admission_id)
    if row["projection_state"] == "expired" or row["terminal_json"] is not None:
        return row
    run = conn.execute("SELECT status,output_json FROM runs WHERE run_id=?",
                       (row["run_id"],)).fetchone()
    if run["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
        raise TerminalUnavailable("run has no terminal outcome")
    execution = None
    if run["status"] == "completed":
        try:
            output = json.loads(run["output_json"])
        except (ValueError, TypeError) as exc:
            raise TerminalUnavailable("run terminal output is malformed") from exc
        if not isinstance(output, dict):
            raise TerminalUnavailable("run terminal output is malformed")
        content = output.get(json.loads(row["selection_json"])["reply_key"])
        if not isinstance(content, str) or not content.strip():
            raise TerminalUnavailable("run has no declared terminal text")
        speaker, failure = "universe", None
        execution = _reply_execution(conn, row, content)
    else:
        content, speaker = failure_notice("unknown"), "platform"
        failure = normalize_turn_failure(turn_failure("unknown"))
    terminal = _json({"version": 1, "speaker": speaker, "content": content,
                      "execution": execution, "failure": failure})
    conn.execute("UPDATE conversation_run_admissions SET terminal_json=?,updated_at=? "
                 "WHERE admission_id=? AND terminal_json IS NULL",
                 (terminal, time.time(), admission_id))
    return _read(conn, scope, admission_id)


def _reply_execution(conn, row, content):
    """Historical exact reply evidence only; missing/ambiguous provenance is unknown."""
    from tinyassets.branch_versions import compute_content_hash
    from tinyassets.providers.graph_reply_execution import direct_reply_execution, direct_reply_node

    try:
        selection = json.loads(row["selection_json"])
        if not selection.get("content_hash") or not selection.get("branch_version_id"):
            return None
        version = conn.execute(
            "SELECT snapshot_json FROM branch_versions "
            "WHERE branch_version_id=? AND content_hash=?",
            (selection["branch_version_id"], selection["content_hash"]),
        ).fetchone()
        if version is None:
            return None
        snapshot = json.loads(version[0])
        if compute_content_hash(snapshot) != selection["content_hash"]:
            return None
        node_id = direct_reply_node(snapshot, selection["reply_key"])
        if node_id is None:
            return None
        events = conn.execute(
            "SELECT detail_json FROM run_events "
            "WHERE run_id=? AND node_id=? AND status='ran' LIMIT 2",
            (row["run_id"], node_id),
        ).fetchall()
        return direct_reply_execution(snapshot, selection["reply_key"], content, [
            {"node_id": node_id, "status": "ran", "detail": json.loads(event[0])}
            for event in events
        ])
    except (KeyError, TypeError, ValueError):
        return None  # Never infer a selected/configured model to repair missing evidence.


def _write_pair(scope, row):
    """One conversation transaction, no runs connection or provider lock inside."""
    scope.check()
    path = scope.home / ".conversation_memory.db"
    if path.exists() or path.is_symlink():
        _plain(path)
    terminal = json.loads(row["terminal_json"])
    intent = json.loads(row["intent_json"])
    digest = _digest(row["terminal_json"])
    conn = conversation_store._connect(path)
    try:
        conn.executescript(_PROJECTION_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT session_id,terminal_digest,founder_turn_no,reply_turn_no "
                                "FROM conversation_terminal_projections WHERE admission_id=?",
                                (row["admission_id"],)).fetchone()
        if previous is not None:
            if previous[:2] != (scope.session, digest):
                raise TerminalUnavailable("terminal projection identity conflicts")
            present = conn.execute("SELECT count(*) FROM conversation_turns WHERE session_id=? "
                                   "AND ext_id IN (?,?)", (scope.session,
                                   f"consumer:{row['admission_id']}:founder",
                                   f"consumer:{row['admission_id']}:reply")).fetchone()[0]
            conn.commit()
            return previous[2] if present == 2 else None
        if row["projection_state"] == "committed":
            # A purged/replaced history DB must not be rebuilt from the run.
            conn.commit()
            return None
        number = conn.execute("SELECT coalesce(max(turn_no),0)+1 FROM conversation_turns "
                              "WHERE session_id=?", (scope.session,)).fetchone()[0]
        now = time.time()
        for offset, speaker, content, execution, failure in (
            (0, "founder", intent["message"], "", ""),
            (1, terminal["speaker"], terminal["content"],
             _json(terminal["execution"]) if terminal["execution"] else "",
             _json(terminal["failure"]) if terminal["failure"] else ""),
        ):
            suffix = "founder" if offset == 0 else "reply"
            conn.execute("INSERT INTO conversation_turns (session_id,turn_no,speaker,content,ts,"
                         "ext_id,execution_json,failure_json) VALUES (?,?,?,?,?,?,?,?)",
                         (scope.session, number + offset, speaker, content, now,
                          f"consumer:{row['admission_id']}:{suffix}", execution, failure))
        conn.execute("INSERT INTO conversation_terminal_projections VALUES (?,?,?,?,?,?)",
                     (row["admission_id"], scope.session, digest, number, number + 1, now))
        # No retention delete. The projected pair joins the transcript and stays
        # there; see conversation_store, where the 400-turn ceiling used to be.
        conn.commit()
        return number
    finally:
        conn.close()


def _mark_projected(scope, row, number):
    with runs_transaction(scope) as conn:
        current = _read(conn, scope, row["admission_id"])
        if current["terminal_json"] != row["terminal_json"]:
            raise TerminalUnavailable("terminal changed during projection")
        if number is None:
            conn.execute("UPDATE conversation_run_admissions SET projection_state='expired',"
                         "intent_json=NULL,context_json=NULL,terminal_json=NULL,"
                         "selection_json='{}',conversation_turn_no=NULL,updated_at=? "
                         "WHERE admission_id=?", (time.time(), row["admission_id"]))
        else:
            conn.execute("UPDATE conversation_run_admissions SET projection_state='committed',"
                         "conversation_turn_no=?,updated_at=? WHERE admission_id=?",
                         (number, time.time(), row["admission_id"]))
        return _read(conn, scope, row["admission_id"])


def project_terminal(scope, admission_id):
    """Repair only projection. Never submit work or grant execution authority."""
    with runs_transaction(scope) as conn:
        row = _freeze(conn, scope, admission_id)
    if row["projection_state"] == "expired":
        return row
    number = _write_pair(scope, row)
    return _mark_projected(scope, row, number)
