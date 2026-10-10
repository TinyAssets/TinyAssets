"""Text extraction from various file formats.

Extracts plain text from:
- Markdown (.md, .markdown) -- pass-through
- Plain text (.txt, .text) -- pass-through
- JSON/YAML -- pass-through
- PDF (.pdf) -- pymupdf if available, fallback to raw bytes decode
- DOCX (.docx) -- python-docx if available, fallback to zip XML extraction

All extractors return plain text suitable for LLM processing.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from tinyassets.ingestion.canon_names import resolve_within_canon, safe_canon_filename
from tinyassets.ingestion.core import FileType, detect_file_type
from tinyassets.utils.json_parsing import parse_llm_json

logger = logging.getLogger(__name__)


def extract_text(filename: str, data: bytes, *, universe_dir: Path | None = None,
                 describe_frame=None) -> str:
    """Extract text content from a file.

    Parameters
    ----------
    filename : str
        Filename (for extension-based detection).
    data : bytes
        Raw file content.

    Returns
    -------
    str
        Extracted text. Empty string if extraction fails or file
        type is not text-extractable.
    """
    detected = detect_file_type(filename, data)
    ext = Path(filename).suffix.lower()

    # Direct text formats
    if detected.file_type == FileType.TEXT:
        return _decode_text(data)

    # PDF
    if ext == ".pdf" or detected.mime_type == "application/pdf":
        return _extract_pdf(data)

    # DOCX
    if ext == ".docx" or "wordprocessingml" in detected.mime_type:
        return _extract_docx(data)

    # EPUB (ZIP with XHTML inside)
    if ext == ".epub":
        return _extract_epub(data)

    # Image -- use vision model for description
    if detected.file_type == FileType.IMAGE:
        from tinyassets.ingestion.image_extractor import extract_image_description

        return extract_image_description(filename, data)

    # Video -- extract keyframes via ffmpeg + image pipeline
    if detected.file_type == FileType.VIDEO:
        from tinyassets.ingestion.video_extractor import extract_video_description

        return extract_video_description(filename, data, universe_dir=universe_dir,
                                         describe_frame=describe_frame)

    # Unknown -- try text decode as last resort
    if detected.file_type == FileType.UNKNOWN:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    return ""


def _decode_text(data: bytes) -> str:
    """Decode bytes to text, trying UTF-8 then Latin-1."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    """Extract text from PDF bytes.

    Uses pymupdf (fitz) if available, falls back to regex extraction
    of text-like content from raw PDF bytes.
    """
    try:
        from tinyassets.ingestion.pdf_bytes import open_pdf_bytes

        doc = open_pdf_bytes(data)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        text = "\n\n".join(pages)
        if text.strip():
            logger.info("Extracted %d chars from PDF via pymupdf", len(text))
            return text
    except ImportError:
        logger.debug("pymupdf not installed; using fallback PDF extraction")
    except Exception as e:
        logger.warning("pymupdf extraction failed: %s", e)

    # Fallback: crude regex extraction of readable text from PDF bytes
    try:
        raw = data.decode("latin-1")
        # Extract text between BT...ET blocks (PDF text objects)
        text_blocks = re.findall(r"\((.*?)\)", raw)
        text = " ".join(
            block for block in text_blocks
            if len(block) > 3 and block.isprintable()
        )
        if text.strip():
            logger.info("Extracted %d chars from PDF via fallback", len(text))
            return text
    except Exception:
        pass

    return ""


def _extract_docx(data: bytes) -> str:
    """Extract text from DOCX bytes.

    Uses python-docx if available, falls back to ZIP XML extraction.
    """
    import io

    try:
        import docx

        doc = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        if text.strip():
            logger.info("Extracted %d chars from DOCX via python-docx", len(text))
            return text
    except ImportError:
        logger.debug("python-docx not installed; using fallback DOCX extraction")
    except Exception as e:
        logger.warning("python-docx extraction failed: %s", e)

    # Fallback: extract from ZIP's word/document.xml
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "word/document.xml" in zf.namelist():
                xml = zf.read("word/document.xml").decode("utf-8")
                # Strip XML tags, extract text content
                text = re.sub(r"<[^>]+>", " ", xml)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    logger.info("Extracted %d chars from DOCX via ZIP fallback", len(text))
                    return text
    except Exception:
        pass

    return ""


def _extract_epub(data: bytes) -> str:
    """Extract text from EPUB bytes via ZIP XHTML extraction."""
    import io

    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            parts = []
            for name in sorted(zf.namelist()):
                if name.endswith((".xhtml", ".html", ".htm")):
                    try:
                        html = zf.read(name).decode("utf-8")
                        text = re.sub(r"<[^>]+>", " ", html)
                        text = re.sub(r"\s+", " ", text).strip()
                        if text:
                            parts.append(text)
                    except Exception:
                        continue
            result = "\n\n".join(parts)
            if result:
                logger.info("Extracted %d chars from EPUB", len(result))
            return result
    except Exception as e:
        logger.warning("EPUB extraction failed: %s", e)
        return ""


# Threshold for Tier 2 bite-by-bite synthesis (~50K chars, generous for Opus).
_TIER2_THRESHOLD = 50_000

# Overlap between bites: last N chars of previous bite prepended to next.
_BITE_OVERLAP_CHARS = 500

# Target bite size when no natural breaks exist.
_BITE_TARGET_CHARS = 30_000

_SYNTHESIS_SYSTEM = (
    "You are a worldbuilding author. Create a complete collection of canon "
    "documents from this source material. Every detail must be represented. "
    "Organize by topic. Each document should be thorough and specific.\n\n"
    "Return a JSON object with keys being descriptive topic slugs "
    "(e.g. 'character-corin', 'location-ashwater', 'magic-lattice', "
    "'faction-wardens', 'history-founding') and values being the full "
    "markdown document content.\n\n"
    "Rules:\n"
    "- Represent EVERY detail from the source -- nothing omitted\n"
    "- Create as many documents as needed to cover all topics thoroughly\n"
    "- Use markdown formatting with headers, lists, and structure\n"
    "- Extract and organize, don't invent new details\n"
    "- Be specific: use exact names, places, numbers, and details from the source\n"
    "- Return ONLY the JSON object, no commentary"
)


def synthesize_source(
    source_text: str,
    filename: str,
    canon_dir: Path,
    premise: str = "",
) -> list[str]:
    """Synthesize structured canon documents from source material.

    For small sources (< ~50K chars), runs a single-pass synthesis.
    For large sources, splits into overlapping bites at natural breaks
    and synthesizes each bite, merging results progressively.

    Parameters
    ----------
    source_text : str
        Extracted text from the source file.
    filename : str
        Source filename (for context in prompts).
    canon_dir : Path
        The canon/ directory to write synthesized docs to.
    premise : str
        Story premise for context.

    Returns
    -------
    list[str]
        Filenames of synthesized documents.
    """
    from domains.fantasy_daemon.phases.worldbuild import _write_canon_file
    from tinyassets.providers.call import call_provider, get_last_provider

    if not source_text.strip():
        return []

    # Decide: single-pass or bite-by-bite
    if len(source_text) <= _TIER2_THRESHOLD:
        docs = _synthesize_single_pass(source_text, filename, premise, call_provider)
    else:
        docs = _synthesize_bite_by_bite(source_text, filename, premise, call_provider)

    if not docs:
        return []

    # --- Verification pass: runs once against full source ---
    docs = _verify_and_fill_gaps(source_text, filename, docs, premise, call_provider)

    generated: list[str] = []
    for topic, content in docs.items():
        if not content or not content.strip():
            continue
        try:
            doc_filename = safe_canon_filename(topic)
        except ValueError:
            logger.warning("Skipping synthesized canon topic with unsafe empty slug: %r", topic)
            continue

        # Don't overwrite user-authored canon (check for .reviewed marker).
        # Containment before read: ``read_text`` follows symlinks, so a
        # symlinked ``.reviewed`` marker pointing outside canon_dir would leak
        # external content into the overwrite-guard decision. Resolve and
        # reject escapes before any read.
        try:
            marker = resolve_within_canon(
                canon_dir, f".{doc_filename}.reviewed", kind="marker"
            )
        except ValueError:
            logger.warning(
                "Marker escapes canon dir, skipping synthesized doc: %s",
                doc_filename,
            )
            continue
        if marker.exists():
            try:
                marker_data = json.loads(marker.read_text(encoding="utf-8"))
                if marker_data.get("model") == "user":
                    logger.info(
                        "Skipping %s: user-authored (won't overwrite)", doc_filename,
                    )
                    continue
            except Exception:
                pass

        _write_canon_file(
            canon_dir, doc_filename, content, model=get_last_provider(),
        )
        generated.append(doc_filename)
        logger.info(
            "Synthesized %s from %s (%d chars)",
            doc_filename, filename, len(content),
        )

    return generated


def _synthesize_single_pass(
    source_text: str,
    filename: str,
    premise: str,
    provider_call: Any,
) -> dict[str, str]:
    """Single-pass synthesis for sources that fit in one prompt."""
    premise_section = f"# Story Premise\n\n{premise}\n\n" if premise else ""
    prompt = (
        f"{premise_section}"
        f"# Source Material: {filename}\n\n"
        f"{source_text}\n\n"
        f"# Task\n\n"
        f"Create a complete set of canon documents from this source material. "
        f"Every detail must be captured. Organize by topic. Return as JSON."
    )

    raw = provider_call(prompt, _SYNTHESIS_SYSTEM, role="writer")
    if not raw:
        logger.warning("Synthesis returned empty for %s", filename)
        return {}

    docs = _parse_synthesis_response(raw)
    if not docs:
        topic = Path(filename).stem.replace("-", "_").replace(" ", "_")
        docs = {topic: raw}
    return docs


_LAST_BITE_OUTCOMES: dict[str, dict[str, int]] = {}


def get_last_bite_outcomes(filename: str) -> dict[str, int]:
    """Return the per-bite outcome tally from the most recent Tier-2 run.

    Task #17 Fix C — exposes the internal diagnostics so the
    worldbuild handler can surface them into the manifest for post-mortem
    debugging. Keys: ``ok``, ``provider_error``, ``empty_response``,
    ``parse_failed``, ``parsed_but_empty``. Returns an empty dict if
    the file hasn't been synthesized (or was single-pass).
    """
    return dict(_LAST_BITE_OUTCOMES.get(filename, {}))


def _synthesize_bite_by_bite(
    source_text: str,
    filename: str,
    premise: str,
    provider_call: Any,
) -> dict[str, str]:
    """Tier 2: split large source into overlapping bites and synthesize each.

    1. Split at natural breaks (section headers, chapter markers, double newlines).
    2. Fall back to fixed-size bites with overlap when no natural breaks exist.
    3. Each bite synthesizes into canon using the same prompt format.
    4. Results are merged progressively -- later bites update/extend earlier docs.

    Task #17 Fix C: per-bite outcomes are stashed via
    ``_LAST_BITE_OUTCOMES`` so the caller can surface them into the
    manifest — "why is synthesized_docs empty?" becomes answerable
    without source-spelunking.
    """
    bites = _split_into_bites(source_text)
    logger.info(
        "Tier 2 synthesis: %s split into %d bites (%d chars total)",
        filename, len(bites), len(source_text),
    )

    all_docs: dict[str, str] = {}
    existing_topics: list[str] = []
    # Task #17 Fix C: per-bite outcome tally so an empty-result failure
    # mode ("all bites returned non-JSON") is debuggable from logs alone.
    bite_outcomes: dict[str, int] = {
        "ok": 0, "provider_error": 0, "empty_response": 0,
        "parse_failed": 0, "parsed_but_empty": 0,
    }

    for i, bite in enumerate(bites):
        logger.info(
            "Synthesizing bite %d/%d for %s (%d chars)",
            i + 1, len(bites), filename, len(bite),
        )

        premise_section = f"# Story Premise\n\n{premise}\n\n" if premise else ""
        existing_section = ""
        if existing_topics:
            existing_section = (
                f"# Already Synthesized Topics\n\n"
                f"{', '.join(existing_topics)}\n\n"
                f"If this bite contains new details about an existing topic, "
                f"create a supplementary document with a distinct slug "
                f"(e.g. 'character-corin-backstory' if 'character-corin' exists).\n\n"
            )

        prompt = (
            f"{premise_section}"
            f"{existing_section}"
            f"# Source Material: {filename} (section {i + 1}/{len(bites)})\n\n"
            f"{bite}\n\n"
            f"# Task\n\n"
            f"Create canon documents from this section. "
            f"Every detail must be captured. Organize by topic. Return as JSON."
        )

        try:
            raw = provider_call(prompt, _SYNTHESIS_SYSTEM, role="writer")
        except Exception as e:
            logger.warning(
                "bite_diag: %s bite %d/%d provider_error (%d chars in) — %s",
                filename, i + 1, len(bites), len(bite), e,
            )
            bite_outcomes["provider_error"] += 1
            continue

        if not raw:
            logger.warning(
                "bite_diag: %s bite %d/%d empty_response (%d chars in, 0 chars out)",
                filename, i + 1, len(bites), len(bite),
            )
            bite_outcomes["empty_response"] += 1
            continue

        bite_docs = _parse_synthesis_response(raw)
        if not bite_docs:
            # Try to distinguish parse-failure vs parsed-empty-dict —
            # both return ``{}`` from _parse_synthesis_response, so we
            # look at the raw for a top-level JSON object shape. Cheap
            # heuristic but enough to narrow the failure mode.
            stripped = raw.strip().lstrip("`").strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                logger.warning(
                    "bite_diag: %s bite %d/%d parsed_but_empty "
                    "(%d chars in, %d chars raw out)",
                    filename, i + 1, len(bites), len(bite), len(raw),
                )
                bite_outcomes["parsed_but_empty"] += 1
            else:
                logger.warning(
                    "bite_diag: %s bite %d/%d parse_failed "
                    "(%d chars in, %d chars raw out, head=%r)",
                    filename, i + 1, len(bites), len(bite), len(raw),
                    raw[:80],
                )
                bite_outcomes["parse_failed"] += 1
            continue

        bite_outcomes["ok"] += 1
        logger.info(
            "bite_diag: %s bite %d/%d ok (%d chars in, %d topics out)",
            filename, i + 1, len(bites), len(bite), len(bite_docs),
        )

        # Merge bite results: for duplicate keys, append content
        for topic, content in bite_docs.items():
            if topic in all_docs:
                all_docs[topic] += f"\n\n{content}"
            else:
                all_docs[topic] = content

        existing_topics = list(all_docs.keys())

    # Summary log so "why is synthesized_docs empty?" is answerable from
    # one grep on the filename.
    summary_level = logger.warning if not all_docs else logger.info
    summary_level(
        "Tier 2 synthesis complete for %s: %d topics from %d bites "
        "(ok=%d, provider_error=%d, empty_response=%d, parse_failed=%d, "
        "parsed_but_empty=%d)",
        filename, len(all_docs), len(bites),
        bite_outcomes["ok"], bite_outcomes["provider_error"],
        bite_outcomes["empty_response"], bite_outcomes["parse_failed"],
        bite_outcomes["parsed_but_empty"],
    )
    # Stash for the caller (worldbuild._handle_synthesize_source) to
    # mirror into the manifest entry so the state is visible on disk.
    _LAST_BITE_OUTCOMES[filename] = {
        **bite_outcomes, "bites_total": len(bites),
    }
    return all_docs


def _split_into_bites(text: str) -> list[str]:
    """Split text into synthesis-sized bites.

    Strategy:
    1. Try natural breaks first (markdown headers, chapter markers).
    2. Fall back to paragraph-boundary splits with overlap.
    3. Last resort: fixed-size splits with character overlap.

    Each bite includes overlap from the previous bite for continuity.
    """
    # --- Try natural section breaks ---
    # Matches markdown headers (# ... ## ... ###) or "Chapter N" patterns
    section_pattern = re.compile(
        r"^(?:#{1,3}\s+.+|Chapter\s+\d+|CHAPTER\s+\d+|Part\s+\d+|PART\s+\d+)",
        re.MULTILINE,
    )
    section_starts = [m.start() for m in section_pattern.finditer(text)]

    if len(section_starts) >= 2:
        # We have natural sections -- group them into bites under target size
        return _group_sections_into_bites(text, section_starts)

    # --- Fall back to paragraph-boundary splits ---
    paragraphs = text.split("\n\n")
    if len(paragraphs) >= 3:
        return _group_paragraphs_into_bites(paragraphs)

    # --- Last resort: fixed-size with overlap ---
    return _fixed_size_bites(text)


def _group_sections_into_bites(
    text: str,
    section_starts: list[int],
) -> list[str]:
    """Group natural sections into bites that fit under the target size."""
    # Add end-of-text as final boundary
    boundaries = section_starts + [len(text)]

    bites: list[str] = []
    current_size = 0
    bite_start = 0

    for i in range(len(boundaries) - 1):
        section_text = text[boundaries[i]:boundaries[i + 1]]
        section_size = len(section_text)

        if current_size + section_size > _BITE_TARGET_CHARS and current_size > 0:
            # Emit current bite
            bite_text = text[bite_start:boundaries[i]]
            bites.append(bite_text)
            # Start new bite with overlap
            overlap_start = max(bite_start, boundaries[i] - _BITE_OVERLAP_CHARS)
            bite_start = overlap_start
            current_size = boundaries[i] - overlap_start

        current_size += section_size

    # Emit final bite
    if bite_start < len(text):
        bites.append(text[bite_start:])

    return bites if bites else [text]


def _group_paragraphs_into_bites(paragraphs: list[str]) -> list[str]:
    """Group paragraphs into bites under the target size with overlap."""
    bites: list[str] = []
    current: list[str] = []
    current_size = 0
    overlap_paras: list[str] = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if current_size + len(para) > _BITE_TARGET_CHARS and current:
            bites.append("\n\n".join(current))
            # Keep last few paragraphs as overlap
            overlap_chars = 0
            overlap_paras = []
            for p in reversed(current):
                if overlap_chars + len(p) > _BITE_OVERLAP_CHARS:
                    break
                overlap_paras.insert(0, p)
                overlap_chars += len(p)
            current = list(overlap_paras)
            current_size = sum(len(p) for p in current)

        current.append(para)
        current_size += len(para)

    if current:
        bites.append("\n\n".join(current))

    return bites if bites else ["\n\n".join(paragraphs)]


def _fixed_size_bites(text: str) -> list[str]:
    """Split text into fixed-size bites with character overlap."""
    bites: list[str] = []
    start = 0
    while start < len(text):
        end = start + _BITE_TARGET_CHARS
        if end >= len(text):
            bites.append(text[start:])
            break
        # Try to break at a sentence boundary
        break_pos = text.rfind(". ", start + _BITE_TARGET_CHARS // 2, end)
        if break_pos > start:
            end = break_pos + 1
        bites.append(text[start:end])
        start = end - _BITE_OVERLAP_CHARS
    return bites if bites else [text]


def _parse_synthesis_response(raw: str) -> dict[str, str]:
    """Parse the LLM's JSON response into topic -> content mapping."""
    data = parse_llm_json(raw, expect_type=dict, fallback=None)
    if data is None:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _verify_and_fill_gaps(
    source_text: str,
    filename: str,
    docs: dict[str, str],
    premise: str,
    provider_call: Any,
) -> dict[str, str]:
    """Verify synthesis completeness and fill gaps with a second pass.

    Sends the original source + synthesized canon to the LLM and asks
    for any missing details. If gaps are found, runs a targeted
    re-synthesis and merges the results.

    Returns the updated docs dict (original + any gap-filling additions).
    """
    if not docs:
        return docs

    # Build a summary of what was synthesized
    canon_summary = "\n\n".join(
        f"### {topic}\n{content[:1000]}" for topic, content in docs.items()
    )

    verify_system = (
        "You are a completeness checker. Compare the original source material "
        "against the synthesized canon documents. List any details, names, "
        "places, facts, relationships, or world elements from the source that "
        "are NOT represented in the canon.\n\n"
        "If everything is covered, return exactly: {\"gaps\": []}\n\n"
        "If there are gaps, return a JSON object:\n"
        "{\"gaps\": [\"description of missing detail 1\", ...]}"
    )

    verify_prompt = (
        f"# Original Source: {filename}\n\n"
        f"{source_text}\n\n"
        f"# Synthesized Canon Documents\n\n"
        f"{canon_summary}\n\n"
        f"# Task\n\n"
        f"What details from the source are missing from the canon?"
    )

    try:
        raw = provider_call(verify_prompt, verify_system, role="writer")
    except Exception as e:
        logger.warning("Verification pass failed: %s", e)
        return docs

    if not raw:
        return docs

    gaps = _parse_gap_response(raw)
    if not gaps:
        logger.info("Verification pass: no gaps found for %s", filename)
        return docs

    logger.info("Verification found %d gaps for %s, re-synthesizing", len(gaps), filename)

    # Re-synthesize with gaps highlighted
    gap_list = "\n".join(f"- {g}" for g in gaps)
    premise_section = f"# Story Premise\n\n{premise}\n\n" if premise else ""

    fill_system = (
        "You are a worldbuilding author. Create additional canon documents to "
        "cover the gaps identified below. Return a JSON object with topic slug "
        "keys and markdown document values. Only cover the MISSING details -- "
        "do not duplicate what already exists.\n\n"
        "Return ONLY the JSON object, no commentary."
    )

    fill_prompt = (
        f"{premise_section}"
        f"# Original Source: {filename}\n\n"
        f"{source_text}\n\n"
        f"# Existing Canon Topics\n\n"
        f"{', '.join(docs.keys())}\n\n"
        f"# Missing Details\n\n{gap_list}\n\n"
        f"# Task\n\n"
        f"Create canon documents for the missing details. Return as JSON."
    )

    try:
        fill_raw = provider_call(fill_prompt, fill_system, role="writer")
    except Exception as e:
        logger.warning("Gap-filling synthesis failed: %s", e)
        return docs

    if not fill_raw:
        return docs

    fill_docs = _parse_synthesis_response(fill_raw)
    if fill_docs:
        docs.update(fill_docs)
        logger.info("Gap-filling added %d documents for %s", len(fill_docs), filename)

    return docs


def _parse_gap_response(raw: str) -> list[str]:
    """Parse the verification LLM response into a list of gap descriptions."""
    data = parse_llm_json(raw, expect_type=dict, fallback=None)
    if data is None:
        return []

    gaps = data.get("gaps", [])
    if isinstance(gaps, list):
        return [g for g in gaps if isinstance(g, str) and g.strip()]

    return []
