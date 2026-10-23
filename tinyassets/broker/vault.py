"""Credential custody writer. Only broker operations may import this module."""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets.credential_vault import (
    _credential_root,
    _prepare_credential_write,
    _records_from_payload,
    credential_vault_path,
    load_credential_vault,
)

logger = logging.getLogger(__name__)


def _prepare_credential_root(namespace: Path) -> Path:
    root = _credential_root(namespace)
    for directory in (namespace.parent.parent, namespace.parent, namespace, root):
        directory.mkdir(mode=0o700, exist_ok=True)
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
    from tinyassets.broker.key_service import seal_document

    _persist_role_vault(path, seal_document(universe, path.name, data.encode()).decode())


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
    namespace: Path, owner_user_id: str | None, universe_id: str | None,
) -> tuple[str, str]:
    from tinyassets.credential_vault import custody_data_root, custody_namespace

    if namespace != custody_namespace(custody_data_root(namespace), owner_user_id):
        raise PermissionError("credential store requires its authenticated owner")
    return owner_user_id, namespace.name


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
    namespace: Path, credentials: list[dict[str, Any]] | dict[str, Any], owner: str, uid: str,
) -> dict[str, Any]:
    from tinyassets.credential_vault import custody_data_root

    check_not_deleted(custody_data_root(namespace), owner)
    records, summary = _prepare_credential_write(namespace, credentials)
    _persist_credential_vault_file(namespace, records)
    return summary


@contextmanager
def vault_lock(universe: Path):
    from tinyassets.broker.credential_refresh import _thread_lock, file_lock

    root = _prepare_credential_root(universe)
    with _thread_lock(str(root)):
        with file_lock(root / ".custody.lock", time.monotonic() + 45):
            yield


def check_not_deleted(data_root, owner):
    from tinyassets.broker.admission import local_read

    local_read(data_root, owner).require_work()


def erase_owner(data_root, principal):
    """Remove this owner's shared custody after admission is withdrawn."""
    from tinyassets.credential_vault import custody_namespace

    namespace = custody_namespace(data_root, principal)
    with vault_lock(namespace):
        credential_vault_path(namespace).unlink(missing_ok=True)
        from tinyassets.broker.subscription_custody import FILENAME

        (_credential_root(namespace) / FILENAME).unlink(missing_ok=True)
        _fsync_directory(_credential_root(namespace))
        from tinyassets.broker.key_service import erase_owner_keys

        erase_owner_keys(data_root, namespace.name)
