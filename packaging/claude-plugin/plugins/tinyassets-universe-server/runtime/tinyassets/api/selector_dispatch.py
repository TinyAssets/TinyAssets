"""User-buildable selector branch dispatch — DESIGN-008.

The architectural commitment from host's 2026-05-21 reframe: selection
logic is per-Goal user-buildable, NOT platform-coded. ``quality_leaderboard``
no longer applies an opinionated formula; instead it dispatches a
**selector branch** (a published TinyAssets branch bound to the Goal)
that consumes signal data and emits ranked entries.

**Retired by AGENTS.md Hard Rule 15 (the platform has no LLM, 2026-09-24).**
A selector run was a platform model call: it ran on the raw ``call_provider``
with no universe, which the router served from the host's own CLI login, for
whoever happened to read the leaderboard. Ranking is platform operation, so
:func:`dispatch_selector` now fails closed with ``selector_retired`` and runs
nothing. It is not rewired to any universe (founder, 2026-09-24): leaderboards
and selection are superseded by user-built workflows. Resolution, the default
selector publication and the selector binding columns remain only until the
follow-up deletion listed in
``docs/reviews/2026-09-24-platform-llm-call-audit.md``.

This module owns:

* :func:`resolve_selector_branch_version_id` — return the selector
  branch_version_id for a Goal. If ``goal.selector_branch_version_id``
  is set, use it. Otherwise return the platform default selector's
  branch_version_id (lazily published on first call so the substrate
  works on a clean DB).
* :func:`dispatch_selector` — retired; fails closed with
  ``selector_retired`` (see above).
* :func:`ensure_default_selector_published` — idempotent helper that
  builds + publishes the platform default selector branch on first
  call. The default is a single prompt-template node that consumes
  the signal map and asks the LLM to rank — the "weights" are a
  prompt the chatbot can tune via fork, not Python constants.

The historical selector-branch contract (inputs ``goal_id`` +
``candidate_branches`` with signals, output ``ranked_entries``) is in
``drafts/concepts/selector-branch-contract.md`` and in git history.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Deterministic branch_def_id for the platform default selector.
# Tests rely on this being stable across processes.
DEFAULT_SELECTOR_BRANCH_DEF_ID = "platform_default_selector_v1_20260521"
DEFAULT_SELECTOR_NAME = "Platform Default Selector v1"
DEFAULT_SELECTOR_AUTHOR = "platform"
DEFAULT_SELECTOR_PUBLISHER = "platform"


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------


def resolve_selector_branch_version_id(
    base_path: str | Path,
    *,
    goal_id: str,
) -> dict[str, Any]:
    """Return the selector branch_version_id for the Goal.

    Resolution order:

      1. ``goal.selector_branch_version_id`` if set.
      2. Otherwise, the platform default selector's
         ``branch_version_id`` (published lazily by
         ``ensure_default_selector_published``).

    Returns ``{"ok": True, "branch_version_id": "...", "source":
    "goal_binding" | "platform_default"}`` on success or
    ``{"ok": False, "error_kind": "...", "error": "..."}`` on
    failure. Never raises.
    """
    try:
        from tinyassets.daemon_server import get_goal
        goal = get_goal(base_path, goal_id=goal_id)
    except KeyError:
        return {
            "ok": False,
            "error_kind": "goal_not_found",
            "error": f"Goal {goal_id!r} not found.",
        }
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("resolve_selector | get_goal crashed")
        return {
            "ok": False,
            "error_kind": "goal_load_failed",
            "error": str(exc),
        }

    explicit = (goal.get("selector_branch_version_id") or "").strip() or None
    # P1.C (DESIGN-008 round 4) — re-check the bound version's status
    # at READ/DISPATCH time. Bind-time enforces ``status='active'`` (see
    # ``set_selector_branch``), but the version can be rolled back AFTER
    # bind by a separate ``branch_versions.status`` flip or by a
    # rollback operation. Without this re-check, a rolled-back selector
    # keeps running on every leaderboard read — exactly the bug Codex
    # round 3 caught. Mirrors the round-2 P1.1 contract on
    # ``set_canonical_branch`` / ``_latest_published_version_id``.
    #
    # When the bound version is no longer active, fall back to the
    # platform default with a structured ``fellback_from`` diagnostic
    # so the chatbot / audit tools can flag the stale binding and the
    # operator can re-bind or unbind.
    fellback_from: dict[str, str] | None = None
    if explicit:
        try:
            from tinyassets.branch_versions import get_branch_version
            version = get_branch_version(base_path, explicit)
        except Exception:
            logger.exception(
                "resolve_selector | get_branch_version crashed for "
                "goal=%s explicit=%s", goal_id, explicit,
            )
            version = None
        if version is None:
            fellback_from = {
                "branch_version_id": explicit,
                "reason": "selector_version_not_found",
            }
            logger.warning(
                "selector binding for goal=%s points at "
                "branch_version_id=%r which is no longer in "
                "branch_versions; falling back to platform default",
                goal_id, explicit,
            )
            explicit = None
        else:
            status = getattr(version, "status", "active") or "active"
            if status != "active":
                fellback_from = {
                    "branch_version_id": explicit,
                    "reason": "selector_version_inactive",
                    "status": status,
                }
                logger.warning(
                    "selector binding for goal=%s points at "
                    "branch_version_id=%r with status=%r "
                    "(active-only required); falling back to "
                    "platform default. Operator: use the internal selector "
                    "binding surface to bind an active version, or unbind "
                    "with branch_version_id=''",
                    goal_id, explicit, status,
                )
                explicit = None

    if explicit:
        return {
            "ok": True,
            "branch_version_id": explicit,
            "source": "goal_binding",
        }

    # Fall back to the platform default selector. This path is
    # reached for: no explicit binding, OR an explicit binding that
    # is no longer active (see fellback_from above).
    try:
        default_bvid = ensure_default_selector_published(base_path)
    except Exception as exc:
        logger.exception("resolve_selector | default-selector publish crashed")
        return {
            "ok": False,
            "error_kind": "default_selector_publish_failed",
            "error": str(exc),
        }
    if not default_bvid:
        return {
            "ok": False,
            "error_kind": "default_selector_unavailable",
            "error": (
                "Platform default selector branch could not be "
                "published; no fallback selector available."
            ),
        }
    result: dict[str, Any] = {
        "ok": True,
        "branch_version_id": default_bvid,
        "source": "platform_default",
    }
    if fellback_from is not None:
        # Distinguish "no binding" platform_default from "binding
        # invalidated by rollback" platform_default. Programmatic
        # consumers can spot stale bindings without scraping logs.
        result["fellback_from"] = fellback_from
    return result


# ---------------------------------------------------------------------------
# Default selector materialization
# ---------------------------------------------------------------------------


def ensure_default_selector_published(base_path: str | Path) -> str:
    """Ensure the platform default selector branch + active version exist.

    Returns the active ``branch_version_id``. Idempotent: if the
    branch_def already exists AND has an active published version,
    that version's id is returned without re-publishing.

    The branch is a single prompt-template node:

      * Input keys: ``goal_id``, ``candidate_branches`` (the signal
        bundle the leaderboard collects).
      * Output keys: ``ranked_entries`` (JSON array of
        ``{branch_def_id, branch_version_id, score, rationale}``).

    The prompt explicitly instructs the LLM to weight signals and
    emit valid JSON. Community forks of this branch tune the prompt
    rather than Python constants.
    """
    from tinyassets.branch_versions import (
        get_branch_version,
        list_branch_versions,
        publish_branch_version,
    )
    from tinyassets.daemon_server import (
        get_branch_definition,
        save_branch_definition,
    )

    # Step 1: ensure the branch_def exists.
    try:
        existing = get_branch_definition(
            base_path, branch_def_id=DEFAULT_SELECTOR_BRANCH_DEF_ID,
        )
    except KeyError:
        existing = None

    if existing is None:
        branch_dict = _build_default_selector_branch_dict()
        save_branch_definition(base_path, branch_def=branch_dict)

    # Step 2: find an active published version, else publish one.
    versions = list_branch_versions(
        base_path,
        branch_def_id=DEFAULT_SELECTOR_BRANCH_DEF_ID,
        limit=50,
    )
    for v in versions:
        if getattr(v, "status", "active") == "active":
            return v.branch_version_id

    # No active version — publish v1.
    #
    # P1 (DESIGN-008 round 5): ``publish_branch_version`` is deterministic
    # in ``(branch_def_id, content_hash)``. When an operator has rolled
    # back the platform default (every existing version's status flipped
    # to ``rolled_back``), the re-publish call returns the same existing
    # rolled-back row (tinyassets/branch_versions.py:267-273) rather than
    # minting a fresh active one. Without the post-publish status check
    # below, ``ensure_default_selector_published`` would silently hand
    # back a rolled-back branch_version_id, and ``dispatch_selector``
    # would execute it — defeating the rollback. Fail closed instead:
    # return empty so the caller (``resolve_selector_branch_version_id``)
    # surfaces ``default_selector_unavailable``.
    branch_dict = _build_default_selector_branch_dict()
    version = publish_branch_version(
        base_path,
        branch_dict=branch_dict,
        publisher=DEFAULT_SELECTOR_PUBLISHER,
        public=True,
        notes=(
            "Platform default selector v1 — single prompt-template "
            "node ranking candidate branches from collected signals. "
            "Community forks customize the prompt per domain."
        ),
    )
    # Sanity: re-read to confirm row landed + status.
    persisted = get_branch_version(base_path, version.branch_version_id)
    if persisted is None:
        raise RuntimeError(
            "publish_branch_version reported success but the row is "
            "not readable post-write."
        )
    status = getattr(persisted, "status", "active") or "active"
    if status != "active":
        logger.warning(
            "ensure_default_selector_published | publish_branch_version "
            "returned existing branch_version_id=%r with status=%r "
            "(deterministic re-publish hit a rolled-back row). Cannot "
            "dispatch a rolled-back default selector. Operator: "
            "republish the platform default branch with a content "
            "change (so a fresh content_hash mints a new active row), "
            "or bind a custom selector to the affected Goal through "
            "the internal selector-binding surface.",
            persisted.branch_version_id, status,
        )
        return ""
    return persisted.branch_version_id


def _build_default_selector_branch_dict() -> dict[str, Any]:
    """Construct the default selector's BranchDefinition dict.

    Kept as a function (not a constant) so each call returns a fresh
    dict the storage layer can mutate without aliasing.
    """
    prompt = _DEFAULT_SELECTOR_PROMPT
    node = {
        "node_id": "rank",
        "display_name": "Rank Candidates",
        "description": (
            "Read candidate_branches + their signals; emit "
            "ranked_entries JSON in score-desc order."
        ),
        "phase": "custom",
        "input_keys": ["goal_id", "candidate_branches"],
        "output_keys": ["ranked_entries"],
        "prompt_template": prompt,
        "model_hint": "writer",
        # The prompt-template node consumes ``candidate_branches`` —
        # a structured JSON-able list. The default prompt-template
        # renderer treats input_keys as a dict the template's
        # placeholders are formatted against; the strict_input_isolation
        # default (True) keeps the prompt from leaking state it
        # didn't declare.
    }
    return {
        "branch_def_id": DEFAULT_SELECTOR_BRANCH_DEF_ID,
        "name": DEFAULT_SELECTOR_NAME,
        "description": (
            "Platform default selector — ranks candidate branches "
            "for a Goal's leaderboard using LLM judgment over "
            "collected signals. Fork + tune the prompt for "
            "domain-specific selection."
        ),
        "author": DEFAULT_SELECTOR_AUTHOR,
        "domain_id": "workflow",
        "tags": ["selector", "platform-default", "leaderboard"],
        "version": 1,
        "skills": [],
        "entry_point": "rank",
        "graph_nodes": [
            {
                "id": "rank",
                "type": "prompt",
                "phase": "custom",
                "input_keys": ["goal_id", "candidate_branches"],
                "output_keys": ["ranked_entries"],
            },
        ],
        "edges": [
            {"from": "START", "to": "rank"},
            {"from": "rank", "to": "END"},
        ],
        "node_defs": [node],
        # state_schema types are intentionally all ``str``: the
        # ``ranked_entries`` output is emitted as a JSON-string by the
        # prompt-template node, then the substrate parses it via
        # ``_parse_ranked_entries`` (which tolerates JSON-string +
        # markdown-fence shapes). Declaring ``list`` here would force
        # the graph_compiler's JSON-contract code path on the response,
        # which assumes native structured-output and rejects any
        # provider that returns the JSON as a plain string. Selectors
        # talk to many providers (CLI-wrapped Claude / codex / ollama)
        # where structured-output isn't wirable.
        "state_schema": [
            {"name": "goal_id", "type": "str"},
            {"name": "candidate_branches", "type": "str"},
            {"name": "ranked_entries", "type": "str"},
        ],
        "published": True,
        "visibility": "public",
    }


# The default selector prompt. The "weights" are now words that any
# Goal owner can fork and rewrite for their domain (e.g. fantasy-
# writing might rank "novelty" higher; bug-investigation might
# weight "completed_run_count" + "judgment_score_avg" almost
# exclusively). See ``drafts/concepts/selector-branch-contract.md``
# for the full input/output spec.
_DEFAULT_SELECTOR_PROMPT = """\
You are the **platform default selector** for a TinyAssets Goal's
leaderboard. Your job is to rank candidate branches that are competing
on the same Goal and emit a JSON array describing the ranking.

## Goal context
goal_id: {goal_id}

## Candidates with collected signals
{candidate_branches}

## Ranking guidance (default — fork this branch to customize)

Consider, in roughly this order of importance:

1. **judgment_score_avg** — average of numeric judgment tags
   (quality/novelty/score). When non-null this is the strongest
   single signal. Treat as ~0-10.
2. **completed_run_count** — more completed runs = more evidence
   this branch actually works. Penalty for `failed_run_count`.
3. **fork_count** — community votes with their forks; high
   fork_count signals influence + endorsement.
4. **last_successful_run_at + age_days_since_success** — recency
   matters; a branch that succeeded yesterday outranks one that
   succeeded six months ago, all else equal.
5. **has_gate_rung / gate_rung_top** — branches that have climbed
   the Goal's gate ladder (real-world impact) outrank those that
   have not.
6. **safe_to_publish** — when present, a strong positive signal.

Branches with no completed runs and no judgments rank below
branches that have *any* signal. A branch with only one completed
run but high judgment_score_avg can still rank well.

## Output

Emit ONLY a JSON object (no markdown fence, no prose before or
after) with the following shape:

```
{{"ranked_entries":[
  {{
    "branch_def_id":"...",
    "branch_version_id":"...",
    "score":<float>,
    "rationale":"<one sentence>"
  }},
  ...
]}}
```

Order entries best-first. Use the `branch_version_id` from each
candidate if non-empty, otherwise the empty string. `score` should
be on a 0.0-10.0 scale; ties are acceptable. `rationale` is a single
sentence naming the dominant signals (e.g. "highest judgment avg
(8.4) with 12 completed runs and 3 community forks").

If `candidate_branches` is empty, return `{{"ranked_entries":[]}}`.
"""


# ---------------------------------------------------------------------------
# Selector dispatch -- retired (Hard Rule 15)
# ---------------------------------------------------------------------------

SELECTOR_RETIRED_MESSAGE = (
    "The platform has no LLM, so leaderboard selectors no longer run: a "
    "selector was a model call the platform made for whoever read the "
    "leaderboard, not work inside an owner's command center. Rank or pick branches "
    "with your own workflow instead."
)


def dispatch_selector(
    base_path: str | Path,
    *,
    goal_id: str,
    candidate_branches: list[dict[str, Any]],
    actor: str = "",
    timeout_s: float | None = None,
    provider_call: Any = None,
) -> dict[str, Any]:
    """Fail closed: selector ranking is a platform model call, and there is none.

    An empty candidate set still short-circuits to an empty ranking (no model
    was ever involved). Anything else returns ``selector_retired`` without
    resolving, publishing or running a branch, and without touching a
    provider. The keyword arguments are accepted so existing callers keep
    their shape; none of them can re-enable a run.
    """
    del base_path, actor, timeout_s, provider_call
    if not candidate_branches:
        return {
            "ok": True,
            "branch_version_id": None,
            "source": "empty_candidate_set",
            "run_id": None,
            "ranked_entries": [],
        }
    logger.info("selector dispatch refused for goal=%s: selector_retired", goal_id)
    return {
        "ok": False,
        "error_kind": "selector_retired",
        "error": SELECTOR_RETIRED_MESSAGE,
    }
