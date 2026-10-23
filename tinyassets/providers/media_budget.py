"""Bounded media reservations against an owner's cap.

Free-only owners keep a single slice and are never opted into paid use.
Renewal runs before a stream passes the slice it already holds. A cap that
cannot fund the next slice stops the stream and settles measured usage, or
leaves the amount unknown for reconciliation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_SLICE = 64 * 1024
DEFAULT_TTL_S = 30.0


class BudgetExhausted(Exception):
    """The next slice cannot be reserved. ``measured`` is None when usage is unknown."""

    def __init__(self, measured: int | None):
        self.measured = measured
        self.outcome = "budget_exhausted"
        super().__init__("budget_exhausted")


@dataclass
class MediaCap:
    remaining: int | None = None
    slice_bytes: int = DEFAULT_SLICE
    ttl_s: float = DEFAULT_TTL_S
    used: int = 0
    reserved: int = 0
    paid: bool = False
    measured: bool = True
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        if self.expires_at <= 0:
            self.expires_at = time.monotonic() + self.ttl_s
        if self.remaining is None:
            self.free_ceiling = self.slice_bytes
        else:
            self.free_ceiling = None

    @classmethod
    def from_request(cls, request: dict) -> MediaCap:
        remaining = request.get("cap_remaining") if isinstance(request, dict) else None
        if remaining is not None and type(remaining) is not int:
            remaining = 0
        measured = True
        if isinstance(request, dict) and request.get("usage_unknown") is True:
            measured = False
        return cls(remaining=remaining, measured=measured)

    def reserve(self, nbytes: int | None = None) -> int:
        return self._take(self.slice_bytes if nbytes is None else nbytes)

    def renew(self, nbytes: int | None = None, *, now: float | None = None) -> int:
        granted = self._take(self.slice_bytes if nbytes is None else nbytes)
        self.expires_at = (time.monotonic() if now is None else now) + self.ttl_s
        return granted

    def ensure(self, now: float | None = None) -> None:
        clock = time.monotonic() if now is None else now
        if clock >= self.expires_at:
            self.renew(now=clock)

    def meter(self, nbytes: int) -> None:
        if type(nbytes) is not int or nbytes < 0:
            raise ValueError("media usage must be a byte count")
        self.used += nbytes

    def _take(self, nbytes: int) -> int:
        if type(nbytes) is not int or nbytes <= 0:
            raise ValueError("media slice must be a positive byte count")
        if self.remaining is None:
            if self.reserved + nbytes > self.free_ceiling:
                raise BudgetExhausted(self.used if self.measured else None)
        elif nbytes > self.remaining:
            raise BudgetExhausted(self.used if self.measured else None)
        else:
            self.remaining -= nbytes
        self.reserved += nbytes
        return nbytes
