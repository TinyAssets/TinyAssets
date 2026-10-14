"""Read-only priority policy for the persisted v2 request queue.

The API and reconciliation read this policy to explain queued requests. The
owner tick starts executable work; this module has no polling or run path.

Priority scoring starts with the minimum viable tier model:

    score = tier_weight[trigger_source]
          + recency_decay(queued_at)
          + user_boost(priority_weight)

Deferred terms (``bid``, ``goal_affinity``, ``cost_penalty``) are
zero. Their coefficients sit in :class:`DispatcherConfig` so later
market and goal-affinity work can wire them in without re-shaping
``score_task``.

The policy reads the v2 queue only and does not claim or start tasks.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.branch_tasks_v2 import BranchTask

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
    multiply zero but remain in the persisted configuration's read contract.
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
