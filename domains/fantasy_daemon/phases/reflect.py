"""Reflect node -- universe graph; invoked by dispatch_execution for cross-series coherence review.

When a MemoryManager is present, runs the Reflexion engine to
self-critique recent quality trends and update memory weights.

Also reviews canon quality against the premise/source material,
rewriting drifted files when necessary.  Reflection is triggered by
creative signals (worldbuild signals, new facts, contradictions) rather
than timer-based cooldowns.

Contract
--------
Input:  UniverseState.
Output: Partial UniverseState with quality_trace entry.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from tinyassets.enrichment_signals import load_enrichment_signals, state_enrichment_signals
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.ingestion.canon_io import iter_canon_files, safe_canon_path
from tinyassets.universe_soul import premise_from_soul, read_legacy_premise
from tinyassets.utils.json_parsing import parse_llm_json

logger = logging.getLogger(__name__)

# At most this many canon files will be rewritten per cycle.
_MAX_REWRITES_PER_CYCLE = 2


def reflect(state: dict[str, Any]) -> dict[str, Any]:
    """Perform reflection, creative direction review, and canon quality check.

    1. Run the reflexion engine (self-critique on quality trends).
    2. Review canon files against the premise for drift.
    3. Rewrite the most-drifted files (up to ``_MAX_REWRITES_PER_CYCLE``).

    Parameters
    ----------
    state : UniverseState
        Full universe state.

    Returns
    -------
    dict
        Partial state with ``quality_trace`` entry.
    """
    from tinyassets import runtime_singletons as runtime

    trace: dict[str, Any] = {
        "node": "reflect",
        "action": "reflect",
        "reflexion_ran": False,
        "canon_files_reviewed": 0,
        "canon_files_rewritten": [],
    }

    # --- 1. Reflexion engine ---
    mgr = runtime.memory_manager
    if mgr is not None:
        try:
            result = mgr.run_reflexion(state)
            logger.info(
                "Reflexion: critique=%s, weights_updated=%d",
                result.critique[:80] if result.critique else "none",
                len(result.updated_weights),
            )
            trace["reflexion_ran"] = True
        except ProviderAuthorityHeldError:
            # Includes PlatformLLMCallRefusedError (Hard Rule 15): a refused
            # model call propagates; it is never logged away as a failed step.
            raise
        except Exception as e:
            logger.warning("Reflexion failed: %s", e)

    # --- 2. Canon quality review ---
    try:
        review_result = _review_canon_quality(state)
        trace["canon_files_reviewed"] = review_result.get("files_reviewed", 0)
        trace["canon_files_rewritten"] = review_result.get(
            "files_rewritten", []
        )
    except Exception as e:
        logger.warning("Canon quality review failed: %s", e)

    return {"quality_trace": [trace]}


# ---------------------------------------------------------------------------
# Canon quality review
# ---------------------------------------------------------------------------


def _review_canon_quality(state: dict[str, Any]) -> dict[str, Any]:
    """Review canon files against the premise and rewrite drifted ones.

    When worldbuild signals are available, focuses review on the specific
    topics that triggered the signals.  Otherwise reviews all canon files.

    Returns a summary dict with ``files_reviewed`` and ``files_rewritten``.
    """
    universe_path = state.get(
        "_universe_path", state.get("universe_path", "")
    )
    if not universe_path:
        logger.debug("No universe path; skipping canon review")
        return {"files_reviewed": 0, "files_rewritten": []}

    universe_dir = Path(universe_path)
    canon_dir = universe_dir / "canon"

    if not canon_dir.exists():
        logger.debug("No canon directory; skipping canon review")
        return {"files_reviewed": 0, "files_rewritten": []}

    # Read premise
    premise = _read_premise(universe_dir, state)
    if not premise:
        logger.info("No premise found; skipping canon review")
        return {"files_reviewed": 0, "files_rewritten": []}

    # Extract signal topics to focus the review (if any)
    signal_topics = _extract_signal_topics(state, universe_dir)

    # Collect canon files eligible for review
    canon_files = _collect_reviewable_canon(canon_dir, signal_topics)
    if not canon_files:
        logger.debug("No canon files eligible for review")
        return {"files_reviewed": 0, "files_rewritten": []}

    # Evaluate canon files via LLM
    issues = _evaluate_canon(canon_files, premise)
    files_reviewed = len(canon_files)

    # Determine current model tier
    current_model = ""
    try:
        # Probe which provider is currently active
        from tinyassets import runtime_singletons as runtime
        if runtime.memory_manager is not None:
            # The provider name is tracked in state from the last call
            current_model = state.get("provider", "")
    except Exception:
        pass
    current_tier = _model_tier(current_model)

    # Rewrite the most-drifted files
    rewritten: list[str] = []
    for issue in issues[:_MAX_REWRITES_PER_CYCLE]:
        filename = issue.get("filename", "")
        reason = issue.get("reason", "drift detected")
        # ``filename`` is LLM-supplied (from ``_evaluate_canon`` output), so a
        # ``../`` traversal or symlinked name could let the rewrite ``write_text``
        # below clobber a file outside the canon sandbox. Resolve + contain
        # before any ``exists`` / read / write.
        try:
            filepath = safe_canon_path(canon_dir, filename, kind="rewrite target")
        except ValueError:
            logger.warning(
                "Skipping canon rewrite escaping canon dir: %r", filename
            )
            continue
        if not filepath.exists():
            continue

        # Evidence-based rewrite guard: weaker models need stronger
        # evidence to overwrite.  Nothing is untouchable — even user
        # edits can be fixed if there's a genuine contradiction.
        file_model = _get_file_model(filepath)
        file_tier = _model_tier(file_model)
        severity = issue.get("severity", 5)
        if not _rewrite_justified(current_tier, file_tier, severity):
            logger.info(
                "Skipping rewrite of %s: written by %s (tier %d), "
                "current model %s (tier %d), severity %.1f too low",
                filename, file_model, file_tier,
                current_model, current_tier, severity,
            )
            continue

        try:
            new_content = _rewrite_canon_file(
                filename, premise, issue, canon_files
            )
            if new_content:
                filepath.write_text(new_content, encoding="utf-8")
                _stamp_reviewed(filepath, model=current_model)
                rewritten.append(filename)
                logger.info(
                    "Rewrote drifted canon file %s: %s", filename, reason
                )
        except Exception as e:
            logger.warning("Failed to rewrite %s: %s", filename, e)

    # Stamp reviewed files that were NOT rewritten. ``canon_files`` keys come
    # from ``iter_canon_files`` (already contained), but resolve + contain
    # again so the stamp marker derived from this path cannot escape.
    for filename in canon_files:
        if filename in rewritten:
            continue
        try:
            filepath = safe_canon_path(canon_dir, filename, kind="reviewed file")
        except ValueError:
            logger.warning(
                "Skipping reviewed-stamp escaping canon dir: %r", filename
            )
            continue
        if filepath.exists():
            _stamp_reviewed(filepath, model=current_model)

    return {"files_reviewed": files_reviewed, "files_rewritten": rewritten}


def _read_premise(universe_dir: Path, state: dict[str, Any]) -> str:
    """Read the story premise from PROGRAM.md, soul.md, or state."""
    content = read_legacy_premise(universe_dir).strip()
    if content:
        return content
    content = premise_from_soul(universe_dir).strip()
    if content:
        return content
    return state.get("premise_kernel", "")


def _extract_signal_topics(
    state: dict[str, Any], universe_dir: Path
) -> set[str] | None:
    """Extract topic slugs from worldbuild signals for focused review.

    Returns a set of topic slugs if signals are available, or None if
    no signals are present (which means review all files).
    """
    topics: set[str] = set()

    # Check state for signals
    signals = state_enrichment_signals(state)

    # Check file-based signals
    if not signals:
        signals = load_enrichment_signals(universe_dir)

    if not signals:
        return None

    for signal in signals:
        if isinstance(signal, dict) and signal.get("topic"):
            slug = signal["topic"].lower().replace(" ", "_").replace("-", "_")
            topics.add(slug)

    return topics if topics else None


def _collect_reviewable_canon(
    canon_dir: Path,
    signal_topics: set[str] | None = None,
) -> dict[str, str]:
    """Return ``{filename: content}`` for canon files eligible for review.

    When ``signal_topics`` is provided (non-empty), ONLY files whose
    topic slug matches a signal topic are reviewed.  This focuses
    reflection on areas where the writing process discovered something
    worth reviewing, rather than round-robin reviewing everything.

    When ``signal_topics`` is None or empty, ALL canon files are
    eligible (used when reflect is called without specific signals,
    e.g., via user override or after worldbuild generates new docs).
    """
    result: dict[str, str] = {}

    # Enumeration is routed through ``iter_canon_files`` so each entry is
    # resolved + contained before stat/read; a symlinked ``.md`` escaping
    # canon_dir is skipped. The keys returned here become the ``filename``
    # values later passed back into ``canon_dir / filename`` for rewrite, so
    # only contained basenames must ever land in the map.
    for f in iter_canon_files(canon_dir, suffix=".md"):
        # If signal topics are specified, only review matching files
        if signal_topics:
            slug = f.stem.lower().replace("-", "_").replace(" ", "_")
            if not any(
                t in slug or slug in t for t in signal_topics
            ):
                continue

        try:
            content = f.read_text(encoding="utf-8")
            result[f.name] = content
        except OSError:
            logger.debug("Cannot read %s", f, exc_info=True)

    return result


def _recently_reviewed(filepath: Path, now: float) -> bool:
    """Check if a canon file was reviewed recently.

    .. deprecated::
        Timer-based cooldowns have been removed in favor of
        signal-driven reflection.  This function is kept for backward
        compatibility but always returns False.
    """
    return False


def _stamp_reviewed(filepath: Path, model: str = "") -> None:
    """Write a sidecar marker recording that this file was just reviewed."""
    marker = filepath.parent / f".{filepath.name}.reviewed"
    try:
        data = json.dumps({
            "reviewed_at": time.time(),
            "model": model,
        })
        marker.write_text(data, encoding="utf-8")
    except OSError:
        logger.debug("Failed to write review marker for %s", filepath)


def _get_file_model(filepath: Path) -> str:
    """Read which model last wrote or reviewed a canon file."""
    marker = filepath.parent / f".{filepath.name}.reviewed"
    if not marker.exists():
        return ""
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data.get("model", "")
    except (OSError, json.JSONDecodeError):
        return ""


# Model quality tiers -- higher number = stronger model.
# Weaker models need stronger evidence (higher severity) to rewrite.
# User edits (via API/MCP) are highest tier but NOT untouchable --
# if Opus finds a genuine contradiction between user edits and multiple
# other canon sources, it can fix it.
_MODEL_TIERS: dict[str, int] = {
    "ollama-local": 1,
    "codex": 3,
    "claude-code": 4,
    "user": 5,  # Human-directed edits (via API/MCP)
}

# Minimum severity (0-10) needed to rewrite a file, indexed by tier gap.
# tier_gap = file_tier - current_tier.  Negative means current is stronger.
# 0 or negative: no extra evidence needed (equal or stronger model).
# Positive: need higher severity to justify overwriting.
_SEVERITY_THRESHOLDS: dict[int, int] = {
    0: 3,   # Same tier: mild issues justify rewrite
    1: 5,   # One tier weaker: moderate issues needed
    2: 7,   # Two tiers weaker: serious issues only
    3: 8,   # Three tiers: very serious
    4: 9,   # Four tiers (local rewriting user): near-certain contradiction
}


def _model_tier(model_name: str) -> int:
    """Return the quality tier for a model name (0 = unknown)."""
    for key, tier in _MODEL_TIERS.items():
        if key in model_name.lower():
            return tier
    return 0


def _rewrite_justified(
    current_tier: int, file_tier: int, severity: float,
) -> bool:
    """Check if the evidence (severity) justifies overwriting this file.

    Higher-tier files need stronger evidence to overwrite.  Nothing is
    untouchable -- even user edits can be fixed if there's a genuine
    contradiction with multiple other canon sources.
    """
    if file_tier <= 0 or current_tier <= 0:
        return severity >= 3  # Unknown tiers: basic threshold
    tier_gap = file_tier - current_tier
    if tier_gap <= 0:
        return severity >= 3  # Equal or stronger: mild issues suffice
    threshold = _SEVERITY_THRESHOLDS.get(tier_gap, 10)
    return severity >= threshold


def _evaluate_canon(
    canon_files: dict[str, str], premise: str
) -> list[dict[str, Any]]:
    """Call the LLM to evaluate canon files against the premise.

    Returns a list of issue dicts sorted by severity (worst first).
    Each dict has ``filename``, ``reason``, and ``severity`` (0-10).
    Files with no issues are omitted.
    """
    from domains.fantasy_daemon.phases._provider_stub import call_provider

    # Build a summary of each file (truncated to keep prompt manageable)
    file_summaries = []
    for filename, content in canon_files.items():
        truncated = content[:2000]
        file_summaries.append(f"### {filename}\n{truncated}")

    files_block = "\n\n".join(file_summaries)

    system = (
        "You are a worldbuilding quality reviewer for a fantasy novel. "
        "You compare canon documents against the original story premise "
        "to detect drift, shallowness, or internal contradictions. "
        "Respond ONLY with a JSON array. Each element must have: "
        '"filename" (string), "reason" (string, 1-2 sentences), '
        '"severity" (integer 1-10, where 10 is worst). '
        "Only include files that have real issues. If all files are fine, "
        "return an empty array: []"
    )

    prompt = (
        f"# Story Premise\n\n{premise}\n\n"
        f"# Canon Files to Review\n\n{files_block}\n\n"
        "# Task\n\n"
        "Evaluate each canon file above. Does it faithfully represent "
        "the premise? Is it internally consistent? Is it shallow or "
        "generic rather than specific to this world? "
        "Return a JSON array of issues (or [] if none)."
    )

    fallback = "[]"
    raw = call_provider(prompt, system, role="writer", fallback_response=fallback)

    return _parse_issues(raw)


def _parse_issues(raw: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON response into a sorted list of issues."""
    issues = parse_llm_json(raw, expect_type=list, fallback=None)
    if issues is None:
        logger.warning("Could not parse canon review response as JSON")
        return []

    # Validate and normalize
    valid: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        if "filename" not in item:
            continue
        item.setdefault("reason", "unspecified issue")
        item.setdefault("severity", 5)
        try:
            item["severity"] = int(item["severity"])
        except (ValueError, TypeError):
            item["severity"] = 5
        valid.append(item)

    # Sort by severity descending (worst first)
    valid.sort(key=lambda x: x["severity"], reverse=True)
    return valid


def _rewrite_canon_file(
    filename: str,
    premise: str,
    issue: dict[str, Any],
    all_canon: dict[str, str],
) -> str:
    """Call the LLM to rewrite a drifted canon file.

    Returns the new content, or empty string on failure.
    """
    from domains.fantasy_daemon.phases._provider_stub import call_provider

    original = all_canon.get(filename, "")
    reason = issue.get("reason", "quality issues detected")

    # Include other canon files as context (truncated)
    context_parts = []
    for other_name, other_content in all_canon.items():
        if other_name != filename:
            context_parts.append(f"### {other_name}\n{other_content[:1000]}")
    context_block = "\n\n".join(context_parts) if context_parts else "None"

    system = (
        "You are a worldbuilding author for a fantasy novel. "
        "Rewrite the given canon document to fix the identified issues "
        "while preserving valid details. Use markdown formatting with "
        "headers and lists. Be specific to this world, not generic. "
        "Write 500-1500 words. Return ONLY the document content."
    )

    prompt = (
        f"# Story Premise\n\n{premise}\n\n"
        f"# Issues Found\n\n{reason}\n\n"
        f"# Original Document ({filename})\n\n{original}\n\n"
        f"# Other Canon (for consistency)\n\n{context_block}\n\n"
        f"# Task\n\n"
        f"Rewrite {filename} to fix the issues above. Keep valid content, "
        f"fix what has drifted, add depth where it was shallow. "
        f"Return the full revised document."
    )

    fallback = original  # If LLM fails, keep the original
    return call_provider(prompt, system, role="writer", fallback_response=fallback)
