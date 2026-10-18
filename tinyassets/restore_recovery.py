"""Offline paired restore: exact before traffic, reset authority after disaster."""
from __future__ import annotations

import hashlib
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
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise UnreadableFacts("restore authority failed integrity check")
        result = {}
        for name, ddl in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"):
            info = db.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            columns = [row[1] for row in info]
            keys = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
            result[name] = (ddl, columns, keys,
                           [dict(zip(columns, row)) for row in db.execute(
                               f"SELECT * FROM {_quote(name)} NOT INDEXED")])
        return result


def normal_preflight(current, staged, owners, *, backup_time, restored_owners=None):
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
        return
    if opened is not None or credentials_after:
        raise ValueError("normal restore refuses traffic_opened_at or credentials_after; "
                         "use the disaster path")


def _rows(root, relative, table):
    return _read(root, relative).get(table, (None, [], [], []))[3]


def erasures(current, staged, *, identity_exists, unavailable_sources=None):
    """The sole carried fact: union readable tombstones, then ask the identity seam."""
    sources = (
        (".router-deleted-owners.sqlite3", "deleted_owners"),
        (".broker/state/owner-identities.db", "owner_box_revocations"),
        (".broker/credential-custody.db", "deleted_principals"),
        (".boxhost/boxhost.db", "owner_destroys"),
    )
    unavailable_sources = [] if unavailable_sources is None else unavailable_sources
    erased, digests, machines = set(), set(), set()
    identities = _rows(staged, ".broker/state/owner-identities.db", "owner_identities")
    try:
        live_identities = _rows(current, ".broker/state/owner-identities.db", "owner_identities")
    except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
        live_identities = []
        unavailable_sources.append("live owner identities")
    # A post-backup identity has no restorable box. Choose erasure, never copy
    # newer owner content or resurrect an account with somebody else's UID.
    backed_up = {row["principal"] for row in identities}
    erased.update(row["principal"] for row in live_identities if row["principal"] not in backed_up)
    for root in (current, staged):
        for relative, table in sources:
            try:
                rows = _rows(root, relative, table)
            except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
                if root == staged:
                    raise
                unavailable_sources.append("live " + relative)
                continue
            for row in rows:
                if table == "deleted_principals":
                    digests.add(row["digest"])
                elif table == "owner_destroys":
                    erased.add(row["owner_id"])
                    machines.add(row["uid"])
                elif table == "deleted_owners":
                    erased.add(row["owner"])
                elif row["deleted"]:
                    erased.add(row["principal"])
    checked = set()
    for row in [*identities, *live_identities]:
        owner = row["principal"]
        if owner in erased or owner in checked:
            continue
        if (hashlib.sha256(owner.encode()).hexdigest() in digests
                or row["machine_id"] in machines or not identity_exists(owner)):
            erased.add(owner)
        checked.add(owner)
    from tinyassets.router_credentials import DeletedOwners

    deleted = DeletedOwners(staged)
    for owner in sorted(erased):
        receipt = {"unfinished_phases": [
            "owner_tree", "broker_egress", "billing", "credentials", "identity"],
            "completed_phases": [], "home_removed": False, "restore_pending": True,
            "source": "disaster erasure reconciliation"}
        deleted.add(owner, receipt)
        deleted.save_receipt(owner, receipt)
    if erased:
        tombstones = staged / ".router-deleted-owners.sqlite3"
        os.chown(tombstones, 1001, 1001)
        tombstones.chmod(0o600)
    identity = _safe(staged, ".broker/state/owner-identities.db")
    with closing(sqlite3.connect(identity)) as db, db:
        # Carry the erased identity's permanent UID with its tombstone. A
        # quarantined tree/project must never be assigned to a different owner.
        for row in live_identities:
            if row["principal"] in erased and row["principal"] not in backed_up:
                db.execute("INSERT INTO owner_identities (principal,machine_id) VALUES (?,?)",
                           (row["principal"], row["machine_id"]))
        for owner in erased:
            db.execute("INSERT INTO owner_box_revocations VALUES (?,1) "
                       "ON CONFLICT(principal) DO UPDATE SET deleted=1", (owner,))
    # Preserve even digests whose identity row is absent from this older pair:
    # a later identity reconciliation must not resurrect their custody.
    digests.update(hashlib.sha256(owner.encode()).hexdigest() for owner in erased)
    if digests:
        custody = _safe(staged, ".broker/credential-custody.db")
        with closing(sqlite3.connect(custody)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS deleted_principals (digest TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO deleted_principals VALUES (?)",
                           [(digest,) for digest in sorted(digests)])
        os.chown(custody, 1002, 1002)
        custody.chmod(0o600)
    return {row["machine_id"] for row in [*identities, *live_identities]
            if row["principal"] in erased} | machines


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
        with closing(sqlite3.connect(staged / relative)) as db, db:
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
    unavailable_sources = []
    erased = erasures(current, staged, identity_exists=identity_exists or workos_user_exists,
                      unavailable_sources=unavailable_sources)
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
    pending = [{"uid": uid, "store": str(store),
                "quarantine": str(stores[0] / (
                    ".tinyassets-erasure-quarantine." +
                    (staged / ".restore-epoch").read_text() + f"-{index}-{uid}") / str(uid))}
               for index, store in enumerate(stores) for uid in sorted(erased)]
    report = {"backup_time": backup_time, "restore_time": now,
              "credential_roots_reset": True, "operation_fences_reset": True,
              "erased_owners": len(erased), "erased_uids": sorted(erased), "billing": billing,
              "cleanup_complete": False,
              "erasure_failures": [], "pending_erasures": pending,
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


def _save_report(path, report):
    temporary = path.with_suffix(".tmp")
    _safe(path.parent, temporary.name)
    with temporary.open("w") as stream:
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o600)
        json.dump(report, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finish_erasures(root):
    """Under the layout lock, resume published cleanup before any box starts."""
    from tinyassets.boxes.destroy import quarantine, tree
    from tinyassets.boxes.provider import BoxError
    from tinyassets.router_credentials import DeletedOwners

    path = _safe(root, ".disaster-restore.json")
    if not path.exists():
        return
    info = path.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("recovery cleanup requires a root-private report")
    report = json.loads(path.read_text())
    if report.get("cleanup_complete"):
        return
    while report.get("pending_erasures"):
        item = report["pending_erasures"][0]
        uid, store, destination = item["uid"], Path(item["store"]), Path(item["quarantine"])
        # The fixed destination is recorded BEFORE moving: a crash after rename
        # can recover its receipt without losing track of the quarantined tree.
        if not destination.exists():
            try:
                tree(store, uid, erase=False)
                tree(store, uid, erase=True)
            except (OSError, BoxError):
                quarantine(store, uid, destination_root=destination.parent)
        if destination.exists():
            report["erasure_failures"].append({
                "uid": uid, "quarantine": str(destination), "error": "erasure refused"})
        report["pending_erasures"].pop(0)
        _save_report(path, report)
    failed = {item["uid"] for item in report["erasure_failures"]}
    deleted = DeletedOwners(root)
    for row in _rows(root, ".broker/state/owner-identities.db", "owner_identities"):
        if row["machine_id"] in report.get("erased_uids", []):
            receipt = deleted.receipt(row["principal"])
            removed = row["machine_id"] not in failed
            done = list(receipt.get("completed_phases", []))
            if removed and "owner_tree" not in done:
                done.append("owner_tree")
            updated = {**receipt, "home_removed": removed, "completed_phases": done,
                       "restore_pending": False,
                       "quarantines": [item["quarantine"] for item in report["erasure_failures"]
                                       if item["uid"] == row["machine_id"]],
                       "unfinished_phases": [step for step in (
                           "owner_tree", "broker_egress", "billing", "credentials", "identity")
                           if step not in done]}
            if updated != receipt:
                deleted.save_receipt(row["principal"], updated)
    report["cleanup_complete"] = True
    _save_report(path, report)
    print("WARN disaster restore: " + json.dumps(report, sort_keys=True))
