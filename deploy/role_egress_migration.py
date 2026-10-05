"""D12 offline egress relocation, called inside the startup migration window.

Linux only, stdlib only. The caller must have stopped every role/container using
the volume. We take the common layout lock, pin directories, refuse aliases and
never replace a destination. Completion of this SUBSTEP leaves the top-level
layout migrating: only the full role migration may admit normal service.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pickle
import secrets
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

BROKER_DIR = ".broker"
LEDGER = "outbound.db"
PROXY = ".outbound-proxy"
SIDECARS = (LEDGER + "-wal", LEDGER + "-shm", LEDGER + "-journal")
PRIVATE_DIR_MODE = 0o2700
PRIVATE_FILE_MODE = 0o600


def migrate_liveness(data_root, *, modes, reverse=False, dry_run=False, after_step=None):
    """Offline proof-mode substep. ``modes`` is the chain-verified role_modes declaration.

    The full startup caller has stopped every role before taking this lock.
    No content is read, deleted or rewritten, and no liveness claim is minted.
    """
    import fcntl

    direction = "reverse" if reverse else "forward"
    uid = modes["DAEMON_UID"]
    gid = uid if reverse else modes["BROKER_READ_GID"]
    directory_mode = modes["LEGACY_LIVENESS_DIRECTORY_MODE" if reverse
                           else "LIVENESS_DIRECTORY_MODE"]
    file_mode = modes["LEGACY_LIVENESS_FILE_MODE" if reverse else "LIVENESS_FILE_MODE"]
    with ExitStack() as stack:
        root = stack.enter_context(_directory(data_root))
        if _regular(root, ".layout.lock") is None:
            raise MigrationRefused("liveness migration requires the layout lock")
        lock = os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        document = _read_marker(root)
        roles = document.get("roles", {})
        progress = roles.get("liveness", {})
        if (document.get("layout") != 2
                or document.get("moves", {}).get("consents_outside_command_centers") != "done"
                or document.get("state") not in {"stable", "migrating"}
                or document.get("state") == "migrating" and roles.get("state") != "migrating"):
            raise MigrationRefused("complete unrelated layout migration first")
        if progress.get("state") == "migrating" and progress.get("direction") != direction:
            raise MigrationRefused("finish interrupted liveness direction before reversing")
        name = ".consumer_liveness"
        if _stat(root, name) is None:
            return []  # runtime creates it using the same declaration
        parent = stack.enter_context(_directory(name, parent=root))
        if os.fstat(parent).st_uid != uid or os.fstat(parent).st_dev != os.fstat(root).st_dev:
            raise MigrationRefused("liveness directory owner or device changed")
        entries = []
        for child in sorted(os.listdir(parent)):
            info = _regular(parent, child)
            if info.st_uid != uid or not child.endswith((".lock", ".lock.pid")):
                raise MigrationRefused("unexpected liveness entry")
            fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, fd)
            opened = os.fstat(fd)
            if not os.path.samestat(info, opened) or opened.st_nlink != 1:
                raise MigrationRefused("liveness proof changed")
            entries.append((child, fd, opened, file_mode))
        entries.append((name, parent, os.fstat(parent), directory_mode))
        changes = [(child, fd, mode) for child, fd, info, mode in entries
                   if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode)]
        plan = [f"{direction}: liveness {child} -> {uid}:{gid} {mode:04o}"
                for child, _, mode in changes]
        if dry_run:
            return plan
        if not changes and progress == {"state": "stable", "direction": direction}:
            return []
        document = _mark(root, document, {"state": "migrating", "direction": direction},
                         section="liveness")
        if after_step:
            after_step("liveness-marker")
        for child, fd, mode in changes:
            _permissions(fd, uid, gid, mode)
            os.fsync(fd)
            if after_step:
                after_step("liveness-entry")
        os.fsync(parent)
        _mark(root, document, {"state": "stable", "direction": direction}, section="liveness")
        return plan


class MigrationRefused(RuntimeError):
    """Unsafe or inconsistent input; retained data needs operator attention."""


@contextmanager
def _directory(name, *, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _stat(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _regular(parent, name):
    info = _stat(parent, name)
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise MigrationRefused(f"non-regular file or hardlink: {name}")
    return info


def _tree(parent, name, *, uid=None, gid=None):
    """Validate before mutation; then fsync and re-mode using pinned descriptors."""
    info = _stat(parent, name)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode):
        with _directory(name, parent=parent) as fd:
            if os.fstat(fd).st_dev != os.fstat(parent).st_dev:
                raise MigrationRefused(f"cross-filesystem broker tree: {name}")
            for child in sorted(os.listdir(fd)):
                _tree(fd, child, uid=uid, gid=gid)
            if uid is not None:
                _permissions(fd, uid, gid, PRIVATE_DIR_MODE if uid == 1002 else 0o700)
                os.fsync(fd)
    else:
        _regular(parent, name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            actual = os.fstat(fd)
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise MigrationRefused(f"entry changed during validation: {name}")
            if uid is not None:
                _permissions(fd, uid, gid, PRIVATE_FILE_MODE)
                os.fsync(fd)
        finally:
            os.close(fd)


def _permissions(fd, uid, gid, mode):
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.fchown(fd, uid, gid)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        # Without FSETID, Linux silently clears S_ISGID when the target group
        # is absent from the caller's groups, even with FOWNER. SETGID already
        # belongs to the startup capability set; retain no extra authority.
        previous_gid = os.getegid()
        try:
            if mode & stat.S_ISGID:
                os.setegid(gid)
            os.fchmod(fd, mode)
        finally:
            os.setegid(previous_gid)
    actual = os.fstat(fd)
    if (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (uid, gid, mode):
        raise MigrationRefused("ownership/mode readback failed")


def _rename(source, destination, name):
    """Atomic no-replace rename, including directories; never clobber data."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, os.fsencode(name), destination, os.fsencode(name), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)
    os.fsync(destination)
    os.fsync(source)


def _read_marker(root):
    if _regular(root, ".layout.json") is None:
        raise MigrationRefused("missing .layout.json; initialize the data layout first")
    fd = os.open(".layout.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
    with os.fdopen(fd, encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("layout") not in (1, 2):
        raise MigrationRefused("unsupported layout marker")
    return document


def _mark(root, document, progress, *, section="egress"):
    document = {**document, "state": "migrating", "roles": {
        **document.get("roles", {}), "state": "migrating", section: progress,
    }}
    temporary = ".layout.roles." + secrets.token_hex(16) + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=root)
    # The temporary is ours (O_EXCL); only this file may be removed on error.
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True)
            handle.flush()
            os.fchown(handle.fileno(), 1001, 1001)
            os.fsync(handle.fileno())
        os.replace(temporary, ".layout.json", src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)
    finally:
        if _stat(root, temporary) is not None:
            os.unlink(temporary, dir_fd=root)
    return document


def _checkpoint(parent):
    if _regular(parent, LEDGER) is None:
        if any(_stat(parent, name) is not None for name in SIDECARS):
            raise MigrationRefused("orphan SQLite sidecar without ledger")
        return
    # Every entry was validated under the exclusive startup lock. SQLite needs
    # a pathname to manage its sidecars; the pinned parent survives renames.
    path = f"/proc/self/fd/{parent}/{LEDGER}"
    connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result[0] != 0:
            raise MigrationRefused("busy WAL; another writer has not stopped")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise MigrationRefused("ledger integrity check failed")
    finally:
        connection.close()
    for name in (LEDGER, *SIDECARS):
        if _regular(parent, name) is not None:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    os.fsync(parent)


def relocate(data_root: Path, *, reverse=False, dry_run=False, after_step=None):
    """Return a plan or complete the egress substep; no service is started.

    after_step is an in-process fault-injection seam, not a CLI/RPC operation.
    Dry-run requires an existing layout lock and reads without opening SQLite.
    The full startup migration owns initialization and final layout admission.
    """
    import fcntl

    direction = "reverse" if reverse else "forward"
    with ExitStack() as stack:
        root = stack.enter_context(_directory(data_root))
        if _regular(root, ".layout.lock") is None:
            raise MigrationRefused("missing .layout.lock; initialize the data layout first")
        lock = os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        document = _read_marker(root)
        progress = document.get("roles", {}).get("egress", {})
        accounting = document.get("roles", {}).get("accounting", {})
        if accounting.get("state") == "migrating":
            raise MigrationRefused("complete accounting transfer before relocating egress")
        if reverse and accounting.get("direction") == "forward":
            raise MigrationRefused("reverse accounting transfer before relocating egress")
        # storage_layout can resume the consents move and mark the whole
        # volume stable. Do not overlap it: that would admit an old daemon
        # after relocation, which would create an empty ledger at the old path.
        if (document.get("layout") != 2
                or document.get("moves", {}).get("consents_outside_command_centers") != "done"):
            raise MigrationRefused("complete the consents layout migration before role migration")
        own_migration = (document.get("roles", {}).get("state") == "migrating"
                         and progress.get("direction") in ("forward", "reverse")
                         and progress.get("state") in ("migrating", "stable"))
        if document.get("state") != "stable" and not (
                document.get("state") == "migrating" and own_migration):
            raise MigrationRefused("unrelated layout migration is not complete")
        if progress.get("state") == "migrating" and progress.get("direction") != direction:
            raise MigrationRefused("finish interrupted egress direction before reversing")
        broker_info = _stat(root, BROKER_DIR)
        broker = None
        if broker_info is not None:
            broker = stack.enter_context(_directory(BROKER_DIR, parent=root))
            if os.fstat(broker).st_dev != os.fstat(root).st_dev:
                raise MigrationRefused("broker directory must share the data filesystem")
        names = (*SIDECARS, LEDGER, PROXY)
        plan = []
        for name in names:
            old, new = _stat(root, name), _stat(broker, name) if broker is not None else None
            if old is not None and new is not None:
                raise MigrationRefused(f"conflicting source and destination: {name}")
            if old is not None:
                _tree(root, name)
            if new is not None:
                _tree(broker, name)
            if (new if reverse else old) is not None:
                plan.append(f"{direction}: {name}")
        if dry_run:
            return plan
        # Durable marker precedes even mkdir/chown. A completed repeat makes no
        # metadata changes; normal broker writes retain these declared modes.
        if (progress.get("direction") == direction
                and progress.get("state") == "stable" and not plan):
            return []
        resumed = progress.get("direction") == direction and progress.get("state") == "migrating"
        progress = {"direction": direction, "state": "migrating",
                    "checkpointed": bool(resumed and progress.get("checkpointed"))}
        document = _mark(root, document, progress)
        if broker is None:
            os.mkdir(BROKER_DIR, 0o700, dir_fd=root)
            os.fsync(root)
            broker = stack.enter_context(_directory(BROKER_DIR, parent=root))
        _permissions(broker, 1002, 1101, PRIVATE_DIR_MODE)
        os.fsync(broker)
        source, destination = (broker, root) if reverse else (root, broker)
        if not progress["checkpointed"]:
            _checkpoint(source)
            progress["checkpointed"] = True
            document = _mark(root, document, progress)
            if after_step:
                after_step("checkpoint")
        uid, gid = (1001, 1001) if reverse else (1002, 1101)
        for name in names:
            if _stat(source, name) is not None:
                _tree(source, name, uid=uid, gid=gid)
                _rename(source, destination, name)
                if after_step:
                    after_step(name)
            elif _stat(destination, name) is not None:
                _tree(destination, name, uid=uid, gid=gid)
        _mark(root, document, {**progress, "state": "stable"})
        return plan


# Kept stdlib-only for the isolated privileged chain. The oracle compares this
# schema to storage.agent_request_usage._SCHEMA; no volume SQL is executed as DDL.
ACCOUNTING_SCHEMA = (
    """CREATE TABLE agent_request_usage (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      policy_json TEXT NOT NULL, failures_json TEXT NOT NULL,
      owner_token TEXT NOT NULL, parent_token TEXT NOT NULL, closed INTEGER NOT NULL,
      created_at TEXT NOT NULL, PRIMARY KEY(owner, universe, usage_id))""",
    """CREATE TABLE agent_request_attempts (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL, attempt_json TEXT NOT NULL, dispatched_at TEXT,
      source_ref TEXT NOT NULL, state TEXT NOT NULL,
      PRIMARY KEY(owner, universe, usage_id, ordinal))""",
    """CREATE TABLE agent_request_usage_links (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      kind TEXT NOT NULL, subject_id TEXT NOT NULL,
      PRIMARY KEY(owner, universe, kind, subject_id, usage_id))""",
    """CREATE TABLE agent_request_dispatches (
      reference_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, universe TEXT NOT NULL,
      usage_id TEXT NOT NULL, first_ordinal INTEGER NOT NULL, last_ordinal INTEGER NOT NULL,
      grant_id TEXT NOT NULL, connection_id TEXT NOT NULL, request_digest TEXT NOT NULL,
      operation_id TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      UNIQUE(owner, universe, usage_id, first_ordinal))""",
)
ACCOUNTING_TABLES = tuple(sql.split()[2] for sql in ACCOUNTING_SCHEMA)
ACCOUNTING_INDEX = """CREATE INDEX agent_request_attempt_day
      ON agent_request_attempts(owner, source_ref, dispatched_at)"""


def _accounting_facts(conn):
    """Typed row digests with a static schema allowlist, never execute stored DDL."""
    expected = {name: "".join(sql.split()).lower()
                for name, sql in zip(ACCOUNTING_TABLES, ACCOUNTING_SCHEMA)}
    index = conn.execute("SELECT name,type,sql FROM sqlite_master WHERE lower(name)=?",
                         ("agent_request_attempt_day",)).fetchone()
    if index is not None and (index[:2] != ("agent_request_attempt_day", "index")
            or "".join((index[2] or "").split()).lower()
            != "".join(ACCOUNTING_INDEX.split()).lower()):
        raise MigrationRefused("unknown accounting index schema")
    result = {}
    for table in ACCOUNTING_TABLES:
        row = conn.execute("SELECT type,sql,name FROM sqlite_master WHERE lower(name)=?",
                            (table,)).fetchone()
        if row is None:
            continue
        if (row[0] != "table" or row[2] != table
                or "".join((row[1] or "").split()).lower() != expected[table]):
            raise MigrationRefused(f"unknown accounting schema: {table}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                        (table,)).fetchone():
            raise MigrationRefused(f"accounting trigger is not migratable: {table}")
        keys = sorted((r[5], r[1]) for r in conn.execute(f"PRAGMA table_info({table})") if r[5])
        ordering = ",".join(key for _, key in keys)
        digest, count = hashlib.sha256(), 0
        for values in conn.execute(f"SELECT * FROM {table} ORDER BY {ordering}"):
            encoded = pickle.dumps(tuple(values), protocol=4)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def _database_entries(parent, name):
    """Validate SQLite's complete filename set before any SQLite open."""
    names = (name, name + "-wal", name + "-shm", name + "-journal")
    found = {item: _regular(parent, item) for item in names}
    if found[name] is None and any(found[item] is not None for item in names[1:]):
        raise MigrationRefused(f"orphan accounting sidecar: {name}")
    return found


@contextmanager
def _database_snapshot(parent, name):
    """Read stopped SQLite/WAL through private copies; dry-run changes no sidecars."""
    entries = _database_entries(parent, name)
    with tempfile.TemporaryDirectory(prefix="ta-accounting-plan-") as temporary:
        destination = Path(temporary) / name
        for entry, info in entries.items():
            if info is None or entry.endswith("-shm"):
                continue  # SQLite rebuilds its private WAL index in the copy.
            fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME,
                         dir_fd=parent)
            with os.fdopen(fd, "rb") as source:
                if not os.path.samestat(info, os.fstat(source.fileno())):
                    raise MigrationRefused("accounting entry changed during snapshot")
                with (Path(temporary) / entry).open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
        conn = sqlite3.connect(destination)
        try:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise MigrationRefused("accounting database integrity check failed")
            yield conn
        finally:
            conn.close()


def transfer_accounting(data_root: Path, *, reverse=False, dry_run=False, after_step=None):
    """Offline copy/verify/drop, reversible and resumable; never admits a service."""
    import fcntl

    direction = "reverse" if reverse else "forward"
    with ExitStack() as stack:
        root = stack.enter_context(_directory(data_root))
        if _regular(root, ".layout.lock") is None:
            raise MigrationRefused("accounting transfer requires the layout lock")
        lock = os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        document = _read_marker(root)
        roles = document.get("roles", {})
        egress, progress = roles.get("egress", {}), roles.get("accounting", {})
        if (reverse and progress.get("direction") == "reverse"
                and progress.get("state") == "stable"
                and egress.get("direction") == "reverse"
                and egress.get("state") in {"stable", "migrating"}):
            return []  # Let a subsequent interrupted reverse relocation resume.
        if (document.get("layout") != 2
                or document.get("moves", {}).get("consents_outside_command_centers") != "done"
                or document.get("state") not in {"stable", "migrating"}
                or roles.get("state") not in {"stable", "migrating"}
                or egress.get("direction") != "forward" or egress.get("state") != "stable"):
            raise MigrationRefused("complete forward egress relocation before accounting transfer")
        if progress.get("state") == "migrating" and progress.get("direction") != direction:
            raise MigrationRefused("finish interrupted accounting direction before reversing")
        broker = stack.enter_context(_directory(BROKER_DIR, parent=root))
        if os.fstat(broker).st_dev != os.fstat(root).st_dev:
            raise MigrationRefused("accounting stores must share the data filesystem")
        if _regular(broker, LEDGER) is None:
            raise MigrationRefused("missing relocated ledger")
        old, new = (root, ".tinyassets.db"), (broker, LEDGER)
        source, target = (new, old) if reverse else (old, new)
        source_copy = stack.enter_context(_database_snapshot(*source))
        target_copy = stack.enter_context(_database_snapshot(*target))
        source_facts, target_facts = _accounting_facts(source_copy), _accounting_facts(target_copy)
        if (progress.get("direction") == direction and progress.get("state") == "stable"
                and not source_facts):
            return []
        resumed = progress.get("direction") == direction and progress.get("state") == "migrating"
        manifest = progress.get("manifest") if resumed else source_facts
        if not isinstance(manifest, dict) or set(manifest) - set(ACCOUNTING_TABLES):
            raise MigrationRefused("invalid accounting transfer manifest")
        if resumed:
            if (source_facts not in ({}, manifest) or target_facts not in ({}, manifest)
                    or (manifest and not source_facts and not target_facts)):
                raise MigrationRefused("accounting copies diverged from the transfer manifest")
        elif target_facts:
            raise MigrationRefused("conflicting destination accounting tables")
        plan = [f"{direction}: {table} ({facts['rows']} rows)"
                for table, facts in manifest.items()]
        if dry_run:
            return plan
        progress = {"direction": direction, "state": "migrating", "manifest": manifest}
        document = _mark(root, document, progress, section="accounting")

        def step(name):
            if after_step:
                after_step(name)

        step("accounting-manifest")
        if manifest:
            # Hold the source write lock through the destination commit and
            # verification. The startup lock also excludes all admitted roles.
            src = sqlite3.connect(f"file:/proc/self/fd/{source[0]}/{source[1]}?mode=rw",
                                  uri=True, timeout=0, isolation_level=None)
            stack.callback(src.close)
            src.execute("PRAGMA synchronous=FULL")
            src.execute("BEGIN IMMEDIATE")
            if _accounting_facts(src) != source_facts:
                raise MigrationRefused("source accounting changed after preflight")
            dst = sqlite3.connect(f"file:/proc/self/fd/{target[0]}/{target[1]}?mode=rwc",
                                  uri=True, timeout=0, isolation_level=None)
            stack.callback(dst.close)
            dst.execute("PRAGMA synchronous=FULL")
            dst.execute("BEGIN IMMEDIATE")
            if _accounting_facts(dst) != target_facts:
                raise MigrationRefused("target accounting changed after preflight")
            if not target_facts:
                for table, schema in zip(ACCOUNTING_TABLES, ACCOUNTING_SCHEMA):
                    if table not in manifest:
                        continue
                    dst.execute(schema)
                    columns = len(src.execute(f"PRAGMA table_info({table})").fetchall())
                    dst.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * columns)})",
                                    src.execute(f"SELECT * FROM {table}"))
                if "agent_request_attempts" in manifest:
                    dst.execute(ACCOUNTING_INDEX)
            if _accounting_facts(dst) != manifest:
                raise MigrationRefused("destination accounting verification failed")
            dst.commit()
            os.fsync(target[0])
            step("accounting-copy")
            # Verify the committed transaction again before dropping source tables.
            dst.execute("BEGIN IMMEDIATE")
            if _accounting_facts(dst) != manifest:
                raise MigrationRefused("committed accounting verification failed")
            document = _mark(root, document, {**progress, "copied": True}, section="accounting")
            step("accounting-verified")
            for table in source_facts:
                src.execute(f"DROP TABLE {table}")
            src.commit()
            os.fsync(source[0])
            step("accounting-drop")
            dst.commit()
            src.close()
            dst.close()
            # A newly created reverse target and retained SQLite sidecars take
            # the destination role's ownership, never root's startup identity.
            uid, gid = (1001, 1001) if reverse else (1002, 1101)
            for entry, info in _database_entries(*target).items():
                if info is not None:
                    _tree(target[0], entry, uid=uid, gid=gid)
            os.fsync(target[0])
        _mark(root, document, {**progress, "state": "stable"}, section="accounting")
        return plan
