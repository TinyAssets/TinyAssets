"""Authoring session behavior — inspect / edit / test / publish — plus the
``extensions`` dispatch table that the canonical router half consumes.

Tasks 4.2 and 4.3 of
``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets``.

Public behavior composes under the canonical handle surface (design.md): these
are ``extensions`` *actions*, never new advertised MCP handles, and the
``extensions`` tool signature is not widened — each action reuses existing
kwargs, the same technique the effector-consent actions use.

Router field reuse (also documented at the call site in
``tinyassets/api/extensions.py``):

===========================  ==================================================
``extensions`` kwarg         authoring meaning
===========================  ==================================================
``key``                      session_id
``field_type``               artifact_kind (``node`` | ``evaluator``)
``intent``                   sketch (start) / effect name (confirm_effect)
``branch_version_id``        base published version to resume from
``resume_from``              existing draft session to continue
``select``                   view: ``full`` | ``diff`` | ``summary`` | ``history``
``since``                    diff anchor event_id
``changes_json``             JSON array of edit operations
``expected_version``         draft version the caller reviewed (CAS)
``notes``                    publish change message
``value``                    test mode (``dry`` | ``real``)
``request_id``               per-run effect confirmation token
``payload_json``             action body: test inputs, visibility, risk acks
``limit``                    list bound
===========================  ==================================================

Every handler returns a JSON string and never raises through the router: an
authoring error becomes ``{"error": ..., "code": ..., "issues": [...]}`` so a
chatbot sees a machine-readable rejection instead of a stack trace.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tinyassets.authoring import io as authoring_io
from tinyassets.authoring import sandbox as authoring_sandbox
from tinyassets.authoring.models import (
    ARTIFACT_KINDS,
    VISIBILITIES,
    AuthoringAccessError,
    AuthoringConflictError,
    AuthoringError,
    AuthoringSession,
    AuthoringValidationError,
    BudgetExceeded,
    ConfirmationRequired,
    SandboxDenied,
    ValidationIssue,
    access_denied,
    apply_operations,
    canonical_json,
    definition_hash,
    diff_definitions,
    skeleton_for,
    summarize_definition,
    validate_definition,
)
from tinyassets.authoring.store import AuthoringStore
from tinyassets.ids import new_ulid
from tinyassets.principals import named_principal

#: Drafts are retained for this long; a diff anchor outside retention fails
#: explicitly rather than being diffed against a substitute version.
DRAFT_RETENTION_DAYS = 30

VIEWS: frozenset[str] = frozenset({"full", "diff", "summary", "history"})


def _resolve_store(store: AuthoringStore | None) -> AuthoringStore:
    if store is not None:
        return store
    resolved = AuthoringStore()
    resolved.initialize()
    return resolved


def _require_actor(actor_id: str) -> str:
    actor = named_principal(actor_id)
    if not actor:
        raise AuthoringAccessError("authentication required to author artifacts")
    return actor


def _require_unexpired(session: AuthoringSession, *, now: datetime | None = None) -> None:
    """Refuse a write to a draft past its retention boundary.

    Reads stay allowed so the owner can still see what lapsed; resuming, editing,
    testing, and publishing do not, because the retention boundary is what makes
    "an unexpired prior draft" a real precondition rather than a stored string.
    """
    raw = (session.retention_until or "").strip()
    if not raw:
        return
    try:
        boundary = datetime.fromisoformat(raw)
    except ValueError:
        return
    if boundary.tzinfo is None:
        boundary = boundary.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    if moment >= boundary:
        raise AuthoringValidationError([
            ValidationIssue(
                "session.retention_expired",
                "retention_until",
                f"this draft's retention boundary passed at {raw}; start a new "
                "session from a published version or a fresh sketch",
            ),
        ])


def _version_resolver(store: AuthoringStore, actor_id: str) -> Callable[[str], dict | None]:
    """Resolve a version_id *or* artifact_id to its readable version record."""

    def resolve(reference: str) -> dict[str, Any] | None:
        try:
            return store.get_version(reference, actor_id=actor_id).to_dict()
        except AuthoringAccessError:
            pass
        latest = store.latest_version_for_artifact(reference, actor_id=actor_id)
        return latest.to_dict() if latest else None

    return resolve


def _validate(
    session: AuthoringSession, store: AuthoringStore, actor_id: str
) -> list[ValidationIssue]:
    return validate_definition(
        session.definition,
        artifact_kind=session.artifact_kind,
        self_artifact_id=session.artifact_id,
        resolve_version=_version_resolver(store, actor_id),
    )


def _validate_definition_for(
    definition: dict[str, Any],
    *,
    artifact_kind: str,
    artifact_id: str,
    store: AuthoringStore,
    actor_id: str,
) -> list[ValidationIssue]:
    return validate_definition(
        definition,
        artifact_kind=artifact_kind,
        self_artifact_id=artifact_id,
        resolve_version=_version_resolver(store, actor_id),
    )


# ── start ──────────────────────────────────────────────────────────────────


def start_session(
    *,
    actor_id: str,
    artifact_kind: str = "node",
    sketch: str = "",
    base_version_id: str = "",
    resume_session_id: str = "",
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Begin a session from exactly one of sketch / published version / draft."""
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    kind = (artifact_kind or "node").strip()
    if kind not in ARTIFACT_KINDS:
        raise AuthoringValidationError([
            ValidationIssue(
                "session.unknown_artifact_kind",
                "artifact_kind",
                f"artifact_kind must be one of {sorted(ARTIFACT_KINDS)}",
            ),
        ])

    seeds = {
        "sketch": bool((sketch or "").strip()),
        "artifact": bool((base_version_id or "").strip()),
        "session": bool((resume_session_id or "").strip()),
    }
    chosen = [mode for mode, present in seeds.items() if present]
    if len(chosen) != 1:
        raise AuthoringValidationError([
            ValidationIssue(
                "seed.exactly_one_required",
                "seed",
                "supply exactly one of sketch, base_version_id, resume_session_id "
                f"(got {len(chosen)})",
            ),
        ])
    seed_mode = chosen[0]

    if seed_mode == "session":
        # Resuming an unexpired draft is a read of the caller's own session.
        existing = store.get_session(resume_session_id.strip(), actor_id=actor)
        _require_unexpired(existing)
        if existing.status != "active":
            raise AuthoringValidationError([
                ValidationIssue(
                    "seed.session_not_active", "resume_session_id", existing.status,
                ),
            ])
        return existing.to_dict()

    parent_version_id = ""
    if seed_mode == "artifact":
        version = store.get_version(base_version_id.strip(), actor_id=actor)
        if version.artifact_kind != kind:
            raise AuthoringValidationError([
                ValidationIssue(
                    "seed.artifact_kind_mismatch",
                    "base_version_id",
                    f"base version is a {version.artifact_kind}, not a {kind}",
                ),
            ])
        # Copy the definition only. Execution data, credentials, instance state,
        # and another user's private provenance never ride along.
        definition = dict(version.definition)
        definition["kind"] = kind
        parent_version_id = version.version_id
        seed_ref = version.version_id
        artifact_id = f"art_{new_ulid()}"
    else:
        definition = skeleton_for(kind, sketch=sketch.strip())
        seed_ref = ""
        artifact_id = f"art_{new_ulid()}"

    now = datetime.now(timezone.utc)
    session = AuthoringSession(
        session_id=f"ses_{new_ulid()}",
        owner_id=actor,
        artifact_id=artifact_id,
        artifact_kind=kind,
        seed_mode=seed_mode,
        seed_ref=seed_ref,
        status="active",
        draft_version=1,
        definition=definition,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        retention_until=(now + timedelta(days=DRAFT_RETENTION_DAYS)).isoformat(),
        parent_version_id=parent_version_id,
    )
    store.create_session(session)
    return session.to_dict()


def get_session_record(
    *, actor_id: str, session_id: str, store: AuthoringStore | None = None
) -> AuthoringSession:
    """Owner-scoped session record (dataclass) for in-process callers."""
    store = _resolve_store(store)
    return store.get_session(session_id, actor_id=_require_actor(actor_id))


# ── inspect ────────────────────────────────────────────────────────────────


def inspect_session(
    *,
    actor_id: str,
    session_id: str,
    view: str = "full",
    anchor: str = "",
    limit: int = 200,
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Render the draft at full, diff, summary, or history fidelity."""
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    requested = (view or "full").strip() or "full"
    if requested not in VIEWS:
        raise AuthoringValidationError([
            ValidationIssue("view.unknown", "view", f"view must be one of {sorted(VIEWS)}"),
        ])

    session = store.get_session(session_id, actor_id=actor)
    issues = _validate(session, store, actor)
    envelope = {
        "session_id": session.session_id,
        "owner_id": session.owner_id,
        "artifact_id": session.artifact_id,
        "artifact_kind": session.artifact_kind,
        "status": session.status,
        "draft_version": session.draft_version,
        "definition_hash": session.definition_hash,
        "retention_until": session.retention_until,
        "lineage": {"parent_version_id": session.parent_version_id},
        "view": requested,
    }

    if requested == "full":
        return {
            **envelope,
            "definition": session.definition,
            "validation": {
                "valid": not issues,
                "issues": [issue.to_dict() for issue in issues],
            },
            "isolation": authoring_sandbox.isolation_report(),
        }

    if requested == "summary":
        return {
            **envelope,
            **summarize_definition(
                session.definition, artifact_kind=session.artifact_kind, issues=issues
            ),
        }

    if requested == "history":
        events = store.list_events(session.session_id, actor_id=actor, limit=limit)
        return {**envelope, "events": [event.to_dict() for event in events]}

    # diff — anchored to an immutable session event.
    anchor_id = (anchor or "").strip()
    if not anchor_id:
        raise AuthoringValidationError([
            ValidationIssue(
                "diff.anchor_required",
                "anchor",
                "a diff requires an anchor event_id from the session history",
            ),
        ])
    try:
        event = store.get_event(session.session_id, anchor_id, actor_id=actor)
    except AuthoringAccessError as exc:
        raise AuthoringValidationError(
            [
                ValidationIssue(
                    "diff.anchor_unavailable",
                    "anchor",
                    "the requested diff anchor is not in retained session history",
                ),
            ],
            message="diff anchor is unavailable; no diff was produced",
        ) from exc

    anchored_definition = _definition_at_event(store, session, event, actor)
    if anchored_definition is None:
        raise AuthoringValidationError(
            [
                ValidationIssue(
                    "diff.anchor_unavailable",
                    "anchor",
                    "the anchored definition is no longer retained",
                ),
            ],
            message="diff anchor is unavailable; no diff was produced",
        )
    return {
        **envelope,
        "anchor": {
            "event_id": event.event_id,
            "seq": event.seq,
            "event_type": event.event_type,
            "definition_hash": event.definition_hash,
            "created_at": event.created_at,
        },
        "changes": diff_definitions(anchored_definition, session.definition),
    }


def _definition_at_event(
    store: AuthoringStore,
    session: AuthoringSession,
    event: Any,
    actor_id: str,
) -> dict[str, Any] | None:
    """Reconstruct the definition an event anchored.

    Events carry the definition hash plus (for edits) the operations applied, so
    the anchored document is replayed from the session's first retained state.
    A hash mismatch means the anchor is not reconstructable — the caller is told,
    never handed a diff against a different version.
    """
    events = store.list_events(session.session_id, actor_id=actor_id, limit=1000)
    replayed = None
    for recorded in events:
        if recorded.event_type == "created":
            replayed = recorded.payload.get("definition")
        elif recorded.event_type == "edit" and replayed is not None:
            operations = recorded.payload.get("operations") or []
            try:
                replayed = apply_operations(
                    replayed, operations, artifact_kind=session.artifact_kind
                )
            except AuthoringValidationError:
                return None
        if recorded.event_id == event.event_id:
            break
    if replayed is None:
        return None
    if definition_hash(replayed) != event.definition_hash:
        return None
    return replayed


# ── edit ───────────────────────────────────────────────────────────────────


def apply_edit_batch(
    *,
    actor_id: str,
    session_id: str,
    operations: list[dict[str, Any]],
    expected_version: int | None = None,
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Apply one atomic batch: all operations commit as one event, or none do."""
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    session = store.get_session(session_id, actor_id=actor)
    _require_unexpired(session)
    if session.status != "active":
        raise AuthoringValidationError([
            ValidationIssue("session.not_active", "status", session.status),
        ])
    if expected_version is not None and int(expected_version) != session.draft_version:
        raise AuthoringConflictError(
            f"draft advanced: expected version {expected_version}, "
            f"stored {session.draft_version}"
        )

    candidate = apply_operations(
        session.definition, operations, artifact_kind=session.artifact_kind
    )
    issues = _validate_definition_for(
        candidate,
        artifact_kind=session.artifact_kind,
        artifact_id=session.artifact_id,
        store=store,
        actor_id=actor,
    )
    # Completeness blockers (an unfinished draft) must not block editing; a
    # *structural* defect introduced by this batch must.
    blocking = [issue for issue in issues if not _is_completeness_issue(issue)]
    if blocking:
        raise AuthoringValidationError(blocking)

    updated, event = store.commit_definition(
        session.session_id,
        actor_id=actor,
        expected_version=session.draft_version,
        definition=candidate,
        event_type="edit",
        payload={
            "operation_count": len(operations),
            "operations": operations,
            "paths": sorted({str(op.get("path", "")) for op in operations}),
        },
    )
    return {
        **updated.to_dict(),
        "event_id": event.event_id,
        "validation": {
            "valid": not issues,
            "issues": [issue.to_dict() for issue in issues],
        },
    }


_COMPLETENESS_CODES: frozenset[str] = frozenset({
    "definition.name_required",
    "definition.no_nodes",
    "definition.entry_point_required",
    "evaluator.inputs_undeclared",
    "evaluator.outputs_undeclared",
    "evaluator.determinism_undeclared",
    "evaluator.stages_undeclared",
})


def _is_completeness_issue(issue: ValidationIssue) -> bool:
    return issue.code in _COMPLETENESS_CODES


# ── test ───────────────────────────────────────────────────────────────────


def run_test(
    *,
    actor_id: str,
    session_id: str,
    inputs: dict[str, Any] | None = None,
    mode: str = "dry",
    confirmation: str = "",
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Execute the current immutable draft version under policy — never publish.

    **The draft actually runs.** Every code node (one declaring ``source_code``)
    is executed through the shipped subprocess sandbox
    (:class:`tinyassets.node_sandbox.NodeSandbox`: separate process, import
    allowlist, stripped environment, hard timeout, output cap), and the wall-time
    and output budgets are charged from the *measured* run — so a node that
    raises, hangs, or floods output fails the test instead of reporting a nominal
    pass. Prompt-template nodes are reported as ``not_executed`` with a reason
    rather than counted as passing: executing them means model spend, which the
    optimization/evaluator lane owns.

    **Network-capable draft source is refused, not "denied".** The host has no
    egress filter (see :func:`tinyassets.authoring.sandbox.isolation_report`), so
    a node importing ``requests``/``socket``/… cannot be held to
    "declared destinations only". Such a draft is refused before execution rather
    than run under a promise the platform cannot keep.

    Default mode replaces every declared effect with a redacted
    ``would_execute`` record. ``mode="real"`` authorizes reversible effects and
    requires fresh per-run confirmation for irreversible ones; an unconfirmed
    irreversible effect is blocked *before* any adapter call and no receipt
    exists. Real effects still execute only through the canonical effect
    authority/receipt owners, which this lane does not modify — so a real run
    reports authorization, not a fabricated receipt.
    """
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    session = store.get_session(session_id, actor_id=actor)
    _require_unexpired(session)
    selected_mode = (mode or "dry").strip() or "dry"
    if selected_mode not in ("dry", "real"):
        raise AuthoringValidationError([
            ValidationIssue("test.unknown_mode", "mode", "mode must be 'dry' or 'real'"),
        ])

    policy, policy_issues = authoring_sandbox.policy_from_declaration(
        session.definition.get("sandbox_policy")
    )
    isolation = authoring_sandbox.require_isolation(policy)
    ledger = authoring_sandbox.BudgetLedger(policy)
    issues = _validate(session, store, actor)

    manifest = authoring_io.parse_manifest(session.definition)
    bound = (
        authoring_io.bind_inputs(session, inputs, store=store, actor_id=actor)
        if (manifest.inputs or inputs)
        else None
    )

    effect_records: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    budget_error: dict[str, Any] | None = None

    for effect in session.definition.get("effects", []):
        if not isinstance(effect, dict):
            continue
        payload = effect.get("payload_example") or {}
        record = authoring_sandbox.simulate_effect(effect, payload=payload)
        network_sink = authoring_sandbox.is_network_sink(effect)
        if network_sink:
            # Egress is governed by the draft's declared destinations, deny-first.
            decision = authoring_sandbox.decide_network(
                policy, effect.get("destination", "")
            )
            if not decision.allowed:
                record["network_denied"] = True
                record["decision"] = decision.to_dict()
                effect_records.append(record)
                continue
        if selected_mode == "dry":
            effect_records.append(record)
            continue
        if network_sink:
            # A real egress call spends the external-call budget, which is 0
            # unless the draft declared one.
            try:
                ledger.charge("max_external_calls", 1)
            except BudgetExceeded as exc:
                budget_error = {
                    "budget": exc.budget,
                    "limit": exc.limit,
                    "attempted": exc.attempted,
                }
                record["blocked"] = True
                record["code"] = "sandbox.budget_exceeded"
                effect_records.append(record)
                continue
        try:
            authorization = authoring_sandbox.authorize_real_effect(
                store,
                session_id=session.session_id,
                draft_version=session.draft_version,
                effect=effect,
                payload=payload,
                token=confirmation,
            )
        except ConfirmationRequired as exc:
            record["blocked"] = True
            record["code"] = "effect.confirmation_required"
            record["reason"] = str(exc)
            effect_records.append(record)
            continue
        record["simulated"] = False
        record["authorized"] = True
        record["authorization"] = authorization
        record["note"] = (
            "authorized for the canonical effect boundary; this run records "
            "authorization only — the effector package owns the external write "
            "and its receipt"
        )
        effect_records.append(record)

    executions, execution_budget_error = _execute_draft_nodes(
        session, ledger=ledger, policy=policy, bound=bound
    )
    budget_error = budget_error or execution_budget_error

    effects_clean = not any(
        record.get("blocked") or record.get("network_denied")
        for record in effect_records
    )
    executions_clean = not any(
        execution["status"] in ("failed", "refused") for execution in executions
    )
    clean = bool(
        not issues and effects_clean and executions_clean and budget_error is None
    )

    result = {
        "session_id": session.session_id,
        "draft_version": session.draft_version,
        "definition_hash": session.definition_hash,
        "mode": selected_mode,
        "published": False,
        "clean": clean,
        "status": "passed" if clean else "failed",
        "validation": {
            "valid": not issues,
            "issues": [issue.to_dict() for issue in issues],
        },
        "policy": policy.to_dict(),
        "policy_issues": [issue.to_dict() for issue in policy_issues],
        "isolation": isolation,
        "budgets": ledger.to_dict(),
        "budget_error": budget_error,
        "effects": effect_records,
        "executions": executions,
        "receipts": receipts,
        "inputs": bound.to_dict() if bound else {"values": {}, "handle_count": 0},
    }
    store.append_event(
        session.session_id,
        actor_id=actor,
        event_type="test",
        definition_hash=session.definition_hash,
        payload={
            "mode": selected_mode,
            "definition_hash": session.definition_hash,
            "draft_version": session.draft_version,
            "isolation": isolation,
            "budgets": ledger.to_dict(),
            "budget_error": budget_error,
            "valid": not issues,
            # The publication gate reads this: only a clean test of this exact
            # definition *and* draft version can satisfy the required-test rule.
            "clean": clean,
            "status": "passed" if clean else "failed",
            "executions": [
                {
                    "node_id": execution["node_id"],
                    "status": execution["status"],
                    "reason": execution.get("reason", ""),
                }
                for execution in executions
            ],
            "effects": [
                {
                    "name": record["would_execute"]["name"],
                    "simulated": record.get("simulated", True),
                    "blocked": bool(record.get("blocked")),
                    "network_denied": bool(record.get("network_denied")),
                }
                for record in effect_records
            ],
        },
    )
    return result


def _execute_draft_nodes(
    session: AuthoringSession,
    *,
    ledger: authoring_sandbox.BudgetLedger,
    policy: authoring_sandbox.SandboxPolicy,
    bound: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run each code node of the draft in the shipped subprocess sandbox.

    Returns ``(execution records, budget error or None)``. Budgets are charged
    from the measured run, so "the budget fired" is an observation, not a
    declaration. Execution stops at the first budget breach.
    """
    from tinyassets.node_sandbox import NodeSandbox, SandboxUnavailableError

    if session.artifact_kind != "node":
        return [], None

    seed_state: dict[str, Any] = {}
    for field_decl in session.definition.get("state_schema", []):
        if isinstance(field_decl, dict) and field_decl.get("name"):
            seed_state[str(field_decl["name"])] = field_decl.get("default", "")
    if bound is not None:
        seed_state.update({
            name: value
            for name, value in bound.values.items()
            if not isinstance(value, (dict, list))
        })

    executions: list[dict[str, Any]] = []
    budget_error: dict[str, Any] | None = None

    for node in session.definition.get("node_defs", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id", ""))
        source = str(node.get("source_code", "") or "")
        if not source.strip():
            executions.append({
                "node_id": node_id,
                "status": "not_executed",
                "reason": "prompt-template node: executing it means model spend, "
                          "which the optimization/evaluator lane owns",
            })
            continue

        network_imports = authoring_sandbox.network_capable_imports(source)
        if network_imports:
            executions.append({
                "node_id": node_id,
                "status": "refused",
                "reason": "sandbox.network_capable_source_denied: this node imports "
                          f"{network_imports}, and the host reports no egress "
                          "boundary, so 'declared destinations only' cannot be "
                          "enforced for it",
            })
            continue

        remaining_wall = max(
            0.0, policy.wall_seconds - ledger.to_dict()["spent"]["wall_seconds"]
        )
        if remaining_wall <= 0:
            budget_error = {
                "budget": "wall_seconds",
                "limit": policy.wall_seconds,
                "attempted": policy.wall_seconds,
            }
            executions.append({
                "node_id": node_id,
                "status": "refused",
                "reason": "sandbox.budget_exceeded: no wall-time budget left",
            })
            break

        from tinyassets.broker.supervisor import broker_selected

        owner_scope = None
        if broker_selected():
            from tinyassets.daemon_server import get_founder_home
            from tinyassets.storage import data_dir

            root = data_dir().resolve()
            home = get_founder_home(root, session.owner_id)
            if home:
                owner_scope = root / home
        sandbox_runtime = NodeSandbox(timeout=remaining_wall,
            max_output_bytes=policy.max_output_bytes, universe_dir=owner_scope)
        started = time.monotonic()
        try:
            outcome = asyncio.run(
                sandbox_runtime.execute(
                    node_id=node_id or "draft_node",
                    source_code=source,
                    input_state=seed_state,
                    input_keys=[str(k) for k in node.get("input_keys", [])],
                    output_keys=[str(k) for k in node.get("output_keys", [])],
                    timeout=remaining_wall,
                    dependencies=[str(d) for d in node.get("dependencies", [])],
                )
            )
        except (RuntimeError, SandboxUnavailableError) as exc:
            # An already-running event loop, or a host with no OS sandbox
            # (`SandboxUnavailableError`, design D2: code nodes refuse rather
            # than run unsandboxed), is an environment problem, not a draft
            # problem; report it instead of claiming a pass.
            executions.append({
                "node_id": node_id,
                "status": "refused",
                "reason": f"sandbox.unavailable: {exc}",
            })
            continue

        elapsed = time.monotonic() - started
        output = outcome.output_state or {}
        output_bytes = len(canonical_json(output).encode("utf-8"))
        record: dict[str, Any] = {
            "node_id": node_id,
            "status": "passed" if outcome.success else "failed",
            "reason": "",
            "duration_seconds": round(elapsed, 4),
            "output_bytes": output_bytes,
            "output_keys": sorted(output),
        }
        if not outcome.success:
            record["reason"] = str(outcome.error)[:500]
        for budget, amount in (
            ("wall_seconds", elapsed),
            ("max_output_bytes", output_bytes),
        ):
            try:
                ledger.charge(budget, amount)
            except BudgetExceeded as exc:
                budget_error = {
                    "budget": exc.budget,
                    "limit": exc.limit,
                    "attempted": exc.attempted,
                }
                record["status"] = "failed"
                record["reason"] = f"sandbox.budget_exceeded: {exc.budget}"
        executions.append(record)
        if budget_error is not None:
            break

    return executions, budget_error


def request_confirmation(
    *,
    actor_id: str,
    session_id: str,
    effect_name: str,
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Show destination/class/payload/credential/idempotency, then mint a token."""
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    session = store.get_session(session_id, actor_id=actor)
    wanted = (effect_name or "").strip()
    for effect in session.definition.get("effects", []):
        if isinstance(effect, dict) and str(effect.get("name", "")) == wanted:
            issued = authoring_sandbox.issue_confirmation(
                store,
                session_id=session.session_id,
                owner_id=actor,
                draft_version=session.draft_version,
                effect=effect,
                payload=effect.get("payload_example") or {},
            )
            store.append_event(
                session.session_id,
                actor_id=actor,
                event_type="confirmation",
                definition_hash=session.definition_hash,
                payload={
                    "effect": wanted,
                    "draft_version": session.draft_version,
                    "confirmation": issued["confirmation"],
                },
            )
            return issued
    raise AuthoringValidationError([
        ValidationIssue(
            "effect.unknown", "effect_name", f"no declared effect named {wanted!r}",
        ),
    ])


# ── publish ────────────────────────────────────────────────────────────────


def publish_session(
    *,
    actor_id: str,
    session_id: str,
    expected_version: int,
    change_message: str = "",
    visibility: str = "public",
    acknowledge_risks: list[str] | None = None,
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Publish exactly one reviewed draft version as an immutable artifact."""
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    session = store.get_session(session_id, actor_id=actor)

    if visibility not in VISIBILITIES:
        raise AuthoringValidationError([
            ValidationIssue(
                "publish.unknown_visibility",
                "visibility",
                f"visibility must be one of {sorted(VISIBILITIES)}",
            ),
        ])
    if int(expected_version) != session.draft_version:
        raise AuthoringConflictError(
            "the session advanced after review: publication requested for version "
            f"{expected_version}, current draft is {session.draft_version}"
        )

    issues = _validate(session, store, actor)
    blockers = list(issues)
    acknowledged = {str(item) for item in (acknowledge_risks or [])}

    # Required tests: the exact draft *version* must have a *clean* test.
    # Hash equality alone is not enough — a definition hash recurs if the user
    # edits away and back, which would let a stale test from an earlier version
    # satisfy the gate. And a test that ended blocked, network-denied, invalid,
    # budget-stopped, or with a failed node execution is evidence of a problem,
    # not evidence of readiness.
    candidates = store.find_test_events(
        session.session_id, actor_id=actor, definition_hash=session.definition_hash
    )
    tested = [
        event for event in candidates
        if int(event.payload.get("draft_version", -1)) == session.draft_version
        and event.payload.get("clean") is True
    ]
    if not tested:
        unclean = [
            event for event in candidates
            if int(event.payload.get("draft_version", -1)) == session.draft_version
        ]
        detail = (
            "the most recent test of this version did not pass "
            f"(status={unclean[-1].payload.get('status', 'unknown')!r}); fix the "
            "reported problem and re-test before publishing"
            if unclean
            else "run a test against this exact draft version before publishing"
        )
        blockers.append(ValidationIssue("publish.untested_version", "tests", detail))

    # Effect/credential review: every declared irreversible effect must be
    # acknowledged by name, so publication is never a silent effect grant.
    for index, effect in enumerate(session.definition.get("effects", [])):
        if not isinstance(effect, dict):
            continue
        if authoring_sandbox.classify_effect(effect) != "irreversible":
            continue
        name = str(effect.get("name", ""))
        if name not in acknowledged:
            blockers.append(ValidationIssue(
                "publish.unacknowledged_effect",
                f"effects[{index}]",
                f"declared irreversible effect {name!r} must be acknowledged "
                "at publication (acknowledge_risks)",
            ))

    if blockers:
        store.append_event(
            session.session_id,
            actor_id=actor,
            event_type="publish_failed",
            definition_hash=session.definition_hash,
            payload={
                "draft_version": session.draft_version,
                "issues": [issue.to_dict() for issue in blockers],
            },
        )
        raise AuthoringValidationError(blockers)

    version = store.publish_version(
        artifact_id=session.artifact_id,
        artifact_kind=session.artifact_kind,
        owner_id=actor,
        visibility=visibility,
        definition=session.definition,
        definition_hash=session.definition_hash,
        change_message=change_message,
        # Re-checked inside the insert transaction: if the draft advances between
        # validation and commit, the reviewed version must not publish anyway.
        source_session_id=session.session_id,
        expected_draft_version=session.draft_version,
        provenance={
            "source_session_id": session.session_id,
            "source_draft_version": session.draft_version,
            "seed_mode": session.seed_mode,
            "seed_ref": session.seed_ref,
            "author": actor,
            "source": {"kind": "authoring_session", "session_id": session.session_id},
        },
        evidence={
            "tests": [
                {
                    "event_id": event.event_id,
                    "created_at": event.created_at,
                    "mode": event.payload.get("mode", ""),
                    "definition_hash": event.definition_hash,
                }
                for event in tested
            ],
            "validation": {"valid": True, "issues": []},
            "acknowledged_risks": sorted(acknowledged),
        },
    )
    store.append_event(
        session.session_id,
        actor_id=actor,
        event_type="publish",
        definition_hash=session.definition_hash,
        payload={
            "version_id": version.version_id,
            "version_no": version.version_no,
            "draft_version": session.draft_version,
            "visibility": visibility,
        },
    )
    return {"published": True, "version": version.to_dict()}


def import_definition(
    *,
    actor_id: str,
    artifact_kind: str,
    definition: dict[str, Any],
    source_provenance: dict[str, Any],
    change_message: str = "",
    visibility: str = "public",
    store: AuthoringStore | None = None,
) -> dict[str, Any]:
    """Contributor path: materialize a reviewed code-defined artifact.

    Same versioned contract, same inspection guarantees, same immutability as the
    chat path — the only difference is provenance, which records the reviewed
    source instead of a chat session.
    """
    actor = _require_actor(actor_id)
    store = _resolve_store(store)
    kind = (artifact_kind or "").strip()
    if kind not in ARTIFACT_KINDS:
        raise AuthoringValidationError([
            ValidationIssue("session.unknown_artifact_kind", "artifact_kind", kind),
        ])
    if visibility not in VISIBILITIES:
        raise AuthoringValidationError([
            ValidationIssue("publish.unknown_visibility", "visibility", visibility),
        ])
    if not isinstance(source_provenance, dict) or not source_provenance.get("kind"):
        raise AuthoringValidationError([
            ValidationIssue(
                "import.provenance_required",
                "source_provenance",
                "declare the source kind (e.g. contributor_source) and its review ref",
            ),
        ])

    artifact_id = f"art_{new_ulid()}"
    issues = _validate_definition_for(
        definition,
        artifact_kind=kind,
        artifact_id=artifact_id,
        store=store,
        actor_id=actor,
    )
    if issues:
        raise AuthoringValidationError(issues)

    version = store.publish_version(
        artifact_id=artifact_id,
        artifact_kind=kind,
        owner_id=actor,
        visibility=visibility,
        definition=definition,
        definition_hash=definition_hash(definition),
        change_message=change_message,
        provenance={
            "source_session_id": "",
            "source_draft_version": 0,
            "author": actor,
            "source": dict(source_provenance),
        },
        evidence={
            "tests": [],
            "validation": {"valid": True, "issues": []},
            "review": source_provenance.get("review", ""),
        },
    )
    return {"published": True, "version": version.to_dict()}


# ── reads ──────────────────────────────────────────────────────────────────


def list_sessions(
    *, actor_id: str, limit: int = 50, store: AuthoringStore | None = None
) -> list[dict[str, Any]]:
    store = _resolve_store(store)
    sessions = store.list_sessions(actor_id=_require_actor(actor_id), limit=limit)
    return [session.to_dict() for session in sessions]


def list_versions(
    *,
    actor_id: str,
    artifact_id: str = "",
    limit: int = 50,
    store: AuthoringStore | None = None,
) -> list[dict[str, Any]]:
    store = _resolve_store(store)
    versions = store.list_versions(
        actor_id=_require_actor(actor_id), artifact_id=artifact_id, limit=limit
    )
    return [version.to_dict() for version in versions]


def get_version(
    *, actor_id: str, version_id: str, store: AuthoringStore | None = None
) -> dict[str, Any]:
    store = _resolve_store(store)
    if not (version_id or "").strip():
        raise access_denied()
    return store.get_version(
        version_id.strip(), actor_id=_require_actor(actor_id)
    ).to_dict()


# ── router half — extensions dispatch table ────────────────────────────────


def _json_error(exc: Exception) -> str:
    payload: dict[str, Any] = {"error": str(exc), "code": type(exc).__name__}
    if isinstance(exc, AuthoringValidationError):
        payload["issues"] = [issue.to_dict() for issue in exc.issues]
    return json.dumps(payload)


def _guard(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict], str]:
    """Wrap a handler so the router always returns JSON, never an exception."""

    def wrapped(kwargs: dict[str, Any]) -> str:
        try:
            return json.dumps(handler(kwargs), default=str)
        except (
            AuthoringValidationError,
            AuthoringAccessError,
            AuthoringConflictError,
            SandboxDenied,
            ConfirmationRequired,
            BudgetExceeded,
            AuthoringError,
        ) as exc:
            return _json_error(exc)
        except (ValueError, TypeError, LookupError) as exc:
            return json.dumps({"error": str(exc), "code": type(exc).__name__})

    return wrapped


def _parse_json_object(raw: str, field: str) -> dict[str, Any]:
    if not (raw or "").strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthoringValidationError([
            ValidationIssue("router.malformed_json", field, str(exc)),
        ]) from exc
    if not isinstance(parsed, dict):
        raise AuthoringValidationError([
            ValidationIssue("router.expected_object", field, "expected a JSON object"),
        ])
    return parsed


def _parse_json_array(raw: str, field: str) -> list[Any]:
    if not (raw or "").strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthoringValidationError([
            ValidationIssue("router.malformed_json", field, str(exc)),
        ]) from exc
    if not isinstance(parsed, list):
        raise AuthoringValidationError([
            ValidationIssue("router.expected_array", field, "expected a JSON array"),
        ])
    return parsed


def _optional_int(raw: Any, field: str) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise AuthoringValidationError([
            ValidationIssue("router.expected_integer", field, str(raw)),
        ]) from exc


def _action_start(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "started",
        "session": start_session(
            actor_id=kwargs["actor_id"],
            artifact_kind=kwargs.get("artifact_kind") or "node",
            sketch=kwargs.get("sketch", ""),
            base_version_id=kwargs.get("base_version_id", ""),
            resume_session_id=kwargs.get("resume_session_id", ""),
        ),
    }


def _action_inspect(kwargs: dict[str, Any]) -> dict[str, Any]:
    return inspect_session(
        actor_id=kwargs["actor_id"],
        session_id=kwargs.get("session_id", ""),
        view=kwargs.get("view") or "full",
        anchor=kwargs.get("anchor", ""),
        limit=int(kwargs.get("limit") or 200),
    )


def _action_edit(kwargs: dict[str, Any]) -> dict[str, Any]:
    return apply_edit_batch(
        actor_id=kwargs["actor_id"],
        session_id=kwargs.get("session_id", ""),
        operations=_parse_json_array(kwargs.get("operations_json", ""), "changes_json"),
        expected_version=_optional_int(
            kwargs.get("expected_version"), "expected_version"
        ),
    )


def _action_test(kwargs: dict[str, Any]) -> dict[str, Any]:
    body = _parse_json_object(kwargs.get("payload_json", ""), "payload_json")
    return run_test(
        actor_id=kwargs["actor_id"],
        session_id=kwargs.get("session_id", ""),
        inputs=body.get("inputs"),
        mode=kwargs.get("mode") or "dry",
        confirmation=kwargs.get("confirmation", ""),
    )


def _action_confirm_effect(kwargs: dict[str, Any]) -> dict[str, Any]:
    return request_confirmation(
        actor_id=kwargs["actor_id"],
        session_id=kwargs.get("session_id", ""),
        effect_name=kwargs.get("effect_name", ""),
    )


def _action_publish(kwargs: dict[str, Any]) -> dict[str, Any]:
    body = _parse_json_object(kwargs.get("payload_json", ""), "payload_json")
    expected = _optional_int(kwargs.get("expected_version"), "expected_version")
    if expected is None:
        raise AuthoringValidationError([
            ValidationIssue(
                "publish.expected_version_required",
                "expected_version",
                "publication must name the exact reviewed draft version",
            ),
        ])
    risks = body.get("acknowledge_risks") or []
    if not isinstance(risks, list):
        raise AuthoringValidationError([
            ValidationIssue(
                "router.expected_array", "payload_json.acknowledge_risks", "",
            ),
        ])
    return publish_session(
        actor_id=kwargs["actor_id"],
        session_id=kwargs.get("session_id", ""),
        expected_version=expected,
        change_message=kwargs.get("change_message", ""),
        visibility=str(body.get("visibility") or "public"),
        acknowledge_risks=risks,
    )


def _action_list(kwargs: dict[str, Any]) -> dict[str, Any]:
    sessions = list_sessions(
        actor_id=kwargs["actor_id"], limit=int(kwargs.get("limit") or 50)
    )
    return {
        "sessions": [
            {
                "session_id": session["session_id"],
                "artifact_id": session["artifact_id"],
                "artifact_kind": session["artifact_kind"],
                "status": session["status"],
                "draft_version": session["draft_version"],
                "name": session["definition"].get("name", ""),
                "updated_at": session["updated_at"],
            }
            for session in sessions
        ],
        "count": len(sessions),
    }


#: ``extensions`` action table. Keys are the advertised-action names inside the
#: canonical ``extensions`` router; no new MCP handle is created.
_AUTHORING_ACTIONS: dict[str, Callable[[dict[str, Any]], str]] = {
    "authoring_start": _guard(_action_start),
    "authoring_inspect": _guard(_action_inspect),
    "authoring_edit": _guard(_action_edit),
    "authoring_test": _guard(_action_test),
    "authoring_confirm_effect": _guard(_action_confirm_effect),
    "authoring_publish": _guard(_action_publish),
    "authoring_list": _guard(_action_list),
}

#: Actions that mutate authoring state (OAuth scope derivation, auth/provider.py).
_AUTHORING_WRITE_ACTIONS: frozenset[str] = frozenset({
    "authoring_start",
    "authoring_edit",
    "authoring_test",
    "authoring_confirm_effect",
    "authoring_publish",
})

#: A test run consumes model/execution budget — scope it costly like run_branch.
_AUTHORING_COSTLY_ACTIONS: frozenset[str] = frozenset({"authoring_test"})
