"""NodeBidProducer — Phase G cross-universe bid-market reader.

Reads ``<repo_root>/bids/*.yaml``. Each open NodeBid whose
``required_llm_type`` matches the daemon's served LLM (or is blank)
and whose ``inputs`` pass the flat-dict invariant becomes a
BranchTask with a sentinel ``branch_def_id`` prefix ``<node_bid>``
so the dispatcher and ``_run_graph`` can route it to the NodeBid
executor instead of the Branch wrapper stream.

Registration is gated on ``TINYASSETS_PAID_MARKET=on``. Under flag-off,
the producer is not registered and the dispatcher never sees bid
tasks (preflight §4.2 flag matrix).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from tinyassets.bid.node_bid import read_node_bids, validate_node_bid_inputs
from tinyassets.branch_tasks import BranchTask
from tinyassets.graph_compiler import _BID_DANGEROUS_PATTERNS
from tinyassets.producers.branch_task import register_branch_task_producer
from tinyassets.producers.goal_pool import repo_root_path

logger = logging.getLogger(__name__)

NODE_BID_PRODUCER_NAME = "node_bid"
NODE_BID_ORIGIN = "paid_bid"

# Sentinel prefix used on BranchTask.branch_def_id so the dispatcher
# and _run_graph can distinguish NodeBid tasks from real Branch runs.
# Angle brackets are not valid in a Branch slug → zero collision risk.
NODE_BID_SENTINEL_PREFIX = "<node_bid>"


def paid_market_enabled() -> bool:
    """Read ``TINYASSETS_PAID_MARKET``. Default OFF."""
    value = os.environ.get("TINYASSETS_PAID_MARKET", "off")
    return value.strip().lower() in {"on", "1", "true", "yes"}


def _producer_sandbox_reject(
    bid_node_def_id: str,
    node_lookup_fn,
) -> str:
    """Producer-side layers 1+2 of the three-layer sandbox.

    Returns an empty string on pass, or a short reason code on
    rejection. Preflight §4.3 invariant 1 requires ALL THREE layers
    at BOTH producer side (pre-pick rejection) AND executor side
    (defense-in-depth). This is the producer side of layers 1 + 2.

    Layer 1: node must resolve in the registry with ``approved=True``.
    Layer 2: the resolved node's ``source_code`` must NOT contain
    any pattern in ``_BID_DANGEROUS_PATTERNS`` (wider than the
    wrapper-node list used by Phase D).

    ``node_lookup_fn`` is ``None`` or a callable
    ``(node_def_id) -> NodeDefinition | None``. If ``None``, the
    producer relies on the executor side to enforce these layers —
    acceptable as a fallback when no registry is wired, but the
    preferred path is to pass a real lookup function so adversarial
    bids never enter the queue.
    """
    if node_lookup_fn is None:
        return ""
    try:
        node = node_lookup_fn(bid_node_def_id)
    except Exception:  # noqa: BLE001
        return "node_lookup_error"
    if node is None:
        return "unknown_node_def_id"
    if not getattr(node, "approved", False):
        return "unapproved_node"
    source = getattr(node, "source_code", "") or ""
    for pattern in _BID_DANGEROUS_PATTERNS:
        if pattern in source:
            return f"dangerous_pattern:{pattern}"
    return ""


class NodeBidProducer:
    """BranchTaskProducer reading ``<repo_root>/bids/*.yaml``."""

    name = NODE_BID_PRODUCER_NAME
    origin = NODE_BID_ORIGIN

    def __init__(self, node_lookup_fn=None) -> None:
        # mtime cache on the bids/ directory: if unchanged, reuse prior
        # result without re-parsing every YAML.
        self._mtime: float | None = None
        self._cache: list[BranchTask] = []
        self._cache_repo_root: Path | None = None
        self._cache_served_llm: str = ""
        # node_lookup_fn supplies producer-side layers 1+2. None is
        # acceptable for unit tests + fresh installs without a
        # registry wired yet; executor-side checks still catch
        # adversarial bids.
        self._node_lookup_fn = node_lookup_fn

    def produce(
        self,
        universe_path: Path,
        *,
        subscribed_goals: list[str],
        config: dict | None = None,
    ) -> list[BranchTask]:
        try:
            repo_root = repo_root_path(Path(universe_path))
        except RuntimeError as exc:
            logger.info("node_bid: repo_root not available (%s)", exc)
            return []

        bids_root = repo_root / "bids"
        if not bids_root.is_dir():
            return []

        # Which LLM types are we willing to serve?
        served_llm_type = ""
        if isinstance(config, dict):
            served_llm_type = str(config.get("served_llm_type", "") or "")

        # mtime short-circuit — bust cache if repo_root or served LLM changed.
        try:
            current_mtime = bids_root.stat().st_mtime
        except OSError:
            return []
        if (
            self._mtime == current_mtime
            and self._cache_repo_root == repo_root
            and self._cache_served_llm == served_llm_type
        ):
            return list(self._cache)

        out: list[BranchTask] = []
        for bid in read_node_bids(repo_root):
            if bid.status != "open":
                continue
            # LLM-type filter at producer side — the dispatcher also
            # filters, but doing it here keeps the queue cleaner.
            if (
                bid.required_llm_type
                and served_llm_type
                and bid.required_llm_type != served_llm_type
            ):
                continue
            ok, reason = validate_node_bid_inputs(bid.inputs or {})
            if not ok:
                logger.warning(
                    "node_bid: %s rejected — %s", bid.node_bid_id, reason,
                )
                continue
            if not bid.node_def_id:
                logger.warning(
                    "node_bid: %s missing node_def_id; skipping",
                    bid.node_bid_id,
                )
                continue

            # Preflight §4.3 invariant 1 layers 1+2, producer side:
            # unapproved node and dangerous-source-pattern bids
            # never enter the queue when a registry lookup is wired.
            reject_reason = _producer_sandbox_reject(
                bid.node_def_id, self._node_lookup_fn,
            )
            if reject_reason:
                logger.warning(
                    "node_bid: %s sandbox-rejected at producer: %s",
                    bid.node_bid_id, reject_reason,
                )
                continue

            branch_task_id = f"nb_{bid.node_bid_id}"[:50]
            branch_def_id = f"{NODE_BID_SENTINEL_PREFIX}{bid.node_def_id}"
            merged_inputs: dict = {
                **(bid.inputs or {}),
                "__node_bid_id": bid.node_bid_id,
                "__node_def_id": bid.node_def_id,
            }
            try:
                bid_value = float(bid.bid or 0.0)
            except (TypeError, ValueError):
                bid_value = 0.0
            out.append(BranchTask(
                branch_task_id=branch_task_id,
                branch_def_id=branch_def_id,
                universe_id="",  # stamped by dispatcher wire-up
                inputs=merged_inputs,
                trigger_source=NODE_BID_ORIGIN,
                priority_weight=0.0,  # paid bids sort on bid, not boost
                queued_at=bid.submitted_at or "",
                claimed_by="",
                status="pending",
                bid=bid_value,
                required_llm_type=bid.required_llm_type or "",
            ))

        self._mtime = current_mtime
        self._cache = list(out)
        self._cache_repo_root = repo_root
        self._cache_served_llm = served_llm_type
        return out


def register_if_enabled(node_lookup_fn=None) -> bool:
    """Register the NodeBidProducer if the flag is on. Idempotent.

    Phase H: ``node_lookup_fn`` closes G.1 follow-up #1 — the
    producer-side sandbox layers 1+2 (approved-node check +
    dangerous-pattern scan) become load-bearing when a real lookup
    is wired. Import-time call keeps the default ``None`` so tests
    and fresh installs without a registry continue to work;
    ``DaemonController`` re-registers with the real lookup at
    daemon-start per preflight §4.1 #5.
    """
    if paid_market_enabled():
        register_branch_task_producer(
            NodeBidProducer(node_lookup_fn=node_lookup_fn),
        )
        return True
    return False


# Side-effect import: register when the module loads (no lookup yet —
# DaemonController re-registers with the real lookup at startup).
register_if_enabled()
