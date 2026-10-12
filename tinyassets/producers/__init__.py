"""TaskProducer protocol + active registry.

Phase C.3 landing. The protocol is the contract; the registry is the
live list of producers a daemon cycle iterates. No producers register
themselves here in C.3 — that's C.4's job (seed / user_request /
fantasy_authorial each import `tinyassets.producers` in their domain
init and call ``register(...)``).

See openspec/specs/background-branch-execution-authority/spec.md §1 for full contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from tinyassets.work_targets import WorkTarget


@runtime_checkable
class TaskProducer(Protocol):
    """Produces candidate WorkTargets for one universe's daemon cycle.

    Called once per daemon cycle (before review gates run). Must be
    idempotent: calling twice with unchanged inputs returns the same
    WorkTarget set. Producers MAY upsert targets into the universe's
    ``work_targets.json``, but MUST return only the targets produced
    or updated by this call — callers use the return value to
    attribute origin and log what each producer contributed.

    v1 protocol is synchronous; Phase F may introduce an async variant
    for cross-universe producers that read repo-root state with I/O
    latency. Synchronous design is deliberate for v1 — simpler
    contract, easier ON/OFF identity testing.
    """

    name: str
    """Stable identifier. Logged per produced target. Must match
    ``[a-z][a-z0-9_-]*``. Examples: ``"fantasy_authorial"``,
    ``"user_request"``, ``"seed"``."""

    origin: str
    """The ``WorkTarget.origin`` value this producer stamps on every
    emitted target. Must be one of the allowed origin values
    (spec §3). A single producer always stamps one origin —
    producers don't commingle origin values."""

    def produce(
        self,
        universe_path: Path,
        *,
        config: dict | None = None,
    ) -> list[WorkTarget]:
        """Read universe state, return candidate WorkTargets.

        ``config`` is a producer-specific dict passed from the daemon's
        config file — lets producers take parameters without needing
        constructor args. Producers that need no config ignore it.

        Raises: should NOT raise on empty state — return ``[]``. MAY
        raise on genuinely corrupt state (unreadable JSON, missing
        required files); the daemon logs and skips to the next
        producer.
        """
        ...


# Module-level registry. Order is dispatch order — later producers
# overwrite earlier ones on target_id collision per spec §1.2. The
# Phase C.4 identity test depends on this determinism, so do NOT
# reorder at iteration time.
_REGISTRY: list[TaskProducer] = []


def register(producer: TaskProducer) -> None:
    """Add a producer to the active list.

    Idempotent on ``producer.name``: re-registering by name replaces
    the prior instance (supports hot-reload in dev + guards against
    duplicate-register bugs in domain init).
    """
    global _REGISTRY
    _REGISTRY = [p for p in _REGISTRY if p.name != producer.name]
    _REGISTRY.append(producer)


def registered_producers() -> tuple[TaskProducer, ...]:
    """Return active producers in registration order.

    Returns a tuple so callers can't mutate the registry by mutating
    the returned sequence. Use ``register()`` / ``unregister()`` to
    change state.
    """
    return tuple(_REGISTRY)


def unregister(name: str) -> bool:
    """Remove a producer by name. Returns True if it was present."""
    global _REGISTRY
    before = len(_REGISTRY)
    _REGISTRY = [p for p in _REGISTRY if p.name != name]
    return len(_REGISTRY) != before


def reset_registry() -> None:
    """Clear all registered producers. Test-only hook."""
    global _REGISTRY
    _REGISTRY = []


# Phase C.4 — feature flag + dispatcher helper ----------------------

def producer_interface_enabled() -> bool:
    """Return True if the producer interface is active.

    Default ``on``. Flip via ``TINYASSETS_PRODUCER_INTERFACE=off`` to
    bypass the producer list entirely. Off is the revert lever; C.5
    call sites branch on this flag.
    """
    import os
    value = os.environ.get("TINYASSETS_PRODUCER_INTERFACE", "on")
    return value.strip().lower() not in {"off", "0", "false", "no"}


def run_producers(
    universe_path,
    *,
    producer_config: dict | None = None,
) -> list[WorkTarget]:
    """Invoke every registered producer, merge results with last-write-
    wins on ``target_id``.

    Each emitted target has its ``origin`` overwritten by the emitting
    producer's ``origin`` value (enforced stamp per spec §1.2). A
    producer that raises is logged and skipped — other producers still
    run.

    Returns a list of WorkTargets in registration order, deduped by
    target_id. Safe to pass to ``choose_authorial_targets`` via
    ``candidate_override``.
    """
    import logging
    logger = logging.getLogger(__name__)
    cfg = producer_config or {}
    merged: dict[str, WorkTarget] = {}
    for producer in registered_producers():
        try:
            produced = producer.produce(
                universe_path,
                config=cfg.get(producer.name),
            )
        except Exception as exc:
            logger.warning(
                "producer %s failed: %s", producer.name, exc,
            )
            continue
        for target in produced:
            if target.target_id in merged:
                logger.warning(
                    "producer %s upserted target %s already emitted "
                    "earlier this cycle; last-write-wins",
                    producer.name, target.target_id,
                )
            target.origin = producer.origin
            merged[target.target_id] = target
    return list(merged.values())
