"""Tier-aware BranchTask dispatcher.

Stateless selection function over ``branch_tasks.json``. Called
exactly twice per ``_run_graph`` invocation — once at daemon startup
(before first graph compile) and once at each cycle boundary (between
wrapper returns). No internal timer, no continuous polling loop.

Priority scoring starts with the minimum viable tier model:

    score = tier_weight[trigger_source]
          + recency_decay(queued_at)
          + user_boost(priority_weight)

Deferred terms (``bid``, ``goal_affinity``, ``cost_penalty``) are
zero. Their coefficients sit in :class:`DispatcherConfig` so later
market and goal-affinity work can wire them in without re-shaping
``score_task``.

The dispatcher reads the queue only. Producers run inside the graph's
review gates; the dispatcher does not invoke them.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.branch_tasks import BranchTask, append_task, read_queue

logger = logging.getLogger(__name__)

# Default tier weights for the dispatcher scoring contract.
_DEFAULT_TIER_WEIGHTS: dict[str, float] = {
    "host_request": 100.0,
    "operator_request": 100.0,
    "owner_queued": 80.0,
    "user_request": 60.0,
    "goal_pool": 40.0,
    "paid_bid": 50.0,
    "opportunistic": 10.0,
}


@dataclass
class DispatcherConfig:
    accept_external_requests: bool = True
    accept_goal_pool: bool = False
    accept_paid_bids: bool = False
    allow_opportunistic: bool = False
    tier_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_TIER_WEIGHTS),
    )
    recency_half_life_seconds: float = 86400.0
    pickup_signal_term_cap: float = 5.0
    bid_coefficient: float = 0.0
    bid_term_cap: float = 30.0
    goal_affinity_coefficient: float = 0.0
    cost_penalty_coefficient: float = 0.0
    served_llm_type: str = ""
    active_daemon_id: str = ""
    soul_affinity_coefficient: float = 0.0
    soul_affinity_term_cap: float = 8.0

    def tier_enabled(self, trigger_source: str) -> bool:
        if trigger_source in {
            "host_request",
            "operator_request",
            "owner_queued",
        }:
            return True
        if trigger_source == "user_request":
            return self.accept_external_requests
        if trigger_source == "goal_pool":
            return self.accept_goal_pool
        if trigger_source == "paid_bid":
            return self.accept_paid_bids
        if trigger_source == "opportunistic":
            return self.allow_opportunistic
        return False

    def tier_status_map(self) -> dict[str, str]:
        """Self-documenting status per tier."""
        return {
            "host_request": "live",
            "operator_request": "live",
            "user_request": (
                "live" if self.accept_external_requests else "disabled"
            ),
            "owner_queued": "live",
            "goal_pool": (
                "live" if self.accept_goal_pool else "stubbed"
            ),
            "paid_bid": (
                "live" if self.accept_paid_bids else "disabled"
            ),
            "opportunistic": (
                "live" if self.allow_opportunistic else "stubbed"
            ),
        }


def dispatcher_enabled() -> bool:
    """Read ``TINYASSETS_DISPATCHER_ENABLED``. Default on."""
    value = os.environ.get("TINYASSETS_DISPATCHER_ENABLED", "on")
    return value.strip().lower() not in {"off", "0", "false", "no"}


def paid_market_enabled() -> bool:
    """Read ``TINYASSETS_PAID_MARKET``. Default OFF."""
    value = os.environ.get("TINYASSETS_PAID_MARKET", "off")
    return value.strip().lower() in {"on", "1", "true", "yes"}


def get_request_type_priorities() -> list[str]:
    """Read ``TINYASSETS_REQUEST_TYPE_PRIORITIES``.

    Returns ordered list of request_type strings this daemon will claim.
    Empty list = accept all types (no filtering, backward-compatible default).
    Format: comma-separated, e.g. ``bug_investigation,paid_market,branch_run``.
    """
    raw = os.environ.get("TINYASSETS_REQUEST_TYPE_PRIORITIES", "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def prefers_request_type(request_type: str) -> bool:
    """Return True if this daemon will claim tasks of the given request_type.

    When ``TINYASSETS_REQUEST_TYPE_PRIORITIES`` is unset or empty, all
    types are accepted (backward-compatible). When set, only listed
    types are accepted.
    """
    priorities = get_request_type_priorities()
    if not priorities:
        return True
    return request_type in priorities


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def score_task(
    task: BranchTask,
    *,
    now_iso: str,
    config: DispatcherConfig,
) -> float:
    """Deterministic BranchTask priority score.

    ``tier_weight + recency_decay + user_boost``. Other coefficients
    multiply zero in v1 but remain wired for later market and goal terms.
    """
    tier = config.tier_weights.get(task.trigger_source, 0.0)

    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    queued = _parse_iso(task.queued_at) or now
    age_s = max(0.0, (now - queued).total_seconds())
    # Exponential decay: boost fresh tasks slightly; decays to 0.
    # Scale of 10 picked so recency tweak doesn't cross tier boundaries.
    half_life = max(1.0, config.recency_half_life_seconds)
    recency = 10.0 * math.exp(-age_s * math.log(2) / half_life)

    boost = max(0.0, float(task.priority_weight))
    pickup_signal = min(
        max(0.0, float(getattr(task, "pickup_signal_weight", 0.0))),
        config.pickup_signal_term_cap,
    )

    # Paid-bid term, capped to prevent a high bid from swamping the
    # host_request tier.
    raw_bid_term = config.bid_coefficient * float(task.bid)
    bid_term = min(raw_bid_term, config.bid_term_cap)
    goal_term = config.goal_affinity_coefficient  # coefficient only; no signal
    cost_term = -config.cost_penalty_coefficient

    return tier + recency + boost + pickup_signal + bid_term + goal_term + cost_term


def _task_dispatch_text(task: BranchTask) -> str:
    parts = [
        task.branch_task_id,
        task.branch_def_id,
        task.universe_id,
        task.trigger_source,
        task.request_type,
        task.goal_id,
        task.evidence_url,
    ]
    try:
        parts.append(json.dumps(task.inputs, sort_keys=True, default=str))
    except TypeError:
        parts.append(str(task.inputs))
    return " ".join(str(part or "") for part in parts)


def _dispatch_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.lower())
        if token
    }


def soul_guided_dispatch_read(
    universe_path: Path,
    *,
    task: BranchTask,
    active_daemon_id: str,
    coefficient: float = 0.0,
    term_cap: float = 8.0,
) -> dict[str, Any]:
    """Return read-only daemon/soul dispatch guidance for one task.

    The helper never claims or mutates the queue. It enforces requester-directed
    daemon affinity as eligibility when the caller identifies the active daemon,
    then computes an advisory soul/brain score term from daemon metadata and
    open mini-brain hints.
    """
    daemon_id = str(active_daemon_id or "").strip()
    directed_id = str(task.directed_daemon_id or "").strip()
    if not daemon_id:
        return {
            "eligible": True,
            "reason": "no_active_daemon",
            "score_adjustment": 0.0,
            "read_only": True,
        }
    if directed_id and directed_id != daemon_id:
        return {
            "eligible": False,
            "reason": "directed_to_other_daemon",
            "directed_daemon_id": directed_id,
            "active_daemon_id": daemon_id,
            "score_adjustment": 0.0,
            "read_only": True,
        }

    try:
        from tinyassets.daemon_registry import get_daemon

        base_path = Path(universe_path).parent
        daemon = get_daemon(base_path, daemon_id=daemon_id, include_soul=True)
    except Exception as exc:  # noqa: BLE001 - dispatch must degrade to queue policy
        logger.warning("dispatcher: soul guidance unavailable for %s: %s", daemon_id, exc)
        return {
            "eligible": True,
            "reason": "daemon_unavailable",
            "active_daemon_id": daemon_id,
            "score_adjustment": 0.0,
            "read_only": True,
        }

    task_text = _task_dispatch_text(task)
    task_tokens = _dispatch_tokens(task_text)
    claim_hits = [
        claim for claim in daemon.get("domain_claims", [])
        if _dispatch_tokens(str(claim)) & task_tokens
    ]
    soul_hits = sorted(_dispatch_tokens(str(daemon.get("soul_text") or "")) & task_tokens)

    brain_hits: list[dict[str, Any]] = []
    try:
        from tinyassets.daemon_brain import read_daemon_brain_dispatch_hints
        from tinyassets.memory.runtime import current_memory

        brain = read_daemon_brain_dispatch_hints(
            current_memory().source("daemon"),
            daemon_id=daemon_id,
            query=task_text,
            limit=3,
        )
        brain_hits = list(brain.get("entries") or [])
    except Exception as exc:  # noqa: BLE001 - advisory path only
        logger.warning("dispatcher: brain guidance unavailable for %s: %s", daemon_id, exc)

    raw_affinity = (
        2.0 * len(claim_hits)
        + 0.5 * min(len(soul_hits), 6)
        + sum(float(entry.get("importance") or 0.0) for entry in brain_hits)
    )
    adjustment = min(
        max(0.0, float(coefficient)) * raw_affinity,
        max(0.0, float(term_cap)),
    )
    return {
        "eligible": True,
        "reason": "soul_guided",
        "active_daemon_id": daemon_id,
        "daemon_soul_hash": daemon.get("soul_hash"),
        "domain_claim_hits": claim_hits,
        "soul_token_hits": soul_hits[:12],
        "brain_entry_ids": [entry["entry_id"] for entry in brain_hits],
        "score_adjustment": adjustment,
        "read_only": True,
    }


def run_branch_task_producers_into_queue(
    universe_path: Path,
    *,
    subscribed_goals: list[str],
    producer_config: dict | None = None,
) -> int:
    """Invoke every registered ``BranchTaskProducer`` and
    append emitted tasks into ``branch_tasks.json``.

    Called at the dispatcher boundary (cycle boundary), BEFORE
    ``select_next_task``. Idempotency is enforced at the queue
    level: a task whose ``branch_task_id`` is already present is
    NOT appended again (dedupe-on-append semantics preserve the producer
    idempotency invariant).

    Returns the count appended.
    """
    from tinyassets.producers.branch_task import (
        registered_branch_task_producers,
        run_branch_task_producers,
    )

    producers = registered_branch_task_producers()
    if not producers:
        return 0
    emitted = run_branch_task_producers(
        Path(universe_path),
        subscribed_goals=subscribed_goals,
        producer_config=producer_config,
    )
    if not emitted:
        return 0
    existing = read_queue(Path(universe_path))
    existing_ids = {t.branch_task_id for t in existing}
    appended = 0
    for task in emitted:
        if task.branch_task_id in existing_ids:
            continue
        if not task.universe_id:
            # Pool producers don't know the subscriber's universe
            # at read time; stamp it from the dispatch context
            # (the directory name is the canonical universe_id).
            task.universe_id = Path(universe_path).name
        try:
            append_task(Path(universe_path), task)
            appended += 1
            existing_ids.add(task.branch_task_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "dispatcher: failed to append task %s from producer",
                task.branch_task_id,
            )
    return appended


def select_next_task(
    universe_path: Path,
    *,
    config: DispatcherConfig,
    now_iso: str | None = None,
    epoch2_adapter: Any | None = None,
    queue_epochs: frozenset[int] | None = None,
) -> BranchTask | None:
    """Read eligible v1/v2 queues and return the top task without mutation.

    Returns ``None`` on empty / no-eligible. Called event-driven at
    graph-cycle boundaries. Passing no epoch-2 adapter is the legacy-worker
    boundary: that caller can read and claim v1 only. ``queue_epochs`` lets
    readiness probes restrict a merged read without changing ordinary callers.
    """
    queue = read_queue(universe_path)
    if epoch2_adapter is not None:
        queue.extend(
            epoch2_adapter.list_candidates(
                universe_id=Path(universe_path).name,
            )
        )
    if not queue:
        return None
    now = now_iso or datetime.now(timezone.utc).isoformat()
    eligible: list[tuple[float, BranchTask]] = []
    for task in queue:
        if task.status != "pending":
            continue
        queue_epoch = int(getattr(task, "queue_epoch", 1))
        if queue_epochs is not None and queue_epoch not in queue_epochs:
            continue
        if task.trigger_source == "operator_request" and queue_epoch != 2:
            continue
        if queue_epoch == 2 and int(
            getattr(task, "protocol_version", 0)
        ) != 2:
            continue
        if not config.tier_enabled(task.trigger_source):
            continue
        # LLM-type filter. Both sides non-empty and non-matching
        # → skip. Empty ``served_llm_type`` means "serve all types";
        # empty ``task.required_llm_type`` means "any daemon welcome".
        if (
            task.required_llm_type
            and config.served_llm_type
            and task.required_llm_type != config.served_llm_type
        ):
            continue
        # Request-type filter: only claim types this daemon prefers.
        if not prefers_request_type(task.request_type):
            continue
        guidance = soul_guided_dispatch_read(
            universe_path,
            task=task,
            active_daemon_id=config.active_daemon_id,
            coefficient=config.soul_affinity_coefficient,
            term_cap=config.soul_affinity_term_cap,
        )
        if not guidance["eligible"]:
            continue
        s = score_task(task, now_iso=now, config=config)
        s += float(guidance.get("score_adjustment") or 0.0)
        eligible.append((s, task))
    if not eligible:
        return None
    # Stable sort: primary score desc, tiebreak queued_at asc.
    eligible.sort(
        key=lambda pair: (-pair[0], pair[1].queued_at),
    )
    return eligible[0][1]


def load_dispatcher_config(universe_path: Path) -> DispatcherConfig:
    """Read ``<universe>/dispatcher_config.yaml``; defaults if missing.

    YAML keys mirror :class:`DispatcherConfig` field names. Missing
    file or missing keys silently fall back to defaults; the config is
    optional.
    """
    cfg_path = Path(universe_path) / "dispatcher_config.yaml"
    if not cfg_path.exists():
        # Flag-on + no config means paid market defaults enabled.
        if paid_market_enabled():
            return DispatcherConfig(
                bid_coefficient=1.0, accept_paid_bids=True,
            )
        return DispatcherConfig()
    try:
        import yaml  # local import: dispatcher is hot-path-adjacent
    except ImportError:
        logger.warning(
            "pyyaml not available; using DispatcherConfig defaults "
            "for %s", universe_path,
        )
        return DispatcherConfig()
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - broad log+fallback
        logger.warning(
            "Failed to read %s: %s; using defaults", cfg_path, exc,
        )
        return DispatcherConfig()
    if not isinstance(data, dict):
        return DispatcherConfig()

    kwargs: dict[str, Any] = {}
    for key in (
        "accept_external_requests", "accept_goal_pool",
        "accept_paid_bids", "allow_opportunistic",
        "recency_half_life_seconds", "bid_coefficient",
        "bid_term_cap",
        "goal_affinity_coefficient", "cost_penalty_coefficient",
        "served_llm_type",
        "active_daemon_id",
        "soul_affinity_coefficient",
        "soul_affinity_term_cap",
    ):
        if key in data:
            kwargs[key] = data[key]
    if "tier_weights" in data and isinstance(data["tier_weights"], dict):
        weights = dict(_DEFAULT_TIER_WEIGHTS)
        for k, v in data["tier_weights"].items():
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
        kwargs["tier_weights"] = weights

    # If the paid-market flag is on and the YAML didn't pin
    # values, default ``bid_coefficient`` to 1.0 and enable paid_bid
    # acceptance. Explicit YAML values win — allows ``bid_coefficient: 0.0``
    # to keep paid scoring neutral even under flag-on.
    if paid_market_enabled():
        if "bid_coefficient" not in data:
            kwargs["bid_coefficient"] = 1.0
        if "accept_paid_bids" not in data:
            kwargs["accept_paid_bids"] = True

    return DispatcherConfig(**kwargs)
