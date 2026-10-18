"""Offline monotonic authority projection into unpublished restore volumes.

The caller holds both layout locks with all consumers stopped. Only staged
paths are mutated; publication and rollback belong to the paired restore.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

from tinyassets.sqlite_readonly import readonly_db

# Absence is a revocation for these authorities. Compare credential incarnation
# as well as primary key: replacing a secret must never resurrect the old one.
AUTHORITIES = {
    ".api-keys.sqlite3": ("api_keys",),
    ".hook-owners.sqlite3": ("hook_owners",),
    ".auth.db": ("oauth_clients", "authorization_codes", "access_tokens", "refresh_tokens"),
    ".outside-client-authority.sqlite3": ("outside_switch",),
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
    ".broker/state/owner-identities.db": ("owner_box_secrets",),
}
MONOTONIC = {
    ".router-deleted-owners.sqlite3": ("deleted_owners",),
    ".broker/credential-custody.db": ("deleted_principals",),
    ".broker/state/owner-identities.db": (
        "owner_identities", "center_admissions", "box_records", "owner_box_revocations"),
    ".boxhost/boxhost.db": ("owner_destroys",),
    ".broker/plans.db": ("checkout_settlements",),
}


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


def _merge(db, name, source):
    ddl, columns, keys, rows = source
    db.execute(ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
               if "IF NOT EXISTS" not in ddl else ddl)
    for row in rows:
        # Permanent identity/admission tables reject updates. A conflicting
        # reservation means a mismatched backup, rather than a newer fact.
        where = " AND ".join(f"{_quote(key)}=?" for key in (keys or columns))
        previous = db.execute(f"SELECT * FROM {_quote(name)} WHERE {where}",
                              [row[key] for key in (keys or columns)]).fetchone()
        if previous is not None:
            if name == "box_records":
                old = dict(zip(columns, previous))
                if any(old[key] != row[key] for key in columns if key != "bhard_bytes"):
                    raise ValueError("mismatched permanent identity in backup pair")
                db.execute(f"UPDATE box_records SET bhard_bytes=? WHERE {where}",
                           [row["bhard_bytes"], *[row[key] for key in (keys or columns)]])
                continue
            if name in {"owner_identities", "center_admissions"}:
                if tuple(row.values()) != previous:
                    raise ValueError("mismatched permanent identity in backup pair")
                continue
            if name == "owner_box_revocations":
                db.execute("UPDATE owner_box_revocations SET deleted=MAX(deleted,?) "
                           "WHERE principal=?", (row["deleted"], row["principal"]))
                continue
            if name == "checkout_settlements":
                old = dict(zip(columns, previous))
                if any(old[key] != row[key] for key in ("session_id", "attempt_id", "owner")):
                    raise ValueError("mismatched checkout settlement in backup pair")
            if name == "policy":
                old = dict(zip(columns, previous))
                if old["generation"] > row["generation"]:
                    continue
                if old["generation"] == row["generation"] and old != row:
                    raise ValueError("mismatched policy generation in backup pair")
            db.execute(f"DELETE FROM {_quote(name)} WHERE {where}",
                       [row[key] for key in (keys or columns)])
        db.execute(f"INSERT INTO {_quote(name)} ({','.join(map(_quote, columns))}) "
                   f"VALUES ({','.join('?' for _ in columns)})", list(row.values()))


def _intersect(db, name, restored, live, erased):
    _, columns, keys, rows = restored
    live_rows = live[3] if live else []
    # These observational values are not credential incarnation.
    ignored = {"last_used_at", "revoked_at", "revoked"}
    for row in rows:
        matches = [candidate for candidate in live_rows if all(
            candidate.get(key) == row[key] for key in (keys or columns))]
        candidate = matches[0] if len(matches) == 1 else None
        if any(row.get(key) in erased for key in ("owner", "owner_user_id", "principal",
                                                 "user_id")):
            candidate = None
        if "identity_json" in row and json.loads(row["identity_json"]).get("user_id") in erased:
            candidate = None
        revoked = candidate is None or any(
            candidate.get(key) != row[key] for key in columns if key not in ignored)
        revoked = revoked or bool(candidate and (candidate.get("revoked_at") is not None
                                                 or candidate.get("revoked", 0)))
        if not revoked:
            continue
        where = " AND ".join(f"{_quote(key)} IS ?" for key in (keys or columns))
        values = [row[key] for key in (keys or columns)]
        if "revoked_at" in columns:
            db.execute(f"UPDATE {_quote(name)} SET revoked_at=COALESCE(revoked_at,?) "
                       f"WHERE {where}", [(candidate or {}).get("revoked_at") or time.time(),
                                           *values])
        elif "revoked" in columns:
            db.execute(f"UPDATE {_quote(name)} SET revoked=1 WHERE {where}", values)
        else:
            db.execute(f"DELETE FROM {_quote(name)} WHERE {where}", values)


def carry_shared(current: Path, staged: Path, *, disaster: bool = False) -> set[int]:
    """Read the live projection first, then reapply erasures and revocations."""
    authorities, monotonic = dict(AUTHORITIES), dict(MONOTONIC)
    for root in (current, staged):
        for path in root.glob(".broker/accounts/*/policy.db"):
            relative = path.relative_to(root).as_posix()
            authorities[relative] = ("once_approvals",)
            monotonic[relative] = ("policy",)
    relatives = authorities.keys() | monotonic.keys()
    restored = {name: _read(staged, name) for name in relatives}
    live = {}
    readable = set()
    for name in relatives:
        try:
            live[name] = _read(current, name)
            if (current / name).exists():
                readable.add(name)
            elif disaster:
                live[name] = restored[name]
        except (OSError, sqlite3.DatabaseError, UnreadableFacts):
            if not disaster:
                raise
            live[name] = restored[name]
    erased = set()
    for name, table, owner_key in (
        (".router-deleted-owners.sqlite3", "deleted_owners", "owner"),
        (".boxhost/boxhost.db", "owner_destroys", "owner_id"),
    ):
        for tables in (live, restored):
            erased.update(row[owner_key] for row in tables[name].get(table, (None, [], [], []))[3])
    identity = ".broker/state/owner-identities.db"
    for tables in (live, restored):
        erased.update(row["principal"] for row in tables[identity].get(
            "owner_box_revocations", (None, [], [], []))[3] if row["deleted"])
    digests = {row["digest"] for tables in (live, restored) for row in tables[
        ".broker/credential-custody.db"].get("deleted_principals", (None, [], [], []))[3]}
    owners = [row for tables in (live, restored) for row in tables[identity].get(
        "owner_identities", (None, [], [], []))[3]]
    erased.update(row["principal"] for row in owners
                  if hashlib.sha256(row["principal"].encode()).hexdigest() in digests)
    for relative in sorted(relatives):
        if not live[relative] and not restored[relative]:
            continue
        path = _safe(staged, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            for name in monotonic.get(relative, ()):
                if name in live[relative]:
                    _merge(db, name, live[relative][name])
            for name in authorities.get(relative, ()):
                if name in restored[relative]:
                    _intersect(db, name, restored[relative][name], live[relative].get(name), erased)
            if relative == identity and "owner_box_revocations" in live[identity]:
                for owner in sorted(erased):
                    db.execute("INSERT INTO owner_box_revocations VALUES (?,1) "
                               "ON CONFLICT(principal) DO UPDATE SET deleted=1", (owner,))
        if relative in readable:
            import os

            source_info = (current / relative).stat()
            os.chown(path, source_info.st_uid, source_info.st_gid)
            path.chmod(stat.S_IMODE(source_info.st_mode))
    # Files whose deletion/rotation is the revocation event. A changed sealed
    # handle or raw router credential cannot safely authenticate its old value.
    for pattern in (".runtime/app_refresh_sessions/*.json", ".connect/*",
                    ".broker/accounts/*/.deleted"):
        for path in staged.glob(pattern):
            relative = path.relative_to(staged)
            source = _safe(current, relative)
            _safe(staged, relative)
            try:
                contents = source.read_bytes() if source.exists() else None
            except OSError:
                if not disaster:
                    raise
                contents = None
            if disaster and contents is None:
                contents = path.read_bytes()
            if path.name != ".deleted" and contents != path.read_bytes():
                path.unlink()
            elif pattern.startswith(".runtime/") and json.loads(path.read_text()).get(
                    "owner") in erased:
                path.unlink()
        if pattern.endswith(".deleted"):
            markers = list(current.glob(pattern))
            if disaster:
                markers += list(staged.glob(pattern))
            for path in markers:
                root = staged if path.is_relative_to(staged) else current
                source = _safe(root, path.relative_to(root))
                try:
                    contents = source.read_bytes()
                except OSError:
                    if not disaster:
                        raise
                    continue
                target = _safe(staged, path.relative_to(root))
                target.parent.mkdir(parents=True, exist_ok=True)
                if source != target:
                    shutil.copy2(source, target)
                for suffix in ("", "-wal", "-shm", "-journal"):
                    _safe(staged, target.with_name("policy.db" + suffix).relative_to(staged)
                          ).unlink(missing_ok=True)
    erased_centers = {row["center"] for tables in (live, restored) for row in tables[
        identity].get("center_admissions", (None, [], [], []))[3] if row["principal"] in erased}
    for path in staged.glob(".platform/credential-vaults/*/.credential-vault.json"):
        source = _safe(current, path.relative_to(staged))
        _safe(staged, path.relative_to(staged))
        payload = json.loads(path.read_text())
        try:
            records = json.loads(source.read_text())["credentials"] if source.exists() else (
                payload["credentials"] if disaster else [])
        except (OSError, ValueError):
            if not disaster:
                raise
            records = payload["credentials"]
        payload["credentials"] = [row for row in payload["credentials"] if row in records]
        if path.parent.name in erased_centers:
            payload["credentials"] = []
        path.write_text(json.dumps(payload))
    if erased:
        from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
        from tinyassets.storage.outbound_connections import ConnectionLedger

        ledger_path = _safe(staged, ".broker/outbound.db")
        if ledger_path.exists():
            ledger = ConnectionLedger(ledger_path, data_root=staged)
            for owner in sorted(erased):
                local_erase(ledger, principal=owner, command_center=ACCOUNT_SCOPE,
                            erase_external_keys=False)
        from tinyassets.router_credentials import DeletedOwners

        for owner in sorted(erased):
            DeletedOwners(staged).add(owner, {
                "unfinished_phases": ["billing", "identity"], "home_removed": True,
                "source": "restore carried owner erasure",
            })
            with closing(sqlite3.connect(staged / ".broker/credential-custody.db")) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS deleted_principals "
                           "(digest TEXT PRIMARY KEY)")
                db.execute("INSERT OR IGNORE INTO deleted_principals VALUES (?)",
                           (hashlib.sha256(owner.encode()).hexdigest(),))
            policy = ".broker/accounts/" + hashlib.sha256(owner.encode()).hexdigest()
            marker = _safe(staged, policy + "/.deleted")
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(mode=0o600)
            for suffix in ("", "-wal", "-shm", "-journal"):
                _safe(staged, policy + "/policy.db" + suffix).unlink(missing_ok=True)
    return {row.get("machine_id", row.get("machine")) for row in owners
            if row["principal"] in erased}


def disaster_unknowns(current: Path, staged: Path, backup_time: str) -> list[str]:
    """Survivors prove individual facts, never completeness after volume loss."""
    classes = {f"{relative}:{table}" for registry in (AUTHORITIES, MONOTONIC)
               for relative, tables in registry.items() for table in tables}
    classes.update({"owner erasures", "shared revocations", "consumed operation IDs",
                    "owner journal epochs", "sealed refresh-session revocations",
                    "router credential revocations", "custody-slot revocations",
                    "deleted policy authority"})
    for root in (current, staged):
        for path in root.glob(".broker/accounts/*/policy.db"):
            classes.update(f"{path.relative_to(root).as_posix()}:{table}"
                           for table in ("policy", "once_approvals"))
    return [f"WARN disaster restore: {name} after {backup_time}: unknown"
            for name in sorted(classes)]


def erase_trees(store: Path, uids: set[int]) -> None:
    """Use the same descriptor sweep and destroy as fenced account deletion."""
    from tinyassets.boxes.destroy import tree

    for uid in sorted(uids):
        tree(store, uid, erase=False)
        tree(store, uid, erase=True)
