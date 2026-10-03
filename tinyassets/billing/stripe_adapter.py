"""Stripe adapter — the only module that knows Stripe exists.

Deliberately stdlib-only. Adding the SDK would put a Stripe import one `pip install`
away from anywhere in the tree, and the boundary this package exists to hold is
easier to keep when there is nothing to accidentally import.

**Fail closed around money.** Billing is off unless both Stripe API and webhook
secrets are configured. A payment processor being unreachable must never grant an
entitlement or turn a normal free universe into an application crash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request

_log = logging.getLogger(__name__)

_API = "https://api.stripe.com/v1"

#: Pinned, per Stripe's own go-live checklist. An unversioned request inherits the
#: ACCOUNT's default version -- a dashboard setting, not something in this repo -- so
#: Stripe could change the shape of what we send and receive without us deploying
#: anything. Bump this deliberately, with tests.
STRIPE_API_VERSION = "2025-08-27.basil"

#: Referenced by lookup key rather than a raw id, so the same code works against
#: test and live mode without an id-swap step.
PRICE_LOOKUP_KEY = "tinyassets_paid_monthly"
PLAN_UNIT_AMOUNT = 2_000
PLAN_CURRENCY = "usd"
PLAN_INTERVAL = "month"

_KEY_VAR = "STRIPE_SECRET_KEY"
_WEBHOOK_SECRET_VAR = "STRIPE_WEBHOOK_SECRET"

#: Version 1 signed the entitlement claim with the STRIPE WEBHOOK SECRET -- a key
#: Stripe tells you to roll periodically, and one you MUST roll the moment it leaks.
#: Rolling it silently invalidates the claim on every subscription already sold, and
#: those subscriptions can then never move a tier again: a later cancellation fails
#: authorization and is ignored, leaving someone entitled who cancelled. That nearly
#: happened on 2026-08-28 when the signing secret leaked and the endpoint had to be
#: recreated. There were no live subscriptions yet. There will be.
#:
#: Version 2 signs with a key of OUR OWN that no third party can force us to rotate.
#: Version 1 is still VERIFIED so subscriptions sold under it keep working; nothing
#: new is issued under it.
_ENTITLEMENT_KEY_VAR = "TINYASSETS_BILLING_ENTITLEMENT_KEY"
_ENTITLEMENT_V1 = "1"
_ENTITLEMENT_V2 = "2"
_ENTITLEMENT_VERSION_KEY = "tinyassets_entitlement_version"
_ENTITLEMENT_CLAIM_KEY = "tinyassets_entitlement_claim"

#: Carried on the Checkout Session so terminal session events name their attempt.
ATTEMPT_ID_KEY = "tinyassets_attempt_id"

#: Stripe rejects a signature older than this; so do we, to stop replay.
_WEBHOOK_TOLERANCE_S = 300

#: Re-exported from the storage module so there is exactly ONE definition of how
#: long a checkout stays completable. Two constants that "must agree" is a comment,
#: not an invariant -- and this pair silently did not agree with Stripe's actual
#: behaviour at all (see CHECKOUT_SESSION_SECONDS).


def _basic_auth(key: str) -> str:
    """Stripe basic auth: the secret key as username, empty password."""
    return "Basic " + base64.b64encode(f"{key}:".encode()).decode("ascii")


class AlreadySubscribed(RuntimeError):
    """This universe already has an active subscription."""

    def __init__(self, subscription_id: str) -> None:
        super().__init__("command center already has an active subscription")
        self.subscription_id = subscription_id


class BillingUnavailable(RuntimeError):
    """Billing is not configured, or Stripe could not be reached."""


class BillingAmbiguous(BillingUnavailable):
    """The request may or may not have reached Stripe.

    A subclass, so every existing `except BillingUnavailable` keeps catching it, but
    the checkout route can tell the two apart -- and it must. On a definite refusal
    (Stripe answered) no session was created, so the attempt can be released. On an
    ambiguous one (timeout, connection reset, lost response) a session MAY exist, and
    releasing the attempt would let a retry create a second one beside it. That is the
    lost-response path that made the old idempotency key useless.
    """


def _secret_key() -> str:
    return (os.environ.get(_KEY_VAR) or "").strip()


def _webhook_secret(secret: str = "") -> str:
    return (secret or os.environ.get(_WEBHOOK_SECRET_VAR) or "").strip()


def billing_enabled() -> bool:
    """True only when checkout and authoritative webhook handling can both work."""
    return bool(_secret_key() and _webhook_secret())


def _price_matches_plan(price: object) -> bool:
    """Whether a Stripe Price is exactly the advertised $20 USD monthly plan."""
    if not isinstance(price, dict):
        return False
    recurring = price.get("recurring")
    return bool(
        price.get("lookup_key") == PRICE_LOOKUP_KEY
        and price.get("unit_amount") == PLAN_UNIT_AMOUNT
        and str(price.get("currency") or "").lower() == PLAN_CURRENCY
        and isinstance(recurring, dict)
        and recurring.get("interval") == PLAN_INTERVAL
        and recurring.get("interval_count") == 1
        and recurring.get("usage_type") in (None, "licensed")
    )


def entitlement_key() -> str:
    """The key NEW entitlement claims are signed with, or '' if unconfigured."""
    return (os.environ.get(_ENTITLEMENT_KEY_VAR) or "").strip()


def _issuing_version() -> str:
    """Version 2 when we have our own key, else the old scheme.

    Falling back keeps existing deployments working, but the go-live check BLOCKS on
    the fallback: selling durable subscriptions whose authority rests on a secret
    Stripe can invalidate is not a live-safe state.
    """
    return _ENTITLEMENT_V2 if entitlement_key() else _ENTITLEMENT_V1


def _entitlement_claim(
    universe_id: str, price_id: str, *, secret: str = "", version: str = ""
) -> str:
    """Sign authority that only our checkout creator may attach to metadata.

    The VERSION selects the key -- that is the point of versioning it.
    """
    version = version or _issuing_version()
    if version == _ENTITLEMENT_V2:
        key = entitlement_key()
        if not key:
            raise BillingUnavailable("no billing entitlement key configured")
    else:
        key = _webhook_secret(secret)
        if not key:
            raise BillingUnavailable("no Stripe webhook secret configured")
    message = "\0".join(
        (version, universe_id, PRICE_LOOKUP_KEY, price_id)
    ).encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _authorized_subscription_universe(obj: object, *, secret: str = "") -> str:
    """Return the bound universe only for a service-created subscription plan."""
    if not isinstance(obj, dict):
        return ""
    metadata = obj.get("metadata")
    items = obj.get("items")
    if not isinstance(metadata, dict) or not isinstance(items, dict):
        return ""
    item_data = items.get("data")
    if not isinstance(item_data, list) or len(item_data) != 1:
        return ""
    item = item_data[0]
    if not isinstance(item, dict) or item.get("quantity") != 1:
        return ""
    price = item.get("price")
    if not _price_matches_plan(price):
        return ""
    universe_id = str(metadata.get("universe_id") or "")
    price_id = str(price.get("id") or "")
    claim = str(metadata.get(_ENTITLEMENT_CLAIM_KEY) or "")
    version = str(metadata.get(_ENTITLEMENT_VERSION_KEY) or "")
    if (
        not universe_id
        or not price_id
        or not claim
        or version not in (_ENTITLEMENT_V1, _ENTITLEMENT_V2)
    ):
        return ""
    try:
        # Verify against the version the subscription was SOLD under, so upgrading
        # the scheme never orphans subscriptions already in the wild.
        expected = _entitlement_claim(
            universe_id, price_id, secret=secret, version=version
        )
    except BillingUnavailable:
        return ""
    return universe_id if hmac.compare_digest(expected, claim) else ""


def _post(
    path: str,
    fields: list[tuple[str, str]],
    *,
    timeout: float = 20.0,
    idempotency_key: str = "",
) -> dict:
    key = _secret_key()
    if not key:
        raise BillingUnavailable("no Stripe key configured")
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        f"{_API}/{path.lstrip('/')}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # Basic auth: key as username, empty password, per Stripe.
    request.add_unredirected_header("Authorization", _basic_auth(key))
    request.add_unredirected_header("Stripe-Version", STRIPE_API_VERSION)
    if idempotency_key:
        # Stripe deduplicates on this for 24h. Without it, a response lost in
        # transit turns a retry into a SECOND object — for checkout that means a
        # second subscription billing one universe twice, and our own duplicate
        # check cannot see it because a pending session is not yet a subscription
        # (Codex round 3, 3).
        request.add_unredirected_header("Idempotency-Key", idempotency_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do NOT surface Stripe's message. It can quote request parameters back,
        # and those include customer and subscription identifiers — an avoidable
        # disclosure surface once this string is returned to a browser
        # (Codex REJECT 2026-08-28 D). Log the detail, return only the class.
        try:
            detail = json.loads(exc.read().decode("utf-8"))["error"]["message"]
        except Exception:
            detail = ""
        _log.warning(
            "stripe rejected %s: HTTP %s %s", path, exc.code, detail or "(no message)"
        )
        raise BillingUnavailable(
            f"Stripe rejected the request (HTTP {exc.code})"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # We do not know whether Stripe processed this. AMBIGUOUS, not refused.
        raise BillingAmbiguous(
            f"Stripe unreachable: {type(exc).__name__}"
        ) from None


def _get(path: str, *, timeout: float = 20.0) -> dict:
    key = _secret_key()
    if not key:
        raise BillingUnavailable("no Stripe key configured")
    request = urllib.request.Request(f"{_API}/{path.lstrip('/')}", method="GET")
    request.add_unredirected_header("Authorization", _basic_auth(key))
    request.add_unredirected_header("Stripe-Version", STRIPE_API_VERSION)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BillingUnavailable(f"Stripe rejected the request: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BillingUnavailable(f"Stripe unreachable: {type(exc).__name__}") from None


def resolve_price_id() -> str:
    """Find the $20/month price by lookup key, so no raw id is hardcoded."""
    found = _get(f"prices?lookup_keys[]={PRICE_LOOKUP_KEY}&active=true&limit=1")
    prices = found.get("data") or []
    if not prices:
        raise BillingUnavailable(
            f"no active price with lookup key {PRICE_LOOKUP_KEY!r}"
        )
    price = prices[0]
    if not _price_matches_plan(price):
        raise BillingUnavailable(
            f"active price {PRICE_LOOKUP_KEY!r} is not the $20 USD monthly plan"
        )
    return str(price["id"])


def checkout_params(
    *, universe_id: str, success_url: str, cancel_url: str, expires_at: int
) -> dict:
    """The creation inputs for one attempt, resolved ONCE and then frozen.

    Everything that could drift between calls is settled here and persisted with the
    attempt: the price id (re-resolved per call otherwise), the entitlement version
    and its claim (recomputed from the current key otherwise), and the return URLs
    (built from the request Origin otherwise). Stripe replays an idempotent request
    only for byte-identical parameters, so a retry that recomputes any of these is not
    a retry -- it is a second request that creates a second session.
    """
    price_id = resolve_price_id()
    version = _issuing_version()
    return {
        "price_id": price_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "expires_at": int(expires_at),
        "entitlement_version": version,
        "entitlement_claim": _entitlement_claim(
            universe_id, price_id, version=version
        ),
    }


def create_checkout_session(
    *,
    universe_id: str,
    attempt_id: str,
    params: dict,
) -> dict[str, str]:
    """Start a subscription. Returns the hosted checkout URL for the user.

    ``universe_id`` rides on ``client_reference_id`` and on subscription metadata so
    the webhook can map the resulting subscription back to a universe without us
    keeping a side table that could drift.

    ``params`` must be the FROZEN inputs stored with the attempt, and ``attempt_id``
    its identity. Together they make "a retry of this attempt" byte-identical to its
    first send, so Stripe replays the original session instead of creating a second.
    Recomputing any input here -- as re-resolving the price or re-deriving the expiry
    from the clock would -- turns a retry into a new request under the same key, which
    Stripe rejects outright.

    The attempt id also rides on the SESSION's metadata, not just the subscription's,
    so a terminal Checkout Session event can be bound back to its attempt even when
    the session id was never recorded locally.
    """
    if not universe_id:
        raise ValueError("universe_id is required")
    if not attempt_id:
        raise ValueError("attempt_id is required")
    # Idempotency at the product level: without this, two completed sessions
    # create two live subscriptions for one universe and the user is billed
    # twice (Codex REJECT 2026-08-28 C).
    existing = find_active_subscription(universe_id)
    if existing:
        raise AlreadySubscribed(existing)
    price_id = str(params["price_id"])
    claim = str(params["entitlement_claim"])
    expires_at = int(params["expires_at"])
    # Stripe measures its 30-minute floor from ITS creation time, not from when we
    # froze these params. If a slow preflight or a late retry has eaten the margin,
    # say so instead of sending an expiry Stripe will reject with a generic 400.
    if expires_at < _time.time() + 1800:
        raise BillingUnavailable(
            "this checkout attempt is too old to be given a valid Stripe expiry; "
            "start a new one"
        )
    session = _post(
        "checkout/sessions",
        [
            ("mode", "subscription"),
            # Bound how long this session stays completable. Sending nothing let
            # Stripe apply its 24-HOUR default, so the session outlived the claim
            # guarding it by nearly a day: after the claim expired a second session
            # could be created while the first was still payable, and completing both
            # billed one universe twice (Codex, 2026-08-28). Derived from the attempt
            # anchor, not the clock, so a retry sends byte-identical parameters under
            # the same idempotency key.
            ("expires_at", str(expires_at)),
            ("line_items[0][price]", price_id),
            ("line_items[0][quantity]", "1"),
            ("success_url", str(params["success_url"])),
            ("cancel_url", str(params["cancel_url"])),
            ("client_reference_id", universe_id),
            # On the SESSION, so `checkout.session.completed` / `.expired` carry it.
            # That is what lets a terminal event settle an attempt whose session id we
            # never managed to record -- Stripe can deliver it before we write.
            (f"metadata[{ATTEMPT_ID_KEY}]", attempt_id),
            ("subscription_data[metadata][universe_id]", universe_id),
            (
                f"subscription_data[metadata][{_ENTITLEMENT_VERSION_KEY}]",
                str(params["entitlement_version"]),
            ),
            (
                f"subscription_data[metadata][{_ENTITLEMENT_CLAIM_KEY}]",
                claim,
            ),
        ],
        # Keyed on the ATTEMPT, so a new attempt cannot replay an old session. A
        # wall-clock bucket did the opposite: resubscribing inside the same bucket
        # replayed the original COMPLETED session (Stripe keeps idempotency results
        # ~24h) and handed the user a dead checkout URL.
        #
        # NOT claimed: that a lost response is safely retried. The route releases the
        # claim on any BillingUnavailable, so the retry arrives with a NEW anchor and
        # therefore a new key, and Stripe creates a second session. Closing that needs
        # the claim to remember its session id -- see
        # docs/concerns/2026-08-28-the-checkout-claim-is-not-tied-to-its-session.md.
        # The ATTEMPT is the identity. A retry of this attempt is the same request;
        # a new attempt is a new one. A wall-clock bucket was neither: resubscribing
        # inside the same bucket replayed the original COMPLETED session and handed
        # the user a dead checkout URL.
        idempotency_key="checkout:" + attempt_id,
    )
    return {"id": str(session["id"]), "url": str(session["url"])}


def cancel_subscription(subscription_id: str, *, immediately: bool = False) -> dict:
    """Cancel a subscription.

    Defaults to cancelling at period end — the user paid for the period and should
    keep it. ``immediately`` is for tests and for an explicit "cancel now" request.
    """
    if not subscription_id:
        raise ValueError("subscription_id is required")
    if immediately:
        return _post(f"subscriptions/{subscription_id}/cancel", [])
    return _post(
        f"subscriptions/{subscription_id}", [("cancel_at_period_end", "true")]
    )


def find_active_subscription(universe_id: str) -> str | None:
    """The universe's active subscription id, or None. Used by the cancel path."""
    if not _webhook_secret():
        raise BillingUnavailable("no Stripe webhook secret configured")
    starting_after = ""
    for _page in range(20):  # bounded, but far past the single page it had
        # `all`, not `active`. trialing is treated as PAID here, and past_due /
        # unpaid still bill — searching only `active` made cancel report "no
        # subscription" while billing continued (Codex 2026-08-28).
        path = "subscriptions?status=all&limit=100"
        if starting_after:
            path += f"&starting_after={starting_after}"
        found = _get(path)
        data = found.get("data") or []
        for sub in data:
            if _authorized_subscription_universe(sub) != universe_id:
                continue
            # Terminal states are not cancellable and must not mask a live one.
            if str(sub.get("status") or "") in ("canceled", "incomplete_expired"):
                continue
            return str(sub["id"])
        if not found.get("has_more") or not data:
            return None
        starting_after = str(data[-1]["id"])
    # Ran out of pages while Stripe still says there are more. Returning None here
    # would read as "no subscription" and silently refuse a legitimate cancel, so
    # fail loudly instead (Codex REJECT round 2 C).
    raise BillingUnavailable(
        "could not scan all active subscriptions; cancel not attempted"
    )




#: Written when a webhook signature verifies. See `record_verified_delivery`.
_DELIVERY_MARKER = ".billing_webhook_verified.json"


def verified_delivery_marker_path():
    from tinyassets.storage import data_dir

    return data_dir() / _DELIVERY_MARKER


def record_verified_delivery(*, now: float, livemode: bool) -> None:
    """Remember that a webhook's signature verified, and in which Stripe mode.

    This is the ONLY evidence that the daemon's configured signing secret actually
    belongs to the endpoint Stripe is delivering to. Nothing else can establish it:
    signing secrets are per-endpoint and cannot be read back from the API, so the
    go-live check could otherwise only warn.

    It matters because of a failure that is silent in the worst direction. A live API
    key with a STALE TEST signing secret lets real checkout succeed -- Stripe takes the
    money -- while every entitlement webhook fails signature verification and is
    dropped. The user pays and is never entitled, and nothing in the checkout flow
    looks wrong. Recording a verified delivery turns that into something a go-live
    check can refuse on.

    Best-effort: a marker we cannot write must never cost us a webhook we can process.
    """
    import json as _json

    try:
        path = verified_delivery_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps({"at": float(now), "livemode": bool(livemode)}),
            encoding="utf-8",
        )
    except Exception:
        _log.warning("could not record verified webhook delivery", exc_info=True)


def last_verified_delivery() -> dict | None:
    """The last verified delivery, or None if there has never been one."""
    import json as _json

    try:
        raw = verified_delivery_marker_path().read_text(encoding="utf-8")
        record = _json.loads(raw)
        float(record["at"])
        bool(record["livemode"])
    except Exception:
        return None
    return record


def key_is_live() -> bool:
    """Whether the configured key transacts real money."""
    return _secret_key().startswith("sk_live")


def event_mode_matches_key(event: dict) -> bool:
    """Whether an event was produced in the same Stripe mode as our key.

    Cheap, and it only matters once: at the test -> live switch. Stripe stamps every
    object with ``livemode``, and an endpoint's signing secret is per-endpoint and
    per-mode, so a mode mismatch means one of two things has gone wrong -- a test
    endpoint's secret is still configured on a live deployment, or someone has
    replayed a body from the other mode against a secret that somehow verifies it.

    Neither should entitle anybody. Refusing is the whole point: a leftover test
    secret would otherwise let TEST-mode subscriptions -- which anyone can create for
    free with card 4242 -- grant the paid tier on a live deployment.

    Absent ``livemode`` is treated as a mismatch rather than a pass. Stripe always
    sends it; something that does not is not something to guess about.
    """
    livemode = event.get("livemode")
    if not isinstance(livemode, bool):
        return False
    return livemode == key_is_live()


def verify_webhook_signature(
    payload: bytes, signature_header: str, *, now: float, secret: str = ""
) -> bool:
    """Verify Stripe's `Stripe-Signature`. Constant-time, with replay rejection.

    An unverified webhook must never be able to change a universe's tier — that
    would let anyone who can reach the endpoint grant themselves the paid tier.
    """
    key = (secret or os.environ.get(_WEBHOOK_SECRET_VAR) or "").strip()
    if not key or not signature_header:
        return False
    timestamp = ""
    signatures: list[str] = []
    for part in signature_header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            timestamp = value
        elif name == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        return False
    try:
        age = now - float(timestamp)
    except ValueError:
        return False
    if age > _WEBHOOK_TOLERANCE_S or age < -_WEBHOOK_TOLERANCE_S:
        return False
    expected = hmac.new(
        key.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def subscription_end_from_event(event: dict, *, secret: str = "") -> float | None:
    """When an entitling subscription is scheduled to lapse, or None.

    DISPLAY ONLY. Entitlement is decided by ``subscription_state_from_event`` and
    nothing else; this exists so the app can say "ends on the 27th" instead of
    leaving a user who cancelled unable to tell whether their cancellation took.
    Never gate access on it -- a date is not an authority.

    Returns None whenever the subscription is not both entitling and scheduled to
    end: a renewing subscription has no end to show, and a subscription that is
    already gone is described by the tier itself.

    Goes through the same authorization as the tier, so an unclaimed subscription
    cannot inject a date any more than it can inject entitlement.
    """
    kind = str(event.get("type") or "")
    if not kind.startswith("customer.subscription."):
        return None
    if kind == "customer.subscription.deleted":
        return None
    obj = (event.get("data") or {}).get("object") or {}
    if not _authorized_subscription_universe(obj, secret=secret):
        return None
    if str(obj.get("status") or "") not in ("active", "trialing"):
        return None
    if not obj.get("cancel_at_period_end"):
        return None
    try:
        ends_at = float(obj.get("current_period_end") or 0.0)
    except (TypeError, ValueError):
        return None
    return ends_at if ends_at > 0 else None


def checkout_settlement_from_event(event: dict) -> tuple[str, str] | None:
    """``(session_id, attempt_id)`` for a TERMINAL Checkout Session event, else None.

    Only `completed` and `expired` settle a lease: they are the two ways a session
    stops being payable. Every other session event leaves it open, and releasing the
    lease then would allow a second session beside one the user can still pay.

    This grants nothing. It only identifies which lease may be released -- signature
    verification already established that Stripe sent it, `livemode` already matched,
    and entitlement is decided elsewhere, by the subscription's own signed claim.
    """
    kind = str(event.get("type") or "")
    if kind not in ("checkout.session.completed", "checkout.session.expired"):
        return None
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict):
        return None
    metadata = obj.get("metadata")
    attempt_id = ""
    if isinstance(metadata, dict):
        attempt_id = str(metadata.get(ATTEMPT_ID_KEY) or "")
    session_id = str(obj.get("id") or "")
    if not session_id and not attempt_id:
        return None
    return session_id, attempt_id


def subscription_state_from_event(
    event: dict, *, secret: str = ""
) -> tuple[str, str] | None:
    """Map a Stripe event to ``(universe_id, tier)``, or None if irrelevant.

    Only subscription lifecycle events move a tier. Anything else — including a
    successful payment on its own — is ignored, because the subscription's own
    status is the single fact that decides entitlement.
    """
    kind = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}

    # A Checkout Session does not carry the complete subscription item/price
    # authority needed here. Even a paid completion is therefore non-authoritative;
    # the service-claimed customer.subscription event decides entitlement.
    if kind == "checkout.session.completed":
        return None

    if kind.startswith("customer.subscription."):
        universe_id = _authorized_subscription_universe(obj, secret=secret)
        if not universe_id:
            return None
        status = str(obj.get("status") or "")
        # `active` and `trialing` entitle; everything else (canceled, unpaid,
        # past_due, incomplete_expired) returns the universe to free.
        tier = "paid" if status in ("active", "trialing") else "free"
        if kind == "customer.subscription.deleted":
            tier = "free"
        return universe_id, tier

    return None
