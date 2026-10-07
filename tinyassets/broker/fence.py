"""The owner-generation fence (I14 decision 6, target architecture D5/D11).

Every owner-channel stream carries the generation and the token the broker's
last admitted barrier issued; a stream is admitted only if both equal the
persisted fence, and re-checked immediately before each write to the network.

The barrier is authorized by the LEASE, not by the caller's channel: an old
owner and a new one share an image and a uid, so channel identity cannot tell
them apart. ``FENCE{G, proof}`` is admitted only if the lease authority verifies
that ``proof`` is the secret minted for the acquisition that holds generation
G (``control_plane.lease.verify_lease_proof``, injected here).

* A barrier below the persisted generation is refused.
* A repeat of the persisted generation, with a valid proof, is idempotent: it
  returns the same token, which is how a lost acknowledgement is recovered.
* A higher generation is persisted durably with a fresh token BEFORE anything
  else; only then are older streams stopped and the barrier acknowledged.
* After a restart the persisted fence is enforced whether or not the
  acknowledgement was ever delivered.

Linearization: :meth:`Fence.send` is a read lock held across one network
write. The barrier persists, marks older streams cancelled, then takes the
write lock (waiting for writes in progress) before closing their sockets.
When :meth:`barrier` returns, no stream of an older generation is inside a
write, and none can start another (each re-checks under the read lock).
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class Fenced(PermissionError):
    """Below the fence, or a barrier without a valid lease proof."""


class Fence:
    def __init__(self, path: Path, *, verify_lease_proof: Callable[[int, str], bool],
                 lease_sha256: str | None = None) -> None:
        self._path = Path(path)
        self._verify = verify_lease_proof
        self._lease_sha256 = lease_sha256
        self._persisted_lease: str | None = None
        self._state_lock = threading.Lock()
        self._rw = threading.Condition()
        self._readers = 0
        self._writer = False
        self._barrier_lock = threading.Lock()
        #: The generation whose barrier last ran to completion (none yet after
        #: a restart: a repeat barrier re-runs the stop, which is idempotent).
        self._completed = 0
        self.generation, self.token = self._load()

    def _load(self) -> tuple[int, str]:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0, ""
        generation, token = document["generation"], document["token"]
        if type(generation) is not int or generation < 0 or not isinstance(token, str):
            raise ValueError("the persisted fence is malformed; refusing to serve")
        self._persisted_lease = document.get("lease_sha256")
        return generation, token

    def _persist(self, generation: int, token: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".fence.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                document = {"generation": generation, "token": token}
                if self._lease_sha256 is not None:
                    document["lease_sha256"] = self._lease_sha256
                json.dump(document, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def admits(self, generation: int, token: str) -> bool:
        with self._state_lock:
            return bool(self.token) and (generation, token) == (self.generation, self.token)

    def barrier(self, generation: int | None, proof: str, *,
                cancel_older: Callable[[int], None] = lambda generation: None,
                close_older: Callable[[int], None] = lambda generation: None,
                ) -> tuple[int, str]:
        """Advance (or re-acknowledge) the fence; returns the persisted ``(G, token)``.

        Barriers are serialized, and an acknowledgement is returned only once
        the barrier for the persisted generation has COMPLETED:

        1. the new fence is made durable (new senders are refused from here);
        2. ``cancel_older(G)`` marks every stream below G cancelled, so a
           producer waiting inside a write is unblocked;
        3. the barrier waits until no sender holds the write lock;
        4. ``close_older(G)`` closes those streams' upstream sockets.

        A repeat of the persisted generation whose earlier barrier did not
        complete (it raised, or is still running) runs steps 2-4 again rather
        than acknowledging early.
        """
        with self._barrier_lock:
            if self._lease_sha256 is not None:
                if generation is not None:
                    raise Fenced("the broker owns generation allocation")
                generation = (self.generation if self.token and
                              self._persisted_lease == self._lease_sha256
                              else self.generation + 1)
            if type(generation) is not int or generation < 1 or not isinstance(proof, str):
                raise Fenced("a barrier needs a positive generation and a lease proof")
            if not self._verify(generation, proof):
                raise Fenced("the lease does not hold this generation with this proof")
            with self._state_lock:
                if generation < self.generation:
                    raise Fenced("a newer generation already holds the fence")
                if generation == self.generation and self.token:
                    if self._completed == generation:
                        return self.generation, self.token
                    token = self.token
                else:
                    token = secrets.token_urlsafe(32)
                    self._persist(generation, token)
                    self.generation, self.token = generation, token
                    self._persisted_lease = self._lease_sha256
            cancel_older(generation)
            with self._rw:
                self._writer = True
                try:
                    while self._readers:
                        self._rw.wait()
                finally:
                    self._writer = False
                    self._rw.notify_all()
            close_older(generation)
            self._completed = generation
            return generation, token

    @contextmanager
    def send(self, generation: int, token: str) -> Iterator[None]:
        """Hold across ONE network write; refuses if the stream is below the fence."""
        with self._rw:
            while self._writer:
                self._rw.wait()
            if not self.admits(generation, token):
                raise Fenced("this stream's generation is below the fence")
            self._readers += 1
        try:
            yield
        finally:
            with self._rw:
                self._readers -= 1
                self._rw.notify_all()
