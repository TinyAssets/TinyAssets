"""Paid-market subsystem — extracted from tinyassets/universe_server.py
(Task #14 — decomp Step 7).

Houses the 5 paid-market economy primitives: escrow, outcomes, attribution
chain, goals, and outcome gates. The MCP tool decorations for `goals()` and
`gates()` stay in `tinyassets/universe_server.py` (Pattern A2 from the decomp
plan); plain `goals(...)` and `gates(...)` callables are exposed here, and
the wrappers there delegate to them.

Public surface (back-compat re-exported via ``tinyassets.universe_server``):
    Dispatch tables:
        _ESCROW_ACTIONS / _OUTCOME_ACTIONS / _ATTRIBUTION_ACTIONS
        _GOAL_ACTIONS / _GOAL_WRITE_ACTIONS / _dispatch_goal_action
        _GATES_ACTIONS / _GATE_EVENT_ACTIONS

    Plain MCP-tool callables (Pattern A2):
        goals(action, ...)  / gates(action, ...)

    Escrow handlers:
        _action_escrow_lock / _action_escrow_release / _action_escrow_refund /
        _action_escrow_inspect

    Outcome handlers:
        _action_record_outcome / _action_list_outcomes / _action_get_outcome

    Attribution handlers:
        _action_record_remix / _action_get_provenance

    Goal handlers (10):
        _action_goal_propose / _action_goal_update / _action_goal_bind /
        _action_goal_list / _action_goal_get / _action_goal_search /
        _action_goal_leaderboard / _action_goal_common_nodes /
        _action_goal_archive_consultation / _action_goal_set_canonical

    Gates handlers (10 + 6 gate_event):
        _action_gates_define_ladder / _action_gates_get_ladder /
        _action_gates_claim / _action_gates_claim_from_branch_run /
        _action_gates_retract / _action_gates_list_claims /
        _action_gates_leaderboard / _action_gates_stake_bonus /
        _action_gates_unstake_bonus / _action_gates_release_bonus
        _action_attest_gate_event / _action_verify_gate_event /
        _action_dispute_gate_event / _action_retract_gate_event /
        _action_get_gate_event / _action_list_gate_events

    Internal helper:
        _current_actor_or_anon (goals-internal wrapper around _current_actor)

Cross-module note: ``_append_global_ledger``, ``_truncate``, ``_current_actor``,
``_ensure_workflow_db``, ``_storage_backend``, ``_format_commit_failed``,
``_format_dirty_file_conflict``, ``_filter_claims_by_branch_visibility``,
``_filter_leaderboard_by_branch_visibility``, ``_split_csv`` all live in
their owning API/helper modules and are lazy-imported inside the functions that
use them. Same pattern as Tasks
#11/#12/#13 used for their cross-module symbols.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path
from tinyassets.catalog import CommitFailedError, DirtyFileError

logger = logging.getLogger("universe_server.market")

ENV_CAPABILITIES_VAR = "UNIVERSE_SERVER_CAPABILITIES"


def _current_actor_grants() -> tuple[str, ...]:
    raw = os.environ.get(ENV_CAPABILITIES_VAR, "")
    return tuple(part for part in raw.replace(",", " ").split() if part)


def _current_actor_has_capability(action: str) -> bool:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.auth.provider import resolve_permission

    return resolve_permission(
        actor_id=_current_actor(),
        action=action,
        grants=_current_actor_grants(),
    ).allowed


PATCH_REQUEST_AUTHORITY_BOUNDARY: dict[str, bool] = {
    "affects_pickup_priority": True,
    "affects_acceptance": False,
    "affects_release": False,
    "affects_merge": False,
}
PATCH_REQUEST_PICKUP_SIGNAL_WEIGHT = 5.0
MERGE_INSTANT_PICKUP_SIGNAL_WEIGHT = 5.0
_PATCH_REQUEST_MEANING_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bug", ("bug", "broken", "crash", "error", "fail", "flake", "regression")),
    ("project_design", ("architecture", "design note", "plan.md", "principle")),
    ("docs_ops", ("docs", "documentation", "runbook", "ops", "deploy", "ci")),
    ("feature", ("feature", "add support", "new capability")),
    ("patch", ("patch", "fix", "update", "change", "cleanup")),
)
_REQUEST_TYPE_MEANING: dict[str, str] = {
    "branch_proposal": "branch_refinement",
    "canon_change": "patch",
    "general": "patch",
    "revision": "patch",
    "scene_direction": "branch_refinement",
}
_GHOST_RISK_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "research_prior_art",
        (
            "prior art",
            "paper",
            "benchmark",
            "auc",
            "msr",
            "dataset",
            "classifier",
            "prediction",
            "model",
            "33k",
        ),
    ),
    (
        "review_gate",
        (
            "opposite-family",
            "opposite family",
            "checker",
            "gate requirement",
            "gate ladder",
            "review blocker",
        ),
    ),
    (
        "stall_language",
        (
            "ghost-risk",
            "ghost risk",
            "stall",
            "carrier attention",
            "before stall",
        ),
    ),
)
_MERGE_INSTANT_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "mechanical_shape",
        (
            "mechanical",
            "typo",
            "copy edit",
            "copy-edit",
            "formatting",
            "format-only",
            "rename only",
            "one-line",
        ),
    ),
    (
        "low_runtime_risk",
        (
            "docs only",
            "docs-only",
            "documentation only",
            "no runtime",
            "no behavior change",
            "comment only",
        ),
    ),
    (
        "merge_instant_language",
        (
            "merge-instant",
            "merge instant",
            "safe to merge",
            "trivial patch",
        ),
    ),
)
_GHOST_TAG_CLUSTER: frozenset[str] = frozenset({
    "checker",
    "ghost-risk",
    "ghost risk",
    "opposite-family",
    "opposite family",
    "prior-art",
    "prior art",
    "research",
    "reverted",
    "review-blocked",
    "stuck",
})
_STANDARD_CROSS_REFERENCE_COUNT = 3
_GHOST_LENGTH_RATIO = 3.0
_GHOST_LENGTH_MIN_OBSERVED_CHARS = 240


def classify_patch_request(
    *,
    text: str,
    request_type: str,
    requester_id: str,
    priority_authorized: bool = False,
    directed_daemon: bool = False,
) -> dict[str, Any]:
    """Classify patch-loop intake before daemon implementation work starts."""
    lower_text = text.lower()
    meaning = _REQUEST_TYPE_MEANING.get(request_type, "patch")
    for candidate, keywords in _PATCH_REQUEST_MEANING_KEYWORDS:
        if any(keyword in lower_text for keyword in keywords):
            meaning = candidate
            break

    authority_scope = (
        "operator_priority_allowed" if priority_authorized else "requester_pickup_only"
    )
    if directed_daemon:
        authority_scope = f"{authority_scope}+proposal_only_directed_daemon"

    return {
        "access": {
            "claimable_by": ["free_daemon", "paid_daemon"],
            "code_writer_gate": "claude_or_codex",
            "checker_gate": "opposite_family_checker",
        },
        "meaning": meaning,
        "authority": {
            "scope": authority_scope,
            "boundary": dict(PATCH_REQUEST_AUTHORITY_BOUNDARY),
        },
    }


def classify_filing_effort(
    *,
    title: str,
    component: str = "",
    severity: str = "",
    kind: str = "bug",
    repro: str = "",
    observed: str = "",
    expected: str = "",
    workaround: str = "",
    tags: str = "",
    cross_reference_count: int = 0,
) -> dict[str, Any]:
    """Classify filing attention needs while the wiki entry is created.

    This is a deterministic circuit-breaker, not a merge decision. It gives
    carriers a pickup signal for filings likely to stall despite looking
    mechanical, while keeping merge authority with the normal review gates.
    """
    haystack = " ".join(
        str(part or "")
        for part in (
            title,
            component,
            severity,
            kind,
            repro,
            observed,
            expected,
            workaround,
            tags,
        )
    ).lower()

    def _matched_signals(catalog: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
        return [
            signal
            for signal, keywords in catalog
            if any(keyword in haystack for keyword in keywords)
        ]

    ghost_signals = _matched_signals(_GHOST_RISK_SIGNALS)
    merge_instant_signals = _matched_signals(_MERGE_INSTANT_SIGNALS)

    observed_len = len(observed.strip())
    expected_len = len(expected.strip())
    observed_expected_ratio = (
        round(observed_len / expected_len, 2) if expected_len else None
    )
    tag_tokens = {
        token.strip().lower()
        for token in tags.replace(";", ",").split(",")
        if token.strip()
    }
    ghost_tag_overlap = len(tag_tokens & _GHOST_TAG_CLUSTER)
    structural_features: dict[str, Any] = {
        "cross_reference_count": max(cross_reference_count, 0),
        "observed_expected_length_ratio": observed_expected_ratio,
        "ghost_tag_cluster_overlap": ghost_tag_overlap,
    }

    structural_ghost_signals: list[str] = []
    if (
        observed_expected_ratio is not None
        and observed_expected_ratio >= _GHOST_LENGTH_RATIO
        and observed_len >= _GHOST_LENGTH_MIN_OBSERVED_CHARS
    ):
        structural_ghost_signals.append("observed_expected_length_ratio")
    if ghost_tag_overlap >= 2:
        structural_ghost_signals.append("ghost_tag_cluster_overlap")

    structural_standard_signals: list[str] = []
    if cross_reference_count >= _STANDARD_CROSS_REFERENCE_COUNT:
        structural_standard_signals.append("cross_reference_count")

    if ghost_signals:
        effort_class = "ghost-risk"
        attention = "carrier-review-before-daemon-pickup"
        signals = ghost_signals + structural_ghost_signals + [
            signal
            for signal in merge_instant_signals + structural_standard_signals
            if signal not in ghost_signals and signal not in structural_ghost_signals
        ]
    elif structural_ghost_signals:
        effort_class = "ghost-risk"
        attention = "carrier-review-before-daemon-pickup"
        signals = structural_ghost_signals + [
            signal
            for signal in merge_instant_signals + structural_standard_signals
            if signal not in structural_ghost_signals
        ]
    elif merge_instant_signals and not structural_standard_signals:
        effort_class = "merge-instant"
        attention = "normal-review-gates"
        signals = merge_instant_signals
    else:
        effort_class = "standard"
        attention = "normal-review-gates"
        signals = structural_standard_signals

    return {
        "effort_class": effort_class,
        "attention": attention,
        "signals": signals,
        "confidence": "heuristic",
        "combiner": "rule_based",
        "structural_features": structural_features,
        "authority_boundary": dict(PATCH_REQUEST_AUTHORITY_BOUNDARY),
    }


def filing_effort_dispatch_route(
    effort_classification: dict[str, Any] | None,
) -> dict[str, Any]:
    """Translate filing effort metadata into dispatcher-visible routing hints."""
    effort_class = str(
        (effort_classification or {}).get("effort_class") or "standard"
    )
    attention = str(
        (effort_classification or {}).get("attention") or "normal-review-gates"
    )
    if effort_class == "merge-instant":
        return {
            "lane": "merge-instant-fast-lane",
            "pickup_signal_weight": MERGE_INSTANT_PICKUP_SIGNAL_WEIGHT,
            "triage_policy": "skip-extended-triage-when-no-ghost-signals",
            "visible_reason": "low-risk mechanical filing",
        }
    if effort_class == "ghost-risk":
        return {
            "lane": "carrier-attention",
            "pickup_signal_weight": 0.0,
            "triage_policy": "notify-carrier-before-daemon-pickup",
            "attention_family": "opposite-family-checker",
            "visible_reason": attention,
        }
    return {
        "lane": "standard-triage",
        "pickup_signal_weight": 0.0,
        "triage_policy": "normal-review-gates",
        "visible_reason": attention,
    }


def normalize_patch_request_incentive(
    terms: str,
    *,
    requester_id: str,
    visibility: str = "public",
) -> dict[str, Any]:
    """Return bounded pickup-only incentive metadata for a patch request."""
    clean_terms = terms.strip()
    if not clean_terms:
        return {
            "enabled": False,
            "terms": "",
            "visibility": visibility,
            "requester_id": requester_id,
            "pickup_signal_weight": 0.0,
        }
    return {
        "enabled": True,
        "terms": clean_terms,
        "visibility": visibility,
        "requester_id": requester_id,
        "pickup_signal_weight": PATCH_REQUEST_PICKUP_SIGNAL_WEIGHT,
        "authority_boundary": dict(PATCH_REQUEST_AUTHORITY_BOUNDARY),
    }


# ── Escrow MCP handlers ────────────────────────────────────────────────────────


def _escrow_host_user() -> str:
    """The host identity allowed to act on behalf of other actors' escrow."""
    return (os.environ.get("UNIVERSE_SERVER_HOST_USER") or "host").strip()


def _resolve_escrow_actor(
    kwargs: dict[str, Any], *, payload_key: str = "staker_id"
) -> tuple[str | None, str | None]:
    """Resolve the actor a money-escrow action operates on, from auth context.

    Financial-integrity rule (slice1a review CRITICAL 1): money actions
    (fund / set_wallet / withdraw) act on the AUTHENTICATED actor, never a
    caller-supplied identity, so a caller cannot fund/withdraw/redirect another
    actor's escrow. A caller-supplied ``staker_id`` is only honored when it
    matches the authenticated actor, or when the authenticated actor is the
    configured host (``UNIVERSE_SERVER_HOST_USER``) acting on behalf of another.

    Returns ``(actor, None)`` on success, or ``(None, error_message)`` when a
    cross-actor attempt is rejected.
    """
    from tinyassets.api.engine_helpers import _current_actor

    authed = (_current_actor() or "").strip()
    requested = (kwargs.get(payload_key) or "").strip()

    if not requested or requested == authed:
        return (authed, None)

    if authed == _escrow_host_user():
        # Host may act on behalf of another actor's escrow explicitly.
        return (requested, None)

    return (
        None,
        (
            f"Cross-actor escrow is not permitted: authenticated actor "
            f"{authed!r} cannot act on {payload_key}={requested!r}. Money "
            f"actions operate on your own escrow only."
        ),
    )


def _action_escrow_lock(kwargs: dict[str, Any]) -> str:
    """Lock escrow for a node request. Requires TINYASSETS_PAID_MARKET=on."""
    from tinyassets.payments.actions import action_escrow_lock
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    from tinyassets.api.engine_helpers import _current_actor

    node_id = (kwargs.get("node_id") or "").strip()
    # Lock reserves the caller's OWN funded budget — always the authenticated
    # actor, never a caller-supplied identity (slice1a review CRITICAL 1).
    claimer = _current_actor().strip()
    currency = (kwargs.get("currency") or "MicroToken").strip()
    raw_amount = kwargs.get("amount", 0)
    try:
        amount = int(raw_amount)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": f"amount must be an integer, got {raw_amount!r}.",
        })

    with _connect(_base_path()) as conn:
        result = action_escrow_lock(
            conn,
            node_id=node_id,
            amount=amount,
            claimer=claimer,
            currency=currency,
        )
    return json.dumps(result)


def _action_escrow_release(kwargs: dict[str, Any]) -> str:
    """Release locked escrow to a recipient on completion verdict."""
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.payments.actions import action_escrow_release
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    lock_id = (kwargs.get("lock_id") or "").strip()
    recipient_id = (kwargs.get("recipient_id") or "").strip()
    evidence = (kwargs.get("evidence") or "").strip()
    # Only the lock's staker (or host) may release it — pass the authenticated
    # actor so action_escrow_release can authorize ownership (CRITICAL 1).
    caller_id = _current_actor().strip()

    if not recipient_id:
        return json.dumps({
            "status": "rejected",
            "error": "recipient_id is required for escrow_release.",
        })

    with _connect(_base_path()) as conn:
        result = action_escrow_release(
            conn,
            lock_id=lock_id,
            recipient_id=recipient_id,
            evidence=evidence,
            caller_id=caller_id,
            host_id=_escrow_host_user(),
        )
    return json.dumps(result)


def _action_escrow_refund(kwargs: dict[str, Any]) -> str:
    """Refund locked escrow to staker on abandonment or rejection."""
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.payments.actions import action_escrow_refund
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    lock_id = (kwargs.get("lock_id") or "").strip()
    reason = (kwargs.get("reason") or "").strip()
    # Only the lock's staker (or host) may refund it — pass the authenticated
    # actor so action_escrow_refund can authorize ownership (CRITICAL round 2).
    # A write-scoped caller cannot cancel another actor's escrow by lock_id.
    caller_id = _current_actor().strip()

    with _connect(_base_path()) as conn:
        result = action_escrow_refund(
            conn,
            lock_id=lock_id,
            reason=reason,
            caller_id=caller_id,
            host_id=_escrow_host_user(),
        )
    return json.dumps(result)


def _action_escrow_inspect(kwargs: dict[str, Any]) -> str:
    """Read-only inspection of escrow lock(s). No paid-market gate."""
    from tinyassets.payments.actions import action_escrow_inspect
    from tinyassets.storage import _connect

    node_id = (kwargs.get("node_id") or "").strip()
    lock_id = (kwargs.get("lock_id") or "").strip()

    with _connect(_base_path()) as conn:
        result = action_escrow_inspect(conn, node_id=node_id, lock_id=lock_id)
    return json.dumps(result)


def _action_escrow_fund(kwargs: dict[str, Any]) -> str:
    """Credit a staker's escrow budget (off-chain / testnet faucet).

    Requires TINYASSETS_PAID_MARKET=on. Funds the authenticated actor's own
    budget; a supplied staker_id is honored only when it is the authenticated
    actor (or the host acting on behalf of another).
    """
    from tinyassets.payments.actions import action_escrow_fund
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    staker_id, err = _resolve_escrow_actor(kwargs, payload_key="staker_id")
    if err is not None:
        return json.dumps({"status": "rejected", "error": err})
    currency = (kwargs.get("currency") or "MicroToken").strip()
    raw_amount = kwargs.get("amount", 0)
    try:
        amount = int(raw_amount)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": f"amount must be an integer, got {raw_amount!r}.",
        })

    with _connect(_base_path()) as conn:
        result = action_escrow_fund(
            conn, staker_id=staker_id, amount=amount, currency=currency
        )
    return json.dumps(result)


def _action_escrow_balance(kwargs: dict[str, Any]) -> str:
    """Read-only — a staker's escrow budget (total / reserved / spendable)."""
    # A balance is read FOR a principal. Falling back to an env var read one
    # stranger's escrow for another (and, unset, read an unowned bucket).
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.payments.actions import action_escrow_balance
    from tinyassets.principals import named_principal
    from tinyassets.storage import _connect

    staker_id = named_principal(kwargs.get("staker_id")) or _current_actor()
    currency = (kwargs.get("currency") or "MicroToken").strip()

    with _connect(_base_path()) as conn:
        result = action_escrow_balance(conn, staker_id=staker_id, currency=currency)
    return json.dumps(result)


def _action_escrow_set_wallet(kwargs: dict[str, Any]) -> str:
    """Register an actor's on-chain payout address (where withdrawals land).

    Requires TINYASSETS_PAID_MARKET=on. Operates on the authenticated actor; a
    supplied staker_id is honored only when it is the authenticated actor (or
    the host acting on behalf of another) — a caller cannot redirect another
    actor's payout address (slice1a review CRITICAL 1).
    """
    from tinyassets.payments.actions import action_escrow_set_wallet
    from tinyassets.payments.settlement_backend import BASE_SEPOLIA_CHAIN_ID
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    actor_id, err = _resolve_escrow_actor(kwargs, payload_key="staker_id")
    if err is not None:
        return json.dumps({"status": "rejected", "error": err})
    address = (kwargs.get("wallet_address") or "").strip()
    raw_chain = kwargs.get("chain_id") or BASE_SEPOLIA_CHAIN_ID
    try:
        chain_id = int(raw_chain)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": f"chain_id must be an integer, got {raw_chain!r}.",
        })

    with _connect(_base_path()) as conn:
        result = action_escrow_set_wallet(
            conn, actor_id=actor_id, address=address, chain_id=chain_id
        )
    return json.dumps(result)


def _action_escrow_withdraw(kwargs: dict[str, Any]) -> str:
    """Withdraw spendable balance to the actor's payout wallet via the
    configured settlement backend. Requires TINYASSETS_PAID_MARKET=on."""
    from tinyassets.payments.actions import action_escrow_withdraw
    from tinyassets.payments.settlement_backend import BASE_SEPOLIA_CHAIN_ID
    from tinyassets.producers.node_bid import paid_market_enabled
    from tinyassets.storage import _connect

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Escrow actions require TINYASSETS_PAID_MARKET=on.",
        })

    # Withdraw moves funds OUT — only against the authenticated actor's own
    # balance/wallet (slice1a review CRITICAL 1).
    actor_id, err = _resolve_escrow_actor(kwargs, payload_key="staker_id")
    if err is not None:
        return json.dumps({"status": "rejected", "error": err})
    currency = (kwargs.get("currency") or "MicroToken").strip()
    raw_amount = kwargs.get("amount", 0)
    raw_chain = kwargs.get("chain_id") or BASE_SEPOLIA_CHAIN_ID
    try:
        amount = int(raw_amount)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": f"amount must be an integer, got {raw_amount!r}.",
        })
    try:
        chain_id = int(raw_chain)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": f"chain_id must be an integer, got {raw_chain!r}.",
        })
    # Optional client-supplied idempotency key — a retry MUST reuse the same key
    # so the withdrawal is not paid out twice (slice1a review HIGH 4).
    idempotency_key = (kwargs.get("idempotency_key") or "").strip() or None

    with _connect(_base_path()) as conn:
        result = action_escrow_withdraw(
            conn, actor_id=actor_id, amount=amount, currency=currency,
            chain_id=chain_id, idempotency_key=idempotency_key,
        )
    return json.dumps(result)


_ESCROW_ACTIONS: dict[str, Any] = {
    "escrow_lock": _action_escrow_lock,
    "escrow_release": _action_escrow_release,
    "escrow_refund": _action_escrow_refund,
    "escrow_inspect": _action_escrow_inspect,
    "escrow_fund": _action_escrow_fund,
    "escrow_balance": _action_escrow_balance,
    "escrow_set_wallet": _action_escrow_set_wallet,
    "escrow_withdraw": _action_escrow_withdraw,
}

# ── Outcome event MCP actions ─────────────────────────────────────────────


def _outcome_db_path(base_path: "Path") -> "Path":
    return base_path / ".runs.db"


def _outcome_connect(base_path: "Path") -> Any:
    import sqlite3 as _sqlite3

    from tinyassets.outcomes.schema import migrate_outcome_schema
    db = _outcome_db_path(base_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    migrate_outcome_schema(conn)
    conn.commit()
    return conn


def _outcome_row_to_dict(
    row: Any,
    *,
    evidence: Any | None = None,
    transitions: list[Any] | None = None,
) -> dict:
    result = {
        "outcome_id": row["outcome_id"],
        "run_id": row["run_id"],
        "outcome_type": row["outcome_type"],
        "evidence_url": row["evidence_url"],
        "verified_at": row["verified_at"],
        "verified_by": row["verified_by"],
        "claim_run_id": row["claim_run_id"],
        "payload": json.loads(row["payload"] or "{}"),
        "recorded_at": row["recorded_at"],
        "note": row["note"] or "",
    }
    if evidence is not None:
        result.update({
            "account_id": evidence["account_id"],
            "branch_def_id": evidence["branch_def_id"],
            "branch_version_id": evidence["branch_version_id"],
            "content_hash": evidence["content_hash"],
            "output_field": evidence["output_field"],
            "output_sha256": evidence["output_sha256"],
            "handoff_id": evidence["handoff_id"],
            "effect_key": evidence["effect_key"],
            "sink": evidence["sink"],
            "evidence_source": evidence["evidence_source"],
            "evidence_level": evidence["evidence_level"],
            "external_id": evidence["external_id"],
            "normalized_external_ref": evidence["normalized_external_ref"],
            "attested_by": evidence["attested_by"],
            "evidence_updated_at": evidence["updated_at"],
            "evidence_transitions": [
                {
                    "seq": item["seq"],
                    "from_level": item["from_level"],
                    "to_level": item["to_level"],
                    "evidence_source": item["evidence_source"],
                    "actor_id": item["actor_id"],
                    "evidence": json.loads(item["evidence_json"] or "{}"),
                    "recorded_at": item["recorded_at"],
                }
                for item in (transitions or [])
            ],
        })
    return result


def _outcome_lifecycle(conn: Any, outcome_id: str) -> tuple[Any | None, list[Any]]:
    evidence = conn.execute(
        "SELECT * FROM outcome_evidence WHERE outcome_id = ?",
        (outcome_id,),
    ).fetchone()
    transitions = conn.execute(
        """
        SELECT *
          FROM outcome_evidence_transition
         WHERE outcome_id = ?
         ORDER BY seq
        """,
        (outcome_id,),
    ).fetchall()
    return evidence, transitions


def _outcome_run_owner(run: dict[str, Any]) -> str:
    owner = str(run.get("owner_user_id") or "").strip()
    if owner:
        return owner
    from tinyassets.principals import named_principal

    return named_principal(run.get("actor"))


def _action_record_outcome(kwargs: dict[str, Any]) -> str:
    import uuid as _uuid
    from datetime import datetime, timezone

    from tinyassets.outcomes.schema import (
        OUTCOME_TYPES,
        record_user_attested_outcome_evidence,
    )
    from tinyassets.principals import named_principal
    from tinyassets.runs import get_run

    run_id = (kwargs.get("run_id") or "").strip()
    actor_id = named_principal(kwargs.get("actor_id"))
    outcome_type = (kwargs.get("outcome_type") or "").strip()
    if not run_id:
        return json.dumps({"error": "run_id is required."})
    if not outcome_type:
        return json.dumps({"error": "outcome_type is required."})
    if outcome_type not in OUTCOME_TYPES:
        return json.dumps({
            "error": f"Unknown outcome_type '{outcome_type}'.",
            "valid": sorted(OUTCOME_TYPES),
        })
    source_run = get_run(_base_path(), run_id)
    if (
        not actor_id
        or source_run is None
        or _outcome_run_owner(source_run) != actor_id
    ):
        return json.dumps({
            "error": f"run {run_id!r} is not available to this account",
            "code": "handoff_authority_required",
        })
    evidence_url = (kwargs.get("evidence_url") or "").strip() or None
    gate_event_id = (kwargs.get("gate_event_id") or "").strip() or None
    note = (kwargs.get("note") or "").strip()
    raw_payload = kwargs.get("payload_json") or "{}"
    try:
        payload = json.dumps(
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
    except (json.JSONDecodeError, TypeError):
        payload = "{}"
    outcome_id = str(_uuid.uuid4())
    recorded_at = datetime.now(timezone.utc).isoformat()
    base = _base_path()
    with _outcome_connect(base) as conn:
        conn.execute(
            """
            INSERT INTO outcome_event
                (outcome_id, run_id, outcome_type, evidence_url,
                 claim_run_id, payload, recorded_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, run_id, outcome_type, evidence_url,
             gate_event_id, payload, recorded_at, note),
        )
        record_user_attested_outcome_evidence(
            conn,
            outcome_id=outcome_id,
            account_id=actor_id,
            actor_id=actor_id,
        )
    return json.dumps({
        "status": "recorded",
        "outcome_id": outcome_id,
        "run_id": run_id,
        "outcome_type": outcome_type,
        "recorded_at": recorded_at,
    })


def _action_list_outcomes(kwargs: dict[str, Any]) -> str:
    branch_def_id = (kwargs.get("branch_def_id") or "").strip()
    run_id = (kwargs.get("run_id") or "").strip()
    outcome_type = (kwargs.get("outcome_type") or "").strip()
    try:
        limit = max(1, min(int(kwargs.get("limit") or 50), 200))
    except (TypeError, ValueError):
        limit = 50

    base = _base_path()

    # If filtering by branch_def_id, resolve matching run_ids first.
    if branch_def_id and not run_id:
        try:
            from tinyassets.runs import initialize_runs_db, query_runs
            initialize_runs_db(base)
            runs = query_runs(base, branch_def_id=branch_def_id, limit=1000)
            run_ids = [r["run_id"] for r in runs]
            if not run_ids:
                return json.dumps({"outcomes": [], "count": 0})
        except Exception:
            return json.dumps({"outcomes": [], "count": 0})
    else:
        run_ids = [run_id] if run_id else []

    with _outcome_connect(base) as conn:
        clauses: list[str] = []
        params: list[Any] = []
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            clauses.append(f"run_id IN ({placeholders})")
            params.extend(run_ids)
        if outcome_type:
            clauses.append("outcome_type = ?")
            params.append(outcome_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM outcome_event {where} ORDER BY recorded_at DESC LIMIT ?",
            params,
        ).fetchall()
        outcomes = []
        for row in rows:
            evidence, transitions = _outcome_lifecycle(conn, row["outcome_id"])
            outcomes.append(
                _outcome_row_to_dict(
                    row,
                    evidence=evidence,
                    transitions=transitions,
                )
            )
    return json.dumps({"outcomes": outcomes, "count": len(outcomes)})


def _action_get_outcome(kwargs: dict[str, Any]) -> str:
    outcome_id = (kwargs.get("outcome_id") or "").strip()
    if not outcome_id:
        return json.dumps({"error": "outcome_id is required."})
    base = _base_path()
    with _outcome_connect(base) as conn:
        row = conn.execute(
            "SELECT * FROM outcome_event WHERE outcome_id = ?",
            (outcome_id,),
        ).fetchone()
        evidence, transitions = (
            _outcome_lifecycle(conn, outcome_id)
            if row is not None
            else (None, [])
        )
    if row is None:
        return json.dumps({"error": f"outcome_id '{outcome_id}' not found."})
    return json.dumps(
        _outcome_row_to_dict(row, evidence=evidence, transitions=transitions)
    )


_OUTCOME_ACTIONS: dict[str, Any] = {
    "record_outcome": _action_record_outcome,
    "list_outcomes": _action_list_outcomes,
    "get_outcome": _action_get_outcome,
}

# ── Attribution chain ──────────────────────────────────────────────────────

def _attribution_connect(base_path: "Path") -> Any:
    import sqlite3 as _sqlite3

    from tinyassets.attribution.schema import migrate_attribution_schema

    db = base_path / ".runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    migrate_attribution_schema(conn)
    conn.commit()
    return conn


_VALID_CONTRIBUTION_KINDS = frozenset({"original", "remix", "patch", "template"})


def _action_record_remix(kwargs: dict[str, Any]) -> str:
    import uuid as _uuid
    from datetime import datetime, timezone

    parent_id = (kwargs.get("parent_branch_def_id") or "").strip()
    child_id = (kwargs.get("child_branch_def_id") or "").strip()
    if not parent_id:
        return json.dumps({"error": "parent_branch_def_id is required."})
    if not child_id:
        return json.dumps({"error": "child_branch_def_id is required."})
    if parent_id == child_id:
        return json.dumps({"error": "parent_branch_def_id and child_branch_def_id must differ."})

    contribution_kind = (kwargs.get("contribution_kind") or "remix").strip()
    if contribution_kind not in _VALID_CONTRIBUTION_KINDS:
        return json.dumps({
            "error": f"Unknown contribution_kind '{contribution_kind}'.",
            "valid": sorted(_VALID_CONTRIBUTION_KINDS),
        })

    try:
        credit_share = float(kwargs.get("credit_share") or 0.0)
    except (TypeError, ValueError):
        credit_share = 0.0
    credit_share = max(0.0, min(1.0, credit_share))

    from tinyassets.principals import named_principal

    actor_id = named_principal(kwargs.get("actor_id"))
    owner_user_id = (kwargs.get("owner_user_id") or "").strip()
    daemon_id = (kwargs.get("daemon_id") or "").strip()
    runtime_instance_id = (kwargs.get("runtime_instance_id") or "").strip()
    worker_id = (kwargs.get("worker_id") or "").strip()
    base = _base_path()

    with _attribution_connect(base) as conn:
        # Cycle guard: reject if child already appears as an ancestor of parent.
        # Walk attribution_edge parents of parent_id up to 50 hops.
        ancestors: set[str] = set()
        frontier = [parent_id]
        for _ in range(50):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT parent_id FROM attribution_edge WHERE child_id IN ({placeholders})",
                frontier,
            ).fetchall()
            frontier = []
            for r in rows:
                pid = r["parent_id"]
                if pid not in ancestors:
                    ancestors.add(pid)
                    frontier.append(pid)
        if child_id in ancestors or child_id == parent_id:
            return json.dumps({
                "error": "Cycle detected: child is already an ancestor of parent."
            })

        # Compute generation_depth = max depth of parent + 1 (minimum 1).
        row = conn.execute(
            "SELECT MAX(generation_depth) AS d FROM attribution_edge WHERE child_id = ?",
            (parent_id,),
        ).fetchone()
        parent_depth = int(row["d"] or 0) if row and row["d"] is not None else 0
        generation_depth = parent_depth + 1

        edge_id = str(_uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """
                INSERT INTO attribution_edge
                    (edge_id, parent_id, child_id, parent_kind, child_kind,
                     generation_depth, contribution_kind, created_at)
                VALUES (?, ?, ?, 'branch', 'branch', ?, ?, ?)
                """,
                (edge_id, parent_id, child_id, generation_depth, contribution_kind, created_at),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return json.dumps({"error": f"Edge {parent_id} → {child_id} already exists."})
            raise

        # Record credit for the remixing actor on the child artifact.
        if credit_share > 0.0:
            credit_id = str(_uuid.uuid4())
            conn.execute(
                """
                INSERT OR IGNORE INTO attribution_credit
                    (credit_id, artifact_id, artifact_kind, actor_id,
                     owner_user_id, daemon_id, runtime_instance_id, worker_id,
                     credit_share, royalty_share, generation_depth,
                     contribution_kind, recorded_at)
                VALUES (?, ?, 'branch', ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?)
                """,
                (
                    credit_id, child_id, actor_id, owner_user_id, daemon_id,
                    runtime_instance_id, worker_id, credit_share,
                    generation_depth, contribution_kind, created_at,
                ),
            )

    return json.dumps({
        "status": "recorded",
        "edge_id": edge_id,
        "parent_branch_def_id": parent_id,
        "child_branch_def_id": child_id,
        "contribution_kind": contribution_kind,
        "generation_depth": generation_depth,
        "credit_share": credit_share,
        "actor_id": actor_id,
        "created_at": created_at,
    })


def _action_get_provenance(kwargs: dict[str, Any]) -> str:
    child_id = (kwargs.get("child_branch_def_id") or "").strip()
    if not child_id:
        return json.dumps({"error": "child_branch_def_id is required."})
    try:
        max_depth = max(1, min(50, int(kwargs.get("max_depth") or 10)))
    except (TypeError, ValueError):
        max_depth = 10

    base = _base_path()
    chain: list[dict[str, Any]] = []

    with _attribution_connect(base) as conn:
        frontier = [child_id]
        visited: set[str] = {child_id}
        depth = 0
        while frontier and depth < max_depth:
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"""
                SELECT e.parent_id, e.child_id, e.generation_depth,
                       e.contribution_kind, e.edge_id, e.created_at,
                       c.actor_id, c.credit_share
                FROM attribution_edge e
                LEFT JOIN attribution_credit c
                    ON c.artifact_id = e.child_id AND c.artifact_kind = 'branch'
                WHERE e.child_id IN ({placeholders})
                ORDER BY e.generation_depth ASC
                """,
                frontier,
            ).fetchall()
            next_frontier: list[str] = []
            for r in rows:
                chain.append({
                    "edge_id": r["edge_id"],
                    "parent_branch_def_id": r["parent_id"],
                    "child_branch_def_id": r["child_id"],
                    "generation_depth": r["generation_depth"],
                    "contribution_kind": r["contribution_kind"],
                    "actor_id": r["actor_id"],
                    "credit_share": r["credit_share"],
                    "created_at": r["created_at"],
                })
                pid = r["parent_id"]
                if pid not in visited:
                    visited.add(pid)
                    next_frontier.append(pid)
            frontier = next_frontier
            depth += 1

    return json.dumps({
        "child_branch_def_id": child_id,
        "chain": chain,
        "count": len(chain),
    })


_ATTRIBUTION_ACTIONS: dict[str, Any] = {
    "record_remix": _action_record_remix,
    "get_provenance": _action_get_provenance,
}

# ═══════════════════════════════════════════════════════════════════════════
# TOOL 3 — Goals (first-class shared primitive above Branches)
# ═══════════════════════════════════════════════════════════════════════════
# Phase 5 per docs/specs/community_branches_phase5.md. A Goal is the
# intent a Branch serves — "produce a research paper", "plan a
# wedding". Many Branches bind to one Goal. 8 actions: propose,
# update, bind, list, get, search, leaderboard, common_nodes,
# archive_consultation.
# Storage in tinyassets/author_server.py.


def _current_actor_or_anon() -> str:
    """Wrapper around the existing ``_current_actor`` for clarity in
    the Goals surface. Identical behavior."""
    from tinyassets.api.engine_helpers import _current_actor

    return _current_actor()


def _format_goal_catalog_line(g: dict[str, Any]) -> str:
    tags = g.get("tags") or []
    tag_suffix = f" [{', '.join(tags[:3])}]" if tags else ""
    desc = (g.get("description") or "").strip().split("\n", 1)[0]
    if len(desc) > 100:
        desc = desc[:100].rstrip() + "…"
    name = g.get("name") or "(unnamed)"
    return (
        f"- `{g['goal_id']}` · **{name}** · {g.get('author')}"
        f"{tag_suffix}" + (f" · {desc}" if desc else "")
    )


_PRODUCTION_ONLY_EXCLUDED_TAGS = {"retracted", "smoke", "disposable"}
_PRODUCTION_ONLY_EXCLUDED_TEXT = ("retracted", "smoke", "disposable")


def _truthy_tool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_production_goal(g: dict[str, Any]) -> bool:
    if (g.get("visibility") or "public") != "public":
        return False
    tags = {str(tag).strip().lower() for tag in (g.get("tags") or [])}
    if tags & _PRODUCTION_ONLY_EXCLUDED_TAGS:
        return False
    text = " ".join([
        str(g.get("name") or ""),
        str(g.get("description") or ""),
    ]).lower()
    return not any(marker in text for marker in _PRODUCTION_ONLY_EXCLUDED_TEXT)


def _action_goal_propose(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import (
        _ensure_workflow_db,
        _split_csv,
    )
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.identity import git_author

    name = (kwargs.get("name") or "").strip()
    if not name:
        return json.dumps({
            "status": "rejected",
            "error": "name is required for propose.",
        })
    _ensure_workflow_db()
    tags = _split_csv(kwargs.get("tags", ""))
    visibility = (kwargs.get("visibility") or "public").strip().lower()
    if visibility not in {"public", "private"}:
        return json.dumps({
            "status": "rejected",
            "error": (
                "visibility must be 'public' or 'private' at propose "
                "time. Goal soft-deletion is not exposed by the advertised "
                "handles."
            ),
        })
    goal_dict = {
        "name": name,
        "description": kwargs.get("description", ""),
        "author": _current_actor_or_anon(),
        "tags": tags,
        "visibility": visibility,
    }
    try:
        saved, _commit = _storage_backend().save_goal_and_commit(
            goal_dict,
            author=git_author(_current_actor()),
            message=f"goals.propose: {name}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    text = (
        f"**Proposed Goal: {saved['name']}.**\n\n"
        "Goal binding is not exposed by the advertised handles. Search for "
        'related intent with read_graph target="goals" query="<terms>".'
    )
    return json.dumps({
        "text": text,
        "status": "proposed",
        "goal": saved,
    }, default=str)


def _action_goal_update(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import (
        _ensure_workflow_db,
        _split_csv,
    )
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.daemon_server import get_goal
    from tinyassets.daemon_server import update_goal as _update
    from tinyassets.identity import git_author

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    _ensure_workflow_db()
    try:
        current = get_goal(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    # Soft ownership: author-only for update. Host is ambient so future
    # host-admin override can be bolted on without schema change.
    actor = _current_actor_or_anon()
    if current["author"] != actor and actor != "host":
        return json.dumps({
            "status": "rejected",
            "error": (
                f"Goal '{gid}' is owned by '{current['author']}'. "
                "Only the author can update it (Phase 5 default). "
                "Propose a new Goal to capture your variant."
            ),
        })

    updates: dict[str, Any] = {}
    for fld in ("name", "description"):
        if kwargs.get(fld):
            updates[fld] = kwargs[fld]
    if kwargs.get("tags"):
        updates["tags"] = _split_csv(kwargs["tags"])
    if kwargs.get("visibility"):
        vis = (kwargs["visibility"] or "").strip().lower()
        if vis not in {"public", "private", "deleted"}:
            return json.dumps({
                "status": "rejected",
                "error": (
                    "visibility must be 'public', 'private', or "
                    "'deleted'."
                ),
            })
        updates["visibility"] = vis
    if not updates:
        return json.dumps({
            "status": "rejected",
            "error": (
                "No fields to update. Pass one or more of name, "
                "description, tags, visibility."
            ),
        })

    # Apply the SQLite update (owns column-level merging + validation).
    # Then route the resulting full goal dict through the cached backend
    # so the YAML mirror refreshes + single commit lands.
    updated = _update(_base_path(), goal_id=gid, updates=updates)
    try:
        saved, _commit = _storage_backend().save_goal_and_commit(
            updated,
            author=git_author(_current_actor()),
            message=f"goals.update: {gid}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    changed = sorted(updates.keys())
    text = (
        f"**Updated Goal '{saved['name']}'.** Fields changed: "
        f"{', '.join(changed)}."
    )
    return json.dumps({
        "text": text,
        "status": "updated",
        "changed_fields": changed,
        "goal": saved,
    }, default=str)


def _action_goal_bind(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.daemon_server import (
        get_branch_definition,
        get_goal,
        update_branch_definition,
    )

    bid = (kwargs.get("branch_def_id") or "").strip()
    gid = (kwargs.get("goal_id") or "").strip()
    if not bid:
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id is required.",
        })
    _ensure_workflow_db()
    try:
        branch = get_branch_definition(_base_path(), branch_def_id=bid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Branch '{bid}' not found.",
        })

    if gid:
        try:
            goal = get_goal(_base_path(), goal_id=gid)
        except KeyError:
            return json.dumps({
                "status": "rejected",
                "error": f"Goal '{gid}' not found.",
            })
        if goal.get("visibility") == "deleted":
            return json.dumps({
                "status": "rejected",
                "error": (
                    f"Goal '{gid}' is soft-deleted. Propose a new Goal "
                    "or re-open the deleted one first."
                ),
            })
    else:
        goal = None

    from tinyassets.branches import BranchDefinition
    from tinyassets.identity import git_author

    update_branch_definition(
        _base_path(), branch_def_id=bid,
        updates={"goal_id": gid or None},
    )
    # Re-read post-update and route through cached backend so the YAML
    # mirror + single commit capture the cross-table edit. Commit targets
    # the branch path only (goal row isn't mutated by bind).
    updated_branch = get_branch_definition(_base_path(), branch_def_id=bid)
    branch_obj = BranchDefinition.from_dict(updated_branch)
    if gid and goal is not None:
        commit_msg = f"goals.bind: {branch['name']} → {goal['name']}"
    else:
        commit_msg = f"goals.bind: {branch['name']} ∅ (unbind)"
    try:
        _storage_backend().save_branch_and_commit(
            branch_obj,
            author=git_author(_current_actor()),
            message=commit_msg,
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    if gid:
        # `text` is the phone/chat channel and must never carry a raw id
        # (task #58) -- `goal_id` is kept below in the structured fields,
        # which is where a caller that needs to look the Goal back up reads
        # it from.
        text = (
            f"**Bound** workflow '{branch['name']}' to "
            f"Goal '{goal['name']}'."
        )
        status = "bound"
    else:
        text = (
            f"**Unbound** workflow '{branch['name']}' from its "
            "previous Goal."
        )
        status = "unbound"
    return json.dumps({
        "text": text,
        "status": status,
        "branch_def_id": bid,
        "goal_id": gid or None,
    }, default=str)


def _action_goal_list(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import (
        _ensure_workflow_db,
        _split_csv,
    )
    from tinyassets.daemon_server import list_goals

    _ensure_workflow_db()
    requested_limit = max(1, int(kwargs.get("limit", 50) or 50))
    production_only = _truthy_tool_arg(kwargs.get("production_only", False))
    fetch_limit = max(requested_limit * 10, 100) if production_only else requested_limit
    rows = list_goals(
        _base_path(),
        author=kwargs.get("author", ""),
        tag=(_split_csv(kwargs.get("tags", ""))[:1] or [""])[0],
        limit=fetch_limit,
    )
    unfiltered_count = len(rows)
    if production_only:
        rows = [g for g in rows if _is_production_goal(g)][:requested_limit]
    if rows:
        label = "production Goal(s)" if production_only else "Goal(s)"
        lines = [f"**{len(rows)} {label}:**", ""]
        for g in rows[:12]:
            lines.append(_format_goal_catalog_line(g))
        if len(rows) > 12:
            lines.append(
                f"- … and {len(rows) - 12} more. Narrow with "
                "`tags=...` or `author=...`."
            )
        text = "\n".join(lines)
    else:
        text = (
            "No Goals match the filter yet. Propose one with "
            'write_graph target="goal" name="<name>".'
        )
    return json.dumps({
        "text": text,
        "goals": rows,
        "count": len(rows),
        "production_only": production_only,
        "excluded_count": unfiltered_count - len(rows),
    }, default=str)


def _get_public_goal(goal_id: str) -> dict[str, Any]:
    """Resolve a Goal for a public read action, failing closed otherwise."""
    from tinyassets.daemon_server import get_goal

    goal = get_goal(_base_path(), goal_id=goal_id)
    if goal.get("visibility") != "public":
        raise KeyError(goal_id)
    return goal


def _public_goal_ids() -> set[str]:
    """Return the complete exactly-public Goal ID set for record filtering."""
    from tinyassets.daemon_server import public_goal_ids

    return public_goal_ids(_base_path())


def _action_goal_get(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
    )
    from tinyassets.api.market import _gates_enabled
    from tinyassets.daemon_server import (
        branches_for_goal,
        goal_gate_summary,
        resolve_goal_canonical,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    _ensure_workflow_db()
    try:
        goal = _get_public_goal(gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    # Phase 6.2.2 — viewer-aware. Private Branches owned by other
    # actors are excluded from this Goal's published Branch list.
    actor = _current_actor()
    scope_actor = actor
    branches = branches_for_goal(
        _base_path(), goal_id=gid, viewer=actor,
    )
    actor_canonical_branch_version_id = resolve_goal_canonical(
        _base_path(),
        goal_id=gid,
        scope_actor=scope_actor,
    )
    # Phase 6.4: gate_summary rides alongside branches.
    # When GATES_ENABLED=0, return a flag-gated placeholder so the
    # UI can render "gates off" without mistaking it for "no claims".
    if _gates_enabled():
        gate_summary = goal_gate_summary(_base_path(), goal_id=gid)
    else:
        gate_summary = {"status": "gates_disabled"}
    protocol = list(goal.get("branch_protocol") or [])
    current_protocol_step = None
    if protocol:
        from tinyassets.daemon_server import current_goal_protocol_step
        current_protocol_step = current_goal_protocol_step(protocol)

    lines = [
        f"**Goal: {goal['name']}**",
        f"Author: {goal['author']}   Visibility: {goal['visibility']}",
    ]
    if goal.get("tags"):
        lines.append(f"Tags: {', '.join(goal['tags'])}")
    if goal.get("description"):
        lines.append("")
        lines.append(goal["description"])
    lines.append("")
    if protocol:
        lines.append(f"**Branch protocol** ({len(protocol)} step(s)):")
        for step in protocol[:12]:
            marker = " ← current" if step == current_protocol_step else ""
            lines.append(
                f"- {step.get('order')}. `{step.get('branch_def_id')}` · "
                f"{step.get('source_label') or step.get('step_id')}"
                f" · {step.get('status', 'pending')}{marker}"
            )
        if len(protocol) > 12:
            lines.append(
                f"- … and {len(protocol) - 12} more in the structured result."
            )
        lines.append("")
    if branches:
        lines.append(
            f"**{len(branches)} Branch(es)** bound to this Goal:"
        )
        lines.append("")
        for b in branches[:12]:
            lines.append(
                f"- `{b['branch_def_id']}` · **{b['name']}** · "
                f"{b['author']} · v{b.get('version', 1)}"
            )
        if len(branches) > 12:
            lines.append(
                f"- … and {len(branches) - 12} more in the structured result."
            )
    else:
        lines.append(
            "_No Branches yet. Goal binding is not exposed by the "
            "advertised handles._"
        )
    return json.dumps({
        "text": "\n".join(lines),
        "goal": goal,
        "scope_actor": scope_actor,
        "actor_canonical_branch_version_id": (
            actor_canonical_branch_version_id
        ),
        "is_deleted": False,
        "branches": branches,
        "branch_count": len(branches),
        "gate_summary": gate_summary,
        "branch_protocol": protocol,
        "current_protocol_step": current_protocol_step,
    }, default=str)


def _action_goal_define_protocol(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.daemon_server import (
        current_goal_protocol_step,
        get_goal,
        set_goal_branch_protocol,
    )
    from tinyassets.identity import git_author

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    protocol_raw = kwargs.get("protocol_json", "")
    if not protocol_raw:
        return json.dumps({
            "status": "rejected",
            "error": "protocol_json is required.",
        })
    _ensure_workflow_db()
    try:
        current = get_goal(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })
    actor = _current_actor_or_anon()
    if current["author"] != actor and actor != "host":
        return json.dumps({
            "status": "rejected",
            "error": (
                f"Goal '{gid}' is owned by '{current['author']}'. "
                "Only the author can define its Branch protocol."
            ),
        })
    try:
        protocol = json.loads(protocol_raw)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"protocol_json must be valid JSON: {exc.msg}",
        })
    if not isinstance(protocol, list):
        return json.dumps({
            "status": "rejected",
            "error": "protocol_json must decode to a list of step objects.",
        })
    try:
        updated = set_goal_branch_protocol(
            _base_path(),
            goal_id=gid,
            protocol=protocol,
        )
    except ValueError as exc:
        return json.dumps({
            "status": "rejected",
            "error": str(exc),
        })
    try:
        saved, _commit = _storage_backend().save_goal_and_commit(
            updated,
            author=git_author(_current_actor()),
            message=f"goals.define_protocol: {gid}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    current_step = current_goal_protocol_step(saved.get("branch_protocol") or [])
    return json.dumps({
        "text": (
            f"**Defined Branch protocol for Goal '{saved['name']}'.** "
            f"{len(saved.get('branch_protocol') or [])} step(s)."
        ),
        "status": "defined",
        "goal_id": gid,
        "branch_protocol": saved.get("branch_protocol") or [],
        "current_protocol_step": current_step,
        "goal": saved,
    }, default=str)


def _action_goal_get_protocol(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.daemon_server import (
        current_goal_protocol_step,
        get_goal_branch_protocol,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    _ensure_workflow_db()
    try:
        goal = _get_public_goal(gid)
        protocol = get_goal_branch_protocol(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })
    current_step = current_goal_protocol_step(protocol)
    if protocol:
        lines = [f"**Branch protocol for Goal '{goal['name']}':**", ""]
        for step in protocol:
            marker = " ← current" if step == current_step else ""
            lines.append(
                f"- {step.get('order')}. `{step.get('branch_def_id')}` · "
                f"{step.get('source_label') or step.get('step_id')} · "
                f"{step.get('status', 'pending')}{marker}"
            )
    else:
        lines = [
            f"Goal '{goal['name']}' has no Branch protocol yet.",
            "Protocol definition is not exposed by the advertised handles.",
        ]
    return json.dumps({
        "text": "\n".join(lines),
        "goal_id": gid,
        "branch_protocol": protocol,
        "current_protocol_step": current_step,
        "count": len(protocol),
    }, default=str)


def _action_goal_search(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.daemon_server import search_goals

    query = (kwargs.get("query") or "").strip()
    if not query:
        return json.dumps({
            "status": "rejected",
            "error": "query is required for search.",
        })
    _ensure_workflow_db()
    rows = search_goals(
        _base_path(), query=query,
        limit=int(kwargs.get("limit", 20) or 20),
    )
    if rows:
        lines = [f"**{len(rows)} match(es) for `{query}`:**", ""]
        for g in rows[:12]:
            lines.append(_format_goal_catalog_line(g))
        if len(rows) > 12:
            lines.append(
                f"- … and {len(rows) - 12} more. Tighten the query."
            )
        text = "\n".join(lines)
    else:
        text = (
            f"No Goals match `{query}`. Propose a new Goal if the "
            "intent doesn't already exist."
        )
    return json.dumps({
        "text": text,
        "query": query,
        "goals": rows,
        "count": len(rows),
    }, default=str)


_V1_LEADERBOARD_METRICS = ("run_count", "forks", "outcome")
_GATE_EVENT_LEADERBOARD_METRICS = ("gate_events",)
_ALL_LEADERBOARD_METRICS = _V1_LEADERBOARD_METRICS + _GATE_EVENT_LEADERBOARD_METRICS


def _action_goal_leaderboard(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
    )
    from tinyassets.api.market import _gates_enabled
    from tinyassets.daemon_server import (
        goal_leaderboard,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    metric = (kwargs.get("metric") or "run_count").strip().lower()
    _ensure_workflow_db()
    try:
        goal = _get_public_goal(gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    if metric not in _ALL_LEADERBOARD_METRICS:
        return json.dumps({
            "status": "rejected",
            "error": f"Unknown metric '{metric}'.",
            "available_metrics": list(_ALL_LEADERBOARD_METRICS),
        })

    # gate_events metric: rank by attributed real-world gate events.
    if metric == "gate_events":
        from tinyassets.gate_events import leaderboard_by_gate_events
        window = (kwargs.get("window") or "all").strip().lower()
        limit = int(kwargs.get("limit", 20) or 20)
        try:
            lb = leaderboard_by_gate_events(
                _base_path(), goal_id=gid, window=window, limit=limit,
            )
        except ValueError as exc:
            return json.dumps({"status": "rejected", "error": str(exc)})
        lines = [
            f"**Gate-event leaderboard for Goal '{goal.get('name', gid)}'**"
            f" — window `{window}`",
            "",
        ]
        if lb["ranked"]:
            for rank, entry in enumerate(lb["ranked"], 1):
                lines.append(
                    f"{rank}. `{entry['branch_version_id']}` · "
                    f"gate events: {entry['gate_event_count']} "
                    f"({entry['verified_event_count']} verified) · "
                    f"score: {entry['score']:.1f} · "
                    f"latest: {entry['most_recent_event_date']}"
                )
        else:
            lines.append("_No gate events attributed to any workflow under this Goal._")
        return json.dumps({
            "text": "\n".join(lines),
            "goal_id": gid,
            "metric": metric,
            "window": lb["window"],
            "ranked": lb["ranked"],
            "total_events_in_window": lb["total_events_in_window"],
        }, default=str)

    # GATES_ENABLED gates the outcome metric. When the flag is off,
    # return a friendly flag-gated envelope rather than letting the
    # live leaderboard path run against an empty ladder (which would
    # confuse the UI into thinking the Goal simply has no claims).
    # Flag flips in 6.3 per spec.
    if metric == "outcome" and not _gates_enabled():
        return json.dumps({
            "text": (
                "**Leaderboard metric `outcome`** is gated by the "
                "`GATES_ENABLED` flag (Phase 6.2). Set "
                "`GATES_ENABLED=1` on the TinyAssets Server to opt in, "
                "or use `metric=run_count` / `metric=forks` today."
            ),
            "status": "gates_disabled",
            "goal_id": gid,
            "metric": metric,
            "entries": [],
        }, default=str)

    try:
        entries = goal_leaderboard(
            _base_path(), goal_id=gid, metric=metric,
            limit=int(kwargs.get("limit", 20) or 20),
            viewer=_current_actor(),
        )
    except ValueError as exc:
        return json.dumps({
            "status": "rejected",
            "error": str(exc),
            "available_metrics": list(_ALL_LEADERBOARD_METRICS),
        })

    lines = [
        f"**Leaderboard for Goal '{goal['name']}'**"
        f" — metric `{metric}`",
        "",
    ]
    if entries:
        for rank, entry in enumerate(entries, 1):
            value = entry.get("value", 0)
            if metric == "outcome":
                name = entry.get("branch_name") or entry.get("name", "")
                rung = entry.get("highest_rung_key", "")
                lines.append(
                    f"{rank}. **{name}** · rung `{rung}` "
                    f"(index {value}) · {entry.get('claimed_at', '')}"
                )
            else:
                lines.append(
                    f"{rank}. **{entry['name']}** · {entry['author']} · "
                    f"{metric}={value}"
                )
    elif metric == "outcome":
        lines.append(
            "_No gate claims yet. Ladder definition and gate claiming are "
            "not exposed by the advertised handles._"
        )
    else:
        lines.append(
            "_No workflows bound to this Goal yet. Goal binding is not "
            "exposed by the advertised handles._"
        )

    return json.dumps({
        "text": "\n".join(lines),
        "goal_id": gid,
        "metric": metric,
        "entries": entries,
    }, default=str)


def _action_goal_common_nodes(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
    )
    from tinyassets.daemon_server import (
        goal_common_nodes,
        goal_common_nodes_all,
    )

    _ensure_workflow_db()
    min_branches = int(kwargs.get("min_branches", 2) or 2)
    limit = int(kwargs.get("limit", 20) or 20)
    scope = (kwargs.get("scope") or "this_goal").strip().lower() or "this_goal"
    if scope not in ("this_goal", "all"):
        return json.dumps({
            "status": "rejected",
            "error": (
                f"scope='{scope}' is unknown. Use 'this_goal' (default) "
                "to limit to one Goal, or 'all' for cross-Goal "
                "aggregation."
            ),
        })

    if scope == "all":
        # Phase 6.2.2 — viewer-aware cross-Goal aggregation; private
        # Branches owned by other actors don't contribute.
        entries = goal_common_nodes_all(
            _base_path(),
            min_branches=min_branches,
            limit=limit,
            viewer=_current_actor(),
            public_goals_only=True,
        )
        lines = [
            "**Common nodes across ALL Goals** "
            f"appearing in ≥{min_branches} workflows.",
            "",
        ]
        if entries:
            for e in entries[:12]:
                goal_tag = ""
                if e.get("goal_ids"):
                    gl = e["goal_ids"][:3]
                    goal_tag = (
                        f" · goals: {', '.join(gl)}"
                        + (" …" if len(e["goal_ids"]) > 3 else "")
                    )
                lines.append(
                    f"- `{e['node_id']}` · **{e['display_name']}** · "
                    f"used in {e['occurrence_count']} branches"
                    f"{goal_tag}"
                )
            if len(entries) > 12:
                lines.append(f"- … and {len(entries) - 12} more.")
            lines.append("")
            lines.append(
                "_Reuse an existing node with write_graph target=\"branch\" "
                "branch_id=\"<branch_id>\" changes_json='[{\"op\":"
                "\"add_node\",\"node_ref\":{\"source\":\"<source>\","
                "\"node_id\":\"<node_id>\"}}]' (#66)._"
            )
        else:
            lines.append(
                "_No node_ids repeat across Branches yet. Consider "
                "using the same node_id when nodes serve the same "
                "role so the reuse surface can discover them._"
            )
        return json.dumps({
            "text": "\n".join(lines),
            "scope": "all",
            "min_branches": min_branches,
            "entries": entries,
        }, default=str)

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": (
                "goal_id is required for scope='this_goal'. Pass "
                "scope='all' to skip the Goal filter."
            ),
        })
    try:
        goal = _get_public_goal(gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    # Phase 6.2.2 — viewer-aware aggregation; private Branches
    # owned by other actors don't contribute their node inventory.
    entries = goal_common_nodes(
        _base_path(), goal_id=gid,
        min_branches=min_branches,
        limit=limit,
        viewer=_current_actor(),
    )
    lines = [
        f"**Common nodes in Goal '{goal['name']}'** "
        f"appearing in ≥{min_branches} workflows.",
        "",
    ]
    if entries:
        for e in entries[:12]:
            lines.append(
                f"- `{e['node_id']}` · **{e['display_name']}** · "
                f"used in {e['occurrence_count']} branches "
                f"(first seen: `{e['first_seen_in']}`)"
            )
        if len(entries) > 12:
            lines.append(
                f"- … and {len(entries) - 12} more."
            )
    else:
        lines.append(
            "_No nodes repeated across Branches yet — each Branch "
            "uses its own node_ids. Consider renaming nodes to share "
            "identity when they serve the same role._"
        )
    return json.dumps({
        "text": "\n".join(lines),
        "goal_id": gid,
        "scope": "this_goal",
        "min_branches": min_branches,
        "entries": entries,
    }, default=str)


def _action_goal_archive_consultation(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_server import goal_archive_consultation

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required.",
        })
    _ensure_workflow_db()
    try:
        goal = _get_public_goal(gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    query = (kwargs.get("query") or "").strip()
    consultation = goal_archive_consultation(
        _base_path(),
        goal_id=gid,
        query=query,
        limit=int(kwargs.get("limit", 20) or 20),
        viewer=_current_actor(),
    )
    candidates = consultation["candidates"]
    lines = [
        f"**Archive consultation for Goal '{goal['name']}'**",
        (
            "Parent candidates are ranked with quality, diversity, "
            "and the gate leaderboard outcome signal."
        ),
        "",
    ]
    if candidates:
        for candidate in candidates[:12]:
            outcome = candidate.get("outcome_signal") or {}
            lines.append(
                f"{candidate['rank']}. **{candidate['name']}** · "
                f"score={candidate['parent_rank_score']} · "
                f"quality={candidate['quality_score']} · "
                f"outcome={candidate['outcome_score']} "
                f"(`{outcome.get('highest_rung_key', '')}`) · "
                f"diversity={candidate['diversity_score']} · "
                f"`{candidate['branch_def_id']}`"
            )
    else:
        if query:
            lines.append("_No bound Branches match that archive query._")
        else:
            lines.append(
                "_No Branches are bound to this Goal yet. Goal-to-branch "
                "binding is not exposed by the advertised handles._"
            )

    return json.dumps({
        "status": "ok",
        "text": "\n".join(lines),
        "goal_id": gid,
        "query": query,
        "candidates": candidates,
        "outcome_leaderboard": consultation["outcome_leaderboard"],
        "selection_basis": consultation["selection_basis"],
        "count": len(candidates),
    }, default=str)


def _action_goal_set_selector(kwargs: dict[str, Any]) -> str:
    """Bind (or unbind) a Goal's selector branch — DESIGN-008.

    The selector branch is the published TinyAssets branch the
    substrate dispatches to rank a Goal's bound branches. Pass
    ``branch_version_id=""`` to unbind (fall back to platform
    default selector).

    Authority: only Goal author or an actor with the selector-bind
    capability may bind a selector.

    Required kwargs:
      * ``goal_id`` — Goal whose selector is being bound.

    Optional kwargs:
      * ``branch_version_id`` — selector branch_version to bind, or
        empty string / omitted to unbind.

    Returns:
      * ``{status: "ok", selector_branch_version_id: ...}`` on bind/unbind.
      * ``{status: "rejected", error: ...}`` on auth failure / bad input
        / non-active version.
    """
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_server import (
        CAP_SET_GOAL_SELECTOR,
        SelectorHasEffectsError,
        get_goal,
        set_selector_branch,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required for set_selector.",
        })
    raw_bvid = (kwargs.get("branch_version_id") or "").strip()
    branch_version_id = raw_bvid or None
    _ensure_workflow_db()

    try:
        goal = get_goal(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })

    actor = _current_actor()
    if actor != goal["author"] and not _current_actor_has_capability(
        CAP_SET_GOAL_SELECTOR,
    ):
        return json.dumps({
            "status": "rejected",
            "error": (
                "Only the Goal author or an actor with "
                f"{CAP_SET_GOAL_SELECTOR!r} may bind the selector branch. "
                f"Goal author is '{goal['author']}'; request actor is '{actor}'."
            ),
        })

    try:
        updated = set_selector_branch(
            _base_path(), goal_id=gid,
            branch_version_id=branch_version_id, set_by=actor,
        )
    except SelectorHasEffectsError as exc:
        # P1.3 — surface the effects rejection with a structured
        # error_kind so chatbots can route the operator to the fix
        # path ("remove effects on offending nodes, then re-bind").
        return json.dumps({
            "status": "rejected",
            "error_kind": "selector_has_effects",
            "error": str(exc),
        })
    except ValueError as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})

    if branch_version_id:
        text = (
            f"Selector branch for Goal '{goal['name']}' set to "
            f"`{branch_version_id}`. Future internal leaderboard and "
            "recommended-parent operations dispatch this branch to rank "
            "candidates; those operations are not exposed by the advertised "
            "handles."
        )
    else:
        text = (
            f"Selector branch for Goal '{goal['name']}' unbound. "
            "Leaderboard calls fall back to the platform default "
            "selector."
        )

    return json.dumps({
        "status": "ok",
        "text": text,
        "goal_id": gid,
        "selector_branch_version_id": updated.get(
            "selector_branch_version_id",
        ),
    }, default=str)


def _action_goal_set_canonical(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
    )
    from tinyassets.daemon_server import (
        CAP_SET_CANONICAL_BRANCH,
        get_goal,
        set_canonical_branch,
        set_goal_canonical,
    )
    from tinyassets.principals import named_principal

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({"status": "rejected", "error": "goal_id is required."})
    branch_version_id = (kwargs.get("branch_version_id") or "").strip() or None
    _ensure_workflow_db()

    try:
        goal = get_goal(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({"status": "rejected", "error": f"Goal '{gid}' not found."})

    # A canonical is something every runner of the goal will run: it may only
    # name a version the setter can read, or a personal canonical becomes a
    # way to run -- and preflight-read -- another user's private snapshot
    # (astra refute 2026-09-30). Unreadable reads as absent.
    if branch_version_id:
        from tinyassets.api.branches import _resolve_readable_version

        if _resolve_readable_version(branch_version_id, str(_base_path())) is None:
            return json.dumps({
                "status": "rejected",
                "error": f"Branch version '{branch_version_id}' not found.",
            })

    actor = named_principal(_current_actor())
    raw_scope_actor = str(kwargs.get("scope") or "").strip()
    scope_actor = named_principal(raw_scope_actor)
    if raw_scope_actor and (not actor or not scope_actor):
        return json.dumps({
            "status": "rejected",
            "error": "Authentication is required for a personal canonical.",
        })
    if scope_actor and scope_actor != actor:
        return json.dumps({
            "status": "rejected",
            "error": "Cannot set a personal canonical for another actor.",
        })
    if (
        not scope_actor
        and actor != goal["author"]
        and not _current_actor_has_capability(CAP_SET_CANONICAL_BRANCH)
    ):
        return json.dumps({
            "status": "rejected",
            "error": (
                "Only the Goal author or an actor with "
                f"{CAP_SET_CANONICAL_BRANCH!r} may set the canonical branch. "
                f"Goal author is '{goal['author']}'; request actor is '{actor}'."
            ),
        })

    try:
        if scope_actor:
            updated = set_goal_canonical(
                _base_path(),
                goal_id=gid,
                scope_actor=scope_actor,
                branch_version_id=branch_version_id,
                set_by=actor,
            )
        else:
            updated = set_canonical_branch(
                _base_path(),
                goal_id=gid,
                branch_version_id=branch_version_id,
                set_by=actor,
            )
    except ValueError as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})

    if branch_version_id:
        # `text` is the phone/chat channel and must never carry a raw id
        # (task #58); `branch_version_id` is `<branch_def_id>@<hash>`, so
        # printing it here leaked the branch id. The id stays in the
        # structured `canonical_branch_version_id` field below.
        if scope_actor:
            text = (
                f"Your canonical branch for Goal '{goal['name']}' is set. "
                "Your canonical runs prefer this immutable version."
            )
        else:
            text = (
                f"Canonical branch for Goal '{goal['name']}' set. "
                f"New users forking this Goal will start from this version."
            )
    else:
        if scope_actor:
            text = (
                f"Your personal canonical for Goal '{goal['name']}' is unset. "
                "Your canonical runs now fall back to the Goal default."
            )
        else:
            text = (
                f"Canonical branch for Goal '{goal['name']}' unset. "
                f"No starter branch is currently designated."
            )

    canonical_branch_version_id = (
        updated.get("branch_version_id")
        if scope_actor
        else updated.get("canonical_branch_version_id")
    )
    return json.dumps({
        "status": "ok",
        "text": text,
        "goal_id": gid,
        "scope_actor": scope_actor,
        "canonical_branch_version_id": canonical_branch_version_id,
    }, default=str)


def _action_goal_run_canonical(kwargs: dict[str, Any]) -> str:
    """Dispatch a run against a Goal's canonical handler — PR-127 (M6).

    The Goal's stored ``canonical_branch_version_id`` is the dispatch
    target. When ``goal.auto_canonical_via_leaderboard`` is True, the
    canonical is first refreshed against the freshest leaderboard
    top-entry (subject to the ``min_completed_runs_for_canonical``
    threshold and the in-flight guard — see
    :func:`tinyassets.api.canonical_dispatch.resolve_canonical_for_run`).

    Required kwargs:
      * ``goal_id`` — the Goal to dispatch against.

    Optional kwargs:
      * ``inputs_json`` — JSON string of inputs forwarded to the run.
      * ``run_name`` — display name for the resulting run row.
      * ``recursion_limit_override`` — passthrough to the run executor.

    Returns one of:

    Success::

        {
            "status": "queued",
            "text": "<phone-legible summary>",
            "run_id": "...",
            "branch_version_id_used": "...",
            "branch_def_id": "...",
            "source": "<canonical_stored | leaderboard_refreshed | ...>",
            "goal_id": "...",
            ...passthrough from run_branch_version...
        }

    Rejection::

        {
            "status": "rejected",
            "error": "...",
            "error_kind": "no_canonical_handler | no_goal | ...",
            "goal_id": "...",
        }
    """
    from tinyassets.api.canonical_dispatch import resolve_canonical_for_run
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.api.runs import _action_run_branch_version

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required for run_canonical.",
            "error_kind": "missing_goal_id",
        })

    resolution = resolve_canonical_for_run(
        _base_path(),
        goal_id=gid,
        viewer=_current_actor(),
    )
    if not resolution.get("ok"):
        # Surface the failure verbatim so the caller's branching logic
        # can read error_kind directly.
        return json.dumps({
            "status": "rejected",
            "error": resolution.get("error", ""),
            "error_kind": resolution.get(
                "error_kind", "no_canonical_handler",
            ),
            "goal_id": gid,
            "hint": resolution.get("hint", ""),
        }, default=str)

    bvid = resolution["branch_version_id"]
    bdid = resolution.get("branch_def_id", "")

    # Delegate dispatch to the existing run_branch_version path so
    # both surfaces share executor + provider + recursion-limit
    # behavior. Inputs flow through verbatim.
    dispatch_result_raw = _action_run_branch_version({
        "branch_version_id": bvid,
        "inputs_json": kwargs.get("inputs_json", "") or "",
        "run_name": kwargs.get("run_name", "") or "",
        "recursion_limit_override": (
            kwargs.get("recursion_limit_override", "") or ""
        ),
    })
    try:
        dispatch_result = json.loads(dispatch_result_raw)
    except (TypeError, ValueError):
        # Defensive — run_branch_version always returns JSON.
        return dispatch_result_raw

    # Annotate the dispatch response with canonical-resolution metadata
    # WITHOUT overwriting any of run_branch_version's existing fields.
    dispatch_result.setdefault("goal_id", gid)
    dispatch_result["branch_version_id_used"] = bvid
    dispatch_result.setdefault("branch_def_id", bdid)
    dispatch_result["scope_actor"] = resolution.get("scope_actor", "")
    dispatch_result["source"] = resolution.get("source")
    if resolution.get("refresh_attempted"):
        dispatch_result["refresh_attempted"] = True
        if resolution.get("displaced_canonical_branch_version_id"):
            dispatch_result["displaced_canonical_branch_version_id"] = (
                resolution["displaced_canonical_branch_version_id"]
            )
        for k in (
            "candidate_branch_def_id",
            "candidate_completed_runs",
            "candidate_score",
            "min_completed_runs_for_canonical",
            "in_flight_run_id",
            "in_flight_status",
            "in_flight_started_at",
        ):
            if k in resolution:
                dispatch_result[k] = resolution[k]
        if resolution.get("hint") and not dispatch_result.get("hint"):
            dispatch_result["hint"] = resolution["hint"]

    # Render a short text summary if the underlying handler didn't.
    if "text" not in dispatch_result or not dispatch_result["text"]:
        run_id = dispatch_result.get("run_id") or "(no run_id)"
        dispatch_result["text"] = (
            f"Canonical dispatch for Goal `{gid}` -> "
            f"branch_version_id `{bvid}` (run_id `{run_id}`, "
            f"source `{resolution.get('source')}`)."
        )

    return json.dumps(dispatch_result, default=str)


_GOAL_ACTIONS: dict[str, Any] = {
    "propose": _action_goal_propose,
    "update": _action_goal_update,
    "bind": _action_goal_bind,
    "list": _action_goal_list,
    "get": _action_goal_get,
    "search": _action_goal_search,
    "leaderboard": _action_goal_leaderboard,
    "common_nodes": _action_goal_common_nodes,
    "archive_consultation": _action_goal_archive_consultation,
    "set_canonical": _action_goal_set_canonical,
    "define_protocol": _action_goal_define_protocol,
    "get_protocol": _action_goal_get_protocol,
    # PR-127 (M6 cutover Step 4) — leaderboard-driven canonical
    # dispatch. Honors auto_canonical_via_leaderboard + threshold +
    # in-flight gate; delegates the actual run to run_branch_version.
    "run_canonical": _action_goal_run_canonical,
    # DESIGN-008 — user-buildable selector primitive. Bind the
    # published TinyAssets branch that synthesizes the Goal's
    # leaderboard. Pass branch_version_id="" to fall back to the
    # platform default selector.
    "set_selector": _action_goal_set_selector,
}

# Provider-routing compatibility: older clients can render legacy goal tool
# names but dispatch them through the canonical `Goals` wrapper.
_GOAL_ACTION_ALIASES: dict[str, str] = {
    "list_workflow_goals": "list",
    "search_workflow_goals": "search",
    "get_workflow_goal": "get",
    "propose_workflow_goal": "propose",
}

_GOAL_WRITE_ACTIONS: frozenset[str] = frozenset({
    "propose", "update", "bind", "set_canonical", "define_protocol",
    # DESIGN-008 — selector branch binding writes to goals row.
    "set_selector",
})
_GOAL_NON_READ_ACTIONS: frozenset[str] = (
    _GOAL_WRITE_ACTIONS | frozenset({"run_canonical"})
)


def _canonical_goal_action(action: str) -> str:
    return _GOAL_ACTION_ALIASES.get(action, action)


def _available_goal_actions() -> list[str]:
    return sorted(set(_GOAL_ACTIONS.keys()) | set(_GOAL_ACTION_ALIASES.keys()))


def _dispatch_goal_action(
    action: str, handler: Any, kwargs: dict[str, Any],
) -> str:
    """Run a Goal action; ledger write actions for public attribution.

    Catches :class:`DirtyFileError` from cached-backend writes (per H2)
    and formats it as the structured ``local_edit_conflict`` payload so
    chat clients render actionable options rather than a raw traceback.
    """
    from tinyassets.api.branches import _append_global_ledger, ledger_actor
    from tinyassets.api.engine_helpers import (
        _format_dirty_file_conflict,
        _truncate,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if action not in _GOAL_NON_READ_ACTIONS and gid:
        try:
            _get_public_goal(gid)
        except KeyError:
            return json.dumps({
                "status": "rejected",
                "error": f"Goal '{gid}' not found.",
            })

    try:
        result_str = handler(kwargs)
    except DirtyFileError as exc:
        return json.dumps(_format_dirty_file_conflict(exc))
    if action not in _GOAL_WRITE_ACTIONS:
        return result_str
    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str
    if not isinstance(result, dict):
        return result_str
    if result.get("error") or result.get("status") == "rejected":
        return result_str
    try:
        target = (
            result.get("goal", {}).get("goal_id")
            or result.get("goal_id")
            or kwargs.get("goal_id", "")
            or kwargs.get("branch_def_id", "")
        )
        summary_bits = [f"goals.{action}"]
        if kwargs.get("name"):
            summary_bits.append(kwargs["name"])
        if kwargs.get("goal_id"):
            summary_bits.append(f"goal={kwargs['goal_id']}")
        if kwargs.get("branch_def_id"):
            summary_bits.append(f"branch={kwargs['branch_def_id']}")
        _append_global_ledger(
            f"goals.{action}",
            # `actor` is REQUIRED. Omitting it raised TypeError into the
            # `except` below, which logged a warning and dropped the row —
            # silently losing attribution for every goals write. Four call
            # sites had this defect; see `ledger_actor` for why the fallback
            # must be a literal rather than "".
            actor=ledger_actor(),
            target=str(target),
            summary=_truncate(" ".join(summary_bits)),
            payload=None,
        )
    except Exception as exc:
        logger.warning("Ledger write failed for goals.%s: %s", action, exc)
    return result_str


def goals(
    action: str,
    goal_id: str = "",
    branch_def_id: str = "",
    branch_version_id: str = "",
    name: str = "",
    description: str = "",
    tags: str = "",
    visibility: str = "",
    query: str = "",
    metric: str = "",
    min_branches: int = 2,
    author: str = "",
    limit: int = 50,
    scope: str = "",
    production_only: bool = False,
    protocol_json: str = "",
    force: bool = False,
    inputs_json: str = "",
    run_name: str = "",
    recursion_limit_override: int = 0,
) -> str:
    """Goals — first-class shared primitives above workflow Branches.

    A Goal captures the intent a workflow serves ("produce a research
    paper", "plan a wedding"). Many Branches bind to one Goal. Users
    discover existing Goals before building a new Branch so the
    "reuse vs invent" decision is tractable.

    Actions:
      propose      Create a new Goal. Needs `name`. Optional
                   description, tags (CSV), visibility (public/private).
      update       Patch a Goal you own. Fields: name, description,
                   tags, visibility. Non-owners cannot update — propose
                   a new Goal instead.
      bind         Attach a Branch to a Goal. Pass goal_id="" to
                   unbind. Needs branch_def_id.
      define_protocol Attach an ordered Goal runbook. Needs goal_id and
                   protocol_json, a JSON list of step objects. Each
                   step references a Branch already bound to this Goal
                   and may carry input_artifact_labels,
                   output_artifact_labels, source_label,
                   required_rung_key, required_verdict,
                   rollback_policy, and next_step_conditions.
      get_protocol Read a Goal's ordered Branch protocol/runbook.
                   Needs goal_id.
      set_canonical Mark a branch_version_id as the Goal's canonical
                   (best-known) branch. Author/capability-only by
                   default. Pass scope=<your actor id> to set only
                   your personal canonical. Pass branch_version_id=""
                   to unset the selected scope.
      set_selector Bind the Goal's selector branch_version — the
                   published TinyAssets branch the substrate dispatches
                   to rank this Goal's bound branches on the
                   leaderboard (DESIGN-008). Author-only or
                   host-only. Pass branch_version_id="" to unbind
                   and fall back to the platform default selector.
                   The bound selector MUST conform to the contract
                   in drafts/concepts/selector-branch-contract.md.
      run_canonical Dispatch a run against the Goal's canonical
                   branch_version. PR-127 (M6 cutover): when
                   ``auto_canonical_via_leaderboard`` is enabled on the
                   Goal, the canonical is first refreshed via the
                   quality leaderboard (subject to
                   ``min_completed_runs_for_canonical`` threshold and
                   the in-flight guard). Needs goal_id. Accepts
                   ``inputs_json``, ``run_name``,
                   ``recursion_limit_override``. Returns
                   ``branch_version_id_used`` + ``source`` so the
                   caller can see which version was picked and why.
      list         Browse Goals. Optional author, tags (CSV first
                   value only), limit, production_only. Soft-deleted
                   Goals hidden. production_only keeps public Goals
                   and filters RETRACTED/smoke/disposable entries.
      get          Full Goal view + bound Branches. Needs goal_id.
      search       LIKE-based substring search over name, description,
                   tags. Needs query.
      leaderboard  Rank bound Branches by metric. v1 supports
                   `run_count` and `forks`. `outcome` returns a Phase 6
                   stub today; the same call will surface real outcome
                   rankings once Phase 6 ships.
      common_nodes Nodes appearing in ≥`min_branches` Branches.
                   With `scope="this_goal"` (default), restricts to
                   one Goal's Branches. With `scope="all"`, aggregates
                   across every Goal and every unbound Branch — use
                   this when helping a user decide "is there already
                   a node that does X somewhere on this server?" even
                   if they haven't committed to a Goal yet.
      archive_consultation Rank bound Branches as fork parents using
                   quality, diversity, and gates leaderboard outcome
                   signal. Optional query filters the candidate space.

    Args:
      action: see above.
      goal_id: Goal target for bind/get/update/search/leaderboard,
        and for common_nodes when scope='this_goal'.
      branch_def_id: Branch target for bind.
      branch_version_id: Published branch_version_id for set_canonical
        (pass empty string to unset). Must reference a row in
        `branch_versions` — only published versions may be canonical.
      name/description/tags/visibility: Goal fields for propose/update.
      query: search query.
      metric: leaderboard metric (run_count/forks/outcome).
      min_branches: common_nodes cutoff (default 2).
      scope: for set_canonical, the current actor ID selects a personal
        canonical. For common_nodes, 'this_goal' (default) restricts to
        one Goal and 'all' aggregates cross-Goal.
      production_only: list filter for fresh-user discovery. Keeps
        public Goals and filters RETRACTED/smoke/disposable entries.
      protocol_json: JSON list for define_protocol.
      inputs_json/run_name/recursion_limit_override: run_canonical options
        forwarded to the immutable Branch-version runner.
      author: list filter.
      limit: cap on returned rows.
      force: override `local_edit_conflict` refusal on propose/update/bind
        when the target YAML has uncommitted local edits. Default False —
        the conflict surfaces as a structured response so the caller can
        commit / stash / discard first.
    """
    from tinyassets.api.branches import _ensure_workflow_db

    _ensure_workflow_db()
    goal_kwargs: dict[str, Any] = {
        "goal_id": goal_id,
        "branch_def_id": branch_def_id,
        "branch_version_id": branch_version_id,
        "name": name,
        "description": description,
        "tags": tags,
        "visibility": visibility,
        "query": query,
        "metric": metric,
        "min_branches": min_branches,
        "author": author,
        "limit": limit,
        "scope": scope,
        "production_only": production_only,
        "protocol_json": protocol_json,
        "inputs_json": inputs_json,
        "run_name": run_name,
        "recursion_limit_override": recursion_limit_override,
        "force": force,
    }
    canonical_action = _canonical_goal_action(action)
    handler = _GOAL_ACTIONS.get(canonical_action)
    if handler is None:
        return json.dumps({
            "error": f"Unknown action '{action}'.",
            "available_actions": _available_goal_actions(),
        })

    # Auth scope gate — goals is a write-capable commons surface, so enforce the
    # named action scope (resolve-always/WorkOS): public projections are separate;
    # not propose/update/bind/set_canonical/define_protocol/set_selector. Covers
    # write_graph(target="goal"), which routes here.
    from tinyassets.auth.middleware import require_action_scope
    try:
        require_action_scope("goals", canonical_action)
    except PermissionError as exc:
        return json.dumps({
            "error": str(exc),
            "auth_scope_required": True,
            "tool": "goals",
            "action": canonical_action,
        })

    return _dispatch_goal_action(canonical_action, handler, goal_kwargs)

# ═══════════════════════════════════════════════════════════════════════════
# TOOL 3b — Outcome Gates (Phase 6.1)
# ═══════════════════════════════════════════════════════════════════════════
#
# Flag-gated by GATES_ENABLED=1. Phase 6.1 ships schema + three actions
# (define_ladder / get_ladder / claim) write-through SQLite only. Git
# commit integration + remaining actions (retract / list_claims /
# leaderboard) ship in 6.2 / 6.3.


def _gates_enabled() -> bool:
    import os
    return os.environ.get("GATES_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


_TINYASSETS_RUN_EVIDENCE_PREFIXES = (
    "workflow:run:",
    "run:",
    "run-attachment:",
    "child_run:",
)


def _is_workflow_run_evidence_handle(value: str) -> bool:
    """Return True for opaque run evidence handles issued by TinyAssets."""
    if any(char.isspace() for char in value):
        return False
    return any(
        value.startswith(prefix) and len(value) > len(prefix)
        for prefix in _TINYASSETS_RUN_EVIDENCE_PREFIXES
    )


def _validate_evidence_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return ""
    if _is_workflow_run_evidence_handle(url or ""):
        return ""
    return (
        "evidence_url must be an http(s) URL with a host "
        "(e.g. https://example.com/path) or a TinyAssets run evidence "
        "handle (e.g. workflow:run:<run_id>)."
    )


def _validate_conformance_pack_for_claim(
    *,
    base_path: str | Path,
    pack_id: str,
    goal_id: str,
    branch_def_id: str,
    rung_key: str,
    required_standard_id: str,
) -> dict[str, Any] | None:
    from tinyassets.conformance_packs import get_conformance_pack

    pack = get_conformance_pack(base_path, pack_id)
    if pack is None:
        return {
            "status": "rejected",
            "error": "conformance_pack_not_found",
            "conformance_pack_id": pack_id,
        }
    if pack.goal_id != goal_id:
        return {
            "status": "rejected",
            "error": "conformance_pack_goal_mismatch",
            "conformance_pack_id": pack_id,
            "expected_goal_id": goal_id,
            "actual_goal_id": pack.goal_id,
        }
    if pack.branch_def_id and pack.branch_def_id != branch_def_id:
        return {
            "status": "rejected",
            "error": "conformance_pack_branch_mismatch",
            "conformance_pack_id": pack_id,
            "expected_branch_def_id": branch_def_id,
            "actual_branch_def_id": pack.branch_def_id,
        }
    if pack.target_rung and pack.target_rung != rung_key:
        return {
            "status": "rejected",
            "error": "conformance_pack_rung_mismatch",
            "conformance_pack_id": pack_id,
            "expected_rung_key": rung_key,
            "actual_target_rung": pack.target_rung,
        }
    if required_standard_id and pack.standard_id != required_standard_id:
        return {
            "status": "rejected",
            "error": "conformance_pack_standard_mismatch",
            "conformance_pack_id": pack_id,
            "expected_standard_id": required_standard_id,
            "actual_standard_id": pack.standard_id,
        }
    if pack.status != "ready":
        return {
            "status": "rejected",
            "error": "conformance_pack_not_ready",
            "conformance_pack_id": pack_id,
            "pack_status": pack.status,
            "blockers": pack.blockers,
        }
    return None


def _action_gates_define_ladder(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.catalog.layout import slugify
    from tinyassets.daemon_server import CAP_DEFINE_GATE_LADDER, get_goal
    from tinyassets.identity import git_author

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required for define_ladder.",
        })
    ladder_raw = (kwargs.get("ladder") or "").strip()
    if not ladder_raw:
        return json.dumps({
            "status": "rejected",
            "error": "ladder JSON is required for define_ladder.",
        })
    try:
        ladder = json.loads(ladder_raw)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"ladder must be a JSON list. {exc}",
        })
    if not isinstance(ladder, list):
        return json.dumps({
            "status": "rejected",
            "error": "ladder must be a JSON list of rung objects.",
        })
    seen: set[str] = set()
    for idx, rung in enumerate(ladder):
        if not isinstance(rung, dict):
            return json.dumps({
                "status": "rejected",
                "error": f"ladder[{idx}] must be an object.",
            })
        key = (rung.get("rung_key") or "").strip()
        if not key:
            return json.dumps({
                "status": "rejected",
                "error": f"ladder[{idx}].rung_key is required.",
            })
        if key in seen:
            return json.dumps({
                "status": "rejected",
                "error": f"duplicate rung_key '{key}' in ladder.",
            })
        seen.add(key)
    _ensure_workflow_db()
    try:
        goal = get_goal(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })
    actor = _current_actor_or_anon()
    if goal.get("author") and goal["author"] != actor and not (
        _current_actor_has_capability(CAP_DEFINE_GATE_LADDER)
    ):
        return json.dumps({
            "status": "rejected",
            "error": (
                "Only the Goal author or an actor with "
                f"{CAP_DEFINE_GATE_LADDER!r} can define its ladder. "
                f"Owner: {goal['author']}."
            ),
        })
    # Ride the ladder through `save_goal_and_commit` — spec §Migration
    # 5: ladder is a dict key on the goal, no backend protocol change
    # beyond the existing save_goal_and_commit. Commit namespace is
    # `goals.define_ladder` because the file written is
    # `goals/<slug>.yaml`, not `gates/...`.
    updated_goal = dict(goal)
    updated_goal["gate_ladder"] = ladder
    force = bool(kwargs.get("force", False))
    goal_slug = slugify(goal.get("name") or gid)
    try:
        saved, _commit = _storage_backend().save_goal_and_commit(
            updated_goal,
            author=git_author(_current_actor()),
            message=f"goals.define_ladder: {goal_slug}",
            force=force,
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    return json.dumps({
        "status": "defined",
        "goal_id": gid,
        "gate_ladder": saved.get("gate_ladder", []),
    }, default=str)


def _action_gates_get_ladder(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.daemon_server import get_goal_ladder

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required for get_ladder.",
        })
    _ensure_workflow_db()
    try:
        ladder = get_goal_ladder(_base_path(), goal_id=gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })
    return json.dumps({
        "status": "ok",
        "goal_id": gid,
        "gate_ladder": ladder,
    }, default=str)


def _action_gates_claim(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.catalog.layout import slugify
    from tinyassets.daemon_server import (
        get_branch_definition,
        get_gate_claim,
        get_goal,
        get_goal_ladder,
    )
    from tinyassets.identity import git_author

    bid = (kwargs.get("branch_def_id") or "").strip()
    rung_key = (kwargs.get("rung_key") or "").strip()
    evidence_url = (kwargs.get("evidence_url") or "").strip()
    force = bool(kwargs.get("force", False))
    if not (bid and rung_key and evidence_url):
        return json.dumps({
            "status": "rejected",
            "error": (
                "branch_def_id, rung_key, evidence_url are required."
            ),
        })
    url_err = _validate_evidence_url(evidence_url)
    if url_err:
        return json.dumps({"status": "rejected", "error": url_err})
    _ensure_workflow_db()
    try:
        branch = get_branch_definition(_base_path(), branch_def_id=bid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Branch '{bid}' not found.",
        })
    goal_id = branch.get("goal_id") or ""
    if not goal_id:
        return json.dumps({
            "status": "rejected",
            "error": (
                "Branch is not bound to a Goal. "
                "Goal binding is not exposed by the advertised handles."
            ),
        })
    try:
        goal = get_goal(_base_path(), goal_id=goal_id)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{goal_id}' not found.",
        })
    # Rebind guard: if an ACTIVE (non-retracted) claim exists for
    # (branch, rung) under a different Goal, the Branch was rebound
    # between claims. Reject so the original Goal's leaderboard keeps
    # its history; caller must retract the stale claim before
    # re-claiming under the new Goal. Retracted prior claims are
    # resolved intent — re-claim reactivates under the new Goal.
    existing = get_gate_claim(
        _base_path(), branch_def_id=bid, rung_key=rung_key,
    )
    if (
        existing is not None
        and not existing.get("retracted_at")
        and (existing.get("goal_id") or "") != goal_id
    ):
        return json.dumps({
            "status": "rejected",
            "error": "branch_rebound",
            "original_goal_id": existing.get("goal_id") or "",
            "current_goal_id": goal_id,
            "hint": (
                "Retract the existing claim under the original Goal "
                "first, then re-claim under the new Goal."
            ),
        })
    ladder = get_goal_ladder(_base_path(), goal_id=goal_id)
    available = [r.get("rung_key") for r in ladder if r.get("rung_key")]
    if rung_key not in available:
        return json.dumps({
            "status": "rejected",
            "error": "unknown_rung",
            "available_rungs": available,
        })
    from tinyassets.conformance_packs import required_standard_id_for_rung

    required_standard_id = required_standard_id_for_rung(rung_key, ladder)
    conformance_pack_id = (kwargs.get("conformance_pack_id") or "").strip()
    if required_standard_id is not None:
        if not conformance_pack_id:
            return json.dumps({
                "status": "rejected",
                "error": "conformance_pack_required",
                "rung_key": rung_key,
                "required_standard_id": required_standard_id,
                "hint": (
                    "Record a ready conformance pack first and pass "
                    "conformance_pack_id with this claim."
                ),
            })
        pack_error = _validate_conformance_pack_for_claim(
            base_path=_base_path(),
            pack_id=conformance_pack_id,
            goal_id=goal_id,
            branch_def_id=bid,
            rung_key=rung_key,
            required_standard_id=required_standard_id,
        )
        if pack_error is not None:
            return json.dumps(pack_error)
    from tinyassets.daemon_server import BranchRebindError

    goal_slug = slugify(goal.get("name") or goal_id)
    branch_slug = slugify(branch.get("name") or bid)
    try:
        saved, _commit = _storage_backend().save_gate_claim_and_commit(
            branch_def_id=bid,
            goal_id=goal_id,
            rung_key=rung_key,
            evidence_url=evidence_url,
            evidence_note=kwargs.get("evidence_note", ""),
            conformance_pack_id=conformance_pack_id,
            claimed_by=_current_actor_or_anon(),
            goal_slug=goal_slug,
            branch_slug=branch_slug,
            author=git_author(_current_actor()),
            message=f"gates.claim: {goal_slug}/{branch_slug}@{rung_key}",
            force=force,
        )
    except BranchRebindError as exc:
        # Storage-layer guard fired — means another caller rebound the
        # Branch between this handler's pre-check and the storage
        # write. Surface the same envelope as the handler pre-check.
        return json.dumps({
            "status": "rejected",
            "error": "branch_rebound",
            "original_goal_id": exc.original_goal_id,
            "current_goal_id": exc.current_goal_id,
            "hint": (
                "Retract the existing claim under the original Goal "
                "first, then re-claim under the new Goal."
            ),
        })
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    return json.dumps({
        "status": "claimed",
        "claim": saved,
    }, default=str)


def _action_gates_claim_from_branch_run(kwargs: dict[str, Any]) -> str:
    """PR-126 M5 — claim a gate rung from a completed run's final state.

    Reads the run's ``output["recommended_rung_claim"]`` and validates
    the rung_key against the bound Goal's ladder before delegating to
    ``_action_gates_claim`` for the actual write. Keeps the substrate
    convention crisp:

      * Branch authors emit ``recommended_rung_claim`` (a rung_key
        matching the bound Goal's ladder) in the run's final output.
      * Optionally, branches emit ``recommended_rung_claim_evidence_url``
        and ``recommended_rung_claim_evidence_note`` for the supporting
        evidence the gate machinery requires. Either field can be
        overridden by the caller of this action.

    Required kwargs:
      * ``run_id`` — the completed run to read the recommendation from.

    Optional kwargs:
      * ``evidence_url`` — overrides any branch-supplied evidence URL.
      * ``evidence_note`` — overrides any branch-supplied evidence note.
      * ``force`` — passthrough to the underlying ``gates.claim``.

    Failure shapes (``status="rejected"``):
      * ``run_not_found`` — no run row for ``run_id``.
      * ``run_not_completed`` — run.status is not ``completed``.
      * ``branch_not_found`` — run references a deleted branch.
      * ``branch_not_bound_to_goal`` — branch has no ``goal_id``.
      * ``missing_recommended_rung_claim`` — run output has no
        ``recommended_rung_claim`` (or it's empty / non-string).
      * ``unknown_rung`` — the recommended rung is not in the bound
        Goal's ladder; ``available_rungs`` lists what the branch
        SHOULD have emitted.
      * ``missing_evidence_url`` — neither the branch nor the caller
        supplied an evidence URL.
      * Any other ``status`` shape returned by ``_action_gates_claim``
        propagates verbatim (e.g. ``branch_rebound``,
        ``local_edit_conflict``).
    """
    from tinyassets.daemon_server import (
        get_branch_definition,
        get_goal_ladder,
    )
    from tinyassets.runs import RUN_STATUS_COMPLETED, get_run

    rid = (kwargs.get("run_id") or "").strip()
    if not rid:
        return json.dumps({
            "status": "rejected",
            "error": "run_id is required for claim_from_branch_run.",
        })

    run = get_run(_base_path(), rid)
    if run is None:
        return json.dumps({
            "status": "rejected",
            "error": "run_not_found",
            "run_id": rid,
        })

    if run.get("status") != RUN_STATUS_COMPLETED:
        return json.dumps({
            "status": "rejected",
            "error": "run_not_completed",
            "run_id": rid,
            "run_status": run.get("status"),
            "hint": (
                "Only runs in 'completed' status can claim a rung. Re-run the "
                "branch or check it later with read_graph target=\"run\" "
                f'run_id="{rid}" before claiming.'
            ),
        })

    bid = (run.get("branch_def_id") or "").strip()
    if not bid:
        # Defensive — runs always carry a branch_def_id; included so the
        # response is debuggable if the schema ever drifts.
        return json.dumps({
            "status": "rejected",
            "error": "branch_not_found",
            "hint": "Run row is missing branch_def_id.",
        })
    try:
        branch = get_branch_definition(_base_path(), branch_def_id=bid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": "branch_not_found",
            "branch_def_id": bid,
        })

    goal_id = (branch.get("goal_id") or "").strip()
    if not goal_id:
        return json.dumps({
            "status": "rejected",
            "error": "branch_not_bound_to_goal",
            "branch_def_id": bid,
            "hint": (
                "Goal binding is not exposed by the advertised handles; ask "
                "an operator to bind the branch before claiming a rung."
            ),
        })

    output = run.get("output") or {}
    if not isinstance(output, dict):
        output = {}
    raw_rung = output.get("recommended_rung_claim")
    rung_key = raw_rung.strip() if isinstance(raw_rung, str) else ""
    if not rung_key:
        return json.dumps({
            "status": "rejected",
            "error": "missing_recommended_rung_claim",
            "run_id": rid,
            "branch_def_id": bid,
            "goal_id": goal_id,
            "hint": (
                "The branch must emit a non-empty string field named "
                "'recommended_rung_claim' in the run's final output. "
                "The value must match a rung_key in the bound Goal's "
                "ladder; inspect the Goal with read_graph target=\"goal\" "
                f'goal_id="{goal_id}".'
            ),
        })

    try:
        ladder = get_goal_ladder(_base_path(), goal_id=goal_id)
    except KeyError:
        # Branch was bound to a now-deleted Goal. Surface explicitly.
        return json.dumps({
            "status": "rejected",
            "error": "branch_not_bound_to_goal",
            "branch_def_id": bid,
            "goal_id": goal_id,
            "hint": (
                "Branch references a Goal that no longer exists. "
                "Goal rebinding is not exposed by the advertised handles."
            ),
        })
    available_rungs = [
        r.get("rung_key") for r in ladder if r.get("rung_key")
    ]
    if rung_key not in available_rungs:
        return json.dumps({
            "status": "rejected",
            "error": "unknown_rung",
            "run_id": rid,
            "branch_def_id": bid,
            "goal_id": goal_id,
            "recommended_rung_claim": rung_key,
            "available_rungs": available_rungs,
            "hint": (
                "The branch's recommended_rung_claim does not match "
                "any rung in the bound Goal's ladder. Branch authors "
                "must emit a rung_key from the ladder vocabulary."
            ),
        })

    # Evidence resolution: caller override > branch-supplied output >
    # internal run handle. A completed run is valid local evidence even
    # when no public artifact URL exists yet.
    evidence_url = (kwargs.get("evidence_url") or "").strip()
    if not evidence_url:
        branch_url = output.get("recommended_rung_claim_evidence_url")
        if isinstance(branch_url, str):
            evidence_url = branch_url.strip()
    if not evidence_url:
        evidence_url = f"workflow:run:{rid}"

    evidence_note = (kwargs.get("evidence_note") or "").strip()
    if not evidence_note:
        branch_note = output.get("recommended_rung_claim_evidence_note")
        if isinstance(branch_note, str):
            evidence_note = branch_note.strip()
    if not evidence_note:
        # Default note ties the claim back to the run so a downstream
        # auditor can trace the chain. Non-empty by construction so
        # the gates.claim handler never sees an empty note from this
        # path — branch-authored notes still win when present.
        evidence_note = f"Auto-claim from completed run {rid}"

    claim_kwargs: dict[str, Any] = {
        "branch_def_id": bid,
        "rung_key": rung_key,
        "evidence_url": evidence_url,
        "evidence_note": evidence_note,
        "conformance_pack_id": (
            kwargs.get("conformance_pack_id")
            or output.get("conformance_pack_id")
            or ""
        ),
        "force": bool(kwargs.get("force", False)),
    }
    response_json = _action_gates_claim(claim_kwargs)
    try:
        response = json.loads(response_json)
    except (TypeError, ValueError):
        # Defensive — the inner handler should always return JSON.
        return response_json
    # Decorate the response so the caller sees the run origin and the
    # rung that was claimed, without losing any of the existing
    # gates.claim fields (status, claim, hints, error structure).
    response.setdefault("run_id", rid)
    response.setdefault("source", "claim_from_branch_run")
    if response.get("status") == "claimed":
        # Surface the rung that was claimed at top level so the chatbot
        # can render "Branch X reached rung Y" without parsing the
        # nested claim dict.
        response.setdefault("rung_key", rung_key)
    return json.dumps(response, default=str)


def _action_gates_retract(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.catalog.layout import slugify
    from tinyassets.daemon_server import (
        CAP_RETRACT_GATE_CLAIM,
        get_branch_definition,
        get_gate_claim,
        get_goal,
    )
    from tinyassets.identity import git_author

    bid = (kwargs.get("branch_def_id") or "").strip()
    rung_key = (kwargs.get("rung_key") or "").strip()
    reason = (kwargs.get("reason") or "").strip()
    force = bool(kwargs.get("force", False))
    if not (bid and rung_key):
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id and rung_key are required for retract.",
        })
    if not reason:
        return json.dumps({
            "status": "rejected",
            "error": "reason is required for retract (non-empty).",
        })
    _ensure_workflow_db()
    existing = get_gate_claim(
        _base_path(), branch_def_id=bid, rung_key=rung_key,
    )
    if existing is None:
        return json.dumps({
            "status": "rejected",
            "error": "claim_not_found",
            "message": (
                f"No claim exists for branch '{bid}' at rung "
                f"'{rung_key}'."
            ),
        })
    if existing.get("retracted_at"):
        # Idempotent: a second retract on an already-retracted claim is
        # a no-op return, not a fresh write. Keeps owners from churning
        # retracted_at timestamps. No YAML rewrite either.
        return json.dumps({
            "status": "already_retracted",
            "claim": existing,
        }, default=str)
    actor = _current_actor_or_anon()
    # Owner-retract: original claimant, Goal author, or explicit action grant.
    claimed_by = existing.get("claimed_by") or ""
    goal_author = ""
    goal_id = existing.get("goal_id") or ""
    goal_name = ""
    if goal_id:
        try:
            goal = get_goal(_base_path(), goal_id=goal_id)
            goal_author = goal.get("author") or ""
            goal_name = goal.get("name") or ""
        except KeyError:
            pass
    allowed = {actor_id for actor_id in (claimed_by, goal_author) if actor_id}
    if actor not in allowed and not _current_actor_has_capability(
        CAP_RETRACT_GATE_CLAIM,
    ):
        return json.dumps({
            "status": "rejected",
            "error": (
                "Only the claim author, Goal owner, or an actor with "
                f"{CAP_RETRACT_GATE_CLAIM!r} can retract "
                f"(claimant: '{claimed_by}', goal owner: "
                f"'{goal_author}')."
            ),
        })
    # Verify branch still exists (defensive; claim-time check is in claim).
    try:
        branch = get_branch_definition(_base_path(), branch_def_id=bid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Branch '{bid}' not found.",
        })
    goal_slug = slugify(goal_name or goal_id)
    branch_slug = slugify(branch.get("name") or bid)
    try:
        saved, _commit = _storage_backend().retract_gate_claim_and_commit(
            branch_def_id=bid,
            rung_key=rung_key,
            reason=reason,
            goal_slug=goal_slug,
            branch_slug=branch_slug,
            author=git_author(_current_actor()),
            message=f"gates.retract: {goal_slug}/{branch_slug}@{rung_key}",
            force=force,
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    return json.dumps({
        "status": "retracted",
        "claim": saved,
    }, default=str)


_LIST_CLAIMS_LIMIT_CAP = 500


def _action_gates_list_claims(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _filter_claims_by_branch_visibility,
    )
    from tinyassets.daemon_server import get_goal, list_gate_claims

    bid = (kwargs.get("branch_def_id") or "").strip()
    gid = (kwargs.get("goal_id") or "").strip()
    if bool(bid) == bool(gid):
        return json.dumps({
            "status": "rejected",
            "error": (
                "list_claims requires exactly one of branch_def_id "
                "or goal_id."
            ),
            "available_filters": ["branch_def_id", "goal_id"],
        })
    include_retracted = bool(kwargs.get("include_retracted", False))
    limit = int(kwargs.get("limit", 50) or 50)
    limit = max(1, min(limit, _LIST_CLAIMS_LIMIT_CAP))
    _ensure_workflow_db()
    # Unknown goal_id is a hard reject (caller asked about a specific
    # ID). Unknown branch_def_id falls through to an empty result set,
    # matching `branch list` ergonomics.
    if gid:
        try:
            get_goal(_base_path(), goal_id=gid)
        except KeyError:
            return json.dumps({
                "status": "rejected",
                "error": f"Goal '{gid}' not found.",
            })
    try:
        claims = list_gate_claims(
            _base_path(),
            branch_def_id=bid,
            goal_id=gid,
            include_retracted=include_retracted,
            public_only=True,
            limit=limit,
        )
    except ValueError as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})
    # Phase 6.2.2 — hide private-Branch claims from non-owners.
    claims = _filter_claims_by_branch_visibility(
        claims, viewer=_current_actor(),
    )
    return json.dumps({
        "status": "ok",
        "filter": {
            "branch_def_id": bid,
            "goal_id": gid,
            "include_retracted": include_retracted,
        },
        "claims": claims,
        "count": len(claims),
    }, default=str)


def _action_gates_leaderboard(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.api.engine_helpers import (
        _current_actor,
        _filter_leaderboard_by_branch_visibility,
    )
    from tinyassets.daemon_server import (
        gates_leaderboard,
        get_goal_ladder,
    )

    gid = (kwargs.get("goal_id") or "").strip()
    if not gid:
        return json.dumps({
            "status": "rejected",
            "error": "goal_id is required for leaderboard.",
        })
    _ensure_workflow_db()
    try:
        goal = _get_public_goal(gid)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{gid}' not found.",
        })
    ladder = get_goal_ladder(_base_path(), goal_id=gid)
    if not ladder:
        return json.dumps({
            "status": "ok",
            "goal_id": gid,
            "goal_name": goal.get("name", ""),
            "entries": [],
            "count": 0,
            "note": "Goal has no ladder defined.",
        }, default=str)
    limit = int(kwargs.get("limit", 50) or 50)
    entries = gates_leaderboard(_base_path(), goal_id=gid, limit=limit)
    # Phase 6.2.2 — hide private-Branch entries from non-owners.
    entries = _filter_leaderboard_by_branch_visibility(
        entries, viewer=_current_actor(),
    )
    return json.dumps({
        "status": "ok",
        "goal_id": gid,
        "goal_name": goal.get("name", ""),
        "entries": entries,
        "count": len(entries),
    }, default=str)


def _action_gates_stake_bonus(kwargs: dict[str, Any]) -> str:
    """Lock a bonus stake on an existing gate claim.

    Requires: claim_id, bonus_stake (int), node_id.
    Rejected when TINYASSETS_PAID_MARKET is off, claim is retracted,
    or claim already has a bonus staked.
    """
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.gates.actions import stake_bonus, validate_stake_amount
    from tinyassets.producers.node_bid import paid_market_enabled

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": (
                "Gate bonuses require TINYASSETS_PAID_MARKET=on. "
                "Enable the paid-market flag to use bonus staking."
            ),
        })

    claim_id = (kwargs.get("claim_id") or "").strip()
    node_id = (kwargs.get("node_id") or "").strip()
    attachment_scope = (kwargs.get("attachment_scope") or "node").strip()
    raw_stake = kwargs.get("bonus_stake", 0)

    if not claim_id:
        return json.dumps({"status": "rejected", "error": "claim_id is required."})
    if not node_id:
        return json.dumps({"status": "rejected", "error": "node_id is required."})

    stake, err = validate_stake_amount(raw_stake)
    if err:
        return json.dumps({"status": "rejected", "error": err})
    if stake == 0:
        return json.dumps({
            "status": "rejected",
            "error": "bonus_stake must be > 0 to stake a bonus.",
        })

    _ensure_workflow_db()
    from tinyassets.gates.actions import ensure_bonus_columns
    from tinyassets.gates.schema import GateBonusClaim
    from tinyassets.storage import _connect as _storage_connect
    with _storage_connect(_base_path()) as conn:
        # Authorization (slice1a review CRITICAL — round 4): staking a bonus
        # mutates an existing gate claim, and its refund leg always returns to
        # the recorded claimer (``claimed_by``). Only the claim owner or the
        # configured host may stake a bonus on a claim — a write-scoped caller
        # who merely knows a ``claim_id`` must not be able to attach a stake to
        # another actor's claim. Mirrors the ``unstake_bonus`` staker check.
        ensure_bonus_columns(conn)
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            return json.dumps({
                "status": "rejected",
                "error": f"Claim '{claim_id}' not found.",
            })
        claim = GateBonusClaim.from_row(row)
        actor = _current_actor_or_anon()
        recorded_claimer = (claim.claimed_by or "").strip()
        host_user = _escrow_host_user()
        if actor != recorded_claimer and actor != host_user:
            return json.dumps({
                "status": "rejected",
                "error": (
                    "Cross-actor bonus staking is not permitted: only the claim "
                    f"owner ('{recorded_claimer}') or the host ('{host_user}') "
                    f"may stake a bonus on claim '{claim_id}'. Authenticated "
                    f"actor '{actor}' is not authorized."
                ),
            })
        # The bonus belongs to the claim owner-of-record, recorded immutably as
        # bonus_staker_id; even when the host initiates the stake, ownership
        # stays with the claimer so a later re-claim cannot transfer it.
        result = stake_bonus(
            conn,
            claim_id=claim_id,
            bonus_stake=stake,
            node_id=node_id,
            staker_id=recorded_claimer,
            attachment_scope=attachment_scope,
        )
    return json.dumps(result, default=str)


def _action_gates_unstake_bonus(kwargs: dict[str, Any]) -> str:
    """Remove a bonus stake from a gate claim (refund to original staker).

    Requires: claim_id. Only the original staker can unstake.
    """
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.gates.actions import unstake_bonus
    from tinyassets.producers.node_bid import paid_market_enabled

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Gate bonuses require TINYASSETS_PAID_MARKET=on.",
        })

    claim_id = (kwargs.get("claim_id") or "").strip()
    if not claim_id:
        return json.dumps({"status": "rejected", "error": "claim_id is required."})

    actor = _current_actor_or_anon()
    _ensure_workflow_db()
    from tinyassets.storage import _connect as _storage_connect
    with _storage_connect(_base_path()) as conn:
        result = unstake_bonus(conn, claim_id=claim_id, actor=actor)
    return json.dumps(result, default=str)


def _action_gates_release_bonus(kwargs: dict[str, Any]) -> str:
    """Release or refund a bonus based on an evaluator verdict.

    Requires: claim_id, eval_verdict ("pass"|"fail"|"skip"),
    node_last_claimer (who gets the payout on pass).
    Rejected when no verdict supplied or bonus_stake is 0.

    Authorization (slice1a review CRITICAL — round 3): releasing/refunding a gate
    bonus moves staked value. Only the legitimate gate-outcome authority may do
    it — the Goal owner (who owns the ladder that defines the gate), the
    configured host (``UNIVERSE_SERVER_HOST_USER``), or an actor holding the
    gate-claim capability (``CAP_RETRACT_GATE_CLAIM``). An arbitrary write-scoped
    caller who knows a ``claim_id`` cannot settle another actor's bonus to a
    caller-supplied recipient, nor refund another actor's stake to themselves.
    """
    from tinyassets.api.branches import _ensure_workflow_db
    from tinyassets.daemon_server import CAP_RETRACT_GATE_CLAIM, get_goal
    from tinyassets.gates.actions import release_bonus
    from tinyassets.gates.schema import GateBonusClaim
    from tinyassets.producers.node_bid import paid_market_enabled

    if not paid_market_enabled():
        return json.dumps({
            "status": "not_available",
            "error": "Gate bonuses require TINYASSETS_PAID_MARKET=on.",
        })

    claim_id = (kwargs.get("claim_id") or "").strip()
    eval_verdict = (kwargs.get("eval_verdict") or "").strip()
    node_last_claimer = (kwargs.get("node_last_claimer") or "").strip()

    if not claim_id:
        return json.dumps({"status": "rejected", "error": "claim_id is required."})
    if not eval_verdict:
        return json.dumps({
            "status": "rejected",
            "error": (
                "eval_verdict is required ('pass', 'fail', or 'skip'). "
                "Use the Evaluator protocol to obtain a verdict before releasing."
            ),
        })
    if not node_last_claimer:
        return json.dumps({
            "status": "rejected",
            "error": (
                "node_last_claimer is required "
                "(the actor holding the node at gate-pass time)."
            ),
        })

    _ensure_workflow_db()
    from tinyassets.storage import _connect as _storage_connect
    with _storage_connect(_base_path()) as conn:
        # Load the claim BEFORE any disbursement so authorization can be checked
        # against recorded state (goal owner, recorded staker), never against
        # caller-supplied identities.
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            return json.dumps({
                "status": "rejected",
                "error": f"Claim '{claim_id}' not found.",
            })
        claim = GateBonusClaim.from_row(row)

        # Authorization: only the gate-outcome authority may release/refund.
        actor = _current_actor_or_anon()
        goal_owner = ""
        if claim.goal_id:
            try:
                goal = get_goal(_base_path(), goal_id=claim.goal_id)
                goal_owner = (goal.get("author") or "").strip()
            except KeyError:
                goal_owner = ""
        host_user = _escrow_host_user()
        allowed = {a for a in (goal_owner, host_user) if a}
        if actor not in allowed and not _current_actor_has_capability(
            CAP_RETRACT_GATE_CLAIM,
        ):
            return json.dumps({
                "status": "rejected",
                "error": (
                    "Cross-actor bonus release is not permitted: only the Goal "
                    f"owner ('{goal_owner}'), the host ('{host_user}'), or an "
                    f"actor with {CAP_RETRACT_GATE_CLAIM!r} can release/refund a "
                    f"gate bonus. Authenticated actor '{actor}' is not the "
                    "gate-outcome authority for this claim."
                ),
            })

        # The refund recipient is always the IMMUTABLE recorded staker — pass it
        # as an assertion so release_bonus rejects any mismatch (defense in
        # depth). It must be bonus_staker_id, NOT claimed_by: a gate re-claim
        # rewrites claimed_by, so asserting claimed_by would make the legitimate
        # owner/host release fail against the immutable staker and strand the
        # bonus (round-6 finding). Falls back to claimed_by for pre-migration rows.
        assertion_staker = (claim.bonus_staker_id or claim.claimed_by or "").strip()
        result = release_bonus(
            conn,
            claim_id=claim_id,
            eval_verdict=eval_verdict,
            node_last_claimer=node_last_claimer,
            staker=assertion_staker,
        )
    return json.dumps(result, default=str)


def _action_attest_gate_event(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.gate_events import attest_gate_event
    from tinyassets.principals import named_principal

    goal_id = (kwargs.get("goal_id") or "").strip()
    event_type = (kwargs.get("event_type") or "").strip()
    event_date = (kwargs.get("event_date") or "").strip()
    attested_by = named_principal(kwargs.get("attested_by")) or _current_actor()
    notes = (kwargs.get("note") or "").strip()
    cites_raw = (kwargs.get("cites_json") or "[]").strip()
    try:
        cites = json.loads(cites_raw) if cites_raw else []
        if not isinstance(cites, list):
            return json.dumps({"error": "cites_json must be a JSON array."})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"cites_json is not valid JSON: {exc}"})
    try:
        evt = attest_gate_event(
            _base_path(),
            goal_id=goal_id,
            event_type=event_type,
            event_date=event_date,
            attested_by=attested_by,
            cites=cites,
            notes=notes,
        )
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"status": "attested", "event_id": evt.event_id,
                       "goal_id": evt.goal_id, "event_type": evt.event_type,
                       "event_date": evt.event_date, "attested_by": evt.attested_by,
                       "verification_status": evt.verification_status,
                       "cite_count": len(evt.cites)})


def _action_verify_gate_event(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.gate_events import verify_gate_event
    from tinyassets.principals import named_principal

    event_id = (kwargs.get("event_id") or "").strip()
    verifier_id = named_principal(kwargs.get("verifier_id")) or _current_actor()
    if not event_id:
        return json.dumps({"error": "event_id is required."})
    try:
        evt = verify_gate_event(_base_path(), event_id=event_id, verifier_id=verifier_id)
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"status": "verified", "event_id": evt.event_id,
                       "verification_status": evt.verification_status,
                       "verified_by": evt.verified_by})


def _action_dispute_gate_event(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.gate_events.store import dispute_gate_event
    from tinyassets.principals import named_principal

    event_id = (kwargs.get("event_id") or "").strip()
    disputed_by = named_principal(kwargs.get("disputed_by")) or _current_actor()
    reason = (kwargs.get("reason") or "").strip()
    if not event_id:
        return json.dumps({"error": "event_id is required."})
    try:
        evt = dispute_gate_event(_base_path(), event_id=event_id,
                                 disputed_by=disputed_by, reason=reason)
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"status": "disputed", "event_id": evt.event_id,
                       "verification_status": evt.verification_status})


def _action_retract_gate_event(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.gate_events.store import retract_gate_event
    from tinyassets.principals import named_principal

    event_id = (kwargs.get("event_id") or "").strip()
    retracted_by = named_principal(kwargs.get("retracted_by")) or _current_actor()
    note = (kwargs.get("note") or "").strip()
    if not event_id:
        return json.dumps({"error": "event_id is required."})
    try:
        evt = retract_gate_event(_base_path(), event_id=event_id,
                                 retracted_by=retracted_by, note=note)
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"status": "retracted", "event_id": evt.event_id,
                       "verification_status": evt.verification_status})


def _action_get_gate_event(kwargs: dict[str, Any]) -> str:
    from tinyassets.gate_events.store import get_gate_event

    event_id = (kwargs.get("event_id") or "").strip()
    if not event_id:
        return json.dumps({"error": "event_id is required."})
    evt = get_gate_event(_base_path(), event_id)
    if evt is None:
        return json.dumps({"error": f"event_id '{event_id}' not found."})
    try:
        _get_public_goal(evt.goal_id)
    except KeyError:
        return json.dumps({"error": f"event_id '{event_id}' not found."})
    return json.dumps({
        "event_id": evt.event_id,
        "goal_id": evt.goal_id,
        "event_type": evt.event_type,
        "event_date": evt.event_date,
        "attested_by": evt.attested_by,
        "attested_at": evt.attested_at,
        "verification_status": evt.verification_status,
        "verified_by": evt.verified_by,
        "notes": evt.notes,
        "cites": [
            {"branch_version_id": c.branch_version_id, "run_id": c.run_id,
             "contribution_summary": c.contribution_summary}
            for c in evt.cites
        ],
    }, default=str)


def _action_list_gate_events(kwargs: dict[str, Any]) -> str:
    from tinyassets.gate_events.store import list_gate_events

    goal_id = (kwargs.get("goal_id") or "").strip()
    bvid = (kwargs.get("branch_version_id") or "").strip()
    limit = min(max(1, int(kwargs.get("limit") or 50)), 500)
    include_retracted = bool(kwargs.get("include_retracted", True))
    public_goal_ids = _public_goal_ids()
    events = list_gate_events(
        _base_path(),
        goal_id=goal_id,
        branch_version_id=bvid,
        include_retracted=include_retracted,
        public_goal_ids=public_goal_ids,
        limit=limit,
    )
    return json.dumps({
        "goal_id": goal_id,
        "count": len(events),
        "events": [
            {"event_id": e.event_id, "event_type": e.event_type,
             "event_date": e.event_date, "attested_by": e.attested_by,
             "verification_status": e.verification_status,
             "cite_count": len(e.cites)}
            for e in events
        ],
    }, default=str)


def _action_record_conformance_pack(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.conformance_packs import record_conformance_pack
    from tinyassets.daemon_server import get_branch_definition, get_goal

    goal_id = (kwargs.get("goal_id") or "").strip()
    branch_def_id = (kwargs.get("branch_def_id") or "").strip()
    pack_raw = (kwargs.get("conformance_pack_json") or "").strip()
    target_rung = (kwargs.get("rung_key") or "").strip()
    if not pack_raw:
        return json.dumps({
            "status": "rejected",
            "error": "conformance_pack_json is required.",
        })
    try:
        pack_payload = json.loads(pack_raw)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"conformance_pack_json is not valid JSON: {exc}",
        })
    if not isinstance(pack_payload, dict):
        return json.dumps({
            "status": "rejected",
            "error": "conformance_pack_json must be a JSON object.",
        })
    if not goal_id:
        return json.dumps({"status": "rejected", "error": "goal_id is required."})
    try:
        get_goal(_base_path(), goal_id=goal_id)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": f"Goal '{goal_id}' not found.",
        })
    if branch_def_id:
        try:
            branch = get_branch_definition(_base_path(), branch_def_id=branch_def_id)
        except KeyError:
            return json.dumps({
                "status": "rejected",
                "error": f"Branch '{branch_def_id}' not found.",
            })
        if (branch.get("goal_id") or "") != goal_id:
            return json.dumps({
                "status": "rejected",
                "error": "branch_goal_mismatch",
                "goal_id": goal_id,
                "branch_goal_id": branch.get("goal_id") or "",
            })
    try:
        pack = record_conformance_pack(
            _base_path(),
            goal_id=goal_id,
            branch_def_id=branch_def_id,
            target_rung=target_rung,
            pack=pack_payload,
            created_by=_current_actor(),
        )
    except ValueError as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})
    return json.dumps({
        "status": "recorded",
        "conformance_pack": pack.to_dict(),
    }, default=str)


def _action_get_conformance_pack(kwargs: dict[str, Any]) -> str:
    from tinyassets.conformance_packs import get_conformance_pack

    pack_id = (kwargs.get("conformance_pack_id") or "").strip()
    if not pack_id:
        return json.dumps({
            "status": "rejected",
            "error": "conformance_pack_id is required.",
        })
    pack = get_conformance_pack(_base_path(), pack_id)
    if pack is None:
        return json.dumps({
            "status": "rejected",
            "error": "conformance_pack_not_found",
            "conformance_pack_id": pack_id,
        })
    try:
        _get_public_goal(pack.goal_id)
    except KeyError:
        return json.dumps({
            "status": "rejected",
            "error": "conformance_pack_not_found",
            "conformance_pack_id": pack_id,
        })
    return json.dumps({
        "status": "ok",
        "conformance_pack": pack.to_dict(),
    }, default=str)


def _action_list_conformance_packs(kwargs: dict[str, Any]) -> str:
    from tinyassets.conformance_packs import list_conformance_packs

    requested_limit = min(max(1, int(kwargs.get("limit") or 50)), 500)
    public_goal_ids = _public_goal_ids()
    records = list_conformance_packs(
        _base_path(),
        goal_id=(kwargs.get("goal_id") or "").strip(),
        branch_def_id=(kwargs.get("branch_def_id") or "").strip(),
        standard_id=(kwargs.get("standard_id") or "").strip(),
        public_goal_ids=public_goal_ids,
        limit=requested_limit,
    )
    return json.dumps({
        "status": "ok",
        "count": len(records),
        "conformance_packs": [record.to_dict() for record in records],
    }, default=str)


_GATE_EVENT_ACTIONS: dict[str, Any] = {
    "attest_gate_event": _action_attest_gate_event,
    "verify_gate_event": _action_verify_gate_event,
    "dispute_gate_event": _action_dispute_gate_event,
    "retract_gate_event": _action_retract_gate_event,
    "get_gate_event": _action_get_gate_event,
    "list_gate_events": _action_list_gate_events,
}


def _action_gates_list(_kwargs: dict[str, Any]) -> str:
    return json.dumps({
        "status": "ok",
        "tool": "gates",
        "available_actions": sorted(_GATES_ACTIONS.keys()),
        "gate_event_actions": sorted(_GATE_EVENT_ACTIONS.keys()),
    })


_GATES_ACTIONS: dict[str, Any] = {
    "list": _action_gates_list,
    "define_ladder": _action_gates_define_ladder,
    "get_ladder": _action_gates_get_ladder,
    "claim": _action_gates_claim,
    "claim_from_branch_run": _action_gates_claim_from_branch_run,
    "record_conformance_pack": _action_record_conformance_pack,
    "get_conformance_pack": _action_get_conformance_pack,
    "list_conformance_packs": _action_list_conformance_packs,
    "retract": _action_gates_retract,
    "list_claims": _action_gates_list_claims,
    "leaderboard": _action_gates_leaderboard,
    "stake_bonus": _action_gates_stake_bonus,
    "unstake_bonus": _action_gates_unstake_bonus,
    "release_bonus": _action_gates_release_bonus,
}
_GATES_WRITE_ACTIONS: frozenset[str] = frozenset({
    "define_ladder",
    "claim",
    "claim_from_branch_run",
    "record_conformance_pack",
    "retract",
    "stake_bonus",
    "unstake_bonus",
    "release_bonus",
})


def _gates_scope_error(action: str) -> str | None:
    from tinyassets.auth.middleware import require_action_scope
    from tinyassets.auth.provider import PermissionScope

    try:
        require_action_scope(
            "gates",
            action,
            scope=PermissionScope(resource_type="outcome-gate", resource_id=action),
        )
    except PermissionError as exc:
        return json.dumps({
            "status": "rejected",
            "error": str(exc),
            "auth_scope_required": True,
            "tool": "gates",
            "action": action,
        })
    return None


def gates(
    action: str,
    goal_id: str = "",
    branch_def_id: str = "",
    rung_key: str = "",
    ladder: str = "",
    evidence_url: str = "",
    evidence_note: str = "",
    reason: str = "",
    include_retracted: bool = False,
    limit: int = 50,
    force: bool = False,
    claim_id: str = "",
    bonus_stake: int = 0,
    attachment_scope: str = "node",
    eval_verdict: str = "",
    node_last_claimer: str = "",
    node_id: str = "",
    run_id: str = "",
    conformance_pack_json: str = "",
    conformance_pack_id: str = "",
    standard_id: str = "",
) -> str:
    """Outcome Gates — real-world impact claims per Branch.

    Each Goal declares a ladder of rungs (draft → peer-reviewed → published
    → cited → breakthrough). Branches self-report which rungs they've
    reached, with an evidence URL.

    All actions require GATES_ENABLED=1 on the server; the tool returns
    {"status": "not_available"} when the flag is off. Bonus actions
    additionally require TINYASSETS_PAID_MARKET=on.

    Actions (all live when GATES_ENABLED=1):
      list          Discover supported gates actions.
      define_ladder Owner sets the rung list on a Goal. Needs goal_id
                    and `ladder` (JSON list of {rung_key, name,
                    description}).
      get_ladder    Read a Goal's ladder. Needs goal_id.
      record_conformance_pack
                    Store a standards/readiness conformance pack for a
                    Goal or Branch before a gated rung claim.
      claim         Report a rung reached. Needs branch_def_id,
                    rung_key, evidence_url. Idempotent on (branch, rung).
      claim_from_branch_run
                    Claim a rung whose key + (optionally) evidence URL
                    came from a completed run's final output state.
                    Needs run_id. The branch's
                    ``recommended_rung_claim`` output field selects the
                    rung; this action validates it against the bound
                    Goal's ladder before delegating to ``claim``. PR-126
                    M5 (Loop 1 retirement roadmap).
      retract       Soft-delete a claim. Needs branch_def_id, rung_key,
                    reason. Claim author, Goal owner, or host can
                    retract.
      list_claims   Browse claims. Provide exactly one of branch_def_id
                    or goal_id. `include_retracted` optional; claims
                    whose rung no longer exists in the Goal's ladder
                    are tagged `orphaned`.
      leaderboard   Rank Branches bound to a Goal by highest rung
                    reached. Tiebreak: earliest claim wins. Also
                    callable as `goals leaderboard metric=outcome`.

    Bonus actions (live when GATES_ENABLED=1 + TINYASSETS_PAID_MARKET=on):
      stake_bonus   Lock a bonus stake on a claim. Needs claim_id,
                    bonus_stake (int > 0), node_id. attachment_scope
                    controls what the stake attaches to ("node" default).
      unstake_bonus Remove a bonus stake and refund the staker. Needs
                    claim_id. Only the original staker can unstake.
      release_bonus Resolve a bonus payout via evaluator verdict. Needs
                    claim_id, eval_verdict ("pass"|"fail"|"skip"),
                    node_last_claimer (recipient on pass). Only the Goal
                    owner, the host, or an actor with the gate-claim
                    capability may release/refund — refunds always return
                    to the recorded staker.

    Evidence URL must be http(s) with a host or a TinyAssets run
    evidence handle such as ``workflow:run:<run_id>``; content is not
    fetched (local-first). Social accountability handles fraud in v1.

    Args:
      action: see Actions above.
      goal_id: Goal target for ladder / leaderboard / list_claims.
      branch_def_id: Branch that's claiming / retracting / listing.
      rung_key: matches a ladder entry's rung_key.
      ladder: JSON list string for define_ladder.
      evidence_url: http(s) URL or TinyAssets run evidence handle
                    pointing at the claim's evidence.
      evidence_note: optional human summary.
      reason: retract reason (required for retract, non-empty).
      include_retracted: list_claims filter (default False).
      limit: cap for leaderboard / list_claims.
      force: bypass the dirty-file guard on the target YAML when a
             user has uncommitted local edits. Same ergonomics as
             `goals propose/update/bind` and `branch` mutations.
      claim_id: target claim for stake_bonus / unstake_bonus /
                release_bonus.
      bonus_stake: integer token amount for stake_bonus (must be > 0).
                   Harmless when TINYASSETS_PAID_MARKET is off — the
                   action returns not_available before reading this arg.
      attachment_scope: what the stake attaches to; "node" (default)
                        or "branch". Used by stake_bonus.
      eval_verdict: evaluator decision for release_bonus —
                    "pass" (payout to node_last_claimer), "fail"
                    (refund to staker), or "skip" (no-op).
      node_last_claimer: actor_id of the node fulfiller who receives
                         payout on a "pass" release_bonus verdict.
      node_id: node target for stake_bonus.
      run_id: completed-run target for claim_from_branch_run; the run's
              final-state ``recommended_rung_claim`` selects the rung
              and (optionally) the supporting evidence URL. When no
              evidence URL is supplied, the claim uses
              ``workflow:run:<run_id>``.
      conformance_pack_json: JSON object for record_conformance_pack.
      conformance_pack_id: ready conformance pack supporting a claim.
      standard_id: optional list_conformance_packs filter.
    """
    from tinyassets.api.engine_helpers import (
        _format_dirty_file_conflict,
    )
    from tinyassets.api.market import _gates_enabled

    if not _gates_enabled():
        return json.dumps({
            "status": "not_available",
            "error": (
                "Outcome gates are gated by the GATES_ENABLED flag "
                "(Phase 6.1). Set GATES_ENABLED=1 on the TinyAssets Server "
                "to opt in."
            ),
        })
    handler = _GATES_ACTIONS.get(action)
    if handler is None:
        return json.dumps({
            "status": "rejected",
            "error": f"Unknown action '{action}'.",
            "available_actions": sorted(_GATES_ACTIONS.keys()),
        })
    scope_error = _gates_scope_error(action)
    if scope_error is not None:
        return scope_error
    if action not in _GATES_WRITE_ACTIONS and goal_id:
        try:
            _get_public_goal(goal_id)
        except KeyError:
            return json.dumps({
                "status": "rejected",
                "error": f"Goal '{goal_id}' not found.",
            })
    kwargs: dict[str, Any] = {
        "goal_id": goal_id,
        "branch_def_id": branch_def_id,
        "rung_key": rung_key,
        "ladder": ladder,
        "evidence_url": evidence_url,
        "evidence_note": evidence_note,
        "reason": reason,
        "include_retracted": include_retracted,
        "limit": limit,
        "force": force,
        "claim_id": claim_id,
        "bonus_stake": bonus_stake,
        "attachment_scope": attachment_scope,
        "eval_verdict": eval_verdict,
        "node_last_claimer": node_last_claimer,
        "node_id": node_id,
        "run_id": run_id,
        "conformance_pack_json": conformance_pack_json,
        "conformance_pack_id": conformance_pack_id,
        "standard_id": standard_id,
    }
    try:
        return handler(kwargs)
    except DirtyFileError as exc:
        # Phase 6.3: dirty-file guard surfaces as the structured
        # local_edit_conflict payload (same shape as `goals` and
        # `branch` handlers). Chat-side renders the options;
        # force=True retries through.
        return json.dumps(_format_dirty_file_conflict(exc))
