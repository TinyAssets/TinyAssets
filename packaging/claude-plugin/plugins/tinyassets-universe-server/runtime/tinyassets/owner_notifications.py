"""Deliver a request to its owner's own devices, and clear it when answered.

A request is durable and readable from every surface, but nothing ever *told*
the owner one had been raised -- so a universe with something to say could only
wait for them to open the app. This is the other direction.

The boundary, in one paragraph
------------------------------
The destination set is resolved from the **owner of the universe holding the
request**, and from nothing else. No caller, payload, or field of the request
names a device, token, subject or endpoint; the only input that reaches a
transport is a device row the server looked up and a body the server composed.
A dispatch whose raising actor is not that owner sends nothing and says so.

What the notification is allowed to say
---------------------------------------
The **title is the platform's** -- the universe's own display name -- and the
**body is the agent's**, stripped of control characters and bounded. Nothing an
agent writes can occupy the identity position, so no ask can be composed to
read as a platform notice or as another person. Field values never enter a
payload at all: what the owner typed into a request does not come back out on
a lock screen.

Cost
----
No meter, no rate limit, and no notification-specific bound of any kind
(founder, 2026-09-30: account limits are cloud storage and concurrent
agent-run seats, and nothing else). An earlier shape added a per-device
"one outstanding alert" latch to stop an agent raising and withdrawing the
same ask in a loop; it was a rate limiter wearing another name, and it was
where most of two review rounds' defects lived.

What bounds delivery is what already bounds everything else:

* **Seats.** A run that raises a request holds a concurrent agent-run seat
  while it does, so churn is an agent spending its owner's own seat time. That
  is the account limit doing its job, not a hole.
* **The ledger's primary key.** One notification per
  ``(request_id, item_id, device_id, kind)``, claimed in the same transaction
  that reads the destination — so a retry, a redelivery, or a crash mid-send
  never notifies twice.

A deduplicated ask dispatches nothing either, because only a genuinely new
pending row reaches here at all.

Failure
-------
Best effort, always after the fact. Delivery never fails, delays or reorders
the thing that caused it: a raise that cannot notify is still a raised request
in the rail, and an answer that cannot clear is still an answer. Every outcome
is recorded as a class in the content-free ledger, so "nothing arrived" is
diagnosable without keeping a copy of what was said.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from tinyassets.notify import (
    FAILURE_CLASSES,
    OUTCOME_GONE,
    OUTCOME_NO_TRANSPORT,
    OUTCOME_SENT,
    OUTCOME_UNAVAILABLE,
    Notification,
    TransportFailed,
    TransportGone,
    resolve_transports,
)
from tinyassets.storage.owner_devices import (
    KIND_CLEAR,
    KIND_RAISED,
    delivered_devices,
    delivery_targets,
    record_delivery,
    reserve_delivery,
    retire_device,
)

logger = logging.getLogger(__name__)

#: A lock screen is a single line. Longer than this is not read, and a long
#: body is where an ask would try to smuggle a second, official-looking
#: paragraph.
MAX_BODY_CHARS = 180
MAX_TITLE_CHARS = 60
#: Item ids travel so a client can open at the right row. Bounded so one
#: request cannot make an unbounded payload.
MAX_ITEM_IDS = 20

#: The whole payload has to fit one web-push record (RFC 8291), and the title
#: and body are the only parts that vary in size. Bounded in BYTES, because a
#: character budget is not a size budget: a 120-emoji title is 120 characters
#: and 480 bytes, and JSON-escaping non-ASCII inflated it six-fold, so an ask
#: the request API accepted produced 4164 bytes against a 4079-byte record and
#: was refused at the transport -- a notification silently lost to input shape
#: (gpt-6-astra round 2, 2026-09-29).
MAX_TITLE_BYTES = 200
MAX_BODY_BYTES = 600

#: Anything that is not printable text. Newlines included: a body that can
#: start a new line can fake a second notification inside one.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
#: Characters that OCCUPY NO SPACE, by Unicode CATEGORY rather than by a list
#: of ranges. A hand-listed range missed U+2067 and U+061C -- both bidi
#: controls -- so a name made of them still rendered as an invisible identity
#: line, and one of them could re-open the suffix visually (gpt-6-astra,
#: 2026-09-30). Enumerating a class the standard already names is how the list
#: ends up incomplete; `Cf` (format) plus `Cc`/`Cs`/`Co`/`Cn` is the class, and
#: `unicodedata` is the authority for membership.
_HIDDEN_CATEGORIES = frozenset({"Cf", "Cc", "Cs", "Co", "Cn"})


def _visible(value: str) -> str:
    """``value`` with zero-width, format and control characters removed.

    Removed rather than replaced with a space: a name of them is not a name,
    and substituting spaces would leave a title of blanks that still looks
    like an identity.
    """
    return "".join(
        ch for ch in value
        if unicodedata.category(ch) not in _HIDDEN_CATEGORIES
    )


def _flat(value: Any, limit: int, *, byte_limit: int = 0) -> str:
    """One line of printable text, bounded in characters and optionally bytes.

    ``byte_limit`` is what actually matters for anything that reaches a
    payload: the record a web push fits into is measured in bytes, and a
    character cap is not a size cap. Truncation is on a UTF-8 boundary, so a
    bounded string never ends in half a character.
    """
    text = _visible(_CONTROL.sub(" ", str(value or ""))).strip()[:limit]
    if byte_limit and len(text.encode("utf-8")) > byte_limit:
        text = text.encode("utf-8")[:byte_limit].decode("utf-8", "ignore").strip()
    return text


#: The title's fixed, server-owned tail. Every request notification carries it,
#: so the identity line always says WHAT this is -- a universe asking its owner
#: something -- and not merely who. Without it the unnamed-universe fallback
#: rendered a bare "TinyAssets", and an ask with kind "TinyAssets" and a
#: security-shaped title read as a platform notice (gpt-6-astra, 2026-09-29).
#: No agent-supplied string can produce this suffix, because agent text only
#: ever reaches the body.
_SOURCE_SUFFIX = " asks"
#: When the universe has no name of its own. Deliberately not the product name:
#: "TinyAssets" in the identity position is exactly the impersonation the
#: suffix exists to prevent.
_UNNAMED = "Your agent"


def _universe_title(base: Path, universe_id: str) -> str:
    """The identity line: "<the universe's own name> asks".

    The **structure** is server-owned: the suffix is appended here and nothing
    an ask supplies can reach the title, nor produce the suffix. The **name**
    is the universe's own, read off its record -- which is the owner's content,
    and on a universe that names itself from its soul is content its agent
    influenced. That is a real limit and is stated rather than papered over
    (``docs/concerns/2026-09-29-a-universe-can-name-itself-anything.md``): what
    is enforced here is that the name is one line of visible text, is not the
    bare product name, and cannot fake the structure.

    An unreadable record, a missing name, or a name that is merely the universe
    id all fall back to a neutral phrase.
    """
    try:
        from tinyassets.daemon_server import get_universe

        name = _flat(
            get_universe(base, universe_id=universe_id).get("display_name"),
            MAX_TITLE_CHARS - len(_SOURCE_SUFFIX),
            byte_limit=MAX_TITLE_BYTES - len(_SOURCE_SUFFIX.encode("utf-8")),
        )
    except Exception:  # noqa: BLE001 - a name we cannot read is not a reason to leak one
        name = ""
    # A name that already ends in the suffix would render "x asks asks", which
    # reads like a malfunction and is a way to make the structure look
    # accidental (gpt-6-astra round 2, 2026-09-29).
    while name.endswith(_SOURCE_SUFFIX):
        name = name[: -len(_SOURCE_SUFFIX)].rstrip()
    if not name or name == universe_id:
        name = _UNNAMED
    return name + _SOURCE_SUFFIX


def _compose(base: Path, universe_id: str, request: dict[str, Any]) -> Notification:
    """What the owner sees. Identity server-side, words agent-side, ids only."""
    kind = _flat(request.get("kind"), 24, byte_limit=96)
    title = _flat(request.get("title"), MAX_BODY_CHARS, byte_limit=MAX_BODY_BYTES)
    body = f"{kind}: {title}" if kind else title
    items = [
        _flat(i.get("item_id"), 64)
        for i in (request.get("items") or [])
        if isinstance(i, dict) and i.get("item_id")
    ]
    data = {
        "kind": "request",
        "request_id": _flat(request.get("request_id"), 64),
        "universe_id": _flat(universe_id, 64),
        "request_kind": kind,
    }
    if items:
        # Item ids are ASCII by validation, so the character bound IS a byte
        # bound here -- but the JOIN is what has to fit, not each id.
        joined = ",".join(items[:MAX_ITEM_IDS])[:MAX_ITEM_IDS * 65]
        data["item_ids"] = joined
        data["item_count"] = str(len(items))
    notification = Notification(
        title=_universe_title(base, universe_id),
        body=_flat(body, MAX_BODY_CHARS, byte_limit=MAX_BODY_BYTES)
        or "Something needs you.",
        data=data,
    )
    # The whole thing, as the web transport will serialise it. Composing each
    # part to its own bound still let the SUM exceed one record, and the
    # transport correctly refused to send -- losing the notification to input
    # shape (gpt-6-astra round 2, 2026-09-29). Trimming the body is the right
    # give: the identity line and the ids are what make it actionable.
    return _within_record(notification)


def _within_record(notification: Notification) -> Notification:
    """Trim the body until the serialised payload fits one web-push record."""
    from tinyassets.notify.webpush import payload_bytes, record_budget

    budget = record_budget()
    body = notification.body
    while body and len(payload_bytes(notification)) > budget:
        # Halve, then settle: a linear walk over a 4 KB body is pointless work.
        body = body[: max(1, len(body) // 2)]
        notification = Notification(
            title=notification.title, body=body, data=notification.data,
            silent=notification.silent,
        )
    if len(payload_bytes(notification)) > budget:
        # The identity line and the ids alone do not fit, which means the ids
        # are the size. Drop the optional ones rather than send nothing.
        trimmed = {
            k: v for k, v in notification.data.items() if k != "item_ids"
        }
        notification = Notification(
            title=notification.title, body=notification.body, data=trimmed,
            silent=notification.silent,
        )
    return notification


#: What a transport is allowed to RETURN. The exception paths were already
#: bounded to a closed set; the success path was not, so a transport that
#: returned a string instead of the literal outcome had it stored in the ledger
#: and handed back in the dispatch result -- a leak channel reachable through
#: the return value rather than through an exception (gpt-6-astra, 2026-09-30).
#: The shipped transports return the fixed literal; the boundary belongs here
#: anyway, exactly as it does for `retired_reason`.
_ALLOWED_OUTCOMES = frozenset({
    OUTCOME_SENT, OUTCOME_GONE, OUTCOME_NO_TRANSPORT, *FAILURE_CLASSES,
})


def _bounded_outcome(value: object) -> str:
    """A transport's return value, mapped onto the closed outcome set."""
    if isinstance(value, str) and value in _ALLOWED_OUTCOMES:
        return value
    logger.warning(
        "owner_notifications: transport returned %s outside the outcome set",
        type(value).__name__,
    )
    return OUTCOME_UNAVAILABLE


def _owner_of(base: Path, universe_id: str) -> str:
    """The universe's admin owner, or "". The ONLY routing key there is."""
    try:
        from tinyassets.daemon_server import list_universe_acl

        admins = [
            str(row.get("actor_id") or "")
            for row in list_universe_acl(base, universe_id=universe_id)
            if row.get("permission") == "admin"
        ]
    except Exception:  # noqa: BLE001 - an unreadable ACL routes nowhere
        logger.warning(
            "owner_notifications: ACL unreadable for %r", universe_id, exc_info=True,
        )
        return ""
    # Exactly one admin is the shape a universe has. Several means the routing
    # key is ambiguous, and guessing which person a request is "really" for is
    # how a notification reaches the wrong one -- so it reaches neither.
    return admins[0] if len(admins) == 1 else ""


def _dispatch(
    base: Path,
    *,
    owner_user_id: str,
    request_id: str,
    notification: Notification,
    kind: str,
    item_id: str = "",
    only_devices: list[str] | None = None,
    skip_device_id: str = "",
    transports: dict | None = None,
) -> dict[str, Any]:
    """Send to each of the owner's live devices, once each. Never raises.

    ``only_devices`` narrows to a known set -- used by the clear, which goes
    only to destinations that actually received the notification.
    """
    available = resolve_transports() if transports is None else transports
    targets = [
        d for d in delivery_targets(base, owner_user_id=owner_user_id)
        if d["device_id"] != skip_device_id
        and (only_devices is None or d["device_id"] in only_devices)
    ]
    outcomes: dict[str, str] = {}
    for device in targets:
        device_id = device["device_id"]
        transport = available.get(device["platform"])
        if transport is None:
            # Truthful, and not a receipt: nothing was sent and nothing claims
            # it was. Logged at INFO because an unconfigured deployment is a
            # normal state, not an incident.
            outcomes[device_id] = OUTCOME_NO_TRANSPORT
            logger.info(
                "owner_notifications: no %s transport configured; request %s "
                "not delivered to %s", device["platform"], request_id, device_id,
            )
            continue
        claim = reserve_delivery(
            base, request_id=request_id, device_id=device_id,
            kind=kind, item_id=item_id, owner_user_id=owner_user_id,
        )
        if "token" not in claim:
            outcomes[device_id] = claim["refused"]
            continue
        # The token comes from the CLAIM, not from the snapshot above: a
        # registration that moved this handset to another account between the
        # two would otherwise still receive this owner's private title.
        destination = {**device, "token": claim["token"]}
        try:
            outcome = _bounded_outcome(transport(destination, notification))
        except TransportGone as exc:
            outcome = OUTCOME_GONE
            retire_device(
                base, owner_user_id=owner_user_id, device_id=device_id,
                reason=str(exc)[:80],
            )
        except TransportFailed as exc:
            outcome = exc.cls
        except Exception as exc:  # noqa: BLE001 - a transport must never break the caller
            # The CLASS of exception and nothing else. A transport's exception
            # text is a credential-leak channel, and exc_info=True put a bearer
            # token from a raising transport into the log while the test that
            # claimed "leaks nothing" only checked the ledger and the return
            # value (gpt-6-astra, 2026-09-29).
            logger.warning(
                "owner_notifications: %s transport raised %s outside its "
                "contract for device %s",
                device["platform"], type(exc).__name__, device_id,
            )
            outcome = OUTCOME_UNAVAILABLE
        outcomes[device_id] = outcome
        record_delivery(
            base, request_id=request_id, device_id=device_id, kind=kind,
            outcome=outcome, item_id=item_id,
        )
    return {
        "devices": len(targets),
        "sent": sum(1 for o in outcomes.values() if o == OUTCOME_SENT),
        "outcomes": outcomes,
    }


def notify_request_raised(
    base_path: str | Path,
    *,
    universe_id: str,
    raised_by: str,
    request: dict[str, Any],
    transports: dict | None = None,
) -> dict[str, Any]:
    """A NEW request was stored: tell its owner's devices.

    ``raised_by`` is the actor the owner gate already verified. It is checked
    against the universe's admin owner rather than trusted: a request raised by
    anyone who is not that owner notifies nobody, because there is no correct
    person to send it to.
    """
    base = Path(base_path)
    request_id = str(request.get("request_id") or "")
    if not request_id:
        return {"skipped": "no_request_id"}
    owner = _owner_of(base, universe_id)
    if not owner:
        return {"skipped": "owner_unresolved"}
    if owner != (raised_by or "").strip():
        # Fail closed. The alternative -- notify the resolved owner anyway --
        # would let anyone with write access put a notification on someone
        # else's phone.
        logger.warning(
            "owner_notifications: request %s raised by a non-owner actor in %r; "
            "nothing dispatched", request_id, universe_id,
        )
        return {"skipped": "actor_is_not_owner"}
    return _dispatch(
        base, owner_user_id=owner, request_id=request_id,
        notification=_compose(base, universe_id, request), kind=KIND_RAISED,
        transports=transports,
    )


def clear_request(
    base_path: str | Path,
    *,
    universe_id: str,
    answered_by: str,
    request_id: str,
    skip_device_id: str = "",
    transports: dict | None = None,
) -> dict[str, Any]:
    """The owner answered somewhere: take it off their OTHER devices.

    Silent and content-free -- it carries the ids the app needs to cancel a
    local notification and nothing else. ``skip_device_id`` is the device they
    answered on, when the client says which; the app cancels its own locally,
    so pushing a clear back at it would be a second wake for one act.

    Goes ONLY to destinations this request's notification actually reached,
    read off the delivery ledger. A device that never received one has nothing
    to take down, so it is not woken -- which is also what keeps an itemised
    note from costing a push per item: the clear is per REQUEST, because a
    notification is per request. Per-item state is what the rail shows when the
    app opens.
    """
    base = Path(base_path)
    owner = _owner_of(base, universe_id)
    if not owner or owner != (answered_by or "").strip():
        return {"skipped": "actor_is_not_owner"}
    holding = delivered_devices(
        base, owner_user_id=owner, request_id=request_id,
    )
    if not holding:
        return {"skipped": "no_alert_outstanding", "devices": 0, "sent": 0,
                "outcomes": {}}
    notification = Notification(
        title="", body="", silent=True,
        data={
            "kind": "clear",
            "request_id": _flat(request_id, 64),
            "universe_id": _flat(universe_id, 64),
        },
    )
    return _dispatch(
        base, owner_user_id=owner, request_id=request_id,
        notification=notification, kind=KIND_CLEAR,
        only_devices=holding, skip_device_id=skip_device_id,
        transports=transports,
    )


def clear_for_universe_dir(
    universe_dir: str | Path,
    *,
    request_id: str,
    transports: dict | None = None,
) -> dict[str, Any]:
    """The resolution seam: clear from wherever a request was just resolved.

    Sits beside ``emit_pending_request_answered`` and derives the same two
    things the same way -- the universe from the directory, the actor from the
    live request -- so EVERY surface that resolves a request clears the owner's
    other devices, and there is exactly one definition of when that happens.
    A background resolver with no bound actor clears nothing, which is correct:
    nobody answered on a device.

    There is no item variant. A notification is per REQUEST, so answering one
    item of fifty changes nothing a device is displaying; pushing a clear per
    item made a 50-item note cost 51 wakeups per device (gpt-6-astra,
    2026-09-29). The rail shows per-item state when the app opens.

    Never raises. The answer is already written.
    """
    try:
        from tinyassets.api.permissions import current_request_actor_id

        udir = Path(universe_dir)
        return clear_request(
            udir.parent, universe_id=udir.name,
            answered_by=current_request_actor_id(),
            request_id=request_id, transports=transports,
        )
    except Exception:  # noqa: BLE001 - the answer stands
        logger.warning(
            "owner_notifications: could not clear %s on the owner's other devices",
            request_id, exc_info=True,
        )
        return {"skipped": "clear_failed"}


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_TITLE_CHARS",
    "clear_for_universe_dir",
    "clear_request",
    "notify_request_raised",
]
