"""What one account tier permits: THE table, and the only one.

Founder directive 2026-09-30, verbatim: *"usage limits for accounts should really
only be based on 2 things, total gibs thier universe takes up in the cloud. and how
many agent calls thier universe can simoltaniously run ... free users have less cloud
storage space and less simaltaniouse agent runs."* Both are per ACCOUNT: one
allowance per person, shared across all of their universes.

* ``seats`` -- concurrent agent calls (`tinyassets.universe_seats`). Over the limit,
  work QUEUES; it is never refused and never dropped.
* ``storage_bytes`` -- the account's cloud footprint (change
  `account-storage-quota`).

Which tier an account is on is `universe_owner.account_type_of`; this module only says what
each tier permits. An unresolvable tier is FREE, never unlimited. There are no rate
meters of any kind.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

_log = logging.getLogger(__name__)

TIER_FREE = "free"
TIER_PAID = "paid"


class AccountType(StrEnum):
    """The ONLY per-account input allowed to change behaviour (ADR-011, *The Owner
    Door Is Complete*; founder 2026-09-30: "there are only two account types,
    free or subscription and we dont care what connections they have").

    Resolved in exactly one place, per ACCOUNT: `universe_owner.account_type_of`
    (and `account_type_for_universe`, which goes through the owner). Policy takes
    this value and nothing else about an account -- not its connections, not how
    much it has stored or asked, not whose it is. Two numbers depend on it
    (`limits_for`); nothing a user SEES does, apart from those numbers and the
    upgrade link. ``SUBSCRIPTION`` keeps the stored value ``"paid"`` because that is
    what billing has already written.

    A ``StrEnum`` so a stored value and a constant compare equal (and format as
    the stored word) without a conversion at every call site.
    """

    FREE = TIER_FREE
    SUBSCRIPTION = TIER_PAID

#: Weakest first. `upgrade_url` returns None for the last entry, so a future middle
#: tier needs no change at the call sites -- "is there a tier above this one" is a
#: question about the table, never a `tier != "free"` test at a message site.
TIER_ORDER = (AccountType.FREE, AccountType.SUBSCRIPTION)

#: Seats: concurrent agent calls per account.
#:
#: Free is 3 rather than the directive's "e.g. 2" (approved 2026-09-30). It is a
#: PRODUCT choice -- two simultaneous background agents on free -- and not a
#: necessity. The necessity argument was refuted: 2 seats with 1 reserved gives one
#: running background agent and three waiting, which is a queue, and does
#: demonstrate what the founder asked to see (astra round 1, finding 19). Keeping
#: the honest version because an argument that does not hold is worse than none.
#:
#: What IS load-bearing is the reserve, not the total. The owner's chat must never
#: wait on background work, and guaranteeing that needs a RESERVED seat rather than
#: a priority ordering: a background run may legitimately last
#: `automations.DEFAULT_RUN_TIMEOUT_SECONDS` (3 hours), so an ordering alone bounds
#: the chat's wait by three hours.
_FREE_SEATS_VAR = "TINYASSETS_FREE_SEATS"
_PAID_SEATS_VAR = "TINYASSETS_PAID_SEATS"
_DEFAULT_FREE_SEATS = 3
_DEFAULT_PAID_SEATS = 8

#: Seats background work may never take, so interactive work always has one.
#: Clamped below the seat count in `limits_for`: a reserve at or above it would
#: refuse every background run to protect a chat that is not asking.
_RESERVE_VAR = "TINYASSETS_INTERACTIVE_SEAT_RESERVE"
_DEFAULT_RESERVE = 1

#: Storage: ONE pool per ACCOUNT, shared by all of its universes -- the directive's
#: first number (founder, 2026-09-30: free 2 GiB, paid 20 GiB). This is the only
#: storage quota; the flat 16 GiB workspace quota it replaces is deleted.
#:
#: Measured and enforced by `tinyassets.storage_accounting`, which excludes the
#: platform's own bytes (provider runtime, checkout staging, live scratch) so the
#: number is what the person actually stores. GiB because that is the unit the
#: owner reads; the old `_MB` variables were set nowhere.
_FREE_STORAGE_VAR = "TINYASSETS_FREE_STORAGE_GIB"
_PAID_STORAGE_VAR = "TINYASSETS_PAID_STORAGE_GIB"
_DEFAULT_FREE_STORAGE_GIB = 2.0
_DEFAULT_PAID_STORAGE_GIB = 20.0

#: Where an owner goes to buy more of either number.
#:
#: There is no GET upgrade route: the app's own control (`app.html` `btn-plan` ->
#: `startSubscribe`) POSTs `/app/billing/checkout`, which is identity-gated. A
#: link inside a message cannot POST, and inventing a route is forbidden -- so the
#: link is the app's EXISTING route plus a query parameter wired to that same
#: `startSubscribe()`. One builder, so there is exactly one string to test -- and
#: that paid off: when the app's public URL moved (#4112) this was the one-line
#: default change below.
_UPGRADE_ORIGIN = "https://tinyassets.io"
_APP_PATH_VAR = "TINYASSETS_APP_PATH"
_DEFAULT_APP_PATH = "/app"
_UPGRADE_QUERY = "upgrade=1"

def _positive_number(var: str, default: float) -> float:
    """Read a positive finite number, announcing an unusable override rather than
    swallowing it. A misconfiguration must not silently become the default."""
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        print(
            f"{var}={raw!r} is not a positive number; using {default:g}",
            flush=True,
        )
        return default
    return value


def _positive_int(var: str, default: int) -> int:
    """Read a positive integer, announcing an unusable override rather than
    swallowing it. Shares `_positive_number`'s contract so a misconfigured seat
    count is as loud as a misconfigured quota."""
    value = _positive_number(var, float(default))
    return max(1, int(value))


@dataclass(frozen=True)
class TierLimits:
    """What one tier permits: the directive's two numbers.

    ``seats`` and ``storage_bytes`` are the directive's two dimensions.
    ``background_seats`` is derived rather than stored, so the reserve can never
    disagree with the seat count it is subtracted from.
    """

    name: AccountType
    seats: int
    interactive_reserve: int
    storage_bytes: float

    @property
    def background_seats(self) -> int:
        """Seats background work may occupy. At least 1: a reserve that consumed
        every seat would refuse all automation to protect a chat nobody is having."""
        return max(1, self.seats - self.interactive_reserve)

    def seats_for(self, seat_class: str) -> int:
        """The ceiling this class of work may reach. Interactive work may take
        every seat; background work stops one short, which is the whole of the
        fairness guarantee."""
        from tinyassets.universe_seats import CLASS_INTERACTIVE

        return self.seats if seat_class == CLASS_INTERACTIVE else self.background_seats

    @property
    def storage_gib(self) -> float:
        return self.storage_bytes / float(1024**3)


def app_path() -> str:
    """The app's served path. One reader, which is why moving the app's public
    URL (#4112) was a single default change here and nothing else."""
    raw = (os.environ.get(_APP_PATH_VAR) or "").strip()
    path = raw or _DEFAULT_APP_PATH
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or _DEFAULT_APP_PATH


def upgrade_url(tier: AccountType | str) -> str | None:
    """Where this tier's owner goes to buy more, or None on the top tier.

    None rather than a link on the highest tier because there is nothing to sell
    them, and derived from `TIER_ORDER` rather than compared against `"free"` so a
    future middle tier needs no change at any message site.
    """
    normalized = account_type(tier)
    if normalized == TIER_ORDER[-1]:
        return None
    return f"{_UPGRADE_ORIGIN}{quote(app_path())}?{_UPGRADE_QUERY}"


def upgrade_sentence(tier: AccountType | str, *, what: str = "seats") -> str:
    """The upgrade half of a waiting or full message: one clickable link inline,
    never a banner, button, card or modal (founder, 2026-09-30). Empty on the top
    tier, so a caller concatenates unconditionally and the top tier simply gets the
    fact."""
    url = upgrade_url(tier)
    if url is None:
        return ""
    return f"[Upgrade]({url}) for more {what}."


def account_type(tier: AccountType | str) -> AccountType:
    """Read a stored tier value as an `AccountType`. Anything unrecognized is the
    WEAKEST type and says so, because the alternative to "unknown means free" is
    "unknown means unlimited". This READS a value; it does not resolve whose
    account it is -- that is `universe_owner.account_type_of`."""
    if isinstance(tier, AccountType):
        return tier
    normalized = (tier or "").strip().lower()
    for member in TIER_ORDER:
        if normalized == member.value:
            return member
    if normalized:
        _log.warning(
            "unrecognized account tier %r; applying the %s tier's limits",
            tier,
            TIER_ORDER[0],
        )
    return TIER_ORDER[0]


def limits_for(account: AccountType) -> TierLimits:
    """What an account type permits. The account type is the whole input: there is
    no other per-account argument, so no caller can make a limit depend on
    anything else about the account. An unknown value resolves to FREE."""
    normalized = account_type(account)
    paid = normalized is AccountType.SUBSCRIPTION
    seats = _positive_int(
        _PAID_SEATS_VAR if paid else _FREE_SEATS_VAR,
        _DEFAULT_PAID_SEATS if paid else _DEFAULT_FREE_SEATS,
    )
    # Clamped INSIDE the resolver, not at the call sites: a reserve at or above the
    # seat count would refuse every background run, and a reserve read
    # independently at two call sites is two chances to forget the clamp.
    reserve = min(_positive_int(_RESERVE_VAR, _DEFAULT_RESERVE), max(0, seats - 1))
    storage_gib = _positive_number(
        _PAID_STORAGE_VAR if paid else _FREE_STORAGE_VAR,
        _DEFAULT_PAID_STORAGE_GIB if paid else _DEFAULT_FREE_STORAGE_GIB,
    )
    return TierLimits(
        name=normalized,
        seats=seats,
        interactive_reserve=reserve,
        storage_bytes=storage_gib * 1024.0**3,
    )


def limits_for_universe(universe_dir) -> TierLimits:
    """The limits of the ACCOUNT that owns this universe (founder, 2026-09-30:
    per account, not per universe)."""
    from tinyassets.universe_owner import account_type_for_universe

    return limits_for(account_type_for_universe(universe_dir))
