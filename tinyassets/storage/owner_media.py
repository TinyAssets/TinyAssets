"""Private media bytes for one owner. Shared services do not import this module.

A reference names the owner, command center and digest. Retrieval checks the
owner before it opens a file, so another owner learns nothing about the bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_REFERENCE = re.compile(
    r"^media:([A-Za-z0-9_][A-Za-z0-9_.-]{0,63}):"
    r"([A-Za-z0-9_][A-Za-z0-9_.-]{0,63}):([0-9a-f]{64})$"
)


def _component(value: object) -> str:
    if not isinstance(value, str) or _COMPONENT.fullmatch(value) is None or ".." in value:
        raise ValueError("invalid media scope")
    return value


def parse_reference(reference: object) -> tuple[str, str, str] | None:
    if not isinstance(reference, str):
        return None
    match = _REFERENCE.fullmatch(reference)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


class OwnerMediaStore:
    """Complete committed parts under the owner's box root. Mode 0600 files."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def commit(self, owner: str, command_center: str, data: bytes) -> str:
        if not isinstance(data, bytes) or not data:
            raise ValueError("media part is empty")
        owner = _component(owner)
        command_center = _component(command_center)
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / owner / command_center
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        target = directory / digest
        if not target.exists():
            temporary = directory / f".{digest}.tmp"
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, data)
            finally:
                os.close(descriptor)
            os.replace(temporary, target)
        return f"media:{owner}:{command_center}:{digest}"

    def read(self, owner: str, reference: str) -> bytes:
        parsed = parse_reference(reference)
        if parsed is None or parsed[0] != owner:
            raise PermissionError("media reference is not this owner's")
        path = self.root / parsed[0] / parsed[1] / parsed[2]
        if not path.is_file():
            raise FileNotFoundError(reference)
        return path.read_bytes()
