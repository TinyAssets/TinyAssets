"""Canonical-handler resolution for Goal-bound run dispatch — PR-127 (M6).

The M6 cutover (canonical at
``pages/plans/drafts-plans-m6-cutover-retire-cheat-loop-leaderboard-driven-canonical.md``
on the live brain) replaces the hand-wired cheat loop (an env-var
pointing at a single ``TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID``)
with leaderboard-driven canonical selection. Every Goal can opt into
``auto_canonical_via_leaderboard``; when it does, every
``goals action=run_canonical`` call:

  1. Re-queries the quality leaderboard for the freshest top-ranked
     entry.
  2. If the top entry meets ``min_completed_runs_for_canonical``
     (default 5) AND differs from the current canonical AND no
     in-flight run is currently using the prior canonical, the stored
     ``canonical_branch_version_id`` is refreshed via
     :func:`tinyassets.daemon_server.set_canonical_branch`.
  3. The (possibly updated) canonical is then dispatched.

This module owns the resolution logic. The MCP dispatch wrapper
(``_action_goal_run_canonical`` in ``tinyassets/api/market.py``) calls into
it. (The bug-investigation enqueue path that also did was deleted under
Hard Rule 15.)

**Hard Rule 15 (the platform has no LLM, 2026-09-24):** the leaderboard's
selector was a platform model call and now fails closed
(``selector_retired``), so an auto-refresh finds no entries and the stored
canonical, if any, is used. ``auto_canonical_via_leaderboard`` is inert until
the follow-up deletion in ``docs/decisions/ADR-006-the-platform-has-no-llm.md``.

No new substrate primitives — pure orchestration over existing
``goals`` / ``branch_versions`` / ``quality_leaderboard`` / ``runs``
storage.

**Auth-boundary contract (PR-127 round 2 — Codex P1 findings):**

* ``_latest_published_version_id`` filters to ``status='active'``
  versions only. Rolled-back / superseded versions can NEVER become
  the auto-refresh target. Defense in depth:
  ``tinyassets.daemon_server.set_canonical_branch`` also rejects
  non-active versions at the write site, so a bug in this filter
  alone cannot promote a dead version (P1.1).
* The auto-refresh leaderboard query is ALWAYS computed against
  ``viewer=""`` (strictly-public). The caller-supplied viewer is
  retained for log telemetry only; private branches authored by
  the calling actor cannot become the Goal's global canonical via
  auto-refresh (P1.2).
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from tinyassets.evaluation import EvalResult, PatchNotes
from tinyassets.runs import RunOutcome

logger = logging.getLogger(__name__)


# How far back to look for an in-flight run on the prior canonical
# before deciding it's safe to refresh. Matches the "don't swap
# canonical out from under a live run" rule in the cutover plan.
# A long-running graph may hold ``running`` status for minutes; the
# window is therefore generous. Stale ``queued`` / ``running`` rows
# outside this window are treated as orphaned and don't block the
# refresh.
IN_FLIGHT_WINDOW_SECONDS = 600.0  # 10 min

# Resolution-source labels surfaced in the response so the caller can
# render "why this version was picked". Stable; treat as enum-like.
SOURCE_CANONICAL_STORED = "canonical_stored"
SOURCE_ACTOR_CANONICAL = "actor_canonical"
SOURCE_LEADERBOARD_REFRESHED = "leaderboard_refreshed"
SOURCE_LEADERBOARD_NO_CHANGE = "leaderboard_no_change"
SOURCE_LEADERBOARD_SKIPPED_INSUFFICIENT_RUNS = (
    "leaderboard_skipped_insufficient_runs"
)
SOURCE_LEADERBOARD_SKIPPED_IN_FLIGHT = "leaderboard_skipped_in_flight"
SOURCE_LEADERBOARD_SKIPPED_NO_PUBLISHED_VERSION = (
    "leaderboard_skipped_no_published_version"
)
SOURCE_LEADERBOARD_NO_ENTRIES = "leaderboard_no_entries"


class RouteBackError(RuntimeError):
    """Base class for fail-loud Goal route-back termination."""

    failure_class = "route_back_error"

    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class NoCanonicalBound(RouteBackError):
    failure_class = "no_canonical_bound"


class CanonicalArtifactMissing(RouteBackError):
    failure_class = "canonical_artifact_missing"


class RouteBackLoop(RouteBackError):
    failure_class = "route_back_loop"


class OriginatingRunMissing(RouteBackError):
    failure_class = "originating_run_missing"


def route_back_evaluation(
    base_path: str | Path,
    *,
    evaluation: EvalResult,
    originating_run_id: str,
    provider_call: Callable[..., str] | None = None,
) -> RunOutcome:
    """Route a gate rejection to the originating actor's Goal canonical."""
    if evaluation.verdict != "route_back":
        raise ValueError("route_back_evaluation requires verdict='route_back'")
    if not evaluation.goal_id or not isinstance(evaluation.patch_notes, PatchNotes):
        raise ValueError("route_back evaluation is missing goal_id or PatchNotes")

    goal_id = evaluation.goal_id.strip()
    clean_run_id = originating_run_id.strip()
    from tinyassets.runs import get_run

    origin = get_run(base_path, clean_run_id) if clean_run_id else None
    if origin is None:
        raise OriginatingRunMissing(
            f"Originating run {clean_run_id!r} does not exist",
            details={"originating_run_id": clean_run_id},
        )
    run_actor = str(origin.get("actor") or "").strip()
    scope_actor = run_actor
    notes = PatchNotes.from_dict(evaluation.patch_notes.to_dict())
    hop = (goal_id, scope_actor)
    if hop in notes.route_history or len(notes.route_history) >= 3:
        raise RouteBackLoop(
            f"route back to {hop!r} repeats a hop or exceeds three hops",
            details={"history": notes.route_history, "cycle_detected_at": hop},
        )
    routed_notes = replace(notes, route_history=[*notes.route_history, hop])

    from tinyassets.daemon_server import resolve_goal_canonical

    try:
        branch_version_id = resolve_goal_canonical(
            base_path,
            goal_id=goal_id,
            scope_actor=scope_actor,
        )
    except KeyError as exc:
        raise NoCanonicalBound(
            f"Goal {goal_id!r} does not exist",
            details={"goal_id": goal_id, "scope_actor": scope_actor},
        ) from exc
    if not branch_version_id:
        raise NoCanonicalBound(
            f"Goal {goal_id!r} has no canonical bound for actor {scope_actor!r}",
            details={
                "goal_id": goal_id,
                "scope_actor": scope_actor,
                "fallback_attempted": ["default", "legacy"],
            },
        )

    from tinyassets.runs import execute_branch_version

    try:
        return execute_branch_version(
            base_path,
            branch_version_id=branch_version_id,
            inputs={"patch_notes": routed_notes.to_dict()},
            actor=run_actor,
            provider_call=provider_call,
        )
    except KeyError as exc:
        raise CanonicalArtifactMissing(
            f"Canonical Branch version {branch_version_id!r} is missing",
            details={
                "goal_id": goal_id,
                "scope_actor": scope_actor,
                "branch_version_id": branch_version_id,
            },
        ) from exc


def resolve_canonical_for_run(
    base_path: str | Path,
    *,
    goal_id: str,
    viewer: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Resolve which ``branch_version_id`` should fulfil a canonical run.

    Returns one of two shapes:

    Success::

        {
            "ok": True,
            "branch_version_id": "...",
            "branch_def_id": "...",
            "source": "<SOURCE_*>",
            "goal": <goal-row>,
            "refresh_attempted": <bool>,
            "displaced_canonical_branch_version_id": <str | None>,
            # Top-entry signals (only when refresh_attempted=True):
            "candidate_branch_def_id": <str | None>,
            "candidate_completed_runs": <int | None>,
            "candidate_score": <float | None>,
        }

    Failure::

        {
            "ok": False,
            "error": "<machine-readable>",
            "error_kind": "<one of: no_goal | no_canonical_handler |
                            no_published_version_for_candidate>",
            "goal_id": "...",
            "goal": <goal-row | None>,
        }

    Never raises — all storage / leaderboard failures surface as
    ``ok=False`` shapes the caller can render.

    Concurrency note: the in-flight detection window
    (:data:`IN_FLIGHT_WINDOW_SECONDS`) bounds the period during which
    a refresh is blocked by an active run on the prior canonical.
    Outside that window any pending/running row is treated as
    orphaned and does NOT block the refresh.
    """
    if now is None:
        now = time.time()

    from tinyassets.daemon_server import (
        get_goal,
        get_goal_canonical,
        resolve_goal_canonical,
    )

    try:
        goal = get_goal(base_path, goal_id=goal_id)
    except KeyError:
        return {
            "ok": False,
            "error": f"Goal '{goal_id}' not found.",
            "error_kind": "no_goal",
            "goal_id": goal_id,
            "goal": None,
        }
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("resolve_canonical_for_run | get_goal failed")
        return {
            "ok": False,
            "error": f"failed to load Goal '{goal_id}': {exc}",
            "error_kind": "goal_load_failed",
            "goal_id": goal_id,
            "goal": None,
        }

    from tinyassets.principals import named_principal

    scope_actor = named_principal(viewer)
    if scope_actor:
        try:
            actor_canonical = get_goal_canonical(
                base_path,
                goal_id=goal_id,
                scope_actor=scope_actor,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("resolve_canonical_for_run | actor lookup failed")
            return {
                "ok": False,
                "error": (
                    f"failed to load actor canonical for Goal "
                    f"'{goal_id}': {exc}"
                ),
                "error_kind": "goal_load_failed",
                "goal_id": goal_id,
                "goal": goal,
            }
        if actor_canonical is not None:
            actor_bvid = str(actor_canonical["branch_version_id"])
            _version_id, branch_def_id = _split_version_id(actor_bvid)
            return {
                "ok": True,
                "branch_version_id": actor_bvid,
                "branch_def_id": branch_def_id,
                "scope_actor": scope_actor,
                "source": SOURCE_ACTOR_CANONICAL,
                "goal": goal,
                "refresh_attempted": False,
                "displaced_canonical_branch_version_id": None,
            }

    stored_canonical = resolve_goal_canonical(
        base_path,
        goal_id=goal_id,
    )
    auto_flag = bool(goal.get("auto_canonical_via_leaderboard"))
    min_runs = int(goal.get("min_completed_runs_for_canonical") or 5)

    if not auto_flag:
        if not stored_canonical:
            return {
                "ok": False,
                "error": (
                    f"Goal '{goal_id}' has no canonical_branch_version_id "
                    "set, and auto_canonical_via_leaderboard is off. "
                    "Canonical selection is not exposed by the advertised "
                    "handles; an operator must set it or enable automatic "
                    "leaderboard selection through the internal admin surface."
                ),
                "error_kind": "no_canonical_handler",
                "goal_id": goal_id,
                "goal": goal,
            }
        bvid, bdid = _split_version_id(stored_canonical)
        return {
            "ok": True,
            "branch_version_id": stored_canonical,
            "branch_def_id": bdid,
            "source": SOURCE_CANONICAL_STORED,
            "scope_actor": "",
            "goal": goal,
            "refresh_attempted": False,
            "displaced_canonical_branch_version_id": None,
        }

    # auto_canonical_via_leaderboard=true — try to refresh.
    return _refresh_via_leaderboard(
        base_path,
        goal=goal,
        viewer=viewer,
        stored_canonical=stored_canonical,
        min_runs=min_runs,
        now=now,
    )


def is_in_flight_for_version(
    base_path: str | Path,
    *,
    branch_version_id: str,
    now: float,
    window_seconds: float = IN_FLIGHT_WINDOW_SECONDS,
) -> dict[str, Any] | None:
    """Return the most recent in-flight run on ``branch_version_id``, or None.

    "In-flight" = ``status`` ∈ {queued, running} AND ``started_at`` is
    within the trailing ``window_seconds``. Older queued/running rows do
    NOT block a refresh (recovery ends them once their owner is provably
    dead: ``runs.recover_in_flight_runs``).

    Returns the run row when present so the caller can surface
    ``in_flight_run_id`` / ``in_flight_started_at`` in evidence.
    """
    if not branch_version_id:
        return None
    cutoff = now - max(0.0, float(window_seconds))
    from tinyassets.runs import _connect, initialize_runs_db

    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT run_id, branch_def_id, branch_version_id, status,
                   started_at, finished_at
              FROM runs
             WHERE branch_version_id = ?
               AND status IN ('queued', 'running')
               AND started_at >= ?
          ORDER BY started_at DESC
             LIMIT 1
            """,
            (branch_version_id, cutoff),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "branch_def_id": row["branch_def_id"],
        "branch_version_id": row["branch_version_id"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _refresh_via_leaderboard(
    base_path: str | Path,
    *,
    goal: dict[str, Any],
    viewer: str,
    stored_canonical: str | None,
    min_runs: int,
    now: float,
) -> dict[str, Any]:
    """Re-query the leaderboard and (maybe) refresh ``canonical_branch_version_id``.

    Five outcomes:

    1. No entries on the leaderboard → fall back to stored canonical if
       set, otherwise no_canonical_handler error.
    2. Top entry has < min_runs completed runs → fall back to stored
       canonical (or no_canonical_handler).
    3. Top entry has no published branch_version → fall back to stored
       canonical (or no_canonical_handler).
    4. Top entry matches stored canonical's branch_def_id → no-op,
       return stored canonical.
    5. Top entry passes all checks AND no in-flight run is using the
       prior canonical → call set_canonical_branch + return the new
       version.

    The function NEVER raises; storage / leaderboard failures collapse
    to a fall-back path with the original stored canonical (if any).

    Round-2 P1.2 fix (Codex round-1 finding on PR #979): the auto-
    refresh decision MUST be computed against a strictly-public
    leaderboard view, regardless of which actor invoked
    ``run_canonical``. The round-1 path forwarded the caller's
    ``viewer`` (== ``_current_actor()``) into the leaderboard, which
    intentionally surfaces private branches authored by that viewer
    (PR-970's auth-boundary contract). The auto-refresh then wrote
    that private branch as the Goal's **global** canonical via
    ``set_canonical_branch``, exposing per-viewer private content to
    every other actor as the default dispatch target.

    Fix: hard-code the leaderboard query's viewer to ``""`` (strictly
    public) here. The caller's ``viewer`` parameter is retained for
    log telemetry — it identifies which actor triggered the refresh —
    but never reaches the leaderboard write decision.
    """
    goal_id = goal["goal_id"]
    from tinyassets.api.quality_leaderboard import build_quality_leaderboard

    if viewer:
        # Telemetry only — record which actor's run_canonical call
        # triggered this refresh, but do NOT scope the leaderboard
        # query to their viewer. Canonical is global by definition.
        logger.info(
            "canonical_refresh | goal=%s | triggered_by=%s | "
            "using_viewer='' for global decision",
            goal_id, viewer,
        )

    try:
        board = build_quality_leaderboard(
            # P1.2 fix: hard-coded empty viewer so private branches
            # cannot become the global canonical. The caller-supplied
            # viewer is intentionally ignored at this seam.
            base_path, goal_id=goal_id, viewer="", now=now,
        )
    except Exception:
        logger.exception(
            "resolve_canonical_for_run | leaderboard query crashed for "
            "%s; falling back to stored canonical", goal_id,
        )
        board = {"entries": []}

    entries = board.get("entries") or []
    if not entries:
        if stored_canonical:
            bvid, bdid = _split_version_id(stored_canonical)
            return {
                "ok": True,
                "branch_version_id": stored_canonical,
                "branch_def_id": bdid,
                "source": SOURCE_LEADERBOARD_NO_ENTRIES,
                "goal": goal,
                "refresh_attempted": True,
                "displaced_canonical_branch_version_id": None,
            }
        return _no_canonical_response(
            goal_id=goal_id, goal=goal,
            hint=(
                "auto_canonical_via_leaderboard is on, but no Branches "
                "are bound to this Goal yet. Goal-to-branch binding is not "
                "exposed by the advertised handles; an operator must bind a "
                "branch before canonical selection can proceed."
            ),
        )

    top = entries[0]
    candidate_bdid = top.get("branch_def_id") or ""
    candidate_signals = top.get("signals") or {}
    candidate_completed = int(
        candidate_signals.get("completed_run_count") or 0
    )
    candidate_score = float(top.get("score") or 0.0)

    # Threshold guard — security gate against a malicious branch
    # ranking high with zero / few actual runs.
    if candidate_completed < min_runs:
        if stored_canonical:
            bvid, bdid = _split_version_id(stored_canonical)
            return {
                "ok": True,
                "branch_version_id": stored_canonical,
                "branch_def_id": bdid,
                "source": SOURCE_LEADERBOARD_SKIPPED_INSUFFICIENT_RUNS,
                "goal": goal,
                "refresh_attempted": True,
                "displaced_canonical_branch_version_id": None,
                "candidate_branch_def_id": candidate_bdid,
                "candidate_completed_runs": candidate_completed,
                "candidate_score": candidate_score,
                "min_completed_runs_for_canonical": min_runs,
                "hint": (
                    f"Top entry '{candidate_bdid}' has "
                    f"{candidate_completed} completed run(s); the "
                    f"Goal requires >= {min_runs}. Keeping the stored "
                    "canonical."
                ),
            }
        return _no_canonical_response(
            goal_id=goal_id, goal=goal,
            hint=(
                f"Top leaderboard entry '{candidate_bdid}' has "
                f"{candidate_completed} completed run(s); the Goal "
                f"requires >= {min_runs}. No prior canonical is set, "
                "so the dispatch is blocked. Either lower "
                "min_completed_runs_for_canonical or run the branch "
                "more times before retrying."
            ),
        )

    # Resolve top entry's latest published version. The leaderboard
    # ranks branch_def_id values; the canonical surface keys on
    # branch_version_id (the immutable snapshot). We pick the most
    # recently published version for the top branch_def_id.
    candidate_bvid = _latest_published_version_id(
        base_path, branch_def_id=candidate_bdid,
    )
    if not candidate_bvid:
        # Branch_def is bound to the Goal but has not been published.
        # Fall back to stored canonical if any.
        if stored_canonical:
            bvid, bdid = _split_version_id(stored_canonical)
            return {
                "ok": True,
                "branch_version_id": stored_canonical,
                "branch_def_id": bdid,
                "source": SOURCE_LEADERBOARD_SKIPPED_NO_PUBLISHED_VERSION,
                "goal": goal,
                "refresh_attempted": True,
                "displaced_canonical_branch_version_id": None,
                "candidate_branch_def_id": candidate_bdid,
                "candidate_completed_runs": candidate_completed,
                "candidate_score": candidate_score,
                "hint": (
                    f"Top entry '{candidate_bdid}' has no published "
                    "branch_version. Keeping the stored canonical. "
                    "Branch-version publication is not exposed by the "
                    "advertised handles; ask an operator to publish it "
                    "through the internal admin surface."
                ),
            }
        return _no_canonical_response(
            goal_id=goal_id, goal=goal,
            hint=(
                f"Top entry '{candidate_bdid}' has no published "
                "branch_version, and no prior canonical is set. "
                "Publish the branch first."
            ),
        )

    # Did the candidate's branch_def_id already match the stored
    # canonical's branch_def_id? If so, the candidate is effectively
    # the same handler — no swap needed, but we DO swap to the latest
    # version of that def_id so future runs land on the freshest
    # snapshot. Two sub-cases:
    #   (a) Stored canonical IS the latest version → no-op.
    #   (b) Stored canonical points to an older version of the same
    #       def_id → swap to the latest.
    if stored_canonical and stored_canonical == candidate_bvid:
        # Already on the freshest version of the top entry.
        return {
            "ok": True,
            "branch_version_id": stored_canonical,
            "branch_def_id": candidate_bdid,
            "source": SOURCE_LEADERBOARD_NO_CHANGE,
            "goal": goal,
            "refresh_attempted": True,
            "displaced_canonical_branch_version_id": None,
            "candidate_branch_def_id": candidate_bdid,
            "candidate_completed_runs": candidate_completed,
            "candidate_score": candidate_score,
        }

    # We have a different version to swap in. Check for in-flight runs
    # on the prior canonical before swapping.
    in_flight = None
    if stored_canonical:
        in_flight = is_in_flight_for_version(
            base_path,
            branch_version_id=stored_canonical,
            now=now,
        )
    if in_flight is not None:
        bvid, bdid = _split_version_id(stored_canonical or "")
        return {
            "ok": True,
            "branch_version_id": stored_canonical,
            "branch_def_id": bdid,
            "source": SOURCE_LEADERBOARD_SKIPPED_IN_FLIGHT,
            "goal": goal,
            "refresh_attempted": True,
            "displaced_canonical_branch_version_id": None,
            "candidate_branch_def_id": candidate_bdid,
            "candidate_completed_runs": candidate_completed,
            "candidate_score": candidate_score,
            "in_flight_run_id": in_flight.get("run_id"),
            "in_flight_status": in_flight.get("status"),
            "in_flight_started_at": in_flight.get("started_at"),
            "hint": (
                f"A run on the prior canonical "
                f"('{stored_canonical}') is currently "
                f"{in_flight.get('status')}; refusing to swap canonical "
                "out from under it. Retry after the in-flight run "
                "completes."
            ),
        }

    # Safe to swap.
    set_by = _resolve_actor_for_history()
    refreshed = _attempt_set_canonical(
        base_path,
        goal_id=goal_id,
        branch_version_id=candidate_bvid,
        set_by=set_by,
    )
    if not refreshed["ok"]:
        # set_canonical failed (e.g. version validation). Fall back to
        # stored canonical if any.
        if stored_canonical:
            bvid, bdid = _split_version_id(stored_canonical)
            return {
                "ok": True,
                "branch_version_id": stored_canonical,
                "branch_def_id": bdid,
                "source": SOURCE_LEADERBOARD_SKIPPED_NO_PUBLISHED_VERSION,
                "goal": goal,
                "refresh_attempted": True,
                "displaced_canonical_branch_version_id": None,
                "candidate_branch_def_id": candidate_bdid,
                "candidate_completed_runs": candidate_completed,
                "candidate_score": candidate_score,
                "hint": (
                    f"set_canonical rejected candidate "
                    f"'{candidate_bvid}': {refreshed.get('error')}. "
                    "Keeping the stored canonical."
                ),
            }
        return _no_canonical_response(
            goal_id=goal_id, goal=goal,
            hint=(
                f"set_canonical rejected candidate '{candidate_bvid}': "
                f"{refreshed.get('error')}. No prior canonical to fall "
                "back to."
            ),
        )

    return {
        "ok": True,
        "branch_version_id": candidate_bvid,
        "branch_def_id": candidate_bdid,
        "source": SOURCE_LEADERBOARD_REFRESHED,
        "goal": refreshed.get("goal") or goal,
        "refresh_attempted": True,
        "displaced_canonical_branch_version_id": stored_canonical,
        "candidate_branch_def_id": candidate_bdid,
        "candidate_completed_runs": candidate_completed,
        "candidate_score": candidate_score,
    }


def _attempt_set_canonical(
    base_path: str | Path,
    *,
    goal_id: str,
    branch_version_id: str,
    set_by: str,
) -> dict[str, Any]:
    """Wrap :func:`tinyassets.daemon_server.set_canonical_branch` so the
    leaderboard refresher never raises. Returns ``{ok: bool, error?,
    goal?}``.
    """
    from tinyassets.daemon_server import set_canonical_branch

    try:
        updated = set_canonical_branch(
            base_path,
            goal_id=goal_id,
            branch_version_id=branch_version_id,
            set_by=set_by,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except KeyError as exc:
        return {"ok": False, "error": f"Goal not found: {exc}"}
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("set_canonical_branch crashed")
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "goal": updated}


def _latest_published_version_id(
    base_path: str | Path, *, branch_def_id: str,
) -> str | None:
    """Return the newest **active** ``branch_version_id`` for the def,
    or None when no active published version exists.

    Round-2 P1.1 fix (Codex round-1 finding on PR #979): the round-1
    implementation returned ``versions[0].branch_version_id`` for the
    most recently published version regardless of status, which let a
    rolled-back version become the auto-refresh target. The repro:
    publish a version, roll it back, give the underlying branch_def
    enough leaderboard signal to rank first, call run_canonical with
    auto_canonical_via_leaderboard=true -> the rolled-back version
    was selected (source=leaderboard_refreshed).

    Fix: filter the version list to ``status == 'active'`` so rolled-
    back / superseded versions are NEVER returned to the auto-refresh
    path. Defense in depth: :func:`tinyassets.daemon_server.set_canonical_branch`
    also rejects non-active versions, so a bug in this filter alone
    can't promote a dead version.
    """
    if not branch_def_id:
        return None
    from tinyassets.branch_versions import list_branch_versions

    try:
        # Pull a window of recent versions and filter for status='active'
        # in Python — list_branch_versions does not expose a status
        # filter today, and adding one would require touching its
        # signature which is out of scope for this round. A small
        # constant window (default-50 of the helper) covers the
        # realistic case where the latest few versions include at most
        # a handful of rolled-back rows.
        versions = list_branch_versions(
            base_path, branch_def_id=branch_def_id, limit=50,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "list_branch_versions crashed for %s", branch_def_id,
        )
        return None
    for version in versions:
        if version.public and getattr(version, "status", "active") == "active":
            bvid = version.branch_version_id or ""
            if bvid:
                return bvid
    return None


def _split_version_id(version_id: str) -> tuple[str, str]:
    """Best-effort ``branch_def_id`` extraction from a version id.

    Published versions are minted as ``<branch_def_id>@<sha256_prefix8>``
    (see ``tinyassets.branch_versions``); the def_id is the prefix before
    the ``@``. Returns ``(version_id, "")`` when the input has no ``@``
    (defensive — keeps the response shape uniform even if a caller
    feeds in a malformed id).
    """
    if not version_id:
        return "", ""
    if "@" in version_id:
        bdid, _ = version_id.split("@", 1)
        return version_id, bdid
    return version_id, ""


def _no_canonical_response(
    *,
    goal_id: str,
    goal: dict[str, Any] | None,
    hint: str = "",
) -> dict[str, Any]:
    """Standard `no_canonical_handler` failure shape."""
    payload: dict[str, Any] = {
        "ok": False,
        "error": (
            f"Goal '{goal_id}' has no canonical_branch_version_id "
            "available for dispatch."
        ),
        "error_kind": "no_canonical_handler",
        "goal_id": goal_id,
        "goal": goal,
    }
    if hint:
        payload["hint"] = hint
    return payload


def _resolve_actor_for_history() -> str:
    """Identity recorded on the canonical history audit row.

    Auto-refresh writes are system-driven, not user-driven; tagging
    them with a synthetic ``auto-canonical-refresh`` author keeps the
    audit trail honest (matches the ``cloud-droplet`` identity pattern
    documented in AGENTS.md for system-driven writes).
    """
    return "auto-canonical-refresh"


__all__ = [
    "CanonicalArtifactMissing",
    "IN_FLIGHT_WINDOW_SECONDS",
    "SOURCE_CANONICAL_STORED",
    "SOURCE_LEADERBOARD_REFRESHED",
    "SOURCE_LEADERBOARD_NO_CHANGE",
    "SOURCE_LEADERBOARD_SKIPPED_INSUFFICIENT_RUNS",
    "SOURCE_LEADERBOARD_SKIPPED_IN_FLIGHT",
    "SOURCE_LEADERBOARD_SKIPPED_NO_PUBLISHED_VERSION",
    "SOURCE_LEADERBOARD_NO_ENTRIES",
    "NoCanonicalBound",
    "OriginatingRunMissing",
    "RouteBackError",
    "RouteBackLoop",
    "resolve_canonical_for_run",
    "route_back_evaluation",
    "is_in_flight_for_version",
]
