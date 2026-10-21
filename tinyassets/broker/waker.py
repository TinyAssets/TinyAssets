"""The shared clock: bounded scheduled wakes, independent of interactive traffic."""
from __future__ import annotations

import asyncio
import logging
import math
import time

log = logging.getLogger(__name__)
POLL_SECONDS = 30


class Waker:
    def __init__(self, index, plans, *, wake, clock=time.time,
                 poll_seconds=POLL_SECONDS, lifecycle_tick=None, config=None):
        from tinyassets.broker.lifecycle_config import LifecycleConfig

        self.config = config or LifecycleConfig()
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("waker interval must be positive and finite")
        self.index, self.plans, self.wake = index, plans, wake
        self.clock, self.poll_seconds = clock, poll_seconds
        self.lifecycle_tick = lifecycle_tick
        self.pending = {}
        self.attempted = {}

    async def _wake(self, owner):
        from tinyassets.broker.admission import read

        if read(self.index.identities, owner).state != "admitted":
            return
        # C5: the broker lifecycle authority serializes this wake with withdrawal.
        await self.wake(owner, scheduled=True)

    async def tick(self):
        if self.lifecycle_tick is not None:
            await self.lifecycle_tick()
            if not self.index.identities.work_ready:
                return
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
            if len(self.pending) >= self.config.volume_concurrency:
                break
            if owner not in self.pending:
                self.attempted[owner] = self.clock()
                self.pending[owner] = asyncio.create_task(self._wake(owner))

    async def run(self, stop):
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("next-wake index scan failed" if self.index.identities.work_ready
                                  else "platform recovery failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            # Control calls have a 65-second deadline; never abandon their threads.
            await asyncio.gather(*self.pending.values(), return_exceptions=True)
