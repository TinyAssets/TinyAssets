"""The one connection the platform offers a new universe: a way to report gaps.

Founder, 2026-09-30:

    "for new users the request that enables connection to the tinyassets universe
    for patch requests should be already there when they first login just like
    the connect another llm one is already there from the start."

A *patch request* is how a user's universe tells TinyAssets about a gap -- a bug,
a missing capability, an idea. It files one itself, mid-turn, without
interrupting its user. The receiving side is an ordinary user's intake: the
founder's universe built it through the app like anybody else, and its owner's
own gate decides each request. The founder's universe is *one* intake owner, not
a privileged one, which is exactly why the platform must be told WHICH intake to
offer rather than knowing one by name -- see :func:`configured_intake`.

Why a seeded consent request rather than an automatic connection
----------------------------------------------------------------
Sending a patch request means sending the user's words to ANOTHER USER. That is
the cross-user floor, so it is the user's yes to give, not the platform's to
assume. The ask is seeded into the same "Waiting on you" rail the model-connect
entry uses, with nothing to paste: approving it records ONE grant naming exactly
the configured intake.

What went wrong without it (live, 2026-09-30, free account
``u-01ky3zh1arr8qth8jee7zx63pq``): with no connection and nothing telling it one
existed, the universe invented its own pending request asking its user for a
bearer token -- a credential field for an address that needs no credential, and a
help link that 404ed. A universe cannot ask for a connection nobody told it
about, so the platform seeds this one, exactly as it synthesizes the
model-connect entry.

The grant is the authority, and it is narrow
--------------------------------------------
The grant is an ordinary effector consent: sink ``patch_intake``, destination
the intake's ``receiver_id``. ``effector_consents`` matches destinations exactly
and has no wildcards, so one grant can only ever name one intake -- "send-only
access to that one intake, nothing else" is structural rather than promised.
:func:`require_send_consent` is where it bites: a delivery to the configured
intake without an active grant is refused, so approving the ask is what actually
creates the connection.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Effector-consent sink for "this universe may send patch requests to <intake>".
#: Its destination is the intake's ``receiver_id`` verbatim, so the grant cannot
#: describe a wider reach than one receiver.
PATCH_INTAKE_SINK = "patch_intake"

#: The pending-request action type whose answer records the grant above.
ACTION_TYPE = "grant_patch_intake"

#: Tab header for the seeded ask. Bounded by ``_MAX_KIND_CHARS`` (24).
REQUEST_KIND = "TinyAssets"

#: Which intake new universes are offered. NOT a universe id and NOT a code
#: constant: the platform names the intake, and any user could own it.
RECEIVER_ID_VAR = "TINYASSETS_PATCH_INTAKE_RECEIVER_ID"

#: What the platform calls that intake on screen. Display text only; it confers
#: nothing and names no universe.
LABEL_VAR = "TINYASSETS_PATCH_INTAKE_LABEL"

DEFAULT_LABEL = "TinyAssets"

#: A receiver id is an address: bounded, no whitespace, and never ``*`` (which
#: ``storage/receiver_links._name`` refuses everywhere for the same reason -- a
#: sentinel that means "any" must not be spellable as a name).
_RECEIVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

#: Bounded, single-line, printable. The label lands in prose a person reads.
_MAX_LABEL_CHARS = 48


class PatchIntakeMisconfigured(ValueError):
    """The intake is configured, but not to something that can be offered.

    Distinct from "unset" on purpose: unset means the platform offers no intake
    and says nothing, which is a legitimate deployment. A present-but-invalid
    value is an operator error, and silently treating it as unset is the mock
    fallback Hard Rule 8 forbids.
    """


class PatchIntakeConsentMissing(ValueError):
    """A delivery to the configured intake with no grant behind it."""


def configured_intake() -> dict[str, str] | None:
    """``{"receiver_id", "label"}`` for the offered intake, or ``None`` if unset.

    Raises :class:`PatchIntakeMisconfigured` when a value is present but cannot
    be an address or a label. Callers on a user-facing read path catch that and
    log it rather than breaking the rail; callers deciding authority let it
    propagate.
    """
    raw = os.environ.get(RECEIVER_ID_VAR)
    if raw is None or not raw.strip():
        return None
    receiver_id = raw.strip()
    if not _RECEIVER_ID_RE.match(receiver_id):
        raise PatchIntakeMisconfigured(
            f"{RECEIVER_ID_VAR} must be 8-128 characters of [A-Za-z0-9._:-] "
            f"naming one receiver; got {receiver_id!r}"
        )
    label = (os.environ.get(LABEL_VAR) or DEFAULT_LABEL).strip()
    if not label or len(label) > _MAX_LABEL_CHARS or not label.isprintable():
        raise PatchIntakeMisconfigured(
            f"{LABEL_VAR} must be 1-{_MAX_LABEL_CHARS} printable characters on "
            f"one line; got {label!r}"
        )
    return {"receiver_id": receiver_id, "label": label}


def _intake_or_none(where: str) -> dict[str, str] | None:
    """Read the configuration on a path that must never raise into a user's face."""
    try:
        return configured_intake()
    except PatchIntakeMisconfigured as exc:
        # LOUD, and not once-per-process quiet: an operator reading logs is the
        # only person who can fix this, and nothing else in the product will
        # complain. Nothing is seeded and nothing is granted, so no surface
        # pretends the connection exists.
        logger.error("patch intake is misconfigured (%s): %s", where, exc)
        return None


def consent_is_active(universe_dir: str | Path, receiver_id: str) -> bool:
    """Whether this universe currently holds the grant for exactly this intake."""
    from tinyassets.storage.effector_consents import is_consent_active

    if not receiver_id:
        return False
    return is_consent_active(
        universe_dir, sink=PATCH_INTAKE_SINK, destination=receiver_id
    )


def grant_send_consent(
    universe_dir: str | Path, *, receiver_id: str, granted_by: str
) -> dict[str, Any]:
    """Record the one grant. Raises for an id that is not an address."""
    from tinyassets.storage.effector_consents import grant_consent

    if not _RECEIVER_ID_RE.match(str(receiver_id or "")):
        raise ValueError("a patch-intake grant names one receiver id")
    return grant_consent(
        universe_dir,
        sink=PATCH_INTAKE_SINK,
        destination=receiver_id,
        granted_by=granted_by,
    )


def require_send_consent(*, universe_dir: str | Path, receiver_id: str) -> None:
    """Fence the configured intake behind its grant. Other receivers untouched.

    Only the intake the PLATFORM offers is gated here, and deliberately: every
    other receiver is one this universe found itself, where the receiving owner's
    own exposure (``open_to_all`` / ``allowed_senders``) is the whole authority
    and the platform has no consent to hold. The offered intake is different --
    the platform put the address in front of the universe, so the platform holds
    the user's yes for it.

    "Offered" is a per-universe FACT, not the current value of an env var. The
    requirement follows the ask this universe was actually shown, so unsetting
    or retargeting the variable cannot un-fence an intake a universe already
    holds a link to (gpt-6-astra refute round on PR #4121, P1: configure A,
    connect, unset, and the same link delivered without consent). The current
    configuration is also honoured, so a freshly configured intake is fenced
    before its first ask is seeded.

    What is NOT gated: a receiver this universe found on its own, which the
    platform never put in front of it. There the receiving owner's exposure is
    the only authority there is, and gating it would break ordinary cross-user
    work. A present-but-invalid configuration therefore gates only what was
    already offered -- it offers nothing new, so there is nothing new to fence.
    """
    address = str(receiver_id or "")
    if not address:
        return
    # Cheapest first: an active grant ends the question for any receiver.
    if consent_is_active(universe_dir, address):
        return
    intake = _intake_or_none("fencing a delivery")
    offered = intake is not None and intake["receiver_id"] == address
    label = intake["label"] if offered else DEFAULT_LABEL
    if not offered:
        # Was this universe ever OFFERED this address? Only reached for a
        # delivery with no patch-intake grant to a receiver that is not the
        # current intake -- i.e. ordinary cross-user sends pay one indexed read.
        prior = _offer_for(universe_dir, address)
        if prior is None:
            return
        label = str((prior.get("action") or {}).get("label") or DEFAULT_LABEL)
    raise PatchIntakeConsentMissing(
        "patch_intake_consent_required: sending to the "
        f"{label} patch intake needs the owner's approval of the "
        f'"Let your command center report problems to {label}" request in their '
        "rail; nothing has been sent"
    )


def _seeded_rows(universe_dir: Path) -> list[dict[str, Any]]:
    from tinyassets.storage.pending_requests import find_by_action_type

    return find_by_action_type(universe_dir, ACTION_TYPE)


def _for_receiver(rows: list[dict[str, Any]], receiver_id: str) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if str((row.get("action") or {}).get("receiver_id") or "") == receiver_id
    ]


def _offer_for(universe_dir: Path, receiver_id: str) -> dict[str, Any] | None:
    """The most recent ask that offered this universe this exact address."""
    try:
        rows = _for_receiver(_seeded_rows(universe_dir), receiver_id)
    except Exception:  # noqa: BLE001 - an unreadable history must not open a gate
        logger.warning("patch intake: offer history unreadable", exc_info=True)
        return None
    return rows[0] if rows else None


#: Statuses that mean THE OWNER decided. ``withdrawn`` is deliberately absent:
#: an agent shares its user's principal and can raise -- then withdraw -- an ask
#: of this type, which would have suppressed the platform's offer for good
#: (gpt-6-astra refute round on PR #4121). The platform retires its own obsolete
#: cards to the same status, and those are not decisions either.
_OWNER_DECIDED = frozenset({"answered", "dismissed"})


def _already_answered(rows: list[dict[str, Any]], receiver_id: str) -> bool:
    """Whether this user already decided THIS intake, however they decided it.

    Keyed on the receiver id rather than on the request's dedupe key, because the
    dedupe key is a hash of the rendered text: reword the title and every past
    answer stops matching, so a user who declined would be asked again on the
    next deploy. The intake's address is what the decision was actually about.
    """
    return any(
        row["status"] in _OWNER_DECIDED
        for row in _for_receiver(rows, receiver_id)
    )


def request_payload(intake: dict[str, str]) -> dict[str, Any]:
    """The seeded ask, in the user's words. No fields: there is nothing to paste."""
    label = intake["label"]
    return {
        "kind": REQUEST_KIND,
        "title": f"Let your command center report problems to {label}",
        "body": (
            f"When your command center hits a bug, a missing feature or an idea worth "
            f"building, it can tell {label} directly instead of stopping. "
            "Approving this lets it send those reports -- what it was trying to "
            "do and what was missing -- and nothing else: not your files, not "
            "your conversations, not your other work. Whoever runs the intake "
            "sees who sent it and what your command center sent them, nothing more, "
            "and you can take this back at any time. There is nothing to paste."
        ),
        "fields": [],
        "action": {"type": ACTION_TYPE, "receiver_id": intake["receiver_id"],
                   "label": label},
    }


def _retire_obsolete_offers(
    universe_dir: Path, rows: list[dict[str, Any]], receiver_id: str
) -> None:
    """Take down a still-pending ask for an intake that is no longer offered.

    Without this, a pending card for the OLD address blocked the new one from
    ever being seeded, so the promise ``patch_intake_changed`` makes -- "it will
    be re-offered with the current one" -- was never kept (gpt-6-astra refute
    round on PR #4121). Retired rather than answered or dismissed: the owner
    decided nothing, and neither of those statuses would be honest about it.
    """
    from tinyassets.storage.pending_requests import retire_platform_request

    for row in rows:
        if row["status"] != "pending":
            continue
        if str((row.get("action") or {}).get("receiver_id") or "") == receiver_id:
            continue
        retire_platform_request(
            universe_dir, row["request_id"],
            reason="this intake is no longer the one TinyAssets offers",
        )


def rail_entry(universe_id: str, universe_dir: Path) -> dict[str, Any] | None:
    """Seed the ask if it is owed, then describe the RESULT for the agent.

    One pass, because the two halves answer the same question and a rail read
    that computed them separately contradicted itself: the block said "the
    request is already waiting in their rail" whenever the grant was absent,
    including right after the owner declined it and nothing was waiting at all
    (gpt-6-astra refute round on PR #4121). The guidance is therefore built from
    what is actually in the rail after seeding, not from the grant alone.

    Runs on the rail read, the same code path every account and every surface
    already uses -- so the ask is there at a new user's first sign-in for the
    same reason the model-connect entry is, and an EXISTING user gets it on
    their next sign-in with no migration. Never raises: a rail that cannot
    decide must still render.

    Idempotent by four checks, cheapest first: no intake configured, the grant
    is already held, an ask for this intake is already pending, or the owner
    already answered/cleared one for this intake. ``create_request`` adds two
    more of its own -- an identical pending dedupe key is returned rather than
    duplicated, and a standing "don't ask me this again" is honoured.
    """
    intake = _intake_or_none("the rail entry")
    if intake is None:
        return None
    address, label = intake["receiver_id"], intake["label"]
    granted = pending = decided = False
    try:
        granted = consent_is_active(universe_dir, address)
        if not granted:
            rows = _seeded_rows(universe_dir)
            _retire_obsolete_offers(universe_dir, rows, address)
            mine = _for_receiver(rows, address)
            pending = any(row["status"] == "pending" for row in mine)
            decided = _already_answered(rows, address)
            if not pending and not decided:
                pending = _seed(universe_id, intake)
    except Exception:  # noqa: BLE001 - the rail must render either way
        logger.warning("patch intake: rail entry could not be resolved", exc_info=True)
    return {
        "receiver_id": address,
        "label": label,
        "granted": granted,
        "request_pending": pending,
        "how": _how(address, label, granted=granted, pending=pending),
    }


def _seed(universe_id: str, intake: dict[str, str]) -> bool:
    """Raise the platform's ask. True when one is now pending."""
    import json

    from tinyassets.api.pending_requests import request_from_user

    result = request_from_user(
        universe_id=universe_id,
        origin="platform",
        payload=json.dumps(request_payload(intake)),
    )
    if isinstance(result, dict) and result.get("error"):
        # Reported, never silent: the rail still renders, but an operator can see
        # that the one connection the platform offers did not reach this user.
        logger.warning(
            "patch intake: seeding the consent request failed for %s: %s",
            universe_id,
            result.get("detail") or result.get("error"),
        )
        return False
    return isinstance(result, dict) and bool(result.get("request_id"))


def _how(receiver_id: str, label: str, *, granted: bool, pending: bool) -> str:
    """The short version of the ``delivering`` chapter, for the state it is in.

    Carried on the rail rather than in the resident tool description because the
    agent polls the rail anyway: it costs no per-round bytes and it is current.
    Specific enough that a universe never invents a credential ask for an
    address that needs no credential -- and it must never claim an ask is
    waiting when none is, or the agent tells its user to go tap something that
    is not there.
    """
    if granted:
        return (
            'Use write_graph target="patch_request" operation="send" with '
            'payload_json={"title": "One line", "details": "What I tried and what was missing"}. '
            "No credential, URL or token is involved."
        )
    if pending:
        return (
            f"Your user has not approved sending to {label} yet. The request "
            f'"Let your command center report problems to {label}" is waiting in their '
            "rail -- point them at that one. Do NOT raise a connection or "
            "credential request for this: there is no token, and approving that "
            "one request is the whole setup."
        )
    return (
        f"Your user has already declined or cleared sending to {label}, so "
        "nothing is waiting and reports cannot be sent. Respect that: do NOT "
        "raise another request for it, and do NOT raise a connection or "
        "credential request -- there is no token involved. If they bring it up "
        "themselves, they can lift it from the muted list in their rail."
    )


def send_patch_request(universe_id: str, principal_id: str, title: Any, details: Any) -> dict:
    """Send through an owner-authored private source and the native delivery core."""
    import hashlib
    import json
    from uuid import uuid4

    from tinyassets.api import deliveries, receiver_links
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import create_branch_definition_once
    from tinyassets.storage import receiver_links as store

    example = {"title": "One line", "details": "What I tried and what was missing"}
    for field, value, limit in (("title", title, 120), ("details", details, 8000)):
        if value is None:
            return {"error": "patch_request_field_missing", "field": field, "example": example}
        if (not isinstance(value, str) or not value.strip() or len(value) > limit
                or (field == "title" and value.splitlines() != [value])):
            return {"error": "patch_request_field_invalid", "field": field, "example": example}
    intake = configured_intake()
    if intake is None:
        return {"error": "patch_intake_unavailable"}
    universe_dir = _universe_dir(universe_id)
    address = intake["receiver_id"]
    try:
        require_send_consent(universe_dir=universe_dir, receiver_id=address)
    except PatchIntakeConsentMissing:
        view = rail_entry(universe_id, universe_dir)
        return {"error": "patch_intake_consent_required", "how": view["how"]}

    # The engine binds this identity from its pins, never from the report payload.
    if receiver_links._principal(write=True) != principal_id:
        raise PermissionError("patch request principal does not match the bound identity")
    base = receiver_links._base()
    receiver_links._require_admin(base, universe_id, principal_id)
    receiver = receiver_links.inspect_receiver(receiver_id=address)
    outputs = _report_outputs(receiver["contract"], title, details)
    branch_id = "patch-report-" + hashlib.sha256(
        json.dumps([universe_id, principal_id]).encode()
    ).hexdigest()
    branch = BranchDefinition(
        branch_def_id=branch_id, name="Report to TinyAssets", author=principal_id,
        visibility="private", entry_point="report",
        node_defs=[NodeDefinition(
            node_id="report", display_name="Report to TinyAssets", output_keys=list(outputs),
        )],
        graph_nodes=[GraphNodeRef(id="report", node_def_id="report")],
        edges=[EdgeDefinition("START", "report"), EdgeDefinition("report", "END")],
        state_schema=[{"name": key, "type": "str"} for key in outputs],
    )
    create_branch_definition_once(base, branch_def=branch.to_dict())
    # Serialize lookup + connect under the same author-store reservation used by
    # native link management. The storage connect retains contract/generation checks.
    with receiver_links._owner_authority(base, universe_id, principal_id):
        owned = receiver_links._owned_branch(base, universe_id, branch_id, principal_id)
        if (owned.visibility != "private" or owned.author != principal_id
                or owned.graph_nodes != branch.graph_nodes
                or len(owned.node_defs) != 1
                or set(owned.node_defs[0].output_keys) != set(outputs)):
            raise ValueError("patch request source changed; restore its private output contract")
        with store.transaction(base) as conn:
            link = conn.execute(
                "SELECT link_id FROM graph_output_links WHERE owner_id=? AND universe_id=? "
                "AND branch_def_id=? AND node_id=? AND receiver_id=? "
                "AND receiver_generation=? AND mapping_json=? AND disconnected_at IS NULL",
                (principal_id, universe_id, branch_id, "report", address,
                 receiver["generation"], store._json({key: key for key in outputs})),
            ).fetchone()
        link_id = link["link_id"] if link else store.connect_output(
            base, owner_id=principal_id, universe_id=universe_id, branch_def_id=branch_id,
            node_id="report", receiver_id=address, expected_generation=receiver["generation"],
            mapping={key: key for key in outputs},
        )["link_id"]
    try:
        receipt = deliveries.deliver_output(
            universe_id=universe_id, link_id=link_id, occurrence_id=uuid4().hex, outputs=outputs,
        )
    except PatchIntakeConsentMissing:
        # A revocation between provisioning and acceptance still gets the same guidance.
        view = rail_entry(universe_id, universe_dir)
        return {"error": "patch_intake_consent_required", "how": view["how"]}
    return {"sent": True, "delivery_id": receipt["delivery_id"], "to": intake["label"]}


#: Declared input names that mean "the one-line summary".
_TITLE_NAMES = frozenset({"title", "summary", "subject", "reporttitle", "requesttitle"})
#: Keep the shipped aliases separate: recognizing a new optional input can
#: change an existing sender's output keys even when its contract already worked.
_LEGACY_DETAIL_NAMES = frozenset({
    "details", "description", "body", "reportdetails", "requestdetails",
})
#: Declared input names that mean "the body of the report". ``tried``/``missing``
#: and ``broken`` are here because the intake this platform actually offers asks
#: ``what_they_tried`` / ``what_was_missing_or_broken`` (docs/host-actions.md):
#: both are asking for the body, in the owner's own wording.
_DETAIL_NAMES = _LEGACY_DETAIL_NAMES | frozenset({
    "whattheytried", "whattried", "tried",
    "whatwasmissingorbroken", "whatwasmissing", "missing", "broken",
})


def _report_outputs(contract: list[dict], title: str, details: str) -> dict[str, str]:
    """Map the report onto the receiver's declared inputs.

    The mapping that shipped is used UNCHANGED wherever it worked, because the
    sender branch is created once per (command center, principal) and a later
    send refuses outright if the output KEY SET no longer matches the stored
    one (``set(owned.node_defs[0].output_keys) != set(outputs)`` below). So
    anyone whose channel already worked must keep getting the same keys; only a
    contract the old mapping REFUSED may be mapped differently.

    What it refused: it placed the whole report in ONE field when no name
    matched, then failed the contract because the other required inputs were
    empty. The intake this platform offers declares three required inputs
    (``what_they_tried``/``what_was_missing_or_broken``/``request_type``), so
    every patch request came back ``invalid_patch_request`` -- the agent's one
    channel for telling us something is broken was itself broken (live
    2026-10-03).

    For those, and only those: every required text input is filled, a named one
    with its own part and an unnamed one with the full report, which is accurate
    rather than invented. Both halves of the report always reach the receiver --
    an input matched to the title alone never leaves the details unsent. A
    required input that is NOT text cannot be filled from a text report, and
    that refusal names it so the owner can see which input to relax.
    """
    text_fields = [field for field in contract if field["type"] in {"str", "string"}]
    whole = title + "\n\n" + details
    if len(contract) == len(text_fields) == 1:
        return {text_fields[0]["name"]: whole}

    def _named(detail_names: frozenset[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for field in text_fields:
            name = re.sub(r"[^a-z0-9]", "", field["name"].lower())
            if name in _TITLE_NAMES:
                found[field["name"]] = title
            elif name in detail_names:
                found[field["name"]] = details
        return found

    # --- exactly what shipped, for every contract it could satisfy ---
    outputs = _named(_LEGACY_DETAIL_NAMES)
    if title not in outputs.values() or details not in outputs.values():
        target = next((f for f in text_fields if f["required"]),
                      text_fields[0] if text_fields else None)
        outputs = {target["name"]: whole} if target else {}
    if outputs and not any(field["required"] and field["name"] not in outputs
                           for field in contract):
        return outputs

    # --- only now: a contract the mapping above refuses ---
    unfillable = sorted(field["name"] for field in contract
                        if field["required"] and field["type"] not in {"str", "string"})
    if unfillable:
        raise ValueError(
            "patch intake declares required non-text input(s) a text report cannot "
            f"fill: {', '.join(unfillable)}. Make them optional or text."
        )
    if not text_fields:
        raise ValueError("patch intake contract declares no text input to report into")
    outputs = _named(_DETAIL_NAMES)
    for field in text_fields:
        if field["required"] and field["name"] not in outputs:
            outputs[field["name"]] = whole
    if not outputs:
        outputs = {text_fields[0]["name"]: whole}
    # No half of the report may be dropped: if one of them is nowhere, the field
    # that would carry it least surprisingly carries the whole thing instead.
    if not any(title in value for value in outputs.values()) or not any(
            details in value for value in outputs.values()):
        carrier = next((f["name"] for f in text_fields if f["required"]),
                       text_fields[0]["name"])
        outputs[carrier] = whole
    return outputs


__all__ = [
    "ACTION_TYPE",
    "DEFAULT_LABEL",
    "LABEL_VAR",
    "PATCH_INTAKE_SINK",
    "PatchIntakeConsentMissing",
    "PatchIntakeMisconfigured",
    "RECEIVER_ID_VAR",
    "REQUEST_KIND",
    "configured_intake",
    "consent_is_active",
    "grant_send_consent",
    "rail_entry",
    "request_payload",
    "require_send_consent",
    "send_patch_request",
]
