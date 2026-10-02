"""Which agent turns THIS daemon boot is running. Progress, never authority.

A turn row is only *working* while a process is executing it. The journal has no
way to say that: ``agent_turns.state`` records how far the turn got, and a
container recreated mid-turn leaves ``native_started`` behind forever (founder,
2026-09-26: turn ``652a2f31`` sat in ``native_started`` for 35 minutes after a
22:31:54Z deploy, and the app's server-driven status line painted "your universe
is thinking" the whole time).

Boot ownership is the missing fact, and it is in-memory on purpose: a boot has no
durable identity worth writing to a row, and a row's owning process is exactly
what a restart destroys. One :class:`BootTurns` instance == one daemon boot.

Two disjuncts, both necessary:

* A turn this boot CREATED and has not finished. Claimed by the journal's
  ``create`` (the only way a turn comes into being) and released by the
  coordinator when its ``run`` returns, however it returns -- once no task is
  executing the turn, nothing in this boot is running it, whatever the row says.
* A turn created AFTER this boot started. A deploy recreates the container, so
  every process in it restarts together; a row younger than this process cannot
  be a dead container's leftover. This covers a sibling process in the same
  container that keeps its own registry.

Neither disjunct grants anything. This module decides whether a row may be
*reported* as activity and whether startup may *settle* it -- never whether an
effect may run or be replayed.

**Load-bearing assumption: exactly ONE process writes ``agent_turns``.** The
second disjunct is safe only because of it. Add a second writer -- a
``workers=N`` on the ASGI server, or another service with a WRITABLE data mount
-- and a lone restart of one worker gives it a ``started_at`` newer than the
other worker's in-flight rows, so startup would settle a turn that is genuinely
RUNNING and tell the founder their live turn had been interrupted. That is the
worse of the two failures this module exists to avoid, so the assumption is
pinned by ``tests/test_orphaned_turn_reconcile.py`` rather than left as prose: a
future ``workers=N`` fails a test instead of quietly reaping live turns.

One known, accepted degradation: ``holds`` compares the journal's ``created_at``
with this boot's ``started_at`` on the same wall clock, so a BACKWARDS clock step
between boots can make a dead container's row look younger than this boot and
survive the sweep. It self-heals on the next restart, and a non-creating
``mode=rw`` scan cannot do better without a durable boot id on the row -- which
is a storage-shape change this does not need.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone


class BootTurns:
    """The agent turns one daemon boot is executing right now."""

    def __init__(self, *, started_at: datetime | None = None) -> None:
        self.boot_id = uuid.uuid4().hex
        when = datetime.now(timezone.utc) if started_at is None else started_at
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("boot start must be timezone-aware")
        self.started_at = when.astimezone(timezone.utc)
        self._lock = threading.Lock()
        self._claimed: set[tuple[str, str]] = set()
        # Where each running turn is: (round, model id, round started at).
        self._progress: dict[tuple[str, str], tuple[int, str, datetime]] = {}

    def claim(self, universe_id: str, turn_id: str) -> None:
        """This boot created that turn and is about to execute it."""
        with self._lock:
            self._claimed.add((universe_id, turn_id))

    def release(self, universe_id: str, turn_id: str) -> None:
        """Nothing in this boot is executing that turn any more.

        Called when the coordinator's ``run`` returns -- not when the turn
        reaches a terminal state. A turn whose task was cancelled mid-flight is
        no longer running even though its row still says ``native_started``, and
        that row is precisely what must stop reading as activity. Idempotent: a
        turn released twice, or never claimed, is not an error.
        """
        with self._lock:
            self._claimed.discard((universe_id, turn_id))
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

    def holds(self, universe_id: str, turn_id: str, *, created_at: str) -> bool:
        """Is that row a turn this boot is running?

        ``created_at`` is the journal's own ``...Z`` stamp. One unparsable or
        naive stamp answers False for the time disjunct alone: an unclaimed row
        whose age cannot be established is not evidence that this boot owns it.
        """
        with self._lock:
            if (universe_id, turn_id) in self._claimed:
                return True
        if not isinstance(created_at, str) or not created_at.endswith("Z"):
            return False
        try:
            when = datetime.fromisoformat(created_at[:-1] + "+00:00")
        except ValueError:
            return False
        if when.tzinfo is None or when.utcoffset() is None:
            return False
        return when >= self.started_at


#: This process's boot. Created at import, which is before anything can serve a
#: request or create a turn, so every row older than it predates this process.
BOOT = BootTurns()
