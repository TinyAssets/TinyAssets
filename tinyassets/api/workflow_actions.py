"""Owner-local workflow action dispatch; extensions use activated v2 packages."""
from __future__ import annotations

import json
import logging
from typing import Any

from tinyassets.api.auto_ship_actions import _AUTO_SHIP_ACTIONS
from tinyassets.api.branches import (
    _BRANCH_ACTIONS,
    _dispatch_branch_action,
)
from tinyassets.api.evaluation import (
    _BRANCH_VERSION_ACTIONS,
    _JUDGMENT_ACTIONS,
    _dispatch_judgment_action,
)
from tinyassets.api.extensions_consent_actions import _EFFECTOR_CONSENT_ACTIONS
from tinyassets.api.extensions_leaderboard_actions import _LEADERBOARD_ACTIONS
from tinyassets.api.goals import (
    _GATE_EVENT_ACTIONS,
)
from tinyassets.api.helpers import _base_path
from tinyassets.api.runs import (
    _RUN_ACTIONS,
    _dispatch_run_action,
)
from tinyassets.api.runtime_ops import (
    _INSPECT_DRY_ACTIONS,
    _MESSAGING_ACTIONS,
    _PROJECT_MEMORY_ACTIONS,
    _PROJECT_MEMORY_WRITE_ACTIONS,
    _SCHEDULER_ACTIONS,
)
from tinyassets.attribution.actions import _ATTRIBUTION_ACTIONS
from tinyassets.authoring.service import _AUTHORING_ACTIONS
from tinyassets.handoffs.service import _HANDOFF_ACTIONS
from tinyassets.outcomes.actions import _OUTCOME_ACTIONS

logger = logging.getLogger(__name__)

_RETIRED_PAID_ACTIONS = frozenset({
    "escrow_lock", "escrow_release", "escrow_refund", "escrow_inspect",
    "escrow_fund", "escrow_balance", "escrow_set_wallet", "escrow_withdraw",
})


# ═══════════════════════════════════════════════════════════════════════════
# TOOL 2 — Extensions (node registration system)
# ═══════════════════════════════════════════════════════════════════════════


def _dispatch_scope_error(tool: str, action: str) -> str | None:
    from tinyassets.auth.middleware import require_action_scope
    from tinyassets.auth.provider import PermissionScope

    try:
        require_action_scope(
            tool,
            action,
            scope=PermissionScope(resource_type="mcp-tool", resource_id=tool),
        )
    except PermissionError as exc:
        return json.dumps({
            "error": str(exc),
            "auth_scope_required": True,
            "tool": tool,
            "action": action,
        })
    return None


def _public_goal_read_rejection(action: str, goal_id: str) -> str | None:
    """Fail closed when a read action names a Goal that is not public."""
    goal_id = goal_id.strip()
    if not goal_id:
        return None
    if action in _LEADERBOARD_ACTIONS:
        # These handlers already apply the same exact-public check and retain
        # their established ``goal=None`` / ``entries=[]`` response envelope.
        return None

    from tinyassets.auth.provider import action_scope_for
    from tinyassets.daemon_server import get_goal

    action_scope = action_scope_for("extensions", action)
    if action_scope is None or action_scope.effect != "read":
        return None

    try:
        goal = get_goal(_base_path(), goal_id=goal_id)
    except KeyError:
        goal = None
    if goal is not None and goal.get("visibility") == "public":
        return None
    return json.dumps({
        "status": "rejected",
        "error": f"Goal '{goal_id}' not found.",
    })


def _public_goal_ids() -> set[str]:
    """Return the complete exactly-public Goal ID set for record filtering."""
    from tinyassets.daemon_server import public_goal_ids

    return public_goal_ids(_base_path())


def _filter_branch_goal_records(action: str, result: str) -> str:
    """Remove non-public Goal attachments from public Branch reads."""
    if action not in {"get_branch", "list_branches"}:
        return result
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result
    if not isinstance(payload, dict) or payload.get("error"):
        return result

    public_ids = _public_goal_ids()
    if action == "list_branches":
        rows = payload.get("branches")
        if not isinstance(rows, list):
            return result
        kept = [
            row for row in rows
            if isinstance(row, dict)
            and (
                not (row.get("goal_id") or "")
                or row.get("goal_id") in public_ids
            )
        ]
        payload["branches"] = kept
        payload["count"] = len(kept)
        return json.dumps(payload, default=str)

    goal_id = str(payload.get("goal_id") or "")
    if goal_id and goal_id not in public_ids:
        payload.pop("goal_id", None)
    claims = payload.get("gate_claims")
    if isinstance(claims, list):
        payload["gate_claims"] = [
            claim for claim in claims
            if isinstance(claim, dict)
            and (claim.get("goal_id") or "") in public_ids
        ]
    return json.dumps(payload, default=str)


def _extensions_impl(
    action: str,
    node_id: str = "",
    display_name: str = "",
    description: str = "",
    phase: str = "",
    input_keys: str = "",
    output_keys: str = "",
    source_code: str = "",
    branch_def_id: str = "",
    name: str = "",
    domain_id: str = "",
    author: str = "",
    from_node: str = "",
    to_node: str = "",
    prompt_template: str = "",
    field_name: str = "",
    field_type: str = "",
    reducer: str = "",
    field_default: str = "",
    run_id: str = "",
    inputs_json: str = "",
    run_name: str = "",
    resume_from: str = "",
    status: str = "",
    since_step: int = -1,
    max_wait_s: int = 60,
    limit: int = 50,
    spec_json: str = "",
    changes_json: str = "",
    judgment_text: str = "",
    judgment_id: str = "",
    tags: str = "",
    run_a_id: str = "",
    run_b_id: str = "",
    field: str = "",
    value: str = "",
    node_ids: str = "",
    context: str = "",
    triggered_by_judgment_id: str = "",
    to_version: str = "",
    goal_id: str = "",
    node_ref_json: str = "",
    intent: str = "",
    node_query: str = "",
    scope: str = "published",
    force: bool = False,
    project_id: str = "",
    key: str = "",
    key_prefix: str = "",
    expected_version: str = "",
    recursion_limit_override: str = "",
    filters_json: str = "",
    select: str = "",
    aggregate_json: str = "",
    receipt_type: str = "",
    payload_json: str = "",
    subject_id: str = "",
    branch_spec_json: str = "",
    from_run_id: str = "",
    to_node_id: str = "",
    message_type: str = "",
    body_json: str = "",
    ship_attempt_id: str = "",
    head_branch: str = "",
    title: str = "",
    pr_body: str = "",
    base_branch: str = "",
    reply_to_message_id: str = "",
    message_types: str = "",
    message_id: str = "",
    since: str = "",
    branch_version_id: str = "",
    parent_version_id: str = "",
    child_run_id: str = "",
    notes: str = "",
    event_id: str = "",
    event_type: str = "",
    event_date: str = "",
    attested_by: str = "",
    cites_json: str = "",
    verifier_id: str = "",
    disputed_by: str = "",
    retracted_by: str = "",
    schedule_id: str = "",
    cron_expr: str = "",
    interval_seconds: float = 0.0,
    owner_actor: str = "",
    inputs_template_json: str = "",
    skip_if_running: bool = False,
    subscription_id: str = "",
    active_only: bool = True,
    token: str = "",
    token_prefix: str = "",
    source_id: str = "",
    outcome_id: str = "",
    evidence_url: str = "",
    gate_event_id: str = "",
    outcome_payload_json: str = "",
    outcome_note: str = "",
    parent_branch_def_id: str = "",
    child_branch_def_id: str = "",
    output_digest: str = "",
    contribution_kind: str = "remix",
    credit_share: float = 0.0,
    max_depth: int = 10,
    reason: str = "",
    severity: str = "P1",
    since_days: int = 7,
    record_in_ledger: bool = False,
    universe_id: str = "",
    request_id: str = "",
    parent_run_id: str = "",
    release_gate_result: str = "",
    ship_class: str = "",
    changed_paths_json: str = "",
    stable_evidence_handle: str = "",
    bounded_output: bool = False,
    output_offset: int = 0,
    output_max_chars: int = 8192,
) -> str:
    if action in _RETIRED_PAID_ACTIONS:
        return json.dumps({
            "status": "rejected",
            "error": f"Action '{action}' was retired with the paid market.",
        })

    if action == "get_action_scope_status":
        from tinyassets.auth.provider import action_scope_audit

        return json.dumps(action_scope_audit(), default=str)

    scope_error = _dispatch_scope_error("extensions", action)
    if scope_error is not None:
        return scope_error

    goal_rejection = _public_goal_read_rejection(action, goal_id)
    if goal_rejection is not None:
        return goal_rejection

    # ── Phase 2: Community Branches ────────────────────────────────────────
    branch_kwargs: dict[str, Any] = {
        "branch_def_id": branch_def_id,
        "name": name,
        "description": description,
        "domain_id": domain_id,
        "author": author,
        "node_id": node_id,
        "display_name": display_name,
        "phase": phase,
        "source_code": source_code,
        "prompt_template": prompt_template,
        "input_keys": input_keys,
        "output_keys": output_keys,
        "from_node": from_node,
        "to_node": to_node,
        "field_name": field_name,
        "field_type": field_type,
        "reducer": reducer,
        "field_default": field_default,
        "spec_json": spec_json,
        "changes_json": changes_json,
        "field": field,
        "value": value,
        "node_ids": node_ids,
        "triggered_by_judgment_id": triggered_by_judgment_id,
        "goal_id": goal_id,
        "intent": intent,
        "query": node_query,
        "limit": limit,
        "scope": scope,
        "force": force,
        "request_id": request_id,
        "reason": reason,
    }
    if node_ref_json:
        try:
            parsed_ref = json.loads(node_ref_json)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "error": f"node_ref_json is not valid JSON: {exc}",
            })
        branch_kwargs["node_ref"] = parsed_ref
    branch_handler = _BRANCH_ACTIONS.get(action)
    if branch_handler is not None:
        result = _dispatch_branch_action(action, branch_handler, branch_kwargs)
        return _filter_branch_goal_records(action, result)

    # ── Phase 3: Graph Runner ──────────────────────────────────────────────
    run_kwargs: dict[str, Any] = {
        "branch_def_id": branch_def_id,
        "branch_version_id": branch_version_id,
        "run_id": run_id,
        "child_run_id": child_run_id,
        "child_branch_def_id": child_branch_def_id,
        "output_digest": output_digest,
        "inputs_json": inputs_json,
        "run_name": run_name,
        "resume_from": resume_from,
        "node_id": node_id,
        "status": status,
        "since_step": since_step,
        "max_wait_s": max_wait_s,
        "limit": limit,
        "field_name": field_name,
        "recursion_limit_override": recursion_limit_override,
        "bounded_output": bounded_output,
        "output_offset": output_offset,
        "output_max_chars": output_max_chars,
        "filters_json": filters_json,
        "select": select,
        "aggregate_json": aggregate_json,
        "receipt_type": receipt_type,
        "payload_json": payload_json,
        "subject_id": subject_id,
        "universe_id": universe_id,
        # Inbound-trigger ops (webhook mint/revoke/list, source create/revoke/list).
        "token": token,
        "token_prefix": token_prefix,
        "source_id": source_id,
        # Surgical-rollback args (Task #22 Phase B).
        "reason": reason,
        "severity": severity,
        "since_days": since_days,
    }
    run_handler = _RUN_ACTIONS.get(action)
    if run_handler is not None:
        return _dispatch_run_action(action, run_handler, run_kwargs)

    # ── Phase 4: Eval + iteration hooks ────────────────────────────────────
    judgment_kwargs: dict[str, Any] = {
        "branch_def_id": branch_def_id,
        "run_id": run_id,
        "node_id": node_id,
        "judgment_text": judgment_text,
        "judgment_id": judgment_id,
        "tags": tags,
        "run_a_id": run_a_id,
        "run_b_id": run_b_id,
        "field": field,
        "context": context,
        "limit": limit,
        "to_version": to_version,
    }
    judgment_handler = _JUDGMENT_ACTIONS.get(action)
    if judgment_handler is not None:
        return _dispatch_judgment_action(
            action, judgment_handler, judgment_kwargs,
        )

    # ── Project Memory ─────────────────────────────────────────────────────
    pm_kwargs: dict[str, Any] = {
        "project_id": project_id,
        "key": key,
        "key_prefix": key_prefix,
        "value": value,
        "expected_version": expected_version if expected_version else None,
        "limit": limit,
    }
    pm_handler = _PROJECT_MEMORY_ACTIONS.get(action)
    if pm_handler is not None:
        result_str = pm_handler(pm_kwargs)
        if action in _PROJECT_MEMORY_WRITE_ACTIONS:
            try:
                res = json.loads(result_str)
                if isinstance(res, dict) and not res.get("error") and not res.get("conflict"):
                    from tinyassets.api.branches import (
                        _append_global_ledger,
                        ledger_actor,
                    )
                    _append_global_ledger(
                        action=action,
                        actor=ledger_actor(),
                        target=f"{project_id}/{key}",
                        summary=f"{action} project_id={project_id} key={key}",
                    )
            except (json.JSONDecodeError, TypeError):
                pass
        return result_str

    # ── Branch versioning ──────────────────────────────────────────────────
    bv_handler = _BRANCH_VERSION_ACTIONS.get(action)
    if bv_handler is not None:
        from tinyassets.api.permissions import current_request_actor_id

        bv_kwargs: dict[str, Any] = {
            "branch_def_id": branch_def_id,
            "branch_version_id": branch_version_id,
            "parent_version_id": parent_version_id,
            "notes": notes,
            "publisher": current_request_actor_id(),
            "limit": limit,
        }
        return bv_handler(bv_kwargs)

    # ── Teammate messaging ─────────────────────────────────────────────────
    messaging_handler = _MESSAGING_ACTIONS.get(action)
    if messaging_handler is not None:
        messaging_kwargs: dict[str, Any] = {
            "from_run_id": from_run_id,
            "to_node_id": to_node_id,
            "message_type": message_type,
            "body_json": body_json,
            "reply_to_message_id": reply_to_message_id,
            "message_types": message_types,
            "node_id": node_id,
            "message_id": message_id,
            "since": since,
            "limit": limit,
        }
        return messaging_handler(messaging_kwargs)

    # ── Gate events (real-world outcome attestation) ───────────────────────
    gate_event_handler = _GATE_EVENT_ACTIONS.get(action)
    if gate_event_handler is not None:
        ge_kwargs: dict[str, Any] = {
            "goal_id": goal_id,
            "event_id": event_id,
            "event_type": event_type,
            "event_date": event_date,
            "attested_by": attested_by,
            "cites_json": cites_json,
            "verifier_id": verifier_id,
            "disputed_by": disputed_by,
            "retracted_by": retracted_by,
            "reason": notes,
            "note": notes,
            "branch_version_id": branch_version_id,
            "since": since,
            "limit": limit,
        }
        return gate_event_handler(ge_kwargs)

    # ── Dry inspect ────────────────────────────────────────────────────────
    inspect_dry_handler = _INSPECT_DRY_ACTIONS.get(action)
    if inspect_dry_handler is not None:
        di_kwargs: dict[str, Any] = {
            "branch_def_id": branch_def_id,
            "node_id": node_id,
            "branch_spec_json": branch_spec_json,
            "changes_json": changes_json,
        }
        return inspect_dry_handler(di_kwargs)

    # ── Auto-ship validator (PR #198 Phase 2A) ─────────────────────────────
    # Wraps tinyassets.auto_ship.validate_ship_request as an MCP action so the
    # loop's release_safety_gate prompt (and chatbots / canaries) can call it
    # via tool. Pure validator — no IO, no repo writes.
    auto_ship_handler = _AUTO_SHIP_ACTIONS.get(action)
    if auto_ship_handler is not None:
        as_kwargs: dict[str, Any] = {
            "body_json": body_json,
            "record_in_ledger": record_in_ledger,
            "universe_id": universe_id,
            "request_id": request_id,
            "parent_run_id": parent_run_id,
            "child_run_id": child_run_id,
            "branch_def_id": branch_def_id,
            "release_gate_result": release_gate_result,
            "ship_class": ship_class,
            "changed_paths_json": changed_paths_json,
            "stable_evidence_handle": stable_evidence_handle,
            "ship_attempt_id": ship_attempt_id,
            "head_branch": head_branch,
            "title": title,
            "body": pr_body,
            "base_branch": base_branch,
        }
        return auto_ship_handler(as_kwargs)

    # ── Event subscriptions ───────────────────────────────────────────────
    scheduler_handler = _SCHEDULER_ACTIONS.get(action)
    if scheduler_handler is not None:
        sched_kwargs: dict[str, Any] = {
            "branch_def_id": branch_def_id,
            "owner_actor": owner_actor,
            "subscription_id": subscription_id,
            "event_type": event_type,
            "active_only": active_only,
        }
        return scheduler_handler(sched_kwargs)

    # ── Outcome events ─────────────────────────────────────────────────────
    outcome_handler = _OUTCOME_ACTIONS.get(action)
    if outcome_handler is not None:
        actor_id = ""
        if action == "record_outcome":
            from tinyassets.handoffs.authority import request_subject
            from tinyassets.handoffs.models import HandoffAuthorityError

            try:
                actor_id = request_subject()
            except HandoffAuthorityError as exc:
                return json.dumps({"error": str(exc), "code": exc.code})
        oc_kwargs: dict[str, Any] = {
            "actor_id": actor_id,
            "branch_def_id": branch_def_id,
            "run_id": run_id,
            "outcome_id": outcome_id,
            "outcome_type": event_type,  # reuse event_type param
            "evidence_url": evidence_url,
            "gate_event_id": gate_event_id,
            "payload_json": outcome_payload_json,
            "note": outcome_note,
            "limit": limit,
        }
        return outcome_handler(oc_kwargs)

    # ── Effector consent grants (PR-122 Phase 2 Slice 1) ───────────────────
    consent_handler = _EFFECTOR_CONSENT_ACTIONS.get(action)
    if consent_handler is not None:
        # Field reuse: ``intent`` carries the sink name and ``project_id``
        # carries the destination, so chatbots can call this without
        # adding new tool-signature kwargs. Slice-2 may add dedicated
        # ``sink`` / ``destination`` arg names; for now reuse the
        # existing slots to keep the MCP surface stable.
        consent_kwargs: dict[str, Any] = {
            "sink": intent or "",
            "destination": project_id or "",
            "granted_by": author or "",
            "active_only": active_only,
        }
        return consent_handler(consent_kwargs)

    # ── Attribution chain ──────────────────────────────────────────────────
    attribution_handler = _ATTRIBUTION_ACTIONS.get(action)
    if attribution_handler is not None:
        from tinyassets.api.engine_helpers import _current_actor
        attr_kwargs: dict[str, Any] = {
            "parent_branch_def_id": parent_branch_def_id,
            "child_branch_def_id": child_branch_def_id,
            "contribution_kind": contribution_kind,
            "credit_share": credit_share,
            "max_depth": max_depth,
            "actor_id": _current_actor(),
        }
        return attribution_handler(attr_kwargs)

    # ── Node / evaluator authoring sessions (target 4.3) ───────────────────
    # Authoring composes under this canonical router: no new advertised MCP
    # handle, and the ``extensions`` tool signature is NOT widened. Each
    # authoring parameter reuses an existing kwarg (same technique as the
    # effector-consent actions above), which is also why a chatbot can reach
    # these without a connector-surface change:
    #   key                → session_id          field_type   → artifact_kind
    #   intent             → sketch / effect name resume_from  → draft to resume
    #   branch_version_id  → base published ver.  select       → view
    #   since              → diff anchor event_id changes_json → edit operations
    #   expected_version   → reviewed draft ver.  notes        → change message
    #   value              → test mode            request_id   → confirmation token
    #   payload_json       → action body (test inputs, visibility, risk acks)
    authoring_handler = _AUTHORING_ACTIONS.get(action)
    if authoring_handler is not None:
        from tinyassets.api.engine_helpers import _current_actor

        return authoring_handler({
            "actor_id": _current_actor(),
            "session_id": key,
            "artifact_kind": field_type,
            "sketch": intent,
            "effect_name": intent,
            "base_version_id": branch_version_id,
            "resume_session_id": resume_from,
            "view": select,
            "anchor": since,
            "operations_json": changes_json,
            "expected_version": expected_version,
            "change_message": notes,
            "mode": value,
            "confirmation": request_id,
            "payload_json": payload_json,
            "limit": limit,
        })

    # ── Real-world handoffs and outcomes (target 5.2/5.4) ──────────────────
    # Handoffs compose under this canonical router: no new advertised MCP handle
    # and the ``extensions`` tool signature is NOT widened. Each parameter reuses
    # an existing kwarg (same technique as the effector-consent and authoring
    # actions), so a chatbot reaches these without a connector-surface change:
    #   key            → handoff_id        field_name  → declared output field
    #   project_id     → destination check request_id  → confirmation token
    #   event_type     → outcome_kind      status      → target state / filter
    #   notes          → attestation note  payload_json→ external_id + evidence
    # Authority is NOT taken from any of them: the acting subject is resolved
    # server-side from the credential-validated request, and source ownership
    # comes from the persisted run + immutable version.
    handoff_handler = _HANDOFF_ACTIONS.get(action)
    if handoff_handler is not None:
        from tinyassets.api.helpers import _request_universe, _universe_dir
        from tinyassets.handoffs.authority import request_subject
        from tinyassets.handoffs.models import HandoffAuthorityError

        try:
            subject = request_subject()
        except HandoffAuthorityError as exc:
            return json.dumps({"error": str(exc), "code": exc.code})
        return handoff_handler({
            "actor_id": subject,
            "base_path": _base_path(),
            "universe_dir": _universe_dir(_request_universe(universe_id)),
            "handoff_id": key,
            "run_id": run_id,
            "branch_version_id": branch_version_id,
            "output_field": field_name,
            "destination": project_id,
            "confirmation": request_id,
            "outcome_kind": event_type,
            "state": status,
            "evidence_url": evidence_url,
            "outcome_id": outcome_id,
            "note": notes,
            "payload_json": payload_json,
            "limit": limit,
        })

    # ── Quality leaderboard / parent selection (PR-123 substrate M2) ───────
    leaderboard_handler = _LEADERBOARD_ACTIONS.get(action)
    if leaderboard_handler is not None:
        # P1.1 fix (round 2) — the dispatch MUST NOT forward
        # caller-supplied ``author`` / ``force`` into the leaderboard
        # handler's visibility surface. Round-1 mapped
        # ``author -> viewer`` and ``force -> include_private``, which
        # let an MCP caller see private branches authored by anyone
        # they could name. The handler now resolves viewer identity
        # server-side via ``_current_actor()``; visibility is
        # public-or-author-owned for the actual caller, never the
        # caller-named identity.
        return leaderboard_handler({"goal_id": goal_id})

    return json.dumps({
        "error": f"Unknown action '{action}'.",
        "available_actions": [
            "build_branch", "patch_branch", "update_node",
            "create_branch", "add_node", "connect_nodes",
            "set_entry_point", "add_state_field",
            "validate_branch", "describe_branch",
            "get_branch", "list_branches", "delete_branch", "delete_own_branch",
            "run_branch", "get_run", "list_runs",
            "stream_run", "wait_for_run", "cancel_run", "get_run_output",
            "attach_existing_child_run",
            "resume_run", "query_runs",
            "get_action_scope_status",
            "judge_run", "list_judgments", "compare_runs",
            "suggest_node_edit", "get_node_output",
            "rollback_node", "list_node_versions",
            "project_memory_get", "project_memory_set", "project_memory_list",
            "dry_inspect_node", "dry_inspect_patch",
            "messaging_send", "messaging_receive", "messaging_ack",
            "publish_version", "get_branch_version", "list_branch_versions",
            "fork_tree",
            "attest_gate_event", "verify_gate_event", "dispute_gate_event",
            "retract_gate_event", "get_gate_event", "list_gate_events",
            "subscribe_branch", "unsubscribe_branch", "list_scheduler_subscriptions",
            "record_outcome", "list_outcomes", "get_outcome",
            "record_remix", "get_provenance",
            "quality_leaderboard", "recommended_parent_for_fork",
            "grant_effector_consent", "revoke_effector_consent",
            "list_effector_consents",
            "authoring_start", "authoring_inspect", "authoring_edit",
            "authoring_test", "authoring_confirm_effect", "authoring_publish",
            "authoring_list",
            "handoff_declarations", "handoff_dry_run", "handoff_prepare",
            "handoff_execute", "handoff_get", "handoff_list",
            "handoff_record_evidence", "handoff_attest_outcome",
            "handoff_outcome_evidence",
        ],
    })
