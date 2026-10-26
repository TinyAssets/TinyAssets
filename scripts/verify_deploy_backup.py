"""Prove a deploy snapshot is restorable, checksummed, and from this run.

``deploy/deploy_fail_safe.sh`` calls this after ``deploy/backup.sh`` and before
any migration probe. Exit 0 only when the archive name is
``tinyassets-snapshot-`` or ``tinyassets-box-store-`` plus a UTC stamp at least
as new as this deploy (five seconds of clock skew), the ``.sha256`` sidecar
matches the bytes, and the gzip tar lists at least one member.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(
    r"(tinyassets-snapshot|tinyassets-box-store)-"
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})Z\.tar\.gz"
)
_SKEW_S = 5


def _fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def verify(path: Path, started_epoch: int) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        _fail(f"backup {path} is missing or empty")
    match = _NAME.fullmatch(path.name)
    if match is None:
        _fail(f"backup name {path.name} is not a timestamped snapshot")
    stamp = datetime(
        *(int(part) for part in match.groups()[1:]), tzinfo=timezone.utc,
    ).timestamp()
    if stamp + _SKEW_S < started_epoch:
        _fail(f"backup {path.name} is older than this run")
    sidecar = Path(str(path) + ".sha256")
    try:
        parts = sidecar.read_text(encoding="ascii").split()
    except OSError as exc:
        _fail(f"backup digest unreadable: {type(exc).__name__}")
    if (len(parts) != 2 or parts[1] != path.name
            or not re.fullmatch(r"[a-f0-9]{64}", parts[0])):
        _fail(f"backup digest sidecar for {path.name} is invalid")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != parts[0]:
        _fail(f"backup digest mismatch for {path.name}")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError) as exc:
        _fail(f"backup {path.name} is not a readable tar.gz ({type(exc).__name__})")
    if not members:
        _fail(f"backup {path.name} has no members")
    return f"verified backup: {path.name} sha256:{digest}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        _fail("usage: verify_deploy_backup.py ARCHIVE STARTED_EPOCH")
    try:
        started = int(args[1])
    except ValueError:
        _fail("started epoch is not an integer")
    print(verify(Path(args[0]), started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
