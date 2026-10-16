"""Read-only priority policy for the persisted v2 request queue.

The API and reconciliation read this policy to explain persisted requests.
The owner tick does not claim v2 requests; this module has no run path.

Priority scoring starts with the minimum viable tier model:

    score = tier_weight[trigger_source]
          + recency_decay(queued_at)
          + user_boost(priority_weight)

Goal-affinity and cost coefficients remain part of the existing read policy.
Retired paid-bid configuration is ignored; historical requests are not rewritten.

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
    "opportunistic": 10.0,
}


@dataclass
class DispatcherConfig:
    accept_external_requests: bool = True
    accept_goal_pool: bool = False
    allow_opportunistic: bool = False
    tier_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_TIER_WEIGHTS),
    )
    recency_half_life_seconds: float = 86400.0
    pickup_signal_term_cap: float = 5.0
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
            "opportunistic": (
                "live" if self.allow_opportunistic else "stubbed"
            ),
        }




def get_request_type_priorities() -> list[str]:
    """Read ``TINYASSETS_REQUEST_TYPE_PRIORITIES``.

    Returns request types selected for operational queue reads. No worker claims
    them. Empty list means no read filter.
    Format: comma-separated, e.g. ``bug_investigation,branch_run``.
    """
    raw = os.environ.get("TINYASSETS_REQUEST_TYPE_PRIORITIES", "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def prefers_request_type(request_type: str) -> bool:
    """Return True if operational reads include this request type.

    An unset or empty filter includes all types; otherwise only listed types.
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

    goal_term = config.goal_affinity_coefficient  # coefficient only; no signal
    cost_term = -config.cost_penalty_coefficient

    return tier + recency + boost + pickup_signal + goal_term + cost_term


def load_dispatcher_config(universe_path: Path) -> DispatcherConfig:
    """Read ``<universe>/dispatcher_config.yaml``; defaults if missing.

    YAML keys mirror :class:`DispatcherConfig` field names. Missing
    file or missing keys silently fall back to defaults; the config is
    optional.
    """
    from tinyassets.owner_box_reads import BoxConfigInvalid, UnadmittedCenter, box_read_yaml

    try:
        data = box_read_yaml(Path(universe_path), "dispatcher_config.yaml") or {}
    except UnadmittedCenter:
        return DispatcherConfig()
    except BoxConfigInvalid as exc:
        logger.warning("%s; using DispatcherConfig defaults", exc)
        return DispatcherConfig()
    if not isinstance(data, dict):
        return DispatcherConfig()

    kwargs: dict[str, Any] = {}
    for key in (
        "accept_external_requests", "accept_goal_pool",
        "allow_opportunistic",
        "recency_half_life_seconds",
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
            if k not in _DEFAULT_TIER_WEIGHTS:
                continue
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
        kwargs["tier_weights"] = weights

    return DispatcherConfig(**kwargs)
