"""Which agent turns THIS process has stopped executing. Progress, never authority.

Ownership of a turn row follows the OWNER LEASE GENERATION, not this module
(change ``execution-owner-lease`` D2, replacing the boot rule that lived here): a
working row is a live owner's turn only while its ``owner_generation`` equals the
generation its command center's key is held at and that owner tree is alive.
Startup reconcile settles rows below the current generation, after acquiring the
key (``agent_turn_reconcile``).

What remains here is the one fact the generation cannot carry: a task in THIS
process that was cancelled or timed out leaves its row progressing at the
current generation with nothing executing it. The coordinator records that here
when its ``run`` returns (:meth:`BootTurns.release`), and the status projection
stops painting the row as activity. In-memory on purpose: it describes this
process, and a restart is reconciled by generation instead.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone


class BootTurns:
    """The agent turns this process created, and which of them it stopped running."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: set[tuple[str, str]] = set()
        #: Stopped turns whose rows may still read as progressing. Kept until the
        #: projection sees the row settled (:meth:`forget`), never evicted by count:
        #: forgetting a still-progressing row would paint it as activity again.
        self._released: set[tuple[str, str]] = set()
        # Where each running turn is: (round, model id, round started at).
        self._progress: dict[tuple[str, str], tuple[int, str, datetime]] = {}

    def claim(self, universe_id: str, turn_id: str) -> None:
        """This process created that turn and is about to execute it."""
        with self._lock:
            self._claimed.add((universe_id, turn_id))
            self._released.discard((universe_id, turn_id))

    def release(self, universe_id: str, turn_id: str) -> None:
        """Nothing in this process is executing that turn any more.

        Called when the coordinator's ``run`` returns, however it returns.
        Idempotent; a turn never claimed here is ignored.
        """
        with self._lock:
            if (universe_id, turn_id) not in self._claimed:
                return
            self._claimed.discard((universe_id, turn_id))
            self._released.add((universe_id, turn_id))
            self._progress.pop((universe_id, turn_id), None)

    def note_round(self, universe_id: str, turn_id: str, *, round: int, model: str,
                   now: datetime | None = None) -> None:
        """The turn just opened ``round`` on ``model``: what a waiting owner sees.

        Display only, and in memory for the same reason ownership is: the journal
        records no time per round, and a round's start is meaningless after the
        restart that ends it. A 10-minute wait on one model request read exactly
        like a hang (live 2026-10-02, turn c6ae56f9).
        """
        when = datetime.now(timezone.utc) if now is None else now
        with self._lock:
            if (universe_id, turn_id) in self._claimed:
                self._progress[(universe_id, turn_id)] = (round, model, when)

    def progress(self, universe_id: str, turn_id: str) -> tuple[int, str, datetime] | None:
        """The last :meth:`note_round` for a turn this boot is running, or None."""
        with self._lock:
            return self._progress.get((universe_id, turn_id))

    def holds(self, universe_id: str, turn_id: str) -> bool:
        with self._lock:
            return (universe_id, turn_id) in self._claimed

    def forget(self, universe_id: str, turn_id: str) -> None:
        """The row is no longer progressing; the stopped mark has done its job."""
        with self._lock:
            self._released.discard((universe_id, turn_id))

    def stopped(self, universe_id: str, turn_id: str) -> bool:
        """This process created the turn and is no longer executing it."""
        with self._lock:
            return (universe_id, turn_id) in self._released


#: This process's turns.
BOOT = BootTurns()
