"""Credential custody writer. Only broker operations may import this module."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets.credential_vault import (
    _credential_root,
    _ensure_credential_owner_schema,
    _ownership_service_keys,
    _prepare_credential_write,
    _records_from_payload,
    _service,
    credential_vault_path,
    custody_metadata_path,
    load_credential_vault,
)

logger = logging.getLogger(__name__)


def _prepare_credential_root(universe: Path) -> Path:
    root = _credential_root(universe)

    # The guest's XFS tree is never used as a parent for a credential write.
    # At cutover this directory is owned outright by the broker uid.
    platform = root.parent.parent
    if not platform.exists():
        platform.mkdir(mode=0o711)
    if platform.is_symlink() or not platform.is_dir():
        raise PermissionError("credential platform parent is invalid")
    for directory in (root.parent, root):
        if directory.is_symlink():
            raise PermissionError("credential custody parent is a link")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        info = directory.stat()
        if (not stat.S_ISDIR(info.st_mode) or
                (os.name != "nt" and info.st_uid != os.geteuid()) or
                stat.S_IMODE(info.st_mode) & 0o007):
            raise PermissionError("credential custody parent is not private")
    return root


def _fsync_file(path: Path) -> None:
    """fsync a file's contents to disk. May raise OSError.

    Opened ``r+b`` (writable): Windows' ``os.fsync`` requires a handle open for
    writing and raises ``EBADF`` on a read-only one. No bytes are written.
    """
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """fsync a directory so a rename inside it is persisted. May raise OSError.

    No-op on Windows, which has no portable directory-fsync via ``os.open`` here.
    """
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _post_commit_durability(vault_file: Path, universe: Path) -> None:
    """Best-effort durability AFTER the commit point (fsync file + directory).

    Kept as one seam so the caller can treat any failure here as non-fatal — the
    deposit has already taken effect once the vault file is replaced.
    """
    _fsync_file(vault_file)
    _fsync_directory(universe)


def _persist_credential_vault_file(
    universe_dir: str | Path,
    records: list[dict[str, Any]],
) -> None:
    """Atomically replace the vault file with *records*.

    This is the LAST mutation of a deposit — the owner-row DB commit has already
    landed — so a concurrent unlocked reader never observes a credential before
    its ownership row exists.

    The publication inside :func:`_persist_role_vault` is the COMMIT POINT.
    Everything before it may raise, and such a pre-commit failure leaves the
    prior file intact so the caller compensates the owner rows.
    """
    universe = Path(universe_dir)
    _prepare_credential_root(universe)
    path = credential_vault_path(universe)
    data = (
        json.dumps({"schema_version": 1, "credentials": records}, indent=2, sort_keys=True)
        + "\n"
    )
    _persist_role_vault(path, data)


def _persist_role_vault(path: Path, data: str) -> None:
    """Publish a private broker-owned inode."""
    import tempfile


    fd, name = tempfile.mkstemp(prefix=".vault-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        from tinyassets.universe_files import unlink_universe_file

        try:
            unlink_universe_file(
                temporary.anchor, temporary.relative_to(temporary.anchor).as_posix())
        except FileNotFoundError:
            pass
        raise
    # Publication is the existing vault commit point. Never compensate owner
    # rows after it, even if a subsequent durability flush reports a failure.
    try:
        _post_commit_durability(path, path.parent)
    except Exception as exc:  # noqa: BLE001 - committed deposit must not be compensated
        logger.warning("credential vault durability flush failed after commit (%s); "
                       "deposit already took effect", type(exc).__name__)


def _restore_owner_rows(
    conn: sqlite3.Connection,
    universe_id: str,
    prior_rows: list[Any],
) -> None:
    """Compensating transaction: restore the owner rows for *universe_id* to
    *prior_rows*.

    Used only when the owner-row DB change already committed but the subsequent
    vault-file replace failed, so no committed-but-ineffective ownership survives.
    """
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "DELETE FROM llm_credential_deposit_owners WHERE universe_id = ?",
        (universe_id,),
    )
    for row in prior_rows:
        conn.execute(
            """
            INSERT INTO llm_credential_deposit_owners (
                universe_id, service, owner_user_id
            ) VALUES (?, ?, ?)
            """,
            (universe_id, str(row[0]), str(row[1])),
        )
    conn.commit()


def forget_credential(
    universe_dir: str | Path,
    *,
    credential_type: str,
    destination: str,
    before_erase: Callable[[str], None] | None = None,
) -> int:
    """Remove every vault record matching ``(credential_type, destination)``.

    Returns how many records went away; zero is not an error, because "remove
    this" and "it was already gone" are the same outcome for the caller.

    Why this exists at all: :func:`write_credential_vault` MERGES. It deposits
    the records it is given and deliberately leaves untouched ones alone, so
    there was no way to take a credential back — a user who pasted a key,
    including one pasted against a host they did not intend, could not withdraw
    it through any surface they could reach
    (``docs/concerns/2026-08-27-no-reachable-remove-for-http-connections.md``).

    Under the SAME exclusive admission lock a deposit takes, and reusing the
    same atomic writer, so a remove racing a deposit cannot interleave and a
    crash mid-remove leaves the previous vault intact rather than a truncated
    one.

    The ledger rows are the caller's business. This owns only the secret.
    """
    universe = Path(universe_dir).resolve(strict=False)
    wanted_type = (credential_type or "").strip().lower()
    wanted_destination = (destination or "").strip()
    if not wanted_type or not wanted_destination:
        raise ValueError("forget_credential needs a credential_type and destination")


    with vault_lock(universe):
        records = load_credential_vault(universe)
        kept = [
            record
            for record in records
            if not (
                str(record.get("credential_type") or "").strip().lower() == wanted_type
                and str(record.get("destination") or "").strip() == wanted_destination
            )
        ]
        removed = len(records) - len(kept)
        if removed:
            if before_erase is not None:
                for record in records:
                    if record not in kept:
                        before_erase(str(record.get("token") or ""))
            _persist_credential_vault_file(universe, kept)
    return removed


def write_credential_vault(
    universe_dir: str | Path,
    credentials: list[dict[str, Any]] | dict[str, Any],
    *,
    owner_user_id: str | None = None,
    universe_id: str | None = None,
) -> dict[str, Any]:
    """Write under broker custody locks and record depositor ownership.

    ``owner_user_id`` is trusted transport state, never a vault-record field.
    """


    universe = Path(universe_dir).resolve(strict=False)
    owner, uid = _write_identity(universe, owner_user_id, universe_id)

    # Validate the payload BEFORE acquiring the exclusive lock. Entering the lock
    # creates only the broker custody directory and lock, so a malformed deposit
    # (e.g. bad Codex base64) mutates NOTHING — no directory, no lock, no schema,
    # no vault file. Pure, side-effect-free; the merge below re-runs it.
    _records_from_payload(credentials)

    with vault_lock(universe):
        result = _write_credential_vault_locked(universe, credentials, owner, uid)
    return result


def _write_identity(
    universe: Path, owner_user_id: str | None, universe_id: str | None,
) -> tuple[str, str]:
    owner = (owner_user_id or "").strip()
    uid = (universe_id or universe.name).strip()
    if owner_user_id is not None and not owner:
        raise ValueError("credential owner must be a non-empty server principal")
    if uid != universe.name:
        raise ValueError("credential command center does not match its canonical directory")
    return owner, uid


@contextmanager
def exclusive_credential_vault(universe_dir: str | Path) -> Iterator[Callable[..., dict]]:
    """Hold the vault's exclusive admission and yield a writer usable inside it.

    For a writer that must know it CAN persist before it acts: an oauth2
    refresh spends a (possibly single-use) refresh token, so it takes this
    first and writes the rotated token under the same hold. Acquiring can fail
    (a bounded cross-process lock); then nothing has been spent.
    """

    universe = Path(universe_dir).resolve(strict=False)
    with vault_lock(universe):
        def write(credentials, *, owner_user_id=None, universe_id=None) -> dict:
            owner, uid = _write_identity(universe, owner_user_id, universe_id)
            _records_from_payload(credentials)
            return _write_credential_vault_locked(universe, credentials, owner, uid)

        yield write


def _write_credential_vault_locked(
    universe: Path, credentials: list[dict[str, Any]] | dict[str, Any], owner: str, uid: str,
) -> dict[str, Any]:
    """The write itself; the caller holds the exclusive admission."""
    conn = metadata_connection(universe.parent)
    try:
        # The records THIS call is depositing (pre-merge). Ownership is claimed
        # only for these — never for untouched records already in the vault.
        incoming_records = _records_from_payload(credentials)
        _ensure_credential_owner_schema(conn)
        if owner:
            existing = conn.execute(
                """
                SELECT service, owner_user_id
                  FROM llm_credential_deposit_owners
                 WHERE universe_id = ?
                """,
                (uid,),
            ).fetchall()
            if any(str(row[1]) != owner for row in existing):
                raise PermissionError(
                    "credential ownership transfer requires a dedicated flow"
                )
            # Fail-closed for LEGACY unowned http credentials. An http record
            # deposited before http ownership was tracked (or via an owner-less
            # path) has a vault record but NO owner row, so the universe-wide
            # guard above cannot see an owner to compare — a caller could
            # otherwise overwrite the orphan and provision as themselves (Codex
            # review). Refuse to overwrite an existing unowned http slot: it is
            # unprovable whether the caller is the original owner, so recovering
            # such a record needs a dedicated flow, never a silent owned rewrite.
            # Scoped to deposits that actually TOUCH an http slot, so a non-http
            # multi-record replace that recovers a malformed vault still works
            # (it never reads the existing file).
            incoming_http_services = {
                service
                for record in incoming_records
                if record.get("credential_type") == "http"
                and (service := _service(record))
            }
            if incoming_http_services:
                owned_keys = {str(row[0]) for row in existing}
                on_disk_http = {
                    service
                    for record in load_credential_vault(universe)
                    if record.get("credential_type") == "http"
                    and (service := _service(record))
                }
                for service in incoming_http_services:
                    if (
                        service in on_disk_http
                        and f"http:{service}" not in owned_keys
                    ):
                        raise PermissionError(
                            "credential ownership transfer requires a dedicated flow"
                        )

        # Compute the final merged records + summary IN MEMORY. The vault file
        # is the LAST mutation (below), never written before the owner-row
        # commit, so a concurrent unlocked reader can never observe a
        # credential before its ownership row exists.
        final_records, summary = _prepare_credential_write(universe, credentials)
        # Owner-row keys: PRUNE against every ownership-bearing record that
        # SURVIVES the merge (so a row for a vanished record is dropped), but
        # CLAIM ownership only for the records this call actually deposits.
        # Claiming against the whole merged vault would let an unrelated owned
        # deposit silently seize a pre-existing unowned http record (Codex
        # review — the LLM-deposit-first seizure).
        final_owner_keys = _ownership_service_keys(final_records)
        incoming_owner_keys = _ownership_service_keys(incoming_records)

        # Snapshot the prior owner rows so a (rare) file-replace failure AFTER
        # the DB commit can be compensated below.
        prior_owner_rows = conn.execute(
            """
            SELECT service, owner_user_id
              FROM llm_credential_deposit_owners
             WHERE universe_id = ?
            """,
            (uid,),
        ).fetchall()

        # 1. Owner-row DB transaction FIRST, and commit it.
        conn.execute("BEGIN IMMEDIATE")
        if owner:
            # Deletion takes this same admission lock while tombstoning.
            # Keep non-home administrators valid: this is not a home check.
            check_not_deleted(conn, owner)
        placeholders = ",".join("?" for _ in final_owner_keys)
        if final_owner_keys:
            conn.execute(
                f"""
                DELETE FROM llm_credential_deposit_owners
                 WHERE universe_id = ? AND service NOT IN ({placeholders})
                """,
                (uid, *sorted(final_owner_keys)),
            )
        else:
            conn.execute(
                "DELETE FROM llm_credential_deposit_owners WHERE universe_id = ?",
                (uid,),
            )
        if owner:
            for service_name in incoming_owner_keys:
                conn.execute(
                    """
                    INSERT INTO llm_credential_deposit_owners (
                        universe_id, service, owner_user_id
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(universe_id, service) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    (uid, service_name, owner),
                )
        conn.commit()

        # 2. ONLY after the DB commit, atomically replace the vault file.
        #    _persist_credential_vault_file raises ONLY for a pre-commit failure
        #    (the vault file was NOT replaced — Path.replace is atomic — so the
        #    prior file is intact); it never raises once the file is visible.
        try:
            _persist_credential_vault_file(universe, final_records)
        except BaseException as persist_error:
            # 3. Pre-commit failure: the file did not change. Compensate by
            #    restoring the prior owner rows so no committed-but-ineffective
            #    ownership survives, then re-raise.
            try:
                _restore_owner_rows(conn, uid, prior_owner_rows)
            except Exception as compensation_error:  # noqa: BLE001
                # Double failure: the file write failed AND the owner-row
                # commit could not be undone. Fail loud, chaining both — do
                # not swallow. Residual: a committed owner row with no vault
                # file is a benign phantom (the owner cannot act — there is no
                # credential — and a re-deposit reconciles).
                raise compensation_error from persist_error
            raise
        return summary
    finally:
        conn.close()



@contextmanager
def vault_lock(universe: Path):
    from tinyassets.broker.credential_refresh import _thread_lock, file_lock

    root = _prepare_credential_root(universe)
    with _thread_lock(str(root)):
        with file_lock(root / ".custody.lock", time.monotonic() + 45):
            yield


def metadata_connection(data_root):
    path = custody_metadata_path(data_root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    _ensure_credential_owner_schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS deleted_principals (digest TEXT PRIMARY KEY)")
    return conn


def check_not_deleted(conn, owner):
    digest = hashlib.sha256(owner.encode()).hexdigest()
    if conn.execute("SELECT 1 FROM deleted_principals WHERE digest=?", (digest,)).fetchone():
        raise PermissionError("credential owner has been deleted")


def erase_owner(data_root, principal):
    """Tombstone before locking vaults: a delayed writer cannot resurrect custody."""
    conn = metadata_connection(data_root)
    try:
        conn.execute("INSERT OR IGNORE INTO deleted_principals VALUES (?)",
                     (hashlib.sha256(principal.encode()).hexdigest(),))
        centers = [row[0] for row in conn.execute(
            "SELECT DISTINCT universe_id FROM llm_credential_deposit_owners WHERE owner_user_id=?",
            (principal,))]
        for center in centers:
            universe = Path(data_root) / center
            with vault_lock(universe):
                credential_vault_path(universe).unlink(missing_ok=True)
                conn.execute("DELETE FROM llm_credential_deposit_owners WHERE universe_id=?",
                             (center,))
    finally:
        conn.close()
