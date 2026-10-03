"""Tests for the ``add_canon`` action and the source reads over what it wrote.

``add_canon`` previously wrote directly to ``canon/<filename>`` and did NOT
emit the ``synthesize_source`` signal -- premise/canon/entity synthesis never
fired on MCP uploads. It now routes through
:func:`tinyassets.ingestion.core.ingest_file` so the signal fires.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tinyassets.api.universe as us
from tinyassets.enrichment_signals import load_enrichment_signals


def _call(action: str, **kwargs) -> dict:
    """Invoke an action through the dispatch+ledger path the tool uses."""
    base_kwargs = {
        "universe_id": "",
        "text": "",
        "path": "",
        "category": "direction",
        "target": "",
        "query_type": "facts",
        "filter_text": "",
        "request_type": "scene_direction",
        "branch_id": "",
        "filename": "",
        "provenance_tag": "",
        "limit": 20,
    }
    base_kwargs.update(kwargs)

    dispatch = {
        "add_canon": us._action_add_canon,
    }
    handler = dispatch[action]
    return json.loads(us._dispatch_with_ledger(action, handler, base_kwargs))


@pytest.fixture
def universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    base = tmp_path / "output"
    uid = "test-uni"
    (base / uid).mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "test-user")
    from tests.conftest import own_universe

    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(base, uid)
    return uid


def _signals(uid: str) -> list[dict]:
    return load_enrichment_signals(us._base_path() / uid)


# ─── add_canon now emits synthesize_source ─────────────────────────────


class TestAddCanonSynthesisSignal:
    def test_add_canon_response_makes_version_semantics_explicit(
        self, universe: str,
    ) -> None:
        out = _call("add_canon", filename="ryn.md", text="# Ryn\n\nA scout.")

        assert out["source_operation"] == "created"
        assert out["version_semantics"] == {
            "mode": "filename_upsert",
            "identity": "canon/sources/ryn.md",
            "same_filename_behavior": (
                "A later ingest of the same canon-source filename replaces "
                "the stored source bytes and manifest entry when the content "
                "hash changes; identical bytes are treated as unchanged."
            ),
            "history_retained": False,
            "supersede_supported": False,
            "deprecate_supported": False,
        }

    def test_add_canon_emits_signal(self, universe: str) -> None:
        """The pre-fix path bypassed ingest_file and no signal ever
        fired. Post-fix: every user upload emits synthesize_source."""
        out = _call(
            "add_canon", filename="ryn.md", text="# Ryn\n\nA scout.",
            provenance_tag="rough notes",
        )
        assert out["status"] == "written"
        assert out["synthesis_signal_emitted"] is True

        signals = _signals(universe)
        assert len(signals) == 1
        assert signals[0]["type"] == "synthesize_source"
        assert signals[0]["source_file"] == "ryn.md"

    def test_add_canon_routes_to_sources_dir(self, universe: str) -> None:
        """User uploads land under ``canon/sources/`` per ingest_file
        routing, not ``canon/`` directly."""
        _call("add_canon", filename="notes.md", text="notes")
        udir = us._base_path() / universe
        assert (udir / "canon" / "sources" / "notes.md").exists()
        # The direct-to-canon path is no longer used for uploads.
        assert not (udir / "canon" / "notes.md").exists()

    def test_add_canon_ledger_entry_unchanged(self, universe: str) -> None:
        """Ledger contract from the 2a landing preserved."""
        _call(
            "add_canon", filename="ref.md", text="# Reference\n",
            provenance_tag="rough notes",
        )
        ledger_path = us._base_path() / universe / "ledger.json"
        entries = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["action"] == "add_canon"
        assert entries[0]["target"] == "canon/ref.md"
        assert entries[0]["payload"]["provenance"] == "rough notes"

    def test_add_canon_reports_replace_and_unchanged(
        self, universe: str,
    ) -> None:
        first = _call("add_canon", filename="notes.md", text="Old notes")
        second = _call("add_canon", filename="notes.md", text="New notes")
        third = _call("add_canon", filename="notes.md", text="New notes")

        assert first["source_operation"] == "created"
        assert second["source_operation"] == "replaced"
        assert third["source_operation"] == "unchanged"


class TestSourceInspection:
    def test_list_sources_exposes_manifest_and_attestation(
        self, universe: str, tmp_path: Path,
    ) -> None:
        src = tmp_path / "chapter-one.md"
        src.write_text("# Chapter One\n\nThe gate opens.", encoding="utf-8")
        _call(
            "add_canon", filename=src.name,
            text=src.read_text(encoding="utf-8"), provenance_tag="draft upload",
        )

        out = json.loads(us._universe_impl(action="list_sources"))

        assert out["universe_id"] == universe
        assert out["source_count"] == 1
        source = out["source_files"][0]
        assert source["filename"] == "chapter-one.md"
        assert source["source_path"] == "sources/chapter-one.md"
        assert source["provenance"] == "draft upload"
        written = src.read_text(encoding="utf-8").encode("utf-8")
        assert source["sha256"] == hashlib.sha256(written).hexdigest()
        assert source["manifest_sha256"] == source["sha256"]
        assert source["synthesis_complete"] is False
        assert source["synthesized_docs"] == []

    def test_read_source_returns_verbatim_content_and_checksum(
        self, universe: str, tmp_path: Path,
    ) -> None:
        src = tmp_path / "lore.md"
        source_bytes = b"# Lore\n\nThe old bridge remembers every footstep."
        content = source_bytes.decode("utf-8")
        src.write_bytes(source_bytes)
        _call(
            "add_canon", filename=src.name,
            text=src.read_text(encoding="utf-8"), provenance_tag="source pack",
        )

        out = json.loads(us._universe_impl(action="read_source", filename="lore.md"))

        assert out["universe_id"] == universe
        assert out["filename"] == "lore.md"
        assert out["content"] == content
        assert out["truncated"] is False
        assert out["content_preview_chars"] == 4000
        assert "continue with that write action" in out["next_action_hint"]
        assert out["provenance"] == "source pack"
        assert out["sha256"] == hashlib.sha256(source_bytes).hexdigest()

    def test_read_source_default_preview_preserves_chatgpt_continuation_budget(
        self, universe: str, tmp_path: Path,
    ) -> None:
        src = tmp_path / "long-source.md"
        content = "A" * 6000
        src.write_text(content, encoding="utf-8")
        _call(
            "add_canon", filename=src.name,
            text=src.read_text(encoding="utf-8"), provenance_tag="long source",
        )

        out = json.loads(us._universe_impl(
            action="read_source",
            filename="long-source.md",
        ))

        assert out["content"] == content[:4000]
        assert out["truncated"] is True
        assert out["content_preview_chars"] == 4000
        assert out["total_chars"] == 6000
        assert "do not stop after reading sources" in out["next_action_hint"]

    def test_read_source_explicit_limit_allows_larger_preview(
        self, universe: str, tmp_path: Path,
    ) -> None:
        src = tmp_path / "longer-source.md"
        content = "B" * 6000
        src.write_text(content, encoding="utf-8")
        _call(
            "add_canon", filename=src.name,
            text=src.read_text(encoding="utf-8"), provenance_tag="longer source",
        )

        out = json.loads(us._universe_impl(
            action="read_source",
            filename="longer-source.md",
            limit=10000,
        ))

        assert out["content"] == content
        assert out["truncated"] is False
        assert out["content_preview_chars"] == 10000

    def test_read_source_rejects_path_segments(self, universe: str) -> None:
        out = json.loads(us._universe_impl(
            action="read_source",
            filename="../PROGRAM.md",
        ))

        assert "error" in out
        assert "not exposed by the advertised handles" in out["error"]
        assert "list_sources" not in out["error"]
