"""Unscheduled, data-only application of a real stored presentation-update grant.

Not a request API: callers cannot supply consent, source content or an actor to
impersonate. Stored grants authorize only the checked UI mutation below. Root
integration must join the admitted service/writer lifecycle before calling it.
Only main is written during the attached-store transaction; accounting settles
afterwards with a durable outbox. No multi-file atomic-write claim is made.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from tinyassets import command_center_release_series as series
from tinyassets import command_center_update_policy as policy
from tinyassets import command_center_update_registry as registry
from tinyassets import storage_accounting as accounting
from tinyassets.api.command_center_updates import _prepare
from tinyassets.command_center_update_reads import HeldReads
from tinyassets.command_center_updates import digest
from tinyassets.custom_agents import (
    _agent_connect,
    _canonical_json,
    _check_app_ui_fields,
    _check_assets_held,
)

_log = logging.getLogger(__name__)

_RECEIPTS = """CREATE TABLE IF NOT EXISTS command_center_auto_receipts (
    operation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, universe_id TEXT NOT NULL,
    adoption_id TEXT NOT NULL, grant_request_id TEXT NOT NULL, result_json TEXT NOT NULL,
    reservation_id INTEGER, reserved_bytes INTEGER NOT NULL,
    settlement TEXT NOT NULL, settlement_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL)"""


_STATUS = """CREATE TABLE IF NOT EXISTS command_center_auto_status (
    owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, adoption_id TEXT NOT NULL,
    operation_id TEXT NOT NULL, status_json TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(owner_id,universe_id,adoption_id))"""


def _schema(conn):
    policy._schema(conn)
    conn.execute(_RECEIPTS)
    conn.execute(_STATUS)


def _safe_failure(exc):
    # Details remain in host logs. Owner responses never expose filesystem paths,
    # raw SQL or private payloads carried by an underlying exception.
    if isinstance(exc, PermissionError):
        reason = "Recipient access or ownership changed."
        code = "authority_changed"
    elif isinstance(exc, (ValueError, LookupError)):
        if "storage" in str(exc).lower() or "quota" in str(exc).lower():
            reason, code = "Storage admission changed or needs attention.", "storage_changed"
        elif "nested" in str(exc).lower():
            reason, code = (
                "Nested workflow dependencies require a decision.",
                "unsupported_dependency",
            )
        else:
            reason = "The source, policy or recipient content changed; review the update."
            code = "update_requires_decision"
    else:
        reason, code = "The update could not finish; it will be retried.", "temporarily_unavailable"
    return {
        "status": "requires_decision"
        if isinstance(exc, (ValueError, LookupError, PermissionError))
        else "retryable_error",
        "applied": False,
        "reason": reason,
        "reason_code": code,
    }


def _status(conn, owner, uid, adoption_id, result):
    conn.execute(
        "INSERT INTO command_center_auto_status VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(owner_id,universe_id,adoption_id) DO UPDATE SET "
        "operation_id=excluded.operation_id,status_json=excluded.status_json,updated_at=excluded.updated_at",
        (
            owner,
            uid,
            adoption_id,
            result.get("operation_id", ""),
            _canonical_json(result),
            time.time(),
        ),
    )


def _store_status(base, owner, uid, adoption_id, result, *, expected_revision=None):
    with _agent_connect(base) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        grant = policy._policy(conn, owner, uid, adoption_id)
        if grant is not None and (
            expected_revision is None or grant["revision"] == expected_revision
        ):
            _status(conn, owner, uid, adoption_id, result)


def inspect_status(*, universe_id, adoption_id):
    """Owner-facing read; route registration remains the integration owner's job."""
    from tinyassets.api.command_center_updates import _scope

    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
        _schema(conn)
        row = conn.execute(
            "SELECT status_json FROM command_center_auto_status "
            "WHERE owner_id=? AND universe_id=? AND adoption_id=?",
            (owner, uid, adoption_id),
        ).fetchone()
        result = json.loads(row[0]) if row else {"status": "not_checked", "applied": False}
        debt = conn.execute(
            "SELECT COUNT(*) FROM command_center_auto_receipts "
            "WHERE owner_id=? AND universe_id=? AND adoption_id=? AND settlement='pending'",
            (owner, uid, adoption_id),
        ).fetchone()[0]
        return {**result, "pending_settlements": debt} if debt else result


def _safe_path(base, relative):
    path = base / relative
    if path.resolve() != path.absolute() or not path.is_relative_to(base):
        raise ValueError("update store path is not a trusted regular path")
    return path


@contextmanager
def _connect(base, owner):
    """Hold all actual source/dependency/accounting writers; never initialize satellites."""
    base = Path(base).absolute()
    if base.resolve() != base:
        raise ValueError("update data root must not traverse links")
    # Existing quota resolvers have an initialization fast path. Establish it
    # before reservations; under the final fence they may only read.
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base)
    with _agent_connect(base) as conn:
        for statement in registry._SCHEMA:
            conn.execute(statement)
        _schema(conn)
        home = conn.execute(
            "SELECT universe_id FROM founder_home WHERE founder_sub=?", (owner,)
        ).fetchone()
        home = str(home[0]) if home else ""
        paths = {
            "retained_automations": ".automations.db",
            "source_versions": ".runs.db",
            "source_pins": ".command-center-packages/packages.db",
            "storage_ledger": accounting.DB_FILENAME,
        }
        if home:
            if Path(home).name != home or home.startswith("."):
                raise ValueError("invalid founder home")
            paths["account_tier"] = home + "/.subscription_state.db"
        for alias, relative in paths.items():
            path = _safe_path(base, relative)
            if path.is_file():
                conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(path),))
        conn.execute("BEGIN IMMEDIATE")
        # Home resolved before attachment must be identical under reservation.
        actual = conn.execute(
            "SELECT universe_id FROM founder_home WHERE founder_sub=?", (owner,)
        ).fetchone()
        if (str(actual[0]) if actual else "") != home:
            raise ValueError("founder home changed during preparation")
        yield conn, HeldReads(conn, base)


def _authority(conn, base, owner, uid):
    from tinyassets.principals import named_principal

    if named_principal(owner) != owner or not accounting.is_account(base, owner):
        raise PermissionError("recipient owner unavailable")
    registry.require_owner(conn, owner=owner, uid=uid)
    if not uid or Path(uid).name != uid or uid.startswith("."):
        raise PermissionError("recipient center unavailable")
    if not _safe_path(Path(base).absolute(), uid).is_dir():
        raise PermissionError("recipient center unavailable")
    row = conn.execute("SELECT owner_id FROM universe_owner WHERE universe_id=?", (uid,)).fetchone()
    if row is None or row[0] != owner:
        raise PermissionError("recipient ownership changed")
    if conn.execute(
        "SELECT 1 FROM deleted_principals WHERE founder_sub=?",
        (hashlib.sha256(owner.strip().encode()).hexdigest(),),
    ).fetchone():
        raise PermissionError("recipient owner unavailable")


def _quota_inputs(conn, owner):
    """Fence resolver inputs without implementing another tier/quota resolver."""
    home = conn.execute("SELECT * FROM founder_home WHERE founder_sub=?", (owner,)).fetchone()
    if home is None:
        raise PermissionError("recipient founder home unavailable")
    databases = {r[1]: r[2] for r in conn.execute("PRAGMA database_list")}
    tier_path = Path(databases["main"]).parent / home["universe_id"] / ".subscription_state.db"
    if ("account_tier" in databases) != tier_path.is_file():
        raise ValueError("account tier store changed during preparation")
    tier = None
    if "account_tier" in databases:
        tier = [
            tuple(r)
            for r in conn.execute("SELECT * FROM account_tier.subscription_meta ORDER BY key")
        ]
    owned = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM universe_owner WHERE owner_id=? ORDER BY universe_id", (owner,)
        )
    ]
    return {"home": dict(home), "owned": owned, "tier": tier}


def _candidate(conn, reads, owner, uid, adoption_id):
    _authority(conn, reads.base, owner, uid)
    grant = policy._policy(conn, owner, uid, adoption_id)
    if grant is None or not grant["enabled"]:
        return {"status": "disabled", "applied": False}
    snapshot = json.loads(grant["snapshot_json"])
    # Accepted snapshot remains intact; automatic progress is not new consent.
    accepted = {k: v for k, v in snapshot.items() if k != "progress"}
    if (
        digest(accepted) != grant["last_digest"]
        or not grant["last_request_id"]
        or accepted.get("policy") != policy.POLICY
        or accepted.get("enabled") is not True
        or (accepted.get("owner_id"), accepted.get("universe_id"), accepted.get("adoption_id"))
        != (owner, uid, adoption_id)
    ):
        raise ValueError("stored policy grant integrity mismatch")
    link = snapshot.get("progress", snapshot["link"])
    if (link["series_id"], link["author_id"], link["ui_id"]) != (
        accepted["link"]["series_id"],
        accepted["link"]["author_id"],
        accepted["link"]["ui_id"],
    ):
        raise ValueError("policy progress is outside the accepted grant")
    current = policy._link_snapshot(
        conn,
        reads.base,
        owner,
        uid,
        adoption_id,
        link["series_id"],
        link["release_id"],
        readers=reads,
    )
    if current != link:
        raise ValueError("installed baseline, adoption or dependencies changed")
    chain = series._chain(conn, link["series_id"])
    index = next(i for i, r in enumerate(chain) if r["release_id"] == link["release_id"])
    if index == len(chain) - 1:
        return {"status": "up_to_date", "applied": False}
    target = chain[index + 1]
    installed, document, replacement, readset = _prepare(
        conn,
        reads.base,
        owner,
        uid,
        adoption_id,
        target["definition_id"],
        readers=reads,
    )
    before, after = (
        reads.definition(link["definition_id"]),
        reads.definition(target["definition_id"]),
    )
    if digest(series._normalized(after)) != target["definition_digest"]:
        raise ValueError("target release definition digest changed")
    decisions = policy.presentation_decisions(before, after)
    if decisions:
        raise ValueError("; ".join(decisions))
    library = [
        replacement if ui["ui_id"] == installed["ui_id"] else ui for ui in document["ui_library"]
    ]
    library_json = _canonical_json(library)
    return {
        "status": "ready",
        "grant": grant,
        "snapshot": snapshot,
        "link": link,
        "target": target,
        "installed": installed,
        "document": document,
        "replacement": replacement,
        "readset": readset,
        "library_json": library_json,
        "library": library,
        "changed": readset["current_hash"] != readset["replacement_hash"],
        "quota_inputs": _quota_inputs(conn, owner),
        "operation_id": digest(
            [
                owner,
                uid,
                adoption_id,
                grant["last_request_id"],
                link["release_id"],
                target["release_id"],
            ]
        ),
    }


def _validate_reservation(conn, reservation, owner, nbytes, quota_context):
    quota, tier, pairs = quota_context
    row = conn.execute(
        "SELECT * FROM storage_ledger.pending WHERE id=?", (reservation.id,)
    ).fetchone()
    if (
        row is None
        or row["state"] != "reserved"
        or row["account_id"] != owner
        or row["scope_id"] != owner
        or row["store"] != "ui_library"
        or row["bytes"] != nbytes
        or reservation.account_id != owner
        or time.time() - row["created_at"] >= accounting.RESERVED_TTL_S - 5
    ):
        raise ValueError("storage reservation is missing, expired or mismatched")
    # The resolver was used outside locks. Its exact input rows are rechecked
    # before this call. Count existing reservations exactly once, including ours.
    measured, missing = 0, []
    for scope, store in pairs:
        value = conn.execute(
            "SELECT bytes FROM storage_ledger.measurements WHERE scope_id=? AND store=?",
            (scope, store),
        ).fetchone()
        if value is None:
            missing.append((scope, store))
        else:
            measured += value[0]
    pending = conn.execute(
        "SELECT COALESCE(SUM(bytes),0) FROM storage_ledger.pending WHERE account_id=?", (owner,)
    ).fetchone()[0]
    if missing:
        raise ValueError("storage measurement incomplete; retry after measurement")
    if measured + pending > quota:
        raise ValueError(f"storage quota changed or filled ({tier}); retry after measurement")


def _receipt(conn, operation_id):
    row = conn.execute(
        "SELECT * FROM command_center_auto_receipts WHERE operation_id=?", (operation_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        **json.loads(row["result_json"]),
        "settlement": row["settlement"],
        "settlement_error": row["settlement_error"],
    }


def _write(conn, reads, owner, uid, adoption_id, candidate, reservation):
    grant, target, installed = candidate["grant"], candidate["target"], candidate["installed"]
    _check_app_ui_fields({"ui_library": candidate["library"]})
    _check_assets_held(conn, owner, candidate["library"])
    if candidate["changed"]:
        count = conn.execute(
            "UPDATE universe_app_ui SET ui_library_json=?,revision=revision+1,updated_at=? "
            "WHERE owner_user_id=? AND universe_id=? AND revision=?",
            (candidate["library_json"], time.time(), owner, uid, candidate["document"]["revision"]),
        ).rowcount
        if count != 1:
            raise ValueError("recipient UI revision changed")
    count = conn.execute(
        "UPDATE command_center_adoptions SET ui_definition_id=?,baseline_hash=?,"
        "revision=revision+1 "
        "WHERE owner_id=? AND universe_id=? AND adoption_id=? AND revision=?",
        (
            target["definition_id"],
            candidate["readset"]["replacement_hash"],
            owner,
            uid,
            adoption_id,
            installed["revision"],
        ),
    ).rowcount
    if count != 1:
        raise ValueError("recipient adoption changed")
    snapshot = candidate["snapshot"]
    snapshot["progress"] = policy._link_snapshot(
        conn,
        reads.base,
        owner,
        uid,
        adoption_id,
        target["series_id"],
        target["release_id"],
        readers=reads,
    )
    count = conn.execute(
        "UPDATE command_center_update_policies SET snapshot_json=?,revision=revision+1 "
        "WHERE owner_id=? AND universe_id=? AND adoption_id=? AND enabled=1 AND revision=? "
        "AND last_request_id=? AND last_digest=?",
        (
            _canonical_json(snapshot),
            owner,
            uid,
            adoption_id,
            grant["revision"],
            grant["last_request_id"],
            grant["last_digest"],
        ),
    ).rowcount
    if count != 1:
        raise ValueError("recipient policy changed")
    result = {
        "operation_id": candidate["operation_id"],
        "applied": candidate["changed"],
        "status": "applied" if candidate["changed"] else "advanced_without_content_change",
        "actor_kind": "stored_presentation_policy",
        "grant_request_id": grant["last_request_id"],
        "grant_digest": grant["last_digest"],
        "from_release_id": candidate["link"]["release_id"],
        "to_release_id": target["release_id"],
        "definition_id": target["definition_id"],
        "source_digest": target["definition_digest"],
        "policy_revision": grant["revision"] + 1,
        "adoption_revision": installed["revision"] + 1,
        "ui_revision": candidate["document"]["revision"] + int(candidate["changed"]),
        "baseline_hash": candidate["readset"]["replacement_hash"],
    }
    conn.execute(
        "INSERT INTO command_center_auto_receipts "
        "(operation_id,owner_id,universe_id,adoption_id,grant_request_id,result_json,reservation_id,"
        "reserved_bytes,settlement,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            candidate["operation_id"],
            owner,
            uid,
            adoption_id,
            grant["last_request_id"],
            _canonical_json(result),
            reservation.id if reservation else None,
            reservation.bytes if reservation else 0,
            "pending" if reservation else "not_needed",
            time.time(),
        ),
    )
    _status(
        conn,
        owner,
        uid,
        adoption_id,
        {**result, "settlement": "pending" if reservation else "not_needed"},
    )
    return result


def apply_next(base, *, owner_id, universe_id, adoption_id):
    """Internal bounded step. A protected stored grant, never arguments, authorizes it."""
    base, owner, uid = Path(base), owner_id, universe_id
    reservation, applied, written, expected_revision = None, False, None, None
    try:
        with _connect(base, owner) as (conn, reads):
            grant = policy._policy(conn, owner, uid, adoption_id)
            expected_revision = grant["revision"] if grant else None
            candidate = _candidate(conn, reads, owner, uid, adoption_id)
            if candidate["status"] != "ready" and grant:
                _status(conn, owner, uid, adoption_id, candidate)
        if candidate["status"] != "ready":
            return candidate
        if candidate["changed"]:
            # Existing authoritative resolvers only, outside all held stores.
            quota, tier = accounting._quota(base, owner)
            pairs = accounting._scopes(base, owner)
            quota_context = (quota, tier, pairs)
            with _connect(base, owner) as (conn, _reads):
                if _quota_inputs(conn, owner) != candidate["quota_inputs"]:
                    raise ValueError("quota resolver inputs changed during preparation")
            reservation = accounting.reserve(
                base,
                account_id=owner,
                scope_id=owner,
                store="ui_library",
                nbytes=len(candidate["library_json"].encode()),
            )
        with _connect(base, owner) as (conn, reads):
            old = _receipt(conn, candidate["operation_id"])
            if old is None:
                fresh = _candidate(conn, reads, owner, uid, adoption_id)
                if (
                    fresh["status"] != "ready"
                    or fresh["operation_id"] != candidate["operation_id"]
                    or fresh["grant"]["revision"] != candidate["grant"]["revision"]
                    or fresh["readset"] != candidate["readset"]
                    or fresh["quota_inputs"] != candidate["quota_inputs"]
                ):
                    raise ValueError(
                        "policy, recipient or account state changed during preparation"
                    )
                if reservation:
                    # Reuse the authoritative resolvers while every input store
                    # is reserved. Initialized independent READ connections see
                    # the same protected home/tier/ownership rows, closing ABA.
                    held_quota, held_tier = accounting._quota(base, owner)
                    held_pairs = accounting._scopes(base, owner)
                    held_context = (held_quota, held_tier, held_pairs)
                    if (
                        held_context != quota_context
                        or _quota_inputs(conn, owner) != fresh["quota_inputs"]
                    ):
                        raise ValueError("quota resolver inputs or result changed")
                    _validate_reservation(
                        conn, reservation, owner, len(fresh["library_json"].encode()), held_context
                    )
                written = _write(conn, reads, owner, uid, adoption_id, fresh, reservation)
                # Even a slow validation/write must not commit after its hold expires.
                if reservation:
                    _validate_reservation(
                        conn, reservation, owner, len(fresh["library_json"].encode()), quota_context
                    )
        applied = written is not None
        if not applied and reservation:
            accounting.release(reservation)
        result = settle_receipt(base, operation_id=candidate["operation_id"])
        return {**result, "already_applied": old is not None}
    except (
        ValueError,
        LookupError,
        PermissionError,
        sqlite3.Error,
        accounting.StorageRefused,
    ) as exc:
        _log.warning("automatic presentation update could not finish", exc_info=True)
        if applied:
            return {
                **written,
                "settlement": "pending",
                "settlement_error": "Storage settlement will be retried.",
            }
        if reservation is not None and not applied:
            accounting.release(reservation)
        result = _safe_failure(exc)
        try:
            _store_status(
                base, owner, uid, adoption_id, result, expected_revision=expected_revision
            )
        except Exception:
            _log.warning("automatic presentation status unavailable", exc_info=True)
            result["status_persisted"] = False
        return result
    except BaseException:
        if reservation is not None and not applied:
            accounting.release(reservation)
        raise


def settle_receipt(base, *, operation_id):
    """Resolve post-commit accounting debt without ever replaying the UI mutation."""
    with _agent_connect(base) as conn:
        row = conn.execute(
            "SELECT * FROM command_center_auto_receipts WHERE operation_id=?", (operation_id,)
        ).fetchone()
    if row is None:
        raise LookupError("automatic update receipt not found")
    row = dict(row)
    result = json.loads(row["result_json"])
    if row["settlement"] != "pending":
        return {
            **result,
            "settlement": row["settlement"],
            "settlement_error": row["settlement_error"],
        }
    error = ""
    try:
        with _connect(base, row["owner_id"]) as (conn, _reads):
            _authority(conn, base, row["owner_id"], row["universe_id"])
            pending = conn.execute(
                "SELECT * FROM storage_ledger.pending WHERE id=?", (row["reservation_id"],)
            ).fetchone()
            if pending and (
                pending["account_id"],
                pending["scope_id"],
                pending["store"],
                pending["bytes"],
            ) != (row["owner_id"], row["owner_id"], "ui_library", row["reserved_bytes"]):
                raise ValueError("accounting receipt reservation mismatch")
            state = pending["state"] if pending else "missing"
        if state == "reserved":
            accounting.commit(
                accounting.Reservation(
                    Path(base), row["reservation_id"], row["owner_id"], row["reserved_bytes"]
                )
            )
        # commit() can affect zero rows; verify instead of reporting fake settlement.
        with _connect(base, row["owner_id"]) as (conn, _reads):
            _authority(conn, base, row["owner_id"], row["universe_id"])
            pending = conn.execute(
                "SELECT state FROM storage_ledger.pending WHERE id=?", (row["reservation_id"],)
            ).fetchone()
            covered = bool(pending and pending[0] == "committed")
        if not covered:
            accounting.measure(base, row["owner_id"], "ui_library")
        settlement = "settled" if covered else "measured"
    except Exception:
        _log.warning("automatic presentation storage settlement failed", exc_info=True)
        settlement, error = "pending", "Storage settlement will be retried."
    with _agent_connect(base) as conn:
        conn.execute(
            "UPDATE command_center_auto_receipts SET settlement=?,settlement_error=? "
            "WHERE operation_id=? AND settlement='pending'",
            (settlement, error, operation_id),
        )
        # A competing settler may have completed while this attempt was outside
        # main. Read its canonical outcome under the same write reservation;
        # never publish a stale local failure after the receipt CAS lost.
        canonical = _receipt(conn, operation_id)
        if canonical is None:
            raise LookupError("automatic update receipt not found")
        latest = conn.execute(
            "SELECT operation_id FROM command_center_auto_status "
            "WHERE owner_id=? AND universe_id=? AND adoption_id=?",
            (row["owner_id"], row["universe_id"], row["adoption_id"]),
        ).fetchone()
        if latest and latest[0] == operation_id:
            _status(
                conn,
                row["owner_id"],
                row["universe_id"],
                row["adoption_id"],
                canonical,
            )
    return canonical


def settle_pending(base, *, after="", limit=10):
    """Bounded durable debt sweep, independent of policy enablement or new releases."""
    if not isinstance(after, str) or not 1 <= limit <= 100:
        raise ValueError("invalid settlement cursor")
    with _agent_connect(base) as conn:
        _schema(conn)
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT operation_id FROM command_center_auto_receipts "
                "WHERE settlement='pending' AND operation_id>? ORDER BY operation_id LIMIT ?",
                (after, limit),
            )
        ]
    results = []
    for operation_id in ids:
        try:
            results.append(settle_receipt(base, operation_id=operation_id))
        except Exception as exc:
            _log.warning("automatic update receipt sweep failed", exc_info=True)
            results.append(
                {
                    "operation_id": operation_id,
                    "settlement": "pending",
                    "settlement_error": _safe_failure(exc)["reason"],
                }
            )
    return {"after": ids[-1] if ids else "", "results": results}


def process_policies(base, *, after=(), limit=10):
    """Unscheduled bounded cursor; one step per stored policy, no actor context."""
    if not 1 <= limit <= 100 or len(after) not in {0, 3}:
        raise ValueError("invalid bounded policy cursor")
    with _agent_connect(base) as conn:
        _schema(conn)
        keys = conn.execute(
            "SELECT owner_id,universe_id,adoption_id,revision FROM command_center_update_policies "
            "WHERE enabled=1 AND (owner_id,universe_id,adoption_id)>(?,?,?) "
            "ORDER BY owner_id,universe_id,adoption_id LIMIT ?",
            (*(after or ("", "", "")), limit),
        ).fetchall()
    results = []
    for owner, uid, adoption_id, revision in keys:
        try:
            results.append(
                apply_next(base, owner_id=owner, universe_id=uid, adoption_id=adoption_id)
            )
        except Exception as exc:
            _log.warning("automatic presentation policy sweep failed", exc_info=True)
            result = _safe_failure(exc)
            try:
                _store_status(base, owner, uid, adoption_id, result, expected_revision=revision)
            except Exception:
                _log.warning("automatic presentation status unavailable", exc_info=True)
            results.append(result)
    return {"after": tuple(keys[-1])[:3] if keys else (), "results": results}
