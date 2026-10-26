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
# A refusal names one of these. They are counts-safe and carry no row data.
_STAGE_TOKENS = frozenset({
    "snapshot", "ledger", "definitions", "groups", "bind", "convert", "merge",
})
# A swallowed failure may name one of these. The message is never kept.
_EXCEPTION_TOKENS = frozenset({
    "OperationalError", "IntegrityError", "DatabaseError", "ProgrammingError",
    "DataError", "InterfaceError", "NotSupportedError", "Error", "Warning",
    "KeyError", "AttributeError", "RuntimeError", "TypeError", "ValueError",
    "OSError", "UnicodeError", "UnicodeEncodeError", "UnicodeDecodeError",
    "RecursionError", "LookupError", "IndexError", "OverflowError",
    "MemoryError", "JSONDecodeError", "ImportError", "ModuleNotFoundError",
    "error",
})
_FUNCTION_TOKENS = frozenset({
    "caller", "_convert_snapshot", "_keep_vault_backed", "_restrict_ledger",
    "delete_except", "_restrict_definitions", "_connection_refs",
    "_resolve_outcome", "_served_destinations", "_source_definition_rows",
    "_rewrite_source_definitions", "_verified_definition", "_definition_id",
    "_load_vault_records", "_write_private_json", "_open_source", "_open_ro",
    "legacy_center_owners", "convert_legacy_vaults", "_groups", "_rows",
    "_merge_snapshot", "merge", "_ensure_snapshot", "_copy_definitions",
    "_insert_missing", "_bind_rows", "_vault_resolver", "_record_destination",
    "_http_record_key", "_json_object", "prepare_legacy",
})
_FIXED_STRINGS = frozenset({
    "inspect", "inspect-legacy", "dry-run", "apply", "auto", "archive", "retained",
    "digest_mismatch", "unreadable", "unsafe_member", "digest_unknown",
    "ledger_missing", "source_missing", "broker_stop_failed",
    "broker_start_failed", "package_unavailable",
    *_STAGE_TOKENS, *_EXCEPTION_TOKENS, *_FUNCTION_TOKENS,
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
    "legacy", "platform_vault_files", "center_vault_files", "http",
    "llm_subscription", "other", "custody_db", "centers_one_owner",
    "centers_zero_owners", "centers_several_owners", "centers_ledger_one_owner",
    "http_kept",
    "unmatched", "revoked", "definitions", "secret_missing",
    "stage", "exception", "function",
})


# What a host-side run imports, including the modules those imports load.
# A clean interpreter with only these files resolves one http secret.
PACKAGE_FILES = (
    "tinyassets/__init__.py",
    "tinyassets/ttl_memo.py",
    "tinyassets/credential_vault.py",
    "tinyassets/owner_activity.py",
    "tinyassets/owner_runtime.py",
    "tinyassets/rpc_frames.py",
    "tinyassets/storage/__init__.py",
    "tinyassets/storage/outbound_connections.py",
    "tinyassets/storage/workspace_authority.py",
)


def _package_available():
    """True when the vault resolver can be imported. The message is not kept."""
    try:
        from tinyassets.credential_vault import load_credential_vault
        from tinyassets.owner_runtime import current
        from tinyassets.storage.outbound_connections import (
            _GeneralVaultCredentialResolver,
        )
    except ImportError:
        return False
    return all(callable(item) for item in (
        load_credential_vault, current, _GeneralVaultCredentialResolver,
    ))


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


_CLOSED_SOURCES = set()


def _source_key(path):
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _linked_inside(root, path):
    """True when ``path`` or a parent inside ``root`` is a symlink.

    The root itself may be reached through a symlink. A link inside the tree
    is not followed.
    """
    root = Path(root)
    path = Path(path)
    if path.is_symlink():
        return True
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _close_source(path):
    """Remember a source database the owner scan could not read."""
    _CLOSED_SOURCES.add(_source_key(path))


def _source_closed(path):
    return path is not None and _source_key(path) in _CLOSED_SOURCES


# An owner cell that will not decode. It is not the same as SQL NULL: NULL
# names nobody, and these bytes may name someone we cannot read.
_UNREADABLE = object()


def _decode_text(raw):
    """Text stays text. One undecodable cell is empty.

    Only for cells a caller skips: definitions, capabilities, and convert
    reads such as ``_keep_vault_backed``. Owner columns that choose a center's
    owner are read as blobs and decoded by ``_owner_identity`` instead. The
    default factory raises, and its message includes the cell bytes.
    """
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray)):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return None


def _owner_identity(raw):
    """Owner text from a blob. Undecodable bytes are not an absent owner."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8")
        except UnicodeError:
            return _UNREADABLE
    return _UNREADABLE


def _refuse_center(owners):
    """Two sentinels: the center matches neither one owner nor none."""
    owners.update(("\0unreadable-a", "\0unreadable-b"))


def _open_ro(path):
    if _source_closed(path):
        raise sqlite3.OperationalError("source closed")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.text_factory = _decode_text
    return db


def _open_source(path):
    """Writable open of a source database. A closed file is not opened."""
    if _source_closed(path):
        raise sqlite3.OperationalError("source closed")
    db = sqlite3.connect(path)
    db.text_factory = _decode_text
    return db


class _SourceSnapshot:
    """One private copy of the source ledger and custody database."""

    def __init__(self):
        self.failed = False
        self.stage = ""
        self.exception = ""
        self.function = ""
        self.holds = 0
        self.written = []
        self.skipped_owners = set()
        self._directory = tempfile.TemporaryDirectory(prefix="ta-cred-snap-")
        self.root = Path(self._directory.name)
        self.paths = {}

    def cleanup(self):
        self.paths.clear()
        self._directory.cleanup()


_SNAPSHOTS = {}


def _ensure_snapshot(source_root):
    """Open each source database once and copy it. Later steps read the copy."""
    key = _source_key(source_root)
    snap = _SNAPSHOTS.get(key)
    if snap is not None:
        return snap
    snap = _SourceSnapshot()
    _SNAPSHOTS[key] = snap
    origins = []
    try:
        for name in ("outbound.db", "credential-custody.db"):
            origin = Path(source_root) / ".broker" / name
            origins.append(origin)
            if _linked_inside(source_root, origin):
                raise OSError("source path is a symlink")
            if not origin.is_file():
                continue
            dest = snap.root / name
            with _open_ro(origin) as src, sqlite3.connect(dest) as dst:
                src.backup(dst)
            snap.paths[name] = dest
    except Exception as exc:
        snap.failed = True
        snap.stage = "snapshot"
        _note_failure(snap, exc)
        snap.paths.clear()
        for origin in origins:
            if _linked_inside(source_root, origin):
                continue
            if origin.is_file() and not origin.is_symlink():
                _close_source(origin)
    return snap


def _acquire_snapshot(source_root):
    snap = _ensure_snapshot(source_root)
    snap.holds += 1
    return snap


def _release_snapshot(source_root):
    key = _source_key(source_root)
    snap = _SNAPSHOTS.get(key)
    if snap is None:
        return
    snap.holds -= 1
    if snap.holds <= 0:
        _SNAPSHOTS.pop(key, None)
        snap.cleanup()


def _active_db(source_root, name):
    """The private copy, when this restore already snapped the source."""
    snap = _SNAPSHOTS.get(_source_key(source_root))
    if snap is None:
        origin = Path(source_root) / ".broker" / name
        if (_linked_inside(source_root, origin) or _source_closed(origin)
                or origin.is_symlink() or not origin.is_file()):
            return None
        return origin
    if snap.failed:
        return None
    return snap.paths.get(name)


def _legacy_stat_template():
    return {
        "http_kept": 0,
        "llm_subscription": 0,
        "unmatched": 0,
        "revoked": 0,
        "other": 0,
        "centers_one_owner": 0,
        "centers_zero_owners": 0,
        "centers_several_owners": 0,
        "centers_ledger_one_owner": 0,
        "accounts_failed": 0,
        "definitions": 0,
        "secret_missing": 0,
    }


def _refused_center_stats(source_root):
    """Every vault center refused. No secret and no row is written."""
    stats = _legacy_stat_template()
    platform, local = _legacy_vault_paths(source_root)
    stats["centers_several_owners"] = len(set(platform) | set(local))
    return stats


def _remember_stage(source_root, stage):
    snap = _SNAPSHOTS.get(_source_key(source_root))
    if snap is not None and stage in _STAGE_TOKENS and snap.stage not in _STAGE_TOKENS:
        snap.stage = stage


def _failure_tokens(exc):
    """Class and function from fixed lists. The message is never kept."""
    kind = "error"
    for base in type(exc).__mro__:
        name = base.__name__
        if name in _EXCEPTION_TOKENS and name != "error":
            kind = name
            break
    function = "caller"
    frame = exc.__traceback__
    while frame is not None:
        name = frame.tb_frame.f_code.co_name
        if name in _FUNCTION_TOKENS and name != "caller":
            function = name
        frame = frame.tb_next
    return kind, function


def _note_failure(snap, exc):
    if snap is None or snap.exception in _EXCEPTION_TOKENS:
        return
    snap.exception, snap.function = _failure_tokens(exc)


def _refusal_stage(source_root):
    snap = _SNAPSHOTS.get(_source_key(source_root))
    stage = snap.stage if snap is not None else ""
    return stage if stage in _STAGE_TOKENS else "merge"


def _refused_legacy(source_root):
    """The all-centers refusal, plus the fixed token for the step that failed."""
    legacy = _refused_center_stats(source_root)
    legacy["stage"] = _refusal_stage(source_root)
    snap = _SNAPSHOTS.get(_source_key(source_root))
    kind = snap.exception if snap is not None else ""
    function = snap.function if snap is not None else ""
    if kind in _EXCEPTION_TOKENS and function in _FUNCTION_TOKENS:
        legacy["exception"] = kind
        legacy["function"] = function
    return legacy


def _unlink_written(snap):
    for path in snap.written:
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass
    snap.written.clear()


def _refusal_plan(source_root):
    """Counts only. ``error`` is how apply and the CLI learn the read failed."""
    legacy = _refused_legacy(source_root)
    return {
        "accounts": 0,
        "accounts_affected": 0,
        "accounts_failed": 0,
        "accounts_unchanged": 0,
        "files": {kind: _slot() for kind in FILES},
        "tables": {table: _slot() for table in TABLES},
        "legacy": legacy,
        "error": "unreadable",
    }


def account_files(root):
    """hash -> {kind: Path}. Skips symlinks and non-hash directory names."""
    found = {}
    accounts = root / ".broker" / "accounts"
    if (_linked_inside(root, accounts) or not accounts.is_dir()
            or accounts.is_symlink()):
        return found
    for directory in accounts.iterdir():
        if (_linked_inside(root, directory) or directory.is_symlink()
                or not directory.is_dir()):
            continue
        if not _HASH_RE.fullmatch(directory.name):
            continue
        slot = {}
        for kind, filename in FILES.items():
            path = directory / "credentials" / filename
            if _linked_inside(root, path) or path.is_symlink() or not path.is_file():
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
    root = path.parent.parent
    if _linked_inside(root, path):
        raise SourceRefused("unreadable")
    if _source_closed(path) or not path.is_file() or path.is_symlink():
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
    custody = root / ".broker" / "credential-custody.db"
    if _linked_inside(root, custody):
        raise SourceRefused("unreadable")
    _refuse_wanted_symlink(root)
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


_CENTER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HTTP_PREFIX = "vault://http/"
_DEFINITION_FIELDS = (
    "id", "universe_id", "owner_user_id", "access_method", "protocol",
    "model", "ref", "visibility", "created_at",
)


def _center_from_parts(parts):
    """('platform'|'center', name) for an old vault file, else None."""
    if (len(parts) == 4 and parts[:2] == (".platform", "credential-vaults")
            and parts[3] == ".credential-vault.json"
            and _CENTER_RE.fullmatch(parts[2])):
        return "platform", parts[2]
    if (len(parts) == 2 and parts[1] == ".credential-vault.json"
            and _CENTER_RE.fullmatch(parts[0])):
        return "center", parts[0]
    return None


def _wanted(parts):
    if not parts or ".." in parts:
        return None
    if parts in {(".broker", "outbound.db"), (".broker", "credential-custody.db")}:
        return parts
    if (len(parts) == 5 and parts[:2] == (".broker", "accounts")
            and parts[3] == "credentials" and parts[4] in FILES.values()
            and _HASH_RE.fullmatch(parts[2])):
        return parts
    if (len(parts) == 4 and _UID_RE.fullmatch(parts[0]) and parts[1] == ".ta-cli"
            and _SOURCE_ID_RE.fullmatch(parts[2]) and parts[3] == "connection"):
        return parts
    if _center_from_parts(parts):
        return parts
    if (len(parts) == 2 and parts[1] == "provider_definitions.json"
            and _CENTER_RE.fullmatch(parts[0])):
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


def _legacy_counts():
    return {
        "platform_vault_files": 0,
        "center_vault_files": 0,
        "http": 0,
        "llm_subscription": 0,
        "other": 0,
        "custody_db": False,
        "centers_one_owner": 0,
        "centers_zero_owners": 0,
        "centers_several_owners": 0,
        "centers_ledger_one_owner": 0,
    }


def _vault_facts(data):
    """Kind counts plus http destinations. Destinations are not reported."""
    http = subscription = other = 0
    dests = set()
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError):
        return http, subscription, other, dests
    records = document.get("credentials") if isinstance(document, dict) else None
    if not isinstance(records, list):
        return http, subscription, other, dests
    for record in records:
        if not isinstance(record, dict):
            other += 1
            continue
        kind = record.get("credential_type")
        if kind == "http":
            http += 1
        elif kind == "llm_subscription":
            subscription += 1
        else:
            other += 1
        if isinstance(kind, str) and kind.strip() == "http":
            key = _record_destination(record)
            if key:
                dests.add(key)
    return http, subscription, other, dests


def _custody_refused(centers):
    return {center: {"\0custody-a", "\0custody-b"} for center in centers}


def _owner_groups(db_path, centers):
    """center -> owners, plus whether the file could not be read.

    A missing file is empty and not a failure. Any failed read refuses every
    center and reports the failure so the caller writes nothing.
    """
    groups = {}
    path = Path(db_path) if db_path is not None else None
    if path is None or path.is_symlink() or not path.is_file():
        return groups, False
    if _source_closed(path):
        return _custody_refused(centers), True
    try:
        with _open_ro(path) as db:
            if not _table_exists(db, "llm_credential_deposit_owners"):
                return groups, False
            columns = set(_columns(db, "llm_credential_deposit_owners"))
            center_col = next(
                (name for name in ("universe_id", "center", "center_id") if name in columns),
                None,
            )
            owner_col = next(
                (name for name in ("owner_user_id", "owner", "principal", "owner_id")
                 if name in columns),
                None,
            )
            if center_col is None or owner_col is None:
                return groups, False
            query = (
                f"SELECT {_quote(center_col)}, CAST({_quote(owner_col)} AS BLOB) "
                f"FROM {_quote('llm_credential_deposit_owners')}"
            )
            for center, owner_raw in db.execute(query):
                if not isinstance(center, str) or not _CENTER_RE.fullmatch(center):
                    continue
                slot = groups.setdefault(center, set())
                owner = _owner_identity(owner_raw)
                # A second owner we cannot read must not leave the readable one.
                if owner is _UNREADABLE:
                    _refuse_center(slot)
                    continue
                if isinstance(owner, str) and owner and not owner.startswith("\0"):
                    slot.add(owner)
    except Exception:
        _close_source(path)
        return _custody_refused(centers), True
    return groups, False


def legacy_center_owners(db_path, centers, ledger_path=None, center_dests=None,
                         definition_root=None, definition_docs=None):
    """Map each vault center to its single owner. Zero or several are refused.

    Custody rows win when they are the only evidence. When they are absent,
    a center maps only if its directory name is the grant ``universe_id`` and
    that grant's ``vault://http/`` destination is one of the vault's http
    destinations, and every such row names one owner. A provider definition
    counts only on that same pair. Disagreement refuses the center. A custody
    or ledger file that exists but cannot be read refuses every center.
    """
    groups, custody_failed = _owner_groups(db_path, centers)
    ledger_groups, evidence, ledger_failed = _ledger_owner_groups(
        ledger_path, center_dests or {}, definition_root, definition_docs,
    )
    if custody_failed or ledger_failed:
        named = set(centers)
        return {}, {
            "centers_one_owner": 0,
            "centers_zero_owners": 0,
            "centers_several_owners": len(named),
            "centers_ledger_one_owner": 0,
        }, {center: set() for center in named}, True
    mapping = {}
    one = zero = several = ledger_one = 0
    for center in set(centers):
        custody_owners = groups.get(center, set())
        ledger_owners = ledger_groups.get(center, set())
        owners = custody_owners | ledger_owners
        if len(ledger_owners) == 1 and owners == ledger_owners:
            ledger_one += 1
        if len(owners) == 1:
            one += 1
            mapping[center] = next(iter(owners))
        elif not owners:
            zero += 1
        else:
            several += 1
    return mapping, {
        "centers_one_owner": one,
        "centers_zero_owners": zero,
        "centers_several_owners": several,
        "centers_ledger_one_owner": ledger_one,
    }, evidence, ledger_failed


def _member_parts(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    return path.parts[1:] if path.parts[0] == "_data" else path.parts


def inspect_legacy(archive):
    """Count old vault files and owner mappings. The report has no identifiers."""
    counts = _legacy_counts()
    centers = set()
    center_dests = {}
    custody = None
    ledger = None
    definition_docs = []
    try:
        handle = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError):
        raise SourceRefused("unreadable") from None
    with handle:
        for member in handle.getmembers():
            parts = _member_parts(member.name)
            if parts is None or member.issym() or member.islnk() or not member.isfile():
                continue
            if parts == (".broker", "credential-custody.db"):
                counts["custody_db"] = True
                stream = handle.extractfile(member)
                custody = b"" if stream is None else stream.read()
                continue
            if parts == (".broker", "outbound.db"):
                stream = handle.extractfile(member)
                ledger = b"" if stream is None else stream.read()
                continue
            if (len(parts) == 2 and parts[1] == "provider_definitions.json"
                    and _CENTER_RE.fullmatch(parts[0])):
                if member.size > 8 * 1024 * 1024:
                    continue
                stream = handle.extractfile(member)
                if stream is None:
                    continue
                definition_docs.extend(
                    (parts[0], item) for item in _definition_list(stream.read())
                )
                continue
            located = _center_from_parts(parts)
            if located is None:
                continue
            kind, center = located
            centers.add(center)
            counts["platform_vault_files" if kind == "platform" else "center_vault_files"] += 1
            slot = center_dests.setdefault(center, set())
            if member.size > 8 * 1024 * 1024:
                continue
            stream = handle.extractfile(member)
            if stream is None:
                continue
            http, subscription, other, dests = _vault_facts(stream.read())
            counts["http"] += http
            counts["llm_subscription"] += subscription
            counts["other"] += other
            slot.update(dests)
    with tempfile.TemporaryDirectory(prefix="ta-cred-legacy-") as directory:
        custody_path = None
        ledger_path = None
        if custody is not None:
            custody_path = Path(directory) / "credential-custody.db"
            custody_path.write_bytes(custody)
        if ledger is not None:
            ledger_path = Path(directory) / "outbound.db"
            ledger_path.write_bytes(ledger)
        _mapping, owner_counts, _evidence, _ledger_failed = legacy_center_owners(
            custody_path, centers, ledger_path=ledger_path,
            center_dests=center_dests, definition_docs=definition_docs,
        )
    counts.update(owner_counts)
    return counts


def inspect_legacy_tree(root):
    """Same counts as :func:`inspect_legacy`, for an already extracted tree."""
    counts = _legacy_counts()
    centers = set()
    center_dests = {}
    root = Path(root)
    custody = root / ".broker" / "credential-custody.db"
    ledger = root / ".broker" / "outbound.db"
    if _linked_inside(root, custody) or _linked_inside(root, ledger):
        raise SourceRefused("unreadable")
    if custody.is_file() and not custody.is_symlink():
        counts["custody_db"] = True
    if root.is_dir() and not root.is_symlink():
        for path in root.rglob(".credential-vault.json"):
            if path.is_symlink() or _linked_inside(root, path):
                continue
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            located = _center_from_parts(parts)
            if located is None:
                continue
            kind, center = located
            centers.add(center)
            slot = center_dests.setdefault(center, set())
            counts["platform_vault_files" if kind == "platform" else "center_vault_files"] += 1
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    continue
                http, subscription, other, dests = _vault_facts(path.read_bytes())
            except OSError:
                continue
            counts["http"] += http
            counts["llm_subscription"] += subscription
            counts["other"] += other
            slot.update(dests)
    _mapping, owner_counts, _evidence, _ledger_failed = legacy_center_owners(
        custody if counts["custody_db"] else None, centers,
        ledger_path=(
            ledger if ledger.is_file() and not ledger.is_symlink()
            and not _linked_inside(root, ledger) else None
        ),
        center_dests=center_dests, definition_root=root,
    )
    counts.update(owner_counts)
    return counts


def _definition_id(document):
    parts = tuple(document.get(key) for key in (
        "universe_id", "owner_user_id", "access_method", "protocol", "ref", "model",
    ))
    if any(not isinstance(part, str) for part in parts):
        return None
    try:
        material = "\0".join(f"{len(part)}:{part}" for part in parts).encode()
    except UnicodeError:
        return None
    return "provdef_" + hashlib.sha256(material).hexdigest()[:32]


def _load_vault_records(path):
    if path.is_symlink() or any(
        parent.is_symlink() for parent in path.parents if parent != path.anchor
    ) or not path.is_file():
        raise OSError("vault is not a regular file")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise OSError("vault is too large")
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("credentials") if isinstance(document, dict) else None
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("vault")
    return records


def _connection_refs(db, owner):
    live, revoked = set(), set()
    if db is None or not _table_exists(db, "outbound_connections"):
        return live, revoked
    columns = set(_columns(db, "outbound_connections"))
    if not {"connection_id", "credential_ref", "owner_user_id"} <= columns:
        return live, revoked
    revoked_expr = "revoked_at" if "revoked_at" in columns else "NULL"
    query = (
        f"SELECT connection_id, credential_ref, {revoked_expr} "
        f"FROM {_quote('outbound_connections')} WHERE owner_user_id=?"
    )
    for connection_id, ref, revoked_at in db.execute(query, (owner,)):
        dest = _http_record_key(ref)
        if dest is None or not isinstance(connection_id, str):
            continue
        if revoked_at:
            revoked.add((connection_id, dest))
        else:
            live.add((connection_id, dest))
    return live, revoked


def _write_private_json(path, payload):
    if path.is_symlink() or any(
        parent.is_symlink() for parent in path.parents
        if parent != path.anchor
    ):
        raise OSError("credential path is a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".legacy")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.chmod(0o600)
        try:
            parent_stat = path.parent.stat()
            os.chown(temporary, parent_stat.st_uid, parent_stat.st_gid)
        except PermissionError:
            pass
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _legacy_vault_paths(source_root):
    platform, local = {}, {}
    vault_root = source_root / ".platform" / "credential-vaults"
    if (not _linked_inside(source_root, vault_root) and vault_root.is_dir()
            and not vault_root.is_symlink()):
        for directory in vault_root.iterdir():
            if (directory.is_symlink() or not directory.is_dir()
                    or not _CENTER_RE.fullmatch(directory.name)):
                continue
            path = directory / ".credential-vault.json"
            if (_linked_inside(source_root, path) or path.is_symlink()
                    or not path.is_file()):
                continue
            platform[directory.name] = path
    if source_root.is_dir() and not source_root.is_symlink():
        for child in source_root.iterdir():
            if child.is_symlink() or not child.is_dir() or not _CENTER_RE.fullmatch(child.name):
                continue
            path = child / ".credential-vault.json"
            if (_linked_inside(source_root, path) or path.is_symlink()
                    or not path.is_file()):
                continue
            local[child.name] = path
    return platform, local


def _center_http_dests(platform, local):
    """Http destinations in each center's vault files. Not reported."""
    dests = {}
    for center in set(platform) | set(local):
        found = set()
        for path in (platform.get(center), local.get(center)):
            if path is None:
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                    continue
                _http, _sub, _other, keys = _vault_facts(path.read_bytes())
            except OSError:
                continue
            found.update(keys)
        dests[center] = found
    return dests


def _http_record_key(ref):
    """Vault key the general resolver looks up for this credential ref.

    Same strip and prefix as that resolver, before it reads a record.
    ``None`` when the call would raise before comparing destinations.
    """
    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if not text.startswith(_HTTP_PREFIX):
        return None
    key = text[len(_HTTP_PREFIX):].strip()
    return key or None


def _record_destination(record):
    """Destination text the resolver compares. It does not read ``service``."""
    if not isinstance(record, dict):
        return ""
    return str(record.get("destination") or "").strip()


def _definition_list(data):
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError):
        return []
    if not isinstance(document, list):
        return []
    return [item for item in document if isinstance(item, dict)]


def _definition_files(root, center_dests):
    """(center, document) from ``<center>/provider_definitions.json``."""
    found = []
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return found
    for center in center_dests:
        path = root / center / "provider_definitions.json"
        if _linked_inside(root, path) or path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        found.extend((center, item) for item in _definition_list(data))
    return found


def _confirmed_definition_owner(db, center, document, dests):
    """Owner a definition names only when its grant matches this center's vault."""
    if not isinstance(document, dict) or document.get("universe_id") != center:
        return None
    owner = document.get("owner_user_id")
    ref = document.get("ref")
    if not isinstance(owner, str) or not owner or not isinstance(ref, str) or not ref:
        return None
    if not (_table_exists(db, "outbound_connection_grants")
            and _table_exists(db, "outbound_connections")):
        return None
    grant_cols = set(_columns(db, "outbound_connection_grants"))
    conn_cols = set(_columns(db, "outbound_connections"))
    if not {"grant_id", "owner_user_id", "connection_id"} <= grant_cols:
        return None
    if not {"connection_id", "owner_user_id", "credential_ref"} <= conn_cols:
        return None
    universe_sql = "g.universe_id" if "universe_id" in grant_cols else "NULL"
    dest_sql = "c.destination" if "destination" in conn_cols else "NULL"
    rows = list(db.execute(
        "SELECT CAST(g.owner_user_id AS BLOB), CAST(c.owner_user_id AS BLOB), "
        "c.credential_ref, "
        f"{universe_sql}, {dest_sql} "
        "FROM outbound_connection_grants g "
        "JOIN outbound_connections c ON c.connection_id = g.connection_id "
        "WHERE g.grant_id=?",
        (ref,),
    ))
    if len(rows) != 1:
        return None
    grant_owner, conn_owner, ref_value, grant_universe, stored_dest = rows[0]
    grant_owner = _owner_identity(grant_owner)
    conn_owner = _owner_identity(conn_owner)
    if grant_owner is _UNREADABLE or conn_owner is _UNREADABLE:
        return _UNREADABLE
    if grant_owner != owner or conn_owner != owner:
        return None
    if "universe_id" in grant_cols and grant_universe != center:
        return None
    dest = _http_record_key(ref_value)
    if dest is None or dest not in dests:
        return None
    if stored_dest is not None and not isinstance(stored_dest, str):
        return None
    if isinstance(stored_dest, str) and stored_dest.strip() and stored_dest.strip() != dest:
        return None
    return owner, dest


def _add_grant_owners(db, groups, center_dests, evidence):
    if not (_table_exists(db, "outbound_connection_grants")
            and _table_exists(db, "outbound_connections")):
        return
    grant_cols = set(_columns(db, "outbound_connection_grants"))
    conn_cols = set(_columns(db, "outbound_connections"))
    if not {"universe_id", "owner_user_id", "connection_id"} <= grant_cols:
        return
    if not {"owner_user_id", "connection_id", "credential_ref"} <= conn_cols:
        return
    dest_sql = "c.destination" if "destination" in conn_cols else "NULL"
    query = (
        "SELECT g.universe_id, CAST(g.owner_user_id AS BLOB), "
        "CAST(c.owner_user_id AS BLOB), "
        f"c.credential_ref, {dest_sql} "
        "FROM outbound_connection_grants g "
        "JOIN outbound_connections c ON c.connection_id = g.connection_id"
    )
    for universe_id, grant_raw, conn_raw, ref, stored_dest in db.execute(query):
        if not isinstance(universe_id, str) or universe_id not in center_dests:
            continue
        grant_owner = _owner_identity(grant_raw)
        conn_owner = _owner_identity(conn_raw)
        if grant_owner is _UNREADABLE or conn_owner is _UNREADABLE:
            _refuse_center(groups[universe_id])
            continue
        if (not isinstance(grant_owner, str) or not grant_owner
                or grant_owner != conn_owner):
            continue
        dest = _http_record_key(ref)
        if dest is None or dest not in center_dests[universe_id]:
            continue
        if stored_dest is not None and not isinstance(stored_dest, str):
            continue
        if isinstance(stored_dest, str) and stored_dest.strip() and stored_dest.strip() != dest:
            continue
        groups[universe_id].add(grant_owner)
        evidence[universe_id].add(dest)


def _add_definition_owners(db, groups, center_dests, docs, evidence):
    for center, document in docs:
        if not isinstance(center, str) or center not in groups:
            continue
        confirmed = _confirmed_definition_owner(
            db, center, document, center_dests.get(center, set()),
        )
        if confirmed is _UNREADABLE:
            _refuse_center(groups[center])
            continue
        if confirmed is not None:
            owner, dest = confirmed
            groups[center].add(owner)
            evidence[center].add(dest)
    if not _table_exists(db, "box_model_sources"):
        return
    columns = set(_columns(db, "box_model_sources"))
    if not {"owner", "definition"} <= columns:
        return
    for row_raw, raw in db.execute(
            "SELECT CAST(owner AS BLOB), definition FROM box_model_sources"):
        try:
            document = json.loads(raw) if isinstance(raw, str) else None
        except (ValueError, UnicodeError):
            continue
        if not isinstance(document, dict):
            continue
        center = document.get("universe_id")
        if not isinstance(center, str) or center not in groups:
            continue
        row_owner = _owner_identity(row_raw)
        if row_owner is _UNREADABLE:
            _refuse_center(groups[center])
            continue
        doc_owner = document.get("owner_user_id")
        if (isinstance(row_owner, str) and isinstance(doc_owner, str)
                and row_owner and doc_owner and row_owner != doc_owner):
            groups[center].update((row_owner, doc_owner))
            continue
        confirmed = _confirmed_definition_owner(
            db, center, document, center_dests.get(center, set()),
        )
        if confirmed is _UNREADABLE:
            _refuse_center(groups[center])
            continue
        if confirmed is not None:
            owner, dest = confirmed
            groups[center].add(owner)
            evidence[center].add(dest)


def _refused_ledger(groups):
    """Two owners on every center and no destinations. The file is unreadable."""
    for owners in groups.values():
        owners.update(("\0ledger-a", "\0ledger-b"))
    return groups, {center: set() for center in groups}, True


def _ledger_owner_groups(ledger_path, center_dests, definition_root, definition_docs):
    """Owners and the destinations that named them. A failed read refuses all."""
    groups = {center: set() for center in center_dests}
    evidence = {center: set() for center in center_dests}
    path = Path(ledger_path) if ledger_path is not None else None
    if path is None or path.is_symlink() or not path.is_file():
        return groups, evidence, False
    if _source_closed(path):
        return _refused_ledger(groups)
    try:
        db = _open_ro(path)
    except Exception:
        _close_source(path)
        return _refused_ledger(groups)
    try:
        _add_grant_owners(db, groups, center_dests, evidence)
        docs = list(definition_docs or [])
        if definition_root is not None:
            docs.extend(_definition_files(definition_root, center_dests))
        _add_definition_owners(db, groups, center_dests, docs, evidence)
    except Exception:
        _close_source(path)
        return _refused_ledger(groups)
    finally:
        db.close()
    return groups, evidence, False


def _vault_resolver(namespace):
    """The general vault resolver aimed at one credential namespace."""
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    keyword = "universe_" + "dir"
    return _GeneralVaultCredentialResolver(**{keyword: namespace})


def _resolve_outcome(records, dest):
    """What the general vault resolver does for ``vault://http/<dest>``.

    ``secret`` when it returns a non-empty secret, ``missing`` when the
    destination matches and it raises, ``absent`` when it does not match.
    ``service`` is not a destination. The resolver is the only matcher.
    """
    key = dest.strip() if isinstance(dest, str) else ""
    if not key or not isinstance(records, list):
        return "absent"
    namespace_dir = tempfile.TemporaryDirectory(prefix="ta-cred-resolve-")
    try:
        namespace = Path(namespace_dir.name)
        vault = namespace / "credentials" / ".credential-vault.json"
        vault.parent.mkdir(parents=True)
        vault.write_text(
            json.dumps({"schema_version": 1, "credentials": records}),
            encoding="utf-8",
        )
        try:
            value = _vault_resolver(namespace)(_HTTP_PREFIX + key)
        except RuntimeError as exc:
            if str(exc) == "credential reference is unavailable":
                return "missing"
            return "absent"
    except (OSError, ValueError, TypeError, PermissionError):
        return "absent"
    finally:
        namespace_dir.cleanup()
    if isinstance(value, str) and value.strip():
        return "secret"
    return "absent"


def _served_destinations(path):
    """Destinations for which the real resolver returns a non-empty secret."""
    if path is None or path.is_symlink() or not path.is_file():
        return set()
    from tinyassets.credential_vault import load_credential_vault
    try:
        records = load_credential_vault(path.parent.parent)
    except (OSError, ValueError, RuntimeError, PermissionError):
        return set()
    keys = []
    for record in records:
        key = _record_destination(record)
        if key and key not in keys:
            keys.append(key)
    resolver = _vault_resolver(path.parent.parent)
    served = set()
    for key in keys:
        try:
            value = resolver(_HTTP_PREFIX + key)
        except RuntimeError:
            continue
        if isinstance(value, str) and value.strip():
            served.add(key)
    return served


def _keep_vault_backed(db, source_root, kept_ids, skipped=()):
    """Keep a connection when its destination is already in that owner's vault."""
    if db is None or not _table_exists(db, "outbound_connections"):
        return
    if "owner_user_id" not in set(_columns(db, "outbound_connections")):
        return
    skipped = set(skipped)
    owners = [
        row[0] for row in db.execute(
            "SELECT DISTINCT owner_user_id FROM outbound_connections"
        )
        if isinstance(row[0], str) and row[0] and row[0] not in skipped
    ]
    for owner in owners:
        digest = hashlib.sha256(owner.encode()).hexdigest()
        path = (
            source_root / ".broker" / "accounts" / digest / "credentials"
            / ".credential-vault.json"
        )
        if (_linked_inside(source_root, path) or path.is_symlink()
                or not path.is_file()):
            continue
        have = _served_destinations(path)
        live_refs, _revoked = _connection_refs(db, owner)
        for connection_id, dest in live_refs:
            if dest in have:
                kept_ids.add(connection_id)


def _restrict_ledger(source_root, kept_ids):
    ledger = _active_db(source_root, "outbound.db")
    if ledger is None or _source_closed(ledger):
        return
    ids = list(kept_ids)
    with _open_source(ledger) as db:
        def delete_except(table, extra=""):
            if not _table_exists(db, table):
                return
            # An older capabilities table has no connection_id. Leaving the
            # rows is safe: the binder drops any row it cannot tie to one.
            if "connection_id" not in set(_columns(db, table)):
                return
            if not ids:
                db.execute(f"DELETE FROM {_quote(table)}")
                return
            marks = ",".join("?" for _ in ids)
            where = f"connection_id NOT IN ({marks})"
            if extra:
                where = f"({extra}) OR {where}"
            db.execute(f"DELETE FROM {_quote(table)} WHERE {where}", ids)

        delete_except("connection_capabilities")
        grant_extra = ""
        if (_table_exists(db, "outbound_connection_grants")
                and "revoked_at" in set(_columns(db, "outbound_connection_grants"))):
            grant_extra = "revoked_at IS NOT NULL"
        delete_except("outbound_connection_grants", grant_extra)
        delete_except("outbound_connections")


def _verified_definition(owner, definition_id, raw, grants):
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    if not set(_DEFINITION_FIELDS) <= set(document):
        return None
    document = {key: document[key] for key in _DEFINITION_FIELDS}
    ref = document.get("ref")
    if (document.get("access_method") != "api_key_http"
            or document.get("owner_user_id") != owner
            or document.get("id") != definition_id
            or not isinstance(ref, str)
            or ref not in grants):
        return None
    if _definition_id(document) != definition_id:
        return None
    return document


def _json_object(raw):
    try:
        document = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        return None
    return document if isinstance(document, dict) else None


def _source_definition_rows(db):
    """``(owner, id, raw)`` from the current table or the pre-cutover one.

    The table created before the owner column is ``(center, id, definition)``.
    ``center`` is the universe id. The owner is ``owner_user_id`` inside the
    document. Selecting ``owner`` from that table is an error, and that error
    used to refuse every center.
    """
    if not _table_exists(db, "box_model_sources"):
        return []
    columns = set(_columns(db, "box_model_sources"))
    if "definition" not in columns:
        return []
    selected = [
        name for name in ("owner", "id", "definition", "center") if name in columns
    ]
    if "owner" not in selected and "center" not in selected:
        return []
    query = (
        "SELECT " + ", ".join(_quote(name) for name in selected)
        + " FROM box_model_sources"
    )
    rows = []
    for record in db.execute(query):
        values = dict(zip(selected, record))
        raw = values.get("definition")
        document = _json_object(raw)
        owner = values.get("owner")
        if not isinstance(owner, str) or not owner:
            candidate = document.get("owner_user_id") if document else None
            owner = candidate if isinstance(candidate, str) and candidate else None
        definition_id = values.get("id")
        if not isinstance(definition_id, str) or not definition_id:
            candidate = document.get("id") if document else None
            if isinstance(candidate, str) and candidate:
                definition_id = candidate
            elif document is not None and owner:
                derived = _definition_id(document)
                if derived is None:
                    continue
                document = dict(document)
                document["id"] = derived
                definition_id = derived
                raw = json.dumps(document, sort_keys=True)
            else:
                continue
        if not owner or not isinstance(raw, str):
            continue
        rows.append((owner, definition_id, raw))
    return rows


def _rewrite_source_definitions(db, kept):
    """Put the verified rows back in the columns this private copy already has."""
    columns = set(_columns(db, "box_model_sources"))
    if {"owner", "id", "definition"} <= columns:
        db.execute("DELETE FROM box_model_sources")
        if kept:
            db.executemany(
                "INSERT INTO box_model_sources (owner, id, definition) "
                "VALUES (?, ?, ?)",
                kept,
            )
        return
    if {"center", "id", "definition"} <= columns:
        payload = []
        for _owner, definition_id, raw in kept:
            document = _json_object(raw)
            center = document.get("universe_id") if document else None
            if isinstance(center, str) and center:
                payload.append((center, definition_id, raw))
        db.execute("DELETE FROM box_model_sources")
        if payload:
            db.executemany(
                "INSERT INTO box_model_sources (center, id, definition) "
                "VALUES (?, ?, ?)",
                payload,
            )


def _restrict_definitions(source_root):
    ledger = _active_db(source_root, "outbound.db")
    if ledger is None or _source_closed(ledger):
        return
    try:
        with _open_source(ledger) as db:
            if not _table_exists(db, "box_model_sources"):
                return
            grants = set()
            if _table_exists(db, "outbound_connection_grants"):
                columns = set(_columns(db, "outbound_connection_grants"))
                query = f"SELECT grant_id FROM {_quote('outbound_connection_grants')}"
                if "revoked_at" in columns:
                    query += " WHERE revoked_at IS NULL"
                grants = {
                    row[0] for row in db.execute(query) if isinstance(row[0], str)
                }
            kept = []
            for owner, definition_id, raw in _source_definition_rows(db):
                document = _verified_definition(owner, definition_id, raw, grants)
                if document is None:
                    continue
                kept.append((
                    owner, definition_id, json.dumps(document, sort_keys=True),
                ))
            _rewrite_source_definitions(db, kept)
    except sqlite3.Error:
        _remember_stage(source_root, "definitions")
        raise


def _close_source_databases(source_root):
    broker = Path(source_root) / ".broker"
    _close_source(broker / "outbound.db")
    _close_source(broker / "credential-custody.db")


def convert_legacy_vaults(source_root):
    """Copy http secrets that match one owner's live vault://http connection.

    A destination counts only when the general vault resolver returns a
    non-empty secret for ``vault://http/<destination>``. A matching destination
    with no usable secret is ``secret_missing``. ``service`` is not consulted.
    ``llm_subscription`` records are counted and skipped. A center with zero
    or several owners is refused. One center's bad file does not stop the rest.
    Without a custody row, the center directory is that center's universe id:
    a grant maps it only together with a vault destination. When a grant or
    definition names destinations for that center, only those destinations
    are copied. A grant for another universe, or a destination column that
    disagrees with the credential ref, leaves its secret unread. A custody
    or ledger file that exists but cannot be read refuses every center.
    The source database is copied once; later steps read that private copy.
    Any failure of that copy refuses every center and leaves every secret unwritten.
    """
    snap = None
    try:
        snap = _acquire_snapshot(source_root)
        if snap.failed:
            return _refused_center_stats(source_root)
        return _convert_snapshot(source_root, snap)
    except Exception as exc:
        if snap is not None:
            _unlink_written(snap)
            snap.failed = True
            _note_failure(snap, exc)
            if snap.stage not in _STAGE_TOKENS:
                snap.stage = "convert"
        _close_source_databases(source_root)
        return _refused_center_stats(source_root)
    finally:
        if snap is not None:
            _release_snapshot(source_root)


def _forget_owner(snap, source_root, owner, stats, kept_ids, owner_kept, owner_ids, written):
    """A failed vault write leaves this owner with no secret and no row."""
    snap.skipped_owners.add(owner)
    stats["http_kept"] -= owner_kept.pop(owner, 0)
    if stats["http_kept"] < 0:
        stats["http_kept"] = 0
    for connection_id in owner_ids.pop(owner, ()):
        kept_ids.discard(connection_id)
    digest = hashlib.sha256(owner.encode()).hexdigest()
    prefix = source_root / ".broker" / "accounts" / digest
    kept_paths = []
    for path in snap.written:
        try:
            path.relative_to(prefix)
        except ValueError:
            kept_paths.append(path)
            continue
        written.discard(path)
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass
    snap.written[:] = kept_paths


def _convert_snapshot(source_root, snap):
    stats = _legacy_stat_template()
    platform, local = _legacy_vault_paths(source_root)
    centers = set(platform) | set(local)
    if not centers:
        return stats
    custody = snap.paths.get("credential-custody.db")
    ledger_path = snap.paths.get("outbound.db")
    mapping, owner_counts, evidence, ledger_failed = legacy_center_owners(
        custody, centers, ledger_path=ledger_path,
        center_dests=_center_http_dests(platform, local),
        definition_root=source_root,
    )
    stats.update(owner_counts)
    if ledger_failed:
        snap.failed = True
        snap.stage = "ledger"
        _close_source_databases(source_root)
        return stats
    db = None
    if ledger_path is not None and ledger_path.is_file() and not _source_closed(ledger_path):
        db = _open_source(ledger_path)
    kept_ids = set()
    written = set()
    owner_kept = {}
    owner_ids = {}
    try:
        for center in sorted(centers):
            owner = mapping.get(center)
            if owner is None or owner in snap.skipped_owners:
                continue
            try:
                records = []
                loaded = False
                for vault_path in (platform.get(center), local.get(center)):
                    if vault_path is None:
                        continue
                    try:
                        records.extend(_load_vault_records(vault_path))
                        loaded = True
                    except (OSError, ValueError, TypeError):
                        stats["accounts_failed"] += 1
                if not loaded:
                    continue
                live_refs, revoked_refs = _connection_refs(db, owner)
                live_dests = {dest for _connection_id, dest in live_refs}
                revoked_dests = {dest for _connection_id, dest in revoked_refs}
                limit = evidence.get(center) or set()
                if limit:
                    live_dests = {dest for dest in live_dests if dest in limit}
                    revoked_dests = {dest for dest in revoked_dests if dest in limit}
                chosen = []
                seen = set()
                kept_here = 0
                for record in records:
                    raw_kind = record.get("credential_type")
                    if raw_kind == "llm_subscription":
                        stats["llm_subscription"] += 1
                        continue
                    kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
                    if kind != "http":
                        stats["other"] += 1
                        continue
                    served = missing = None
                    for dest in live_dests:
                        if dest in seen:
                            continue
                        outcome = _resolve_outcome([record], dest)
                        if outcome == "secret":
                            served = dest
                            break
                        if outcome == "missing":
                            missing = dest
                            break
                    if served is not None:
                        chosen.append(record)
                        seen.add(served)
                        kept_here += 1
                    elif missing is not None:
                        stats["secret_missing"] += 1
                    else:
                        field = _record_destination(record)
                        if field in revoked_dests and field not in live_dests:
                            stats["revoked"] += 1
                        else:
                            stats["unmatched"] += 1
                if chosen:
                    digest = hashlib.sha256(owner.encode()).hexdigest()
                    path = (
                        source_root / ".broker" / "accounts" / digest / "credentials"
                        / ".credential-vault.json"
                    )
                    if _linked_inside(source_root, path):
                        raise OSError("credential path is a symlink")
                    if path in written:
                        current = _load_vault_records(path)
                        have = _served_destinations(path)
                        extra = [
                            item for item in chosen
                            if _record_destination(item) not in have
                        ]
                        if extra:
                            _write_private_json(path, json.dumps(
                                {"schema_version": 1, "credentials": current + extra},
                                sort_keys=True,
                            ))
                            have = _served_destinations(path)
                        seen = {dest for dest in seen if dest in have}
                    elif path.is_symlink() or path.exists():
                        if path.is_symlink() or not path.is_file():
                            kept_here -= len(seen)
                            seen = set()
                        else:
                            have = _served_destinations(path)
                            dropped = {dest for dest in seen if dest not in have}
                            kept_here -= len(dropped)
                            seen = seen - dropped
                    else:
                        _write_private_json(path, json.dumps(
                            {"schema_version": 1, "credentials": chosen},
                            sort_keys=True,
                        ))
                        written.add(path)
                        snap.written.append(path)
                    have = _served_destinations(path)
                    dropped = {dest for dest in seen if dest not in have}
                    kept_here -= len(dropped)
                    if dropped:
                        stats["secret_missing"] += len(dropped)
                    seen = seen - dropped
                stats["http_kept"] += kept_here
                owner_kept[owner] = owner_kept.get(owner, 0) + kept_here
                for connection_id, dest in live_refs:
                    if dest in seen:
                        kept_ids.add(connection_id)
                        owner_ids.setdefault(owner, set()).add(connection_id)
            except (OSError, ValueError, sqlite3.Error, TypeError):
                stats["accounts_failed"] += 1
                _forget_owner(
                    snap, source_root, owner, stats, kept_ids, owner_kept, owner_ids,
                    written,
                )
        _keep_vault_backed(db, source_root, kept_ids, snap.skipped_owners)
    finally:
        if db is not None:
            db.close()
    _restrict_ledger(source_root, kept_ids)
    _restrict_definitions(source_root)
    return stats


def prepare_legacy(source_root):
    return convert_legacy_vaults(source_root)


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


def _named_owner(row, column):
    """An owner a secret may be filed under. Empty and sentinel names are not."""
    owner = row.get(column)
    if isinstance(owner, str) and owner and not owner.startswith("\0"):
        return owner
    return None


def _groups(root):
    snap = _SNAPSHOTS.get(_source_key(root))
    if snap is not None and snap.failed:
        return {}
    if snap is not None:
        ledger = snap.paths.get("outbound.db")
    else:
        ledger = _active_db(root, "outbound.db")
    rows = {table: [] for table in TABLES}
    if ledger is not None and ledger.is_file() and not _source_closed(ledger):
        try:
            with _open_ro(ledger) as db:
                loaded = {}
                for table in TABLES:
                    loaded[table] = _rows(db, table)
            rows = loaded
        except Exception as exc:
            if snap is not None:
                snap.failed = True
                snap.stage = "groups"
                _note_failure(snap, exc)
            _close_source_databases(root)
            return {}
    grouped = {}
    skipped = set(snap.skipped_owners) if snap is not None else set()
    skipped_digests = {
        hashlib.sha256(owner.encode()).hexdigest() for owner in skipped
    }

    def bucket(owner):
        return grouped.setdefault(owner, {
            "rows": {table: [] for table in TABLES}, "files": {}, "hash": None,
        })

    connection_owner = {}
    for row in rows["outbound_connections"]:
        owner = _named_owner(row, "owner_user_id")
        if owner is None or owner in skipped:
            continue
        connection_owner[row.get("connection_id")] = owner
        bucket(owner)["rows"]["outbound_connections"].append(row)
    for table in ("outbound_connection_grants", "browser_vault"):
        column = OWNER_COLUMN[table]
        for row in rows[table]:
            owner = _named_owner(row, column)
            if owner is None or owner in skipped:
                continue
            bucket(owner)["rows"][table].append(row)
    for row in rows["connection_capabilities"]:
        owner = connection_owner.get(row.get("connection_id"))
        if owner is None or owner in skipped:
            continue
        bucket(owner)["rows"]["connection_capabilities"].append(row)
    hashes = _owner_hashes([key for key in grouped if key and not key.startswith("\0")])
    for digest, files in account_files(root).items():
        if digest in skipped_digests:
            continue
        owner = hashes.get(digest)
        if owner is None:
            owner = "\0" + digest
        if owner in skipped:
            continue
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


def _source_grants(db):
    if not _table_exists(db, "outbound_connection_grants"):
        return set()
    columns = set(_columns(db, "outbound_connection_grants"))
    query = f"SELECT grant_id FROM {_quote('outbound_connection_grants')}"
    if "revoked_at" in columns:
        query += " WHERE revoked_at IS NULL"
    return {row[0] for row in db.execute(query) if isinstance(row[0], str)}


def _raw_ref(raw):
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    ref = document.get("ref")
    return ref if isinstance(ref, str) else None


def _grant_http_dest(db, owner, grant_id):
    """Destination of a live non-revoked grant, or None when it cannot be used."""
    if not grant_id or not isinstance(owner, str) or not owner:
        return None
    if not (_table_exists(db, "outbound_connection_grants")
            and _table_exists(db, "outbound_connections")):
        return None
    connection_columns = set(_columns(db, "outbound_connections"))
    grant_columns = set(_columns(db, "outbound_connection_grants"))
    if "credential_ref" not in connection_columns or "owner_user_id" not in grant_columns:
        return None
    revoked_grant = "AND g.revoked_at IS NULL" if "revoked_at" in grant_columns else ""
    revoked_conn = "AND c.revoked_at IS NULL" if "revoked_at" in connection_columns else ""
    row = db.execute(
        "SELECT c.credential_ref FROM outbound_connection_grants g "
        "JOIN outbound_connections c ON c.connection_id=g.connection_id "
        "WHERE g.grant_id=? AND g.owner_user_id=? AND c.owner_user_id=? "
        f"{revoked_grant} {revoked_conn}",
        (grant_id, owner, owner),
    ).fetchone()
    if row is None:
        return None
    return _http_record_key(row[0])


def _copy_definitions(live_db, source_root, live_root):
    """Copy verified api_key_http descriptors. A live row or file stays."""
    ledger = _active_db(source_root, "outbound.db")
    if ledger is None or _source_closed(ledger):
        return 0
    pending = []
    try:
        with _open_ro(ledger) as src:
            if not _table_exists(src, "box_model_sources"):
                return 0
            skipped = set()
            active = _SNAPSHOTS.get(_source_key(source_root))
            if active is not None:
                skipped = active.skipped_owners
            for owner, definition_id, raw in _source_definition_rows(src):
                if owner in skipped:
                    continue
                ref = _raw_ref(raw)
                dest = _grant_http_dest(live_db, owner, ref)
                if not isinstance(owner, str) or dest is None:
                    continue
                digest = hashlib.sha256(owner.encode()).hexdigest()
                vault = (
                    live_root / ".broker" / "accounts" / digest / "credentials"
                    / FILES["credential_vault"]
                )
                if dest not in _vault_http_dests(vault):
                    continue
                document = _verified_definition(owner, definition_id, raw, {ref})
                if document is not None:
                    pending.append((owner, definition_id, document))
    except sqlite3.Error:
        active = _SNAPSHOTS.get(_source_key(source_root))
        if active is not None:
            active.failed = True
            active.stage = "definitions"
        raise
    if not pending:
        return 0
    live_db.execute(
        "CREATE TABLE IF NOT EXISTS box_model_sources ("
        "owner TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, "
        "PRIMARY KEY (owner, id))"
    )
    inserted = 0
    by_universe = {}
    for owner, definition_id, document in pending:
        payload = json.dumps(document, sort_keys=True)
        existing = live_db.execute(
            "SELECT definition FROM box_model_sources WHERE owner=? AND id=?",
            (owner, definition_id),
        ).fetchone()
        if existing is None:
            live_db.execute(
                "INSERT INTO box_model_sources (owner, id, definition) "
                "VALUES (?, ?, ?)",
                (owner, definition_id, payload),
            )
            inserted += 1
        universe = document.get("universe_id")
        if isinstance(universe, str) and _CENTER_RE.fullmatch(universe):
            by_universe.setdefault(universe, []).append(document)
    for universe, documents in by_universe.items():
        directory = live_root / universe
        if directory.is_symlink() or not directory.is_dir():
            continue
        path = directory / "provider_definitions.json"
        if path.exists() or path.is_symlink():
            continue
        try:
            _write_private_json(path, json.dumps(documents, sort_keys=True))
        except OSError:
            continue
        active = _SNAPSHOTS.get(_source_key(source_root))
        if active is not None:
            active.written.append(path)
    return inserted


def _ref_dest(row):
    return _http_record_key(row.get("credential_ref"))


def _vault_http_dests(path):
    """Destinations the real resolver can serve from this vault file."""
    return _served_destinations(path)


def _surviving_dests(live_root, spec):
    """Destinations whose secret will still be in the vault after this merge.

    An existing live file wins, so only destinations already in it qualify.
    """
    digest = spec.get("hash")
    live_path = None
    if isinstance(digest, str) and _HASH_RE.fullmatch(digest):
        live_path = (
            live_root / ".broker" / "accounts" / digest / "credentials"
            / FILES["credential_vault"]
        )
    if live_path is not None and (live_path.exists() or live_path.is_symlink()):
        return _vault_http_dests(live_path)
    return _vault_http_dests(spec["files"].get("credential_vault"))


def _bind_rows(spec, dests):
    """Keep connection rows only when that destination's secret survives."""
    kept = set()
    connections = []
    for row in spec["rows"]["outbound_connections"]:
        dest = _ref_dest(row)
        connection_id = row.get("connection_id")
        if dest in dests and isinstance(connection_id, str):
            connections.append(row)
            kept.add(connection_id)
    return {
        "rows": {
            "outbound_connections": connections,
            "outbound_connection_grants": [
                row for row in spec["rows"]["outbound_connection_grants"]
                if row.get("connection_id") in kept
            ],
            "connection_capabilities": [
                row for row in spec["rows"]["connection_capabilities"]
                if row.get("connection_id") in kept
            ],
            "browser_vault": spec["rows"]["browser_vault"],
        },
        "files": spec["files"],
        "hash": spec["hash"],
    }


def merge(live_root, source_root):
    """Insert missing rows and files. Existing live rows and files stay.

    The source ledger and custody file are opened once. A later failure removes
    anything this merge wrote and returns counts only.
    """
    ledger = live_root / ".broker" / "outbound.db"
    if not ledger.is_file() or ledger.is_symlink():
        raise SourceRefused("ledger_missing")
    snap = None
    try:
        snap = _acquire_snapshot(source_root)
        return _merge_snapshot(live_root, source_root, snap)
    except SourceRefused:
        raise
    except Exception as exc:
        if snap is not None:
            _unlink_written(snap)
            snap.failed = True
            _note_failure(snap, exc)
            if snap.stage not in _STAGE_TOKENS:
                snap.stage = "merge"
        _close_source_databases(source_root)
        return _refusal_plan(source_root)
    finally:
        if snap is not None:
            _release_snapshot(source_root)


def _merge_snapshot(live_root, source_root, snap):
    """One owner's failed vault write is skipped. A failed read refuses everyone.

    Merge keeps a savepoint per account, so a bad row does not roll the other
    accounts back. The account savepoint is not the outer transaction: that
    stays open until the snapshot re-read finishes, and a failed re-read rolls
    every account back. A vault write that fails is that owner's row: the
    secret is not counted, and that owner's connections and browser rows are
    not inserted. A failed read of the source snapshot is different. It unlinks
    every file this merge wrote and returns ``error`` ``unreadable``.
    """
    if snap.failed:
        legacy = _refused_legacy(source_root)
        grouped = {}
    else:
        legacy = prepare_legacy(source_root)
        if snap.failed:
            _unlink_written(snap)
            legacy = _refused_legacy(source_root)
            grouped = {}
        else:
            grouped = _groups(source_root)
            if snap.failed:
                _unlink_written(snap)
                legacy = _refused_legacy(source_root)
                grouped = {}
    ledger = live_root / ".broker" / "outbound.db"
    legacy_bound = any(legacy.get(name) for name in (
        "centers_one_owner", "centers_zero_owners", "centers_several_owners",
    ))
    files = {kind: _slot() for kind in FILES}
    tables = {table: _slot() for table in TABLES}
    affected = failed = unchanged = 0
    with sqlite3.connect(ledger) as db:
        db.execute("PRAGMA busy_timeout=5000")
        # A released account savepoint would commit if it opened the transaction.
        db.execute("BEGIN")
        for spec in grouped.values():
            account = spec
            if legacy_bound:
                snap.stage = "bind"
                account = _bind_rows(spec, _surviving_dests(live_root, spec))
                if snap.stage == "bind":
                    snap.stage = ""
            account_files_slot = {kind: _slot() for kind in FILES}
            account_tables = {table: _slot() for table in TABLES}
            db.execute("SAVEPOINT account")
            created = []
            try:
                for table in TABLES:
                    for row in account["rows"][table]:
                        outcome = _insert_missing(db, table, row)
                        account_tables[table][outcome] += 1
                for kind, path in account["files"].items():
                    outcome, written = _copy_missing(
                        live_root, account["hash"], kind, path,
                    )
                    account_files_slot[kind][outcome] += 1
                    if written is not None:
                        created.append(written)
                        snap.written.append(written)
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
        try:
            legacy["definitions"] = _copy_definitions(db, source_root, live_root)
        except (sqlite3.Error, OSError):
            if snap.failed:
                raise
            legacy["accounts_failed"] += 1
    plan = {
        "accounts": len(grouped),
        "accounts_affected": affected,
        "accounts_failed": failed + legacy["accounts_failed"],
        "accounts_unchanged": unchanged,
        "files": files,
        "tables": tables,
        "legacy": legacy,
    }
    if snap.failed:
        plan["error"] = "unreadable"
    return plan


def _definitions(root):
    ledger = root / ".broker" / "outbound.db"
    if _source_closed(ledger) or not ledger.is_file() or ledger.is_symlink():
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
    db = (
        _open_ro(ledger) if ledger.is_file() and not ledger.is_symlink()
        and not _source_closed(ledger) else None
    )
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


def _verification_counts(source_markers=0, markers_restored=0):
    return {
        "api_key_http_definitions": 0,
        "api_key_http_grants_live": 0,
        "cli_accounts": 0,
        "cli_with_custody": 0,
        "cli_marker_consistent": 0,
        "cli_marker_missing": 0,
        "cli_marker_unreadable": 0,
        "cli_marker_separate_step": 0,
        "source_connection_markers": source_markers,
        "cli_markers_restored": markers_restored,
    }


def _safe_refusal_plan(source_root):
    try:
        return _refusal_plan(source_root)
    except Exception:
        return {
            "accounts": 0,
            "accounts_affected": 0,
            "accounts_failed": 0,
            "accounts_unchanged": 0,
            "files": {kind: _slot() for kind in FILES},
            "tables": {table: _slot() for table in TABLES},
            "legacy": {**_legacy_stat_template(), "stage": "merge"},
            "error": "unreadable",
        }


def apply_tree(live_root, source_root, rollback_dir, *, mounts=None, run=_run):
    """Stop the broker, keep a verified copy, merge, start the broker again.

    An unexpected failure becomes a counts-only refusal after the broker starts.
    ``SourceRefused`` still leaves the plan unset.
    """
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
    unexpected = False
    try:
        kept = take_rollback(live_root, rollback_dir)
        plan = merge(live_root, source_root)
        if isinstance(plan, dict) and plan.get("error"):
            merge_error = plan["error"]
        restored = restore_markers(mounts, source_root)
    except SourceRefused as refused:
        merge_error = refused.code
    except Exception:
        merge_error = "unreadable"
        unexpected = True
    finally:
        try:
            broker("start", run)
            started = True
        except SourceRefused:
            started = False
    if unexpected and plan is None:
        plan = _safe_refusal_plan(source_root)
    facts = {
        "rollback_kept": kept,
        "broker_stopped": True,
        "broker_started": started,
        "ledger_missing": False,
        "rollback_date": _rollback_date(rollback_dir, kept),
    }
    try:
        checked = verify(
            live_root, mounts, source_markers=markers, markers_restored=restored,
        )
    except Exception:
        checked = _verification_counts(markers, restored)
        if merge_error is None:
            merge_error = "unreadable"
        if plan is None:
            plan = _safe_refusal_plan(source_root)
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


def _legacy_choice(records):
    readable = [(name, counts) for name, counts, error in records if error is None]
    if not readable:
        return None

    def rank(item):
        name, counts = item
        files = counts["platform_vault_files"] + counts["center_vault_files"]
        return (counts["http"], files, _archive_date(name) or "")
    return max(readable, key=rank)[0]


def _scan_legacy(args):
    records = []
    if args.snapshot_dir.is_dir():
        for path in sorted(args.snapshot_dir.glob("tinyassets-*.tar.gz")):
            if path.is_symlink() or not ARCHIVE_RE.fullmatch(path.name):
                continue
            try:
                records.append((path.name, inspect_legacy(path), None))
            except SourceRefused as refused:
                records.append((path.name, _legacy_counts(), refused.code))
    root = args.root
    if root is None:
        try:
            completed = _run(
                ["docker", "volume", "inspect", "tinyassets-data",
                 "--format", "{{.Mountpoint}}"],
                timeout=60,
            )
        except SourceRefused:
            completed = None
        if completed is not None and completed.returncode == 0:
            text = completed.stdout.decode().strip()
            root = Path(text) if text else None
    if root is not None and root.parent.is_dir():
        for path in root.parent.glob(".tinyassets-restore-old.*"):
            if not (path.is_dir() and not path.is_symlink()
                    and RETAINED_RE.fullmatch(path.name)):
                continue
            try:
                records.append((path.name, inspect_legacy_tree(path), None))
            except SourceRefused as refused:
                records.append((path.name, _legacy_counts(), refused.code))
    return records


def _main_inspect_legacy(args):
    """Counts only. A named archive that is missing or unreadable exits 2."""
    records = _scan_legacy(args)
    counts = _legacy_counts()
    error = None
    if args.source == "auto":
        selected = _legacy_choice(records)
        if selected is None:
            error = "source_missing"
        else:
            counts = next(item[1] for item in records if item[0] == selected)
    else:
        match = next((item for item in records if item[0] == args.source), None)
        selected = args.source if _string_ok(args.source) else None
        if match is None:
            error = "source_missing"
        elif match[2]:
            error = match[2]
        else:
            counts = match[1]
    report = {
        "mode": "inspect-legacy",
        "selected": selected if isinstance(selected, str) and _string_ok(selected) else None,
        "legacy": counts,
    }
    if error:
        report["error"] = error
    try:
        sys.stdout.write(safe_json(report) + "\n")
    except SourceRefused:
        raise SystemExit("diagnostic refused: output_contains_private_value")
    if error:
        raise SystemExit(2)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="restore broker credentials")
    parser.add_argument("--mode", default="inspect")
    parser.add_argument("--source", default="auto")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--mounts", type=Path, default=Path("/var/lib/boxhostd/mounts"))
    parser.add_argument("--backup-log", type=Path, default=Path("/var/log/tinyassets-backup.log"))
    parser.add_argument("--snapshot-dir", type=Path, default=Path("/var/backups/tinyassets"))
    args = parser.parse_args(argv)
    if args.mode not in {"inspect", "inspect-legacy", "dry-run", "apply"}:
        raise SystemExit("diagnostic refused: bad_mode")
    if args.source != "auto" and not (
        ARCHIVE_RE.fullmatch(args.source) or RETAINED_RE.fullmatch(args.source)
    ):
        raise SystemExit("diagnostic refused: bad_source")
    if not _package_available():
        # Before any root, log, archive, or ledger is opened.
        sys.stdout.write(safe_json({
            "mode": args.mode, "error": "package_unavailable",
        }) + "\n")
        raise SystemExit(2)
    # The shell discovers the docker volume and apply writes it. Both need
    # root. A named root on inspect or dry-run only reads that tree.
    if os.geteuid() != 0 and (args.root is None or args.mode == "apply"):
        raise SystemExit("diagnostic refused: not_root")
    if args.mode == "inspect-legacy":
        return _main_inspect_legacy(args)
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
        if chosen is None:
            error = "source_missing"
        elif not chosen["readable"]:
            error = chosen.get("error") or "unreadable"
        elif args.mode == "apply" and chosen["kind"] == "archive" and not chosen["digest_known"]:
            error = "digest_unknown"
        else:
            cleanup = None
            try:
                source_root, cleanup = _materialize(
                    root, args.snapshot_dir, chosen,
                    destination=destination, digests=digests,
                )
                if args.mode == "dry-run":
                    plan = _plan_only(root, source_root)
                    if error is None and isinstance(plan, dict) and plan.get("error"):
                        error = plan["error"]
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
                if cleanup is not None:
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


def _refuse_wanted_symlink(root):
    """A credential member that is a symlink is unreadable. Other links are not."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if _wanted(parts) is not None:
            raise SourceRefused("unreadable")


def _copy_wanted(source, dest):
    """Copy credential members so preparing a retained backup cannot change it."""
    if source.is_symlink() or not source.is_dir():
        raise SourceRefused("unreadable")
    _refuse_wanted_symlink(source)
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            parts = path.relative_to(source).parts
        except ValueError:
            continue
        relative = _wanted(parts)
        if relative is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if relative[-1] != "outbound.db" and size > 8 * 1024 * 1024:
            continue
        target = dest.joinpath(*relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return dest


def _materialize(live_root, snapshot_dir, chosen, *, destination="", digests=None):
    directory = tempfile.TemporaryDirectory(prefix="ta-cred-src-")
    try:
        if chosen["kind"] == "retained":
            path = live_root.parent / chosen["name"]
            if (path.is_symlink() or not path.is_dir()
                    or not RETAINED_RE.fullmatch(path.name)):
                raise SourceRefused("unreadable")
            root = _copy_wanted(path, Path(directory.name) / "tree")
        else:
            local = _stage_archive(
                directory.name, snapshot_dir, chosen, destination, digests,
            )
            root = extract_needed(local, Path(directory.name) / "tree")
    except Exception:
        directory.cleanup()
        raise
    return root, directory.cleanup


if __name__ == "__main__":
    main()
