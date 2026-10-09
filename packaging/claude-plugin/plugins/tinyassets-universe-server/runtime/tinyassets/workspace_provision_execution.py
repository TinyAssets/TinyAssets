"""Dependency installation for a workspace checkout: retired at the owner split.

It ran an acquisition stage and an offline install stage in bubblewrap as the
daemon's uid, beside a registry broker child that was also the daemon. No owner
cell carries a writable checkout together with registry egress, and a class
that cannot be celled is retired rather than left unconfined (per-role-uid-split
design, section 1). The workspace effector keeps its consent and reservation
gates; every admitted request now ends here with a visible refusal and charges
nothing. Agents install dependencies with ``bash`` in their own tool cell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: The failure class the effector records as ``provision_reason``.
RETIRED = "provisioning_retired"


@dataclass(frozen=True)
class ProvisionResult:
    failure: str | None
    bytes_to_charge: int


def execute_provision(
    manifests,
    *,
    lease_fd: int,
    repo_fd: int,
    max_transfer_bytes: int,
    storage_bound: int,
    timeout_s: float,
    cancelled: Callable[[], bool],
) -> ProvisionResult:
    """Refuse: nothing is spawned, written or downloaded."""
    del manifests, lease_fd, repo_fd, max_transfer_bytes, storage_bound, timeout_s, cancelled
    return ProvisionResult(RETIRED, 0)
