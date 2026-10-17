"""One plan/checkout action for every surface.

Wrappers (the /app routes today; a REST route, an MCP tool, and the in-app
agent tool later) authenticate the caller and pass the identity plus a surface.
They do not decide the plan, the limits, the usage, or whether an upgrade
exists. This module does.

The surface changes only how checkout is reached:

* the return URL Stripe sends the person back to
* the display label
* whether this surface may open a purchase (``CAN_PURCHASE_ON_SURFACE``)

It never changes the plan, the limits, the usage, or upgrade eligibility.
Android is ``False`` until the app is enrolled in Google Play's External
content links program. That one table is the only purchase policy. Enabling
Android later is a one-line change here. There is no in-app checkout URL on a
surface that cannot purchase (``checkout_mode`` is ``unavailable_in_app``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

#: Where a non-web checkout returns the person. Hard Rule 11 names this as the
#: only canonical public endpoint. Web may use a configured resource origin
#: instead; see ``allowed_return_origin``.
CANONICAL_ORIGIN = "https://tinyassets.io"


class Surface(StrEnum):
    """Who is asking. Wrappers pass one of these; they do not invent another."""

    WEB = "web"
    ANDROID = "android"
    API = "api"
    AGENT = "agent"


class UnknownSurface(ValueError):
    """The caller named a surface this action does not have a policy for."""


#: Whether a surface may open a purchase. Android stays false until the Play
#: app is enrolled in External content links. One row, one policy.
CAN_PURCHASE_ON_SURFACE: dict[Surface, bool] = {
    Surface.WEB: True,
    Surface.ANDROID: False,
    Surface.API: True,
    Surface.AGENT: True,
}

_UNAVAILABLE_IN_APP = "unavailable_in_app"


def parse_surface(value: str | None) -> Surface:
    """Read a surface name. Empty means web, the /app default."""
    raw = (value or "").strip().lower()
    if not raw:
        return Surface.WEB
    try:
        return Surface(raw)
    except ValueError as exc:
        raise UnknownSurface(raw) from exc


def allowed_return_origin(requested: str, *, configured_resource: str = "") -> str:
    """An origin Stripe may send a *web* checkout back to.

    Only the canonical public origin or the configured public resource is
    honoured. Everything else gets the canonical origin, because these URLs
    leave our control the moment Stripe has them.

    The request's own Host is deliberately not an input. Nothing in this app
    enforces an allowed-hosts list, so a caller can send ``Host: attacker.example``
    with a matching ``Origin`` and have it echoed straight back. Scheme is
    compared too: matching on host alone accepted ``http://tinyassets.io``.
    """
    origin = (requested or "").strip().rstrip("/").lower()
    if not origin:
        return CANONICAL_ORIGIN
    allowed = {CANONICAL_ORIGIN}
    resource = (configured_resource or "").strip().lower()
    if resource:
        try:
            parts = urlsplit(resource)
        except ValueError:
            parts = None
        if parts is not None and parts.scheme in ("https", "http") and parts.netloc:
            allowed.add(f"{parts.scheme}://{parts.netloc}")
    return origin if origin in allowed else CANONICAL_ORIGIN


def return_origin_for(
    surface: Surface,
    requested: str,
    *,
    configured_resource: str = "",
) -> str:
    """Where checkout returns. Web honours an allowed origin; other surfaces
    that may purchase always return to the canonical app."""
    if surface is not Surface.WEB:
        return CANONICAL_ORIGIN
    return allowed_return_origin(requested, configured_resource=configured_resource)


@dataclass(frozen=True)
class AccountPlan:
    """The structured answer every wrapper returns. No HTML, no HTTP status."""

    surface: Surface
    tier: str
    ends_at: float | None
    seats: int
    interactive_reserve: int
    storage_bytes: int
    upgrade_available: bool
    upgrade_unavailable_reason: str | None
    can_purchase_on_surface: bool
    purchase_unavailable_reason: str | None
    checkout_mode: str
    checkout_url: str | None
    label: str
    billing_enabled: bool
    upgrade_url: str | None
    error: str | None = None
    detail: str | None = None
    resumed: bool = False
    session_id: str | None = None

    def eligibility(self) -> dict[str, Any]:
        """The part that must be identical for every surface."""
        return {
            "tier": self.tier,
            "ends_at": self.ends_at,
            "limits": {
                "seats": self.seats,
                "interactive_reserve": self.interactive_reserve,
                "storage_bytes": self.storage_bytes,
            },
            "upgrade_available": self.upgrade_available,
            "upgrade_unavailable_reason": self.upgrade_unavailable_reason,
            "billing_enabled": self.billing_enabled,
            "upgrade_url": self.upgrade_url,
        }

    def as_dict(self) -> dict[str, Any]:
        """Status payload: the plan, plus the one purchase flag wrappers show."""
        body = self.eligibility()
        body.update(
            {
                "surface": self.surface.value,
                "can_purchase_on_surface": self.can_purchase_on_surface,
                "purchase_unavailable_reason": self.purchase_unavailable_reason,
                "checkout_mode": self.checkout_mode,
                "checkout_url": self.checkout_url,
                "label": self.label,
                # Fields the /app client already reads.
                "enforced": ["seats"],
                "seats": self.seats,
                "interactive_reserve": self.interactive_reserve,
            }
        )
        return body

    def checkout_body(self) -> dict[str, Any]:
        """The checkout route's JSON. A URL, or a classified error. No secrets."""
        if self.checkout_url:
            if self.resumed:
                return {"url": self.checkout_url, "resumed": True}
            body = {"url": self.checkout_url}
            if self.session_id:
                body["id"] = self.session_id
            return body
        body = {"error": self.error or "billing_unavailable"}
        if self.detail:
            body["detail"] = self.detail
        return body


def account_plan(
    identity: Any,
    surface: Surface | str,
    *,
    intent: str = "read",
    requested_origin: str = "",
    configured_resource: str = "",
) -> AccountPlan:
    """Read this person's plan, or start checkout, for one surface.

    ``identity`` is already authenticated. ``intent`` is ``"read"`` or
    ``"checkout"``. The surface never changes eligibility; it only changes the
    label, the return URL, and whether a purchase may open.
    """
    if intent not in ("read", "checkout"):
        raise ValueError("intent must be 'read' or 'checkout'")
    chosen = surface if isinstance(surface, Surface) else parse_surface(str(surface))
    user_id = _user_id(identity)
    snap = _snapshot(identity, user_id)
    can_purchase = CAN_PURCHASE_ON_SURFACE[chosen]
    label = _label(snap["tier"], can_purchase)
    purchase_reason = None if can_purchase else _UNAVAILABLE_IN_APP
    if intent == "read" or not can_purchase:
        if can_purchase:
            mode = "ready" if snap["upgrade_available"] else "unavailable"
        else:
            mode = _UNAVAILABLE_IN_APP
        error = _UNAVAILABLE_IN_APP if intent == "checkout" and not can_purchase else None
        return _plan(
            chosen,
            snap,
            can_purchase=can_purchase,
            purchase_reason=purchase_reason,
            mode=mode,
            label=label,
            error=error,
        )
    started = _start_checkout(
        owner=user_id,
        origin=return_origin_for(
            chosen, requested_origin, configured_resource=configured_resource
        ),
    )
    url = str(started["url"]) if started.get("url") else None
    return _plan(
        chosen,
        snap,
        can_purchase=True,
        purchase_reason=None,
        mode="session" if url else "unavailable",
        label=label,
        checkout_url=url,
        error=None if url else str(started.get("error") or "billing_unavailable"),
        detail=str(started["detail"]) if started.get("detail") else None,
        resumed=bool(started.get("resumed")),
        session_id=str(started["id"]) if started.get("id") else None,
    )


def _user_id(identity: Any) -> str:
    user_id = str(getattr(identity, "user_id", "") or "").strip()
    if not user_id:
        raise PermissionError("Authentication required")
    return user_id


def _label(tier: str, can_purchase: bool) -> str:
    """Display only. A surface that cannot purchase never says Upgrade."""
    if tier == "paid":
        return "Paid plan"
    if can_purchase:
        return "Upgrade"
    return "Free"


def _plan(
    surface: Surface,
    snap: dict[str, Any],
    *,
    can_purchase: bool,
    purchase_reason: str | None,
    mode: str,
    label: str,
    checkout_url: str | None = None,
    error: str | None = None,
    detail: str | None = None,
    resumed: bool = False,
    session_id: str | None = None,
) -> AccountPlan:
    limits = snap["limits"]
    return AccountPlan(
        surface=surface,
        tier=str(snap["tier"]),
        ends_at=snap["ends_at"],
        seats=int(limits.seats),
        interactive_reserve=int(limits.interactive_reserve),
        storage_bytes=int(limits.storage_bytes),
        upgrade_available=bool(snap["upgrade_available"]),
        upgrade_unavailable_reason=snap["upgrade_unavailable_reason"],
        can_purchase_on_surface=can_purchase,
        purchase_unavailable_reason=purchase_reason,
        checkout_mode=mode,
        checkout_url=checkout_url,
        label=label,
        billing_enabled=bool(snap["billing_enabled"]),
        upgrade_url=snap["upgrade_url"],
        error=error,
        detail=detail,
        resumed=resumed,
        session_id=session_id,
    )


def _snapshot(identity: Any, user_id: str) -> dict[str, Any]:
    """Plan, limits, usage, and eligibility. No surface argument, on purpose."""
    from tinyassets.billing.plan_store import BillingPlans
    from tinyassets.billing.stripe_adapter import billing_enabled
    from tinyassets.storage import data_dir
    from tinyassets.usage_policy import TIER_ORDER, limits_for, upgrade_url

    enabled = billing_enabled()
    plan = BillingPlans(data_dir()).read(user_id)
    tier = str(plan["tier"])
    limits = limits_for(tier)
    ends_at = plan["ends_at"]
    ends = float(ends_at) if isinstance(ends_at, (int, float)) else None
    link = upgrade_url(tier)
    if tier == TIER_ORDER[-1].value:
        available, reason = False, "already_subscribed"
    elif not enabled:
        available, reason = False, "billing_disabled"
    else:
        available, reason = True, None
    return {
        "tier": tier,
        "ends_at": ends,
        "limits": limits,
        "billing_enabled": enabled,
        "upgrade_url": link,
        "upgrade_available": available,
        "upgrade_unavailable_reason": reason,
    }


def _start_checkout(*, owner: str, origin: str) -> dict[str, Any]:
    """The existing checkout lease, unchanged. Returns the route's dict."""
    from tinyassets.billing import BillingUnavailable, create_checkout_session
    from tinyassets.billing.plan_store import BillingPlans
    from tinyassets.billing.stripe_adapter import (
        AlreadySubscribed,
        BillingAmbiguous,
        checkout_params,
        key_is_live,
    )
    from tinyassets.storage import data_dir
    from tinyassets.storage.subscription_state import (
        CHECKOUT_SESSION_SECONDS,
        CHECKOUT_WINDOW_SECONDS,
    )

    plans = BillingPlans(data_dir())
    now = time.time()
    mode = "live" if key_is_live() else "test"

    existing = plans.current_checkout_attempt(owner, now=now)
    if existing is not None and existing.get("__corrupt__"):
        return {"error": "checkout_already_in_progress"}

    if existing is not None and existing.get("mode") != mode:
        plans.settle_checkout_attempt(
            owner, attempt_id=str(existing.get("attempt_id") or "")
        )
        existing = None

    if existing is not None and existing.get("url"):
        from tinyassets.observability import emit_checkout_attempt

        emit_checkout_attempt(str(existing.get("attempt_id") or ""))
        return {"url": str(existing["url"]), "resumed": True}

    if existing is not None:
        attempt = existing
    else:
        try:
            params = checkout_params(
                owner_id=owner,
                success_url=origin + "/app?subscribed=1",
                cancel_url=origin + "/app?subscribed=0",
                expires_at=int(now + CHECKOUT_SESSION_SECONDS),
            )
        except AlreadySubscribed:
            return {"error": "already_subscribed"}
        except BillingUnavailable as exc:
            return {"error": "billing_unavailable", "detail": str(exc)}
        attempt = plans.begin_checkout_attempt(
            owner,
            now=now,
            attempt_id=uuid.uuid4().hex,
            mode=mode,
            params=params,
            lease_seconds=CHECKOUT_WINDOW_SECONDS,
        )
        if attempt is None:
            return {"error": "checkout_already_in_progress"}

    attempt_id = str(attempt["attempt_id"])
    from tinyassets.observability import emit_checkout_attempt

    emit_checkout_attempt(attempt_id)
    try:
        out = create_checkout_session(
            owner_id=owner,
            attempt_id=attempt_id,
            params=attempt["params"],
        )
    except AlreadySubscribed:
        plans.settle_checkout_attempt(owner, attempt_id=attempt_id)
        return {"error": "already_subscribed"}
    except BillingAmbiguous as exc:
        return {"error": "billing_unavailable", "detail": str(exc)}
    except BillingUnavailable as exc:
        plans.settle_checkout_attempt(owner, attempt_id=attempt_id)
        return {"error": "billing_unavailable", "detail": str(exc)}
    plans.record_checkout_session(
        owner,
        attempt_id=attempt_id,
        session_id=out["id"],
        url=out["url"],
    )
    return out
