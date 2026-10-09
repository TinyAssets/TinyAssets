"""Tests for the ingestion framework -- type detection, routing, manifest, signals."""

from __future__ import annotations

import inspect
import json
from unittest.mock import patch

import pytest

from tinyassets.enrichment_signals import ENRICHMENT_SIGNALS_FILENAME
from tinyassets.ingestion.core import (
    SIZE_THRESHOLD,
    FileType,
    SourceManifest,
    detect_file_type,
    ingest_file,
)
from tinyassets.ingestion.extractors import (
    _parse_gap_response,
    _verify_and_fill_gaps,
    extract_text,
    synthesize_source,
)

# =====================================================================
# File type detection
# =====================================================================


class TestDetectFileType:
    def test_markdown_by_extension(self):
        result = detect_file_type("world.md")
        assert result.file_type == FileType.TEXT
        assert result.mime_type == "text/markdown"

    def test_txt_by_extension(self):
        result = detect_file_type("notes.txt")
        assert result.file_type == FileType.TEXT
        assert result.mime_type == "text/plain"

    def test_png_by_extension(self):
        result = detect_file_type("map.png")
        assert result.file_type == FileType.IMAGE
        assert result.mime_type == "image/png"

    def test_png_by_magic_bytes(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = detect_file_type("unknown_file", data=png_header)
        assert result.file_type == FileType.IMAGE
        assert result.mime_type == "image/png"
        assert result.method == "magic"

    def test_jpeg_by_magic_bytes(self):
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        result = detect_file_type("photo.jpg", data=jpeg_header)
        assert result.file_type == FileType.IMAGE
        assert result.mime_type == "image/jpeg"
        assert result.method == "both"

    def test_pdf_by_extension(self):
        result = detect_file_type("document.pdf")
        assert result.file_type == FileType.DOCUMENT
        assert result.mime_type == "application/pdf"

    def test_pdf_by_magic_bytes(self):
        pdf_header = b"%PDF-1.7" + b"\x00" * 100
        result = detect_file_type("file.pdf", data=pdf_header)
        assert result.file_type == FileType.DOCUMENT
        assert result.method == "both"

    def test_mp4_by_extension(self):
        result = detect_file_type("video.mp4")
        assert result.file_type == FileType.VIDEO

    def test_mp3_by_extension(self):
        result = detect_file_type("sound.mp3")
        assert result.file_type == FileType.AUDIO

    def test_unknown_extension(self):
        result = detect_file_type("data.xyz")
        assert result.file_type == FileType.UNKNOWN

    def test_magic_overrides_wrong_extension(self):
        """A .txt file that's actually a PNG should be detected as image."""
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = detect_file_type("misleading.txt", data=png_header)
        assert result.file_type == FileType.IMAGE
        assert result.method == "magic"

    def test_json_detected_as_text(self):
        result = detect_file_type("data.json")
        assert result.file_type == FileType.TEXT
        assert result.mime_type == "application/json"

    def test_docx_detected_as_document(self):
        result = detect_file_type("story.docx")
        assert result.file_type == FileType.DOCUMENT

    def test_case_insensitive_extension(self):
        result = detect_file_type("MAP.PNG")
        assert result.file_type == FileType.IMAGE


# =====================================================================
# Source manifest
# =====================================================================


class TestSourceManifest:
    def test_save_and_load(self, tmp_path):
        from tinyassets.ingestion.core import ManifestEntry

        manifest = SourceManifest()
        manifest.add(ManifestEntry(
            filename="world.md",
            source_path="world.md",
            file_type="text",
            mime_type="text/markdown",
            byte_count=1024,
            sha256="abc123",
            routed_to="canon",
            ingested_at="2026-04-02T00:00:00Z",
        ))
        manifest.save(tmp_path)

        loaded = SourceManifest.load(tmp_path)
        assert "world.md" in loaded.entries
        assert loaded.entries["world.md"].sha256 == "abc123"

    def test_has_changed_new_file(self):
        manifest = SourceManifest()
        assert manifest.has_changed("new.md", "hash1") is True

    def test_has_changed_same_hash(self, tmp_path):
        from tinyassets.ingestion.core import ManifestEntry

        manifest = SourceManifest()
        manifest.add(ManifestEntry(
            filename="existing.md",
            source_path="existing.md",
            file_type="text",
            mime_type="text/markdown",
            byte_count=100,
            sha256="same_hash",
            routed_to="canon",
        ))
        assert manifest.has_changed("existing.md", "same_hash") is False

    def test_has_changed_different_hash(self):
        from tinyassets.ingestion.core import ManifestEntry

        manifest = SourceManifest()
        manifest.add(ManifestEntry(
            filename="existing.md",
            source_path="existing.md",
            file_type="text",
            mime_type="text/markdown",
            byte_count=100,
            sha256="old_hash",
            routed_to="canon",
        ))
        assert manifest.has_changed("existing.md", "new_hash") is True

    def test_load_missing_file(self, tmp_path):
        manifest = SourceManifest.load(tmp_path)
        assert len(manifest.entries) == 0

    def test_load_corrupt_file(self, tmp_path):
        (tmp_path / ".manifest.json").write_text("not json", encoding="utf-8")
        manifest = SourceManifest.load(tmp_path)
        assert len(manifest.entries) == 0


# =====================================================================
# Ingest file routing
# =====================================================================


class TestIngestFile:
    def test_daemon_doc_goes_to_canon(self, tmp_path):
        """Daemon-generated docs go to canon/ directly."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        data = b"# Small World\n\nA tiny world." * 10

        result = ingest_file(canon_dir, "world.md", data, user_upload=False)

        assert result.routed_to == "canon"
        assert result.signal_emitted is False
        assert (canon_dir / "world.md").exists()
        assert not (canon_dir / "sources" / "world.md").exists()

    def test_small_user_upload_goes_to_sources(self, tmp_path):
        """ALL user uploads go to sources/, regardless of size."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path
        data = b"# Short character sheet"

        result = ingest_file(
            canon_dir, "character.md", data,
            universe_path=universe_path, user_upload=True,
        )

        assert result.routed_to == "sources"
        assert result.signal_emitted is True
        assert (canon_dir / "sources" / "character.md").exists()

    def test_large_file_goes_to_sources(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path
        data = b"x" * (SIZE_THRESHOLD + 1)

        result = ingest_file(
            canon_dir, "big_lore.txt", data,
            universe_path=universe_path,
        )

        assert result.routed_to == "sources"
        assert result.signal_emitted is True
        assert (canon_dir / "sources" / "big_lore.txt").exists()
        assert not (canon_dir / "big_lore.txt").exists()

    def test_large_file_emits_signal(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path
        data = b"x" * (SIZE_THRESHOLD + 1)

        ingest_file(
            canon_dir, "epic.md", data,
            universe_path=universe_path,
        )

        signals_file = universe_path / ENRICHMENT_SIGNALS_FILENAME
        assert signals_file.exists()
        signals = json.loads(signals_file.read_text(encoding="utf-8"))
        assert len(signals) == 1
        assert signals[0]["type"] == "synthesize_source"
        assert signals[0]["source_file"] == "epic.md"

    def test_large_file_appends_legacy_signals_to_enrichment_signal_file(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path
        legacy_file = universe_path / "worldbuild_signals.json"
        legacy_file.write_text(
            json.dumps([{"type": "new_element", "topic": "legacy"}]),
            encoding="utf-8",
        )

        ingest_file(
            canon_dir, "source.md", b"x" * (SIZE_THRESHOLD + 1),
            universe_path=universe_path,
        )

        signals_file = universe_path / ENRICHMENT_SIGNALS_FILENAME
        assert signals_file.exists()
        signals = json.loads(signals_file.read_text(encoding="utf-8"))
        assert [signal["type"] for signal in signals] == [
            "new_element",
            "synthesize_source",
        ]
        assert legacy_file.exists()

    def test_manifest_updated_after_ingest(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        data = b"# Test\n\nContent here."

        ingest_file(canon_dir, "test.md", data, user_upload=False)

        manifest = SourceManifest.load(canon_dir)
        assert "test.md" in manifest.entries
        entry = manifest.entries["test.md"]
        assert entry.routed_to == "canon"
        assert entry.byte_count == len(data)

    def test_duplicate_file_skipped(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        data = b"# Same content"

        result1 = ingest_file(canon_dir, "same.md", data)
        result2 = ingest_file(canon_dir, "same.md", data)

        assert result1.sha256 == result2.sha256
        assert result2.signal_emitted is False

    def test_changed_file_re_ingested(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        ingest_file(canon_dir, "evolving.md", b"version 1", user_upload=False)
        result = ingest_file(canon_dir, "evolving.md", b"version 2", user_upload=False)

        assert result.sha256 != ""
        content = (canon_dir / "evolving.md").read_bytes()
        assert content == b"version 2"

    def test_no_signal_without_universe_path(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        data = b"x" * (SIZE_THRESHOLD + 1)

        result = ingest_file(canon_dir, "big.txt", data)

        assert result.routed_to == "sources"
        assert result.signal_emitted is False

    def test_image_file_routed_correctly(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path
        # Fake PNG header + padding to exceed threshold
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * (SIZE_THRESHOLD + 1)

        result = ingest_file(
            canon_dir, "map.png", data,
            universe_path=universe_path,
        )

        assert result.file_type == FileType.IMAGE
        assert result.routed_to == "sources"
        assert result.signal_emitted is True

    def test_daemon_doc_at_any_size_goes_to_canon(self, tmp_path):
        """Daemon docs go to canon/ regardless of size."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        data = b"x" * SIZE_THRESHOLD

        result = ingest_file(canon_dir, "exact.md", data, user_upload=False)

        assert result.routed_to == "canon"

    def test_signal_appended_to_existing_signals(self, tmp_path):
        """New synthesis signals should append, not overwrite existing ones."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        universe_path = tmp_path

        # Pre-existing signal
        signals_file = universe_path / ENRICHMENT_SIGNALS_FILENAME
        signals_file.write_text(
            json.dumps([{"type": "new_element", "topic": "dragons"}]),
            encoding="utf-8",
        )

        data = b"x" * (SIZE_THRESHOLD + 1)
        ingest_file(
            canon_dir, "lore.md", data,
            universe_path=universe_path,
        )

        signals = json.loads(signals_file.read_text(encoding="utf-8"))
        assert len(signals) == 2
        assert signals[0]["type"] == "new_element"
        assert signals[1]["type"] == "synthesize_source"


# =====================================================================
# Text extraction
# =====================================================================


class TestExtractText:
    def test_markdown_passthrough(self):
        data = b"# Chapter 1\n\nThe wind howled."
        text = extract_text("story.md", data)
        assert "Chapter 1" in text
        assert "wind howled" in text

    def test_plain_text_passthrough(self):
        data = b"Just some plain text notes about the world."
        text = extract_text("notes.txt", data)
        assert "plain text notes" in text

    def test_json_passthrough(self):
        data = b'{"name": "Kael", "role": "protagonist"}'
        text = extract_text("characters.json", data)
        assert "Kael" in text

    def test_utf8_encoding(self):
        data = "The caf\u00e9 was warm.".encode("utf-8")
        text = extract_text("scene.md", data)
        assert "caf\u00e9" in text

    def test_latin1_fallback(self):
        data = "The caf\u00e9 was warm.".encode("latin-1")
        text = extract_text("scene.txt", data)
        assert "caf" in text

    def test_unknown_binary_returns_empty(self):
        data = b"\x00\x01\x02\x03\x04\x05"
        text = extract_text("binary.dat", data)
        # Should return empty or attempt decode
        assert isinstance(text, str)

    def test_empty_file(self):
        text = extract_text("empty.md", b"")
        assert text == ""


# =====================================================================
# Synthesis
# =====================================================================


class TestSynthesizeSource:
    def test_synthesis_generates_docs(self, tmp_path):
        """synthesize_source should call provider and write canon docs."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        mock_response = json.dumps({
            "characters": "# Characters\n\n## Kael\nA wandering mage.",
            "locations": "# Locations\n\n## Thornwall\nA fortified city.",
        })

        with patch(
            "tinyassets.providers.call.call_provider",
            return_value=mock_response,
        ):
            result = synthesize_source(
                "A long source text about Kael the mage in Thornwall.",
                "lore.md",
                canon_dir,
                premise="Epic fantasy about magic.",
            )

        assert "characters.md" in result
        assert "locations.md" in result
        assert (canon_dir / "characters.md").exists()
        assert "Kael" in (canon_dir / "characters.md").read_text(encoding="utf-8")

    def test_synthesis_skips_user_authored(self, tmp_path):
        """User-authored canon files should not be overwritten."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        # Pre-existing user-authored file
        (canon_dir / "characters.md").write_text(
            "# My Characters\n\nHand-written.", encoding="utf-8",
        )
        marker = canon_dir / ".characters.md.reviewed"
        marker.write_text(
            json.dumps({"reviewed_at": 1.0, "model": "user"}),
            encoding="utf-8",
        )

        mock_response = json.dumps({
            "characters": "# Characters\n\nLLM-generated.",
        })

        with patch(
            "tinyassets.providers.call.call_provider",
            return_value=mock_response,
        ):
            result = synthesize_source(
                "Source text.", "source.md", canon_dir,
            )

        assert "characters.md" not in result
        content = (canon_dir / "characters.md").read_text(encoding="utf-8")
        assert "Hand-written" in content

    def test_synthesis_empty_source(self, tmp_path):
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        result = synthesize_source("", "empty.md", canon_dir)
        assert result == []

    def test_synthesis_fallback_on_bad_json(self, tmp_path):
        """When provider returns non-JSON, use whole response as single doc."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        with patch(
            "tinyassets.providers.call.call_provider",
            return_value="# Worldbuilding\n\nSome content here.",
        ):
            result = synthesize_source(
                "Source material.", "lore.md", canon_dir,
            )

        # Should create a single doc named after the source file stem
        assert len(result) == 1
        assert "lore.md" in result


# =====================================================================
# Synthesis verification pass
# =====================================================================


class TestParseGapResponse:
    def test_no_gaps(self):
        assert _parse_gap_response('{"gaps": []}') == []

    def test_gaps_found(self):
        raw = '{"gaps": ["Missing river name", "No mention of calendar system"]}'
        gaps = _parse_gap_response(raw)
        assert len(gaps) == 2
        assert "river" in gaps[0].lower()

    def test_code_fenced_json(self):
        raw = '```json\n{"gaps": ["Missing detail"]}\n```'
        gaps = _parse_gap_response(raw)
        assert len(gaps) == 1

    def test_invalid_json(self):
        assert _parse_gap_response("not json at all") == []

    def test_empty_string(self):
        assert _parse_gap_response("") == []

    def test_non_string_gaps_filtered(self):
        raw = '{"gaps": ["valid", 123, null, "also valid"]}'
        gaps = _parse_gap_response(raw)
        assert gaps == ["valid", "also valid"]


class TestVerifyAndFillGaps:
    def test_no_gaps_returns_unchanged(self):
        """When verification finds no gaps, docs are returned unchanged."""
        docs = {"characters": "# Characters\n\nKael the mage."}
        calls = []

        def mock_provider(prompt, system, role="writer"):
            calls.append(role)
            return '{"gaps": []}'

        result = _verify_and_fill_gaps(
            "Source text.", "source.md", docs, "", mock_provider,
        )
        assert result == docs
        assert len(calls) == 1  # Only verification call, no fill

    def test_gaps_trigger_resynthesis(self):
        """When gaps are found, a second pass fills them."""
        docs = {"characters": "# Characters\n\nKael."}
        call_count = [0]

        def mock_provider(prompt, system, role="writer"):
            call_count[0] += 1
            if call_count[0] == 1:
                return '{"gaps": ["Missing location Thornwall"]}'
            else:
                return json.dumps({"location_thornwall": "# Thornwall\n\nA fortress."})

        result = _verify_and_fill_gaps(
            "Kael in Thornwall.", "source.md", docs, "", mock_provider,
        )
        assert "characters" in result
        assert "location_thornwall" in result
        assert call_count[0] == 2

    def test_empty_docs_skipped(self):
        """Empty docs dict skips verification entirely."""
        result = _verify_and_fill_gaps(
            "Source.", "f.md", {}, "", lambda *a, **kw: "should not be called",
        )
        assert result == {}

    def test_verification_failure_returns_original(self):
        """Provider failure during verification returns original docs."""
        docs = {"chars": "# Chars\n\nContent."}

        def failing_provider(prompt, system, role="writer"):
            raise RuntimeError("Provider down")

        result = _verify_and_fill_gaps(
            "Source.", "f.md", docs, "", failing_provider,
        )
        assert result == docs

    def test_fill_failure_returns_original(self):
        """Provider failure during gap-fill returns original docs."""
        docs = {"chars": "# Chars\n\nContent."}
        call_count = [0]

        def mock_provider(prompt, system, role="writer"):
            call_count[0] += 1
            if call_count[0] == 1:
                return '{"gaps": ["Missing detail"]}'
            raise RuntimeError("Fill failed")

        result = _verify_and_fill_gaps(
            "Source.", "f.md", docs, "", mock_provider,
        )
        assert result == docs


class TestSynthesisWithVerification:
    def test_full_pipeline_with_verification(self, tmp_path):
        """End-to-end: synthesis + verification + gap-fill."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        call_count = [0]

        def mock_provider(prompt, system, role="writer", fallback_response=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # Initial synthesis
                return json.dumps({
                    "characters": "# Characters\n\n## Kael\nA mage.",
                })
            elif call_count[0] == 2:
                # Verification
                return '{"gaps": ["Missing location Thornwall"]}'
            else:
                # Gap fill
                return json.dumps({
                    "location_thornwall": "# Thornwall\n\nA fortress city.",
                })

        with patch(
            "tinyassets.providers.call.call_provider",
            side_effect=mock_provider,
        ):
            result = synthesize_source(
                "Kael the mage lives in Thornwall.",
                "lore.md",
                canon_dir,
            )

        assert "characters.md" in result
        assert "location_thornwall.md" in result
        assert (canon_dir / "location_thornwall.md").exists()
        assert call_count[0] == 3


# =====================================================================
# Tier 2: Bite-by-bite synthesis for large files
# =====================================================================


class TestSplitIntoBites:
    """Tests for the bite splitting logic."""

    def test_small_text_returns_single_bite(self):
        from tinyassets.ingestion.extractors import _split_into_bites

        text = "Short text.\n\nAnother paragraph."
        bites = _split_into_bites(text)
        assert len(bites) == 1
        assert text in bites[0]

    def test_natural_section_breaks(self):
        from tinyassets.ingestion.extractors import _split_into_bites

        # Build text with markdown headers and enough content to split
        sections = []
        for i in range(5):
            sections.append(f"## Section {i}\n\n{'Word ' * 5000}")
        text = "\n\n".join(sections)

        bites = _split_into_bites(text)
        assert len(bites) >= 2, f"Expected multiple bites, got {len(bites)}"
        # All content should be represented
        full = " ".join(bites)
        assert "Section 0" in full
        assert "Section 4" in full

    def test_paragraph_fallback(self):
        from tinyassets.ingestion.extractors import _split_into_bites

        # Many paragraphs, no headers
        paragraphs = [f"Paragraph {i}. " + "content " * 500 for i in range(20)]
        text = "\n\n".join(paragraphs)

        bites = _split_into_bites(text)
        assert len(bites) >= 2
        full = " ".join(bites)
        assert "Paragraph 0" in full
        assert "Paragraph 19" in full

    def test_fixed_size_fallback(self):
        from tinyassets.ingestion.extractors import _split_into_bites

        # Single massive block with no breaks
        text = "word " * 20000
        bites = _split_into_bites(text)
        assert len(bites) >= 2

    def test_bites_have_overlap(self):
        from tinyassets.ingestion.extractors import (
            _BITE_OVERLAP_CHARS,
            _group_paragraphs_into_bites,
        )

        # Build paragraphs that will split into 2+ bites
        paragraphs = [f"Unique marker {i}. " + "filler " * 600 for i in range(15)]
        bites = _group_paragraphs_into_bites(paragraphs)

        if len(bites) >= 2:
            # End of first bite should overlap with start of second
            tail_of_first = bites[0][-200:]
            assert any(
                word in bites[1][:_BITE_OVERLAP_CHARS + 500]
                for word in tail_of_first.split()[:5]
                if len(word) > 5
            ), "Expected overlap between consecutive bites"

    def test_chapter_markers_as_breaks(self):
        from tinyassets.ingestion.extractors import _split_into_bites

        sections = []
        for i in range(4):
            sections.append(f"Chapter {i + 1}\n\n{'Content ' * 6000}")
        text = "\n\n".join(sections)

        bites = _split_into_bites(text)
        assert len(bites) >= 2
        assert "Chapter 1" in bites[0]


class TestSynthesizeBiteByBite:
    """Integration tests for Tier 2 multi-bite synthesis."""

    def test_large_source_triggers_tier2(self, tmp_path):
        """Source text exceeding threshold triggers bite-by-bite."""
        from tinyassets.ingestion.extractors import (
            _TIER2_THRESHOLD,
            synthesize_source,
        )

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        # Build source larger than threshold
        sections = []
        for i in range(10):
            sections.append(
                f"## Region {i}\n\n"
                f"The kingdom of Region{i} has many castles. " * 500
            )
        large_source = "\n\n".join(sections)
        assert len(large_source) > _TIER2_THRESHOLD

        call_count = {"n": 0}

        def mock_provider(prompt, system, *, role="writer", **kw):
            call_count["n"] += 1
            # Synthesis calls return topic docs
            if "section" in prompt.lower() or "source material" in prompt.lower():
                region_num = call_count["n"]
                return json.dumps({
                    f"region_{region_num}": f"# Region {region_num}\n\nA kingdom.",
                })
            # Verification call
            return '{"gaps": []}'

        with patch(
            "tinyassets.providers.call.call_provider",
            side_effect=mock_provider,
        ):
            result = synthesize_source(large_source, "big_world.md", canon_dir)

        # Should have synthesized multiple bite results
        assert len(result) > 0
        # Should have made more calls than just 1 synthesis + 1 verify
        assert call_count["n"] >= 3

    def test_small_source_uses_single_pass(self, tmp_path):
        """Source text under threshold uses single-pass (no bites)."""
        from tinyassets.ingestion.extractors import synthesize_source

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        call_count = {"n": 0}

        def mock_provider(prompt, system, *, role="writer", **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return json.dumps({"chars": "# Characters\n\nKael."})
            return '{"gaps": []}'

        with patch(
            "tinyassets.providers.call.call_provider",
            side_effect=mock_provider,
        ):
            result = synthesize_source(
                "Kael is a mage.", "small.md", canon_dir,
            )

        assert "chars.md" in result
        # Single pass: 1 synthesis + 1 verification = 2 calls
        assert call_count["n"] == 2

    def test_bite_failure_doesnt_block_others(self, tmp_path):
        """If one bite's synthesis fails, other bites still produce output."""
        from tinyassets.ingestion.extractors import (
            _TIER2_THRESHOLD,
            synthesize_source,
        )

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        sections = []
        for i in range(6):
            sections.append(f"## Section {i}\n\n{'Text ' * 5000}")
        large_source = "\n\n".join(sections)
        assert len(large_source) > _TIER2_THRESHOLD

        call_count = {"n": 0}

        def mock_provider(prompt, system, *, role="writer", **kw):
            call_count["n"] += 1
            # First bite fails
            if call_count["n"] == 1:
                raise RuntimeError("Provider timeout")
            if "gaps" not in prompt.lower() and "missing" not in prompt.lower():
                return json.dumps({
                    f"topic_{call_count['n']}": "# Topic\n\nContent.",
                })
            return '{"gaps": []}'

        with patch(
            "tinyassets.providers.call.call_provider",
            side_effect=mock_provider,
        ):
            result = synthesize_source(large_source, "partial.md", canon_dir)

        # Should still produce some output despite first bite failure
        assert len(result) > 0

    def test_bite_results_merge_different_topics(self, tmp_path):
        """Different bites producing different topics should all appear."""
        from tinyassets.ingestion.extractors import _synthesize_bite_by_bite

        call_count = {"n": 0}

        def mock_provider(prompt, system, *, role="writer", **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return json.dumps({
                    "characters": "# Characters\n\nKael the mage.",
                })
            elif call_count["n"] == 2:
                return json.dumps({
                    "locations": "# Locations\n\nThornwall city.",
                })
            return json.dumps({})

        # Build source that will split into 2+ bites
        sections = [f"## Part {i}\n\n{'Text ' * 5000}" for i in range(4)]
        large_text = "\n\n".join(sections)

        docs = _synthesize_bite_by_bite(large_text, "test.md", "", mock_provider)
        assert "characters" in docs
        assert "locations" in docs


# =====================================================================
# Worldbuild synthesis signal handling
# =====================================================================


class TestWorldbuildSynthesisSignal:
    def test_worldbuild_handles_synthesize_source(self, tmp_path):
        """The worldbuild node should handle synthesize_source signals."""
        from domains.fantasy_daemon.phases.worldbuild import _act_on_signals_incremental

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        sources_dir = canon_dir / "sources"
        sources_dir.mkdir()

        # Write a source file
        (sources_dir / "big_lore.md").write_text(
            "# Deep Lore\n\nThe ancient kingdom fell 1000 years ago.",
            encoding="utf-8",
        )

        state = {
            "_universe_path": str(tmp_path),
            "_db_path": ":memory:",
            "premise_kernel": "Epic fantasy.",
        }

        signals = [{
            "type": "synthesize_source",
            "source_file": "big_lore.md",
            "topic": "big_lore",
        }]

        mock_response = json.dumps({
            "history": "# History\n\nThe kingdom fell.",
        })

        with patch(
            "tinyassets.providers.call.call_provider",
            return_value=mock_response,
        ):
            acted, consumed = _act_on_signals_incremental(signals, state)

        assert acted == 1
        assert 0 in consumed

    def test_synthesis_sanitizes_llm_topic_before_writing(self, tmp_path):
        """Synthesized LLM topic names must not become path components."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        mock_response = json.dumps({
            "../../escape": "# Escape\n\nShould stay inside canon.",
        })

        with patch(
            "tinyassets.providers.call.call_provider",
            return_value=mock_response,
        ):
            generated = synthesize_source(
                "# Source\n\nA malicious topic.",
                "source.md",
                canon_dir,
                "Epic fantasy.",
            )

        assert generated == ["escape.md"]
        assert (canon_dir / "escape.md").exists()
        assert not (tmp_path / "escape.md").exists()

    def test_synthesis_failure_consumes_signal_and_records_attempt(self, tmp_path):
        """When synthesis fails, signal is consumed and attempt is recorded.

        The API re-emit mechanism will re-queue if retries remain.
        """
        from domains.fantasy_daemon.phases.worldbuild import _act_on_signals_incremental

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        sources_dir = canon_dir / "sources"
        sources_dir.mkdir()

        (sources_dir / "empty_lore.md").write_text(
            "# Empty\n\nNothing useful here.",
            encoding="utf-8",
        )

        state = {
            "_universe_path": str(tmp_path),
            "_db_path": ":memory:",
            "premise_kernel": "A fantasy story.",
        }

        signals = [{
            "type": "synthesize_source",
            "source_file": "empty_lore.md",
            "topic": "empty_lore",
        }]

        # Provider returns empty string -> synthesis fails
        with patch(
            "tinyassets.providers.call.call_provider",
            return_value="",
        ):
            acted, consumed = _act_on_signals_incremental(signals, state)

        assert acted == 0
        assert 0 in consumed  # Signal consumed; re-emit will re-queue

        # Manifest should record the failed attempt
        manifest = json.loads(
            (canon_dir / ".manifest.json").read_text(encoding="utf-8"),
        )
        assert manifest["empty_lore.md"]["synthesis_attempts"] == 1


# =====================================================================
# Image extraction
# =====================================================================


class TestImageExtractor:
    def test_placeholder_when_no_vision(self):
        """Should return placeholder when Ollama is unavailable."""
        from tinyassets.ingestion.image_extractor import (
            extract_image_description,
        )

        # Use a minimal valid PNG
        png_data = _make_tiny_png()

        with patch(
            "tinyassets.ingestion.image_extractor._find_vision_model",
            return_value="",
        ):
            result = extract_image_description("map.png", png_data)

        assert "Image awaiting visual analysis" in result or "map.png" in result

    def test_ollama_vision_called_when_available(self):
        """Should call Ollama when a vision model is found."""
        from tinyassets.ingestion.image_extractor import (
            extract_image_description,
        )

        png_data = _make_tiny_png()

        mock_resp = json.dumps({"response": "A detailed map of a fantasy kingdom."})
        mock_urlopen = _mock_urlopen(mock_resp.encode())

        with (
            patch(
                "tinyassets.ingestion.image_extractor._find_vision_model",
                return_value="llava",
            ),
            patch(
                "urllib.request.urlopen",
                mock_urlopen,
            ),
        ):
            result = extract_image_description(
                "map.png", png_data, premise="Epic fantasy",
            )

        assert "fantasy kingdom" in result

    def test_resize_preserves_small_images(self):
        """Images under MAX_IMAGE_DIMENSION should not be upscaled."""
        from tinyassets.ingestion.image_extractor import _resize_image

        png_data = _make_tiny_png()
        result = _resize_image(png_data)

        if result is not None:  # Pillow available
            assert len(result) > 0

    def test_extract_text_routes_images(self):
        """extract_text should route image files to image extractor."""
        png_data = _make_tiny_png()

        with patch(
            "tinyassets.ingestion.image_extractor._find_vision_model",
            return_value="",
        ):
            result = extract_text("world_map.png", png_data)

        # Should get a placeholder (no vision model in tests)
        assert isinstance(result, str)
        assert len(result) > 0


class TestVideoExtractor:
    def test_no_ffmpeg_outside_the_owner_cell(self):
        """ffmpeg runs as the owner, in the owner's video cell, or not at all.

        The module used to look for ffmpeg on PATH and, failing that, return a
        placeholder that read like a real description. Both are gone: there is
        no daemon-uid ffmpeg and no answer that is not an answer.
        """
        from tinyassets.ingestion import video_extractor

        assert not hasattr(video_extractor, "_find_ffmpeg")
        assert not hasattr(video_extractor, "_placeholder_description")
        assert not hasattr(video_extractor, "_extract_with_ffmpeg")
        assert not hasattr(video_extractor, "_get_video_duration")
        assert "subprocess" not in inspect.getsource(video_extractor)

    def test_format_timestamp(self):
        """Timestamps should format correctly."""
        from tinyassets.ingestion.video_extractor import (
            _format_timestamp,
        )

        assert _format_timestamp(0) == "0:00"
        assert _format_timestamp(65) == "1:05"
        assert _format_timestamp(3661) == "1:01:01"

    def test_the_cells_frames_reach_the_owner_scoped_vision_callback(self, tmp_path):
        """Full pipeline: the owner's video cell returns frames, the caller
        describes each one under the owner's own vision authority."""
        from tinyassets import role_video
        from tinyassets.ingestion.video_extractor import extract_video_description

        video_data = b"\x00\x00\x00\x1cftyp" + b"\x00" * 1000
        png_data = _make_tiny_png()
        seen = []

        def frames(data, universe_dir):
            assert data is video_data and universe_dir == tmp_path
            return 25.0, [png_data, png_data]

        def describe(name, data, *, premise):
            seen.append((name, premise))
            return f"a frame called {name}"

        with patch.object(role_video, "frames", frames):
            result = extract_video_description(
                "scene.mp4", video_data, premise="Epic quest",
                universe_dir=tmp_path, describe_frame=describe,
            )

        assert "Visual Reference: scene.mp4" in result
        assert "Frame 1" in result and "Frame 2" in result
        assert "0:00" in result and "0:10" in result
        assert seen == [("scene.mp4_frame_000.png", "Epic quest"),
                        ("scene.mp4_frame_001.png", "Epic quest")]

    def test_extract_text_routes_video(self, tmp_path):
        """extract_text routes video files to the video extractor."""
        from tinyassets import role_video
        from tinyassets.ingestion.video_extractor import extract_video_description

        video_data = b"\x00\x00\x00\x1cftyp" + b"\x00" * 1000
        with patch.object(role_video, "frames",
                          lambda data, center: (10.0, [_make_tiny_png()])):
            result = extract_text("cutscene.mp4", video_data, universe_dir=tmp_path,
                                  describe_frame=lambda *a, **k: "described")
        assert isinstance(result, str)
        assert "Visual Reference: cutscene.mp4" in result

        # And without a vision callback it refuses instead of inventing one.
        with pytest.raises(RuntimeError, match="owner-scoped vision"):
            extract_video_description("cutscene.mp4", video_data,
                                      universe_dir=tmp_path)

    def test_video_detection_by_extension(self):
        """Video file types should be detected by extension."""
        for ext in [".mp4", ".webm", ".mkv", ".avi", ".mov"]:
            result = detect_file_type(f"video{ext}")
            assert result.file_type == FileType.VIDEO, (
                f"Expected VIDEO for {ext}, got {result.file_type}"
            )

    def test_mp4_detection_by_magic_bytes(self):
        """MP4 should be detected by ftyp magic bytes."""
        mp4_header = b"\x00\x00\x00\x1cftyp" + b"\x00" * 100
        result = detect_file_type("unknown_file", data=mp4_header)
        assert result.file_type == FileType.VIDEO
        assert result.mime_type == "video/mp4"


def _make_tiny_png() -> bytes:
    """Create a minimal 1x1 pixel PNG for testing."""
    try:
        import io

        from PIL import Image

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Minimal PNG header + IHDR + IDAT + IEND
        return (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            + b"\x00" * 100  # padding (not a valid PNG but works for type detection)
        )


def _mock_urlopen(response_data: bytes):
    """Create a mock for urllib.request.urlopen."""
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    def _urlopen(*args, **kwargs):
        return mock_resp

    return _urlopen
