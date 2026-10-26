"""Bounded duplex frames on the broker stream.

The box never opens a provider socket and never sees a credential. The broker
checks the generation fence, both directions of credit, cancellation and a
renewable spending slice, then settles one receipt for the exchange.
"""

from __future__ import annotations

import time

from tinyassets.providers.media_budget import BudgetExhausted, MediaCap

MAX_FRAME = 64 * 1024
INITIAL_CREDIT = 4
_SECRET_KEYS = frozenset({
    "authorization", "credential", "api_key", "secret", "token_value",
})


class DuplexRefused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DuplexSession:
    def __init__(self, *, generation: int, token: str, fence, cap: MediaCap,
                 credential: str | None = None):
        self.generation = generation
        self.token = token
        self._fence = fence
        self.cap = cap
        self._credential = credential
        self.credit = {"up": INITIAL_CREDIT, "down": INITIAL_CREDIT}
        self.upstream = False
        self.direct_egress = False
        self.outcome = "completed"
        self.measured = 0
        self.usage_known = cap.measured
        self.bytes_sent = 0

    def box_view(self) -> dict:
        return {
            "upstream": self.upstream,
            "outcome": self.outcome,
            "egress": "broker",
            "direct_egress": self.direct_egress,
        }

    def _fence_ok(self) -> bool:
        admits = getattr(self._fence, "admits", None)
        if admits is None:
            return True
        return bool(admits(self.generation, self.token))

    def open(self) -> None:
        if not self._fence_ok():
            raise DuplexRefused("fenced")
        self.cap.reserve()
        self.upstream = True

    def grant(self, direction: str, amount: int) -> None:
        if direction not in self.credit or type(amount) is not int or amount <= 0:
            raise DuplexRefused("credit")
        self.credit[direction] = min(self.credit[direction] + amount, MAX_FRAME)

    def frame(self, direction: str, payload: bytes) -> None:
        self.cap.ensure(time.monotonic())
        if not self._fence_ok():
            self.revoke()
            raise DuplexRefused("fenced")
        if not self.upstream:
            raise DuplexRefused("closed")
        if direction not in self.credit:
            raise DuplexRefused("direction")
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_FRAME:
            raise DuplexRefused("frame")
        if self.credit[direction] <= 0:
            raise DuplexRefused("credit")
        try:
            if self.cap.used + len(payload) > self.cap.reserved:
                self.cap.renew()
        except BudgetExhausted as exc:
            self.upstream = False
            self.outcome = "budget_exhausted"
            self.measured = 0 if exc.measured is None else exc.measured
            self.usage_known = exc.measured is not None
            raise
        self.credit[direction] -= 1
        self.cap.meter(len(payload))
        self.bytes_sent += len(payload)
        self.measured = self.cap.used

    def cancel(self) -> None:
        self.upstream = False
        self.outcome = "cancelled"

    def revoke(self) -> None:
        self.upstream = False
        self.outcome = "revoked"
        self._credential = None

    def act(self, step: dict) -> None:
        op = step.get("op") if isinstance(step, dict) else None
        if op == "frame":
            self.frame(step.get("direction", ""), step.get("payload", b""))
        elif op == "credit":
            self.grant(step.get("direction", ""), step.get("n", 0))
        elif op == "cancel":
            self.cancel()
        elif op == "revoke":
            self.revoke()
        elif op == "fence":
            if not self._fence_ok():
                self.revoke()
                raise DuplexRefused("fenced")
        else:
            raise DuplexRefused("frame")

    def settle(self) -> dict:
        sent = self.bytes_sent > 0
        receipt = {
            "outcome": self.outcome,
            "usage": self.measured if self.usage_known else None,
            "usage_state": "measured" if self.usage_known else "unknown",
            "side_effect_state": "unknown" if sent else "none",
            "receipts": 1,
            "direct_egress": False,
            "upstream": self.upstream,
        }
        return receipt


def accept_duplex(stream, request: dict, *, fence, credential: str | None = None,
                  cap: MediaCap | None = None) -> dict:
    """Play one bounded duplex exchange. Does not open a provider connection."""
    if not isinstance(request, dict) or _SECRET_KEYS & set(request):
        raise DuplexRefused("credential")
    if request.get("upgrade") not in (None, "duplex"):
        raise DuplexRefused("upgrade")
    session = DuplexSession(
        generation=stream.generation, token=stream.token, fence=fence,
        cap=cap or MediaCap.from_request(request), credential=credential,
    )
    stream.duplex = session
    session.open()
    try:
        for step in request.get("script") or ():
            session.act(step)
    except BudgetExhausted:
        pass
    except DuplexRefused:
        if session.outcome == "completed":
            session.outcome = "refused"
        raise
    receipt = session.settle()
    if credential and credential in repr(receipt):
        raise DuplexRefused("credential")
    return receipt


def _duplex_end(receipt: dict) -> tuple[str, str | None]:
    outcome = receipt.get("outcome")
    if outcome == "completed":
        return "completed", None
    if outcome == "cancelled":
        return "cancelled", None
    if outcome in {"revoked", "fenced"}:
        return "cancelled", "fenced"
    if outcome == "budget_exhausted":
        return "failed", "budget_exhausted"
    return "failed", outcome or "refused"
