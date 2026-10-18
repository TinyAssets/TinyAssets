"""Offline paired restore: exact before traffic, reset authority after disaster."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

from tinyassets.boxes.layout import OWNER_BOX_MIGRATION
from tinyassets.sqlite_readonly import readonly_db


class UnreadableFacts(ValueError):
    """A damaged store cannot prove its facts (distinct from an unsafe path)."""


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _safe(root, relative):
    path = root / relative
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError("restore authority is a symlink")
    if path.exists() and (not stat.S_ISREG(path.stat().st_mode) or path.stat().st_nlink != 1):
        raise ValueError("restore authority is not a private regular file")
    return path


def _read(root, relative):
    path = _safe(root, relative)
    for suffix in ("-wal", "-shm", "-journal"):
        _safe(root, str(relative) + suffix)
    if not path.exists():
        return {}
    with readonly_db(path) as db:
        from tinyassets.sqlite_integrity import check

        try:
            check(db, repair=True)
        except ValueError as error:
            raise UnreadableFacts("restore authority failed integrity check") from error
        result = {}
        for name, ddl in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"):
            info = db.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            columns = [row[1] for row in info]
            keys = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
            result[name] = (ddl, columns, keys,
                           [dict(zip(columns, row)) for row in db.execute(
                               f"SELECT * FROM {_quote(name)} NOT INDEXED")])
        return result


def normal_preflight(current, staged, owners, *, backup_time, restored_owners=None,
                     retained_owners=None):
    """Only platform traffic/layout evidence authorizes pre-traffic rollback."""
    from tinyassets.router_traffic import NAME

    try:
        marker = _read(current, NAME).get("traffic", (None, [], [], []))[3]
        if len(marker) != 1 or marker[0].get("singleton") != 1:
            raise ValueError("unreadable traffic marker")
        opened, credentials_after = marker[0]["traffic_opened_at"], marker[0]["credentials_after"]
        if type(credentials_after) not in (int, float):
            raise ValueError("unreadable credential generation")
    except (OSError, ValueError, sqlite3.DatabaseError, RuntimeError, KeyError):
        try:
            layout = json.loads(_safe(current, ".layout.json").read_text())
            unfinished = (layout.get("box_layout") != "owner-box"
                          and layout.get("state") == "migrating"
                          and layout.get("migration") == OWNER_BOX_MIGRATION
                          and (layout.get("split") == "owner-split"
                               or layout.get("box_layout") == "owner-split"))
        except (OSError, ValueError, AttributeError):
            unfinished = False
        if not unfinished:
            raise ValueError("live marker/layout cannot prove pre-traffic cutover; "
                             "use the disaster path") from None
        _carry_rollback(current, staged, owners, retained_owners)
        return
    if opened is not None or credentials_after:
        raise ValueError("normal restore refuses traffic_opened_at or credentials_after; "
                         "use the disaster path")
    _carry_rollback(current, staged, owners, retained_owners)


def _repair_staged_databases(staged):
    from tinyassets.box_layout_migration import _claim_sqlite_sidecars
    from tinyassets.sqlite_integrity import check

    for path in sorted(staged.rglob("*")):
        # A sidecar belongs to its database: closing a repaired database may
        # remove its -wal/-shm before the scan reaches them.
        if path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        # Owner content includes links. Discover SQLite without following any
        # symlink; only database candidates need private-inode custody.
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                raise ValueError("staged file changed during database discovery")
            if stream.read(16) != b"SQLite format 3\x00":
                continue
        _safe(staged, path.relative_to(staged))
        for suffix in ("-wal", "-shm", "-journal"):
            _safe(staged, str(path.relative_to(staged)) + suffix)
        _claim_sqlite_sidecars(path)
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as db, db:
            check(db, repair=True)


def _carry_rollback(current, staged, owners, retained_owners):
    from tinyassets.erasure_ledger import ErasureLedger, carry_forward, seed_legacy

    marker = _safe(staged, ".layout.json")
    legacy = not marker.exists() or json.loads(marker.read_text()).get("box_layout") != "owner-box"
    # Rollback publishes the extracted stage itself. A refusal discards that
    # stage; live data and owner trees are never repaired or read here.
    _repair_staged_databases(staged)
    if legacy:
        ErasureLedger(staged).initialize()
    ledger = carry_forward(current, staged)
    if legacy:
        seed_legacy(staged, ledger, store_root=owners)
        marker = _safe(current, ".layout.json")
        if marker.exists() and json.loads(marker.read_text()).get("box_layout") != "owner-box":
            seed_legacy(current, ledger, store_root=owners)
    else:
        _identify_erased(ledger, current, staged)
    _reserve_erasures(ledger, staged)
    _schedule_trees(ledger, [owners, retained_owners] if retained_owners else [owners])


def _rows(root, relative, table):
    return _read(root, relative).get(table, (None, [], [], []))[3]


def _identify_erased(ledger, current, staged, identity_exists=None, unavailable_sources=None):
    from tinyassets.erasure_ledger import principal_digest

    identities = _rows(staged, ".broker/state/owner-identities.db", "owner_identities")
    try:
        live = _rows(current, ".broker/state/owner-identities.db", "owner_identities")
    except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
        live = []
        if unavailable_sources is not None:
            unavailable_sources.append("live owner identities")
    backed_up = {row["principal"] for row in identities}
    for row in [*identities, *live]:
        owner = row["principal"]
        if (ledger.contains(owner) or (identity_exists is not None and (
                owner not in backed_up or not identity_exists(owner)))):
            ledger.execute("add", {"digest": principal_digest(owner), "principal": owner,
                                   "uids": [row["machine_id"]]})
    return identities, live


def erasures(current, staged, *, identity_exists, unavailable_sources=None,
             store_root=Path("/var/lib/boxhostd/owners")):
    """Carry only ledger authority; import pre-ledger facts during the cutover."""
    from tinyassets.erasure_ledger import carry_forward

    ledger = carry_forward(current, staged, unavailable_sources)
    _identify_erased(ledger, current, staged, identity_exists, unavailable_sources)
    entries = ledger.entries()
    erased = {entry["principal"] for entry in entries if entry["principal"]}
    _reserve_erasures(ledger, staged)
    identity = _safe(staged, ".broker/state/owner-identities.db")
    with closing(sqlite3.connect(identity)) as db, db:
        for owner in erased:
            db.execute("INSERT INTO owner_box_revocations VALUES (?,1) "
                       "ON CONFLICT(principal) DO UPDATE SET deleted=1", (owner,))
    if entries:
        custody = _safe(staged, ".broker/credential-custody.db")
        with closing(sqlite3.connect(custody)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS deleted_principals (digest TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO deleted_principals VALUES (?)",
                           [(entry["digest"],) for entry in entries])
        os.chown(custody, 1002, 1002)
        custody.chmod(0o600)
    return {uid for entry in entries for uid in entry["uids"]}


def _reserve_erasures(ledger, staged):
    """An erased permanent UID can never be reassigned to a surviving owner."""
    entries = [entry for entry in ledger.entries() if entry["uids"]]
    if not entries:
        return
    identity = _safe(staged, ".broker/state/owner-identities.db")
    with closing(sqlite3.connect(identity)) as db, db:
        for entry in entries:
            owner, uids = entry["principal"], entry["uids"]
            if not owner or len(uids) != 1:
                raise ValueError("ambiguous erased UID reservation")
            rows = db.execute("SELECT principal,machine_id FROM owner_identities "
                              "WHERE principal=? OR machine_id=?", (owner, uids[0])).fetchall()
            if any(row != (owner, uids[0]) for row in rows):
                raise ValueError("erased UID conflicts with permanent reservation")
            if not rows:
                db.execute("INSERT INTO owner_identities (principal,machine_id) VALUES (?,?)",
                           (owner, uids[0]))


def _schedule_trees(ledger, stores):
    for entry in ledger.entries():
        ledger.execute("restart", {"digest": entry["digest"]})
        for store in stores:
            for uid in entry["uids"]:
                destination = (Path(".tinyassets-erasure-quarantine." + secrets.token_hex(16))
                               / str(uid))
                ledger.execute("work", {"digest": entry["digest"], "target": {
                    "uid": uid, "store": str(store.relative_to(stores[0])),
                    "quarantine": str(destination)}})


# These are wholesale resets, never intersections with live authority.
RESET_TABLES = {
    ".api-keys.sqlite3": ("api_keys",),
    ".hook-owners.sqlite3": ("hook_owners",),
    ".auth.db": ("oauth_clients", "authorization_codes", "access_tokens", "refresh_tokens"),
    ".outside-client-authority.sqlite3": ("outside_families", "outside_grants", "outside_effects"),
    ".hosted-model-auth.db": ("hosted_model_flows", "connection_oauth_flows"),
    ".runtime/owner-sessions.db": (
        "owner_sessions", "owner_login_flows", "owner_login_completions",
        "owner_refresh_sessions", "owner_approval_handoffs", "canary_sign_in_attempts",
        "approval_handoffs", "inline_model_flows"),
    ".tinyassets.db": ("user_sessions",),
    ".broker/credential-custody.db": (
        "llm_credential_deposit_owners", "llm_credential_custody",
        "llm_credential_deposit_commits", "llm_credential_refresh_state"),
    ".broker/outbound.db": (
        "outbound_connections", "outbound_connection_grants", "connection_capabilities",
        "browser_vault"),
}


def reset_credentials(staged, now):
    for relative, tables in RESET_TABLES.items():
        present = _read(staged, relative)
        if not present:
            continue
        uri = (staged / relative).as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True)) as db, db:
            for table in tables:
                if table in present:
                    db.execute(f"DELETE FROM {_quote(table)}")
    identity = staged / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(identity)) as db, db:
        if "owner_box_secrets" in _read(staged, str(identity.relative_to(staged))):
            for owner, in db.execute("SELECT principal FROM owner_box_secrets").fetchall():
                db.execute("UPDATE owner_box_secrets SET secret=? WHERE principal=?",
                           (secrets.token_urlsafe(32), owner))
    for relative in (".connect/session_secret", ".connect/client_secret",
                     ".runtime/restore-session-seal.key"):
        path = _safe(staged, relative)
        missing_parent = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if missing_parent:
            os.chown(path.parent, 1001, 1001)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
        os.chown(path, 1001, 1001)
    for pattern in (".runtime/app_refresh_sessions/*.json",
                    ".platform/credential-vaults/*/.credential-vault.json"):
        for path in staged.glob(pattern):
            _safe(staged, path.relative_to(staged)).unlink()
    for path in staged.glob(".broker/accounts/*/policy.db"):
        _read(staged, path.relative_to(staged))
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DELETE FROM once_approvals")
    from tinyassets.auth.wiki_canary import WIKI_CANARY_TOKEN_ENV
    from tinyassets.onboarding.canary_sign_in import KEY_ENV

    operational = _safe(staged, ".router-credentials.json")
    operational.unlink(missing_ok=True)
    operational.write_text(json.dumps({name: secrets.token_urlsafe(48) for name in (
        WIKI_CANARY_TOKEN_ENV, KEY_ENV)}))
    operational.chmod(0o600)
    os.chown(operational, 1001, 1001)
    from tinyassets.router_traffic import NAME, initialize

    _read(staged, NAME)
    initialize(staged)
    with closing(sqlite3.connect(staged / NAME)) as db, db:
        db.execute("UPDATE traffic SET traffic_opened_at=NULL, credentials_after=?", (now,))
    os.chown(staged / NAME, 0, 1001)
    (staged / NAME).chmod(0o660)


def fence_ops(staged, trees, now):
    from tinyassets.broker.ops import MAX_FUTURE_SKEW_S

    cutoff = int(now * 1000) + MAX_FUTURE_SKEW_S * 1000 + 1
    _read(staged, ".broker/ops.db")
    path = _safe(staged, ".broker/ops.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER)")
        db.execute("INSERT INTO meta VALUES ('cutoff_ms',?) ON CONFLICT(key) "
                   "DO UPDATE SET value=MAX(value,excluded.value)", (cutoff,))
    os.chown(path, 1002, 1002)
    # The entire owner mount, including boxd's journal, is owner-writable.
    # A fresh platform generation fences every owner without opening their files.
    fence = _safe(staged, ".restore-epoch")
    fence.write_text(secrets.token_hex(32))
    os.chown(fence, 0, 0)
    fence.chmod(0o444)


def disaster_restore(current, staged, trees, *, backup_time, now=None,
                     identity_exists=None, billing_sync=None, revoke_sessions=None,
                     live_trees, retained_trees=None):
    from tinyassets.billing.stripe_adapter import restore_billing
    from tinyassets.router_account_deletion import revoke_workos_sessions, workos_user_exists

    now = time.time() if now is None else now
    # Publish the repaired stage, not the disposable copies used by _read.
    # Both restore modes use the same exhaustive repair/reverification policy.
    _repair_staged_databases(staged)
    unavailable_sources = []
    erased = erasures(current, staged, identity_exists=identity_exists or workos_user_exists,
                      unavailable_sources=unavailable_sources, store_root=live_trees)
    reconciled = 0
    for row in _rows(staged, ".broker/state/owner-identities.db", "owner_identities"):
        if row["machine_id"] not in erased:
            (revoke_sessions or revoke_workos_sessions)(row["principal"])
            reconciled += 1
    reset_credentials(staged, now)
    fence_ops(staged, trees, now)
    _read(staged, ".broker/plans.db")
    billing = (billing_sync or restore_billing)(staged, now)
    # One broker-wide spend fence covers all owners, including absent policies.
    fence = _safe(staged, ".broker/restore-spend-until")
    fence.write_text(str((int(now) // 86400 + 1) * 86400))
    os.chown(fence, 1002, 1002)
    report_path = _safe(staged, ".disaster-restore.json")
    # Paths describe the published pair, never staging. The shell reserves the
    # retained destination before preparation and creates it only during swap.
    stores = [live_trees]
    if retained_trees is not None:
        stores.append(retained_trees)
    from tinyassets.erasure_ledger import ErasureLedger

    _schedule_trees(ErasureLedger(staged), stores)
    report = {"backup_time": backup_time, "restore_time": now,
              "credential_roots_reset": True, "operation_fences_reset": True,
              "erased_owners": len(erased), "erased_uids": sorted(erased), "billing": billing,
              "unavailable_erasure_sources": unavailable_sources,
              "owners_reconciled": reconciled,
              "spend_blocked_until": int(fence.read_text()),
              "reverted_classes": ["owner consents and grants", "effector consents",
                  "capability grants", "graph receivers", "outside grants",
                  "spend reservations", "policies", "content", "turns", "schedules",
                  "receipts", "abuse suspension history"]}
    report_path.write_text(json.dumps(report, sort_keys=True))
    os.chown(report_path, 0, 0)
    report_path.chmod(0o600)
    print("WARN disaster restore: " + json.dumps(report, sort_keys=True))
    return report


def finish_erasures(root, store_root=Path("/var/lib/boxhostd/owners"), *, daemons=None):
    """Under layout exclusion, retry fenced owners through full destruction."""
    from tinyassets.boxes.destroy import quarantine, tree
    from tinyassets.boxes.provider import BoxError
    from tinyassets.erasure_ledger import ErasureLedger, validate_target

    ledger = ErasureLedger(root)
    failures = []
    for entry in ledger.entries():
        completed = []
        for key, item in entry["work"].items():
            uid = item["uid"]
            store = store_root / item["store"]
            destination = store_root / item["quarantine"]
            # Root-private ledger recorded both paths before rename. Neither
            # report replacement nor a later restore can lose the destination.
            try:
                validate_target(item)
                for path in (store, destination):
                    while path != store_root.parent:
                        if path.is_symlink():
                            raise BoxError("erasure target is a symlink")
                        path = path.parent
                if destination.exists():
                    tree(destination.parent, uid, erase=False)
                    tree(destination.parent, uid, erase=True)
                else:
                    try:
                        tree(store, uid, erase=False)
                        tree(store, uid, erase=True)
                    except (OSError, BoxError):
                        if not store.exists():
                            raise
                        quarantine(store, uid, destination_root=destination.parent)
                        raise BoxError("erasure quarantined; retry required")
            except (OSError, BoxError, ValueError) as exc:
                failures.append({"uid": uid, "quarantine": item["quarantine"],
                                 "error": type(exc).__name__})
                ledger.execute("save", {"digest": entry["digest"], "receipt": {
                    "erasure_failures": failures[-1:]}})
                continue
            completed.append(key)
        if daemons is not None and "owner_tree" in entry["tasks"].values():
            uids = set(entry["uids"]) | {item["uid"] for item in entry["work"].values()}
            # Online deletion may not have reached its permanent reservation;
            # the router owns that retry. Do not append a spurious failure.
            if entry["principal"] is None or not uids:
                continue
            try:
                if len(completed) != len(entry["work"]):
                    raise BoxError("full owner destruction remains pending")
                for uid in sorted(uids):
                    daemons.destroy(entry["principal"], uid)
            except Exception as exc:
                failures.append({"digest": entry["digest"], "error": type(exc).__name__})
                ledger.execute("save", {"digest": entry["digest"], "receipt": {
                    "erasure_failures": failures[-1:]}})
            else:
                # Tree work remains durable across ANY failed destruction step,
                # even when all bytes have already gone on an earlier attempt.
                ledger.execute("finish", {"digest": entry["digest"], "ids": [
                    key for key, phase in entry["tasks"].items() if phase == "owner_tree"]})
    if failures:
        print("WARN erasure cleanup: " + json.dumps(failures, sort_keys=True))
    return not any(e["work"] or (e["uids"] and "owner_tree" in e["tasks"].values())
                   for e in ledger.entries())
