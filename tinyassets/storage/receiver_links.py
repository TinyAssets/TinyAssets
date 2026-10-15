"""Owner-scoped receiver/link records; IDs are addresses, never bearer grants.

Callers supply server-authenticated principals, not payload identities. The API
service also checks current universe/graph authority. Future delivery acceptance
must call resolve_link_in_transaction in its acceptance transaction, not use a
previous read as authority. This module does not enqueue or transfer files.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tinyassets.runs import runs_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_receivers (
    receiver_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    branch_def_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    description TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    senders_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    revoked_at REAL,
    open_to_all INTEGER NOT NULL DEFAULT 0,
    discoverable INTEGER NOT NULL DEFAULT 0,
    sender_rate_limit INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS graph_receivers_owner
    ON graph_receivers(owner_id, universe_id);
CREATE TABLE IF NOT EXISTS graph_output_links (
    link_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    branch_def_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    receiver_id TEXT NOT NULL REFERENCES graph_receivers(receiver_id),
    receiver_generation INTEGER NOT NULL,
    mapping_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    disconnected_at REAL
);
CREATE INDEX IF NOT EXISTS graph_output_links_owner
    ON graph_output_links(owner_id, universe_id);
"""


#: Columns added after the original receiver table shipped. SQLite has no
#: ``ADD COLUMN IF NOT EXISTS``, and no CHECK constraint is attached: ``ADD
#: COLUMN`` cannot carry one, and a constraint present on a fresh database but
#: absent on a migrated one is two definitions of one rule. The bounds live in
#: ``save_receiver``, where a refusal can name itself.
_MIGRATIONS = (
    ("graph_receivers", "open_to_all", "INTEGER NOT NULL DEFAULT 0"),
    ("graph_receivers", "discoverable", "INTEGER NOT NULL DEFAULT 0"),
    ("graph_receivers", "sender_rate_limit", "INTEGER NOT NULL DEFAULT 0"),
)

#: Indexes over migrated columns, created after the migration rather than in
#: ``_SCHEMA``: the schema script runs against databases that predate the column.
_MIGRATED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS graph_receivers_discoverable "
    "ON graph_receivers(discoverable, revoked_at)",
)

#: ``sender_rate_limit`` is the OWNER's OWN policy on their OWN receiver, and it
#: is off by default. ``NO_SENDER_RATE_LIMIT`` (0) is the stored "no limit".
#:
#: The platform used to impose ``DEFAULT_SENDER_RATE_LIMIT = 60`` per sender per
#: hour on every receiver, with a ``MAX_SENDER_RATE_LIMIT`` ceiling the owner
#: could not lift. That made the platform the author of a policy about someone
#: else's receiver, and it was justified by "a stranger could consume the owner's
#: run admission budget" -- a budget that no longer exists. What bounds a
#: delivered run now is the receiving universe's seats: over the seat count work
#: WAITS, so a chatty sender queues rather than spending anything the owner
#: cannot get back (founder, 2026-09-30).
#:
#: An owner who WANTS a per-sender bound sets any positive number, with no
#: ceiling, and sets 0 to turn it off again.
NO_SENDER_RATE_LIMIT = 0

#: State fields the PLATFORM fills with the sender's authenticated identity, so an
#: owner's downstream node can act on who sent a deliverable. Reserved: they are
#: refused in a receiver's advertised ``input_keys``, which is what makes them
#: unforgeable -- ``connect_output`` validates a sender's mapping against the
#: contract, so a name that cannot enter the contract cannot be mapped onto.
SENDER_ATTRIBUTION_FIELDS = ("delivery_sender_id", "delivery_sender_universe_id")


class ReceiverAccessDenied(PermissionError):
    def __init__(self) -> None:
        super().__init__("receiver_or_link_not_found")


class ReceiverConflict(ValueError):
    """An authorized caller must inspect the current generation and retry."""


def _name(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or value == "*":
        raise ValueError("a non-empty exact principal/resource name is required")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def migrate_in_transaction(conn):
    """Add the exposure columns under the caller's WRITE LOCK, never outside it.

    ``CREATE TABLE IF NOT EXISTS`` in ``_SCHEMA`` is idempotent, so it can run
    before the transaction. ``ALTER TABLE ADD COLUMN`` has no ``IF NOT EXISTS``:
    two openers checking outside the write lock both see the column missing and the
    loser raises ``duplicate column name`` (reproduced by the 2026-09-26
    cross-family review against a shared database). Serializing on BEGIN IMMEDIATE
    means the second opener sees the first's committed column.

    The precondition is CHECKED rather than commented, because the race that
    violating it opens is timing-dependent -- a concurrency test does not reliably
    catch the wrong placement, so hoping is not enough. Same reason and shape as
    ``storage/deliveries._require_transaction``.
    """
    if not conn.in_transaction:
        raise ValueError("receiver column migration requires the caller's write lock")
    for table, column, declaration in _MIGRATIONS:
        existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    # After the columns they read, so an index can never run against a table
    # mid-migration.
    for statement in _MIGRATED_INDEXES:
        conn.execute(statement)


@contextmanager
def transaction(base_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Use the runs DB, not the author store used by legacy webhook tokens."""
    path = runs_db_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        migrate_in_transaction(conn)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _owned_receiver(conn, receiver_id, owner_id, universe_id):
    row = conn.execute(
        "SELECT * FROM graph_receivers WHERE receiver_id=? AND owner_id=? AND universe_id=?",
        (receiver_id, owner_id, universe_id),
    ).fetchone()
    if row is None:
        raise ReceiverAccessDenied()
    return row


def _permitted_receiver(conn, receiver_id, sender_id):
    """INTERACTION: may this principal connect an output and deliver?

    ``open_to_all`` is the owner's explicit choice to accept any authenticated
    principal. It is a column rather than a ``"*"`` entry in ``senders_json``
    because ``_name`` -- the single validator for every principal, resource, link
    and mapping name in this module -- refuses ``"*"`` outright, so admitting the
    sentinel would mean relaxing it everywhere or growing a second definition of
    "a legal principal name". Revoked stays refused either way.
    """
    row = conn.execute(
        "SELECT * FROM graph_receivers WHERE receiver_id=?", (receiver_id,)
    ).fetchone()
    if (
        row is None
        or row["revoked_at"] is not None
        or not (
            row["open_to_all"]
            or sender_id in json.loads(row["senders_json"])
        )
    ):
        raise ReceiverAccessDenied()
    return row


def _visible_receiver(conn, receiver_id, sender_id):
    """VISIBILITY: may this principal read the advertised contract?

    Strictly weaker than delivering. A ``discoverable`` receiver that is not
    ``open_to_all`` is how an owner invites a request for access without accepting
    traffic, so inspection succeeds where delivery refuses.
    """
    try:
        return _permitted_receiver(conn, receiver_id, sender_id)
    except ReceiverAccessDenied:
        row = conn.execute(
            "SELECT * FROM graph_receivers WHERE receiver_id=?", (receiver_id,)
        ).fetchone()
        if row is None or row["revoked_at"] is not None or not row["discoverable"]:
            raise
        return row


def _receiver_view(row, *, private=False):
    view = {
        "receiver_id": row["receiver_id"],
        "owner_id": row["owner_id"],
        "generation": row["generation"],
        "description": row["description"],
        "contract": json.loads(row["contract_json"]),
        "revoked": row["revoked_at"] is not None,
        # Exposure and the usage bound are the sender's own terms of use, not the
        # owner's private state: a sender needs them to decide whether to connect.
        # The owner's universe_id stays private below, as it already was.
        "open_to_all": bool(row["open_to_all"]),
        "discoverable": bool(row["discoverable"]),
        "sender_rate_limit": row["sender_rate_limit"],
    }
    if private:
        view.update(
            {
                "universe_id": row["universe_id"],
                "branch_def_id": row["branch_def_id"],
                "node_id": row["node_id"],
                "allowed_senders": json.loads(row["senders_json"]),
                "source_sha256": row["source_sha256"],
                "snapshot_sha256": row["snapshot_sha256"],
            }
        )
    return view


def save_receiver(
    base_path,
    *,
    owner_id,
    universe_id,
    branch_def_id,
    projection,
    contract,
    allowed_senders,
    description="",
    receiver_id=None,
    expected_generation=None,
    open_to_all=None,
    discoverable=None,
    sender_rate_limit=None,
):
    """``None`` for an exposure field means KEEP: create defaults, update preserves.

    Not "replace the whole exposure declaration like ``allowed_senders``", which was
    the first shape and was wrong. ``allowed_senders`` is a REQUIRED argument, so
    omitting it fails loudly; these are optional, so omitting them would silently
    change policy -- and silently reset a deliberately tightened ``sender_rate_limit``
    back to off, which LOOSENS a bound. Closing an exposure is an explicit
    ``false``. (Cross-family review, 2026-09-26.)

    ``sender_rate_limit`` is the owner's own optional policy: any positive number
    of accepted deliveries per sender per hour, with no ceiling, or
    ``NO_SENDER_RATE_LIMIT`` (0) for none, which is the default on create.
    """
    for name in (owner_id, universe_id, branch_def_id):
        _name(name)
    if not isinstance(allowed_senders, list):
        raise ValueError("allowed_senders must be an explicit list")
    senders = sorted({_name(sender) for sender in allowed_senders})
    if not isinstance(description, str):
        raise ValueError("description must be text")
    # Exposure is the owner's explicit act, so a near-miss value is refused rather
    # than coerced: "1" and "false" must not silently open a private node. Only the
    # None sentinel is accepted as "unspecified".
    for label, value in (("open_to_all", open_to_all), ("discoverable", discoverable)):
        if value is not None and type(value) is not bool:
            raise ValueError(f"{label} must be true or false")
    # No upper bound: the owner decides how much traffic their own receiver takes.
    # 0 is "no limit", which is also what a receiver is created with.
    if sender_rate_limit is not None and (
        type(sender_rate_limit) is not int or sender_rate_limit < 0
    ):
        raise ValueError(
            "sender_rate_limit must be a whole number: 0 for no limit, "
            "or accepted deliveries per sender per hour"
        )
    with transaction(base_path) as conn:
        creating = receiver_id is None
        if receiver_id is None:
            if expected_generation is not None:
                raise ValueError("new receiver has no expected generation")
            receiver_id = uuid.uuid4().hex
        else:
            row = conn.execute(
                "SELECT * FROM graph_receivers WHERE receiver_id=?",
                (receiver_id,),
            ).fetchone()
            if row is None:
                # A caller-chosen address is created only while it is free.
                # An address that already exists is never overwritten from here.
                if expected_generation is not None:
                    raise ReceiverAccessDenied()
                _name(receiver_id)
                creating = True
            elif row["owner_id"] != owner_id or row["universe_id"] != universe_id:
                raise ReceiverAccessDenied()
            else:
                creating = False
        if creating:
            # Explicit column names, not positional VALUES: the positional form
            # silently mis-assigned every column past a schema addition.
            conn.execute(
                "INSERT INTO graph_receivers ("
                "receiver_id, owner_id, universe_id, branch_def_id, node_id, generation, "
                "description, contract_json, senders_json, snapshot_json, source_sha256, "
                "snapshot_sha256, created_at, revoked_at, open_to_all, discoverable, "
                "sender_rate_limit) VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,NULL,?,?,?)",
                (
                    receiver_id,
                    owner_id,
                    universe_id,
                    branch_def_id,
                    projection.entry_node_id,
                    description,
                    _json(contract),
                    _json(senders),
                    projection.snapshot_json,
                    projection.source_sha256,
                    projection.snapshot_sha256,
                    time.time(),
                    int(bool(open_to_all)),
                    int(bool(discoverable)),
                    NO_SENDER_RATE_LIMIT if sender_rate_limit is None
                    else sender_rate_limit,
                ),
            )
        else:
            row = _owned_receiver(conn, receiver_id, owner_id, universe_id)
            if row["revoked_at"] is not None:
                raise ReceiverConflict("receiver_revoked")
            if type(expected_generation) is not int or row["generation"] != expected_generation:
                raise ReceiverConflict("receiver_generation_changed")
            conn.execute(
                "UPDATE graph_receivers SET branch_def_id=?, node_id=?, generation=generation+1, "
                "description=?, contract_json=?, senders_json=?, snapshot_json=?, source_sha256=?, "
                "snapshot_sha256=?, open_to_all=?, discoverable=?, sender_rate_limit=? "
                "WHERE receiver_id=? AND generation=?",
                (
                    branch_def_id,
                    projection.entry_node_id,
                    description,
                    _json(contract),
                    _json(senders),
                    projection.snapshot_json,
                    projection.source_sha256,
                    projection.snapshot_sha256,
                    row["open_to_all"] if open_to_all is None else int(open_to_all),
                    row["discoverable"] if discoverable is None else int(discoverable),
                    row["sender_rate_limit"] if sender_rate_limit is None
                    else sender_rate_limit,
                    receiver_id,
                    expected_generation,
                ),
            )
        return _receiver_view(
            _owned_receiver(conn, receiver_id, owner_id, universe_id), private=True
        )


def inspect_receiver(base_path, *, receiver_id, principal_id, owner_universe_id=None):
    _name(principal_id)
    with transaction(base_path) as conn:
        if owner_universe_id is not None:
            row = _owned_receiver(conn, receiver_id, principal_id, owner_universe_id)
            return _receiver_view(row, private=True)
        return _receiver_view(_visible_receiver(conn, receiver_id, principal_id))


def discover_receivers(base_path, *, principal_id, query="", limit=25):
    """Receivers whose owners marked them discoverable, in the SENDER view.

    The point of the primitive is that user B can reach user A's node; until now
    nothing let B learn A's ``receiver_id``, so an address you cannot obtain was
    unreachable in practice. ``_name(principal_id)`` keeps the read attributable:
    there is no anonymous listing. A receiver its owner did not mark
    ``discoverable`` never appears here, whatever the search text matches, and
    neither does a revoked one -- the same two conditions ``_visible_receiver``
    applies to a by-id read, so discovery cannot become the wider door.
    """
    _name(principal_id)
    if not isinstance(query, str):
        raise ValueError("receiver search text must be a string")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("receiver search limit must be a whole number from 1 to 100")
    needle = query.strip().lower()
    with transaction(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM graph_receivers WHERE discoverable=1 AND revoked_at IS NULL "
            "ORDER BY created_at DESC",
        ).fetchall()
    matched = [
        row
        for row in rows
        if not needle
        or needle in row["description"].lower()
        or needle in row["owner_id"].lower()
    ]
    return {"receivers": [_receiver_view(row) for row in matched[:limit]]}


def sender_is_permitted(base_path, *, receiver_id, sender_id) -> bool:
    """Whether this principal could actually deliver here, right now.

    The same ``_permitted_receiver`` question delivery asks, exposed so a caller
    deciding whether a connection is worth offering does not have to reconstruct
    it from a view. Reconstruction was wrong: the SENDER-facing
    ``_receiver_view`` deliberately omits ``allowed_senders``, so
    ``open_to_all or sender in allowed_senders`` reads False for a receiver that
    names this sender explicitly -- refusing a sender who can in fact deliver.

    Read-only and boolean by design: it discloses nothing about WHY, matching
    the uniform ``receiver_or_link_not_found`` envelope. Never authority for an
    acceptance -- that must call ``resolve_link_in_transaction`` in its own
    transaction, as this module's header says.
    """
    _name(sender_id)
    with transaction(base_path) as conn:
        try:
            _permitted_receiver(conn, receiver_id, sender_id)
        except ReceiverAccessDenied:
            return False
    return True


def revoke_receiver(base_path, *, receiver_id, owner_id, universe_id, expected_generation):
    _name(owner_id)
    with transaction(base_path) as conn:
        row = _owned_receiver(conn, receiver_id, owner_id, universe_id)
        if type(expected_generation) is not int or row["generation"] != expected_generation:
            raise ReceiverConflict("receiver_generation_changed")
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE graph_receivers SET revoked_at=? WHERE receiver_id=?",
                (time.time(), receiver_id),
            )
        return {"receiver_id": receiver_id, "revoked": True, "generation": row["generation"]}


def connect_output(
    base_path,
    *,
    owner_id,
    universe_id,
    branch_def_id,
    node_id,
    receiver_id,
    expected_generation,
    mapping,
):
    for name in (owner_id, universe_id, branch_def_id, node_id):
        _name(name)
    if not isinstance(mapping, dict):
        raise ValueError("output mapping must be an object")
    for source, target in mapping.items():
        _name(source)
        _name(target)
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("two outputs cannot map to the same receiver input")
    with transaction(base_path) as conn:
        receiver = _permitted_receiver(conn, receiver_id, owner_id)
        if type(expected_generation) is not int or receiver["generation"] != expected_generation:
            raise ReceiverConflict("receiver_generation_changed")
        contract = json.loads(receiver["contract_json"])
        accepted = {item["name"] for item in contract}
        required = {item["name"] for item in contract if item["required"]}
        mapped = set(mapping.values())
        if mapped - accepted or required - mapped:
            raise ValueError("mapping does not satisfy receiver contract")
        link_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO graph_output_links VALUES (?,?,?,?,?,?,?,?,?,NULL)",
            (
                link_id,
                owner_id,
                universe_id,
                branch_def_id,
                node_id,
                receiver_id,
                expected_generation,
                _json(mapping),
                time.time(),
            ),
        )
        return {
            "link_id": link_id,
            "receiver_id": receiver_id,
            "receiver_generation": expected_generation,
        }


def _owned_link(conn, link_id, owner_id, universe_id):
    row = conn.execute(
        "SELECT * FROM graph_output_links WHERE link_id=? AND owner_id=? AND universe_id=?",
        (link_id, owner_id, universe_id),
    ).fetchone()
    if row is None:
        raise ReceiverAccessDenied()
    return row


def disconnect_output(base_path, *, link_id, owner_id, universe_id):
    _name(owner_id)
    with transaction(base_path) as conn:
        _owned_link(conn, link_id, owner_id, universe_id)
        conn.execute(
            "UPDATE graph_output_links SET disconnected_at=COALESCE(disconnected_at,?) "
            "WHERE link_id=?",
            (time.time(), link_id),
        )
        return {"link_id": link_id, "disconnected": True}


def resolve_link_in_transaction(conn, *, link_id, owner_id, universe_id):
    """INTERNAL acceptance check. Never return its private snapshot to a sender."""
    _name(owner_id)
    link = _owned_link(conn, link_id, owner_id, universe_id)
    if link["disconnected_at"] is not None:
        raise ReceiverAccessDenied()
    receiver = _permitted_receiver(conn, link["receiver_id"], owner_id)
    if receiver["generation"] != link["receiver_generation"]:
        raise ReceiverConflict("receiver_generation_changed")
    return dict(link), dict(receiver)
