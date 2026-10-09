"""Durable delivery intent and run reservation in one runs-database transaction.

Internal persistence only, not an intake or execution API. The service must hold
the author-store authority fence, validate source provenance, receiver authority,
inputs and resource/file admission BEFORE accepting a new occurrence. Payloads
never supply identity. This module neither submits a worker nor transfers bytes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from tinyassets import runs
from tinyassets.storage import receiver_links as links

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS graph_deliveries (
        delivery_id TEXT PRIMARY KEY,
        sender_id TEXT NOT NULL,
        sender_universe_id TEXT NOT NULL,
        link_id TEXT NOT NULL REFERENCES graph_output_links(link_id),
        occurrence_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        receiver_id TEXT NOT NULL REFERENCES graph_receivers(receiver_id),
        receiver_owner_id TEXT NOT NULL,
        receiver_universe_id TEXT NOT NULL,
        receiver_generation INTEGER NOT NULL,
        source_branch_id TEXT NOT NULL,
        source_node_id TEXT NOT NULL,
        source_run_id TEXT,
        receiver_branch_id TEXT NOT NULL,
        receiver_node_id TEXT NOT NULL,
        inputs_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        accepted_at REAL NOT NULL,
        UNIQUE(sender_id, sender_universe_id, link_id, occurrence_id)
    )""",
    """CREATE TABLE IF NOT EXISTS graph_delivery_attempts (
        delivery_id TEXT NOT NULL REFERENCES graph_deliveries(delivery_id),
        attempt INTEGER NOT NULL CHECK(attempt > 0),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
        state TEXT NOT NULL CHECK(state IN
            ('pending', 'executing', 'completed', 'failed', 'cancelled', 'interrupted')),
        claim_token TEXT,
        execution_started_at REAL,
        finished_at REAL,
        safe_reason TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        PRIMARY KEY(delivery_id, attempt)
    )""",
    # Sender->receiver custody provenance. inputs_json stays the sender envelope so
    # the replay digest is byte-identical, leaving this the only durable home for
    # the mapping. Receiver projections never read it; the receiver resolves its
    # own copy through its run bindings, which survive sender erasure.
    """CREATE TABLE IF NOT EXISTS graph_delivery_files (
        delivery_id TEXT NOT NULL REFERENCES graph_deliveries(delivery_id),
        field_name TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        sender_file_id TEXT NOT NULL,
        receiver_file_id TEXT NOT NULL UNIQUE,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
        PRIMARY KEY(delivery_id, field_name, ordinal)
    )""",
    # The receiving owner's answer to one delivery: what came of it. Only the
    # receiver side writes it; the sender reads it back with its receipt, and its
    # agent is told once (``noticed_at``) so a belief built on the gap can die.
    """CREATE TABLE IF NOT EXISTS graph_delivery_answers (
        delivery_id TEXT PRIMARY KEY REFERENCES graph_deliveries(delivery_id),
        outcome TEXT NOT NULL CHECK(outcome IN ('resolved', 'declined')),
        note TEXT NOT NULL DEFAULT '',
        answered_at REAL NOT NULL,
        noticed_at REAL
    )""",
    "CREATE INDEX IF NOT EXISTS graph_delivery_files_delivery "
    "ON graph_delivery_files(delivery_id)",
    "CREATE INDEX IF NOT EXISTS graph_deliveries_sender "
    "ON graph_deliveries(sender_id, sender_universe_id)",
    "CREATE INDEX IF NOT EXISTS graph_deliveries_receiver "
    "ON graph_deliveries(receiver_owner_id, receiver_universe_id)",
    # Backs the per-sender rate window count, which runs on every first acceptance.
    "CREATE INDEX IF NOT EXISTS graph_deliveries_sender_window "
    "ON graph_deliveries(receiver_id, sender_id, accepted_at)",
    "CREATE INDEX IF NOT EXISTS graph_delivery_attempts_state ON graph_delivery_attempts(state)",
)


#: Rolling window an OWNER's optional per-sender policy is measured over. There
#: is no platform default and no ceiling; see ``receiver_links`` for why.
SENDER_RATE_WINDOW_SECONDS = 3600.0


class OccurrenceConflict(ValueError):
    def __init__(self):
        super().__init__("occurrence_conflict")


def sender_window_count(conn, *, receiver_id, sender_id, window_seconds):
    """Accepted deliveries from ONE principal to ONE receiver inside the window.

    Keyed on ``sender_id`` alone, not ``(sender_id, sender_universe_id)``: a limit
    scoped per universe would let a sender multiply their budget by founding more
    of them. Counted inside the caller's acceptance transaction, so two concurrent
    senders cannot both read an under-limit count and both insert.
    """
    _require_transaction(conn)
    links._name(receiver_id)
    links._name(sender_id)
    return conn.execute(
        "SELECT count(*) FROM graph_deliveries WHERE receiver_id=? AND sender_id=? "
        "AND accepted_at >= ?",
        (receiver_id, sender_id, time.time() - window_seconds),
    ).fetchone()[0]


def _exact_json(value):
    """Reject lossy JSON conversion, including non-string object keys."""
    active = set()

    def check(item):
        if isinstance(item, (dict, list)):
            if id(item) in active:
                raise ValueError("deliverable cannot contain circular references")
            active.add(id(item))
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("deliverable object keys must be strings")
                children = item.values()
            else:
                children = item
            for child in children:
                check(child)
            active.remove(id(item))
        elif item is not None and type(item) not in {str, bool, int, float}:
            raise ValueError("deliverable must contain exact JSON values or admitted file metadata")

    check(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    )


@contextmanager
def transaction(base_path):
    # Schema work precedes the acceptance transaction, never a nested writer.
    runs.initialize_runs_db(base_path)
    with links.transaction(base_path) as conn:
        # execute (not executescript) retains the caller-owned transaction.
        for statement in _SCHEMA:
            conn.execute(statement)
        yield conn


def _require_transaction(conn):
    if not conn.in_transaction:
        raise ValueError("delivery acceptance requires an active caller-owned transaction")


def _receipt(conn, row, *, receiver_view=False):
    attempt = conn.execute(
        "SELECT * FROM graph_delivery_attempts WHERE delivery_id=? ORDER BY attempt DESC LIMIT 1",
        (row["delivery_id"],),
    ).fetchone()
    if attempt is None:
        raise RuntimeError("accepted delivery is missing its durable run reservation")
    result = {
        "delivery_id": row["delivery_id"],
        "receiver_id": row["receiver_id"],
        "receiver_generation": row["receiver_generation"],
        "attempt": attempt["attempt"],
        "status": {"pending": "accepted", "executing": "running"}.get(
            attempt["state"],
            attempt["state"],
        ),
        "accepted_at": row["accepted_at"],
        "execution_started_at": attempt["execution_started_at"],
        "finished_at": attempt["finished_at"],
        "reason": attempt["safe_reason"],
        "outcome": "pending",
    }
    answer = conn.execute(
        "SELECT outcome, note, answered_at FROM graph_delivery_answers WHERE delivery_id=?",
        (row["delivery_id"],),
    ).fetchone()
    if answer is not None:
        # ``note`` is the receiving owner's words; the API layer envelopes it.
        result["outcome"] = answer["outcome"]
        result["answer"] = {"answered_at": answer["answered_at"], "note": answer["note"]}
    if receiver_view:
        result["run_id"] = attempt["run_id"]
        # Who sent this. The record always carried it; until now nothing returned
        # it, so a receiving owner could not see who delivered to them -- which an
        # open receiver makes load-bearing. Receiver side only: a sender learns
        # nothing new about the receiver from its own receipt.
        result["sender_id"] = row["sender_id"]
        result["sender_universe_id"] = row["sender_universe_id"]
    return result


_FILE_RECORD = ("field_name", "ordinal", "sender_file_id", "receiver_file_id",
                "sha256", "size_bytes")


def _file_records(transfer):
    records = transfer.get("records") if isinstance(transfer, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("delivery file transfer plan is empty")
    ordered = sorted(records, key=lambda item: (item["field_name"], item["ordinal"]))
    for field in {item["field_name"] for item in ordered}:
        positions = [item["ordinal"] for item in ordered if item["field_name"] == field]
        if positions != list(range(len(positions))):
            raise ValueError("delivery file bundle order is not contiguous")
    if len({item["receiver_file_id"] for item in ordered}) != len(ordered):
        raise ValueError("delivery file plan repeats a receiver file")
    return ordered


def _run_inputs(validated_inputs, transfer):
    """The receiver's own run row never carries a sender identifier.

    ``inputs_json`` on the delivery keeps the sender envelope so the replay digest
    stays byte-identical; the run the receiver owns and reads gets its own
    references, which is also why it survives sender erasure.
    """
    if transfer is None:
        return validated_inputs
    result = dict(validated_inputs)
    for field in {item["field_name"] for item in _file_records(transfer)}:
        references = [item["reference"] for item in _file_records(transfer)
                      if item["field_name"] == field]
        bundle = transfer.get("kinds", {}).get(field) == "file_bundle"
        result[field] = references if bundle else references[0]
    return result


def _bind_delivery_files(conn, *, delivery_id, run_id, owner_id, universe_id, transfer):
    """Bind the RECEIVER copies inside the acceptance transaction.

    Binding here, not at worker start, is what makes an accepted copy immune to
    the unbound custody lease by construction: there is no window between "the
    receiver was told yes" and the binding that protects the bytes.
    """
    from tinyassets.storage import run_files

    records = _file_records(transfer)
    for field in sorted({item["field_name"] for item in records}):
        run_files.bind_in_transaction(
            conn, run_id=run_id, owner_id=owner_id, universe_id=universe_id,
            field_name=field,
            file_ids=[item["receiver_file_id"] for item in records
                      if item["field_name"] == field],
        )
    conn.executemany(
        "INSERT INTO graph_delivery_files (delivery_id, field_name, ordinal, sender_file_id, "
        "receiver_file_id, sha256, size_bytes) VALUES (?,?,?,?,?,?,?)",
        [(delivery_id, *(item[key] for key in _FILE_RECORD)) for item in records],
    )


def _verify_delivery_files(conn, delivery_id, transfer):
    expected = [tuple(item[key] for key in _FILE_RECORD) for item in _file_records(transfer)]
    stored = [
        tuple(row[key] for key in _FILE_RECORD)
        for row in conn.execute(
            "SELECT * FROM graph_delivery_files WHERE delivery_id=? ORDER BY field_name, ordinal",
            (delivery_id,),
        )
    ]
    if stored != expected:
        raise OccurrenceConflict()


def accept_in_transaction(
    conn: sqlite3.Connection,
    *,
    sender_id,
    sender_universe_id,
    link_id,
    occurrence_id,
    request_payload,
    validated_inputs,
    source_run_id=None,
    file_transfer=None,
):
    """Persist validated intent and its first receiver run atomically.

    Caller checks authority and admission under the author-store fence. Link
    policy/generation is rechecked here in the SAME transaction as acceptance.
    Retries of an already accepted occurrence don't require a still-active link
    and never create another run. Same-content intentional sends need distinct
    occurrence IDs. The public-safe result never includes the receiver's run ID.
    """
    _require_transaction(conn)
    for name in (sender_id, sender_universe_id, link_id, occurrence_id):
        links._name(name)
    if not isinstance(validated_inputs, dict):
        raise ValueError("validated receiver inputs must be an object")
    canonical_request = _exact_json(
        {
            "payload": request_payload,
            "inputs": validated_inputs,
            "source_run_id": source_run_id,
        }
    )
    digest = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    prior = conn.execute(
        "SELECT * FROM graph_deliveries WHERE sender_id=? AND sender_universe_id=? "
        "AND link_id=? AND occurrence_id=?",
        (sender_id, sender_universe_id, link_id, occurrence_id),
    ).fetchone()
    if prior is not None:
        if prior["request_sha256"] != digest:
            raise OccurrenceConflict()
        if file_transfer is not None:
            # An accepted occurrence keeps its original custody rows. A replay may
            # not re-bind, re-copy or repoint provenance.
            _verify_delivery_files(conn, prior["delivery_id"], file_transfer)
        return _receipt(conn, prior)

    link, receiver = links.resolve_link_in_transaction(
        conn,
        link_id=link_id,
        owner_id=sender_id,
        universe_id=sender_universe_id,
    )
    delivery_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex[:16]
    now = time.time()
    conn.execute(
        """INSERT INTO graph_deliveries (
            delivery_id, sender_id, sender_universe_id, link_id, occurrence_id, request_sha256,
            receiver_id, receiver_owner_id, receiver_universe_id, receiver_generation,
            source_branch_id, source_node_id, source_run_id, receiver_branch_id, receiver_node_id,
            inputs_json, snapshot_json, snapshot_sha256, accepted_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            delivery_id,
            sender_id,
            sender_universe_id,
            link_id,
            occurrence_id,
            digest,
            receiver["receiver_id"],
            receiver["owner_id"],
            receiver["universe_id"],
            receiver["generation"],
            link["branch_def_id"],
            link["node_id"],
            source_run_id,
            receiver["branch_def_id"],
            receiver["node_id"],
            _exact_json(validated_inputs),
            receiver["snapshot_json"],
            receiver["snapshot_sha256"],
            now,
        ),
    )
    runs._insert_run_in_transaction(
        conn,
        run_id=run_id,
        thread_id=run_id,
        branch_def_id=receiver["branch_def_id"],
        inputs=_run_inputs(validated_inputs, file_transfer),
        run_name="received deliverable",
        actor=f"universe:{receiver['universe_id']}",
        owner_user_id=receiver["owner_id"],
        queue_universe_id=receiver["universe_id"],
    )
    if file_transfer is not None:
        _bind_delivery_files(
            conn, delivery_id=delivery_id, run_id=run_id,
            owner_id=receiver["owner_id"], universe_id=receiver["universe_id"],
            transfer=file_transfer,
        )
    conn.execute(
        "INSERT INTO graph_delivery_attempts (delivery_id, attempt, run_id, state, created_at) "
        "VALUES (?,1,?,'pending',?)",
        (delivery_id, run_id, now),
    )
    row = conn.execute(
        "SELECT * FROM graph_deliveries WHERE delivery_id=?",
        (delivery_id,),
    ).fetchone()
    return _receipt(conn, row)


def read_receipt_in_transaction(conn, *, delivery_id, principal_id, universe_id):
    """Return only the requesting party's projection; caller checks current ACL."""
    links._name(principal_id)
    row = conn.execute(
        "SELECT * FROM graph_deliveries WHERE delivery_id=? AND "
        "((sender_id=? AND sender_universe_id=?) OR "
        "(receiver_owner_id=? AND receiver_universe_id=?))",
        (delivery_id, principal_id, universe_id, principal_id, universe_id),
    ).fetchone()
    if row is None:
        raise links.ReceiverAccessDenied()
    receiver_view = (row["receiver_owner_id"], row["receiver_universe_id"]) == (
        principal_id,
        universe_id,
    )
    return _receipt(conn, row, receiver_view=receiver_view)


ANSWER_OUTCOMES = ("resolved", "declined")
MAX_ANSWER_NOTE_CHARS = 4000


def answer_in_transaction(conn, *, delivery_id, principal_id, universe_id, outcome, note):
    """The RECEIVING owner records what came of a delivery; a re-answer replaces it.

    A new answer clears ``noticed_at``, so the sender's agent hears the latest one.
    """
    _require_transaction(conn)
    links._name(principal_id)
    if outcome not in ANSWER_OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(ANSWER_OUTCOMES)}")
    if not isinstance(note, str) or len(note) > MAX_ANSWER_NOTE_CHARS:
        raise ValueError(f"note must be text of at most {MAX_ANSWER_NOTE_CHARS} characters")
    row = conn.execute(
        "SELECT * FROM graph_deliveries WHERE delivery_id=? AND receiver_owner_id=? "
        "AND receiver_universe_id=?",
        (delivery_id, principal_id, universe_id),
    ).fetchone()
    if row is None:
        raise links.ReceiverAccessDenied()
    conn.execute(
        "INSERT INTO graph_delivery_answers(delivery_id, outcome, note, answered_at, noticed_at) "
        "VALUES (?, ?, ?, ?, NULL) ON CONFLICT(delivery_id) DO UPDATE SET "
        "outcome=excluded.outcome, note=excluded.note, answered_at=excluded.answered_at, "
        "noticed_at=NULL",
        (delivery_id, outcome, note.strip(), time.time()),
    )
    return _receipt(conn, row, receiver_view=True)


def list_sent_in_transaction(conn, *, principal_id, universe_id, limit=20):
    """The sender's own deliveries from this universe, newest first."""
    links._name(principal_id)
    rows = conn.execute(
        "SELECT * FROM graph_deliveries WHERE sender_id=? AND sender_universe_id=? "
        "ORDER BY accepted_at DESC LIMIT ?",
        (principal_id, universe_id, max(1, min(int(limit), 100))),
    ).fetchall()
    return [_receipt(conn, row) for row in rows]


def take_new_answers_in_transaction(conn, *, principal_id, universe_id):
    """Answers to this sender's deliveries not yet told to it, marked told.

    Exactly once: the select and the mark share the caller's transaction.
    """
    _require_transaction(conn)
    links._name(principal_id)
    rows = conn.execute(
        "SELECT d.* FROM graph_deliveries d JOIN graph_delivery_answers a "
        "ON a.delivery_id = d.delivery_id WHERE d.sender_id=? AND d.sender_universe_id=? "
        "AND a.noticed_at IS NULL ORDER BY a.answered_at",
        (principal_id, universe_id),
    ).fetchall()
    if rows:
        conn.executemany(
            "UPDATE graph_delivery_answers SET noticed_at=? WHERE delivery_id=?",
            [(time.time(), row["delivery_id"]) for row in rows],
        )
    return [_receipt(conn, row) for row in rows]


def _locked_attempt(conn, guard):
    _require_transaction(conn)
    guard.require_held(conn)
    row = conn.execute(
        "SELECT a.*, r.status AS run_status FROM graph_delivery_attempts a "
        "JOIN runs r ON r.run_id=a.run_id WHERE a.delivery_id=? AND a.attempt=?",
        (guard.delivery_id, guard.attempt),
    ).fetchone()
    if row is None:
        raise RuntimeError("delivery attempt or reserved run is missing")
    return row


_TERMINAL_REASONS = {
    "completed": "",
    "failed": "receiver_processing_failed",
    "cancelled": "receiver_cancelled",
    "interrupted": "receiver_execution_interrupted",
}


def recover_attempt_in_transaction(conn, guard):
    """Reconcile only while holding the attempt's OS lock; never auto-replay.

    Returns pending only for a proven unstarted, non-cancelled attempt. Startup
    may mark its run interrupted; that alone must not destroy the durable intent.
    A started attempt without a terminal run is ambiguous, even if no provider
    call was actually made. Raw private errors never enter the sender receipt.
    Caller commits before deciding whether to dispatch the returned pending row.
    """
    row = _locked_attempt(conn, guard)
    if row["state"] in _TERMINAL_REASONS:
        return dict(row)
    run_status = row["run_status"]
    if row["state"] == "pending" and row["execution_started_at"] is None:
        if run_status in {runs.RUN_STATUS_QUEUED, runs.RUN_STATUS_INTERRUPTED}:
            return dict(row)
        if run_status == runs.RUN_STATUS_RUNNING:
            # Contradictory evidence must fail closed, not reset running work.
            run_status = runs.RUN_STATUS_INTERRUPTED
    elif run_status not in _TERMINAL_REASONS:
        run_status = runs.RUN_STATUS_INTERRUPTED
    if run_status not in _TERMINAL_REASONS:
        raise RuntimeError("unrecognized delivery run state")
    now = time.time()
    conn.execute(
        "UPDATE graph_delivery_attempts SET state=?, safe_reason=?, finished_at=? "
        "WHERE delivery_id=? AND attempt=?",
        (run_status, _TERMINAL_REASONS[run_status], now, guard.delivery_id, guard.attempt),
    )
    if row["run_status"] not in _TERMINAL_REASONS:
        conn.execute(
            "UPDATE runs SET status=?, error=?, finished_at=? WHERE run_id=?",
            (run_status, _TERMINAL_REASONS[run_status], now, row["run_id"]),
        )
    # Preserve the ordinary run lifecycle: terminal status owes workspace
    # cleanup in the SAME database transaction. Never release a lease directly.
    runs._enqueue_workspace_terminal(conn, guard.database_path.parent, row["run_id"])
    return dict(_locked_attempt(conn, guard))


def reconcile_attempt(base_path, guard):
    """Recover under the held lock and finish cross-database cleanup after commit.

    This reconciles one reservation only; scheduling and receiver authority are
    the service's responsibility. The existing universe sweep repairs a crash
    between the root commit and the second WAL, just like ordinary run recovery.
    """
    with transaction(base_path) as conn:
        row = recover_attempt_in_transaction(conn, guard)
        terminal = row["state"] in _TERMINAL_REASONS
        if terminal:
            # Recovery just enqueued the terminal work atomically. A later
            # reconciliation must kick that work, not append a duplicate wipe.
            owed = conn.execute(
                "SELECT count(*) FROM workspace_outbox WHERE run_id=? AND done_at IS NULL",
                (row["run_id"],),
            ).fetchone()[0]
            if not owed:
                owed = runs._enqueue_workspace_terminal(conn, base_path, row["run_id"])
            workspace_base = runs._workspace_terminal_base(conn, base_path, row["run_id"])
    if terminal:
        runs._finish_terminal_workspace_release(
            base_path,
            row["run_id"],
            workspace_base,
            local_owed=owed,
        )
    return row


def start_attempt_in_transaction(conn, guard):
    """Fence execution BEFORE any provider call/effect; caller commits first.

    Receiver authority and admission must already have been checked by the
    service. This is an internal lifecycle seam, not a receiving grant. Returns
    a fresh claim token or None if this attempt may not execute.
    """
    row = recover_attempt_in_transaction(conn, guard)
    if row["state"] != "pending" or row["execution_started_at"] is not None:
        return None
    token = uuid.uuid4().hex
    changed = conn.execute(
        "UPDATE graph_delivery_attempts SET state='executing', claim_token=?, "
        "execution_started_at=? WHERE delivery_id=? AND attempt=? "
        "AND state='pending' AND execution_started_at IS NULL",
        (token, time.time(), guard.delivery_id, guard.attempt),
    ).rowcount
    if changed != 1:
        raise RuntimeError("delivery attempt claim lost")
    # Only the unstarted proof above permits clearing startup's interrupted mark.
    # Provider admission uses queued status; _invoke_graph owns running status.
    conn.execute(
        "UPDATE runs SET status=?, error='', finished_at=NULL WHERE run_id=?",
        (runs.RUN_STATUS_QUEUED, row["run_id"]),
    )
    return token


def finish_attempt_in_transaction(conn, guard, *, claim_token):
    """Publish only a terminal run's safe result under its exact worker claim."""
    row = _locked_attempt(conn, guard)
    if not claim_token or row["claim_token"] != claim_token:
        raise RuntimeError("delivery attempt claim lost")
    if row["state"] != "executing":
        raise RuntimeError("delivery attempt is not executing")
    if row["run_status"] not in _TERMINAL_REASONS:
        raise RuntimeError("receiver run has not reached a terminal state")
    conn.execute(
        "UPDATE graph_delivery_attempts SET state=?, safe_reason=?, finished_at=? "
        "WHERE delivery_id=? AND attempt=? AND claim_token=?",
        (
            row["run_status"],
            _TERMINAL_REASONS[row["run_status"]],
            time.time(),
            guard.delivery_id,
            guard.attempt,
            claim_token,
        ),
    )
