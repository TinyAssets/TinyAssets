"""Owner plan reads over the bound channel; checkout timing shared with the door."""
from __future__ import annotations

from pathlib import Path

TIER_FREE = "free"
TIER_PAID = "paid"

CHECKOUT_SESSION_SECONDS = 2100.0
CHECKOUT_WINDOW_SECONDS = CHECKOUT_SESSION_SECONDS + 120.0


def get_tier(universe_dir: str | Path) -> str:
    """Read the admitted owner's tier; missing plan/channel is a loud error."""
    return get_plan(universe_dir)["tier"]


def read_tier(universe_dir: str | Path) -> str:
    """Read the admitted owner's tier without opening an owner billing database."""
    return get_plan(universe_dir)["tier"]


def get_plan(universe_dir: str | Path) -> dict[str, object]:
    """Read the shared plan using the relay-bound owner identity."""
    from tinyassets.broker.owner_reads import remote

    return remote("plan")
