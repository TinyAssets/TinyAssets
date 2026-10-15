"""One-job credential transfer between owner custody and an external-agent cell."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import stat
import time
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from tinyassets.credential_vault import (
    LLMCredentialCustodyReference,
    carry_llm_subscription_custody,
    current_llm_subscription_custody,
    exclusive_credential_vault,
    llm_subscription_credential_record,
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
    """Platform-held CLI credential custody for an admitted owner box."""

    binding: object

    def _vault(self):
        from tinyassets.credential_vault import (
            _canonical_digest,
            _subscription_material,
            _usable_subscription_record,
            load_credential_vault,
        )

        self.binding.validate(self.owner_user_id, self.universe_dir)
        records = load_credential_vault(self.universe_dir)
        record = _usable_subscription_record(self.universe_dir, self.reference.service)
        material = _subscription_material(self.universe_dir, self.reference.service, record)
        digest = _canonical_digest({
            "material_digest": "sha256:" + hashlib.sha256(material).hexdigest(),
            "record": record,
        })
        return records, record, material, digest

    def reload(self) -> LLMCredentialCustodyReference | None:
        from tinyassets.credential_vault import _subscription_reference_matches

        try:
            _, _, _, digest = self._vault()
        except (PermissionError, ValueError, OSError):
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
        _, _, material, _ = self._vault()
        if len(material) > MAX_AUTH_BYTES or not self.current():
            raise PermissionError("external-agent custody changed during materialization")
        return {"auth.json": material}

    def compare_and_swap(self, auth: dict, files: dict[str, bytes]) -> bool:
        from tinyassets.credential_vault import (
            _decode_subscription_auth_json,
            _subscription_reference_digest,
        )

        if self.reference.reference_digest != _subscription_reference_digest(
                reference_id=self.reference.reference_id,
                owner_user_id=self.owner_user_id, universe_id=self.universe_dir.name,
                service=self.reference.service, generation=self.reference.generation):
            raise PermissionError("CLI refresh requires current custody pin format")
        if not self.current():
            return False
        data = files["auth.json"]
        encoded = base64.b64encode(data).decode("ascii")
        _decode_subscription_auth_json(encoded)
        # The token-spending lease spans this write and its custody-row carry.
        # The vault is in platform storage and no guest file RPC sees its bytes.
        with exclusive_credential_vault(self.universe_dir) as write:
            if not self.current():
                return False
            record = llm_subscription_credential_record(
                service=auth["service"], auth_json_b64=encoded,
            )
            write([record], owner_user_id=self.owner_user_id,
                  universe_id=self.universe_dir.name)
            return self._carry_after_write(auth)

    def _carry_after_write(self, auth: dict) -> bool:
        with SQLiteProviderWorkAuthorityStore(self.universe_dir.parent).connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            carried = carry_llm_subscription_custody(
                conn, universe_dir=self.universe_dir,
                owner_user_id=self.owner_user_id,
                universe_id=self.universe_dir.name, service=auth["service"],
                expected_record_digest=self.reference._record_digest,
            )
            if not carried:
                conn.rollback()
                raise RuntimeError("external-agent custody carry failed after credential write")
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
    root = custody.universe_dir.parent
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
    """Revalidate the platform pin and lease the declared files for one run."""
    if not isinstance(custody, BoxCustody):
        raise PermissionError("external-agent launches require box custody")
    return custody.materialize(auth)


def compare_and_swap(custody: Custody, auth: dict, files: dict[str, bytes]) -> bool:
    """A newer owner deposit wins; a CLI refresh carries only the same pin."""
    if set(files) - set(auth["writeback"]):
        raise ValueError("external-agent returned undeclared auth files")
    if not files:
        return False
    if len(files) != 1 or any(not data or len(data) > MAX_AUTH_BYTES for data in files.values()):
        raise ValueError("external-agent writeback is invalid")
    if not isinstance(custody, BoxCustody):
        raise PermissionError("external-agent writeback requires box custody")
    return custody.compare_and_swap(auth, files)
