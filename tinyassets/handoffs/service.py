"""Handoff behavior — declare / dry-run / prepare / execute / attest — plus the
``extensions`` dispatch table the canonical router half consumes.

Tasks 5.2 and 5.4 of
``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets`` (capability
``real-world-handoffs-and-outcomes``).

Public behavior composes under the canonical handle surface (design.md): these
are ``extensions`` *actions*, never new advertised MCP handles, and the
``extensions`` tool signature is NOT widened — every parameter reuses an existing
kwarg, the same technique the effector-consent and authoring actions use.

Router field reuse (also documented at the call site in
``tinyassets/api/workflow_actions.py``):

=========================  ====================================================
``extensions`` kwarg       handoff meaning
=========================  ====================================================
``key``                    handoff_id
``run_id``                 source run that produced the declared output
``branch_version_id``      immutable source version carrying the declaration
``field_name``             declared output field
``project_id``             destination (checked against the declaration only)
``request_id``             confirmation token
``event_type``             outcome_kind
``status``                 target lifecycle state / evidence-level filter
``evidence_url``           evidence reference for an attestation
``outcome_id``             outcome claim id
``notes``                  attestation narrative
``payload_json``           action body (external_id, provider evidence)
``universe_id``            universe whose consent grants and receipts apply
``limit``                  list bound
=========================  ====================================================

Every handler returns a JSON string and never raises through the router: a
handoff error becomes ``{"error": ..., "code": ...}`` so a chatbot sees a
machine-readable rejection instead of a stack trace.

What this module deliberately does NOT do
-----------------------------------------

- **It does not transport anything.** The adapter registry
  (``tinyassets/handoffs/adapters.py``) is the only way out, and the exactly-once
  journal is the landed
  :func:`tinyassets.effectors.outbound_boundary.execute_replay_safe_effect`.
  There is no second receipt store, no second dedup identity, and no second
  credential path.
- **It does not inflate a claim.** :func:`record_evidence` accepts only
  non-inflating lifecycle transitions (``cancelled`` / ``rejected`` /
  ``orphaned``). Advancing a handoff *up* to ``accepted``/``verified`` requires
  provider-authenticated evidence, which is task 5.3's verification adapters —
  deferred in this lane, so the upgrade path is absent rather than faked.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from tinyassets.handoffs import authority as handoff_authority
from tinyassets.handoffs.adapters import HandoffRequest, resolve_adapter
from tinyassets.handoffs.models import (
    HandoffConfirmationRequired,
    HandoffConflictError,
    HandoffError,
    HandoffRecord,
    HandoffValidationError,
    confirmation_fingerprint,
    derive_handoff_effect_key,
    parse_declarations,
)
from tinyassets.handoffs.store import HandoffStore
from tinyassets.ids import new_ulid

#: A confirmation is fresh for this long. Short enough that "I reviewed this"
#: still means the current state of the world; long enough for a chatbot turn.
CONFIRMATION_TTL_SECONDS = 15 * 60

#: Owner-driven lifecycle transitions. Every one of these either cancels or
#: downgrades — none of them can make a handoff look more proven than it is, so
#: none of them need provider authentication.
_OWNER_TRANSITIONS: frozenset[str] = frozenset({"cancelled", "rejected", "orphaned"})


def _store(base_path: str | Path | None = None) -> HandoffStore:
    store = HandoffStore(base_path)
    store.initialize()
    return store


def _iso(now: float | None = None) -> str:
    from datetime import datetime, timezone

    moment = (
        datetime.now(timezone.utc)
        if now is None
        else datetime.fromtimestamp(now, tz=timezone.utc)
    )
    return moment.isoformat()


# ── Behavior ──────────────────────────────────────────────────────────────────

def list_declarations(
    *,
    actor_id: str,
    base_path: str | Path,
    branch_version_id: str,
) -> dict[str, Any]:
    """Show every external effect an immutable version declares."""
    from tinyassets.branch_versions import branch_version_def_id, get_branch_version
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.principals import named_principal

    bid = branch_version_def_id(base_path, (branch_version_id or "").strip())
    try:
        branch = get_branch_definition(base_path, branch_def_id=bid) if bid else {}
    except KeyError:
        branch = {}
    actor = named_principal(actor_id)
    is_author = bool(actor) and actor == branch.get("author")
    version = get_branch_version(base_path, branch_version_id) if branch else None
    if version is None or not (is_author or (
        branch.get("visibility") == "public" and version.public
    )):
        raise HandoffValidationError(
            f"branch version {branch_version_id!r} not found"
        )
    declarations = parse_declarations(version.snapshot)
    return {
        "branch_version_id": version.branch_version_id,
        "branch_def_id": version.branch_def_id,
        "content_hash": version.content_hash,
        "declarations": [item.to_dict() for item in declarations],
        "count": len(declarations),
    }


def dry_run(
    *,
    actor_id: str,
    base_path: str | Path,
    run_id: str,
    branch_version_id: str,
    output_field: str,
    destination: str = "",
) -> dict[str, Any]:
    """Return a redacted ``would_handoff`` record and touch nothing durable.

    This path deliberately never reaches the receipt store, the handoff table,
    or the outcome registry: an authoring test or preview that reserved a
    production receipt would make "try it safely" the thing that consumes the
    one-shot identity.
    """
    source = handoff_authority.resolve_source(
        subject=actor_id,
        base_path=base_path,
        run_id=run_id,
        branch_version_id=branch_version_id,
        output_field=output_field,
        destination=destination,
    )
    declaration = source.declaration
    effect_key = derive_handoff_effect_key(
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        run_id=source.run_id,
        output_field=source.output_field,
        output_sha256=source.output_sha256,
        adapter_action=declaration.adapter_action,
        destination=declaration.destination,
    )
    request = HandoffRequest(
        effect_key=effect_key,
        adapter=declaration.adapter,
        adapter_action=declaration.adapter_action,
        destination=declaration.destination,
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        run_id=source.run_id,
        output_field=source.output_field,
        output_sha256=source.output_sha256,
        payload=source.output_value,
        evidence_contract=declaration.evidence_contract,
    )
    return {
        "would_handoff": request.redacted(),
        "effect_class": declaration.effect_class,
        "outcome_kind": declaration.outcome_kind,
        "evidence_level": "simulated",
        "evidence_expectation": dict(declaration.evidence_contract),
        "authority_still_required": _outstanding_authority(declaration),
        "executed": False,
        "receipt_reserved": False,
        "handoff_created": False,
        "outcome_created": False,
    }


def _outstanding_authority(declaration: Any) -> list[str]:
    needed = [f"destination consent for {declaration.adapter}:{declaration.destination}"]
    if declaration.irreversible:
        needed.append("fresh per-invocation confirmation (irreversible effect)")
    if declaration.credential_class:
        needed.append(
            f"adapter capability of class {declaration.credential_class!r} "
            "(resolved by the adapter, never by the handoff)"
        )
    return needed


def prepare(
    *,
    actor_id: str,
    base_path: str | Path,
    universe_dir: str | Path,
    run_id: str,
    branch_version_id: str,
    output_field: str,
    destination: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Check authority and, for an irreversible effect, mint the confirmation.

    Returns ``ready`` for a reversible effect with consent, or
    ``confirmation_required`` carrying the token plus exactly what the user is
    confirming. Standing connector consent alone never satisfies the second
    case — that is the whole point of the separate token.
    """
    source = handoff_authority.resolve_source(
        subject=actor_id,
        base_path=base_path,
        run_id=run_id,
        branch_version_id=branch_version_id,
        output_field=output_field,
        destination=destination,
    )
    declaration = source.declaration
    handoff_authority.require_destination_consent(
        universe_dir,
        sink=declaration.adapter,
        destination=declaration.destination,
    )
    effect_key = derive_handoff_effect_key(
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        run_id=source.run_id,
        output_field=source.output_field,
        output_sha256=source.output_sha256,
        adapter_action=declaration.adapter_action,
        destination=declaration.destination,
    )
    base = {
        "effect_key": effect_key,
        "adapter": declaration.adapter,
        "adapter_action": declaration.adapter_action,
        "destination": declaration.destination,
        "effect_class": declaration.effect_class,
        "outcome_kind": declaration.outcome_kind,
        "effect_summary": source.effect_summary(),
        "branch_version_id": source.branch_version_id,
        "content_hash": source.content_hash,
        "run_id": source.run_id,
        "output_field": source.output_field,
        "output_sha256": source.output_sha256,
    }
    if not declaration.irreversible:
        return {**base, "status": "ready", "confirmation_required": False}

    moment = time.time() if now is None else now
    fingerprint = confirmation_fingerprint(
        effect_key=effect_key,
        effect_summary=source.effect_summary(),
        destination=declaration.destination,
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        adapter_action=declaration.adapter_action,
    )
    confirmation = _store(base_path).create_confirmation(
        owner_id=actor_id,
        effect_key=effect_key,
        sink=declaration.adapter,
        fingerprint=fingerprint,
        ttl_seconds=CONFIRMATION_TTL_SECONDS,
        now=moment,
    )
    return {
        **base,
        "status": "confirmation_required",
        "confirmation_required": True,
        "confirmation_token": confirmation["token"],
        "confirmation_expires_at": confirmation["expires_at"],
        "confirming": {
            "effect_summary": source.effect_summary(),
            "destination": declaration.destination,
            "source_version": source.branch_version_id,
            "source_content_hash": source.content_hash,
        },
    }


def execute(
    *,
    actor_id: str,
    base_path: str | Path,
    universe_dir: str | Path,
    run_id: str,
    branch_version_id: str,
    output_field: str,
    destination: str = "",
    confirmation: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Perform one real handoff, exactly once, through the canonical boundary.

    Order is load-bearing:

    1. resolve and authorize the source (persisted run + immutable version);
    2. require the canonical destination consent grant;
    3. resolve the adapter — *before* spending the confirmation, so an
       unregistered adapter cannot burn a single-use token;
    4. for an irreversible effect, atomically consume a fresh confirmation whose
       fingerprint covers the effect summary, destination, and source
       version/hash — a token minted against an earlier version does not match;
    5. create (or re-read) the lifecycle row in ``reserved``;
    6. hand the invocation to
       :func:`~tinyassets.effectors.outbound_boundary.execute_replay_safe_effect`,
       which journals before firing and reconciles every ambiguous reply.

    Nothing external happens before step 6, and step 6 fires the adapter only for
    the request that atomically won the receipt reservation.
    """
    from tinyassets.effectors.outbound_boundary import execute_replay_safe_effect

    source = handoff_authority.resolve_source(
        subject=actor_id,
        base_path=base_path,
        run_id=run_id,
        branch_version_id=branch_version_id,
        output_field=output_field,
        destination=destination,
    )
    declaration = source.declaration
    handoff_authority.require_destination_consent(
        universe_dir,
        sink=declaration.adapter,
        destination=declaration.destination,
    )
    adapter = resolve_adapter(declaration.adapter)

    effect_key = derive_handoff_effect_key(
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        run_id=source.run_id,
        output_field=source.output_field,
        output_sha256=source.output_sha256,
        adapter_action=declaration.adapter_action,
        destination=declaration.destination,
    )
    sink = declaration.adapter
    store = _store(base_path)
    moment = time.time() if now is None else now

    consumed: dict[str, Any] | None = None
    if declaration.irreversible:
        fingerprint = confirmation_fingerprint(
            effect_key=effect_key,
            effect_summary=source.effect_summary(),
            destination=declaration.destination,
            branch_version_id=source.branch_version_id,
            content_hash=source.content_hash,
            adapter_action=declaration.adapter_action,
        )
        consumed = store.consume_confirmation(
            confirmation,
            owner_id=actor_id,
            effect_key=effect_key,
            sink=sink,
            fingerprint=fingerprint,
            now=moment,
        )
        if consumed is None:
            raise HandoffConfirmationRequired(
                "this irreversible handoff needs a fresh confirmation bound to "
                "the exact effect, destination, and source version",
                requirement={
                    "effect_key": effect_key,
                    "effect_summary": source.effect_summary(),
                    "destination": declaration.destination,
                    "source_version": source.branch_version_id,
                    "source_content_hash": source.content_hash,
                    "action": "handoff_prepare",
                },
            )

    record = store.find_by_effect(effect_key=effect_key, sink=sink, actor_id=actor_id)
    if record is None:
        stamp = _iso(moment)
        candidate = HandoffRecord(
            handoff_id=new_ulid(),
            owner_id=actor_id,
            effect_key=effect_key,
            sink=sink,
            adapter_action=declaration.adapter_action,
            destination=declaration.destination,
            branch_def_id=source.branch_def_id,
            branch_version_id=source.branch_version_id,
            content_hash=source.content_hash,
            run_id=source.run_id,
            output_field=source.output_field,
            output_sha256=source.output_sha256,
            effect_class=declaration.effect_class,
            outcome_kind=declaration.outcome_kind,
            credential_class=declaration.credential_class,
            state="reserved",
            created_at=stamp,
            updated_at=stamp,
            declaration=declaration.to_dict(),
        )
        try:
            record = store.create_handoff(
                candidate,
                evidence_source="initiation",
                evidence={"confirmation_consumed": bool(consumed)},
            )
        except HandoffConflictError:
            # A concurrent request for the same identity created it first. The
            # row is the shared one by construction (UNIQUE effect_key/sink), so
            # re-read rather than treating this as a failure.
            record = store.find_by_effect(
                effect_key=effect_key, sink=sink, actor_id=actor_id
            )
            if record is None:  # pragma: no cover - foreign owner on same key
                raise

    request = HandoffRequest(
        effect_key=effect_key,
        adapter=declaration.adapter,
        adapter_action=declaration.adapter_action,
        destination=declaration.destination,
        branch_version_id=source.branch_version_id,
        content_hash=source.content_hash,
        run_id=source.run_id,
        output_field=source.output_field,
        output_sha256=source.output_sha256,
        payload=source.output_value,
        evidence_contract=declaration.evidence_contract,
    )

    def _invoke() -> dict[str, Any]:
        result = adapter(request)
        return {
            "adapter_state": result.state,
            "external_id": result.external_id,
            "provider_evidence": dict(result.evidence),
            "request": request.redacted(),
        }

    try:
        effect = execute_replay_safe_effect(
            universe_dir=universe_dir,
            effect_key=effect_key,
            sink=sink,
            run_id=source.run_id,
            invoke=_invoke,
        )
    except RuntimeError:
        shared = _shared_pending(universe_dir, effect_key=effect_key, sink=sink)
        if shared is None:
            raise
        return {
            "status": "in_flight",
            "handoff": store.get_handoff(record.handoff_id, actor_id=actor_id).to_dict(),
            "effect_key": effect_key,
            "receipt_status": shared["status"],
            "executed": False,
            "note": (
                "another authorized request owns this effect identity; its "
                "evidence is shared rather than pushed a second time"
            ),
        }

    return _settle(
        store,
        actor_id=actor_id,
        record=record,
        effect=effect,
        source=source,
        effect_key=effect_key,
        sink=sink,
        now=moment,
    )


def _shared_pending(
    universe_dir: str | Path,
    *,
    effect_key: str,
    sink: str,
) -> dict[str, Any] | None:
    """Return the concurrent in-flight receipt, or ``None`` if it is not that.

    Re-reads the receipt rather than trusting the exception text: the landed
    boundary raises ``RuntimeError`` for any reservation status it will not act
    on, and only a genuinely ``pending`` row means "someone else is mid-effect".
    """
    from tinyassets.storage.external_write_receipts import (
        STATUS_PENDING,
        lookup_receipt,
    )

    receipt = lookup_receipt(universe_dir, idempotency_hint=effect_key, sink=sink)
    if receipt is None or receipt["status"] != STATUS_PENDING:
        return None
    return receipt


def _settle(
    store: HandoffStore,
    *,
    actor_id: str,
    record: HandoffRecord,
    effect: dict[str, Any],
    source: Any,
    effect_key: str,
    sink: str,
    now: float,
) -> dict[str, Any]:
    """Map one receipt outcome onto a lifecycle transition and, maybe, an outcome.

    The mapping is deliberately conservative:

    - a ``succeeded`` receipt whose adapter claimed ``submitted`` proves
      transport only, so the handoff becomes ``submitted`` and **no** outcome
      claim is created;
    - only an adapter-proven ``accepted`` (which the result type already forces
      to carry a stable external id) creates an outcome, at
      ``externally_verified`` — and that is acceptance evidence, never a claim of
      peer review, publication, citation, sales, or approval;
    - a ``held`` receipt (ambiguous or unreconcilable reply) becomes
      ``uncertain`` and stays retry-blocked under the same identity;
    - a ``failed`` receipt where the provider proved no mutation becomes
      ``rejected`` with no outcome.
    """
    from tinyassets.storage.external_write_receipts import (
        STATUS_FAILED,
        STATUS_HELD,
        STATUS_SUCCEEDED,
    )

    fresh = store.get_handoff(record.handoff_id, actor_id=actor_id)
    replay = bool(effect.get("replay"))
    status = effect.get("status")
    # The boundary nests whatever ``invoke`` returned under ``result`` — both on
    # a fresh success and on a dedup-hit replay, since the replay is the stored
    # evidence row. Reading the top level instead would silently see no adapter
    # state and downgrade every accepted handoff to ``submitted``.
    adapter_reply = effect.get("result")
    if not isinstance(adapter_reply, dict):
        adapter_reply = {}
    adapter_state = str(adapter_reply.get("adapter_state") or "")
    external_id = str(adapter_reply.get("external_id") or "")
    provider_evidence = adapter_reply.get("provider_evidence") or {}

    target: str
    if status == STATUS_SUCCEEDED:
        target = "accepted" if adapter_state == "accepted" else "submitted"
    elif status == STATUS_HELD:
        target = "uncertain"
    elif status == STATUS_FAILED:
        target = "rejected"
    else:  # pragma: no cover - the boundary returns one of the three
        target = "uncertain"

    outcome: dict[str, Any] | None = None
    if fresh.state != target:
        try:
            fresh = store.advance_handoff(
                fresh.handoff_id,
                actor_id=actor_id,
                expected_state=fresh.state,
                to_state=target,
                evidence_source="provider",
                evidence={
                    "receipt_status": status,
                    "adapter_state": adapter_state,
                    "provider_evidence": provider_evidence,
                    "reason": effect.get("reason", ""),
                },
                external_id=external_id,
                now=now,
            )
        except HandoffConflictError:
            # A concurrent settle already recorded this transition; the shared
            # row is authoritative.
            fresh = store.get_handoff(fresh.handoff_id, actor_id=actor_id)

    if target == "accepted":
        outcome = store.record_outcome_evidence(
            account_id=actor_id,
            outcome_kind=fresh.outcome_kind,
            evidence_source="provider",
            evidence_level="externally_verified",
            run_id=fresh.run_id,
            branch_def_id=fresh.branch_def_id,
            branch_version_id=fresh.branch_version_id,
            content_hash=fresh.content_hash,
            output_field=fresh.output_field,
            output_sha256=fresh.output_sha256,
            handoff_id=fresh.handoff_id,
            effect_key=effect_key,
            sink=sink,
            external_id=external_id,
            payload={"provider_evidence": provider_evidence},
            now=now,
        )

    return {
        "status": fresh.state,
        "handoff": fresh.to_dict(),
        "effect_key": effect_key,
        "receipt_status": status,
        "replay": replay,
        "executed": not replay and status == STATUS_SUCCEEDED,
        "provider_evidence": provider_evidence,
        "outcome": outcome,
        "remediation": effect.get("remediation"),
    }


def get(
    *,
    actor_id: str,
    base_path: str | Path,
    handoff_id: str,
) -> dict[str, Any]:
    store = _store(base_path)
    record = store.get_handoff(handoff_id, actor_id=actor_id)
    return {
        "handoff": record.to_dict(),
        "transitions": [
            item.to_dict() for item in store.list_transitions(handoff_id, actor_id=actor_id)
        ],
        "outcomes": store.list_outcome_evidence(
            account_id=actor_id, handoff_id=record.handoff_id, limit=50
        ),
    }


def listing(
    *,
    actor_id: str,
    base_path: str | Path,
    run_id: str = "",
    state: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    records = _store(base_path).list_handoffs(
        actor_id=actor_id, run_id=run_id, state=state, limit=limit
    )
    return {
        "handoffs": [item.to_dict() for item in records],
        "count": len(records),
    }


def record_evidence(
    *,
    actor_id: str,
    base_path: str | Path,
    handoff_id: str,
    to_state: str,
    evidence: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply an owner-driven, non-inflating lifecycle transition.

    Only ``cancelled`` / ``rejected`` / ``orphaned`` are reachable here. An owner
    cannot declare their own handoff ``accepted`` or ``verified``: that requires
    authenticated provider evidence, owned by task 5.3's verification adapters,
    which this lane deferred rather than approximated.
    """
    target = (to_state or "").strip()
    if target not in _OWNER_TRANSITIONS:
        raise HandoffValidationError(
            f"{target!r} is not an owner-settable state; only "
            f"{sorted(_OWNER_TRANSITIONS)} are. Advancing to accepted/verified "
            "requires authenticated provider evidence."
        )
    store = _store(base_path)
    current = store.get_handoff(handoff_id, actor_id=actor_id)
    record = store.advance_handoff(
        current.handoff_id,
        actor_id=actor_id,
        expected_state=current.state,
        to_state=target,
        evidence_source="owner",
        evidence=dict(evidence or {}),
        now=now,
    )
    return {"handoff": record.to_dict(), "status": record.state}


def attest_outcome(
    *,
    actor_id: str,
    base_path: str | Path,
    run_id: str,
    outcome_kind: str,
    external_id: str = "",
    evidence_url: str = "",
    note: str = "",
    handoff_id: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Record an authenticated user's real-world attestation.

    The claim enters the registry at ``user_attested`` and stays there. A
    syntactically valid — or even reachable — evidence URL never promotes it;
    only an explicit authorized verification transition does, and that transition
    preserves who made the original attestation.
    """
    store = _store(base_path)
    run_id = (run_id or "").strip()
    branch_def_id = ""
    if run_id:
        from tinyassets.runs import get_run

        run = get_run(base_path, run_id)
        if run is None:
            raise handoff_authority.HandoffAuthorityError(
                f"run {run_id!r} is not available to this account"
            )
        from tinyassets.principals import named_principal

        owner = named_principal(run.get("owner_user_id")) or named_principal(run.get("actor"))
        if not owner or owner != actor_id:
            raise handoff_authority.HandoffAuthorityError(
                f"run {run_id!r} is not available to this account"
            )
        branch_def_id = str(run.get("branch_def_id") or "")

    linked = ""
    if (handoff_id or "").strip():
        # Owner-scoped read: attaching an attestation to someone else's handoff
        # would forge provenance, so the lookup raises rather than silently
        # dropping the link.
        record = store.get_handoff(handoff_id, actor_id=actor_id)
        linked = record.handoff_id

    return store.record_outcome_evidence(
        account_id=actor_id,
        outcome_kind=outcome_kind,
        evidence_source="user_attestation",
        evidence_level="user_attested",
        run_id=run_id,
        branch_def_id=branch_def_id,
        handoff_id=linked,
        external_id=external_id,
        evidence_url=evidence_url,
        note=note,
        now=now,
    )


def outcome_evidence(
    *,
    actor_id: str,
    base_path: str | Path,
    outcome_id: str = "",
    handoff_id: str = "",
    outcome_kind: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Read outcome claims with their evidence level intact.

    Consumers receive structured counts per evidence level and outcome kind plus
    a distinct-artifact count — never one flattened success number.
    """
    store = _store(base_path)
    if (outcome_id or "").strip():
        return {"outcome": store.get_outcome_evidence(outcome_id, actor_id=actor_id)}
    return {
        "outcomes": store.list_outcome_evidence(
            account_id=actor_id, handoff_id=handoff_id, limit=limit
        ),
        "summary": store.outcome_evidence_summary(
            account_id=actor_id, outcome_kind=outcome_kind
        ),
    }


# ── Router half ───────────────────────────────────────────────────────────────

def _guard(fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], str]:
    """Turn a handler into the router's JSON-string contract.

    A :class:`HandoffError` becomes a machine-readable rejection carrying its
    ``code``; anything else propagates, because an unexpected exception is a bug
    and this project fails loudly rather than returning a plausible-looking
    success.
    """

    def wrapper(kwargs: dict[str, Any]) -> str:
        try:
            return json.dumps(fn(kwargs), default=str)
        except HandoffConfirmationRequired as exc:
            return json.dumps({
                "error": str(exc),
                "code": exc.code,
                "requirement": exc.requirement,
            }, default=str)
        except HandoffError as exc:
            return json.dumps({"error": str(exc), "code": exc.code}, default=str)

    return wrapper


def _payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    raw = kwargs.get("payload_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise HandoffValidationError("payload_json must be valid JSON") from exc
    if not isinstance(value, dict):
        raise HandoffValidationError("payload_json must be a JSON object")
    return value


def _common(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "actor_id": kwargs["actor_id"],
        "base_path": kwargs["base_path"],
    }


def _action_declarations(kwargs: dict[str, Any]) -> dict[str, Any]:
    return list_declarations(
        **_common(kwargs),
        branch_version_id=kwargs.get("branch_version_id") or "",
    )


def _action_dry_run(kwargs: dict[str, Any]) -> dict[str, Any]:
    return dry_run(
        **_common(kwargs),
        run_id=kwargs.get("run_id") or "",
        branch_version_id=kwargs.get("branch_version_id") or "",
        output_field=kwargs.get("output_field") or "",
        destination=kwargs.get("destination") or "",
    )


def _action_prepare(kwargs: dict[str, Any]) -> dict[str, Any]:
    return prepare(
        **_common(kwargs),
        universe_dir=kwargs["universe_dir"],
        run_id=kwargs.get("run_id") or "",
        branch_version_id=kwargs.get("branch_version_id") or "",
        output_field=kwargs.get("output_field") or "",
        destination=kwargs.get("destination") or "",
    )


def _action_execute(kwargs: dict[str, Any]) -> dict[str, Any]:
    return execute(
        **_common(kwargs),
        universe_dir=kwargs["universe_dir"],
        run_id=kwargs.get("run_id") or "",
        branch_version_id=kwargs.get("branch_version_id") or "",
        output_field=kwargs.get("output_field") or "",
        destination=kwargs.get("destination") or "",
        confirmation=kwargs.get("confirmation") or "",
    )


def _action_get(kwargs: dict[str, Any]) -> dict[str, Any]:
    return get(**_common(kwargs), handoff_id=kwargs.get("handoff_id") or "")


def _action_list(kwargs: dict[str, Any]) -> dict[str, Any]:
    return listing(
        **_common(kwargs),
        run_id=kwargs.get("run_id") or "",
        state=kwargs.get("state") or "",
        limit=kwargs.get("limit") or 50,
    )


def _action_record_evidence(kwargs: dict[str, Any]) -> dict[str, Any]:
    return record_evidence(
        **_common(kwargs),
        handoff_id=kwargs.get("handoff_id") or "",
        to_state=kwargs.get("state") or "",
        evidence=_payload(kwargs),
    )


def _action_attest_outcome(kwargs: dict[str, Any]) -> dict[str, Any]:
    body = _payload(kwargs)
    return attest_outcome(
        **_common(kwargs),
        run_id=kwargs.get("run_id") or "",
        outcome_kind=kwargs.get("outcome_kind") or "",
        external_id=str(body.get("external_id") or ""),
        evidence_url=kwargs.get("evidence_url") or "",
        note=kwargs.get("note") or "",
        handoff_id=kwargs.get("handoff_id") or "",
    )


def _action_outcome_evidence(kwargs: dict[str, Any]) -> dict[str, Any]:
    return outcome_evidence(
        **_common(kwargs),
        outcome_id=kwargs.get("outcome_id") or "",
        handoff_id=kwargs.get("handoff_id") or "",
        outcome_kind=kwargs.get("outcome_kind") or "",
        limit=kwargs.get("limit") or 50,
    )


#: ``extensions`` actions. No new advertised MCP handle; the canonical seven
#: stay exactly as they are.
_HANDOFF_ACTIONS: dict[str, Callable[[dict[str, Any]], str]] = {
    "handoff_declarations": _guard(_action_declarations),
    "handoff_dry_run": _guard(_action_dry_run),
    "handoff_prepare": _guard(_action_prepare),
    "handoff_execute": _guard(_action_execute),
    "handoff_get": _guard(_action_get),
    "handoff_list": _guard(_action_list),
    "handoff_record_evidence": _guard(_action_record_evidence),
    "handoff_attest_outcome": _guard(_action_attest_outcome),
    "handoff_outcome_evidence": _guard(_action_outcome_evidence),
}

#: Actions that mutate durable state (OAuth scope derivation, auth/provider.py).
#: Without these rows ``require_action_scope`` derives read scope for a mutating
#: action, which is the fail-OPEN direction — every writer here is listed.
_HANDOFF_WRITE_ACTIONS: frozenset[str] = frozenset({
    "handoff_prepare",
    "handoff_execute",
    "handoff_record_evidence",
    "handoff_attest_outcome",
})

#: ``handoff_execute`` performs a real external effect and spends provider
#: budget, so it derives costly like ``run_branch``.
_HANDOFF_COSTLY_ACTIONS: frozenset[str] = frozenset({"handoff_execute"})


__all__ = [
    "CONFIRMATION_TTL_SECONDS",
    "attest_outcome",
    "dry_run",
    "execute",
    "get",
    "list_declarations",
    "listing",
    "outcome_evidence",
    "prepare",
    "record_evidence",
]
