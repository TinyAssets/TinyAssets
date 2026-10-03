"""The execution owner's lease, as the scheduler sees it (target design D11).

Every always-on duty runs under the execution owner's lease: exactly one owner,
holding a lease with a monotonic generation. The real lease (S8a,
``lane turn-handover``) is not landed, and today there is one serving process,
so the installed lease is :class:`SingleProcessLease`: generation 1, always
held. S8a replaces it by calling :func:`install_owner_lease` at owner start;
nothing that consumes the lease changes.

The contract a duty relies on:

* ``held()`` before a tick. A process that does not hold the lease does no
  scheduling at all -- a standby, or an owner that has started handing over.
* ``check()`` immediately before an effect (a fire). It raises
  :class:`LeaseLost` when the lease was lost since ``held()``, so a stalled
  owner fires nothing after its successor has taken over.
* ``generation`` is recorded on every fire row, so a successor can tell its
  own fires from an older owner's. Read it fresh on every use: a handover
  bumps it while the process lives.
* ``proof`` is the plaintext secret minted at THIS acquisition. The holder
  presents it with its generation where a downstream fence needs proof of
  ownership (the S6 broker's ``FENCE{G, lease_proof}``), and
  :func:`verify_lease_proof` checks it against the lease authority, which
  stores only its hash. Same image and uid are not ownership; the secret is.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from typing import Protocol, runtime_checkable


class LeaseLost(RuntimeError):
    """The owner lease is no longer held; the caller must not act."""


@runtime_checkable
class OwnerLease(Protocol):
    @property
    def generation(self) -> int: ...

    def held(self) -> bool: ...

    def check(self) -> None: ...

    @property
    def proof(self) -> str: ...

    def verify(self, generation: int, proof: str) -> bool: ...


class SingleProcessLease:
    """Today's lease: one serving process is the only owner there is.

    Generation 1 forever. Correct only while exactly one process runs the
    control plane, which is today's deploy shape (``agent_turn_boot`` pins the
    same single-writer invariant); S8a's generation-fenced lease replaces it
    before any second owner can exist.
    """

    generation = 1

    def __init__(self) -> None:
        # Minted per instance: a second process (or a restart) holds a
        # different secret, so it cannot present this one's proof.
        self._proof = secrets.token_urlsafe(32)

    @property
    def proof(self) -> str:
        return self._proof

    def held(self) -> bool:
        return True

    def check(self) -> None:
        return None

    def verify(self, generation: int, proof: str) -> bool:
        return int(generation) == self.generation and hmac.compare_digest(
            str(proof), self._proof
        )


_lock = threading.Lock()
_installed: OwnerLease = SingleProcessLease()


def current_owner_lease() -> OwnerLease:
    with _lock:
        return _installed


def set_owner_lease(lease: OwnerLease) -> OwnerLease:
    """Install ``lease`` as the process's owner lease; returns the previous one."""
    if not isinstance(lease, OwnerLease):
        raise TypeError("set_owner_lease needs an OwnerLease")
    global _installed
    with _lock:
        previous, _installed = _installed, lease
    return previous


def verify_lease_proof(generation: int, proof: str) -> bool:
    """Whether ``proof`` is the secret of the CURRENTLY held ``generation``.

    Read-only; delegates to the installed lease authority. A stale generation,
    or a right generation with another acquisition's secret, is refused.
    """
    if not proof:
        return False
    return bool(current_owner_lease().verify(int(generation), str(proof)))


__all__ = [
    "LeaseLost",
    "OwnerLease",
    "SingleProcessLease",
    "current_owner_lease",
    "set_owner_lease",
    "verify_lease_proof",
]
