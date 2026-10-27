"""Outside-agent wakes on the shared tick (S3b, W3; ADR-026 zero idle tail).

Claims due wakes from the trusted ledger and hands each to AGENT_WAKE. It runs in
the broker, on the same pass as MCP event deliveries (P0-5), so a wake never needs
the owner's box awake, and it keeps no timer of its own: a wake that is not due
yet is simply not claimed until a later tick.
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)
#: Wakes per pass; the pass repeats while any were due, as event deliveries do.
PASS_LIMIT = 4


def run_due(base, *, now: float, send, limit: int = PASS_LIMIT) -> int:
    """Send every due wake once; ``base`` is the broker's ledger, ``send`` is AGENT_WAKE.

    A failed send is logged and left to its ledger state; it never stops the others,
    and it is not counted, so a wake that keeps failing cannot spin the pass.
    """
    done = 0
    for wake in base.due_wakes(now, limit=limit):
        try:
            send(wake, now=now)
            done += 1
        except Exception:  # noqa: BLE001 - one owner's wake never blocks another's
            _LOG.error("outside-agent wake failed")
    return done
