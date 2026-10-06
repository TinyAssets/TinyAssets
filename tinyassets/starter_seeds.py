"""D10 seed transaction store, called only with an authenticated center binding.

The consumer holds its turn boundary across this context (and index assembly).
No tool accepts a filesystem path or owner identity from model arguments.
All durable state, including candidate and Undo bytes, stays outside /u.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
import time
import uuid
from pathlib import Path

from tinyassets.starter_manifest import SeedManifest, digest, relative_path
from tinyassets.universe_files import (
    open_lock_file,
    read_universe_file,
    unlink_universe_file,
    write_universe_file,
)

_SCHEMA = """
CREATE TABLE seed_binding (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, schema_version INTEGER NOT NULL,
 PRIMARY KEY(owner_id, center_id));
CREATE TABLE seed_blobs (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, blob_id TEXT NOT NULL,
 content BLOB NOT NULL, sha256 TEXT NOT NULL, length INTEGER NOT NULL,
 PRIMARY KEY(owner_id, center_id, blob_id));
CREATE TABLE seed_transactions (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
 bundle_id TEXT NOT NULL, version TEXT NOT NULL, manifest_hash TEXT NOT NULL,
 operation TEXT NOT NULL, request_key TEXT NOT NULL, prior_transaction TEXT,
 phase TEXT NOT NULL, created REAL NOT NULL, committed REAL,
 PRIMARY KEY(owner_id, center_id, transaction_id),
 UNIQUE(owner_id, center_id, request_key),
 FOREIGN KEY(owner_id, center_id, prior_transaction)
 REFERENCES seed_transactions(owner_id, center_id, transaction_id));
CREATE TABLE seed_receipts (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
 version TEXT NOT NULL, manifest_hash TEXT NOT NULL, transaction_id TEXT NOT NULL,
 PRIMARY KEY(owner_id, center_id, bundle_id),
 FOREIGN KEY(owner_id, center_id, transaction_id)
 REFERENCES seed_transactions(owner_id, center_id, transaction_id));
CREATE TABLE seed_paths (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, ever_installed INTEGER NOT NULL,
 installed_version TEXT, installed_hash TEXT, outcome TEXT NOT NULL,
 owner_choice TEXT NOT NULL, tombstone INTEGER NOT NULL, transaction_id TEXT NOT NULL,
 PRIMARY KEY(owner_id, center_id, bundle_id, relative_path),
 UNIQUE(owner_id, center_id, relative_path),
 FOREIGN KEY(owner_id, center_id, transaction_id)
 REFERENCES seed_transactions(owner_id, center_id, transaction_id));
CREATE TABLE seed_transaction_paths (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
 relative_path TEXT NOT NULL, prior_kind TEXT NOT NULL, prior_hash TEXT,
 prior_blob TEXT, target_blob TEXT, target_hash TEXT, action TEXT NOT NULL,
 phase TEXT NOT NULL, outcome TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(owner_id, center_id, transaction_id, relative_path),
 FOREIGN KEY(owner_id, center_id, transaction_id)
 REFERENCES seed_transactions(owner_id, center_id, transaction_id),
 FOREIGN KEY(owner_id, center_id, prior_blob)
 REFERENCES seed_blobs(owner_id, center_id, blob_id),
 FOREIGN KEY(owner_id, center_id, target_blob)
 REFERENCES seed_blobs(owner_id, center_id, blob_id));
CREATE TABLE seed_notices (
 owner_id TEXT NOT NULL, center_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
 version TEXT NOT NULL, payload TEXT NOT NULL, delivered INTEGER NOT NULL,
 notification_key TEXT NOT NULL,
 PRIMARY KEY(owner_id, center_id, bundle_id, version));
"""


def _safe_directory(path: Path) -> None:
    """Daemon-owned sidecars: reject every link/reparse component before mkdir."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            try:
                part.mkdir(mode=0o700)
            except FileExistsError:
                pass  # Another bound operation created it; still validate below.
            info = part.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_reparse_tag", 0)
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise OSError("unsafe seed storage directory")


def _regular_or_absent(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0) or info.st_nlink != 1:
        raise OSError("unsafe seed database or SQLite sidecar")


@contextlib.contextmanager
def seed_boundary(center_root: Path, *, exclusive: bool = False, timeout: float = 5):
    """Exclude seed transactions from jailed tools and owner file writes."""
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    root = Path(center_root).absolute()
    _safe_directory(root)
    sidecar = root.parent / UNIVERSE_SIDECARS_DIR / root.name
    _safe_directory(sidecar)
    fd = open_lock_file(sidecar, "starter-seeds.lock", mode=0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("starter file boundary is busy; retry the turn") from None
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        try:
            yield sidecar
        finally:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def seed_store(center_root: Path, *, owner_id: str, center_id: str):
    """Open a bound store under the canonical root supplied by the platform.

    Caller MUST resolve root/IDs from its authenticated binding, never a tool
    argument. The sidecar is deliberately outside all agent jail mounts.
    """
    root = Path(center_root).absolute()
    if not owner_id or not center_id:
        raise ValueError("authenticated owner and center are required")
    with seed_boundary(root, exclusive=True) as sidecar:
        with _open_store(root, sidecar, owner_id, center_id) as store:
            yield store


@contextlib.contextmanager
def _open_store(root, sidecar, owner_id, center_id):
    conn = None
    try:
        path = sidecar / "starter-seeds.sqlite3"
        for suffix in ("", "-wal", "-shm", "-journal"):
            _regular_or_absent(Path(str(path) + suffix))
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='seed_binding'").fetchone()
        if not exists:
            if conn.execute("SELECT 1 FROM sqlite_master").fetchone():
                raise ValueError("unrecognized seed database; refusing reset")
            conn.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA)
            conn.execute("INSERT INTO seed_binding VALUES (?,?,1)", (owner_id, center_id))
            conn.commit()
        binding = [tuple(row) for row in conn.execute("SELECT * FROM seed_binding")]
        if binding != [(owner_id, center_id, 1)]:
            raise PermissionError("seed database binding or schema mismatch")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        yield SeedStore(root, conn, owner_id, center_id)
    finally:
        if conn is not None:
            conn.close()


@contextlib.contextmanager
def seed_snapshot(center_root: Path, *, owner_id: str, center_id: str):
    """Read a committed owner snapshot without provisioning or waiting on tools."""
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    root = Path(center_root).absolute()
    sidecar = root.parent / UNIVERSE_SIDECARS_DIR / root.name
    for part in (sidecar.parent, sidecar):
        try:
            info = part.lstat()
        except FileNotFoundError:
            yield None
            return
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise OSError("unsafe seed storage directory")
    path = sidecar / "starter-seeds.sqlite3"
    _regular_or_absent(path)
    if not path.exists():
        yield None
        return
    for suffix in ("-wal", "-shm", "-journal"):
        _regular_or_absent(Path(str(path) + suffix))
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        binding = [tuple(row) for row in conn.execute("SELECT * FROM seed_binding")]
        if binding != [(owner_id, center_id, 1)]:
            raise PermissionError("seed database binding or schema mismatch")
        yield SeedStore(root, conn, owner_id, center_id)
    finally:
        conn.close()


class SeedStore:
    def __init__(self, root, connection, owner_id, center_id):
        self.root, self.db = root, connection
        self.scope = (owner_id, center_id)

    def _rows(self, table, where="1", values=()):
        # table/where are internal literals; all reference values are parameters.
        return self.db.execute(
            f"SELECT * FROM {table} WHERE owner_id=? AND center_id=? AND {where}",
            (*self.scope, *values),
        ).fetchall()

    def _one(self, table, where, values):
        rows = self._rows(table, where, values)
        if not rows:
            raise KeyError("seed reference not in this center")
        return rows[0]

    def _insert(self, table, **values):
        values = dict(owner_id=self.scope[0], center_id=self.scope[1], **values)
        self.db.execute(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def _update(self, table, where, params, **values):
        self.db.execute(
            f"UPDATE {table} SET {','.join(k + '=?' for k in values)} "
            f"WHERE owner_id=? AND center_id=? AND {where}",
            (*values.values(), *self.scope, *params),
        )

    def _blob(self, content):
        if content is None:
            return None
        key = str(uuid.uuid4())
        self._insert(
            "seed_blobs", blob_id=key, content=content, sha256=digest(content), length=len(content)
        )
        return key

    def candidate(self, blob_id: str) -> bytes:
        row = self._one("seed_blobs", "blob_id=?", (blob_id,))
        content = bytes(row["content"])
        if digest(content) != row["sha256"] or len(content) != row["length"]:
            raise ValueError("corrupt seed blob")
        return content

    def _observe(self, path):
        try:
            content = read_universe_file(self.root, relative_path(path), max_bytes=1024 * 1024)
            return "regular", digest(content), content
        except FileNotFoundError:
            return "absent", None, None
        except OSError:
            # No target read for links. Distinguish diagnostic without traversing.
            current = self.root
            for component in path.split("/"):
                current /= component
                try:
                    info = current.lstat()
                except OSError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
                    return "linked", None, None
            return "unreadable", None, None

    def install(self, manifest: SeedManifest, *, fresh: bool = False) -> dict:
        """Provision/upgrade, or resume a journal before returning its receipt.

        fresh=True is only for the creation path, before this root is exposed.
        Replaying a committed version never recreates an owner-deleted file.
        """
        self.recover()
        operation = "provision" if fresh else "upgrade"
        key = f"automatic:{manifest.bundle_id}:{manifest.version}"
        prior = self._rows("seed_transactions", "request_key=?", (key,))
        if prior:
            if prior[0]["manifest_hash"] != manifest.sha256:
                raise ValueError("published seed version changed")
            return self.receipt(prior[0]["transaction_id"])
        transaction = str(uuid.uuid4())
        with self.db:
            self._insert(
                "seed_transactions",
                transaction_id=transaction,
                bundle_id=manifest.bundle_id,
                version=manifest.version,
                manifest_hash=manifest.sha256,
                operation=operation,
                request_key=key,
                prior_transaction=None,
                phase="prepared",
                created=time.time(),
                committed=None,
            )
            for file in manifest.files:
                history = self._rows("seed_paths", "relative_path=?", (file.path,))
                old = dict(history[0]) if history else {}
                if old and old["bundle_id"] != manifest.bundle_id:
                    raise ValueError("path already belongs to another seed bundle")
                kind, hash_, content = self._observe(file.path)
                deleted = kind == "absent" and (
                    old.get("ever_installed")
                    or old.get("tombstone")
                    or (not fresh and file.historically_seeded)
                )
                choice = old.get("owner_choice", "automatic")
                automatic = choice == "automatic" and not old.get("tombstone")
                stock = kind == "regular" and hash_ in (
                    file.sha256,
                    old.get("installed_hash"),
                    *file.predecessors,
                )
                action = "preserve"
                if automatic and not deleted:
                    if kind == "absent":
                        action = "create"
                    elif stock:
                        action = "replace"
                outcome = (
                    action
                    if action != "preserve"
                    else ("deleted" if deleted else f"preserved-{kind}")
                )
                result = dict(
                    ever_installed=old.get("ever_installed", 0),
                    installed_version=old.get("installed_version"),
                    installed_hash=old.get("installed_hash"),
                    owner_choice="deleted"
                    if deleted
                    else (
                        choice
                        if choice != "automatic"
                        else "preserved"
                        if action == "preserve"
                        else "automatic"
                    ),
                    tombstone=int(bool(deleted or old.get("tombstone"))),
                )
                self._insert(
                    "seed_transaction_paths",
                    transaction_id=transaction,
                    relative_path=file.path,
                    prior_kind=kind,
                    prior_hash=hash_,
                    prior_blob=self._blob(content),
                    target_blob=self._blob(file.content),
                    target_hash=file.sha256,
                    action=action,
                    phase="prepared",
                    outcome=outcome,
                    result=json.dumps(result),
                )
        return self._apply(transaction)

    def recover(self):
        for row in self._rows("seed_transactions", "phase!='committed'"):
            self._apply(row["transaction_id"])

    def _apply(self, transaction):
        tx = self._one("seed_transactions", "transaction_id=?", (transaction,))
        if tx["phase"] == "committed":
            return self.receipt(transaction)
        with self.db:
            self._update("seed_transactions", "transaction_id=?", (transaction,), phase="applying")
        for row in self._rows("seed_transaction_paths", "transaction_id=?", (transaction,)):
            if row["phase"] == "done":
                # A crash can leave a finished path beside an unfinished sibling.
                # Reconcile it again before publishing the complete receipt.
                if row["outcome"] in ("installed", "removed"):
                    kind, hash_, _ = self._observe(row["relative_path"])
                    matches = (
                        kind == "absent"
                        if row["outcome"] == "removed"
                        else kind == "regular" and hash_ == row["target_hash"]
                    )
                    if not matches:
                        result = json.loads(row["result"])
                        result.update(owner_choice="preserved", tombstone=int(kind == "absent"))
                        with self.db:
                            self._update(
                                "seed_transaction_paths",
                                "transaction_id=? AND relative_path=?",
                                (transaction, row["relative_path"]),
                                outcome="preserved-concurrent-edit",
                                result=json.dumps(result),
                            )
                continue
            action, path = row["action"], row["relative_path"]
            result = json.loads(row["result"])
            outcome = row["outcome"]
            if action != "preserve":
                kind, hash_, _ = self._observe(path)
                # After a crash, exact target bytes reconcile an already-applied write.
                applied = row["phase"] == "applying" and (
                    (action == "remove" and kind == "absent")
                    or (kind == "regular" and hash_ == row["target_hash"])
                )
                matches = (kind, hash_) == (row["prior_kind"], row["prior_hash"])
                if not applied and matches:
                    with self.db:
                        self._update(
                            "seed_transaction_paths",
                            "transaction_id=? AND relative_path=?",
                            (transaction, path),
                            phase="applying",
                        )
                    # The consumer's turn boundary excludes running tools; governed
                    # UI edits use the same soul lock around observation + mutation.
                    from tinyassets.soul_edit import _soul_lock

                    with _soul_lock(self.root):
                        if self._observe(path)[:2] == (kind, hash_):
                            try:
                                if action == "remove":
                                    unlink_universe_file(self.root, path)
                                else:
                                    write_universe_file(
                                        self.root,
                                        path,
                                        self.candidate(row["target_blob"]),
                                        mode="exclusive" if kind == "absent" else "replace",
                                    )
                                applied = True
                            except FileExistsError:
                                pass
                if applied:
                    if tx["operation"] in ("provision", "upgrade", "adopt"):
                        result.update(
                            ever_installed=1,
                            installed_version=tx["version"],
                            installed_hash=row["target_hash"],
                            owner_choice="automatic",
                            tombstone=0,
                        )
                    outcome = "removed" if action == "remove" else "installed"
                else:
                    outcome = "preserved-concurrent-edit"
                    result.update(owner_choice="preserved", tombstone=int(kind == "absent"))
            with self.db:
                self._update(
                    "seed_transaction_paths",
                    "transaction_id=? AND relative_path=?",
                    (transaction, path),
                    phase="done",
                    outcome=outcome,
                    result=json.dumps(result),
                )
        with self.db:
            rows = self._rows("seed_transaction_paths", "transaction_id=?", (transaction,))
            for row in rows:
                result = json.loads(row["result"])
                values = dict(**result, outcome=row["outcome"], transaction_id=transaction)
                where = "bundle_id=? AND relative_path=?"
                params = (tx["bundle_id"], row["relative_path"])
                if self._rows("seed_paths", where, params):
                    self._update("seed_paths", where, params, **values)
                else:
                    self._insert(
                        "seed_paths",
                        bundle_id=tx["bundle_id"],
                        relative_path=row["relative_path"],
                        **values,
                    )
            receipt = dict(
                version=tx["version"], manifest_hash=tx["manifest_hash"], transaction_id=transaction
            )
            if self._rows("seed_receipts", "bundle_id=?", (tx["bundle_id"],)):
                self._update("seed_receipts", "bundle_id=?", (tx["bundle_id"],), **receipt)
            else:
                self._insert("seed_receipts", bundle_id=tx["bundle_id"], **receipt)
            self._update(
                "seed_transactions",
                "transaction_id=?",
                (transaction,),
                phase="committed",
                committed=time.time(),
            )
            notice = self._notice(tx, rows)
            where, params = "bundle_id=? AND version=?", (tx["bundle_id"], tx["version"])
            existing = self._rows("seed_notices", where, params)
            if existing:
                # Offers always retain the immutable install transaction and target
                # blobs. An owner choice adds history; it never replaces candidates.
                original = json.loads(existing[0]["payload"])
                original.setdefault("choices", []).append(notice)
                self._update("seed_notices", where, params, payload=json.dumps(original))
            else:
                self._insert(
                    "seed_notices",
                    bundle_id=tx["bundle_id"],
                    version=tx["version"],
                    payload=json.dumps(notice),
                    delivered=0,
                    notification_key=str(uuid.uuid4()),
                )
        return self.receipt(transaction)

    def _notice(self, tx, rows):
        diagnostics = []
        for row in rows:
            if row["relative_path"] != "AGENTS.md":
                continue
            condition = row["prior_kind"]
            if condition == "regular" and row["prior_hash"] == digest(b""):
                diagnostics.append(
                    "Your instructions file is empty; you were previously running defaults. "
                    "It was kept unchanged."
                )
            elif condition in ("linked", "unreadable"):
                diagnostics.append(
                    f"Your instructions file is {condition}; the previous runtime supplied "
                    "defaults because it could not safely read the file. It was kept unchanged."
                )
        if diagnostics:
            diagnostics.append(
                "The new runtime no longer substitutes defaults. See the actual hook/skill "
                "outcomes below; preserved paths have an offered candidate and their "
                "starter guidance is unavailable until adopted. Edit/delete files or Undo."
            )
        return dict(
            transaction_id=tx["transaction_id"],
            operation=tx["operation"],
            diagnostics=diagnostics,
            paths=[
                dict(path=r["relative_path"], outcome=r["outcome"], candidate=r["target_blob"])
                for r in rows
            ],
        )

    def receipt(self, transaction: str) -> dict:
        tx = dict(self._one("seed_transactions", "transaction_id=?", (transaction,)))
        tx["paths"] = [
            dict(r)
            for r in self._rows(
                "seed_transaction_paths",
                "transaction_id=?",
                (transaction,),
            )
        ]
        return tx

    def notices(self) -> list[dict]:
        return [dict(row, payload=json.loads(row["payload"])) for row in self._rows("seed_notices")]

    def mark_delivered(self, notification_key: str):
        self._one("seed_notices", "notification_key=?", (notification_key,))
        with self.db:
            self._update("seed_notices", "notification_key=?", (notification_key,), delivered=1)

    def undo(self, transaction: str, *, request_key: str) -> dict:
        """Conditional file-only Undo, preserving edits and future owner choices."""
        self.recover()
        prior = self._one("seed_transactions", "transaction_id=?", (transaction,))
        if prior["operation"] not in ("provision", "upgrade", "adopt"):
            raise ValueError("only seed installation transactions can be undone")
        return self._choice(prior, request_key, "undo")

    def adopt(
        self, transaction: str, path: str, *, expected_hash: str | None, request_key: str
    ) -> dict:
        """Explicit adoption is bound to the current hash (None means absence)."""
        self.recover()
        prior = self._one("seed_transactions", "transaction_id=?", (transaction,))
        if prior["operation"] not in ("provision", "upgrade"):
            raise ValueError("adoption requires an original seed installation")
        return self._choice(prior, request_key, "adopt", relative_path(path), expected_hash)

    def _choice(self, prior, request_key, operation, path=None, expected_hash=None):
        if not request_key:
            raise ValueError("owner choice requires a durable request key")
        key = "choice:" + request_key
        old = self._rows("seed_transactions", "request_key=?", (key,))
        if old:
            if (
                old[0]["operation"] != operation
                or old[0]["prior_transaction"] != prior["transaction_id"]
            ):
                raise ValueError("idempotency key reused for a different choice")
            if operation == "adopt":
                prior_paths = self._rows(
                    "seed_transaction_paths",
                    "transaction_id=?",
                    (old[0]["transaction_id"],),
                )
                if (
                    len(prior_paths) != 1
                    or prior_paths[0]["relative_path"] != path
                    or prior_paths[0]["prior_hash"] != expected_hash
                ):
                    raise ValueError("idempotency key reused for a different candidate")
            return self.receipt(old[0]["transaction_id"])
        rows = self._rows("seed_transaction_paths", "transaction_id=?", (prior["transaction_id"],))
        if path is not None:
            rows = [r for r in rows if r["relative_path"] == path]
            if not rows:
                raise KeyError("candidate path not in this transaction")
        transaction = str(uuid.uuid4())
        with self.db:
            self._insert(
                "seed_transactions",
                transaction_id=transaction,
                bundle_id=prior["bundle_id"],
                version=prior["version"],
                manifest_hash=prior["manifest_hash"],
                operation=operation,
                request_key=key,
                prior_transaction=prior["transaction_id"],
                phase="prepared",
                created=time.time(),
                committed=None,
            )
            for row in rows:
                kind, hash_, content = self._observe(row["relative_path"])
                result = dict(
                    self._one(
                        "seed_paths",
                        "bundle_id=? AND relative_path=?",
                        (prior["bundle_id"], row["relative_path"]),
                    )
                )
                result = {
                    k: result[k]
                    for k in (
                        "ever_installed",
                        "installed_version",
                        "installed_hash",
                        "owner_choice",
                        "tombstone",
                    )
                }
                if operation == "adopt":
                    if kind not in ("regular", "absent") or hash_ != expected_hash:
                        raise ValueError("candidate adoption has a stale hash/absence")
                    target = row["target_blob"]
                    action = "create" if kind == "absent" else "replace"
                else:
                    if row["outcome"] != "installed" or row["action"] == "preserve":
                        continue
                    target = row["prior_blob"]
                    action = "replace" if target else "remove"
                    if kind != "regular" or hash_ != row["target_hash"]:
                        action = "preserve"
                    result.update(
                        owner_choice="undo", tombstone=int(target is None or kind == "absent")
                    )
                self._insert(
                    "seed_transaction_paths",
                    transaction_id=transaction,
                    relative_path=row["relative_path"],
                    prior_kind=kind,
                    prior_hash=hash_,
                    prior_blob=self._blob(content),
                    target_blob=target,
                    target_hash=digest(self.candidate(target)) if target else None,
                    action=action,
                    phase="prepared",
                    outcome="preserved-owner-edit",
                    result=json.dumps(result),
                )
        return self._apply(transaction)
