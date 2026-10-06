"""Verify privileged image paths without importing application or site code.

Run with python -I -S -B. The launcher uses verify_paths before binding.
Symlink permission bits are not access controls on Linux; check their owner,
their parent and every resolved target instead. Missing sys.path zip entries
are allowed only when their existing ancestors satisfy the same predicate.
"""
from __future__ import annotations

import os
import site
import stat
import sys
from pathlib import Path


class UnsafeChain(RuntimeError):
    """An untrusted filesystem node is on the execution/import chain."""


def verify_path(path: str | Path, *, allow_missing: bool = False) -> None:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise UnsafeChain(f"non-absolute or parent-relative chain: {path}")
    pending = list(path.parts[1:])
    current = Path("/")
    links = 0
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise UnsafeChain(f"missing chain node: {current}") from None
        if info.st_uid != 0:
            raise UnsafeChain(f"non-root chain owner: {current}")
        if stat.S_ISLNK(info.st_mode):
            links += 1
            if links > 40:
                raise UnsafeChain(f"symlink loop: {path}")
            target = os.readlink(current)
            if os.path.isabs(target):
                current = Path("/")
                pending = list(Path(target).parts[1:]) + pending
            else:
                current = current.parent
                pending = list(Path(target).parts) + pending
            continue
        if info.st_mode & 0o022:
            raise UnsafeChain(f"writable chain node: {current}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise UnsafeChain(f"non-file chain node: {current}")
        if not pending:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeChain(f"non-directory chain ancestor: {current}")
        part = pending.pop(0)
        current = current.parent if part == ".." else current / part


def verify_paths(artifacts: list[str], import_paths: list[str]) -> None:
    visited = set()

    def tree(path, *, allow_missing=False):
        verify_path(path, allow_missing=allow_missing)
        pending = [Path(path)]
        while pending:
            current = pending.pop()
            if not current.exists():
                continue
            resolved = current.resolve(strict=True)
            if resolved in visited:
                continue
            visited.add(resolved)
            if current.is_dir():
                for child in current.iterdir():
                    verify_path(child)
                    pending.append(child)

    for path in artifacts:
        tree(path)
    for path in import_paths:
        tree(path, allow_missing=True)


def main() -> int:
    artifacts = sys.argv[1:] or [
        sys.executable,
        "/usr/local/libexec/ta-entry.sh",
        "/usr/local/libexec/ta-chain.py",
        "/usr/local/libexec/ta-egress-migration.py",
        "/usr/local/libexec/ta-owner-migration.py",
        "/usr/local/libexec/ta-metadata-migration.py",
        "/usr/local/libexec/ta-volume-inventory.py",
        "/usr/local/libexec/ta-volume-migration.py",
        "/usr/local/libexec/ta-role-start.py",
        "/usr/local/libexec/ta-launch.py",
        "/usr/local/libexec/ta-owner-launch.py",
        "/usr/local/libexec/ta-decoder.py",
        "/usr/local/libexec/ta-git.py",
        "/usr/local/libexec/ta-op",
        "/app/broker_main.py",
        "/app",
    ]
    # -S must stay: executing .pth before checking it would invert the guard.
    # In Python 3.11 -S also suppresses venv prefix discovery. Derive the
    # installed venv from the executable, then ask site for paths WITHOUT
    # calling site.main()/addsitedir() or evaluating any .pth file.
    broker_sites = site.getsitepackages([str(Path(sys.executable).parent.parent)])
    verify_paths(artifacts, [*sys.path, *broker_sites])
    print("privileged chain: PASS (root owners, protected ancestors and link targets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
