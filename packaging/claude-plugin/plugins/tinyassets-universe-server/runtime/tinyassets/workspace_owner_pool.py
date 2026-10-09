"""Owner-owned pool parents above command-center workspace leases.

New parents are created by the fixed owner-content cell and published by the
root-owning daemon. Existing migrated owner directories keep their inodes and
ACLs; another identity is refused rather than relabelled or replaced.
"""
from __future__ import annotations

import os
from pathlib import Path

SCRATCH_DIR = "scratch"
__all__ = ["SCRATCH_DIR", "pool_parts", "prepare"]


def pool_parts(storage: str, repo_key: str) -> tuple[str, ...]:
    """The center-relative owner directories above a lease."""
    from tinyassets.workspace_pool import WORKSPACES_DIR

    if storage == "scratch":
        return (WORKSPACES_DIR, SCRATCH_DIR)
    if storage == "universe":
        return (WORKSPACES_DIR, repo_key)
    raise ValueError(f"unknown workspace storage class: {storage!r}")


def prepare(center: Path, parts: tuple[str, ...], *, machine: int) -> Path:
    """Ensure owner directories without giving the owner writes to center root."""
    from tinyassets.role_content import ensure_directories

    if os.name != "posix":
        raise NotImplementedError("an owner workspace pool needs POSIX openat")
    if not parts or any(
            not part or part in (".", "..") or "/" in part or "\\" in part
            or "\0" in part for part in parts):
        raise ValueError("a pool parent is one or more plain directory names")
    ensure_directories(center, parts, machine=machine)
    return Path(center, *parts)
