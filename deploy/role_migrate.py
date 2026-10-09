"""One-time, forward-only owner-split migration of the stopped data volume.

Runs once, inside the maintenance window, as a one-shot container from the
cutover image (docs/ops/owner-split-cutover-runbook.md):

    docker run --rm --user 0 --cap-drop ALL \\
        --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE \\
        --network none -v tinyassets-data:/data --entrypoint /opt/venv/bin/python \\
        <image> -I -B /usr/local/libexec/ta-migrate.py --snapshot <id> --snapshot-bytes <n>

``--check`` is read-only and lists every entry not at its target. ``--manifest``
prints the volume's (path, type, uid, gid, mode, ACL, sha256) census.

Preconditions refuse before the first write. Then four steps, each of which
sets its target or skips, and none of which reads a previous run's progress:

1. identities: reserve each principal's UID/GID and append its ``admit`` row;
2. egress: move the ledger and proxy state into ``/data/.broker``, then move
   the accounting tables into the ledger;
3. labels: set uid, gid, mode and ACL on every entry to its target;
4. marker: add ``"split": "owner-split"`` to ``/data/.layout.json``, last.

Every target is a pure function of the path, the entry's own type and kept bits,
and ``owner-identities.db``. Reservations are written before any chown uses
them, so a run killed at any point converges when the same command runs again,
and a converged volume is a no-op. Stdlib only, plus the app's literal
``role_modes`` declaration.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import pickle
import re
import runpy
import secrets
import sqlite3
import stat
import struct
import sys
import tempfile
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

DAEMON, BROKER, BROKER_GROUP = 1001, 1002, 1101
OWNER_FIRST, OWNER_LAST = 300001, 399999
EXT4, OVERLAYFS = 0xEF53, 0x794C7630
MARKER, LOCK = ".layout.json", ".layout.lock"
SPLIT = "owner-split"
LAYOUT, CONSENTS_DONE = 2, "done"  # tinyassets.storage_layout.LAYOUT, platform_state_move
BROKER_DIR, IDENTITY_DB = ".broker", "owner-identities.db"
LEDGER, PROXY = "outbound.db", ".outbound-proxy"
SIDECARS = (LEDGER + "-wal", LEDGER + "-shm", LEDGER + "-journal")
EGRESS = (LEDGER, *SIDECARS, PROXY)
DAEMON_DB = ".tinyassets.db"
# D219: setgid only makes later broker entries inherit 1101; it grants nothing.
SETGID_PLATFORM_DIRS = frozenset({BROKER_DIR, BROKER_DIR + "/" + PROXY})
DELETION_INTENTS = ".role-owner-delete"  # tinyassets.role_owner_tree_deletion.INTENT_DIR
STAGING = ".role-admission"  # role_admission_contract.STAGING
VAULT_NAMES = frozenset({".credentials", ".credential-vault.json", "provider_definitions.json"})
# Hidden center entries are platform metadata (hidden_root_masks), except the
# agent workspace, which the owner's tool cells mount as owner content.
OWNER_HIDDEN = frozenset({".agent-workspace"})
#: Owner-owned directories every center carries (tinyassets/role_center_admission
#: SEED_ENTRIES): an owner cell may write only below these, so a missing one is
#: created 0770 and then labelled as owner content.
SEED_ENTRIES = (".agent-workspace", "previews", "skills", "prompts", "extensions",
                "workflows", "bin", "notes", "wiki")
SIDECAR_SOCKET = re.compile(r"(?:egress-[0-9]+|engine-[0-9]+-[a-f0-9]{12})\.sock")
CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")
ACCESS, DEFAULT = "system.posix_acl_access", "system.posix_acl_default"
# A regular owner work file keeps whatever access ACL it already has.
KEEP = "keep-live-acl"
REFUSE = 2


class MigrationRefused(RuntimeError):
    """A precondition failed; nothing after the refusal was written."""


# --------------------------------------------------------------------------
# Pinned, no-follow filesystem access
# --------------------------------------------------------------------------

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LEAF = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@contextmanager
def _root(data_root):
    path = Path(data_root)
    if not path.is_absolute() or ".." in path.parts:
        raise MigrationRefused("data root must be absolute without parent traversal")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        readable = os.open(".", _DIR, dir_fd=fd)
        try:
            yield readable
        finally:
            os.close(readable)
    finally:
        os.close(fd)


@contextmanager
def _directory(parent, name):
    fd = os.open(name, _DIR, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _stat(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
        return None


def _label(fd):
    info = os.fstat(fd)
    return (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
            _xattr(fd, ACCESS), _xattr(fd, DEFAULT) if stat.S_ISDIR(info.st_mode) else None)


def _acl(owner, named, group=0, *, mask=None, other=0):
    """A POSIX ACL xattr value; ``mask`` defaults to the union of the grants."""
    entries = [(1, owner, 0xFFFFFFFF)]
    entries += [(2, rights, uid) for uid, rights in sorted(named.items())]
    entries += [(4, group, 0xFFFFFFFF)]
    if named:
        if mask is None:
            mask = group
            for rights in named.values():
                mask |= rights
        entries += [(16, mask, 0xFFFFFFFF)]
    entries += [(32, other, 0xFFFFFFFF)]
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def _walk(root):
    """Every entry beneath ``root`` as (path, parent fd, name, stat), parents first.

    Never follows a symlink or leaves the filesystem. A directory that changes
    while it is listed refuses: writers must be stopped.
    """
    device = os.fstat(root).st_dev

    def visit(parent, prefix):
        for name in sorted(os.listdir(parent)):
            path = prefix + name
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if info.st_dev != device:
                raise MigrationRefused(f"cross-filesystem entry: {path}")
            yield path, parent, name, info
            if stat.S_ISDIR(info.st_mode):
                with _directory(parent, name) as child:
                    before = os.fstat(child)
                    if not os.path.samestat(before, info):
                        raise MigrationRefused(f"directory replaced during scan: {path}")
                    yield from visit(child, path + "/")
                    after = os.fstat(child)
                    if (before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_mtime_ns, after.st_ctime_ns):
                        raise MigrationRefused(f"directory changed during scan: {path}")

    yield from visit(root, "")


@contextmanager
def _database_copy(parent, name):
    """Read a stopped SQLite database (and its WAL) through a private copy."""
    with tempfile.TemporaryDirectory(prefix="ta-migrate-") as temporary:
        for entry in (name, name + "-wal", name + "-journal"):
            info = _stat(parent, entry)
            if info is None:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise MigrationRefused(f"database entry is not a regular file: {entry}")
            fd = os.open(entry, _LEAF, dir_fd=parent)
            with os.fdopen(fd, "rb") as source, open(Path(temporary) / entry, "xb") as target:
                while chunk := source.read(1 << 20):
                    target.write(chunk)
        with closing(sqlite3.connect(Path(temporary) / name)) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise MigrationRefused(f"database integrity check failed: {name}")
            yield db


# --------------------------------------------------------------------------
# Discovery: who owns which tree (D64), read-only
# --------------------------------------------------------------------------


def _table(db, name):
    found = db.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    if found is not None and found != ("table",):
        raise MigrationRefused(f"authority is not a stored table: {name}")
    return found is not None


def _admission_log(root):
    """(reservations principal->machine, admit center->principal, retired centers)."""
    reservations, admitted, retired = {}, {}, set()
    broker = _stat(root, BROKER_DIR)
    if broker is None:
        return reservations, admitted, retired
    with _directory(root, BROKER_DIR) as directory:
        if _stat(directory, "state") is None:
            return reservations, admitted, retired
        with _directory(directory, "state") as state:
            if _stat(state, IDENTITY_DB) is None:
                return reservations, admitted, retired
            with _database_copy(state, IDENTITY_DB) as db:
                if _table(db, "owner_identities"):
                    for principal, machine in db.execute(
                            "SELECT principal, machine_id FROM owner_identities"):
                        if (not isinstance(principal, str) or type(machine) is not int
                                or not OWNER_FIRST <= machine <= OWNER_LAST
                                or machine in reservations.values()):
                            raise MigrationRefused("invalid durable identity reservation")
                        reservations[principal] = machine
                if _table(db, "center_admissions"):
                    for event, principal, center in db.execute(
                            "SELECT event, principal, center FROM center_admissions "
                            "ORDER BY generation"):
                        if event == "admit":
                            admitted[center] = principal
                        else:
                            retired.add(center)
    return reservations, admitted, retired


def _discover(root, admitted):
    """Every u-* tree, every legacy universe.json tree, every admitted center
    with a tree, and every root already labelled into the owner range."""
    found = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        labelled = stat.S_ISDIR(info.st_mode) and OWNER_FIRST <= info.st_gid <= OWNER_LAST
        if name.startswith("u-") or name in admitted or labelled:
            if not stat.S_ISDIR(info.st_mode):
                raise MigrationRefused(f"owner root is not a plain directory: {name}")
            found.append(name)
        elif stat.S_ISDIR(info.st_mode):
            with _directory(root, name) as directory:
                if _stat(directory, "universe.json") is not None:
                    found.append(name)
    for name in found:
        if not CENTER.fullmatch(name):
            raise MigrationRefused(f"invalid command center name: {name}")
    return found


def _principals(root, centers):
    """Explicit home bindings; a non-home tree needs exactly one admin.

    No basename, display name or engine-authored manifest confers authority.
    """
    if not centers:
        return {}
    info = _stat(root, DAEMON_DB)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise MigrationRefused("owner trees exist but the authority database is missing")
    result = {}
    with _database_copy(root, DAEMON_DB) as db:
        homes, admins = _table(db, "founder_home"), _table(db, "universe_acl")
        for center in centers:
            candidates = db.execute("SELECT founder_sub FROM founder_home WHERE universe_id=?",
                                    (center,)).fetchall() if homes else []
            if not candidates and admins:
                candidates = db.execute(
                    "SELECT actor_id FROM universe_acl WHERE universe_id=? AND permission='admin'",
                    (center,)).fetchall()
            if len(candidates) != 1:
                raise MigrationRefused(f"missing or ambiguous owner binding: {center}")
            principal = candidates[0][0]
            if (not isinstance(principal, str) or not principal.strip()
                    or principal != principal.strip() or not principal.isprintable()
                    or len(principal) > 512):
                raise MigrationRefused(f"invalid owner principal: {center}")
            result[center] = principal
    return result


def _deletion_intents(root):
    info = _stat(root, DELETION_INTENTS)
    if info is None:
        return set()
    if not stat.S_ISDIR(info.st_mode):
        raise MigrationRefused("deletion intent store is not a directory")
    with _directory(root, DELETION_INTENTS) as intents:
        return {name[:-5] for name in os.listdir(intents)
                if not name.startswith(".") and name.endswith(".json")}


def identities(root):
    """The identity plan: what step 1 writes and the bindings step 3 labels with.

    Allocation mirrors OwnerIdentities.resolve exactly (MAX+1, from 300001),
    over principals in sorted order, so a rerun allocates the same numbers.
    """
    reservations, admitted, retired = _admission_log(root)
    centers = _discover(root, admitted)
    principals = _principals(root, centers)
    pending = _deletion_intents(root)
    for center, principal in principals.items():
        if admitted.get(center, principal) != principal:
            raise MigrationRefused(f"center is admitted to another principal: {center}")
        if center in retired and center not in pending:
            raise MigrationRefused(f"a retired center still has a tree: {center}")
    planned = dict(reservations)
    for principal in sorted(set(principals.values()) - reservations.keys()):
        machine = max(planned.values(), default=OWNER_FIRST - 1) + 1
        if machine > OWNER_LAST:
            raise MigrationRefused("owner identity range exhausted")
        planned[principal] = machine
    admits = sorted((center, principal) for center, principal in principals.items()
                    if center not in admitted and center not in retired)
    missing = sorted(center for center in admitted
                     if center not in principals and center not in retired)
    return dict(principals=principals, reserve=sorted(planned.keys() - reservations.keys()),
                admits=admits, missing=missing, pending=sorted(pending),
                bindings={c: planned[p] for c, p in principals.items()})


# --------------------------------------------------------------------------
# Targets: one pure function of the path, the entry and the bindings
# --------------------------------------------------------------------------


def canonical_root_acl(machine):
    """role_admission_contract.canonical_root_acl: owner r-x, broker --x."""
    return _acl(7, {machine: 5, BROKER: 1}, mask=5)


def target(path, info, bindings, modes):
    """The label (uid, gid, mode, access ACL, default ACL) for one entry.

    Returns ``"skip"`` for an entry the migration leaves alone and ``"remove"``
    for a stale entry it deletes. Anything else refuses.
    """
    parts = path.split("/")
    top, kind = parts[0], stat.S_IFMT(info.st_mode)
    directory, regular = kind == stat.S_IFDIR, kind == stat.S_IFREG
    live = stat.S_IMODE(info.st_mode)
    platform = (DAEMON, DAEMON, 0o700 if directory else 0o600 | (live & 0o111), None, None)
    if path == MARKER or top == STAGING:
        return "skip"  # the marker step writes it; step 3 removes the staging area
    if re.fullmatch(r"\.layout\.json\.[0-9a-f]{16}\.tmp", path):
        return "remove"  # an interrupted marker write
    if kind == stat.S_IFLNK and len(parts) > 1:
        # A link's own label grants nothing and this walk never follows one, so
        # it and its target are left alone (production: workspace links, and
        # provider CLI scratch links under .runtime). A top-level link refuses.
        return "skip"
    if top in bindings:
        machine = bindings[top]
        if len(parts) == 1:
            return (DAEMON, machine, 0o750, canonical_root_acl(machine), None)
        entry = parts[1]
        if entry in OWNER_HIDDEN or not (entry.startswith(".") or entry in VAULT_NAMES):
            _require_plain(path, kind)
            mode = live & 0o777
            if regular:
                return (machine, machine, mode, KEEP, None)
            group = (mode >> 3) & 7
            return (machine, machine, mode,
                    _acl((mode >> 6) & 7, {DAEMON: 7}, group, mask=group, other=mode & 7),
                    _acl(7, {DAEMON: 7}))
        _require_plain(path, kind)
        if entry in VAULT_NAMES:
            return (DAEMON, modes["BROKER_READ_GID"], 0o2750 if directory
                    else modes["VAULT_FILE_MODE"], None, None)
        if entry == ".runtime" and (len(parts) == 2 or parts[2] == "provider-launch-credentials"):
            # Only sealed launch snapshots are made readable to the owner.
            rights = (1 if len(parts) <= 3 else 5) if directory else 4
            mode = (0o700 if directory else 0o400) | (rights << 3)
            return (DAEMON, DAEMON, mode, _acl(mode >> 6 & 7, {machine: rights},
                                               mask=rights), None)
        return platform
    if top == BROKER_DIR:
        if path == BROKER_DIR + "/owner.json":
            return "remove"  # the pre-split broker's identity record
        _require_plain(path, kind)
        if directory:
            return (BROKER, BROKER_GROUP, 0o2700 if path in SETGID_PLATFORM_DIRS else 0o700,
                    None, None)
        return (BROKER, BROKER_GROUP, 0o600, None, None)
    if top == ".consumer_liveness":
        _require_plain(path, kind)
        return (DAEMON, modes["BROKER_READ_GID"], modes["LIVENESS_DIRECTORY_MODE"] if directory
                else modes["LIVENESS_FILE_MODE"], None, None)
    if top == ".universe-sidecars":
        if (kind == stat.S_IFSOCK and len(parts) == 3 and CENTER.fullmatch(parts[1])
                and SIDECAR_SOCKET.fullmatch(parts[2])):
            return "remove"  # a stopped daemon's relay socket
        _require_plain(path, kind)
        if len(parts) == 1:
            return (DAEMON, DAEMON, modes["SIDECAR_PARENT_MODE"], None, None)
        if directory:
            return (DAEMON, modes["WORK_GID"], modes["SIDECAR_DIRECTORY_MODE"], None, None)
        return (DAEMON, modes["WORK_GID"], live & 0o777, None, None)
    if path == LOCK:
        _require_plain(path, kind)
        return (DAEMON, DAEMON, 0o666, None, None)  # every service and host job opens it
    _require_plain(path, kind)
    return platform


def _require_plain(path, kind):
    if kind not in (stat.S_IFDIR, stat.S_IFREG):
        raise MigrationRefused(f"unexpected special file or link: {path}")


def _resolve(wanted, label):
    """Substitute KEEP with the entry's live access ACL."""
    return wanted[:3] + (label[3],) + wanted[4:] if wanted[3] == KEEP else wanted


# --------------------------------------------------------------------------
# Preconditions and --check
# --------------------------------------------------------------------------


def _fs_type(fd):
    buffer = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, buffer) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return ctypes.c_long.from_buffer(buffer).value


def _marker(root):
    info = _stat(root, MARKER)
    if info is None:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise MigrationRefused("layout marker is not a regular file")
    fd = os.open(MARKER, _LEAF, dir_fd=root)
    with os.fdopen(fd, encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise MigrationRefused("layout marker is not a JSON object")
    return document


def preconditions(root, *, snapshot_bytes=None):
    """Everything that must hold before the first write; returns the marker."""
    if os.geteuid() != 0:
        raise MigrationRefused("the migration runs as root (one-shot container)")
    kind = _fs_type(root)
    if kind == OVERLAYFS or kind != EXT4:
        raise MigrationRefused(f"data volume must be ext4, found filesystem {kind:#x}")
    try:
        os.getxattr(root, ACCESS)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise MigrationRefused(f"POSIX ACLs unavailable on the volume: {exc}") from None
    if snapshot_bytes is not None:
        free = os.statvfs(root)
        if free.f_bavail * free.f_frsize <= snapshot_bytes:
            raise MigrationRefused("free space is not above the snapshot size")
    document = _marker(root)
    if document is None:
        if set(os.listdir(root)) - {LOCK}:
            raise MigrationRefused("volume has data but no layout marker; start the "
                                   "previous image once so it writes one")
    elif (document.get("layout") != LAYOUT or document.get("state") != "stable"
            or (document.get("moves") or {}).get("consents_outside_command_centers")
            != CONSENTS_DONE or document.get("split", SPLIT) != SPLIT):
        raise MigrationRefused(f"layout marker is not a stable layout {LAYOUT}: {document}")
    return document


def _scan(root, bindings, modes):
    """Read-only: every entry's target, the inode class check and the uid rule."""
    rows, inodes = [], {}
    for path, _parent, _name, info in _walk(root):
        wanted = target(path, info, bindings, modes)
        rows.append((path, info, wanted))
        if not isinstance(wanted, tuple):
            continue
        # uid 0 is only ever this migration's own partial write (or a host job's
        # lock file); the app never runs as root.
        if info.st_uid not in (0, DAEMON, wanted[0]):
            raise MigrationRefused(f"entry is neither uid {DAEMON} nor at its target: "
                                   f"{path} uid {info.st_uid}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            inodes.setdefault(info.st_ino, []).append((path, info, wanted))
    for names in inodes.values():
        # D61: reachability is ownership. Every name of a multi-link inode lies
        # in one class (one owner tree or one platform area) with one target.
        if len({path.split("/")[0] for path, _, _ in names}) != 1:
            raise MigrationRefused("an inode is reachable from two owners or areas: "
                                   + ", ".join(path for path, _, _ in names))
        if len({wanted for _, _, wanted in names}) != 1:
            raise MigrationRefused(f"an inode's names need different labels: {names[0][0]}")
        if names[0][1].st_nlink != len(names):
            raise MigrationRefused(f"an inode has names outside the volume: {names[0][0]}")
    return rows


def _egress_plan(root):
    broker = _stat(root, BROKER_DIR)
    moves = []
    with ExitStack() as stack:
        directory = stack.enter_context(_directory(root, BROKER_DIR)) if broker else None
        for name in EGRESS:
            old = _stat(root, name)
            new = _stat(directory, name) if directory is not None else None
            if old is not None and new is not None:
                raise MigrationRefused(f"egress state exists in both places: {name}")
            if old is not None:
                moves.append(name)
    return moves


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


def _squash(sql):
    return "".join((sql or "").replace(" IF NOT EXISTS", "").split()).lower()


def _accounting_facts(db):
    """Typed row digests under a static schema allowlist; stored DDL never runs."""
    index = db.execute("SELECT name, type, sql FROM sqlite_master WHERE lower(name)=?",
                       ("agent_request_attempt_day",)).fetchone()
    if index is not None and (index[:2] != ("agent_request_attempt_day", "index")
                              or _squash(index[2]) != _squash(ACCOUNTING_INDEX)):
        raise MigrationRefused("unknown accounting index schema")
    result = {}
    for table, schema in zip(ACCOUNTING_TABLES, ACCOUNTING_SCHEMA):
        row = db.execute("SELECT type, sql, name FROM sqlite_master WHERE lower(name)=?",
                         (table,)).fetchone()
        if row is None:
            continue
        if row[0] != "table" or row[2] != table or _squash(row[1]) != _squash(schema):
            raise MigrationRefused(f"unknown accounting schema: {table}")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                      (table,)).fetchone():
            raise MigrationRefused(f"accounting trigger is not migratable: {table}")
        keys = sorted((r[5], r[1]) for r in db.execute(f"PRAGMA table_info({table})") if r[5])
        digest, count = hashlib.sha256(), 0
        for values in db.execute(f"SELECT * FROM {table} ORDER BY "
                                 + ",".join(key for _, key in keys)):
            encoded = pickle.dumps(tuple(values), protocol=4)
            digest.update(len(encoded).to_bytes(8, "big") + encoded)
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def _accounting_plan(root):
    """'move', 'drop' (already copied) or None; refuses a diverged pair."""
    if _stat(root, DAEMON_DB) is None:
        return None
    with _database_copy(root, DAEMON_DB) as db:
        source = _accounting_facts(db)
    if not source:
        return None
    ledger = {}
    if _stat(root, BROKER_DIR) is not None:
        with _directory(root, BROKER_DIR) as broker:
            if _stat(broker, LEDGER) is not None:
                with _database_copy(broker, LEDGER) as db:
                    ledger = _accounting_facts(db)
    if not ledger and _stat(root, LEDGER) is not None:
        with _database_copy(root, LEDGER) as db:
            ledger = _accounting_facts(db)
    if not ledger:
        return "move"
    if ledger == source:
        return "drop"
    raise MigrationRefused("accounting tables exist in both stores with different rows")


def check(root, modes):
    """Every precondition and every entry not at its target (read-only)."""
    document = preconditions(root)
    plan = identities(root)
    diffs = [f"reserve identity: {p}" for p in plan["reserve"]]
    diffs += [f"admit: {c} -> {p}" for c, p in plan["admits"]]
    diffs += [f"move egress: {name}" for name in _egress_plan(root)]
    accounting = _accounting_plan(root)
    if accounting:
        diffs.append(f"accounting: {accounting}")
    if _stat(root, STAGING) is not None:
        diffs.append(f"remove: {STAGING}")
    for center in plan["bindings"]:
        with _directory(root, center) as directory:
            diffs += [f"create: {center}/{name}" for name in SEED_ENTRIES
                      if _stat(directory, name) is None]
    for path, info, wanted in _scan(root, plan["bindings"], modes):
        if wanted == "remove":
            diffs.append(f"remove: {path}")
        elif isinstance(wanted, tuple):
            actual = _live_label(root, path)
            wanted = _resolve(wanted, actual)
            if actual != wanted:
                diffs.append(f"label: {path} {_show(actual)} -> {_show(wanted)}")
    if _label(root) != (DAEMON, DAEMON, 0o755, None, None):
        diffs.append("label: data root")
    if document is None or document.get("split") != SPLIT:
        diffs.append("marker: missing owner-split")
    return plan, diffs


@contextmanager
def _entry(root, path):
    """(pinned parent fd, leaf name) for a relative path, walked without following."""
    parent_path, _, name = path.rpartition("/")
    with ExitStack() as stack:
        parent = root
        for part in parent_path.split("/") if parent_path else ():
            parent = stack.enter_context(_directory(parent, part))
        yield parent, name


@contextmanager
def _opened(root, path, info=None):
    """A no-follow descriptor on ``path``; refuses if it is not the scanned inode."""
    with _entry(root, path) as (parent, name):
        fd = os.open(name, _LEAF, dir_fd=parent)
        try:
            if info is not None and not os.path.samestat(os.fstat(fd), info):
                raise MigrationRefused(f"entry replaced since the scan: {path}")
            yield fd
        finally:
            os.close(fd)


def _live_label(root, path):
    with _opened(root, path) as fd:
        return _label(fd)


def _show(label):
    uid, gid, mode, access, default = label
    return (f"{uid}:{gid} {mode:04o}" + (" acl" if access else "")
            + (" default-acl" if default else ""))


# --------------------------------------------------------------------------
# Step 1: identities
# --------------------------------------------------------------------------

# Byte-for-byte the DDL tinyassets.broker.owner_identities.OwnerIdentities runs
# (tests/test_role_migrate.py asserts sqlite_master parity).
IDENTITY_DDL = (
    "CREATE TABLE IF NOT EXISTS owner_identities ("
    "principal TEXT PRIMARY KEY NOT NULL, "
    "machine_id INTEGER NOT NULL UNIQUE "
    "CHECK(machine_id BETWEEN 300001 AND 399999))",
    *(f"CREATE TRIGGER IF NOT EXISTS no_identity_{op.lower()} "
      f"BEFORE {op} ON owner_identities BEGIN "
      "SELECT RAISE(ABORT, 'owner identities are permanent'); END" for op in ("DELETE", "UPDATE")),
    "CREATE TABLE IF NOT EXISTS center_admissions ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT, "
    "event TEXT NOT NULL CHECK (event IN ('admit','retire')), "
    "principal TEXT NOT NULL, center TEXT NOT NULL, "
    "machine INTEGER NOT NULL CHECK(machine BETWEEN 300001 AND 399999), "
    "UNIQUE(center, event))",
    *(f"CREATE TRIGGER IF NOT EXISTS no_admission_{op.lower()} "
      f"BEFORE {op} ON center_admissions BEGIN "
      "SELECT RAISE(ABORT, 'center admissions are append-only'); END"
      for op in ("DELETE", "UPDATE")),
)


def _mkdir(parent, name, mode=0o700):
    if _stat(parent, name) is None:
        os.mkdir(name, mode, dir_fd=parent)
        os.chmod(name, mode, dir_fd=parent)  # the umask must not narrow an owner seed
        os.fsync(parent)


def step_identities(root, plan):
    """One transaction: missing reservations, then missing admit rows (DA1)."""
    _mkdir(root, BROKER_DIR)
    with _directory(root, BROKER_DIR) as broker:
        _mkdir(broker, "state")
        path = f"/proc/self/fd/{broker}/state/{IDENTITY_DB}"
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            for statement in IDENTITY_DDL:
                db.execute(statement)
            for principal in sorted(set(plan["principals"].values())):
                if db.execute("SELECT 1 FROM owner_identities WHERE principal=?",
                              (principal,)).fetchone() is None:
                    maximum = db.execute("SELECT MAX(machine_id) FROM owner_identities"
                                         ).fetchone()[0]
                    db.execute("INSERT INTO owner_identities VALUES (?, ?)",
                               (principal, OWNER_FIRST if maximum is None else maximum + 1))
            for center, principal in plan["admits"]:
                if db.execute("SELECT 1 FROM center_admissions WHERE center=?",
                              (center,)).fetchone() is None:
                    machine = db.execute("SELECT machine_id FROM owner_identities "
                                         "WHERE principal=?", (principal,)).fetchone()[0]
                    db.execute("INSERT INTO center_admissions (event, principal, center, "
                               "machine) VALUES ('admit', ?, ?, ?)", (principal, center, machine))
            db.execute("COMMIT")
    reservations, _, _ = _admission_log(root)
    for center, principal in plan["principals"].items():
        if reservations.get(principal) != plan["bindings"][center]:
            raise MigrationRefused("identity reservation readback differs from the plan")


# --------------------------------------------------------------------------
# Step 2: egress and accounting (D11/D12)
# --------------------------------------------------------------------------


def _rename_noreplace(source, destination, name):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
    if rename(source, os.fsencode(name), destination, os.fsencode(name), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)
    os.fsync(destination)
    os.fsync(source)


def _settle(parent, name):
    """Checkpoint and close as the last connection, so SQLite removes its WAL and
    shared-memory files: an interrupted run leaves none behind for the next."""
    if _stat(parent, name) is None:
        return
    path = f"/proc/self/fd/{parent}/{name}"
    with closing(sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0)) as db:
        if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
            raise MigrationRefused(f"busy WAL; a writer has not stopped: {name}")


def step_egress(root):
    with _directory(root, BROKER_DIR) as broker:
        try:
            _egress(root, broker)
        finally:
            _settle(root, DAEMON_DB)
            _settle(broker, LEDGER)


def _egress(root, broker):
    if _stat(root, LEDGER) is not None:
        # SQLite needs a pathname for its sidecars; the pinned parent holds.
        path = f"/proc/self/fd/{root}/{LEDGER}"
        with closing(sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0)) as db:
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise MigrationRefused("busy ledger WAL; a writer has not stopped")
    for name in EGRESS:
        if _stat(root, name) is not None:
            if _stat(broker, name) is not None:
                raise MigrationRefused(f"egress state exists in both places: {name}")
            _rename_noreplace(root, broker, name)
    plan = _accounting_plan(root)
    if plan is None:
        return
    source = f"/proc/self/fd/{root}/{DAEMON_DB}"
    ledger = f"/proc/self/fd/{broker}/{LEDGER}"
    with closing(sqlite3.connect(f"file:{source}?mode=rw", uri=True, timeout=0,
                                 isolation_level=None)) as src:
        src.execute("PRAGMA synchronous=FULL")
        src.execute("BEGIN IMMEDIATE")
        facts = _accounting_facts(src)
        if plan == "move":
            with closing(sqlite3.connect(f"file:{ledger}?mode=rwc", uri=True, timeout=0,
                                         isolation_level=None)) as dst:
                dst.execute("PRAGMA synchronous=FULL")
                dst.execute("BEGIN IMMEDIATE")
                for table, schema in zip(ACCOUNTING_TABLES, ACCOUNTING_SCHEMA):
                    if table not in facts:
                        continue
                    dst.execute(schema)
                    width = len(src.execute(f"PRAGMA table_info({table})").fetchall())
                    dst.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * width)})",
                                    src.execute(f"SELECT * FROM {table}"))
                if "agent_request_attempts" in facts:
                    dst.execute(ACCOUNTING_INDEX)
                if _accounting_facts(dst) != facts:
                    raise MigrationRefused("ledger accounting verification failed")
                dst.execute("COMMIT")
        with closing(sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)) as dst:
            if _accounting_facts(dst) != facts:
                raise MigrationRefused("committed ledger accounting differs")
        for table in facts:
            src.execute(f"DROP TABLE {table}")
        src.execute("COMMIT")


# --------------------------------------------------------------------------
# Step 3: labels
# --------------------------------------------------------------------------


def _apply(fd, wanted):
    """Set one pinned entry's label; only differing fields change."""
    wanted = _resolve(wanted, _label(fd))
    if _label(fd) == wanted:
        return False
    uid, gid, mode, access, default = wanted
    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
    for name, value in ((DEFAULT, default), (ACCESS, access)):
        if name == DEFAULT and not directory:
            continue
        if _xattr(fd, name) != value:
            if value is None:
                os.removexattr(fd, name)
            else:
                os.setxattr(fd, name, value)
    if mode & stat.S_ISGID and os.fstat(fd).st_gid != 0:
        # Without FSETID, chmod keeps S_ISGID only for a group the caller is
        # in. Root is in group 0; a later directory chown keeps the bit.
        os.fchown(fd, -1, 0)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        os.fchmod(fd, mode)
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.fchown(fd, uid, gid)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        os.fchmod(fd, mode)  # a regular file's chown clears its set-id bits
    if _label(fd) != wanted:
        raise MigrationRefused(f"label readback differs: {_show(_label(fd))} != {_show(wanted)}")
    os.fsync(fd)
    return True


def _remove_tree(parent, name):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        with _directory(parent, name) as directory:
            for entry in os.listdir(directory):
                _remove_tree(directory, entry)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def step_labels(root, bindings, modes):
    if _stat(root, STAGING) is not None:
        _remove_tree(root, STAGING)  # DA4: unpublished admission remnants
        os.fsync(root)
    for center in sorted(bindings):
        with _directory(root, center) as directory:
            for name in SEED_ENTRIES:
                _mkdir(directory, name, 0o770)
    roots = []
    changed = 0
    entries = [(path, info, target(path, info, bindings, modes))
               for path, _parent, _name, info in _walk(root)]
    for path, info, wanted in entries:
        if wanted == "skip":
            continue
        if wanted == "remove":
            with _entry(root, path) as (parent, name):
                if not os.path.samestat(os.stat(name, dir_fd=parent, follow_symlinks=False),
                                        info):
                    raise MigrationRefused(f"entry replaced since the scan: {path}")
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            changed += 1
            continue
        if path in bindings:
            roots.append((path, info, wanted))  # last: a half-labelled tree never looks admitted
            continue
        with _opened(root, path, info) as fd:
            changed += _apply(fd, wanted)
    for path, info, wanted in roots:
        with _opened(root, path, info) as fd:
            changed += _apply(fd, wanted)
    changed += _apply(root, (DAEMON, DAEMON, 0o755, None, None))
    return changed


# --------------------------------------------------------------------------
# Step 4: marker, and the census
# --------------------------------------------------------------------------


def step_marker(root, document, snapshot):
    if document is not None and document.get("split") == SPLIT:
        return False
    base = document or {"layout": LAYOUT, "state": "stable",
                        "moves": {"consents_outside_command_centers": CONSENTS_DONE}}
    marked = {**base, "split": SPLIT, "snapshot": snapshot,
              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    temporary = ".layout.json." + secrets.token_hex(8) + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                 dir_fd=root)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(marked, handle, sort_keys=True)
        handle.flush()
        os.fchown(handle.fileno(), DAEMON, DAEMON)
        os.fchmod(handle.fileno(), 0o644)  # PID1 reads it as root without DAC_OVERRIDE
        os.fsync(handle.fileno())
    os.replace(temporary, MARKER, src_dir_fd=root, dst_dir_fd=root)
    os.fsync(root)
    return True


def manifest(root):
    """(path, type, uid, gid, mode, ACLs, sha256) for every entry, sorted.

    The marker's ``at`` timestamp is the one field a rerun may legitimately
    differ in; it is left out of the marker's digest.
    """
    lines = []
    for path, parent, name, info in _walk(root):
        kind = stat.S_IFMT(info.st_mode)
        digest = ""
        if kind == stat.S_IFLNK:
            digest = hashlib.sha256(os.fsencode(os.readlink(name, dir_fd=parent))).hexdigest()
            access = default = None
        elif kind not in (stat.S_IFREG, stat.S_IFDIR):
            access = default = None  # a socket or FIFO is never opened
        else:
            fd = os.open(name, _LEAF, dir_fd=parent)
            try:
                _, _, _, access, default = _label(fd)
                if kind == stat.S_IFREG:
                    with os.fdopen(os.dup(fd), "rb") as handle:
                        data = handle.read()
                    if path == MARKER:
                        data = json.dumps({k: v for k, v in json.loads(data).items()
                                           if k != "at"}, sort_keys=True).encode()
                    digest = hashlib.sha256(data).hexdigest()
            finally:
                os.close(fd)
        lines.append("\t".join((path, f"{kind:o}", str(info.st_uid), str(info.st_gid),
                                f"{stat.S_IMODE(info.st_mode):04o}", (access or b"").hex(),
                                (default or b"").hex(), digest)))
    info = os.fstat(root)
    lines.append("\t".join((".", "root", str(info.st_uid), str(info.st_gid),
                            f"{stat.S_IMODE(info.st_mode):04o}", "", "", "")))
    return lines


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _lock(root):
    """Exclusive, non-blocking: every service holds this shared for life."""
    import fcntl

    info = _stat(root, LOCK)
    if info is None:
        if os.listdir(root):
            raise MigrationRefused("volume has data but no layout lock")
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666,
                     dir_fd=root)
    else:
        fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise MigrationRefused("a process still holds the data volume") from None
    return fd


def migrate(data_root, snapshot, *, snapshot_bytes, modes):
    """Steps 1-4 under the exclusive layout lock; returns a short report."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", snapshot or ""):
        raise MigrationRefused("--snapshot must name the verified backup")
    with _root(data_root) as root:
        lock = _lock(root)
        try:
            document = preconditions(root, snapshot_bytes=snapshot_bytes)
            plan = identities(root)
            _egress_plan(root)
            _accounting_plan(root)
            _scan(root, plan["bindings"], modes)
            for center in plan["missing"]:
                print(f"ALARM: admitted command center {center} has no tree; it stays unbound",
                      file=sys.stderr)
            step_identities(root, plan)
            step_egress(root)
            changed = step_labels(root, plan["bindings"], modes)
            marked = step_marker(root, document, snapshot)
            return dict(centers=len(plan["bindings"]), reserved=len(plan["reserve"]),
                        admitted=len(plan["admits"]), changed=changed, marked=marked)
        finally:
            os.close(lock)


def load_modes(app):
    return runpy.run_path(str(Path(app) / "tinyassets" / "role_modes.py"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ta-migrate")
    parser.add_argument("--data-root", default="/data")
    parser.add_argument("--app", default="/app")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--manifest", action="store_true")
    mode.add_argument("--snapshot")
    parser.add_argument("--snapshot-bytes", type=int)
    args = parser.parse_args(argv)
    modes = load_modes(args.app)
    try:
        if args.manifest:
            with _root(args.data_root) as root:
                lines = manifest(root)
            text = "\n".join(lines) + "\n"
            sys.stdout.write(text)
            print(f"manifest: {len(lines) - 1} names sha256 "
                  f"{hashlib.sha256(text.encode()).hexdigest()}", file=sys.stderr)
            return 0
        if args.check:
            with _root(args.data_root) as root:
                lock = _lock(root) if _stat(root, LOCK) is not None else None
                try:
                    plan, diffs = check(root, modes)
                finally:
                    if lock is not None:
                        os.close(lock)
            for line in diffs:
                print(line)
            print(f"check: {len(plan['bindings'])} centers, {len(diffs)} diffs",
                  file=sys.stderr)
            return 1 if diffs else 0
        if args.snapshot_bytes is None:
            parser.error("--snapshot needs --snapshot-bytes (the snapshot's size)")
        report = migrate(args.data_root, args.snapshot, snapshot_bytes=args.snapshot_bytes,
                         modes=modes)
        print(json.dumps(report, sort_keys=True))
        return 0
    except MigrationRefused as exc:
        print(f"migration refused: {exc}", file=sys.stderr)
        return REFUSE


if __name__ == "__main__":
    sys.exit(main())
