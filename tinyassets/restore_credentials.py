"""Restore broker credential files and ledger rows for every account.

Reads a verified shared backup or a retained pre-restore directory. Never
resets credentials, rotates box secrets, closes admission, or swaps the volume.
One account's bad row is counted and skipped; the others still merge.
Stdout is aggregate counts and fixed names only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ARCHIVE_RE = re.compile(
    r"tinyassets-(?:data|snapshot)-"
    r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz\Z"
)
RETAINED_RE = re.compile(r"\.tinyassets-restore-old\.[A-Za-z0-9]+\Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_HASH_RE = re.compile(r"[a-f0-9]{64}\Z")
_SOURCE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,63}\Z")
_UID_RE = re.compile(r"3[0-9]{5}\Z")
_RESTORE_LINE = re.compile(
    r"\[restore (\d{4}-\d{2}-\d{2})T[^\]]*\] shared-layer restore complete"
)
BROKER = "tinyassets-broker"
TABLES = (
    "outbound_connections",
    "outbound_connection_grants",
    "connection_capabilities",
    "browser_vault",
)
OWNER_COLUMN = {
    "outbound_connections": "owner_user_id",
    "outbound_connection_grants": "owner_user_id",
    "browser_vault": "owner",
}
FILES = {
    "credential_vault": ".credential-vault.json",
    "subscription_state": ".subscription-state.json",
}
_SLOTS = ("insert", "kept_live", "conflict", "skipped")
_FIXED_STRINGS = frozenset({
    "inspect", "dry-run", "apply", "auto", "archive", "retained",
    "digest_mismatch", "unreadable", "unsafe_member", "digest_unknown",
    "ledger_missing", "source_missing", "broker_stop_failed",
    "broker_start_failed",
})
_KEYS = frozenset({
    "mode", "source_requested", "selected", "catalog_readable", "live",
    "sources", "plan", "apply", "verification", "error",
    "credential_roots_reset", "platform_recovery_present", "layout_readable",
    "restore_complete_dates", "retained_dirs", "accounts",
    "accounts_with_vault", "accounts_with_subscription",
    "outbound_connections", "outbound_connection_grants",
    "connection_capabilities", "browser_vault",
    "kind", "name", "date", "digest_known", "readable",
    "connection_markers", "accounts_affected", "accounts_failed",
    "accounts_unchanged", "files", "tables", "credential_vault",
    "subscription_state", "insert", "kept_live", "conflict", "skipped",
    "rollback_kept", "broker_stopped", "broker_started", "ledger_missing",
    "rollback_date",
    "api_key_http_definitions", "api_key_http_grants_live", "cli_accounts",
    "cli_with_custody", "cli_marker_consistent", "cli_marker_missing",
    "cli_marker_unreadable", "cli_marker_separate_step",
    "source_connection_markers", "cli_markers_restored",
})


class SourceRefused(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def _slot():
    return {name: 0 for name in _SLOTS}


def _run(argv, timeout=3600):
    try:
        return subprocess.run(
            argv, check=False, capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SourceRefused("unreadable") from None


def _string_ok(value):
    return (
        value in _FIXED_STRINGS or _DATE_RE.fullmatch(value)
        or ARCHIVE_RE.fullmatch(value) or RETAINED_RE.fullmatch(value)
    )


def safe_json(report):
    """Refuse any string that is not a fixed token, date, or archive name."""
    def walk(value, key=None):
        if key is not None and key not in _KEYS:
            raise SourceRefused("output_contains_private_value")
        if value is None or isinstance(value, (bool, int)):
            return
        if isinstance(value, str):
            if not _string_ok(value):
                raise SourceRefused("output_contains_private_value")
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for item_key, item in value.items():
                walk(item, item_key)
            return
        raise SourceRefused("output_contains_private_value")

    walk(report)
    return json.dumps(report, sort_keys=True)


def _columns(db, table):
    info = db.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    return [row[1] for row in info]


def _primary_key(db, table):
    info = db.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    keys = [row[1] for row in sorted(info, key=lambda item: item[5]) if row[5]]
    return keys


def _table_exists(db, table):
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    return row is not None


def _rows(db, table):
    if not _table_exists(db, table):
        return []
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute(f"SELECT * FROM {_quote(table)}")]


def _open_ro(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def account_files(root):
    """hash -> {kind: Path}. Skips symlinks and non-hash directory names."""
    found = {}
    accounts = root / ".broker" / "accounts"
    if not accounts.is_dir() or accounts.is_symlink():
        return found
    for directory in accounts.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        if not _HASH_RE.fullmatch(directory.name):
            continue
        slot = {}
        for kind, filename in FILES.items():
            path = directory / "credentials" / filename
            if path.is_symlink() or not path.is_file():
                continue
            slot[kind] = path
        if slot:
            found[directory.name] = slot
    return found


def _known_owners(root):
    seen = []
    for _uid, principal in _uids(root):
        if principal not in seen:
            seen.append(principal)
    return seen


def _file_present(root, digest, kind):
    path = root / ".broker" / "accounts" / digest / "credentials" / FILES[kind]
    return path.is_file() and not path.is_symlink()


def _count_files(root):
    owners = _known_owners(root)
    if owners:
        vault = sub = 0
        for owner in owners:
            digest = hashlib.sha256(owner.encode()).hexdigest()
            vault += _file_present(root, digest, "credential_vault")
            sub += _file_present(root, digest, "subscription_state")
        return {
            "accounts": len(owners),
            "accounts_with_vault": vault,
            "accounts_with_subscription": sub,
        }
    files = account_files(root)
    vault = sum("credential_vault" in slot for slot in files.values())
    state = sum("subscription_state" in slot for slot in files.values())
    return {
        "accounts": len(files),
        "accounts_with_vault": vault,
        "accounts_with_subscription": state,
    }


def _ledger_counts(path):
    counts = {table: 0 for table in TABLES}
    if not path.is_file() or path.is_symlink():
        return counts
    try:
        with _open_ro(path) as db:
            for table in TABLES:
                if _table_exists(db, table):
                    counts[table] = db.execute(
                        f"SELECT COUNT(*) FROM {_quote(table)}"
                    ).fetchone()[0]
    except sqlite3.Error:
        raise SourceRefused("unreadable") from None
    return counts


def _iter_markers(root):
    """(uid, source_id, path) for regular marker files directly under a uid box."""
    if not root.is_dir() or root.is_symlink():
        return
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_symlink() or not child.is_dir() or not _UID_RE.fullmatch(child.name):
            continue
        base = child / ".ta-cli"
        if base.is_symlink() or not base.is_dir():
            continue
        try:
            sources = list(base.iterdir())
        except OSError:
            continue
        for source in sources:
            if (source.is_symlink() or not source.is_dir()
                    or not _SOURCE_ID_RE.fullmatch(source.name)):
                continue
            path = source / "connection"
            if path.is_symlink() or not path.is_file():
                continue
            yield child.name, source.name, path


def _marker_count(root):
    return sum(1 for _item in _iter_markers(root))


def measure_root(root):
    ledger = root / ".broker" / "outbound.db"
    measured = _count_files(root)
    measured.update(_ledger_counts(ledger))
    measured["connection_markers"] = _marker_count(root)
    return measured


def layout_facts(root):
    marker = root / ".layout.json"
    facts = {
        "credential_roots_reset": False,
        "platform_recovery_present": (root / ".platform-recovery.json").is_file()
        and not (root / ".platform-recovery.json").is_symlink(),
        "layout_readable": False,
    }
    if not marker.is_file() or marker.is_symlink():
        return facts
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return facts
    facts["layout_readable"] = isinstance(document, dict)
    preparation = document.get("preparation") if isinstance(document, dict) else None
    facts["credential_roots_reset"] = (
        isinstance(preparation, dict) and preparation.get("credential_roots_reset") is True
    )
    return facts


def restore_complete_dates(text):
    return sorted(set(_RESTORE_LINE.findall(text or "")))


def _archive_date(name):
    match = re.search(r"(\d{4}-\d{2}-\d{2})T", name)
    return match.group(1) if match else None


def _wanted(parts):
    if not parts or ".." in parts:
        return None
    if parts == (".broker", "outbound.db"):
        return parts
    if (len(parts) == 5 and parts[:2] == (".broker", "accounts")
            and parts[3] == "credentials" and parts[4] in FILES.values()
            and _HASH_RE.fullmatch(parts[2])):
        return parts
    if (len(parts) == 4 and _UID_RE.fullmatch(parts[0]) and parts[1] == ".ta-cli"
            and _SOURCE_ID_RE.fullmatch(parts[2]) and parts[3] == "connection"):
        return parts
    return None


def extract_needed(archive, dest):
    """Copy only credential members. Rejects links and paths that escape."""
    try:
        handle = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError) as error:
        raise SourceRefused("unreadable") from error
    with handle:
        for member in handle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                continue
            parts = path.parts[1:] if path.parts[0] == "_data" else path.parts
            relative = _wanted(parts)
            if relative is None:
                continue
            if member.issym() or member.islnk() or not member.isfile():
                raise SourceRefused("unsafe_member")
            stream = handle.extractfile(member)
            if stream is None:
                continue
            data = stream.read()
            if relative[-1] != "outbound.db" and len(data) > 8 * 1024 * 1024:
                continue
            target = dest.joinpath(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return dest


def _digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise SourceRefused("digest_unknown")
    if _digest_file(path) != expected:
        raise SourceRefused("digest_mismatch")


def _source_record(kind, name, date, measured, *, digest_known, readable, error=None,
                   markers=0):
    record = {
        "kind": kind,
        "name": name,
        "date": date,
        "digest_known": digest_known,
        "readable": readable,
        "accounts_with_vault": measured.get("accounts_with_vault", 0),
        "accounts_with_subscription": measured.get("accounts_with_subscription", 0),
        "outbound_connections": measured.get("outbound_connections", 0),
        "outbound_connection_grants": measured.get("outbound_connection_grants", 0),
        "connection_capabilities": measured.get("connection_capabilities", 0),
        "browser_vault": measured.get("browser_vault", 0),
        "connection_markers": markers if markers else measured.get("connection_markers", 0),
    }
    if error:
        record["error"] = error
    return record


def _empty_measured():
    return {
        "accounts_with_vault": 0, "accounts_with_subscription": 0,
        **{table: 0 for table in TABLES}, "connection_markers": 0,
    }


def measure_archive(path, expected=None, *, require_digest=False):
    if expected:
        verify_digest(path, expected)
    elif require_digest:
        raise SourceRefused("digest_unknown")
    with tempfile.TemporaryDirectory(prefix="ta-cred-read-") as directory:
        root = extract_needed(path, Path(directory))
        return measure_root(root)


def score(record):
    if not record.get("readable"):
        return (-1, -1, "")
    files = record["accounts_with_vault"] + record["accounts_with_subscription"]
    rows = (record["outbound_connections"] + record["outbound_connection_grants"]
            + record["connection_capabilities"])
    return (files, rows, record.get("date") or "")


def select_source(records):
    """Highest credential count, then later date. A wiped later archive loses."""
    readable = [record for record in records if record.get("readable")]
    if not readable:
        return None
    return max(readable, key=score)["name"]


def _owner_hashes(owners):
    return {hashlib.sha256(owner.encode()).hexdigest(): owner for owner in owners if owner}


def _groups(root):
    ledger = root / ".broker" / "outbound.db"
    rows = {table: [] for table in TABLES}
    if ledger.is_file() and not ledger.is_symlink():
        with _open_ro(ledger) as db:
            for table in TABLES:
                rows[table] = _rows(db, table)
    grouped = {}

    def bucket(owner):
        return grouped.setdefault(owner, {
            "rows": {table: [] for table in TABLES}, "files": {}, "hash": None,
        })

    connection_owner = {}
    for row in rows["outbound_connections"]:
        owner = str(row.get("owner_user_id") or "")
        connection_owner[row.get("connection_id")] = owner
        bucket(owner)["rows"]["outbound_connections"].append(row)
    for table in ("outbound_connection_grants", "browser_vault"):
        column = OWNER_COLUMN[table]
        for row in rows[table]:
            bucket(str(row.get(column) or ""))["rows"][table].append(row)
    for row in rows["connection_capabilities"]:
        owner = connection_owner.get(row.get("connection_id"), "")
        bucket(owner)["rows"]["connection_capabilities"].append(row)
    hashes = _owner_hashes([key for key in grouped if key and not key.startswith("\0")])
    for digest, files in account_files(root).items():
        owner = hashes.get(digest)
        if owner is None:
            owner = "\0" + digest
        slot = bucket(owner)
        slot["files"] = files
        slot["hash"] = digest
    for owner, slot in grouped.items():
        if slot["hash"] is None and owner and not owner.startswith("\0"):
            slot["hash"] = hashlib.sha256(owner.encode()).hexdigest()
    return grouped


def _insert_missing(db, table, row):
    if not _table_exists(db, table):
        raise sqlite3.OperationalError("missing table")
    columns = _columns(db, table)
    keys = _primary_key(db, table)
    if not keys:
        raise sqlite3.OperationalError("missing primary key")
    present = {key: row[key] for key in columns if key in row}
    if any(key not in present for key in keys):
        return "skipped"
    where = " AND ".join(f"{_quote(key)}=?" for key in keys)
    db.row_factory = sqlite3.Row
    existing = db.execute(
        f"SELECT * FROM {_quote(table)} WHERE {where}",
        [present[key] for key in keys],
    ).fetchone()
    if existing is not None:
        live = {key: existing[key] for key in existing.keys()}
        if all(live.get(key) == value for key, value in present.items()):
            return "kept_live"
        return "conflict"
    if table in {"outbound_connection_grants", "connection_capabilities"}:
        if not _table_exists(db, "outbound_connections"):
            return "skipped"
        hit = db.execute(
            "SELECT 1 FROM outbound_connections WHERE connection_id=?",
            (present.get("connection_id"),),
        ).fetchone()
        if hit is None:
            return "skipped"
    names = list(present)
    db.execute(
        f"INSERT INTO {_quote(table)} ({','.join(map(_quote, names))}) "
        f"VALUES ({','.join('?' for _ in names)})",
        [present[name] for name in names],
    )
    return "insert"


def _copy_missing(live_root, digest, kind, source):
    if not digest or not _HASH_RE.fullmatch(digest):
        return "skipped", None
    if source.is_symlink() or not source.is_file():
        raise OSError("source credential is not a regular file")
    dest = live_root / ".broker" / "accounts" / digest / "credentials" / FILES[kind]
    if any(parent.is_symlink() for parent in dest.parents
           if parent != live_root and live_root in parent.parents):
        raise OSError("credential path is a symlink")
    if dest.is_symlink():
        raise OSError("credential path is a symlink")
    data = source.read_bytes()
    if dest.exists():
        if dest.is_file() and dest.read_bytes() == data:
            return "kept_live", None
        return "conflict", None
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".restore")
    try:
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        try:
            stat = dest.parent.stat()
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
        os.replace(temporary, dest)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return "insert", dest


def _add(total, part):
    for key, slot in part.items():
        for name in _SLOTS:
            total[key][name] += slot[name]


def merge(live_root, source_root):
    """Insert missing rows and files. Existing live rows and files stay."""
    ledger = live_root / ".broker" / "outbound.db"
    if not ledger.is_file() or ledger.is_symlink():
        raise SourceRefused("ledger_missing")
    grouped = _groups(source_root)
    files = {kind: _slot() for kind in FILES}
    tables = {table: _slot() for table in TABLES}
    affected = failed = unchanged = 0
    with sqlite3.connect(ledger) as db:
        db.execute("PRAGMA busy_timeout=5000")
        for spec in grouped.values():
            account_files_slot = {kind: _slot() for kind in FILES}
            account_tables = {table: _slot() for table in TABLES}
            db.execute("SAVEPOINT account")
            created = []
            try:
                for table in TABLES:
                    for row in spec["rows"][table]:
                        outcome = _insert_missing(db, table, row)
                        account_tables[table][outcome] += 1
                for kind, path in spec["files"].items():
                    outcome, written = _copy_missing(live_root, spec["hash"], kind, path)
                    account_files_slot[kind][outcome] += 1
                    if written is not None:
                        created.append(written)
                db.execute("RELEASE account")
            except (sqlite3.Error, OSError):
                db.execute("ROLLBACK TO account")
                db.execute("RELEASE account")
                for written in created:
                    try:
                        if written.is_file() and not written.is_symlink():
                            written.unlink()
                    except OSError:
                        pass
                failed += 1
                continue
            _add(files, account_files_slot)
            _add(tables, account_tables)
            inserted = any(slot["insert"] for slot in account_tables.values())
            inserted = inserted or any(slot["insert"] for slot in account_files_slot.values())
            if inserted:
                affected += 1
            else:
                unchanged += 1
    return {
        "accounts": len(grouped),
        "accounts_affected": affected,
        "accounts_failed": failed,
        "accounts_unchanged": unchanged,
        "files": files,
        "tables": tables,
    }


def _definitions(root):
    ledger = root / ".broker" / "outbound.db"
    if not ledger.is_file() or ledger.is_symlink():
        return []
    try:
        with _open_ro(ledger) as db:
            if not _table_exists(db, "box_model_sources"):
                return []
            return [row[0] for row in db.execute(
                "SELECT definition FROM box_model_sources")]
    except sqlite3.Error:
        return []


def _grant_live(db, owner, grant_id):
    if not grant_id or not owner:
        return False
    if not (_table_exists(db, "outbound_connection_grants")
            and _table_exists(db, "outbound_connections")):
        return False
    row = db.execute(
        "SELECT g.connection_id FROM outbound_connection_grants g "
        "JOIN outbound_connections c ON c.connection_id=g.connection_id "
        "WHERE g.grant_id=? AND g.owner_user_id=? AND c.owner_user_id=? "
        "AND g.revoked_at IS NULL AND c.revoked_at IS NULL",
        (grant_id, owner, owner),
    ).fetchone()
    if row is None or not _table_exists(db, "connection_capabilities"):
        return False
    return db.execute(
        "SELECT 1 FROM connection_capabilities WHERE connection_id=? "
        "AND capability_kind IN ('model_use', 'model_discovery')",
        (row[0],),
    ).fetchone() is not None


def _custody_present(root, owner):
    digest = hashlib.sha256(owner.encode()).hexdigest()
    path = (root / ".broker" / "accounts" / digest / "credentials"
            / FILES["subscription_state"])
    if path.is_symlink() or not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(document, dict) and any(
        isinstance(key, str) and _SOURCE_ID_RE.fullmatch(key) for key in document
    )


def _uids(root):
    ledger = root / ".broker" / "state" / "owner-identities.db"
    found = []
    if not ledger.is_file() or ledger.is_symlink():
        return found
    try:
        with _open_ro(ledger) as db:
            for query in (
                "SELECT uid, principal FROM owner_volumes",
                "SELECT machine, principal FROM box_records",
            ):
                try:
                    rows = db.execute(query).fetchall()
                except sqlite3.Error:
                    continue
                for uid, principal in rows:
                    if isinstance(uid, int) and isinstance(principal, str) and principal:
                        found.append((uid, principal))
                if found:
                    break
    except sqlite3.Error:
        return []
    return found


def _marker_ok(path):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8")[:4096])
    except (OSError, ValueError):
        return False
    return (isinstance(document, dict) and isinstance(document.get("source"), str)
            and isinstance(document.get("incarnation"), str) and document["incarnation"])


def verify(live_root, mounts=None, *, source_markers=0, markers_restored=0,
           predicted_uids=()):
    """Ledger-grant and marker/custody counts. No broker RPC and no secrets."""
    definitions = live = 0
    cli_owners = set()
    ledger = live_root / ".broker" / "outbound.db"
    db = _open_ro(ledger) if ledger.is_file() and not ledger.is_symlink() else None
    try:
        for raw in _definitions(live_root):
            try:
                document = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(document, dict):
                continue
            method = document.get("access_method")
            owner = document.get("owner_user_id")
            if not isinstance(owner, str):
                continue
            if method == "api_key_http":
                definitions += 1
                if db is not None and _grant_live(db, owner, document.get("ref")):
                    live += 1
            elif method == "subscription_cli":
                cli_owners.add(owner)
    finally:
        if db is not None:
            db.close()
    consistent = missing = unreadable = 0
    mounts = Path(mounts) if mounts else None
    seen = set()
    for uid, owner in _uids(live_root):
        if owner not in cli_owners and not _custody_present(live_root, owner):
            continue
        cli_owners.add(owner)
        seen.add(owner)
        custody = _custody_present(live_root, owner)
        mount = mounts / str(uid) if mounts else None
        if mount is None or not mount.is_dir() or mount.is_symlink():
            unreadable += 1
            continue
        ok = any(
            _marker_ok(path) for path in (mount / ".ta-cli").glob("*/connection")
            if not path.is_symlink()
        ) if (mount / ".ta-cli").is_dir() else False
        predicted = str(uid) in predicted_uids
        if custody and (ok or predicted):
            consistent += 1
        else:
            missing += 1
    for owner in cli_owners - seen:
        unreadable += 1
    separate = missing + unreadable
    return {
        "api_key_http_definitions": definitions,
        "api_key_http_grants_live": live,
        "cli_accounts": len(cli_owners),
        "cli_with_custody": sum(
            1 for owner in cli_owners if _custody_present(live_root, owner)
        ),
        "cli_marker_consistent": consistent,
        "cli_marker_missing": missing,
        "cli_marker_unreadable": unreadable,
        "cli_marker_separate_step": separate,
        "source_connection_markers": source_markers,
        "cli_markers_restored": markers_restored,
    }


def marker_plan(live_mounts, source_root):
    """uids and file count a marker copy would touch. Does not write or mount."""
    uids = set()
    count = 0
    if not live_mounts or not Path(live_mounts).is_dir():
        return count, uids
    for uid, source_id, path in _iter_markers(source_root):
        dest_root = Path(live_mounts) / uid
        if dest_root.is_symlink() or not dest_root.is_dir():
            continue
        dest = dest_root / ".ta-cli" / source_id / "connection"
        if dest.exists() or dest.is_symlink() or not _marker_ok(path):
            continue
        uids.add(uid)
        count += 1
    return count, uids


def restore_markers(live_mounts, source_root):
    """Copy a missing box marker when the source names its uid and the mount is up.

    Asleep images are not mounted here. Those accounts stay in the separate-step count.
    """
    restored = 0
    if not live_mounts or not Path(live_mounts).is_dir():
        return restored
    for uid, source_id, path in _iter_markers(source_root):
        dest_root = Path(live_mounts) / uid
        if dest_root.is_symlink() or not dest_root.is_dir():
            continue
        dest = dest_root / ".ta-cli" / source_id / "connection"
        if dest.exists() or dest.is_symlink() or not _marker_ok(path):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name("connection.restore")
        try:
            temporary.write_bytes(path.read_bytes())
            temporary.chmod(0o600)
            os.replace(temporary, dest)
        except OSError:
            temporary.unlink(missing_ok=True)
            continue
        restored += 1
    return restored


def take_rollback(live_root, dest):
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    ledger = live_root / ".broker" / "outbound.db"
    if ledger.is_symlink() or not ledger.is_file():
        raise SourceRefused("ledger_missing")
    with sqlite3.connect(ledger) as source, sqlite3.connect(dest / "outbound.db") as copy:
        source.backup(copy)
    with sqlite3.connect(dest / "outbound.db") as copy:
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SourceRefused("unreadable")
    for digest, files in account_files(live_root).items():
        for kind, path in files.items():
            target = dest / "accounts" / digest / FILES[kind]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            os.chmod(target, 0o600)
    return True


def broker(action, run=_run):
    code = "broker_stop_failed" if action == "stop" else "broker_start_failed"
    try:
        completed = run(["docker", action, BROKER], timeout=180)
    except SourceRefused:
        raise SourceRefused(code) from None
    except (OSError, subprocess.TimeoutExpired):
        raise SourceRefused(code) from None
    if getattr(completed, "returncode", 1) != 0:
        raise SourceRefused(code)
    return True


def _rollback_date(path, kept):
    if not kept:
        return None
    stamp = path.name.removeprefix("credential-restore-")
    return stamp[:10] if _DATE_RE.fullmatch(stamp[:10]) else None


def apply_tree(live_root, source_root, rollback_dir, *, mounts=None, run=_run):
    """Stop the broker, keep a verified copy, merge, start the broker again."""
    ledger = live_root / ".broker" / "outbound.db"
    if ledger.is_symlink() or not ledger.is_file():
        raise SourceRefused("ledger_missing")
    markers = _marker_count(source_root)
    broker("stop", run)
    plan = None
    restored = 0
    kept = False
    started = False
    merge_error = None
    try:
        kept = take_rollback(live_root, rollback_dir)
        plan = merge(live_root, source_root)
        restored = restore_markers(mounts, source_root)
    except SourceRefused as refused:
        merge_error = refused.code
    except OSError:
        merge_error = "unreadable"
    finally:
        try:
            broker("start", run)
            started = True
        except SourceRefused:
            started = False
    facts = {
        "rollback_kept": kept,
        "broker_stopped": True,
        "broker_started": started,
        "ledger_missing": False,
        "rollback_date": _rollback_date(rollback_dir, kept),
    }
    checked = verify(
        live_root, mounts, source_markers=markers, markers_restored=restored,
    )
    error = "broker_start_failed" if not started else merge_error
    return plan, facts, checked, error


def _live_summary(root, log_text, retained_count):
    summary = layout_facts(root)
    summary["restore_complete_dates"] = restore_complete_dates(log_text)
    summary["retained_dirs"] = retained_count
    try:
        measured = measure_root(root)
    except SourceRefused:
        measured = _count_files(root)
        measured.update({table: 0 for table in TABLES})
    summary["accounts"] = measured["accounts"]
    for key in (
        "accounts_with_vault", "accounts_with_subscription", *TABLES,
    ):
        summary[key] = measured[key]
    return summary


def build_report(*, mode, requested, live, sources, selected, plan=None,
                 apply_facts=None, verification=None, error=None):
    report = {
        "mode": mode,
        "source_requested": requested if requested == "auto" or _string_ok(requested) else "auto",
        "selected": selected,
        "catalog_readable": True,
        "live": live,
        "sources": sources,
    }
    if plan is not None:
        report["plan"] = plan
    if apply_facts is not None:
        report["apply"] = apply_facts
    if verification is not None:
        report["verification"] = verification
    if error:
        report["error"] = error
    return report


def main(argv=None):
    if os.geteuid() != 0:
        raise SystemExit("diagnostic refused: not_root")
    parser = argparse.ArgumentParser(description="restore broker credentials")
    parser.add_argument("--mode", default="inspect")
    parser.add_argument("--source", default="auto")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--mounts", type=Path, default=Path("/var/lib/boxhostd/mounts"))
    parser.add_argument("--backup-log", type=Path, default=Path("/var/log/tinyassets-backup.log"))
    parser.add_argument("--snapshot-dir", type=Path, default=Path("/var/backups/tinyassets"))
    args = parser.parse_args(argv)
    if args.mode not in {"inspect", "dry-run", "apply"}:
        raise SystemExit("diagnostic refused: bad_mode")
    if args.source != "auto" and not (
        ARCHIVE_RE.fullmatch(args.source) or RETAINED_RE.fullmatch(args.source)
    ):
        raise SystemExit("diagnostic refused: bad_source")
    root = args.root
    if root is None:
        completed = _run(
            ["docker", "volume", "inspect", "tinyassets-data", "--format", "{{.Mountpoint}}"],
            timeout=60,
        )
        if completed.returncode != 0:
            raise SystemExit("diagnostic refused: data_root")
        root = Path(completed.stdout.decode().strip())
    log_text = ""
    if args.backup_log.is_file() and not args.backup_log.is_symlink():
        try:
            log_text = args.backup_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
    retained = []
    parent = root.parent
    if parent.is_dir():
        for path in parent.glob(".tinyassets-restore-old.*"):
            if path.is_dir() and not path.is_symlink() and RETAINED_RE.fullmatch(path.name):
                retained.append(path)
    sources = []
    digests = {}
    for path in retained:
        try:
            measured = measure_root(path)
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
            sources.append(_source_record(
                "retained", path.name, stamp, measured, digest_known=False, readable=True,
            ))
        except (OSError, SourceRefused):
            sources.append(_source_record(
                "retained", path.name, None, _empty_measured(),
                digest_known=False, readable=False, error="unreadable",
            ))
    if args.snapshot_dir.is_dir():
        for path in sorted(args.snapshot_dir.glob("tinyassets-*.tar.gz")):
            if path.is_symlink() or not ARCHIVE_RE.fullmatch(path.name):
                continue
            sidecar = path.with_name(path.name + ".sha256")
            expected = None
            if sidecar.is_file() and not sidecar.is_symlink():
                token = sidecar.read_text(encoding="utf-8").split()
                if token and re.fullmatch(r"[a-f0-9]{64}", token[0]):
                    expected = token[0]
            try:
                measured = measure_archive(path, expected)
                if expected:
                    digests[path.name] = expected
                sources.append(_source_record(
                    "archive", path.name, _archive_date(path.name), measured,
                    digest_known=expected is not None, readable=True,
                ))
            except SourceRefused as error:
                sources.append(_source_record(
                    "archive", path.name, _archive_date(path.name), _empty_measured(),
                    digest_known=expected is not None, readable=False, error=error.code,
                ))
    destination = os.environ.get("BACKUP_DEST", "")
    catalog_ok = True
    if destination:
        try:
            listed = _run(["rclone", "lsf", "--files-only",
                           destination.rstrip("/") + "/platform"])
        except SourceRefused:
            listed = None
        if listed is None or listed.returncode != 0:
            catalog_ok = False
        else:
            for name in listed.stdout.decode().splitlines():
                if not re.fullmatch(
                    r"tinyassets-platform-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.json", name,
                ):
                    continue
                try:
                    document = _run(["rclone", "cat",
                                     destination.rstrip("/") + "/platform/" + name])
                except SourceRefused:
                    catalog_ok = False
                    continue
                if document.returncode != 0:
                    catalog_ok = False
                    continue
                try:
                    payload = json.loads(document.stdout.decode())
                except ValueError:
                    catalog_ok = False
                    continue
                archive = payload.get("archive")
                digest = payload.get("sha256")
                if not isinstance(archive, str) or not ARCHIVE_RE.fullmatch(archive):
                    continue
                known = (digest if isinstance(digest, str)
                         and re.fullmatch(r"[a-f0-9]{64}", digest) else None)
                if any(item["name"] == archive for item in sources):
                    if known:
                        digests[archive] = known
                        for item in sources:
                            if item["name"] != archive or item["digest_known"]:
                                continue
                            local_copy = args.snapshot_dir / archive
                            try:
                                if local_copy.is_file() and not local_copy.is_symlink():
                                    verify_digest(local_copy, known)
                                item["digest_known"] = True
                            except SourceRefused as refused:
                                item["readable"] = False
                                item["error"] = refused.code
                                item["digest_known"] = True
                                for key in (
                                    "accounts_with_vault", "accounts_with_subscription",
                                    *TABLES, "connection_markers",
                                ):
                                    item[key] = 0
                    continue
                try:
                    with tempfile.TemporaryDirectory(prefix="ta-cred-remote-") as directory:
                        local = Path(directory) / archive
                        copied = _run([
                            "rclone", "copyto", destination.rstrip("/") + "/" + archive, str(local),
                        ])
                        if copied.returncode != 0:
                            raise SourceRefused("unreadable")
                        measured = measure_archive(
                            local, known, require_digest=args.mode == "apply",
                        )
                    if known:
                        digests[archive] = known
                    sources.append(_source_record(
                        "archive", archive, _archive_date(archive), measured,
                        digest_known=known is not None, readable=True,
                    ))
                except SourceRefused as error:
                    sources.append(_source_record(
                        "archive", archive, _archive_date(archive), _empty_measured(),
                        digest_known=known is not None, readable=False, error=error.code,
                    ))
    selected_name = args.source
    if selected_name == "auto":
        selected_name = select_source(sources)
    chosen = next((item for item in sources if item["name"] == selected_name), None)
    live = _live_summary(root, log_text, len(retained))
    verification = verify(root, args.mounts, source_markers=(
        chosen["connection_markers"] if chosen else 0
    ))
    plan = None
    apply_facts = None
    error = None
    if args.mode in {"dry-run", "apply"}:
        if chosen is None or not chosen["readable"]:
            error = "source_missing"
        elif args.mode == "apply" and chosen["kind"] == "archive" and not chosen["digest_known"]:
            error = "digest_unknown"
        else:
            source_root, cleanup = _materialize(
                root, args.snapshot_dir, chosen, destination=destination, digests=digests,
            )
            try:
                if args.mode == "dry-run":
                    plan = _plan_only(root, source_root)
                    would, uids = marker_plan(args.mounts, source_root)
                    verification = verify(
                        root, args.mounts,
                        source_markers=chosen.get("connection_markers", 0),
                        markers_restored=would, predicted_uids=uids,
                    )
                else:
                    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
                    rollback = args.snapshot_dir / f"credential-restore-{stamp}"
                    plan, apply_facts, verification, apply_error = apply_tree(
                        root, source_root, rollback, mounts=args.mounts,
                    )
                    error = apply_error
            except (SourceRefused, OSError) as refused:
                error = refused.code if isinstance(refused, SourceRefused) else "unreadable"
            finally:
                cleanup()
    report = build_report(
        mode=args.mode, requested=args.source, live=live, sources=sources,
        selected=selected_name if isinstance(selected_name, str) and _string_ok(selected_name)
        else None,
        plan=plan, apply_facts=apply_facts, verification=verification, error=error,
    )
    report["catalog_readable"] = catalog_ok
    try:
        sys.stdout.write(safe_json(report) + "\n")
    except SourceRefused:
        raise SystemExit("diagnostic refused: output_contains_private_value")
    if error == "broker_start_failed":
        raise SystemExit(1)
    if error:
        raise SystemExit(2)
    return 0


def _plan_only(live_root, source_root):
    """Count the merge against a sqlite snapshot so the live file is unchanged."""
    with tempfile.TemporaryDirectory(prefix="ta-cred-plan-") as directory:
        staging = Path(directory) / "live"
        broker_dir = staging / ".broker"
        broker_dir.mkdir(parents=True)
        ledger = live_root / ".broker" / "outbound.db"
        if ledger.is_symlink() or not ledger.is_file():
            raise SourceRefused("ledger_missing")
        with _open_ro(ledger) as src, sqlite3.connect(broker_dir / "outbound.db") as dst:
            src.backup(dst)
        for digest, files in account_files(live_root).items():
            for kind, path in files.items():
                target = broker_dir / "accounts" / digest / "credentials" / FILES[kind]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        return merge(staging, source_root)


def _stage_archive(directory, snapshot_dir, chosen, destination, digests):
    local = snapshot_dir / chosen["name"]
    if local.is_symlink():
        raise SourceRefused("unreadable")
    expected = (digests or {}).get(chosen["name"])
    if not local.is_file():
        if not destination:
            raise SourceRefused("source_missing")
        local = Path(directory) / chosen["name"]
        copied = _run([
            "rclone", "copyto", "--contimeout", "60s", "--timeout", "900s",
            destination.rstrip("/") + "/" + chosen["name"], str(local),
        ])
        if copied.returncode != 0 or not local.is_file():
            raise SourceRefused("unreadable")
    elif expected is None:
        sidecar = local.with_name(local.name + ".sha256")
        if sidecar.is_file() and not sidecar.is_symlink():
            token = sidecar.read_text(encoding="utf-8").split()
            if token and re.fullmatch(r"[a-f0-9]{64}", token[0]):
                expected = token[0]
    if chosen["digest_known"]:
        if not expected:
            raise SourceRefused("digest_unknown")
        verify_digest(local, expected)
    return local


def _materialize(live_root, snapshot_dir, chosen, *, destination="", digests=None):
    if chosen["kind"] == "retained":
        path = live_root.parent / chosen["name"]
        if path.is_symlink() or not path.is_dir() or not RETAINED_RE.fullmatch(path.name):
            raise SourceRefused("unreadable")
        return path, lambda: None
    directory = tempfile.TemporaryDirectory(prefix="ta-cred-src-")
    try:
        local = _stage_archive(directory.name, snapshot_dir, chosen, destination, digests)
        root = extract_needed(local, Path(directory.name) / "tree")
    except Exception:
        directory.cleanup()
        raise
    return root, directory.cleanup


if __name__ == "__main__":
    main()
