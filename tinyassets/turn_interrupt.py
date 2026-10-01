"""The owner stops their own live conversation turn (founder, 2026-09-30).

*"there is no way to interrupt a run and push your messages. it should work just
like it works in claude code where i can press escape to interrupt and send all
pending messages."*

One live record per running ``converse`` turn, registered by the served handler
under the VERIFIED caller and the universe it spoke to. ``request_interrupt`` from
any of that caller's surfaces (another tab, the phone shell) finds it by exactly
that pair, so another user -- including a collaborator holding write on the same
universe -- can never reach it: the key is the caller's own subject, never a turn
id someone could guess or pass.

How a turn honours it, and why each boundary is where it is:

* **Between rounds and before every tool call** the coordinator checks and stops.
  A tool the model asked for and that never started is recorded ``not_sent``, the
  state the journal already PROVES for a call that was never dispatched.
* **A tool call already running is allowed to finish.** Cancelling our half of an
  engine call does not abort the engine's half; it only turns a known result into
  an unknown one. So the call completes, its result is recorded, and the turn
  stops at the next boundary. Nothing is left half-applied or unrecorded.
* **A native CLI round (codex / claude-code) is cancelled at once.** The provider
  adapters already end the whole owned process family on cancellation
  (``providers/owned_process``) and the router settles its reservation on the
  way out, so the jail, the process and the seat are released by code that
  already runs for every other cancellation. The round is recorded
  ``indeterminate``: a killed agent may have acted, and saying otherwise would
  be the lie ``conversation_failure`` exists to prevent.
* **An HTTP inference round is allowed to return.** The HTTP provider waits for
  its own request even when cancelled (``api_key_http_provider``: the synchronous
  request cannot be aborted, and its spend must be settled), so cancelling it
  would buy no time and would lose the recorded reply and its real cost. The
  reply is recorded; any tools it asked for are not run.

In memory, deliberately, like the boot registry beside the journal
(``storage/agent_turn_boot``): exactly ONE process runs interactive turns, which
``tests/test_orphaned_turn_reconcile.py`` pins, and the turn, the request that
started it and every interrupt of it all live and die with that process. A row
outliving the only process that could honour it would be a second, stale
authority for the same fact.

Only the served ``converse`` path registers. An automation, an agent node or a
wake has no live record, so an interrupt cannot reach one.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
import uuid
from contextlib import contextmanager


class TurnInterrupted(Exception):
    """The turn's owner stopped it. Not a provider failure, never retried.

    Deliberately NOT a ``ProviderError``: nothing about the source is implied,
    so no cooldown, fallback or "exhausted" wording may follow from it.
    """


class LiveTurn:
    """One running interactive turn that its owner may stop."""

    def __init__(self, actor_id: str, universe_id: str) -> None:
        self.actor_id = actor_id
        self.universe_id = universe_id
        self.live_id = uuid.uuid4().hex
        self._requested = threading.Event()
        self._lock = threading.Lock()
        self._wakers: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def requested(self) -> bool:
        return self._requested.is_set()

    def request(self) -> None:
        """Ask the turn to stop. Thread-safe; wakes an in-flight native round."""
        with self._lock:
            self._requested.set()
            wakers = list(self._wakers)
        for loop, event in wakers:
            with contextlib.suppress(RuntimeError):  # that loop already closed
                loop.call_soon_threadsafe(event.set)

    def check(self) -> None:
        """Raise :class:`TurnInterrupted` at a boundary if a stop was asked for."""
        if self.requested():
            raise TurnInterrupted("the owner stopped this turn")

    async def run(self, awaitable):
        """Await ``awaitable``, cancelling it the moment a stop is asked for.

        The cancelled work is AWAITED before this raises, so the provider's own
        teardown (kill the process family, settle the reservation) has finished
        by the time the caller records the outcome. Work that completed in the
        same instant keeps its result: the stop then takes effect at the next
        boundary instead of discarding an answer that exists.
        """
        try:
            return await self.cancel_on_stop(awaitable)
        except asyncio.CancelledError:
            if not self.requested():
                raise
        except BaseException as exc:
            if not self.requested():
                raise
            raise TurnInterrupted("the owner stopped this turn") from exc
        raise TurnInterrupted("the owner stopped this turn")

    async def cancel_on_stop(self, awaitable):
        """:meth:`run` for code BELOW the turn's owner: a stop surfaces as plain
        cancellation.

        The router awaits a native provider through this. Its reservation and
        carrier handling already treat ``CancelledError`` as "cancelled" and its
        failure classifiers deliberately do not catch it, so a stop is never
        recorded as a provider failure, cooled, or answered by trying another
        source. The turn's owner converts it to :class:`TurnInterrupted`.
        """
        task = asyncio.ensure_future(awaitable)
        if self.requested():
            task.cancel()
        loop = asyncio.get_running_loop()
        waker = asyncio.Event()
        entry = (loop, waker)
        with self._lock:
            self._wakers.add(entry)
            if self._requested.is_set():
                waker.set()
        watcher = asyncio.ensure_future(waker.wait())
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if not task.done():
                task.cancel()
            return await task
        finally:
            watcher.cancel()
            with self._lock:
                self._wakers.discard(entry)
            if not task.done():
                # The caller itself was cancelled (a deadline): the child must
                # still unwind, which is where its process is killed.
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task


_LOCK = threading.Lock()
_LIVE: dict[tuple[str, str], set[LiveTurn]] = {}
_CURRENT: contextvars.ContextVar[LiveTurn | None] = contextvars.ContextVar(
    "tinyassets_live_interactive_turn", default=None,
)


def _key(actor_id: str, universe_id: str) -> tuple[str, str]:
    actor, universe = str(actor_id or "").strip(), str(universe_id or "").strip()
    if not actor or not universe:
        # Never key a live turn on an empty subject: "" would be one shared
        # bucket every unauthenticated path fell into.
        raise ValueError("an interactive turn needs an authenticated owner and a universe")
    return actor, universe


@contextmanager
def interactive_turn(actor_id: str, universe_id: str):
    """Register the served turn running in this context until it returns."""
    key = _key(actor_id, universe_id)
    live = LiveTurn(*key)
    with _LOCK:
        _LIVE.setdefault(key, set()).add(live)
    token = _CURRENT.set(live)
    try:
        yield live
    finally:
        _CURRENT.reset(token)
        with _LOCK:
            bucket = _LIVE.get(key)
            if bucket is not None:
                bucket.discard(live)
                if not bucket:
                    del _LIVE[key]


def current() -> LiveTurn | None:
    """The live turn this code runs under, or ``None`` outside a served turn."""
    return _CURRENT.get()


@contextmanager
def bound(live: LiveTurn | None):
    """Carry a live turn into a worker thread, which does not inherit context."""
    token = _CURRENT.set(live)
    try:
        yield live
    finally:
        _CURRENT.reset(token)


def request_interrupt(actor_id: str, universe_id: str) -> int:
    """Stop every live turn THIS caller is running in THIS universe.

    Returns how many were asked to stop; ``0`` means nothing was running, which
    is not an error -- the page may have seen a turn that just finished.
    """
    key = _key(actor_id, universe_id)
    with _LOCK:
        targets = list(_LIVE.get(key, ()))
    for live in targets:
        live.request()
    return len(targets)


def live_count(actor_id: str, universe_id: str) -> int:
    """How many turns this caller is running in this universe (tests, status)."""
    with _LOCK:
        return len(_LIVE.get(_key(actor_id, universe_id), ()))


def live_ids(actor_id: str, universe_id: str) -> set[str]:
    """The live ids of every turn this caller is running in this universe.

    Owner steering (``agent_steering.open_turn``) uses it to close a turn a
    dead process left open, without touching one still running here.
    """
    with _LOCK:
        return {live.live_id for live in _LIVE.get(_key(actor_id, universe_id), ())}
