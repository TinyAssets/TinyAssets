"""One-job credential transfer between owner custody and an external-agent cell."""
from __future__ import annotations

import base64
import os
import stat
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


def materialize(custody: Custody, auth: dict) -> dict[str, bytes]:
    """Revalidate, read only declared regular files, remove the sealed copy."""
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
