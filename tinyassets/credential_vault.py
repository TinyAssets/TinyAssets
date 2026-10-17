"""Per-universe credential vault helpers.

The vault stores credentials that are scoped to one universe directory. Public
state and run evidence should reference only summaries; resolver helpers return
secret values only to daemon-side effectors/providers that need them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

VAULT_FILENAME = ".credential-vault.json"
CREDENTIAL_ARTIFACT_DIR = ".credentials"
VALID_CREDENTIAL_TYPES = frozenset(
    # "http" is the general outbound-connection credential (channel-agnostic
    # outbound): a single token/secret for a user-declared HTTP connection,
    # resolved by connection id via `outbound_connections._GeneralVaultCredentialResolver`.
    {"social", "vcs", "http"}
)

#: When the secret NOW STORED in an http record was stored. There is no expiry
#: warning yet -- a key that died on the provider side was first noticed by a
#: failed run -- and this is the field one needs.
HTTP_DEPOSITED_AT = "deposited_at"


def http_credential_record(*, destination: str, token: str) -> dict[str, Any]:
    """The ONE shape of an http credential record, stamped with its write time.

    Three paths put a secret in an http slot: the owner's deposit
    (``api.http_connection.connect_http``), a rotation (``rotate_http``), and an
    oauth2 refresh (``connection_oauth.tokens``). ``_merge_single_record``
    REPLACES the whole credential slot, so a field only one
    of them wrote would silently disappear on the next write by another. One
    builder, so ``deposited_at`` means the same thing whichever path stored the
    secret that is there now.

    The token is passed straight through and never inspected, logged or returned.
    """
    from datetime import datetime, timezone

    return {
        "credential_type": "http",
        "service": destination,
        "destination": destination,
        "token": token,
        HTTP_DEPOSITED_AT: datetime.now(timezone.utc).isoformat(),
    }


def credential_vault_path(universe_dir: str | Path) -> Path:
    """Return the vault file path for *universe_dir*."""
    return _credential_root(Path(universe_dir)) / VAULT_FILENAME


def _credential_root(universe: Path) -> Path:
    """Platform custody of a command center's credentials: outside its box."""
    from tinyassets.owner_runtime import current

    if current() is not None:
        raise PermissionError("raw credential custody is unavailable in owner mode")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", universe.name):
        raise ValueError("invalid credential command center")
    return universe.parent / ".platform" / "credential-vaults" / universe.name


def _prepare_credential_root(universe: Path) -> Path:
    root = _credential_root(universe)
    from tinyassets.boxes.layout import BROKER_READ_GID

    # The guest's XFS tree is never used as a parent for a credential write.
    # All components here are daemon owned; the broker group can traverse.
    platform = root.parent.parent
    if not platform.exists():
        platform.mkdir(mode=0o711)
    if platform.is_symlink() or not platform.is_dir():
        raise PermissionError("credential platform parent is invalid")
    for directory in (root.parent, root):
        if directory.is_symlink():
            raise PermissionError("credential custody parent is a link")
        if not directory.exists():
            directory.mkdir(mode=0o710)
            if os.name != "nt" and (BROKER_READ_GID in os.getgroups() or os.geteuid() == 0):
                os.chown(directory, -1, BROKER_READ_GID)
        info = directory.stat()
        if (not stat.S_ISDIR(info.st_mode) or
                (os.name != "nt" and info.st_uid != os.geteuid()) or
                stat.S_IMODE(info.st_mode) & 0o007):
            raise PermissionError("credential custody parent is not private")
    return root


def vault_exists(universe_dir: str | Path | None) -> bool:
    """Return True when a vault file exists for *universe_dir*."""
    return universe_dir is not None and credential_vault_path(universe_dir).is_file()


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _as_path(value: Any, universe_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = universe_dir / candidate
    root = _credential_root(universe_dir)
    if root != universe_dir:
        try:
            candidate = root / candidate.relative_to(universe_dir)
        except ValueError:
            pass
    return candidate


def _secret_artifact_dir(universe_dir: Path, service: str) -> Path:
    service_part = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-"
        for ch in service.strip().lower()
    ) or "credential"
    target = _prepare_credential_root(universe_dir) / CREDENTIAL_ARTIFACT_DIR / service_part
    target.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(target.parent, 0o700)
    _chmod_best_effort(target, 0o700)
    return target


def _normalize_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("credential entries must be JSON objects")
    record = dict(raw)
    credential_type = record.get("credential_type")
    if not isinstance(credential_type, str) or not credential_type.strip():
        raise ValueError("credential_type is required")
    normalized_type = credential_type.strip()
    if normalized_type not in VALID_CREDENTIAL_TYPES:
        # The rejected value is NOT echoed. It is attacker- or typo-supplied
        # vault content, and a reviewer put a live token in this field and read
        # it back out of the exception. Naming the allowed set is enough to fix
        # a real mistake.
        allowed = ", ".join(sorted(VALID_CREDENTIAL_TYPES))
        raise ValueError(f"unknown credential_type; expected one of: {allowed}")
    record["credential_type"] = normalized_type
    for key in ("service", "provider", "destination", "purpose"):
        if isinstance(record.get(key), str):
            record[key] = record[key].strip()
    return record


def http_deposit_refusal(
    universe_dir: str | Path, *, destination: str, owner_user_id: str,
) -> str:
    """Why an owned http write for ``destination`` would be refused, or ``""``.

    Read-only, and derived from the SAME rows :func:`write_credential_vault`
    compares, so a surface that previews a write cannot disagree with the write
    that follows. There is deliberately no second copy of the rule here — both
    conditions below are the ones the writer raises ``PermissionError`` for.

    It exists because a ROTATION is previewed before the owner is asked to paste
    anything: a record left with no ownership row (deposited before http
    ownership was tracked, or by an owner-less path) is refused by the write, and
    admitting that card would put an unfulfillable tab in front of the owner
    (Codex refute-review, P2 #4).
    """
    from tinyassets.storage import db_path

    universe = Path(universe_dir).resolve(strict=False)
    service = (destination or "").strip().lower()
    owner = (owner_user_id or "").strip()
    if not service or not owner:
        return "incomplete_request"
    conn = sqlite3.connect(db_path(universe.parent), isolation_level=None)
    try:
        _ensure_credential_owner_schema(conn)
        rows = conn.execute(
            "SELECT service, owner_user_id FROM llm_credential_deposit_owners "
            "WHERE universe_id = ?",
            (universe.name,),
        ).fetchall()
    finally:
        conn.close()
    if any(str(row[1]) != owner for row in rows):
        return "foreign_owner"
    owned = {str(row[0]) for row in rows}
    if f"http:{service}" in owned:
        return ""
    on_disk = {
        _service(record)
        for record in load_credential_vault(universe)
        if record.get("credential_type") == "http"
    }
    return "unowned_record" if service in on_disk else ""


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict):
        raw_records = payload.get("credentials", [])
    else:
        raise ValueError("credential vault must be a JSON object or list")
    if not isinstance(raw_records, list):
        raise ValueError("credential vault 'credentials' must be a list")
    return [_normalize_record(item) for item in raw_records]


#: Service names are short lowercase identifiers (slack, github, anthropic...).
#: Anything else is not a name we issued and must not reach a log surface.
_SERVICE_NAME = re.compile(r"\A[a-z][a-z0-9._-]{0,39}\Z")


def _safe_service_name(value: object) -> str:
    """A service name fit to print, or "" — an allow-list, never a scrub."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    return candidate if _SERVICE_NAME.match(candidate) else ""


def _service(record: dict[str, Any]) -> str:
    return str(record.get("service") or record.get("provider") or "").strip().lower()


def _credential_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Return the logical key used for single-record vault upserts."""
    credential_type = str(record["credential_type"])
    service = _service(record)
    if credential_type == "vcs":
        destination = str(record.get("destination") or "").strip()
        return credential_type, service, destination
    return credential_type, service


def _vcs_purposes(record: dict[str, Any]) -> frozenset[str]:
    purpose = record.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        return frozenset({purpose.strip()})
    purposes = record.get("purposes")
    if isinstance(purposes, list):
        return frozenset(
            str(item).strip()
            for item in purposes
            if str(item).strip()
        )
    return frozenset({"write"})


def _credentials_match(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> bool:
    if _credential_key(existing) != _credential_key(incoming):
        return False
    if incoming["credential_type"] != "vcs":
        return True
    return bool(_vcs_purposes(existing) & _vcs_purposes(incoming))


def _ownership_service_keys(records: list[dict[str, Any]]) -> set[str]:
    """Namespaced HTTP slots whose depositor owns subsequent writes."""
    return {
        f"http:{_service(record)}" for record in records
        if record.get("credential_type") == "http" and _service(record)
    }


def _merge_single_record(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> list[dict[str, Any]]:
    matching_indexes = [
        index
        for index, record in enumerate(existing)
        if _credentials_match(record, incoming)
    ]
    if not matching_indexes:
        return [*existing, incoming]

    replacement = incoming

    first_match = matching_indexes[0]
    matching_set = set(matching_indexes)
    return [
        replacement if index == first_match else record
        for index, record in enumerate(existing)
        if index == first_match or index not in matching_set
    ]


def load_credential_vault(universe_dir: str | Path) -> list[dict[str, Any]]:
    """Load and validate the per-universe vault.

    Missing vaults are treated as empty. Malformed vaults raise ValueError so a
    daemon cannot silently grant or lose authority due to a bad secret file.
    """
    path = credential_vault_path(universe_dir)
    if not path.is_file():
        return []
    payload = None
    malformed = ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        # `UnicodeDecodeError.object` is the whole file too — a second channel
        # with the same consequence as JSONDecodeError.doc, found only because
        # a reviewer tried invalid UTF-8 rather than invalid JSON.
        malformed = "an undecodable byte"
    except json.JSONDecodeError as exc:
        # `exc.doc` is the ENTIRE vault file — every token for every service.
        # Chaining it, or interpolating `exc`, hands the whole thing to any
        # traceback, log, or error collector. Keep only the position, and raise
        # after this handler exits so no chain survives (`from None` clears
        # __cause__ but leaves __context__).
        malformed = f"line {exc.lineno} column {exc.colno}"
    if malformed:
        raise ValueError(f"credential vault is not valid JSON at {malformed}")
    return _records_from_payload(payload)


def _prepare_credential_write(
    universe_dir: str | Path,
    credentials: list[dict[str, Any]] | dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate + merge incoming credentials and return ``(records, summary)``
    WITHOUT touching the vault file.

    Against an existing valid vault, a single record is read-modify-write
    upserted into its logical slot and all matching duplicates collapse at their
    first position. Subscription fields merge; other credential types replace
    the whole slot. Two-or-more records replace the stored list exactly, and an
    empty payload clears it. A malformed existing vault blocks a single upsert.

    The returned ``records`` are what the caller persists as the LAST mutation of
    a deposit (after the owner-row DB commit, via ``_persist_credential_vault_file``)
    so the credential file is never visible before its ownership row is committed.
    The only filesystem access here is a read of the existing vault to compute a
    single-record upsert; nothing is written. The summary is non-secret and
    suitable for logs/status surfaces.
    """
    universe = Path(universe_dir)
    records = _records_from_payload(credentials)
    path = credential_vault_path(universe)
    collapsed_credential_count = 0
    dropped_credential_slots: list[dict[str, Any]] = []
    if len(records) == 1 and path.is_file():
        incoming = records[0]
        existing = load_credential_vault(universe)
        matching = [
            record
            for record in existing
            if _credentials_match(record, incoming)
        ]
        collapsed_credential_count = max(0, len(matching) - 1)
        if incoming["credential_type"] == "vcs":
            dropped_purposes = (
                frozenset().union(*(_vcs_purposes(record) for record in matching))
                - _vcs_purposes(incoming)
            )
            if dropped_purposes:
                dropped_credential_slots.append({
                    "credential_type": "vcs",
                    "service": _service(incoming),
                    "destination": str(
                        incoming.get("destination") or ""
                    ).strip(),
                    "purposes": sorted(dropped_purposes),
                })
        records = _merge_single_record(existing, incoming)
    credential_types = sorted({str(r["credential_type"]) for r in records})
    # Sanitised, not echoed. This summary is explicitly "suitable for logs and
    # status surfaces", and `service` is arbitrary vault content — a review put
    # a live token in that field and read it back out of the summary the
    # deposit script prints under the words "nothing above contains a token".
    # Same class as the credential_type echo fixed one round earlier.
    services = sorted(
        {
            name
            for r in records
            if (name := _safe_service_name(r.get("service") or r.get("provider")))
        }
    )
    summary = {
        "path": str(path),
        "credential_count": len(records),
        "credential_types": credential_types,
        "services": services,
        "collapsed_credential_count": collapsed_credential_count,
        "dropped_credential_slots": dropped_credential_slots,
    }
    return records, summary


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
    """Assign the read-only broker group before publishing a replacement inode."""
    import tempfile

    from tinyassets.boxes.layout import BROKER_READ_GID, VAULT_FILE_MODE

    fd, name = tempfile.mkstemp(prefix=".vault-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchown(handle.fileno(), -1, BROKER_READ_GID)
            os.fchmod(handle.fileno(), VAULT_FILE_MODE)
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

    from tinyassets.provider_assignment import provider_assignment_admission

    with provider_assignment_admission().exclusive(universe):
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
            _persist_credential_vault_file(universe, kept)
    return removed


def write_credential_vault(
    universe_dir: str | Path,
    credentials: list[dict[str, Any]] | dict[str, Any],
    *,
    owner_user_id: str | None = None,
    universe_id: str | None = None,
) -> dict[str, Any]:
    """Write while excluding launches and optionally record depositor ownership.

    ``owner_user_id`` is trusted transport state, never a vault-record field.
    """

    from tinyassets.provider_assignment import provider_assignment_admission

    universe = Path(universe_dir).resolve(strict=False)
    owner, uid = _write_identity(universe, owner_user_id, universe_id)

    # Validate the payload BEFORE acquiring the exclusive lock. Entering the lock
    # creates the universe directory AND the admission lock file
    # (provider_assignment.py), so validating first means a malformed deposit
    # (e.g. bad Codex base64) mutates NOTHING — no directory, no lock, no schema,
    # no vault file. Pure, side-effect-free; the merge below re-runs it.
    _records_from_payload(credentials)

    with provider_assignment_admission().exclusive(universe):
        result = _write_credential_vault_locked(universe, credentials, owner, uid)
    # AFTER the lock, never inside it: warming runs discovery on a background
    # thread, and the boundary it crosses forbids a caller holding the
    # admission lock or a SQL transaction across enumeration.
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
    from tinyassets.provider_assignment import provider_assignment_admission

    universe = Path(universe_dir).resolve(strict=False)
    with provider_assignment_admission().exclusive(universe):
        def write(credentials, *, owner_user_id=None, universe_id=None) -> dict:
            owner, uid = _write_identity(universe, owner_user_id, universe_id)
            _records_from_payload(credentials)
            return _write_credential_vault_locked(universe, credentials, owner, uid)

        yield write


def _write_credential_vault_locked(
    universe: Path, credentials: list[dict[str, Any]] | dict[str, Any], owner: str, uid: str,
) -> dict[str, Any]:
    """The write itself; the caller holds the exclusive admission."""
    from tinyassets.storage import db_path
    from tinyassets.storage.current_home import check_principal_not_deleted

    conn = sqlite3.connect(db_path(universe.parent), isolation_level=None)
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
            check_principal_not_deleted(conn, owner)
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


@dataclass(frozen=True, slots=True)
class LLMCredentialCustodyReference:
    """Secret-free credential identity issued by the vault custody owner."""

    reference_id: str
    owner_user_id: str
    universe_id: str
    service: str
    generation: int
    reference_digest: str
    _record_digest: str = field(repr=False)


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_credential_owner_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_credential_deposit_owners (
            universe_id TEXT NOT NULL,
            service TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            PRIMARY KEY (universe_id, service)
        )
        """
    )


def _custody_reference_digest(
    *,
    reference_id: str,
    owner_user_id: str,
    universe_id: str,
    service: str,
    generation: int,
    record_digest: str,
) -> str:
    return _canonical_digest({
        "generation": generation,
        "owner_user_id": owner_user_id,
        "record_digest": record_digest,
        "reference_id": reference_id,
        "schema_version": 1,
        "service": service,
        "universe_id": universe_id,
    })


def _connection_custody_service(connection_id: str) -> str:
    """Namespaced custody key for a broker connection credential."""
    return f"connection:{connection_id.strip()}"


def _connection_grant_record_digest(
    *,
    grant_id: str,
    connection_id: str,
    credential_ref: str,
    owner_user_id: str,
    universe_id: str,
) -> str:
    """Secret-free grant-identity record digest. Binds custody to the exact grant +
    connection + credential reference; NEVER digests the credential material."""
    return _canonical_digest({
        "connection_id": connection_id,
        "credential_ref": credential_ref,
        "grant_id": grant_id,
        "owner_user_id": owner_user_id,
        "schema_version": 1,
        "service": _connection_custody_service(connection_id),
        "universe_id": universe_id,
    })


def _ensure_custody_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_credential_custody (
            reference_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            universe_id TEXT NOT NULL,
            service TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            record_digest TEXT NOT NULL,
            reference_digest TEXT NOT NULL,
            UNIQUE (owner_user_id, universe_id, service)
        )
        """
    )


def adopt_connection_grant_custody(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    universe_id: str,
    grant_id: str,
    connection_id: str,
    credential_ref: str,
) -> LLMCredentialCustodyReference:
    """Adopt/rotate connection-grant custody under an active transaction.

    Mirrors :func:`adopt_connection_grant_custody` exactly (same table, same
    reference-digest computation, same generation-on-drift semantics), but the
    credential is a connection grant — no subscription material is read, and the
    record digest is the secret-free grant identity. The CALLER MUST have already
    validated the grant is owned by ``owner_user_id`` + bound to ``universe_id`` +
    not revoked; this function records custody, it is not the ownership gate.
    """
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise ValueError(
            "connection-grant custody adoption requires an active SQLite transaction"
        )
    owner = owner_user_id.strip()
    uid = universe_id.strip()
    gid = grant_id.strip()
    cid = connection_id.strip()
    ref = credential_ref.strip()
    if not owner or not uid or not gid or not cid or not ref:
        raise ValueError("connection-grant custody root is invalid")
    service = _connection_custody_service(cid)
    record_digest = _connection_grant_record_digest(
        grant_id=gid,
        connection_id=cid,
        credential_ref=ref,
        owner_user_id=owner,
        universe_id=uid,
    )
    _ensure_custody_schema(conn)
    row = conn.execute(
        """
        SELECT reference_id, generation, record_digest, reference_digest
          FROM llm_credential_custody
         WHERE owner_user_id = ? AND universe_id = ? AND service = ?
        """,
        (owner, uid, service),
    ).fetchone()
    if row is None:
        reference_id = f"llm_credential_{secrets.token_hex(16)}"
        generation = 1
    else:
        reference_id = str(row[0])
        if str(row[2]) == record_digest:
            return LLMCredentialCustodyReference(
                reference_id=reference_id,
                owner_user_id=owner,
                universe_id=uid,
                service=service,
                generation=int(row[1]),
                reference_digest=str(row[3]),
                _record_digest=record_digest,
            )
        generation = int(row[1]) + 1
    reference_digest = _custody_reference_digest(
        reference_id=reference_id,
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=generation,
        record_digest=record_digest,
    )
    conn.execute(
        """
        INSERT INTO llm_credential_custody (
            reference_id, owner_user_id, universe_id, service, generation,
            record_digest, reference_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_user_id, universe_id, service) DO UPDATE SET
            generation = excluded.generation,
            record_digest = excluded.record_digest,
            reference_digest = excluded.reference_digest
        """,
        (reference_id, owner, uid, service, generation, record_digest, reference_digest),
    )
    return LLMCredentialCustodyReference(
        reference_id=reference_id,
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=generation,
        reference_digest=reference_digest,
        _record_digest=record_digest,
    )


def current_connection_grant_custody(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    universe_id: str,
    connection_id: str,
) -> LLMCredentialCustodyReference | None:
    """Return the current connection-grant custody reference, or None. Sibling of
    :func:`current_connection_grant_custody` for the connection service key."""
    if not isinstance(conn, sqlite3.Connection):
        raise ValueError("custody read requires a SQLite connection")
    owner = owner_user_id.strip()
    uid = universe_id.strip()
    service = _connection_custody_service(connection_id)
    _ensure_custody_schema(conn)
    row = conn.execute(
        """
        SELECT reference_id, generation, record_digest, reference_digest
          FROM llm_credential_custody
         WHERE owner_user_id = ? AND universe_id = ? AND service = ?
        """,
        (owner, uid, service),
    ).fetchone()
    if row is None:
        return None
    return LLMCredentialCustodyReference(
        reference_id=str(row[0]),
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=int(row[1]),
        reference_digest=str(row[3]),
        _record_digest=str(row[2]),
    )


def _secret_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    b64 = record.get("token_b64") or record.get("secret_b64")
    if isinstance(b64, str) and b64.strip():
        decoded = None
        try:
            decoded = base64.b64decode(b64.strip()).decode("utf-8").strip()
        except Exception:  # noqa: BLE001
            # Not chained, and raised outside the handler. A UnicodeDecodeError
            # here carries `.object` — the DECODED credential bytes — so
            # chaining published the very token this was decoding.
            decoded = None
        if decoded is None:
            raise ValueError(f"credential {keys[0]} base64 decode failed")
        return decoded
    return ""


def resolve_slack_token(
    universe_dir: str | Path | None,
    connection_id: str,
) -> str:
    """Return a Slack bot token for one connection, or an empty string.

    The record must be a ``social`` credential for service ``slack`` whose
    ``destination`` is the exact connection id. Scoping to the connection --
    rather than to the universe -- keeps one universe's Slack workspaces
    separable, so a second connection cannot be served with the first one's
    token.

    The caller is responsible for the vault-first / never-fall-through-to-env
    rule; this returns only what the vault holds.
    """
    if universe_dir is None:
        return ""
    wanted = connection_id.strip()
    if not wanted:
        return ""
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) != "slack":
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        return _secret_value(record, "bot_token", "token", "access_token")
    return ""


def resolve_slack_app_token(
    universe_dir: str | Path | None,
    connection_id: str,
) -> str:
    """Return a Slack **app-level** token for one connection, or an empty string.

    A Slack connection needs two different credentials, and they are not
    interchangeable: the bot token (``xoxb-``) posts messages, while the
    app-level token (``xapp-``, scope ``connections:write``) is the only one
    that can open a Socket Mode connection. Both live on the same vault record
    because they come from the same app install.

    Deliberately does NOT fall back to ``bot_token``/``token``. Handing a bot
    token to `apps.connections.open` fails with an opaque Slack error, and a
    silent fallback would turn "the app token was never deposited" into a
    mystery instead of the plain answer it is.
    """
    if universe_dir is None:
        return ""
    wanted = connection_id.strip()
    if not wanted:
        return ""
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) != "slack":
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        return _secret_value(record, "app_token", "app_level_token")
    return ""


def resolve_twitter_credentials(
    universe_dir: str | Path | None,
    destination: str,
) -> dict[str, str]:
    """Return the four OAuth 1.0a values for one X/Twitter destination.

    Mirrors :func:`resolve_slack_token`: the record must be a ``social``
    credential for service ``twitter`` (or ``x``) whose ``destination`` is the
    exact authorized destination. Scoping to the destination — never to the host
    process env — is what closes the cross-universe env hole the legacy
    ``twitter_post`` effector left open (it read ambient ``TWITTER_*``).

    Returns a ``{api_key, api_secret, access_token, access_token_secret}`` mapping
    only when ALL four values are present; otherwise an empty dict. The caller is
    responsible for the vault-first / never-fall-through-to-env rule — this returns
    only what the vault holds, and never echoes a value into caller-visible
    evidence.
    """
    if universe_dir is None:
        return {}
    wanted = destination.strip()
    if not wanted:
        return {}
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) not in ("twitter", "x"):
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        values = {
            "api_key": _secret_value(record, "api_key", "consumer_key"),
            "api_secret": _secret_value(record, "api_secret", "consumer_secret"),
            "access_token": _secret_value(record, "access_token"),
            "access_token_secret": _secret_value(record, "access_token_secret"),
        }
        if all(values.values()):
            return values
        return {}
    return {}


def resolve_universe_from_env(env: dict[str, str] | None = None) -> Path | None:
    """Resolve the active universe path from env, if one is explicitly bound."""
    source = os.environ if env is None else env
    value = source.get("TINYASSETS_UNIVERSE", "").strip()
    return Path(value) if value else None
