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
    """Send each due wake once; ``base`` is the broker's ledger, ``send`` is AGENT_WAKE.

    Nothing is claimed while the outside switch is off: wakes stay pending until it
    reopens. A failed send is logged and left to its ledger state; it never stops the
    others. Only an accepted wake counts, so neither a failing nor a slow callback can
    make the pass run again.
    """
    from tinyassets.broker.policy import outside_denied  # the one switch (P0-3)

    if outside_denied():
        return 0
    accepted = 0
    for wake in base.due_wakes(now, limit=limit):
        try:
            accepted += send(wake, now=now) == "sent"
        except Exception:  # noqa: BLE001 - one owner's wake never blocks another's
            _LOG.error("outside-agent wake failed")
    return accepted
