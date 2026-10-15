"""Pool parents above command-center workspace leases, created inside the owner box."""
from __future__ import annotations

SCRATCH_DIR = "scratch"
__all__ = ["SCRATCH_DIR", "pool_parts"]


def pool_parts(storage: str, repo_key: str) -> tuple[str, ...]:
    """The center-relative owner directories above a lease."""
    from tinyassets.workspace_pool import WORKSPACES_DIR

    if storage == "scratch":
        return (WORKSPACES_DIR, SCRATCH_DIR)
    if storage == "universe":
        return (WORKSPACES_DIR, repo_key)
    raise ValueError(f"unknown workspace storage class: {storage!r}")
