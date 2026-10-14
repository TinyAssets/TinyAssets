"""Focused pytest coverage for the daemon mini-brain runtime slice."""

from __future__ import annotations

from pathlib import Path

from tinyassets.daemon_brain import (
    DaemonBrain,
    build_daemon_brain_packet,
    capture_daemon_memory,
    daemon_memory_cost_ledger_status,
    daemon_memory_registry,
    list_daemon_memory,
    memory_observability_status,
    open_brain_status_surface,
    promote_daemon_memory_to_wiki,
    review_daemon_memory,
    search_daemon_memory,
)
from tinyassets.daemon_memory import build_daemon_memory_packet
from tinyassets.daemon_registry import create_daemon
from tinyassets.daemon_wiki import daemon_wiki_root, scaffold_daemon_wiki


def _create_daemon(base: Path, name: str) -> dict:
    return create_daemon(
        base,
        display_name=name,
        created_by="pytest",
        soul_mode="soul",
        soul_text=f"{name} is a careful test daemon.",
        # No daemon_wiki cap metadata: the per-wiki byte cap and its compactor are
        # gone (founder 2026-09-30 -- storage is one universe-level number).
    )


def test_daemon_brain_smoke_roundtrip(canonical_daemon, tmp_path: Path) -> None:
    ada = _create_daemon(tmp_path, "Test Ada")
    mira = _create_daemon(tmp_path, "Test Mira")

    scaffold_daemon_wiki(tmp_path, daemon=ada, soul_text="Test Ada soul.")
    review_page = (
        daemon_wiki_root(tmp_path, ada["daemon_id"])
        / "pages"
        / "brain"
        / "review.md"
    )
    assert review_page.exists()
    original_review = review_page.read_text(encoding="utf-8")

    first = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        memory_kind="failure_mode",
        content=(
            "When checking daemon routing, verify the executor identity is "
            "not copied."
        ),
        source_type="manual",
        source_id="pytest-source",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-05-02"},
        language_type="policy",
        confidence=0.91,
        importance=0.86,
    )
    duplicate = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        memory_kind="failure_mode",
        content=(
            "When checking daemon routing, verify the executor identity is "
            "not copied."
        ),
        source_type="manual",
        source_id="pytest-source",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-05-02"},
        language_type="policy",
    )
    assert duplicate["deduped"] is True
    assert duplicate["entry_id"] == first["entry_id"]

    capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=mira["daemon_id"],
        memory_kind="policy",
        content="Mira-only packet planning memory must not leak into Ada search.",
        source_type="manual",
        source_id="pytest-source",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-05-02"},
        language_type="policy",
    )

    listed = list_daemon_memory(canonical_daemon(tmp_path), daemon_id=ada["daemon_id"])
    assert listed["count"] == 1

    search = search_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        query="executor identity copied",
        limit=5,
    )
    assert [entry["entry_id"] for entry in search["entries"]] == [
        first["entry_id"],
    ]
    assert all("Mira-only" not in entry["content"] for entry in search["entries"])

    brain_packet = build_daemon_brain_packet(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        query="daemon routing executor identity",
        max_chars=700,
    )
    assert first["entry_id"] in brain_packet["context"]
    assert len(brain_packet["context"]) <= 700

    full_packet = build_daemon_memory_packet(
        tmp_path,
        daemon_id=ada["daemon_id"],
        max_chars=2600,
        brain_query="daemon routing executor identity",
        brain_max_chars=700,
    )
    assert first["entry_id"] in full_packet["context"]
    assert len(full_packet["context"]) <= 2600
    assert full_packet["brain"]["selected_count"] == 1

    promotion = promote_daemon_memory_to_wiki(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        entry_ids=[first["entry_id"]],
        summary="Routing memories must preserve executor identity boundaries.",
    )
    assert promotion["promoted_count"] == 1
    source = canonical_daemon(tmp_path)
    promoted_review = source.bundle.provider.read(
        source.bundle.handle, promotion["target_path"], max_bytes=100000
    ).data.decode()
    assert review_page.read_text(encoding="utf-8") == original_review
    assert first["entry_id"] in promoted_review

    status = memory_observability_status(canonical_daemon(tmp_path), daemon_id=ada["daemon_id"])
    assert status["entry_count"] == 1
    assert status["event_count"] >= 5
    assert status["cost_ledger"]["read_only"] is True
    assert status["cost_ledger"]["estimated_total_tokens"] > 0


def test_daemon_memory_registry_describes_kinds_and_lifecycle() -> None:
    registry = daemon_memory_registry()

    assert "failure_mode" in {item["kind"] for item in registry["memory_kinds"]}
    assert registry["default_memory_kind"] == "semantic"
    assert registry["default_promotion_state"] == "candidate"
    assert registry["promotion_transitions"]["candidate"] == [
        "accepted",
        "promoted",
        "rejected",
        "superseded",
    ]
    assert registry["promotion_transitions"]["promoted"] == ["superseded"]
    assert registry["terminal_promotion_states"] == ["rejected", "superseded"]


def test_daemon_memory_lifecycle_blocks_invalid_transitions(
    canonical_daemon, tmp_path: Path
) -> None:
    daemon = _create_daemon(tmp_path, "Lifecycle Daemon")

    entry = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="policy",
        content="Rejected memories must not be promoted later.",
        source_type="manual",
        source_id="pytest-lifecycle",
        reliability="host_observed",
        language_type="policy",
    )
    rejected = review_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        entry_id=entry["entry_id"],
        decision="reject",
        note="Not stable enough.",
    )
    assert rejected["entry"]["promotion_state"] == "rejected"

    try:
        promote_daemon_memory_to_wiki(
            canonical_daemon(tmp_path),
            daemon_id=daemon["daemon_id"],
            entry_ids=[entry["entry_id"]],
            summary="This should not promote.",
        )
    except ValueError as exc:
        assert "cannot transition promotion_state from rejected to promoted" in str(exc)
    else:
        raise AssertionError("rejected memory was promoted")

def test_session_trace_summary_is_recognized_kind_with_full_lifecycle(canonical_daemon,
    tmp_path: Path,
) -> None:
    """Slice 2 of PrivateTraceCommons (per 2026-05-02-private-trace-commons.md (archived in git)).

    Verifies session_trace_summary is a recognized memory_kind that moves
    through the existing promotion state machine like any other kind:
    candidate (capture) → accepted (review) → promoted (publish to wiki).

    Slice 1 spec proposed a `host_private`-blocks-promotion enforcement;
    Slice 2 implementation found that contradicts the existing default
    visibility semantics (host_private is the DEFAULT for new entries, and
    every normal promotion flow today walks through host_private →
    promoted). Per Scoping Rules 2 + 3, visibility enforcement is
    community-composed via gates anyway, not a platform primitive. The
    enforcement is intentionally NOT shipped; visibility tagging
    + community-composed gate enforcement is the path. Follow-up filing
    will retract that part of the Slice 1 spec.
    """
    daemon = _create_daemon(tmp_path, "Trace Daemon")

    entry = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="session_trace_summary",
        content=(
            "Run of branch X completed. 3 artifacts captured. "
            "No sensitive content exposed."
        ),
        source_type="run",
        source_id="run-abc123",
        reliability="host_observed",
        language_type="summary",
        visibility="borrowable_role_context",
    )
    assert entry["memory_kind"] == "session_trace_summary"
    assert entry["promotion_state"] == "candidate"

    review_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        entry_id=entry["entry_id"],
        decision="accept",
        note="Summary reviewed; safe to surface.",
    )

    result = promote_daemon_memory_to_wiki(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        entry_ids=[entry["entry_id"]],
        summary="Session trace summary promotion sanity check.",
    )
    assert result["promoted_count"] == 1


def test_experience_lesson_is_recognized_kind_with_full_lifecycle(canonical_daemon,
    tmp_path: Path,
) -> None:
    """Slice 2 of ExperiencePool.

    Verifies experience_lesson is a recognized memory_kind that moves
    through the existing promotion state machine: candidate (capture) →
    accepted (review) → promoted (publish to wiki).

    Per the audit verdict, experience_lesson reuses the existing memory_kind
    substrate verbatim — no new typed surface, no schema migration, no
    parallel evidence shape (evidence_refs point at existing EvalResult IDs).
    """
    daemon = _create_daemon(tmp_path, "Lesson Daemon")

    entry = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="experience_lesson",
        content=(
            "When the simulator placeholder check fails, adding a "
            "validation gate before the simulator step caught 3 prior "
            "false-passes in retrospective evaluation."
        ),
        source_type="run",
        source_id="run-markovic-2026-05-15-abc",
        reliability="host_observed",
        language_type="lesson",
        visibility="borrowable_role_context",
        metadata={
            "source_run_id": "run:markovic-2026-05-15-abc",
            "goal_id": "goal:markovic-publication",
            "branch_id": "branch:markovic_fingerprint_rd_v3",
            "lesson_kind": "intervention",
            "intervention": {
                "description": "Add placeholder-validation gate before simulator step",
                "change_kind": "add",
                "applied_to": "branch:markovic_fingerprint_rd_v3",
            },
            "observed_delta": (
                "3 prior runs would have been caught at the new gate; "
                "new gate produces FAILED early with typed failure_mode."
            ),
            "evidence_refs": [
                "evalresult://run-markovic-2026-05-15-abc",
            ],
            "confidence": 0.8,
        },
    )
    assert entry["memory_kind"] == "experience_lesson"
    assert entry["promotion_state"] == "candidate"

    review_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        entry_id=entry["entry_id"],
        decision="accept",
        note="Lesson reviewed; intervention pattern is generalizable.",
    )

    result = promote_daemon_memory_to_wiki(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        entry_ids=[entry["entry_id"]],
        summary="Experience lesson promotion sanity check.",
    )
    assert result["promoted_count"] == 1


def test_daemon_memory_cost_ledger_is_read_only_status(canonical_daemon, tmp_path: Path) -> None:
    ada = _create_daemon(tmp_path, "Cost Ada")
    capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        memory_kind="policy",
        content="Track prompt budget costs before promoting open-brain memories.",
        source_type="manual",
        source_id="pytest-cost-ledger",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-05-17"},
        language_type="policy",
        confidence=0.8,
        importance=0.7,
    )
    search_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        query="prompt budget",
        limit=2,
    )

    ledger = daemon_memory_cost_ledger_status(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        recent_limit=2,
    )

    assert ledger["read_only"] is True
    assert ledger["ledger_available"] is True
    assert ledger["entry_count"] == 1
    assert ledger["event_count"] >= 2
    assert ledger["estimated_total_tokens"] > 0
    assert ledger["entries_by_kind"] == {"policy": 1}
    assert "daemon.memory.write_candidate" in ledger["events_by_type"]
    assert ledger["recent_events"][0]["estimated_tokens"] >= 0


def test_open_brain_status_surface_summarizes_soul_daemons(
    canonical_daemon, tmp_path: Path
) -> None:
    ada = _create_daemon(tmp_path, "Surface Ada")
    capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=ada["daemon_id"],
        memory_kind="failure_mode",
        content="Surface mini-brain cost without triggering autonomous cleanup.",
        source_type="manual",
        source_id="pytest-open-brain-status",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-05-17"},
        language_type="policy",
    )

    status = open_brain_status_surface(canonical_daemon(tmp_path))

    assert status["read_only"] is True
    assert status["daemon_count"] == 1
    assert status["daemons"][0]["daemon_id"] == ada["daemon_id"]
    assert status["daemons"][0]["cost_ledger"]["estimated_total_tokens"] > 0


def test_tiny_daemon_external_write_packet_uses_daemonbrain_facade(canonical_daemon,
    tmp_path: Path,
) -> None:
    """PR-048 Phase B first-migration acceptance: Tiny uses DaemonBrain."""
    tiny = _create_daemon(tmp_path, "Tiny")
    brain = DaemonBrain(canonical_daemon(tmp_path), daemon_id=tiny["daemon_id"])
    memory = brain.capture(
        memory_kind="policy",
        content=(
            "Tiny daemon external-write packets for twitter_post must check "
            "the Brain for consent, idempotency, and voice policy before any "
            "sink adapter writes outside TinyAssets."
        ),
        source_type="patch_request",
        source_id="WIKI-PATCH-908",
        reliability="host_observed",
        language_type="policy",
        importance=0.92,
        visibility="borrowable_role_context",
        promotion_state="accepted",
    )

    packet = build_daemon_memory_packet(
        tmp_path,
        daemon_id=tiny["daemon_id"],
        max_chars=2600,
        brain_query="Tiny daemon twitter_post external-write consent idempotency",
        brain_max_chars=900,
    )

    assert packet["brain"]["selected_count"] == 1
    assert packet["brain"]["entries"][0]["entry_id"] == memory["entry_id"]
    assert "twitter_post must check the Brain" in packet["context"]
