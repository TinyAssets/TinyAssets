"""C3/C14 shared-layer preparation and broker startup; no owner content."""

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
        ".broker/accounts/*/credentials/.credential-vault.json",
    ):
        for path in staged.glob(pattern):
            _safe(staged, path.relative_to(staged)).unlink()
    from tinyassets.auth.wiki_canary import WIKI_CANARY_TOKEN_ENV
    from tinyassets.onboarding.canary_sign_in import KEY_ENV

    operational = _safe(staged, ".router-credentials.json")
    operational.unlink(missing_ok=True)
    operational.write_text(
        json.dumps({name: secrets.token_urlsafe(48) for name in (WIKI_CANARY_TOKEN_ENV, KEY_ENV)})
    )
    operational.chmod(0o600)
    os.chown(operational, 1001, 1001)
    from tinyassets.cutover_control import close_admission
    from tinyassets.router_traffic import NAME

    _safe(staged, NAME)
    close_admission(staged, recovering=True)


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
    _read(staged, ".broker/state/ops.db")
    path = _safe(staged, ".broker/state/ops.db")
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


async def resume_deletion_jobs(server, *, backup_time):
    """Import current off-host receipts and execute the broker's normal path.

    Completed receipts matter too: an older backup may predate their erasure.
    The normal path reasserts withdrawal but skips every completed effect.
    """
    from tinyassets.broker.deletion_jobs import STEPS

    jobs = server.deletion_jobs
    from tinyassets.broker.admission import thread_work

    current = {job["id"]: job for job in await thread_work(jobs.records.remote.records)}
    pending = False
    for local in jobs.records.records():
        if local["id"] not in current:
            raise RecoveryUnavailable("current off-host deletion record missing")
    for job in current.values():
        # C3 requires every unfinished job and every completion newer than the
        # backup. Older completed receipts are already terminal at that point;
        # importing their exact progress also replaces any stale local retry.
        if (job["completed_steps"] == list(STEPS)
                and job["results"]["receipt"]["completed_at"] <= backup_time):
            await thread_work(jobs.records.save, job)
            continue
        task = server.routing.tasks.get(job["owner"])
        if task is not None and task.done():
            result = task.result()
            if result is not None and result.get("status") == "deleted":
                continue
        if task is None or task.done():
            async def resume(owner, loaded=job):
                return await jobs._resume_loaded_job(loaded)
            server.routing.start(job["owner"], resume, recovery=True)
        pending = True
    return not pending


async def reconcile_authority(staged, now, *, routing):
    """Stripe owns billing; non-billing reasons remain as restored (C3)."""
    from tinyassets.billing.stripe_adapter import restore_billing

    return await restore_billing(staged, now, routing=routing)


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


def prepare_platform(staged, *, backup_time, now=None):
    """Offline root phase; install this shared layer before starting services."""
    now = time.time() if now is None else now
    marker = _safe(staged, ".layout.json")
    layout = json.loads(marker.read_text())
    if layout.get("box_layout") != "owner-box":
        raise RecoveryUnavailable("platform recovery requires a shared-layer backup")
    layout.pop("preparation", None)
    layout["state"] = "recovering"
    layout["recovery"] = {"backup_time": backup_time, "restore_time": now}
    write_layout(staged, layout)
    reset_credentials(staged, now)
    fence_ops(staged, now)
    rotate_box_secrets(staged)
    _read(staged, ".broker/plans.db")
    report_path = _safe(staged, ".platform-recovery.json")
    report_path.unlink(missing_ok=True)
    report_path.write_text("{}")
    report_path.chmod(0o600)
    os.chown(report_path, 1002, 1002)
    layout["preparation"] = {"credential_roots_reset": True,
                             "broker_replay_cutoff_advanced": True}
    write_layout(staged, layout)
    return layout["recovery"]


async def platform_recovery(server, *, backup_time, now=None, authority=None,
                      identity_exists=None, revoke_sessions=None):
    """Running-broker startup phase, before ordinary admission or lifecycle ticks."""
    from tinyassets.broker.admission import read
    from tinyassets.router_account_deletion import revoke_workos_sessions, workos_user_exists

    staged = server._data_root
    now = time.time() if now is None else now
    if not await resume_deletion_jobs(server, backup_time=backup_time):
        return None
    if authority is not None:
        authority(staged, now)
    else:
        await reconcile_authority(staged, now, routing=server.routing)
    reconciled = 0
    with closing(server._owner_identities._connect(write=False)) as db:
        owners = [row[0] for row in db.execute("SELECT principal FROM owner_identities")]
    for owner in owners:
        if read(server._owner_identities, owner).state not in {"admitted", "suspended"}:
            continue
        if not (identity_exists or workos_user_exists)(owner):
            raise RecoveryUnavailable("identity provider did not confirm current identity")
        (revoke_sessions or revoke_workos_sessions)(owner)
        reconciled += 1
    report = {
        "backup_time": backup_time,
        "restore_time": now,
        "jobs_resumed": True,
        "billing_reconciled": True,
        "owners_reconciled": reconciled,
    }
    report_path = _safe(staged, ".platform-recovery.json")
    with report_path.open("r+") as stream:
        json.dump(report, stream, sort_keys=True)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    return report


def finish_platform(root):
    """Root publishes admission only after the running broker's durable receipt."""
    from tinyassets.cutover_control import _root, _write
    from tinyassets.router_traffic import NAME

    _root()
    layout = json.loads(_safe(root, ".layout.json").read_text())
    report = json.loads(_safe(root, ".platform-recovery.json").read_text())
    expected = layout["recovery"]
    if (layout["state"] not in {"recovering", "stable"}
            or any(report.get(key) != value for key, value in expected.items())
            or layout.get("preparation", {}).get("credential_roots_reset") is not True
            or layout.get("preparation", {}).get("broker_replay_cutoff_advanced") is not True
            or report.get("jobs_resumed") is not True
            or report.get("billing_reconciled") is not True):
        raise RecoveryUnavailable("broker startup recovery has not completed")
    layout["state"] = "stable"
    write_layout(root, layout)
    _write(root / NAME, {"ordinary": True}, mode=0o644)
    return report
