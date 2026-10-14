"""Memory quality replay checks for daemon mini-brain entries."""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.daemon_brain import (
    capture_daemon_memory,
    evaluate_daemon_memory_quality,
    memory_observability_status,
)
from tinyassets.daemon_registry import create_daemon


def _create_daemon(base: Path) -> dict:
    return create_daemon(
        base,
        display_name="Eval Daemon",
        created_by="pytest",
        soul_mode="soul",
        soul_text="Eval Daemon uses memory only when it improves future work.",
    )


def test_memory_quality_eval_reports_positive_delta(canonical_daemon, tmp_path: Path) -> None:
    daemon = _create_daemon(tmp_path)
    entry = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="failure_mode",
        content="When child runs fail, verify provider_call reaches child prompt nodes.",
        source_type="pytest",
        source_id="memory-quality-eval",
        reliability="test_observed",
        temporal_bounds={"valid_from": "2026-05-02"},
        language_type="policy",
        confidence=0.9,
        importance=0.9,
    )

    def replay(case: dict) -> str:
        if "provider_call reaches child prompt nodes" in case["context"]:
            return "Fix checks provider_call reaches child prompt nodes."
        return "Fix checks provider routing generally."

    result = evaluate_daemon_memory_quality(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        query="child run provider_call",
        replay_fn=replay,
        expected_signals=["provider_call reaches child prompt nodes"],
    )

    assert result["entry_ids"] == [entry["entry_id"]]
    assert result["without_memory"]["score"] == 0.0
    assert result["with_memory"]["score"] == 1.0
    assert result["delta"] == 1.0
    assert result["outcome"] == "improved"
    status = memory_observability_status(canonical_daemon(tmp_path), daemon_id=daemon["daemon_id"])
    assert status["event_types"]["daemon.memory.eval"] == 1


def test_memory_quality_eval_allows_custom_score_fn(canonical_daemon, tmp_path: Path) -> None:
    daemon = _create_daemon(tmp_path)
    capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="policy",
        content="Always cite source episodes when promoting memory.",
        source_type="pytest",
        source_id="custom-score",
        reliability="test_observed",
        temporal_bounds={"valid_from": "2026-05-02"},
        language_type="policy",
        confidence=0.8,
        importance=0.8,
    )

    def replay(case: dict) -> dict:
        return {
            "memory_enabled": case["memory_enabled"],
            "score": 0.75 if case["memory_enabled"] else 0.25,
        }

    result = evaluate_daemon_memory_quality(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        query="promoting memory source episodes",
        replay_fn=replay,
        score_fn=lambda output: output["score"],
    )

    assert result["without_memory"]["score"] == 0.25
    assert result["with_memory"]["score"] == 0.75
    assert result["delta"] == 0.5


def test_memory_quality_eval_requires_scorer(canonical_daemon, tmp_path: Path) -> None:
    daemon = _create_daemon(tmp_path)

    with pytest.raises(ValueError, match="expected_signals or score_fn"):
        evaluate_daemon_memory_quality(
            canonical_daemon(tmp_path),
            daemon_id=daemon["daemon_id"],
            query="anything",
            replay_fn=lambda _case: "no scorer",
        )
