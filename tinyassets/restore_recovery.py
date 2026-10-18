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
    """Read LIVE evidence before mutating staging or publishing either member."""
    from tinyassets.router_traffic import NAME

    layout = json.loads((current / ".layout.json").read_text())
    if not (current / NAME).exists():
        if (layout.get("box_layout") == "owner-box" or layout.get("state") != "migrating"
                or layout.get("migration") != "owner-box"):
            raise ValueError("live router traffic marker missing")
        if layout.get("split") != "owner-split" and layout.get("box_layout") != "owner-split":
            raise ValueError("live layout does not prove pre-traffic cutover")
    else:
        marker = _read(current, NAME).get("traffic", (None, [], [], []))[3]
        if len(marker) != 1 or marker[0].get("singleton") != 1:
            raise ValueError("live router traffic marker unreadable")
        opened = marker[0]["traffic_opened_at"]
        credentials_after = marker[0]["credentials_after"]
        if credentials_after:
            raise ValueError("normal restore refuses a disaster recovery generation")
        if opened is not None:
            relation = "newer than backup" if opened > backup_time else "backup is post-traffic"
            raise ValueError("normal restore refuses traffic_opened_at: " + relation)
    registered = set()
    saved_layout = json.loads((staged / ".layout.json").read_text())
    roots = ((current, staged) if layout.get("box_layout") == saved_layout.get("box_layout")
             else (current,))
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    if (stream.read(16) == b"SQLite format 3\x00"
                            or path.suffix in {".db", ".sqlite3"}):
                        registered.add(path.relative_to(root).as_posix())
        receipt = root / ".owner-cutover.json"
        if receipt.exists():
            registered.update(name for name in json.loads(receipt.read_text()).get(
                "final_manifest", {}) if name.endswith((".db", ".sqlite3")))
    for relative in sorted(registered):
        # Pre-cutover source databases move to owner trees; completed cutover
        # normal restore validates its live registry, not retired legacy paths.
        if not (current / relative).is_file():
            if layout.get("box_layout") == "owner-box":
                raise ValueError("registered live store missing: " + relative)
            continue
        _read(current, relative)
    if layout.get("box_layout") != "owner-box":
        return
    identities = _read(current, ".broker/state/owner-identities.db")
    saved_identities = _read(staged, ".broker/state/owner-identities.db")
    saved_machines = {row["principal"]: row["machine_id"]
                      for row in saved_identities.get(
        "owner_identities", (None, [], [], []))[3]}
    for row in identities["owner_identities"][3]:
        if (row["principal"] in saved_machines
                and saved_machines[row["principal"]] != row["machine_id"]):
            raise ValueError("mismatched permanent owner identity in backup pair")
    deleted = {row["principal"] for row in identities.get(
        "owner_box_revocations", (None, [], [], []))[3] if row["deleted"]}
    for row in identities["owner_identities"][3]:
        if row["principal"] in deleted:
            continue
        tree = owners / str(row["machine_id"])
        if tree.is_symlink() or not tree.is_dir():
            raise ValueError("registered live owner tree missing")
        def unreadable(error):
            raise error

        for directory, dirs, files in os.walk(tree, onerror=unreadable):
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    with path.open("rb") as stream:
                        while stream.read(1024 * 1024):
                            pass
        if not (tree / ".owner-boxd/boxhost.db").is_file():
            raise ValueError("live owner journal missing")
        journal = _read(tree, ".owner-boxd/boxhost.db")
        if not {"boxes", "ops", "host"}.issubset(journal):
            raise ValueError("live owner journal unreadable: missing journal schema")
        if restored_owners is not None:
            saved_tree = restored_owners / tree.name
            for path in saved_tree.rglob("*"):
                if path.suffix in {".db", ".sqlite3"}:
                    relative = path.relative_to(saved_tree)
                    if not (tree / relative).is_file():
                        raise ValueError("registered live owner store missing")
                    _read(tree, relative)
    known = {str(row["machine_id"]) for row in identities["owner_identities"][3]}
    if any(path.name.isdecimal() and path.name not in known for path in owners.iterdir()):
        raise ValueError("live owner inventory changed without an identity reservation")


def _rows(root, relative, table):
    return _read(root, relative).get(table, (None, [], [], []))[3]


def erasures(current, staged, *, identity_exists):
    """The sole carried fact: union readable tombstones, then ask the identity seam."""
    sources = (
        (".router-deleted-owners.sqlite3", "deleted_owners"),
        (".broker/state/owner-identities.db", "owner_box_revocations"),
        (".broker/credential-custody.db", "deleted_principals"),
        (".boxhost/boxhost.db", "owner_destroys"),
    )
    erased, digests, machines = set(), set(), set()
    identities = _rows(staged, ".broker/state/owner-identities.db", "owner_identities")
    for root in (current, staged):
        for relative, table in sources:
            try:
                rows = _rows(root, relative, table)
            except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
                if root == staged:
                    raise
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
    for row in identities:
        owner = row["principal"]
        if (hashlib.sha256(owner.encode()).hexdigest() in digests
                or row["machine_id"] in machines or not identity_exists(owner)):
            erased.add(owner)
    from tinyassets.router_credentials import DeletedOwners

    for owner in sorted(erased):
        DeletedOwners(staged).add(owner, {
            "unfinished_phases": ["billing", "identity"], "home_removed": True,
            "source": "disaster erasure reconciliation"})
    if erased:
        tombstones = staged / ".router-deleted-owners.sqlite3"
        os.chown(tombstones, 1001, 1001)
        tombstones.chmod(0o600)
    identity = _safe(staged, ".broker/state/owner-identities.db")
    with closing(sqlite3.connect(identity)) as db, db:
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
    return {row["machine_id"] for row in identities if row["principal"] in erased} | machines


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
    os.chown(staged / NAME, 1001, 1001)


def fence_ops(staged, trees, now):
    from tinyassets.boxes.state import _SCHEMA

    cutoff = int(now * 1000) + 1
    _read(staged, ".broker/ops.db")
    path = _safe(staged, ".broker/ops.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER)")
        db.execute("INSERT INTO meta VALUES ('cutoff_ms',?) ON CONFLICT(key) "
                   "DO UPDATE SET value=MAX(value,excluded.value)", (cutoff,))
    os.chown(path, 1002, 1002)
    owners = {row["machine_id"]: row["principal"] for row in _rows(
        staged, ".broker/state/owner-identities.db", "owner_identities")}
    for tree in trees.iterdir():
        if not tree.name.isdecimal():
            continue
        path = _safe(tree, ".owner-boxd/boxhost.db")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            for statement in _SCHEMA:
                db.execute(statement)
            db.execute("INSERT OR IGNORE INTO boxes VALUES (?,1,0)", (owners[int(tree.name)],))
            db.execute("UPDATE boxes SET epoch=MAX(epoch+1,?), generation=generation+1",
                       (cutoff,))
            db.execute("INSERT INTO host VALUES ('restore_epoch',?) ON CONFLICT(k) "
                       "DO UPDATE SET v=MAX(v+1,excluded.v)", (cutoff,))
            db.execute("UPDATE ops SET state='unknown_after_restore'")
            epoch = db.execute("SELECT epoch FROM boxes WHERE command_center_id=?",
                               (owners[int(tree.name)],)).fetchone()[0]
        os.chown(path.parent, int(tree.name), int(tree.name))
        os.chown(path, int(tree.name), int(tree.name))
        path.chmod(0o600)
        # Fence even centers with no earlier receipts. New receipt databases
        # inherit the owner's epoch through the same runtime check.
        for pattern in (".remote-ta-receipts.sqlite3", ".external_write_receipts.db"):
            for receipt in tree.rglob(pattern):
                _read(tree, receipt.relative_to(tree))
                with closing(sqlite3.connect(receipt)) as db, db:
                    db.execute("CREATE TABLE IF NOT EXISTS restore_fence (epoch INTEGER NOT NULL)")
                    db.execute("DELETE FROM restore_fence")
                    db.execute("INSERT INTO restore_fence VALUES (?)", (epoch,))


def disaster_restore(current, staged, trees, *, backup_time, now=None,
                     identity_exists=None, billing_sync=None):
    from tinyassets.billing.stripe_adapter import restore_billing
    from tinyassets.boxes.destroy import tree as destroy
    from tinyassets.router_account_deletion import workos_user_exists

    now = time.time() if now is None else now
    erased = erasures(current, staged, identity_exists=identity_exists or workos_user_exists)
    for uid in sorted(erased):
        destroy(trees, uid, erase=False)
        destroy(trees, uid, erase=True)
    reset_credentials(staged, now)
    fence_ops(staged, trees, now)
    _read(staged, ".broker/plans.db")
    billing = (billing_sync or restore_billing)(staged, now)
    # One broker-wide spend fence covers all owners, including absent policies.
    fence = _safe(staged, ".broker/restore-spend-until")
    fence.write_text(str((int(now) // 86400 + 1) * 86400))
    os.chown(fence, 1002, 1002)
    report = {"backup_time": backup_time, "restore_time": now,
              "credential_roots_reset": True, "operation_fences_reset": True,
              "erased_owners": len(erased), "billing": billing,
              "spend_blocked_until": int(fence.read_text()),
              "reverted_classes": ["owner consents and grants", "effector consents",
                  "capability grants", "graph receivers", "outside grants",
                  "spend reservations", "policies", "content", "turns", "schedules",
                  "receipts", "abuse suspension history"]}
    _safe(staged, ".disaster-restore.json").write_text(json.dumps(report, sort_keys=True))
    print("WARN disaster restore: " + json.dumps(report, sort_keys=True))
    return report
