"""Offline shared-layer recovery; no owner-volume paths or content."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import tempfile
import time
from contextlib import closing

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
            result[name] = (
                ddl,
                columns,
                keys,
                [
                    dict(zip(columns, row))
                    for row in db.execute(f"SELECT * FROM {_quote(name)} NOT INDEXED")
                ],
            )
        return result


def _rows(root, relative, table):
    return _read(root, relative).get(table, (None, [], [], []))[3]


# These are wholesale resets, never intersections with live authority.
RESET_TABLES = {
    ".api-keys.sqlite3": ("api_keys",),
    ".hook-owners.sqlite3": ("hook_owners",),
    ".auth.db": ("oauth_clients", "authorization_codes", "access_tokens", "refresh_tokens"),
    ".outside-client-authority.sqlite3": ("outside_families", "outside_grants", "outside_effects"),
    ".hosted-model-auth.db": ("hosted_model_flows", "connection_oauth_flows"),
    ".runtime/owner-sessions.db": (
        "owner_sessions",
        "owner_login_flows",
        "owner_login_completions",
        "owner_refresh_sessions",
        "owner_approval_handoffs",
        "canary_sign_in_attempts",
        "approval_handoffs",
        "inline_model_flows",
    ),
    ".tinyassets.db": ("user_sessions",),
    ".broker/credential-custody.db": (
        "llm_credential_deposit_owners",
        "llm_credential_custody",
        "llm_credential_deposit_commits",
        "llm_credential_refresh_state",
    ),
    ".broker/outbound.db": (
        "outbound_connections",
        "outbound_connection_grants",
        "connection_capabilities",
        "browser_vault",
    ),
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
    for relative in (
        ".connect/session_secret",
        ".connect/client_secret",
        ".runtime/restore-session-seal.key",
    ):
        path = _safe(staged, relative)
        missing_parent = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if missing_parent:
            os.chown(path.parent, 1001, 1001)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
        os.chown(path, 1001, 1001)
    for pattern in (
        ".runtime/app_refresh_sessions/*.json",
        ".platform/credential-vaults/*/.credential-vault.json",
    ):
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
    operational.write_text(
        json.dumps({name: secrets.token_urlsafe(48) for name in (WIKI_CANARY_TOKEN_ENV, KEY_ENV)})
    )
    operational.chmod(0o600)
    os.chown(operational, 1001, 1001)
    from tinyassets.router_traffic import NAME, initialize

    _read(staged, NAME)
    initialize(staged)
    with closing(sqlite3.connect(staged / NAME)) as db, db:
        db.execute("UPDATE traffic SET traffic_opened_at=NULL, credentials_after=?", (now,))
    os.chown(staged / NAME, 0, 1001)
    (staged / NAME).chmod(0o660)


def rotate_box_secrets(staged):
    identity = staged / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(identity)) as db, db:
        if "owner_box_secrets" in _read(staged, str(identity.relative_to(staged))):
            for (owner,) in db.execute("SELECT principal FROM owner_box_secrets").fetchall():
                db.execute(
                    "UPDATE owner_box_secrets SET secret=? WHERE principal=?",
                    (secrets.token_urlsafe(32), owner),
                )


def fence_ops(staged, now):
    from tinyassets.broker.ops import MAX_FUTURE_SKEW_S

    cutoff = int(now * 1000) + MAX_FUTURE_SKEW_S * 1000 + 1
    _read(staged, ".broker/ops.db")
    path = _safe(staged, ".broker/ops.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER)")
        db.execute(
            "INSERT INTO meta VALUES ('cutoff_ms',?) ON CONFLICT(key) "
            "DO UPDATE SET value=MAX(value,excluded.value)",
            (cutoff,),
        )
    os.chown(path, 1002, 1002)


class RecoveryUnavailable(RuntimeError):
    """Current platform authority is unavailable; admission stays closed."""


def resume_deletion_jobs(staged, *, backup_time):
    """C3 lane-b seam: recover off-host lifecycle authority and finish its jobs.

    Called after credential invalidation and before provider reconciliation or
    committing a stable layout. Lane b must recover permanent bindings, erased
    admission and all later unfinished jobs. No historical snapshot is enough.
    """
    raise RecoveryUnavailable("deletion-job recovery is not implemented; admission remains closed")


def reconcile_authority(staged, now):
    """Recover current billing, spend and suspension facts from their authorities."""
    from tinyassets.billing.stripe_adapter import restore_billing

    billing = restore_billing(staged, now)
    # C3 requires current spend and abuse authority too. The existing billing
    # adapter reports no external abuse source and has no spend reconciliation.
    # Its subscription refresh alone cannot authorize restored admission.
    raise RecoveryUnavailable(f"current spend and suspension authority unavailable: {billing}")


def write_layout(root, document):
    """C3: persist closed admission before any recoverable authority is changed."""
    target = _safe(root, ".layout.json")
    # Deployment root cannot O_CREAT|O_TRUNC a service-owned file in the
    # sticky shared root with fs.protected_regular=2. Publish a new inode;
    # the closed marker is also never observable as a partially written JSON.
    fd, temporary = tempfile.mkstemp(prefix=".platform-layout-", dir=root)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(document, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def platform_recovery(
    staged, *, backup_time, now=None, authority=None, identity_exists=None, revoke_sessions=None
):
    """Prepare only the shared stage while every service is stopped.

    The layout remains non-admitting across every failure and process restart.
    A caller may publish the stage only after this function returns. Authorities
    are injected solely by offline callers/tests, never by a request payload.
    """
    from tinyassets.router_account_deletion import revoke_workos_sessions, workos_user_exists

    now = time.time() if now is None else now
    marker = _safe(staged, ".layout.json")
    layout = json.loads(marker.read_text())
    if layout.get("box_layout") != "owner-box":
        raise RecoveryUnavailable("platform recovery requires a shared-layer backup")
    layout["state"] = "recovering"
    write_layout(staged, layout)
    reset_credentials(staged, now)
    fence_ops(staged, now)
    resume_deletion_jobs(staged, backup_time=backup_time)
    rotate_box_secrets(staged)
    _read(staged, ".broker/plans.db")
    (authority or reconcile_authority)(staged, now)
    identities = _read(staged, ".broker/state/owner-identities.db")
    if not {"owner_identities", "box_records"} <= identities.keys():
        raise RecoveryUnavailable("current identity authority unavailable")
    reconciled = 0
    # Current admission, installed by lane b, positively selects the accounts
    # to reconcile. Permanent identities include terminally erased accounts.
    for row in identities["box_records"][3]:
        owner = row["principal"]
        if not (identity_exists or workos_user_exists)(owner):
            raise RecoveryUnavailable("identity provider did not confirm current identity")
        (revoke_sessions or revoke_workos_sessions)(owner)
        reconciled += 1
    report = {
        "backup_time": backup_time,
        "restore_time": now,
        "credential_roots_reset": True,
        "broker_replay_cutoff_advanced": True,
        "owners_reconciled": reconciled,
    }
    report_path = _safe(staged, ".platform-recovery.json")
    report_path.write_text(json.dumps(report, sort_keys=True))
    report_path.chmod(0o600)
    layout["state"] = "stable"
    write_layout(staged, layout)
    return report
