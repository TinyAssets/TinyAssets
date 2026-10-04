"""Self-service account deletion.

Google Play requires any app that lets people create an account to offer a
deletion path inside the app AND on a public web page; ``/legal`` promises the
same erasure. ``delete_account`` is that path.

**The row set is derived from the schema, not from a list.** The first draft
carried a hand-written table list, which is the "two definitions of one fact"
failure this repo has paid for repeatedly: ``scoped_reset``'s reviewed inventory
already names 45 root tables, other modules create ~30 more it has never heard
of, and any list would rot at the next migration while silently under-deleting.
So deletion reads the live schema: a table with a ``universe_id`` loses the
deleted universe's rows, a table with one of :data:`PRINCIPAL_KEYS` loses the
deleted person's rows, and the only hand-maintained parts are the short,
reviewable exception sets below — :data:`PRESERVED_TABLES` (commons and ledgers
that are not personal data), :data:`REDACTED_TABLES` (audit rows that survive
without the person or the content) and :data:`BLOCKING_TABLES` (money that is
not ours to discard). A migration that adds a universe- or person-keyed table is
covered the day it lands; ``tests/test_account_deletion.py`` fails if a new
column name means a person's rows would be missed.

**What refuses.** The foreign-ownership and active-work analysis is the operator
path's, reused query for query: another founder bound to the same home, a
foreign grant or foreign-actor row inside it, a live daemon, an open vote, an
in-flight request or task. Those refuse loudly rather than destroying someone
else's rows or racing running work — the platform's one hard invariant is that a
user's action never reaches another user's data. The deliberate divergence is
dependent request rows (``request_admissions``, ``request_admission_events``,
``branch_tasks_v2``): a *reset* preserves them because it keeps the account, a
*deletion* removes the person's own and blocks only on foreign ones, because a
user's own admitted request is their data and leaving it would make "we deleted
your data" false.

**Order.** The home directory is renamed out of the way first (atomic — a crash
leaves a clearly named orphan under ``.deleting/``, never a half-deleted live
universe), then every root-database row in one transaction, then the satellite
databases, then the directory, then billing, then identity. Each phase is
isolated: a failure in one never prevents the next, because the phase that must
not be skipped is the one that stops the money. Anything unfinished is written
to a durable receipt under ``.account-deletions/`` and logged at ERROR — never
swallowed (Hard Rule 8).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

logger = logging.getLogger(__name__)

_WORKOS_USER_ID = re.compile(r"user_[A-Za-z0-9]+\Z")
_WORKOS_BASE_URL = "https://api.workos.com"
_STAGING_DIR = ".deleting"
_RECEIPT_DIR = ".account-deletions"

#: What deletion deliberately leaves behind. The retention sentences on
#: ``/legal`` and ``/account`` say exactly this — change all three together.
RETAINED = (
    "audit records, with the actor replaced by an opaque id and their summary, "
    "target and payload emptied",
    "commons and ledger rows that are not personal data: published author and "
    "branch definitions, goals, and settlement history",
    "invoices held by the payment processor (Stripe)",
    "server backups, until they age out on the configured retention schedule",
)

#: Column names that mean "this row belongs to a person". Applied only to
#: tables that are NOT universe-scoped — see :func:`deletion_plan`.
#:
#: ``created_by`` is deliberately absent: it is authorship attribution, not
#: ownership. Sweeping it would delete the branches and snapshots this person
#: authored inside *someone else's* universe, which is the one thing the
#: platform must never do.
PRINCIPAL_KEYS = (
    "founder_sub",
    "user_id",
    "actor_id",
    "owner_id",
    "owner_user_id",
    "owner_actor",
    "owner_actor_id",
    "owner_principal_id",
    "bound_by_actor_id",
    "authorizing_principal_id",
    "principal_id",
    "remixed_by_user_id",
)

#: The universe column. A table carrying it loses the deleted universe's rows —
#: and ONLY those. A person-keyed row inside another universe is that universe's
#: operational state, not this account's to delete.
UNIVERSE_KEY = "universe_id"

#: Tables carrying a universe column whose rows are nonetheless ONLY ever the
#: owner's: deleted by the owner key alone, never by the universe sweep. A
#: person's saved UI choice about someone else's universe is theirs, so deleting
#: that universe's owner must not take it -- and sweeping by universe could,
#: because a collaborator's save can commit between the foreign-row check and
#: the delete (gpt-6-astra review, 2026-09-26). Left behind, the row names a
#: universe that no longer exists and goes when its own owner is deleted.
OWNER_ONLY_TABLES = MappingProxyType({
    "pins": "owner_id",
    "universe_app_ui": "owner_user_id",
    "command_center_adoptions": "owner_id",
    "command_center_update_requests": "owner_id",
    "command_center_update_policies": "owner_id",
    "command_center_policy_requests": "owner_id",
    "command_center_auto_receipts": "owner_id",
    "command_center_auto_status": "owner_id",
})

#: Universe-scoped tables that ALSO hold a person-keyed row worth removing
#: everywhere: an access grant is this account's access, so deleting the account
#: revokes it wherever it points. Removing it takes nothing from the universe
#: that granted it — only the deleted person's ability to reach it.
PERSON_KEYED_DESPITE_UNIVERSE = MappingProxyType({
    "universe_acl": "actor_id",
    # Defence in depth: the binding is deleted by home anyway, and a foreign
    # binding to the same home is a blocker, but a binding is the person's own
    # row and should not depend on the universe sweep to disappear.
    "founder_home": "founder_sub",
    # A grant is this person's grant OF THEIR OWN connection, so it goes with
    # them wherever it points — and it must, because the grant references the
    # connection with no ON DELETE clause: leaving a grant on someone else's
    # universe makes deleting the parent connection raise, which abandoned the
    # whole outbound store and left the credential behind.
    "outbound_connection_grants": "owner_user_id",
    # Personal preferences must disappear even after the owner's home is rebound.
    "universe_model_preferences": "owner_user_id",
    # Private turn progress follows its owner, including former-home history.
    "agent_turns": "owner_user_id",
    "agent_turn_rounds": "owner_user_id",
    "agent_turn_tools": "owner_user_id",
    # Canonical private request/reply correlation also follows its owner after
    # a home rebind; delete it before its run FK parent in the satellite sweep.
    "conversation_run_admissions": "owner_user_id",
    # Accepted execution inputs belong to their run owner independently of
    # old home bindings or personal delivery controls. Delete before parent run.
    "run_input_admissions": "owner_id",
})

#: Reached through a parent rather than by their own key, and/or entangled in
#: two-way ON DELETE CASCADE. Deleted explicitly and counted before any of them
#: is touched — see ``_delete_root_rows``.
INDIRECTLY_SCOPED_TABLES = frozenset({
    "branch_heads",
    "vote_ballots",
    "request_admissions",
    "request_admission_events",
    "branch_tasks_v2",
})

#: Not personal data: published commons, provenance, and money already settled.
#: These keep their rows even when they carry a principal column.
PRESERVED_TABLES = frozenset({
    "author_definitions",
    "branch_definitions",
    # Immutable public release history is referenced by other owners. Private
    # series ownership and release evidence use owner_id and are swept normally.
    "command_center_releases",
    "goals",
    "goal_canonicals",
    # Which branch is canonical for a goal is a COMMONS pointer other people
    # rely on; `bound_by_actor_id` records who bound it, which is attribution,
    # not ownership. The operator path preserves it and so must this one —
    # deleting the binder's account must not un-canonicalise a shared goal.
    # The attribution is redacted instead (see ATTRIBUTION_COLUMNS).
    "canonical_bindings",
    "gate_claims",
    "transaction_log",
    "take_rate_log",
    "payout_wallet",
    "escrow_balance",
    "treasury_balance",
    "settlement_batch",
    "pending_settlement",
    "bounty_pool_balance",
    "royalty_payout",
    "scoped_reset_leases",
    "scoped_reset_operations",
    "deleted_principals",
})

#: Audit rows survive as evidence that something happened, stripped of who did
#: it and what it said — which is what makes the ``/legal`` retention sentence
#: ("content-free audit records keyed by an opaque id") true rather than a hope.
REDACTED_TABLES = frozenset({"action_records"})

#: Preserved rows that nonetheless name the deleted person. The row stays — it is
#: commons or evidence — and the attribution is replaced by the opaque
#: fingerprint, so no preserved row keeps an identifier of someone who asked to
#: be deleted.
ATTRIBUTION_COLUMNS = MappingProxyType({
    "canonical_bindings": ("bound_by_actor_id",),
    # A universe the person owned that SURVIVES them (not the home, which the
    # universe sweep deletes with its owner row) keeps an owner -- the opaque
    # fingerprint -- rather than none. With none it would become unattributed,
    # which is never refused: a collaborator could fill it without bound
    # (gpt-6-astra, PR #4139). The fingerprint has no home, so it is charged at
    # the free tier, and it names nobody.
    "universe_owner": ("owner_id",),
})

#: Money the person is a party to. Refuse the deletion rather than discard it:
#: an unresolved stake is the host's to settle first.
BLOCKING_TABLES = frozenset({"escrow_locks", "staker_escrow_budget"})

#: (table, column) pairs where a value other than the owner is normal and must
#: NOT block: the column records who granted or acted, not who owns the row.
_FOREIGN_ROWS_EXPECTED = frozenset({
    ("universe_acl", "actor_id"),  # every grant here is one the owner issued
})

#: Columns that identify WHOSE a row is, for the purpose of refusing to destroy
#: it. Deliberately wider than :data:`PRINCIPAL_KEYS`: `created_by` must never
#: be a deletion key (it would delete this person's rows out of other people's
#: universes) but it is exactly the signal that a row inside *this* universe
#: belongs to someone else — a daemon instance B started and stopped here is
#: still B's, and no liveness check will notice it (Codex round 3, finding 1).
FOREIGN_OWNERSHIP_KEYS = PRINCIPAL_KEYS + ("created_by",)


class AccountDeletionError(RuntimeError):
    """Deletion refused. The message never carries a secret."""


class AccountDeletionBlocked(AccountDeletionError):
    """Refused because someone else's data, or live work, is in scope."""


def _fingerprint(principal: str) -> str:
    """Short, content-free label for receipts and logs."""
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()[:16]


def principal_digest(principal: str) -> str:
    """One-way digest used as the tombstone key.

    The tombstone outlives the account, so storing the raw WorkOS user id there
    would keep an identifier of a person who asked to be deleted — while
    ``/account`` says the sign-in identity is removed. A full-length digest lets
    first-contact recognise a deleted principal without retaining one
    (Codex round 3, finding 5); full length, not the short label, because a
    collision here would wrongly refuse a *different* person a universe.
    """
    return hashlib.sha256((principal or "").strip().encode("utf-8")).hexdigest()


def _rmtree(path: Path) -> None:
    def _onerror(func: Any, target: Any, _exc: Any) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(path, onexc=_onerror)  # type: ignore[call-arg]  # 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_onerror)


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass  # another deletion is staged in it, or it is already gone


# --------------------------------------------------------------------------- #
# filesystem
# --------------------------------------------------------------------------- #


def _home_dir(root: Path, home: str) -> Path:
    """The home directory inside ``root`` — refuses traversal and odd names."""
    candidate = (root / home).resolve(strict=False)
    if candidate.parent != root or candidate.name != home:
        raise AccountDeletionError("home path escapes the data root")
    return candidate


def _stage_home(root: Path, home: str) -> Path | None:
    """Rename the home directory under ``.deleting/`` (atomic) and return the
    staged path, or None when there is no directory to remove."""
    target = _home_dir(root, home)
    if not target.exists() and not target.is_symlink():
        return None
    if target.is_symlink() or not target.is_dir():
        raise AccountDeletionError("home path is not a plain directory")
    staging = root / _STAGING_DIR
    staging.mkdir(exist_ok=True)
    staged = staging / f"{home}-{int(time.time())}-{secrets.token_hex(4)}"
    target.rename(staged)
    return staged


# --------------------------------------------------------------------------- #
# schema helpers
# --------------------------------------------------------------------------- #


def _tables(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not str(row[0]).startswith("sqlite_")
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _delete(
    conn: sqlite3.Connection,
    counts: dict[str, int],
    table: str,
    where: str,
    params: tuple[Any, ...],
) -> None:
    deleted = conn.execute(f'DELETE FROM "{table}" WHERE {where}', params).rowcount
    if deleted:
        counts[table] = counts.get(table, 0) + int(deleted)


def deletion_plan(
    conn: sqlite3.Connection, *, principal: str, home: str
) -> dict[str, list[tuple[str, str]]]:
    """What the schema says belongs to this account: ``{table: [(column, kind)]}``
    with kind ``universe`` or ``principal``. Read-only; the same function the
    tests read, so what is asserted is what runs."""
    plan: dict[str, list[tuple[str, str]]] = {}
    for table in _tables(conn):
        if table in PRESERVED_TABLES or table in REDACTED_TABLES:
            continue
        if table in INDIRECTLY_SCOPED_TABLES:
            continue  # deleted through its parent, by home only
        cols = _columns(conn, table)
        owner_key = OWNER_ONLY_TABLES.get(table)
        if owner_key and owner_key in cols:
            plan[table] = [(owner_key, "principal")]
            continue
        if UNIVERSE_KEY in cols:
            # Universe-scoped: this account owns only its own universe's rows.
            # Its rows in someone else's universe stay with that universe.
            matched = []
            if home:
                matched.append((UNIVERSE_KEY, "universe"))
            extra = PERSON_KEYED_DESPITE_UNIVERSE.get(table)
            if extra and extra in cols:
                matched.append((extra, "principal"))
            if matched:
                plan[table] = matched
            continue
        matched = [(key, "principal") for key in PRINCIPAL_KEYS if key in cols]
        if matched:
            plan[table] = matched
    return plan


# --------------------------------------------------------------------------- #
# what refuses
# --------------------------------------------------------------------------- #


def _redacted_owner_exemption(table: str, key: str, tables: set[str] | list[str]) -> str:
    """SQL excluding PROVEN redacted owners from the foreign-ownership scan.

    After A deletes their account, a universe A owned that survives -- say, B's
    home -- carries owner ``deleted:<fingerprint>``. That names nobody, so it
    must not block B's own deletion forever (gpt-6-astra round 2, PR #4139).

    Narrow on purpose (round 3): ONLY ``universe_owner.owner_id``, and only a
    value that matches an actual tombstone -- the fingerprint is the first 16 hex
    of the same digest `deleted_principals` stores. A prefix test alone let a
    live local principal named ``deleted:...`` have its rows swept.
    """
    if (table, key) != ("universe_owner", "owner_id") or "deleted_principals" not in tables:
        return ""
    return (
        ' AND "owner_id" NOT IN '
        "(SELECT 'deleted:' || substr(founder_sub, 1, 16) FROM deleted_principals)"
    )


def deletion_blockers(
    conn: sqlite3.Connection, *, principal: str, home: str
) -> list[str]:
    """Reasons this account may not be deleted right now.

    Foreign-ownership and active-work analysis reuses the operator path's
    queries (``scoped_reset._inspect_database``) so the two agree about whose
    rows are whose and about what "still running" means.
    """
    from tinyassets.scoped_reset import (
        _ACTIVE_DAEMON_STATES,
        _ACTIVE_REQUEST_STATES,
        _ACTIVE_ROLLOUT_STATES,
        _ACTIVE_TASK_STATES,
    )

    blockers: list[str] = []
    tables = set(_tables(conn))

    for table in sorted(BLOCKING_TABLES & tables):
        cols = _columns(conn, table)
        for key in PRINCIPAL_KEYS + ("staker_id",):
            if key in cols and _count(
                conn, f'SELECT COUNT(*) FROM "{table}" WHERE "{key}" = ?', (principal,)
            ):
                blockers.append(f"unsettled financial state in {table}")
                break

    if not home:
        return blockers

    checks = (
        ("another founder is bound to this command center",
         "SELECT COUNT(*) FROM founder_home WHERE universe_id = ? AND founder_sub <> ?",
         (home, principal)),
        ("another person holds access to this command center",
         "SELECT COUNT(*) FROM universe_acl WHERE universe_id = ? AND actor_id <> ?",
         (home, principal)),
        ("another person's requests live in this command center",
         "SELECT COUNT(*) FROM user_requests WHERE universe_id = ? AND user_id <> ?",
         (home, principal)),
        ("another person authored branches here",
         "SELECT COUNT(*) FROM branches WHERE universe_id = ? "
         "AND created_by NOT IN (?, 'system')",
         (home, principal)),
        ("another person authored snapshots here",
         "SELECT COUNT(*) FROM universe_snapshots WHERE universe_id = ? "
         "AND created_by NOT IN (?, 'system')",
         (home, principal)),
        ("another person opened votes here",
         "SELECT COUNT(*) FROM vote_windows WHERE universe_id = ? "
         "AND created_by NOT IN (?, 'system')",
         (home, principal)),
        ("another person cast ballots here",
         "SELECT COUNT(*) FROM vote_ballots AS ballot "
         "JOIN vote_windows AS vote ON vote.vote_id = ballot.vote_id "
         "WHERE vote.universe_id = ? AND ballot.user_id <> ?",
         (home, principal)),
        ("another person's admitted work depends on requests here",
         "SELECT COUNT(*) FROM request_admissions AS dep "
         "JOIN user_requests AS request ON request.request_id = dep.request_id "
         "WHERE request.universe_id = ? AND request.user_id <> ?",
         (home, principal)),
        ("another person's tasks depend on requests here",
         "SELECT COUNT(*) FROM branch_tasks_v2 AS dep "
         "JOIN user_requests AS request ON request.request_id = dep.request_id "
         "WHERE request.universe_id = ? AND request.user_id <> ?",
         (home, principal)),
        ("a daemon is still running for this command center",
         "SELECT COUNT(*) FROM author_runtime_instances WHERE universe_id = ? "
         f"AND lower(status) IN ({','.join('?' for _ in _ACTIVE_DAEMON_STATES)})",
         (home, *sorted(_ACTIVE_DAEMON_STATES))),
        ("a request is still in flight",
         "SELECT COUNT(*) FROM user_requests WHERE universe_id = ? "
         f"AND lower(status) IN ({','.join('?' for _ in _ACTIVE_REQUEST_STATES)})",
         (home, *sorted(_ACTIVE_REQUEST_STATES))),
        ("a vote is still open",
         "SELECT COUNT(*) FROM vote_windows WHERE universe_id = ? "
         "AND lower(status) = 'open'",
         (home,)),
        ("a task is still in flight",
         "SELECT COUNT(*) FROM branch_tasks_v2 WHERE universe_id = ? "
         f"AND lower(status) IN ({','.join('?' for _ in _ACTIVE_TASK_STATES)})",
         (home, *sorted(_ACTIVE_TASK_STATES))),
        ("a rollout is still active",
         "SELECT COUNT(*) FROM request_admission_rollouts WHERE universe_id = ? "
         f"AND lower(state) IN ({','.join('?' for _ in _ACTIVE_ROLLOUT_STATES)})",
         (home, *sorted(_ACTIVE_ROLLOUT_STATES))),
    )
    for reason, sql, params in checks:
        table = sql.split(" FROM ")[1].split()[0]
        if table not in tables:
            continue
        try:
            if _count(conn, sql, params):
                blockers.append(reason)
        except sqlite3.OperationalError:
            # A renamed column must fail loudly, not read as "nothing matched".
            blockers.append(f"cannot evaluate {table} — schema changed")

    # The enumerated checks above name the tables the operator path knows. Any
    # OTHER universe-scoped table can still hold a foreign person's row — a
    # daemon instance B started here and stopped, say, which is terminal and so
    # matches no active-work check (Codex round 3, finding 1). Sweeping the
    # universe would delete it. Refuse instead: whose row it is does not depend
    # on whether it is still running.
    for table, keys in deletion_plan(conn, principal=principal, home=home).items():
        if not any(kind == "universe" for _, kind in keys):
            continue
        cols = _columns(conn, table)
        for key in FOREIGN_OWNERSHIP_KEYS:
            if key not in cols or (table, key) in _FOREIGN_ROWS_EXPECTED:
                continue
            try:
                foreign = _count(
                    conn,
                    f'SELECT COUNT(*) FROM "{table}" '
                    f'WHERE universe_id = ? AND "{key}" NOT IN (?, \'system\', \'\')'
                    + _redacted_owner_exemption(table, key, tables),
                    (home, principal),
                )
            except sqlite3.OperationalError:
                blockers.append(f"cannot evaluate {table} — schema changed")
                break
            if foreign:
                blockers.append(f"another person's rows are in {table} here")
                break
    return blockers


# --------------------------------------------------------------------------- #
# the row work
# --------------------------------------------------------------------------- #


def _delete_root_rows(
    conn: sqlite3.Connection, *, principal: str, home: str, counts: dict[str, int]
) -> None:
    tables = set(_tables(conn))
    fingerprint = _fingerprint(principal)

    # ON DELETE CASCADE means a delete can remove rows in tables the plan has
    # not reached yet — Queue Epoch 2 cascades in BOTH directions, and deleting
    # user_accounts takes user_sessions with it. Counting as we go therefore
    # reports whichever table the cascade happened to reach last and understates
    # the rest (Codex rounds 2 and 3). So: count EVERY targeted table first,
    # then delete. The receipt has to say what actually went, or "we deleted
    # your data" is unverifiable.
    indirect = (
        ("request_admission_events",
         "request_id IN (SELECT request_id FROM user_requests WHERE universe_id = ?)"),
        ("request_admissions",
         "request_id IN (SELECT request_id FROM user_requests WHERE universe_id = ?)"),
        ("branch_tasks_v2", "universe_id = ?"),
        ("branch_heads",
         "branch_id IN (SELECT branch_id FROM branches WHERE universe_id = ?)"),
        ("vote_ballots",
         "vote_id IN (SELECT vote_id FROM vote_windows WHERE universe_id = ?)"),
    )
    # Grouped by table, with the predicates OR-ed: a row matching two of them
    # (founder_home matches both its universe and its person) is one deleted
    # row, and the receipt must say rows, not predicate hits.
    grouped: dict[str, tuple[list[str], list[Any]]] = {}

    def _target(table: str, where: str, params: tuple[Any, ...]) -> None:
        if table not in tables:
            return
        clauses, values = grouped.setdefault(table, ([], []))
        clauses.append(f"({where})")
        values.extend(params)

    if home:
        for table, where in indirect:
            _target(table, where, (home,))
    for table, keys in deletion_plan(conn, principal=principal, home=home).items():
        for column, kind in keys:
            _target(table, f'"{column}" = ?', (home if kind == "universe" else principal,))

    plan = {
        table: (" OR ".join(clauses), tuple(values))
        for table, (clauses, values) in grouped.items()
    }
    for table, (where, params) in plan.items():
        found = _count(conn, f'SELECT COUNT(*) FROM "{table}" WHERE {where}', params)
        if found:
            counts[table] = counts.get(table, 0) + found
    for table, (where, params) in plan.items():
        conn.execute(f'DELETE FROM "{table}" WHERE {where}', params)

    # Audit rows survive, stripped of the person and the content. This is what
    # makes the retention sentence on /legal true rather than aspirational.
    if "action_records" in tables:
        cols = _columns(conn, "action_records")
        sets: list[str] = []
        set_params: list[Any] = []
        if "actor_id" in cols:
            sets.append("actor_id = ?")
            set_params.append(f"deleted:{fingerprint}")
        for blanked in ("summary", "target_id"):
            if blanked in cols:
                sets.append(f"{blanked} = ''")
        if "payload_json" in cols:
            sets.append("payload_json = '{}'")
        where: list[str] = []
        where_params: list[Any] = []
        if "actor_id" in cols:
            where.append("actor_id = ?")
            where_params.append(principal)
        if home and UNIVERSE_KEY in cols:
            where.append("universe_id = ?")
            where_params.append(home)
        if sets and where:
            redacted = conn.execute(
                f"UPDATE action_records SET {', '.join(sets)} "
                f"WHERE {' OR '.join(where)}",
                (*set_params, *where_params),
            ).rowcount
            if redacted:
                counts["action_records (redacted)"] = int(redacted)

    # Preserved rows that name the person keep the row and lose the name, so no
    # retained row carries an identifier of someone who asked to be deleted.
    for table, columns in ATTRIBUTION_COLUMNS.items():
        if table not in tables:
            continue
        cols = _columns(conn, table)
        for column in columns:
            if column not in cols:
                continue
            changed = conn.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                (f"deleted:{fingerprint}", principal),
            ).rowcount
            if changed:
                key = f"{table} (redacted)"
                counts[key] = counts.get(key, 0) + int(changed)


def write_tombstone(base_path: str | Path, principal: str) -> None:
    """Record that this principal deleted their account, keyed by digest.

    Written BEFORE anything is staged or deleted. Round 3 reproduced the race
    the other order leaves open: between staging the home and committing the
    tombstone, a second device's still-valid token reached first-contact, saw a
    live binding and no tombstone, and rebuilt a fresh universe that outlived
    the deletion. Writing the fence first means the worst case is a tombstoned
    principal whose deletion then failed — refused a new universe until a host
    clears it, which is the safe direction to fail.
    """
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.principals import named_principal

    subject = named_principal(principal)
    if not subject:
        return
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            "INSERT INTO deleted_principals (founder_sub, deleted_at) VALUES (?, ?) "
            "ON CONFLICT(founder_sub) DO UPDATE SET deleted_at = excluded.deleted_at",
            (principal_digest(subject), time.time()),
        )


def _root_databases(root: Path) -> list[Path]:
    """Every store at the data root except the main database, which
    ``_delete_root_rows`` owns.

    A registry that reads the directory cannot go stale the way a hand-listed
    one does — round 3 found ``.authoring.db`` and ``.automations.db``
    untouched because the list named only ``outbound.db`` and ``.auth.db``.
    Per-universe stores go with the universe directory. The shared package
    store is nested outside those directories and must be visited explicitly.
    """
    from tinyassets.command_center_packages import database_path
    from tinyassets.storage import DB_FILENAME

    stores = [
        p for p in root.glob("*.db") if p.is_file() and p.name != DB_FILENAME
    ]
    if database_path(root).is_file():
        stores.append(database_path(root))
    return sorted(stores)


def _delivery_deletion_targets(conn, *, principal: str, home: str):
    """Explicit FK children of scoped graph endpoints; never delete peer runs.

    Endpoint ownership keeps the existing universe-scoped deletion rule. A
    two-party receipt naming the deleted account is personal control-plane data,
    unlike the receiver's independently owned run and accepted input content.
    """
    live = set(_tables(conn))
    targets = []
    receiver_sql = "SELECT receiver_id FROM graph_receivers WHERE universe_id = ?"
    link_sql = ("SELECT link_id FROM graph_output_links WHERE universe_id = ? "
                f"OR receiver_id IN ({receiver_sql})")
    if {"graph_receivers", "graph_output_links"} <= live:
        if {"graph_deliveries", "graph_delivery_attempts"} <= live:
            delivery_where = (
                "sender_id = ? OR receiver_owner_id = ? OR sender_universe_id = ? "
                "OR receiver_universe_id = ? "
                f"OR link_id IN ({link_sql}) OR receiver_id IN ({receiver_sql})"
            )
            params = (principal, principal, home, home, home, home, home)
            child_where = ("delivery_id IN (SELECT delivery_id "
                           f"FROM graph_deliveries WHERE {delivery_where})")
            targets.append(("graph_delivery_attempts", child_where, params))
            if "graph_delivery_files" in live:
                # Sender->receiver custody provenance is control-plane mapping, not
                # the bytes: it precedes its parent so the FK cannot dangle, and it
                # is NOT how the receiver resolves its accepted input. That resolves
                # through the receiver's own run bindings and receiver-owned objects,
                # which this deletion never touches for the other party.
                targets.append(("graph_delivery_files", child_where, params))
            targets.append(("graph_deliveries", delivery_where, params))
        targets.extend([
            ("graph_output_links", f"link_id IN ({link_sql})", (home, home)),
            ("graph_receivers", "universe_id = ?", (home,)),
        ])
    return targets


def _delete_satellite_rows(
    path: Path, *, principal: str, home: str, counts: dict[str, int], label: str
) -> None:
    """Apply the same schema-derived rule to one store beside the root database:
    universe-keyed and person-keyed rows go, counted before any delete."""
    if not path.is_file():
        return
    # Physical file custody cannot be erased by generic row ordering. The
    # account tombstone already prevents new capture/binding/read; settle exact
    # owned inventory before opening this store's deletion transaction.
    from tinyassets.runs import runs_db_path
    if path == runs_db_path(path.parent):
        from tinyassets.run_file_erasure import erase_owner_files
        erase_owner_files(path.parent, owner_id=principal)
    conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        local: dict[str, int] = {}
        with conn:
            from tinyassets.command_center_packages import database_path, ensure_pin_owners

            package_store = path == database_path(path.parent.parent)
            if package_store:
                # DDL and legacy owner backfill must precede the deletion plan,
                # even when this is the first access since upgrading the store.
                conn.execute("BEGIN IMMEDIATE")
                ensure_pin_owners(conn)
            plan = deletion_plan(conn, principal=principal, home=home)
            targets = _delivery_deletion_targets(conn, principal=principal, home=home)
            if package_store and home:
                # Only unowned legacy pins may fall back to the current home.
                # Explicit peer ownership always wins over universe identity.
                targets.append(("pins", "owner_id = ? OR (owner_id = '' AND universe_id = ?)",
                                (principal, home)))
            delivery_tables = {target[0] for target in targets}
            from tinyassets.run_file_erasure import settled_deletion_targets
            file_targets = settled_deletion_targets(conn, principal=principal)
            targets = file_targets + targets
            explicitly_handled = delivery_tables | {target[0] for target in file_targets}
            # Child rows whose own columns name neither the person nor the
            # universe, but whose parent is going.
            if "outbound_connector_artifacts" in plan:
                for side in ("parent_artifact_id", "child_artifact_id"):
                    targets.append((
                        "outbound_connector_artifact_edges",
                        f"{side} IN (SELECT artifact_id FROM "
                        "outbound_connector_artifacts WHERE owner_user_id = ?)",
                        (principal,),
                    ))
            for table, keys in plan.items():
                if table in explicitly_handled:
                    continue  # one union predicate per table: count every row once
                for column, kind in keys:
                    value = home if kind == "universe" else principal
                    targets.append((table, f'"{column}" = ?', (value,)))
            live = {t for t in _tables(conn)}
            targets = [t for t in targets if t[0] in live]
            for table, where, params in targets:
                try:
                    found = _count(
                        conn, f'SELECT COUNT(*) FROM "{table}" WHERE {where}', params
                    )
                except sqlite3.OperationalError:
                    continue
                if found:
                    local[table] = local.get(table, 0) + found
            # A peer's receiver survives, but no longer advertises the deleted
            # account as an allowed sender. Do not alter unrelated grants or
            # generations: existing intake rechecks the current sender list.
            if "graph_receivers" in live:
                for receiver_id, encoded in conn.execute(
                    "SELECT receiver_id, senders_json FROM graph_receivers"
                ).fetchall():
                    senders = json.loads(encoded)
                    if principal in senders:
                        conn.execute(
                            "UPDATE graph_receivers SET senders_json = ? WHERE receiver_id = ?",
                            (json.dumps([s for s in senders if s != principal]), receiver_id),
                        )
            for table, where, params in targets:
                try:
                    conn.execute(f'DELETE FROM "{table}" WHERE {where}', params)
                except sqlite3.OperationalError:
                    continue
        for table, n in local.items():
            counts[f"{label}:{table}"] = counts.get(f"{label}:{table}", 0) + n
    finally:
        conn.close()


def _delete_authoring_blobs(root: Path, session_ids: list[str]) -> int:
    """Remove the on-disk blobs of the deleted person's authoring sessions.

    The rows go with ``.authoring.db``; the files they point at are laid out as
    ``.authoring_blobs/<session_id>/<handle_id>`` and would otherwise survive
    the account that uploaded them."""
    from tinyassets.authoring.store import BLOB_DIRNAME

    removed = 0
    blob_root = root / BLOB_DIRNAME
    if not blob_root.is_dir():
        return 0
    for session_id in session_ids:
        candidate = (blob_root / session_id).resolve(strict=False)
        if candidate.parent != blob_root.resolve(strict=False):
            continue  # never follow a crafted session id out of the blob root
        if candidate.is_dir():
            _rmtree(candidate)
            removed += 1
    _rmdir_if_empty(blob_root)
    return removed


def _authoring_session_ids(root: Path, principal: str) -> list[str]:
    """Session ids the principal owns, read before their rows are deleted."""
    from tinyassets.authoring.store import DB_FILENAME

    path = root / DB_FILENAME
    if not path.is_file():
        return []
    conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        if "authoring_sessions" not in _tables(conn):
            return []
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT session_id FROM authoring_sessions WHERE owner_id = ?",
                (principal,),
            )
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# billing + identity (injectable; defaults talk to Stripe and WorkOS)
# --------------------------------------------------------------------------- #


def cancel_stripe_billing(home: str) -> str:
    """Cancel the home's subscription NOW. Returns ``cancelled`` / ``none`` /
    ``not_configured`` / ``unavailable``; raises on a Stripe failure."""
    from tinyassets.billing import BillingUnavailable
    from tinyassets.billing.stripe_adapter import (
        billing_enabled,
        cancel_subscription,
        find_active_subscription,
    )

    if not home or not billing_enabled():
        return "not_configured"
    try:
        subscription_id = find_active_subscription(home)
    except BillingUnavailable:
        return "unavailable"
    if not subscription_id:
        return "none"
    cancel_subscription(subscription_id, immediately=True)
    return "cancelled"


def delete_workos_user(
    user_id: str, *, request: Callable[..., Any] | None = None
) -> str:
    """Delete the WorkOS user record — which is also what ends sessions this
    process cannot enumerate: another device's refresh handle is opaque here, so
    upstream deletion, not local sweeping, is what stops it renewing. Returns
    ``deleted`` / ``not_configured`` / ``not_applicable``; raises
    :class:`AccountDeletionError` (secret-free) on an API failure."""
    api_key = os.environ.get("WORKOS_API_KEY", "").strip()
    if not api_key:
        return "not_configured"
    if _WORKOS_USER_ID.fullmatch(user_id) is None:
        return "not_applicable"
    req = urllib.request.Request(
        f"{_WORKOS_BASE_URL}/user_management/users/{urllib.parse.quote(user_id, safe='')}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "tinyassets-account-deletion/1.0",
        },
        method="DELETE",
    )
    opener = request or urllib.request.urlopen
    try:
        with opener(req, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "deleted"  # already gone — the outcome the caller wanted
        raise AccountDeletionError(
            f"WorkOS user deletion failed (HTTP {exc.code})"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AccountDeletionError("WorkOS user deletion request failed") from None
    if status in (200, 202, 204):
        return "deleted"
    raise AccountDeletionError(f"WorkOS user deletion returned HTTP {status}")


# --------------------------------------------------------------------------- #
# the operation
# --------------------------------------------------------------------------- #


def _write_unfinished_receipt(root: Path, receipt: dict[str, Any]) -> str:
    """Persist a receipt for a deletion that did not finish every phase, so the
    host can complete it. Content-free: a fingerprint, never a principal."""
    try:
        directory = root / _RECEIPT_DIR
        directory.mkdir(exist_ok=True)
        path = directory / f"{receipt['principal_fingerprint']}-{int(time.time())}.json"
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        return str(path)
    except OSError as exc:  # the log is then the only record, and it is loud
        logger.error(
            "account deletion %s: could not write the unfinished-work receipt: %s",
            receipt.get("principal_fingerprint"), exc.__class__.__name__,
        )
        return ""


def pending_deletions(base_path: str | Path) -> list[dict[str, Any]]:
    """Receipts for deletions that left a phase unfinished (host-facing)."""
    directory = Path(base_path) / _RECEIPT_DIR
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def delete_account(
    base_path: str | Path,
    *,
    founder_sub: str,
    cancel_billing: Callable[[str], str] | None = None,
    delete_identity: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Delete everything the platform holds for ``founder_sub`` and return a
    content-free receipt.

    Raises :class:`AccountDeletionError` before changing anything when the
    principal is empty or the bound home path is unsafe, and
    :class:`AccountDeletionBlocked` when someone else's data or live work is in
    scope. Once the home directory has been staged the account is gone; each
    later phase runs independently, so a failure in one never stops the phase
    that cancels the money, and anything unfinished lands in a durable receipt.
    """
    from tinyassets.daemon_server import _connect, get_founder_home, initialize_author_server
    from tinyassets.principals import named_principal

    principal = named_principal(founder_sub)
    if not principal:
        raise AccountDeletionError("no authenticated principal to delete")
    root = Path(base_path).resolve(strict=False)
    fingerprint = _fingerprint(principal)
    counts: dict[str, int] = {}

    initialize_author_server(root)
    home = get_founder_home(root, principal) or ""
    if home:
        _home_dir(root, home)  # refuse an unsafe binding before touching anything

    with _connect(root) as conn:
        blockers = deletion_blockers(conn, principal=principal, home=home)
    if blockers:
        raise AccountDeletionBlocked("; ".join(blockers))

    # Finish any admitted vault persistence before tombstoning. Later writers
    # check the tombstone inside their transaction under this same admission.
    # Release BEFORE staging: Windows cannot rename an open admission-lock file.
    if home:
        from tinyassets.provider_assignment import provider_assignment_admission

        with provider_assignment_admission().exclusive(_home_dir(root, home)):
            write_tombstone(root, principal)
    else:
        write_tombstone(root, principal)
    blob_sessions = _authoring_session_ids(root, principal)

    staged = _stage_home(root, home) if home else None
    failures: list[str] = []

    def _phase(name: str, run: Callable[[], Any]) -> Any:
        try:
            return run()
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            failures.append(name)
            logger.error(
                "account deletion %s: phase %s failed: %s",
                fingerprint, name, exc.__class__.__name__,
            )
            return None

    def _root_rows() -> None:
        with _connect(root) as conn:
            _delete_root_rows(conn, principal=principal, home=home, counts=counts)

    _phase("root_rows", _root_rows)
    # Every store at the data root, not a named few: an account's data is
    # wherever a store keyed it, and the directory is the only registry that
    # cannot go stale.
    for store in _root_databases(root):
        label = store.stem.lstrip(".") or store.name
        _phase(
            f"store:{label}",
            lambda s=store, la=label: _delete_satellite_rows(
                s, principal=principal, home=home, counts=counts, label=la
            ),
        )
    if blob_sessions:
        def _blobs() -> None:
            removed = _delete_authoring_blobs(root, blob_sessions)
            if removed:
                counts["authoring_blobs (directories)"] = removed

        _phase("authoring_blobs", _blobs)

    home_removed = staged is None
    staged_path = ""
    if staged is not None:
        def _remove() -> None:
            nonlocal home_removed
            _rmtree(staged)
            home_removed = True
            _rmdir_if_empty(staged.parent)

        _phase("home_directory", _remove)
        if not home_removed:
            staged_path = str(staged)

    if home:
        # Session records, Custom Rules and review switches (rules.db) live
        # beside the universe in .agent-sessions/<home>/, not inside it, so the
        # home removal above does not take them. Anything but plain directories
        # all the way down is refused and reported, never followed.
        from tinyassets.agent_sessions import RECORDS_DIR

        def _session_records() -> None:
            parent = root / RECORDS_DIR
            records = parent / _home_dir(root, home).name
            for path in (parent, records):
                if not path.exists() and not path.is_symlink():
                    return
                if path.is_symlink() or not path.is_dir():
                    raise AccountDeletionError(f"{path.name} is not a plain directory")
            # Activities first (harness D2), once the directories are proven
            # plain: fail each one, supersede its runner and cancel its runs, so
            # nothing still running can act for the deleted account.
            from tinyassets.agent_activities import fence_all
            from tinyassets.runs import request_cancel

            store = records / "agent-activities.db"
            if store.is_file() and not store.is_symlink():
                for run_id in fence_all(root / records.name, outcome="failed:account_deleted"):
                    request_cancel(root, run_id)
            _rmtree(records)

        _phase("agent_session_records", _session_records)

    billing = "not_configured"
    if home:
        billing = _phase(
            "billing", lambda: (cancel_billing or cancel_stripe_billing)(home)
        ) or "error"
    identity = _phase(
        "identity", lambda: (delete_identity or delete_workos_user)(principal)
    ) or "error"
    # An outcome that is neither "done" nor "nothing to do" is UNFINISHED, even
    # though nothing raised: `unavailable` means we could not reach Stripe, so a
    # subscription may still be charging, and silence there is exactly the harm
    # (Codex round 3, finding 3). `not_configured` for identity means the WorkOS
    # user still exists and a host must remove it.
    if billing not in ("cancelled", "none", "not_configured") and "billing" not in failures:
        failures.append("billing")
    if identity not in ("deleted", "not_applicable") and "identity" not in failures:
        failures.append("identity")

    receipt: dict[str, Any] = {
        "principal_fingerprint": fingerprint,
        "home_id": home,
        "home_removed": home_removed,
        "home_staged_path": staged_path,
        "rows_deleted": dict(sorted(counts.items())),
        "billing": billing,
        "identity": identity,
        "unfinished_phases": sorted(failures),
        "retained": list(RETAINED),
    }
    if failures:
        receipt["host_receipt_path"] = _write_unfinished_receipt(root, receipt)
    logger.info(
        "account deleted %s: home=%s removed=%s rows=%s billing=%s identity=%s unfinished=%s",
        fingerprint, bool(home), home_removed, sum(counts.values()),
        billing, identity, sorted(failures),
    )
    return receipt


__all__ = [
    "BLOCKING_TABLES",
    "PRESERVED_TABLES",
    "PRINCIPAL_KEYS",
    "REDACTED_TABLES",
    "RETAINED",
    "AccountDeletionBlocked",
    "AccountDeletionError",
    "cancel_stripe_billing",
    "delete_account",
    "deletion_blockers",
    "deletion_plan",
    "delete_workos_user",
    "pending_deletions",
]
