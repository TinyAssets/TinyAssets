"""Per-universe subscription tier.

Deliberately small: this is the ONLY durable state the billing path needs. Usage
metering is a separate, unlanded change — nothing here counts, limits, or enforces
anything. A universe is `paid` while it has an entitling subscription and `free`
otherwise, and that is the whole model.

Free is the ABSENCE of a subscription rather than a separate plan record, so there
is one less state to drift out of sync with Stripe.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tinyassets.sqlite_connection import ClosingConnection

_DB_FILENAME = ".subscription_state.db"

TIER_FREE = "free"
TIER_PAID = "paid"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TIER_KEY = "tier"
_EVENT_AT_KEY = "tier_event_at"
_CHECKOUT_CLAIM_KEY = "checkout_claim_at"
#: When an entitling subscription is scheduled to lapse. DISPLAY ONLY -- the tier
#: is the sole entitlement authority, and a date must never gate access.
_ENDS_AT_KEY = "tier_ends_at"

#: How long a Checkout Session we create stays completable. Stripe's floor for
#: ``expires_at`` is 30 minutes MEASURED FROM WHEN STRIPE CREATES THE SESSION, not
#: from our anchor -- and between the two we make up to two Stripe GETs (subscription
#: search, price lookup) that can each take 20 seconds. The surplus over 1800 is that
#: gap. It is a budget, not a guess: `create_checkout_session` refuses loudly if the
#: preflight overran it rather than sending Stripe an expiry it will reject.
CHECKOUT_SESSION_SECONDS = 2100.0

#: How long the claim guarding that session is held. It must be LONGER than the
#: session, because the claim's whole job is to be the only completable checkout.
#:
#: The previous value was 900s with no ``expires_at`` sent at all -- so Stripe applied
#: its 24-hour default and the session outlived its guard by nearly a day. After 15
#: minutes a second session could be created while the first was still completable,
#: and completing both billed one universe twice: exactly the outcome the claim exists
#: to prevent (Codex, 2026-08-28).
CHECKOUT_WINDOW_SECONDS = CHECKOUT_SESSION_SECONDS + 120.0


def state_db_path(universe_dir: str | Path) -> Path:
    return Path(universe_dir) / _DB_FILENAME


def _connect(universe_dir: str | Path) -> sqlite3.Connection:
    path = state_db_path(universe_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def get_tier(universe_dir: str | Path, *, default: str = TIER_FREE) -> str:
    """This universe's tier. Absent, or unreadable, means free.

    Falling back rather than raising is deliberate twice over: a universe with no
    billing record is the ordinary case and must not be an error, and a database we
    cannot read must never silently grant the PAID tier.

    Catches Exception, not just sqlite3.Error, and the difference is load-bearing.
    `_connect` first does `mkdir(parents=True)`, which raises OSError on a read-only
    filesystem, a full disk, or a permissions problem -- none of them sqlite errors.
    That mattered because the effect-quota gate resolves the tier as an ARGUMENT:

        reserve_effect_quota(..., tier=get_tier(universe_dir))

    so the lookup runs OUTSIDE that function's own guard. The guard is what keeps
    metering harmless while dark; a lookup that raises before the call is even made
    slips past it and takes the outbound effect down with it -- a failure mode created
    purely by merging metering, which is exactly what landing dark is meant to avoid.

    Free is the safe answer to every failure here: it is the tier that grants nothing.
    """
    try:
        with _connect(universe_dir) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_TIER_KEY,)
            ).fetchone()
    except Exception:
        return default
    return str(row[0]) if row is not None else default


def read_tier(universe_dir: str | Path, *, default: str = TIER_FREE) -> str:
    """This universe's tier, READ-ONLY: never creates the database or its
    directory, and always closes what it opens.

    For callers on every write path (the account storage quota resolves a tier
    per gated write): `get_tier` creates the database as a side effect and leaves
    its connection to the garbage collector, which on Windows holds a handle in
    the universe directory and blocked account deletion from renaming it. Free on
    every failure, as with `get_tier`.
    """
    path = state_db_path(universe_dir)
    if not path.is_file():
        return default
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_TIER_KEY,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return default
    return str(row[0]) if row is not None else default


def get_plan(universe_dir: str | Path) -> dict[str, object]:
    """This universe's tier plus, if it is ending, when.

    ``ends_at`` is None unless an entitling subscription is scheduled to lapse. It
    exists so the app can show a cancellation that has already happened -- without it
    a user who cancelled sees the same "Paid plan" as before and cannot tell whether
    it took. It is DISPLAY ONLY; ``get_tier`` remains the entitlement authority.
    """
    plan: dict[str, object] = {"tier": get_tier(universe_dir), "ends_at": None}
    try:
        with _connect(universe_dir) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_ENDS_AT_KEY,)
            ).fetchone()
    except sqlite3.Error:
        return plan
    if row is not None:
        try:
            plan["ends_at"] = float(row[0])
        except (TypeError, ValueError):
            pass
    return plan


def apply_tier_event(
    universe_dir: str | Path,
    *,
    tier: str,
    event_created: float,
    ends_at: float | None = None,
) -> bool:
    """Apply a billing event's tier only if it is not older than the last applied.

    Stripe does not guarantee delivery order and retries deliveries, so a delayed
    but validly-signed `active` can arrive after a cancellation. Without ordering it
    would silently hand back a paid tier nobody is paying for. The webhook
    signature's replay window bounds re-delivery of ONE event; it says nothing about
    ordering between different events.

    Stripe timestamps are second-granularity, so ties are ordinary. Ties resolve
    toward DE-ESCALATION — a same-second cancel beats a same-second activate — so
    entitlement is never granted by arrival-order coin flip.

    ``ends_at`` rides along in the same transaction: it describes THIS tier, so it
    is accepted or rejected with it. None clears any stored date, which is what a
    renewal or a reactivation means.

    Returns True when applied, False when ignored.
    """
    if not tier:
        raise ValueError("tier is required")
    with _connect(universe_dir) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_EVENT_AT_KEY,)
            ).fetchone()
            try:
                last = float(row[0]) if row is not None else float("-inf")
            except (TypeError, ValueError):
                last = float("-inf")
            if event_created < last:
                conn.execute("ROLLBACK")
                return False
            if event_created == last:
                current = conn.execute(
                    "SELECT value FROM subscription_meta WHERE key = ?", (_TIER_KEY,)
                ).fetchone()
                if (
                    current is not None
                    and str(current[0]) == TIER_FREE
                    and tier != TIER_FREE
                ):
                    conn.execute("ROLLBACK")
                    return False
            for key, value in ((_TIER_KEY, tier), (_EVENT_AT_KEY, str(event_created))):
                conn.execute(
                    "INSERT INTO subscription_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            # Written in the SAME transaction, under the SAME ordering check, as the
            # tier it describes. A second ordered write would be a second ordering,
            # and the two would drift apart on out-of-order delivery -- leaving a
            # universe showing an end date from an event its tier had rejected.
            if ends_at is None:
                conn.execute(
                    "DELETE FROM subscription_meta WHERE key = ?", (_ENDS_AT_KEY,)
                )
            else:
                conn.execute(
                    "INSERT INTO subscription_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_ENDS_AT_KEY, str(ends_at)),
                )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


# --- the checkout attempt ----------------------------------------------------
#
# A bare timestamp lock could not say WHICH Stripe Checkout Session it guarded, and
# three separate defects reduced to that one gap: a lost response created a second
# session, a delayed event for an old subscription released a live claim, and an
# abandoned checkout locked the user out for the whole lease. A lease has to name the
# thing it is a lease on.
#
# ONE JSON record, not a spread of key/value rows, so an attempt is written and read
# atomically -- a half-updated attempt is a second payable session waiting to happen.
#
#     attempt_id   random, and the identity everything else keys on
#     created_at   when it began
#     expires_at   ABSOLUTE, derived from the session's own expiry rather than
#                  recomputed, so the lease can never end before the session does
#     mode         "test" | "live" -- a test attempt must never be resumed live
#     params       the EXACT creation inputs, stored rather than hashed
#     session_id   None until Stripe answers
#     url          None until Stripe answers
#
# `params` holds the inputs themselves because Stripe replays an idempotent request
# only for IDENTICAL parameters, and ours are not stable across calls: return URLs come
# from the request Origin, the price id is re-resolved per call, and the entitlement
# claim depends on the current key. A hash would detect drift but could not reconstruct
# the request, and reconstructing it is the entire point.

_ATTEMPT_KEY = "checkout_attempt_v1"

#: Returned in place of an attempt when the stored record cannot be parsed. It blocks
#: like a live attempt, because whatever it was may still be payable and starting a
#: second checkout beside it is exactly the failure this record exists to prevent.
_CORRUPT = {"__corrupt__": True}


def _read_attempt(conn: sqlite3.Connection, now: float) -> dict | None:
    row = conn.execute(
        "SELECT value FROM subscription_meta WHERE key = ?", (_ATTEMPT_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        attempt = json.loads(row[0])
        expires_at = float(attempt["expires_at"])
    except (TypeError, ValueError, KeyError):
        return dict(_CORRUPT)
    if now >= expires_at:
        return None
    return attempt


def current_checkout_attempt(universe_dir: str | Path, *, now: float) -> dict | None:
    """The live attempt for this universe, or None if there is none."""
    try:
        with _connect(universe_dir) as conn:
            conn.executescript(_SCHEMA)
            return _read_attempt(conn, now)
    except sqlite3.Error:
        return None


def begin_checkout_attempt(
    universe_dir: str | Path,
    *,
    now: float,
    attempt_id: str,
    mode: str,
    params: dict,
    lease_seconds: float,
) -> dict | None:
    """Start an attempt, or None if one already holds this universe.

    Atomic, so two concurrent clicks cannot both begin. That is the mutual exclusion
    the record exists for: Stripe cannot close it for us, because a pending Checkout
    Session is not yet a subscription for `AlreadySubscribed` to refuse.

    Also migrates the legacy `checkout_claim_at`. A fresh legacy claim keeps blocking
    until its ORIGINAL expiry -- ignoring it on deploy would allow a second payable
    session beside one still open. It is deliberately NOT converted into a resumable
    attempt: its creation inputs were never stored, so an identical replay to Stripe
    cannot be guaranteed. A one-time lockout beats a second subscription.
    """
    attempt = {
        "attempt_id": attempt_id,
        "created_at": now,
        "expires_at": now + lease_seconds,
        "mode": mode,
        "params": params,
        "session_id": None,
        "url": None,
    }
    with _connect(universe_dir) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _read_attempt(conn, now) is not None:
                conn.execute("ROLLBACK")
                return None
            legacy = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?",
                (_CHECKOUT_CLAIM_KEY,),
            ).fetchone()
            if legacy is not None:
                try:
                    held_since = float(legacy[0])
                except (TypeError, ValueError):
                    conn.execute("ROLLBACK")
                    return None
                if now - held_since < CHECKOUT_WINDOW_SECONDS:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute(
                    "DELETE FROM subscription_meta WHERE key = ?",
                    (_CHECKOUT_CLAIM_KEY,),
                )
            conn.execute(
                "INSERT INTO subscription_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_ATTEMPT_KEY, json.dumps(attempt)),
            )
            conn.execute("COMMIT")
            return attempt
        except Exception:
            conn.execute("ROLLBACK")
            raise


def record_checkout_session(
    universe_dir: str | Path, *, attempt_id: str, session_id: str, url: str
) -> bool:
    """Attach Stripe's answer to the attempt that asked for it.

    Compare-and-set on ``attempt_id``: if the attempt was settled or replaced while the
    Stripe call was in flight, this writes nothing rather than reviving it.
    """
    with _connect(universe_dir) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_ATTEMPT_KEY,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            try:
                attempt = json.loads(row[0])
            except ValueError:
                conn.execute("ROLLBACK")
                return False
            if attempt.get("attempt_id") != attempt_id:
                conn.execute("ROLLBACK")
                return False
            attempt["session_id"] = session_id
            attempt["url"] = url
            conn.execute(
                "UPDATE subscription_meta SET value = ? WHERE key = ?",
                (json.dumps(attempt), _ATTEMPT_KEY),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def settle_checkout_attempt(
    universe_dir: str | Path,
    *,
    session_id: str = "",
    attempt_id: str = "",
) -> bool:
    """Release the lease -- but ONLY the one identified.

    Compare-and-delete, never an unconditional DELETE. An unconditional one is how a
    delayed event for an OLD subscription could erase the lease protecting a session
    pending right now, letting a second session be created beside it.

    Matching on either identifier closes the window where Stripe's terminal event
    arrives before we managed to record the session id.
    """
    if not session_id and not attempt_id:
        raise ValueError("settle needs a session_id or an attempt_id")
    with _connect(universe_dir) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT value FROM subscription_meta WHERE key = ?", (_ATTEMPT_KEY,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            try:
                attempt = json.loads(row[0])
            except ValueError:
                # Unreadable, therefore unmatchable. Clearing is safe only because the
                # caller reached here holding a real Stripe identifier.
                conn.execute(
                    "DELETE FROM subscription_meta WHERE key = ?", (_ATTEMPT_KEY,)
                )
                conn.execute("COMMIT")
                return True
            matches = (
                session_id and attempt.get("session_id") == session_id
            ) or (attempt_id and attempt.get("attempt_id") == attempt_id)
            if not matches:
                conn.execute("ROLLBACK")
                return False
            conn.execute("DELETE FROM subscription_meta WHERE key = ?", (_ATTEMPT_KEY,))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
