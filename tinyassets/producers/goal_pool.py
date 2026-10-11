"""GoalPoolProducer — Phase F cross-universe pool reader.

Reads ``<repo_root>/goal_pool/<goal_slug>/*.yaml`` for each goal in
the subscribed-goals list. Each YAML becomes a ``BranchTask``.

Safety contract (R4): ``inputs`` must be a **flat dict** of
primitives. Nested dicts/lists and ``_``-prefixed keys are rejected
at both post-side and producer-side to prevent cross-universe
contamination through smuggled paths or state handles. Flat-only
enforcement is trivially correct; recursive strips are error-prone.

Three-flag matrix (preflight §4.2): the module registers the
producer only when ``TINYASSETS_GOAL_POOL`` is on. Under flag-off,
``registered_branch_task_producers()`` contains no goal_pool entry
and the dispatcher cycle is a no-op on pool reads.

Subscriber-side slug resolution (R9, invariant 6): pool tasks
referencing a ``branch_def_id`` that doesn't resolve against the
subscriber's accessible Branch slugs are skipped silently (INFO
log) — not enqueued-and-failed. Accessible slugs include static
``branches/*.yaml`` files plus public runtime-catalog branches for the
subscriber universe.

Repo-root resolution (§4.1 #7, resolves Q3 as contract):
1. ``TINYASSETS_REPO_ROOT`` env var.
2. Walk parents of ``<universe_path>`` looking for ``.git``.
3. RuntimeError with actionable hint — pool producer treats this
   as "pool not available", returns ``[]``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import fields as dataclass_fields
from pathlib import Path

from tinyassets.branch_tasks import (
    VALID_STATUSES,
    VALID_TRIGGER_SOURCES,
    BranchTask,
    new_task_id,
)
from tinyassets.producers.branch_task import register_branch_task_producer
from tinyassets.subscriptions import validate_goal_id

logger = logging.getLogger(__name__)

POOL_DIRNAME = "goal_pool"
POOL_PRODUCER_NAME = "goal_pool"
POOL_ORIGIN = "goal_pool"

_PRIMITIVE_TYPES = (str, int, float, bool, type(None))

# Keys that must never appear in inputs — they'd smuggle
# universe-specific state across the isolation boundary.
_REJECTED_INPUT_KEYS = frozenset({
    "_universe_path", "_db_path", "_kg_path", "work_target_ref",
})


def _rung_claim_recommendations(
    universe_path: Path,
    goal_id: str,
    branch_def_id: str = "",
) -> list[dict]:
    """Return claim recommendations copied from the bound Goal ladder."""
    if not goal_id:
        return []
    try:
        from tinyassets.daemon_server import get_goal_ladder
        from tinyassets.storage import base_path_from_universe

        ladder = get_goal_ladder(
            base_path_from_universe(universe_path),
            goal_id=goal_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "goal_pool: no gate ladder recommendations for %s: %s",
            goal_id,
            exc,
        )
        return []

    recommendations: list[dict] = []
    for rung in ladder:
        if not isinstance(rung, dict):
            continue
        rung_key = str(rung.get("rung_key") or "").strip()
        if not rung_key:
            continue
        label = rung.get("label") or rung.get("name") or rung_key
        recommendations.append({
            "goal_id": goal_id,
            "rung_key": rung_key,
            "label": label,
            "description": rung.get("description", ""),
            "branch_requirements": dict(rung.get("branch_requirements") or {}),
            "bounty_requirements": dict(rung.get("bounty_requirements") or {}),
            "claim_action": None,
            "claim_gap": (
                "Gate claims are not exposed by the advertised handles; "
                "use an internal operator surface."
            ),
        })
    return recommendations


def repo_root_path(universe_path: Path) -> Path:
    """Resolve the shared pool location. Preflight §4.1 #7 order:

    1. ``TINYASSETS_REPO_ROOT`` env var (explicit host control).
    2. Git-detect upward from ``universe_path`` (walk parents for ``.git``).
    3. ``RuntimeError`` with actionable hint.
    """
    env = os.environ.get("TINYASSETS_REPO_ROOT", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        return p
    start = Path(universe_path).expanduser().resolve()
    candidate = start
    for _ in range(30):
        if (candidate / ".git").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    raise RuntimeError(
        "Cannot resolve repo_root. Set TINYASSETS_REPO_ROOT or run "
        "the daemon from inside a git checkout."
    )


def validate_pool_task_inputs(inputs: object) -> tuple[bool, str]:
    """Flat-dict invariant per preflight invariant 1.

    Returns ``(ok, reason)``. ``reason`` is empty on ok.
    """
    if not isinstance(inputs, dict):
        return False, "inputs must be a dict"
    for k, v in inputs.items():
        if not isinstance(k, str):
            return False, f"inputs key {k!r} is not a string"
        if k.startswith("_"):
            return False, f"inputs key {k!r} begins with underscore (reserved)"
        if k in _REJECTED_INPUT_KEYS:
            return False, f"inputs key {k!r} is reserved"
        if not isinstance(v, _PRIMITIVE_TYPES):
            return False, (
                f"inputs value for {k!r} is not a primitive "
                f"(got {type(v).__name__}; only str/int/float/bool/None allowed)"
            )
    return True, ""


def _catalog_branch_slugs(universe_path: Path) -> set[str]:
    """Return public runtime-catalog branch IDs visible to subscribers.

    Live community-authored branches are persisted in the daemon-server
    catalog under the data root, not necessarily in the image's static
    ``branches/`` directory. Keep this lazy so test repos without an
    initialized catalog and early boot paths fail closed without
    blocking static branch lookup.
    """
    try:
        from tinyassets.daemon_server import list_branch_definitions
        from tinyassets.storage import base_path_from_universe

        branches = list_branch_definitions(
            base_path_from_universe(universe_path),
            include_private=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("goal_pool: runtime catalog unavailable: %s", exc)
        return set()
    return {
        str(branch.get("branch_def_id") or "")
        for branch in branches
        if branch.get("branch_def_id")
    }


def _accessible_branch_slugs(repo_root: Path, universe_path: Path | None = None) -> set[str]:
    """Return set of branch slugs the subscriber can compile.

    Per R9: subscriber's accessible Branches = public (in
    ``<repo_root>/branches/*.yaml``) plus subscriber-local.

    Domain branch slugs (e.g. the fantasy loop wrapper) are added only via
    ``registered_domain_branch_slugs()``, which a domain populates as an
    import-time side effect. Per the engine-is-infrastructure principle
    (``tinyassets/domain_registry.py``) the engine never imports a specific
    domain, so this set is **process-dependent by design**: a process that has
    not loaded a domain (the cloud-worker producer pump, the plugin runtime)
    will not see that domain's slugs, and a goal-pool task targeting one is
    skipped here **when the subscriber can reach at least one other branch**
    (the accessibility filter in ``_parse_pool_yaml`` fail-OPENS on a fully
    empty accessible set — see
    ``test_goal_pool_fails_open_when_no_accessible_slugs``). That is not a lost
    task — the domain's own daemon, which imports its registrations, still scans
    the pool at graph-start. Re-injecting
    a hardcoded domain slug to "fix" this divergence would re-couple the engine
    to a domain and is explicitly not wanted. See
    ``test_goal_pool_skips_fantasy_seed_when_unregistered`` /
    ``test_goal_pool_accepts_registered_fantasy_seed`` which pin both halves.
    """
    from tinyassets.domain_registry import registered_domain_branch_slugs

    slugs: set[str] = set()
    branches_dir = repo_root / "branches"
    if branches_dir.is_dir():
        for p in branches_dir.glob("*.yaml"):
            slugs.add(p.stem)
    if universe_path is not None:
        slugs.update(_catalog_branch_slugs(universe_path))
    slugs.update(registered_domain_branch_slugs())
    return slugs


class GoalPoolProducer:
    """BranchTaskProducer reading ``goal_pool/<goal>/*.yaml``."""

    name = POOL_PRODUCER_NAME
    origin = POOL_ORIGIN

    def __init__(self) -> None:
        # mtime cache for R3: avoid re-scanning unchanged pool dirs.
        self._mtime_cache: dict[Path, float] = {}
        self._result_cache: dict[Path, list[BranchTask]] = {}

    def produce(
        self,
        universe_path: Path,
        *,
        subscribed_goals: list[str],
        config: dict | None = None,
    ) -> list[BranchTask]:
        if not subscribed_goals:
            return []
        try:
            repo_root = repo_root_path(Path(universe_path))
        except RuntimeError as exc:
            logger.info("goal_pool: repo_root not available (%s)", exc)
            return []

        pool_root = repo_root / POOL_DIRNAME
        if not pool_root.is_dir():
            logger.info(
                "goal_pool: %s does not exist; nothing to produce",
                pool_root,
            )
            return []

        try:
            accessible = _accessible_branch_slugs(repo_root, Path(universe_path))
        except Exception:  # noqa: BLE001
            logger.exception("goal_pool: failed to enumerate branches")
            accessible = set()

        # Bound per spec §4.1 #6.
        max_per_cycle = 5
        if isinstance(config, dict):
            try:
                max_per_cycle = int(config.get(
                    "max_pool_tasks_per_cycle", max_per_cycle,
                ))
            except (TypeError, ValueError):
                pass

        out: list[BranchTask] = []
        for goal in subscribed_goals:
            try:
                validate_goal_id(goal)
            except ValueError as exc:
                logger.warning("goal_pool: skipping unsafe goal %r: %s", goal, exc)
                continue
            goal_dir = pool_root / goal
            if not goal_dir.is_dir():
                continue
            out.extend(
                self._scan_goal_dir(
                    goal_dir,
                    goal,
                    accessible,
                    max_per_cycle - len(out),
                    universe_path=Path(universe_path),
                )
            )
            if len(out) >= max_per_cycle:
                break
        return out[:max_per_cycle]

    def _scan_goal_dir(
        self,
        goal_dir: Path,
        goal_id: str,
        accessible_slugs: set[str],
        budget: int,
        *,
        universe_path: Path,
    ) -> list[BranchTask]:
        if budget <= 0:
            return []
        # R3: mtime cache.
        try:
            current_mtime = goal_dir.stat().st_mtime
        except OSError:
            return []
        cached_mtime = self._mtime_cache.get(goal_dir)
        if cached_mtime is not None and cached_mtime == current_mtime:
            cached = list(self._result_cache.get(goal_dir, [])[:budget])
            for task in cached:
                task.rung_claim_recommendations = _rung_claim_recommendations(
                    universe_path, goal_id, task.branch_def_id,
                )
            return cached

        import yaml

        tasks: list[BranchTask] = []
        for yaml_path in sorted(goal_dir.glob("*.yaml")):
            try:
                raw = yaml_path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "goal_pool: malformed YAML at %s: %s", yaml_path, exc,
                )
                continue
            if not isinstance(data, dict):
                logger.warning(
                    "goal_pool: YAML at %s is not a mapping; skipping",
                    yaml_path,
                )
                continue
            task = self._parse_pool_yaml(data, yaml_path, goal_id, accessible_slugs)
            if task is not None:
                task.rung_claim_recommendations = _rung_claim_recommendations(
                    universe_path, goal_id, task.branch_def_id,
                )
                tasks.append(task)
            if len(tasks) >= budget:
                break
        self._mtime_cache[goal_dir] = current_mtime
        self._result_cache[goal_dir] = list(tasks)
        return tasks

    def _parse_pool_yaml(
        self,
        data: dict,
        yaml_path: Path,
        goal_id: str,
        accessible_slugs: set[str],
    ) -> BranchTask | None:
        # Required fields.
        task_id = str(data.get("branch_task_id") or yaml_path.stem)
        if task_id != yaml_path.stem:
            logger.warning(
                "goal_pool: %s has branch_task_id=%r but filename stem=%r; "
                "filename stem wins (required by spec §4.1 #3)",
                yaml_path, task_id, yaml_path.stem,
            )
            task_id = yaml_path.stem

        branch_def_id = data.get("branch_def_id")
        if not isinstance(branch_def_id, str) or not branch_def_id:
            logger.warning(
                "goal_pool: %s missing branch_def_id; skipping", yaml_path,
            )
            return None

        # R9 / invariant 6: reject branch slugs the subscriber can't run.
        # Conditional by design: an empty ``accessible_slugs`` means the
        # subscriber could not enumerate ANY branch ("can't determine
        # accessibility"), so the filter fail-OPENS and the task is queued
        # rather than silently dropped. Not fantasy-specific. Pinned by
        # ``test_goal_pool_fails_open_when_no_accessible_slugs``; the non-empty
        # filter half is pinned by the de-fantasy divergence tests.
        if accessible_slugs and branch_def_id not in accessible_slugs:
            logger.info(
                "goal_pool: branch_def_id=%r not in accessible slugs; "
                "skipping %s",
                branch_def_id, yaml_path,
            )
            return None

        task_goal = data.get("goal_id")
        if task_goal and task_goal != goal_id:
            logger.warning(
                "goal_pool: %s declares goal_id=%r but lives under "
                "goal_dir=%r; directory wins",
                yaml_path, task_goal, goal_id,
            )

        inputs = data.get("inputs") or {}
        ok, reason = validate_pool_task_inputs(inputs)
        if not ok:
            logger.warning(
                "goal_pool: %s rejected — %s", yaml_path, reason,
            )
            return None

        try:
            queued_mtime = yaml_path.stat().st_mtime
        except OSError:
            queued_mtime = 0.0
        from datetime import datetime, timezone
        queued_at = datetime.fromtimestamp(
            queued_mtime or 0.0, tz=timezone.utc,
        ).isoformat()

        # Non-host posters had priority_weight clamped at post time;
        # we trust the YAML's stored value here.
        try:
            priority_weight = float(data.get("priority_weight", 0.0))
        except (TypeError, ValueError):
            priority_weight = 0.0
        if priority_weight < 0:
            priority_weight = 0.0

        # Universe_id: pool tasks are not bound to any single
        # universe at post time; the subscriber's universe owns the
        # execution. The task's universe_id is stamped here as the
        # subscriber's universe (stored in the file-path context via
        # the producer call site).
        # v1: store empty — the dispatcher/wire-up knows which
        # universe is executing.
        task = BranchTask(
            branch_task_id=task_id,
            branch_def_id=branch_def_id,
            universe_id="",  # set by caller / execution wire-up
            inputs={str(k): v for k, v in inputs.items()},
            trigger_source=POOL_ORIGIN,
            priority_weight=priority_weight,
            queued_at=queued_at,
            claimed_by="",
            status="pending",
            goal_id=goal_id,
        )

        # Defense-in-depth: validate final dataclass shape.
        if task.trigger_source not in VALID_TRIGGER_SOURCES:
            return None
        if task.status not in VALID_STATUSES:
            return None
        # Defensive check on unknown extras (paranoid).
        known_fields = {f.name for f in dataclass_fields(BranchTask)}
        for key in data.keys():
            if key not in known_fields and key not in {"goal_id"}:
                # Unknown keys are ignored, not an error.
                continue
        return task


def goal_pool_enabled() -> bool:
    """Read ``TINYASSETS_GOAL_POOL``. Default OFF (preflight §4.2)."""
    value = os.environ.get("TINYASSETS_GOAL_POOL", "off")
    return value.strip().lower() in {"on", "1", "true", "yes"}


def register_if_enabled() -> bool:
    """Register the GoalPoolProducer if the flag is on.

    Idempotent — safe to call multiple times. Returns True if
    registered. Called from module-import side-effect at bottom.
    """
    if goal_pool_enabled():
        register_branch_task_producer(GoalPoolProducer())
        return True
    return False


# Side-effect import: register the producer when the module loads,
# if flag is on at import time. Module-level flag checks match the
# Phase D precedent (``branch_registrations`` side-effect registers).
register_if_enabled()


def pool_post_path(
    repo_root: Path, goal_id: str, branch_task_id: str | None = None,
) -> Path:
    """Canonical path for a pool post YAML."""
    validate_goal_id(goal_id)
    task_id = branch_task_id or new_task_id()
    return repo_root / POOL_DIRNAME / goal_id / f"{task_id}.yaml"


def write_pool_post(
    repo_root: Path,
    goal_id: str,
    *,
    branch_def_id: str,
    inputs: dict,
    priority_weight: float = 0.0,
    posted_by: str = "",
    branch_task_id: str | None = None,
) -> Path:
    """Write a pool YAML. Performs flat-dict validation.

    Returns the YAML path. Raises ``ValueError`` on invalid inputs.
    """
    validate_goal_id(goal_id)
    ok, reason = validate_pool_task_inputs(inputs)
    if not ok:
        raise ValueError(f"invalid pool inputs: {reason}")

    import yaml

    task_id = branch_task_id or new_task_id()
    pool_dir = repo_root / POOL_DIRNAME / goal_id
    pool_dir.mkdir(parents=True, exist_ok=True)
    out_path = pool_dir / f"{task_id}.yaml"

    pw = float(priority_weight) if priority_weight else 0.0
    if pw < 0:
        pw = 0.0

    payload = {
        "branch_task_id": task_id,
        "branch_def_id": branch_def_id,
        "goal_id": goal_id,
        "inputs": dict(inputs),
        "priority_weight": pw,
        "posted_by": posted_by,
    }
    out_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return out_path
