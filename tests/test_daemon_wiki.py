from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tinyassets import daemon_registry, daemon_wiki


def _soul_daemon(tmp_path: Path) -> dict:
    return daemon_registry.create_daemon(
        tmp_path,
        display_name="Recursive Scout",
        created_by="host",
        soul_text="Learn carefully from each run while preserving curiosity.",
        domain_claims=["research"],
    )


def test_soul_daemon_creation_scaffolds_host_local_wiki(tmp_path) -> None:
    daemon = _soul_daemon(tmp_path)

    root = daemon_wiki.daemon_wiki_root(tmp_path, daemon["daemon_id"])

    assert root.exists()
    assert (root / "WIKI.md").exists()
    assert (root / "index.md").exists()
    assert (root / "raw" / "signals").is_dir()
    assert (root / "pages" / "self-model" / "current-self.md").exists()
    assert (root / "pages" / "signals" / "learning-signals.md").exists()
    blocked_patterns = root / "pages" / "brain" / "blocked-patterns.md"
    assert blocked_patterns.exists()
    blocked_text = blocked_patterns.read_text(encoding="utf-8")
    assert "Pre-Claim Read" in blocked_text
    assert "verdict-feedback" in blocked_text
    assert "BUG-049" in blocked_text
    assert daemon["metadata"]["daemon_wiki"]["host_local"] is True
    assert "preserving curiosity" in (root / "raw" / "initial-soul.md").read_text(
        encoding="utf-8"
    )


def test_soulless_daemon_does_not_scaffold_wiki(tmp_path) -> None:
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Default Worker",
        created_by="host",
        soul_mode="soulless",
    )

    assert not daemon["has_soul"]
    assert not daemon_wiki.daemon_wiki_root(tmp_path, daemon["daemon_id"]).exists()


def test_record_daemon_signal_writes_raw_signal_digest_and_log(tmp_path) -> None:
    daemon = _soul_daemon(tmp_path)
    recorded_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    signal = daemon_wiki.record_daemon_signal(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        source_kind="node",
        source_id="node-123",
        outcome="failed",
        summary="Skipped verification too early.",
        details="The node output looked plausible but missed a required proof.",
        metadata={"gate": "evidence-check"},
        recorded_at=recorded_at,
    )

    raw_path = Path(signal["signal_path"])
    root = daemon_wiki.daemon_wiki_root(tmp_path, daemon["daemon_id"])
    digest = root / "pages" / "signals" / "learning-signals.md"
    log = root / "log.md"

    assert raw_path.exists()
    assert "Skipped verification too early." in raw_path.read_text(encoding="utf-8")
    assert "evidence-check" in raw_path.read_text(encoding="utf-8")
    assert "node | failed | node-123" in digest.read_text(encoding="utf-8")
    assert "signal | node:node-123 | failed" in log.read_text(encoding="utf-8")


def test_record_daemon_signal_rejects_soulless_daemon(tmp_path) -> None:
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Default Worker",
        created_by="host",
        soul_mode="soulless",
    )

    with pytest.raises(ValueError, match="Soulless|soulless"):
        daemon_wiki.record_daemon_signal(
            tmp_path,
            daemon_id=daemon["daemon_id"],
            source_kind="gate",
            source_id="gate-1",
            outcome="passed",
            summary="Finished without a soul.",
        )


def test_read_daemon_wiki_context_includes_schema_index_and_signals(
    tmp_path, canonical_daemon,
) -> None:
    daemon = _soul_daemon(tmp_path)
    canonical_daemon(tmp_path)
    root = daemon_wiki.daemon_wiki_root(tmp_path, daemon["daemon_id"])
    (root / "pages" / "brain" / "blocked-patterns.md").write_text(
        "---\n"
        "title: Blocked Patterns\n"
        "type: blocked_patterns\n"
        "---\n\n"
        "# Blocked Patterns\n\n"
        "## Active Patterns\n\n"
        "- Rejected operator-shaped PRs must feed back into the next claim.\n",
        encoding="utf-8",
        newline="\n",
    )
    daemon_wiki.record_daemon_signal(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        source_kind="gate",
        source_id="gate-77",
        outcome="passed",
        summary="Handled a verification gate with careful citations.",
    )

    context = daemon_wiki.read_daemon_wiki_context(
        tmp_path,
        daemon_id=daemon["daemon_id"],
    )

    assert context["exists"] is True
    assert "Daemon Wiki Schema" in context["context"]
    assert "Current Self" in context["context"]
    assert "<!-- pages/brain/blocked-patterns.md -->" in context["context"]
    assert "Rejected operator-shaped PRs" in context["context"]
    assert "Handled a verification gate" in context["context"]


def test_scaffold_rejects_daemon_without_soul(tmp_path) -> None:
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Default Worker",
        created_by="host",
        soul_mode="soulless",
    )

    with pytest.raises(ValueError, match="soul-bearing"):
        daemon_wiki.scaffold_daemon_wiki(tmp_path, daemon=daemon)
