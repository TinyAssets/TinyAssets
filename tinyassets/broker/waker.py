"""The shared clock: bounded scheduled wakes, independent of interactive traffic."""
from __future__ import annotations

import asyncio
import logging
import math
import time

from tinyassets.boxes.daemon_control import ensure_awake

log = logging.getLogger(__name__)
POLL_SECONDS = 30
# Two of boxhostd's four broker workers remain available for retirement.
MAX_PENDING = 2


class Waker:
    def __init__(self, index, plans, *, wake=ensure_awake, clock=time.time,
                 poll_seconds=POLL_SECONDS):
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("waker interval must be positive and finite")
        self.index, self.plans, self.wake = index, plans, wake
        self.clock, self.poll_seconds = clock, poll_seconds
        self.pending = {}
        self.attempted = {}

    def _wake(self, owner):
        if self.plans.suspended(owner):
            return
        # boxhostd rechecks both admission and suspension after capacity waits.
        self.wake(owner, scheduled=True)
        self.index.woken(owner, now=self.clock())

    async def tick(self):
        for owner, task in list(self.pending.items()):
            if task.done():
                del self.pending[owner]
                try:
                    task.result()
                except Exception:
                    log.exception("scheduled owner wake failed owner=%s", owner)
        due = await asyncio.to_thread(self.index.due, now=self.clock())
        self.attempted = {owner: at for owner, at in self.attempted.items() if owner in due}
        # A failed or continuously due box cannot starve owners later in the index.
        for owner in sorted(due, key=lambda owner: self.attempted.get(owner, -1)):
            if len(self.pending) >= MAX_PENDING:
                break
            if owner not in self.pending:
                self.attempted[owner] = self.clock()
                self.pending[owner] = asyncio.create_task(asyncio.to_thread(self._wake, owner))

    async def run(self, stop):
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("next-wake index scan failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            # Control calls have a 65-second deadline; never abandon their threads.
            await asyncio.gather(*self.pending.values(), return_exceptions=True)
