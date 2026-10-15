"""One-job credential transfer between owner custody and an external-agent cell."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import time
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from tinyassets.credential_vault import (
    LLMCredentialCustodyReference,
    carry_llm_subscription_custody,
    cleanup_llm_credential_snapshot,
    current_llm_subscription_custody,
    exclusive_credential_vault,
    llm_subscription_credential_record,
    snapshot_llm_subscription_credential,
)
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

MAX_AUTH_BYTES = 8 * 1024 * 1024
_LOCK_WAIT_SECONDS = 30


class RefreshBusyError(TimeoutError):
    """Another owner job still holds the token-spending lease."""


@dataclass(frozen=True, slots=True)
class Custody:
    universe_dir: Path
    owner_user_id: str
    reference: LLMCredentialCustodyReference

    def reload(self) -> LLMCredentialCustodyReference | None:
        universe = self.universe_dir.resolve(strict=True)
        if (self.reference.owner_user_id != self.owner_user_id
                or self.reference.universe_id != universe.name):
            return None
        with SQLiteProviderWorkAuthorityStore(universe.parent).connection() as conn:
            conn.execute("BEGIN")
            return current_llm_subscription_custody(
                conn, universe_dir=universe, owner_user_id=self.owner_user_id,
                universe_id=universe.name, service=self.reference.service,
            )

    def current(self) -> bool:
        return self.reload() == self.reference


@dataclass(frozen=True, slots=True)
class BoxCustody(Custody):
    """CLI credential custody in the admitted box, with its pin in platform state."""

    binding: object

    def _vault(self):
        from tinyassets.boxes.file_wire import MAX_DATA
        from tinyassets.credential_vault import (
            _canonical_digest,
            _decode_codex_auth_json,
            _records_from_payload,
        )

        self.binding.validate(self.owner_user_id, self.universe_dir)
        provider, handle = self.binding.provider, self.binding.handle
        snapshot = provider.read_many(handle, ["/cc/.credential-vault.json"],
                                      max_total=MAX_DATA)
        raw = snapshot.files.get("/cc/.credential-vault.json")
        if raw is None:
            raise PermissionError("subscription credential is unavailable in owner box")
        try:
            records = _records_from_payload(json.loads(raw))
            matching = [row for row in records if row.get("credential_type") == "llm_subscription"
                        and row.get("service") == self.reference.service]
            if len(matching) != 1:
                raise ValueError
            record = matching[0]
            if self.reference.service != "codex":
                raise ValueError
            encoded = record.get("auth_json_b64")
            if isinstance(encoded, str) and encoded.strip():
                material = _decode_codex_auth_json(encoded)
            else:
                # Only the canonical relative artifact is accepted. A path
                # embedded in an old record is never a host-path authority.
                if any(record.get(key) for key in (
                        "codex_home", "home", "auth_home", "path", "auth_json_path")):
                    raise ValueError
                artifact = provider.read_many(
                    handle, ["/cc/.credentials/codex/auth.json"], max_total=MAX_DATA,
                )
                material = artifact.files.get("/cc/.credentials/codex/auth.json")
                if material is None or artifact.generation != snapshot.generation:
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            raise PermissionError("subscription credential is invalid in owner box") from None
        if provider.committed_generation(handle) != snapshot.generation:
            raise PermissionError("subscription credential changed during box read")
        digest = _canonical_digest({
            "material_digest": "sha256:" + hashlib.sha256(material).hexdigest(),
            "record": record,
        })
        return records, record, material, digest, snapshot.generation

    def reload(self) -> LLMCredentialCustodyReference | None:
        from tinyassets.credential_vault import _subscription_reference_matches

        try:
            _, _, _, digest, _ = self._vault()
        except PermissionError:
            return None
        with SQLiteProviderWorkAuthorityStore(self.universe_dir.parent).connection() as conn:
            owner = conn.execute(
                "SELECT owner_user_id FROM llm_credential_deposit_owners "
                "WHERE universe_id = ? AND service = ?",
                (self.universe_dir.name, self.reference.service),
            ).fetchone()
            if owner is None or owner[0] != self.owner_user_id:
                return None
            row = conn.execute(
                "SELECT reference_id, generation, record_digest, reference_digest "
                "FROM llm_credential_custody WHERE owner_user_id = ? "
                "AND universe_id = ? AND service = ?",
                (self.owner_user_id, self.universe_dir.name, self.reference.service),
            ).fetchone()
        if (row is None or row[2] != digest or not _subscription_reference_matches(
                reference_id=row[0], owner_user_id=self.owner_user_id,
                universe_id=self.universe_dir.name, service=self.reference.service,
                generation=row[1], record_digest=digest, reference_digest=row[3])):
            return None
        return LLMCredentialCustodyReference(
            reference_id=row[0], owner_user_id=self.owner_user_id,
            universe_id=self.universe_dir.name, service=self.reference.service,
            generation=row[1], reference_digest=row[3], _record_digest=digest,
        )

    def materialize(self, auth: dict) -> dict[str, bytes]:
        if auth["service"] != self.reference.service or not self.current():
            raise PermissionError("external-agent custody changed before launch")
        _, _, material, _, _ = self._vault()
        if len(material) > MAX_AUTH_BYTES or not self.current():
            raise PermissionError("external-agent custody changed during materialization")
        return {"auth.json": material}

    def compare_and_swap(self, auth: dict, files: dict[str, bytes]) -> bool:
        from tinyassets.boxes.file_wire import MAX_DATA
        from tinyassets.boxes.provider import WriteConflict, WriteMode
        from tinyassets.credential_vault import (
            _canonical_digest,
            _decode_codex_auth_json,
            _subscription_reference_digest,
            llm_subscription_credential_record,
        )

        if self.reference.reference_digest != _subscription_reference_digest(
                reference_id=self.reference.reference_id,
                owner_user_id=self.owner_user_id, universe_id=self.universe_dir.name,
                service=self.reference.service, generation=self.reference.generation):
            raise PermissionError("CLI refresh requires current custody pin format")
        if not self.current():
            return False
        records, old, _, digest, generation = self._vault()
        if digest != self.reference._record_digest:
            return False
        data = files["auth.json"]
        encoded = base64.b64encode(data).decode("ascii")
        _decode_codex_auth_json(encoded)
        replacement = llm_subscription_credential_record(
            service=auth["service"], auth_json_b64=encoded,
        )
        new_record = {**old, **replacement}
        records[records.index(old)] = new_record
        body = (json.dumps({"schema_version": 1, "credentials": records},
                           indent=2, sort_keys=True) + "\n").encode()
        if len(body) > MAX_DATA:
            raise ValueError("external-agent credential vault exceeds box bound")
        op_id = "cli-refresh:" + hashlib.sha256(
            (self.reference.reference_id + "\0").encode() + body).hexdigest()
        try:
            self.binding.provider.write(
                self.binding.handle, op_id, "/cc/.credential-vault.json", body,
                max_bytes=MAX_DATA, mode=WriteMode.CAS, expect_generation=generation,
            )
        except WriteConflict:
            return False
        new_digest = _canonical_digest({
            "material_digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "record": new_record,
        })
        with SQLiteProviderWorkAuthorityStore(self.universe_dir.parent).connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE llm_credential_custody SET record_digest = ? "
                "WHERE reference_id = ? AND owner_user_id = ? AND universe_id = ? "
                "AND service = ? AND generation = ? AND reference_digest = ? "
                "AND record_digest = ? AND EXISTS ("
                "SELECT 1 FROM llm_credential_deposit_owners WHERE universe_id = ? "
                "AND service = ? AND owner_user_id = ?)",
                (new_digest, self.reference.reference_id, self.owner_user_id,
                 self.universe_dir.name, self.reference.service, self.reference.generation,
                 self.reference.reference_digest, digest, self.universe_dir.name,
                 self.reference.service, self.owner_user_id),
            )
            if changed.rowcount != 1:
                conn.rollback()
                raise RuntimeError("external-agent custody carry failed after box write")
            conn.commit()
        return True


def box_custody(universe: Path, owner: str, service: str, binding) -> BoxCustody | None:
    """Resolve a platform pin against bytes read through this exact box handle."""
    from tinyassets.credential_vault import (
        _ensure_custody_schema,
        _ensure_llm_deposit_owner_schema,
    )

    binding.validate(owner, universe)
    with SQLiteProviderWorkAuthorityStore(universe.parent).connection() as conn:
        _ensure_llm_deposit_owner_schema(conn)
        _ensure_custody_schema(conn)
        row = conn.execute(
            "SELECT reference_id, generation, record_digest, reference_digest "
            "FROM llm_credential_custody WHERE owner_user_id = ? "
            "AND universe_id = ? AND service = ?",
            (owner, universe.name, service),
        ).fetchone()
    if row is None:
        return None
    reference = LLMCredentialCustodyReference(
        reference_id=row[0], owner_user_id=owner, universe_id=universe.name,
        service=service, generation=row[1], reference_digest=row[3],
        _record_digest=row[2],
    )
    custody = BoxCustody(universe, owner, reference, binding)
    return custody if custody.current() else None


@asynccontextmanager
async def exclusive_refresh(custody: Custody):
    """Hold the owner/service token-spending lock through CLI and writeback."""
    import fcntl  # The serving daemon and owner cells run on Linux.

    # On owner-box the center is a guest directory. Only the platform lock
    # parent is opened here; the bound provider owns every center file.
    root = (custody.universe_dir.parent if isinstance(custody, BoxCustody)
            else custody.universe_dir.resolve(strict=True).parent)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    with ExitStack() as stack:
        stack.callback(os.close, root_fd)
        try:
            os.mkdir('.platform', 0o711, dir_fd=root_fd)
        except FileExistsError:
            pass
        else:
            os.chmod('.platform', 0o711, dir_fd=root_fd, follow_symlinks=False)
        platform_fd = os.open('.platform', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=root_fd)
        stack.callback(os.close, platform_fd)
        info = os.fstat(platform_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o711:
            raise PermissionError('external-agent refresh lock parent is invalid')
        key = hashlib.sha256((custody.owner_user_id + '\0' +
                              custody.reference.service).encode()).hexdigest()
        fd = os.open('external-agent-refresh-' + key,
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=platform_fd)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError('external-agent refresh lock file is invalid')
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RefreshBusyError('external-agent credential refresh busy') from None
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def materialize(custody: Custody, auth: dict) -> dict[str, bytes]:
    """Revalidate, read only declared regular files, remove the sealed copy."""
    if isinstance(custody, BoxCustody):
        return custody.materialize(auth)
    if not custody.current() or custody.reference.service != auth["service"]:
        raise PermissionError("external-agent custody changed before launch")
    snapshot = snapshot_llm_subscription_credential(
        universe_dir=custody.universe_dir, custody=custody.reference,
    )
    try:
        files = {}
        for name in auth["files"]:
            fd = os.open(snapshot.directory / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size > MAX_AUTH_BYTES):
                    raise PermissionError("external-agent credential file is invalid")
                data = bytearray()
                while len(data) <= MAX_AUTH_BYTES:
                    part = os.read(fd, min(65536, MAX_AUTH_BYTES + 1 - len(data)))
                    if not part:
                        break
                    data.extend(part)
                if len(data) > MAX_AUTH_BYTES or len(data) != info.st_size:
                    raise PermissionError("external-agent credential file changed during read")
                files[name] = bytes(data)
            finally:
                os.close(fd)
        if not custody.current():
            raise PermissionError("external-agent custody changed during materialization")
        return files
    finally:
        cleanup_llm_credential_snapshot(snapshot)


def compare_and_swap(custody: Custody, auth: dict, files: dict[str, bytes]) -> bool:
    """A newer owner deposit wins; a CLI refresh carries only the same pin."""
    if set(files) - set(auth["writeback"]):
        raise ValueError("external-agent returned undeclared auth files")
    if not files:
        return False
    if len(files) != 1 or any(not data or len(data) > MAX_AUTH_BYTES for data in files.values()):
        raise ValueError("external-agent writeback is invalid")
    if isinstance(custody, BoxCustody):
        return custody.compare_and_swap(auth, files)
    data = next(iter(files.values()))
    universe = custody.universe_dir.resolve(strict=True)
    with exclusive_credential_vault(universe) as write:
        if not custody.current():
            return False
        record = llm_subscription_credential_record(
            service=auth["service"], auth_json_b64=base64.b64encode(data).decode("ascii"),
        )
        write([record], owner_user_id=custody.owner_user_id, universe_id=universe.name)
        with SQLiteProviderWorkAuthorityStore(universe.parent).connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                carried = carry_llm_subscription_custody(
                    conn, universe_dir=universe, owner_user_id=custody.owner_user_id,
                    universe_id=universe.name, service=auth["service"],
                    expected_record_digest=custody.reference._record_digest,
                )
            except BaseException:
                conn.rollback()
                raise
            if not carried:
                conn.rollback()
                raise RuntimeError("external-agent custody carry failed after credential write")
            conn.commit()
    return True
