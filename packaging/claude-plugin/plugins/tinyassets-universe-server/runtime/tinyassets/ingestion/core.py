"""Ingestion core -- file type detection, routing, manifest, and signals.

Routes incoming files based on size and type:
- Files <=5KB: written directly to canon/ (small enough to be useful as-is)
- Files >5KB: written to canon/sources/ and a ``synthesize_source`` signal
  is emitted for the enrichment phase to process into canon documents.

The manifest (canon/.manifest.json) tracks:
- Every source file and its metadata
- Source -> synthesized document mappings (populated by enrichment)
- File hashes for change detection
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from tinyassets.enrichment_signals import append_enrichment_signals
from tinyassets.ingestion.canon_names import resolve_within_canon
from tinyassets.universe_files import (
    MAX_PLATFORM_FILE_BYTES,
    read_data_path,
    write_data_path,
)

logger = logging.getLogger(__name__)

# Files at or below this size go directly to canon/.
# Files above this size go to canon/sources/ and trigger synthesis.
SIZE_THRESHOLD = 5 * 1024  # 5KB

# Magic bytes for common file types.
_MAGIC_TABLE: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP
    (b"%PDF-", "application/pdf"),
    # Note: PK\x03\x04 (ZIP) omitted — too ambiguous (docx, epub, odt all use ZIP containers).
    # Let extension-based detection handle these.
    (b"\x1a\x45\xdf\xa3", "video/webm"),  # Matroska/WebM
    (b"\x00\x00\x00\x1cftyp", "video/mp4"),
    (b"\x00\x00\x00\x18ftyp", "video/mp4"),
    (b"\x00\x00\x00\x20ftyp", "video/mp4"),
]

# Extension -> MIME type mapping for text-like files.
_TEXT_EXTENSIONS: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".csv": "text/csv",
    ".tsv": "text/tsv",
    ".xml": "text/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".rst": "text/x-rst",
    ".org": "text/x-org",
    ".tex": "text/x-latex",
}

_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

_VIDEO_EXTENSIONS: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
}

_AUDIO_EXTENSIONS: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}

_DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".odt": "application/vnd.oasis.opendocument.text",
}


class FileType(Enum):
    """High-level file type categories."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DetectedType:
    """Result of file type detection."""

    file_type: FileType
    mime_type: str
    extension: str
    method: str  # "extension", "magic", or "both"


@dataclass
class ManifestEntry:
    """A single entry in the ingestion manifest."""

    filename: str
    source_path: str  # relative path within canon/ or canon/sources/
    file_type: str
    mime_type: str
    byte_count: int
    sha256: str
    routed_to: str  # "canon" or "sources"
    synthesized_docs: list[str] = field(default_factory=list)
    ingested_at: str = ""
    # Task #17 Fix C: per-bite outcome tally from the most recent
    # Tier-2 synthesis run. Empty dict when the source was Tier-1
    # (single-pass) or hasn't been synthesized yet. Keys:
    # ``ok``, ``provider_error``, ``empty_response``, ``parse_failed``,
    # ``parsed_but_empty``, ``bites_total``.
    last_bite_outcomes: dict[str, int] = field(default_factory=dict)


@dataclass
class SourceManifest:
    """Tracks all ingested files and their synthesized outputs.

    Stored at ``canon/.manifest.json``.
    """

    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def add(self, entry: ManifestEntry) -> None:
        self.entries[entry.filename] = entry

    def get(self, filename: str) -> ManifestEntry | None:
        return self.entries.get(filename)

    def has_changed(self, filename: str, sha256: str) -> bool:
        """Return True if the file is new or its hash has changed."""
        existing = self.entries.get(filename)
        if existing is None:
            return True
        return existing.sha256 != sha256

    def save(self, canon_dir: Path) -> None:
        """Write the manifest to canon/.manifest.json, link-free.

        ``write_data_path`` refuses a link anywhere on the path (and replaces,
        never writes through, one at the name). A refusal raises.
        """
        data = {
            name: asdict(entry) for name, entry in self.entries.items()
        }
        write_data_path(canon_dir / ".manifest.json", json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, canon_dir: Path) -> SourceManifest:
        """Load the manifest from canon/.manifest.json, link-free.

        Absent reads as an empty manifest. A REFUSED read (a link on the path)
        raises: ingestion must not go on to write and save a manifest derived
        from "empty" over the real one.
        """
        manifest = cls()
        raw = read_data_path(canon_dir / ".manifest.json", max_bytes=MAX_PLATFORM_FILE_BYTES)
        if raw is None:
            return manifest
        try:
            data = json.loads(raw.decode("utf-8"))
            for name, entry_data in data.items():
                manifest.entries[name] = ManifestEntry(**entry_data)
        except (ValueError, TypeError, AttributeError):
            logger.debug("Failed to load manifest", exc_info=True)
        return manifest
        raw = read_data_path(canon_dir / ".manifest.json", max_bytes=MAX_PLATFORM_FILE_BYTES)
        if raw is None:
            return manifest
        try:
            data = json.loads(raw.decode("utf-8"))
            for name, entry_data in data.items():
                manifest.entries[name] = ManifestEntry(**entry_data)
        except (OSError, json.JSONDecodeError, TypeError):
            logger.debug("Failed to load manifest", exc_info=True)
        return manifest


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Result of ingesting a single file."""

    filename: str
    routed_to: str  # "canon" or "sources"
    file_type: FileType
    mime_type: str
    byte_count: int
    sha256: str
    signal_emitted: bool  # True if a synthesize_source signal was created


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------


def detect_file_type(
    filename: str,
    data: bytes | None = None,
) -> DetectedType:
    """Detect file type from extension and optional magic bytes.

    Parameters
    ----------
    filename : str
        The filename (used for extension matching).
    data : bytes or None
        File content (first few bytes are enough). When provided,
        magic byte detection is attempted first.

    Returns
    -------
    DetectedType
        The detected file type, MIME type, and detection method.
    """
    ext = Path(filename).suffix.lower()
    magic_mime = _detect_magic(data) if data else None
    ext_result = _detect_extension(ext)

    if magic_mime and ext_result:
        # Both agree on category -- use magic for MIME, extension for category
        magic_type = _mime_to_filetype(magic_mime)
        if magic_type == ext_result.file_type:
            return DetectedType(
                file_type=ext_result.file_type,
                mime_type=magic_mime,
                extension=ext,
                method="both",
            )
        # Disagreement -- trust magic bytes over extension
        return DetectedType(
            file_type=magic_type,
            mime_type=magic_mime,
            extension=ext,
            method="magic",
        )

    if magic_mime:
        return DetectedType(
            file_type=_mime_to_filetype(magic_mime),
            mime_type=magic_mime,
            extension=ext,
            method="magic",
        )

    if ext_result:
        return ext_result

    return DetectedType(
        file_type=FileType.UNKNOWN,
        mime_type="application/octet-stream",
        extension=ext,
        method="extension",
    )


def _detect_magic(data: bytes | None) -> str | None:
    """Check magic bytes against known signatures."""
    if not data:
        return None
    for magic, mime in _MAGIC_TABLE:
        if data[:len(magic)] == magic:
            # Special case: RIFF needs WEBP check
            if magic == b"RIFF" and len(data) >= 12:
                if data[8:12] != b"WEBP":
                    return None
            return mime
    return None


def _detect_extension(ext: str) -> DetectedType | None:
    """Detect file type from extension alone."""
    if ext in _TEXT_EXTENSIONS:
        return DetectedType(
            file_type=FileType.TEXT,
            mime_type=_TEXT_EXTENSIONS[ext],
            extension=ext,
            method="extension",
        )
    if ext in _IMAGE_EXTENSIONS:
        return DetectedType(
            file_type=FileType.IMAGE,
            mime_type=_IMAGE_EXTENSIONS[ext],
            extension=ext,
            method="extension",
        )
    if ext in _VIDEO_EXTENSIONS:
        return DetectedType(
            file_type=FileType.VIDEO,
            mime_type=_VIDEO_EXTENSIONS[ext],
            extension=ext,
            method="extension",
        )
    if ext in _AUDIO_EXTENSIONS:
        return DetectedType(
            file_type=FileType.AUDIO,
            mime_type=_AUDIO_EXTENSIONS[ext],
            extension=ext,
            method="extension",
        )
    if ext in _DOCUMENT_EXTENSIONS:
        return DetectedType(
            file_type=FileType.DOCUMENT,
            mime_type=_DOCUMENT_EXTENSIONS[ext],
            extension=ext,
            method="extension",
        )
    return None


def _mime_to_filetype(mime: str) -> FileType:
    """Convert a MIME type to a FileType category."""
    major = mime.split("/")[0]
    if major == "text":
        return FileType.TEXT
    if major == "image":
        return FileType.IMAGE
    if major == "video":
        return FileType.VIDEO
    if major == "audio":
        return FileType.AUDIO
    if mime in ("application/pdf", "application/epub+zip",
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"):
        return FileType.DOCUMENT
    if mime == "application/json":
        return FileType.TEXT
    return FileType.UNKNOWN


# ---------------------------------------------------------------------------
# Ingestion routing
# ---------------------------------------------------------------------------


def ingest_file(
    canon_dir: Path,
    filename: str,
    data: bytes,
    *,
    universe_path: Path | None = None,
    user_upload: bool = True,
) -> IngestResult:
    """Ingest a file into the canon system.

    Routing rules (by provenance):
    - User uploads (``user_upload=True``): ALWAYS go to canon/sources/.
      A synthesis signal is emitted for files > SIZE_THRESHOLD.
      Small well-structured files (<=5KB) still get a signal but may
      be usable as-is.
    - Daemon-generated docs (``user_upload=False``): go to canon/ directly.

    Parameters
    ----------
    canon_dir : Path
        The canon/ directory for the universe.
    filename : str
        Safe filename (already sanitized by caller).
    data : bytes
        Raw file content.
    universe_path : Path or None
        Universe root directory (for writing signals). If None,
        signals are not written to disk.
    user_upload : bool
        True for user-provided files (routes to sources/),
        False for daemon-generated docs (routes to canon/).

    Returns
    -------
    IngestResult
    """
    from datetime import datetime, timezone

    detected = detect_file_type(filename, data)
    file_hash = hashlib.sha256(data).hexdigest()
    byte_count = len(data)

    # Load manifest
    manifest = SourceManifest.load(canon_dir)

    # Check for duplicates (same hash = skip)
    if not manifest.has_changed(filename, file_hash):
        logger.debug("File %s unchanged (same hash), skipping", filename)
        existing = manifest.get(filename)
        return IngestResult(
            filename=filename,
            routed_to=existing.routed_to if existing else "sources",
            file_type=detected.file_type,
            mime_type=detected.mime_type,
            byte_count=byte_count,
            sha256=file_hash,
            signal_emitted=False,
        )

    # Route by provenance: user uploads -> sources/, daemon docs -> canon/
    signal_emitted = False
    if user_upload:
        # ALL user uploads -> sources/
        # Containment before write: ``filename`` is documented as
        # caller-sanitized, but a symlinked ``canon/sources`` (or sources
        # entry) or a ``../`` traversal would let the write escape canon_dir.
        # Resolve under canon_dir and reject escapes before any I/O — the
        # ``sources/`` subdir is legitimate and still resolves inside.
        try:
            resolve_within_canon(
                canon_dir, f"sources/{filename}", kind="source file"
            )
        except ValueError as exc:
            logger.warning("Refusing to write source escaping canon dir: %s", exc)
            raise
        write_data_path(canon_dir / "sources" / filename, data)
        routed_to = "sources"

        # Emit synthesis signal (always for user uploads)
        if universe_path is not None:
            _emit_synthesis_signal(
                universe_path, filename, detected, byte_count,
            )
            signal_emitted = True

        logger.info(
            "Ingested %s -> sources/ (%d bytes, %s, signal=%s)",
            filename, byte_count, detected.file_type.value, signal_emitted,
        )
    else:
        # Daemon-generated -> canon/ directly
        # Containment before write: a symlinked canon entry or a ``../``
        # traversal in ``filename`` would let the write escape canon_dir.
        try:
            resolve_within_canon(
                canon_dir, filename, kind="filename"
            )
        except ValueError as exc:
            logger.warning("Refusing to write canon escaping canon dir: %s", exc)
            raise
        write_data_path(canon_dir / filename, data)
        routed_to = "canon"

        logger.info(
            "Ingested %s -> canon/ (%d bytes, %s)",
            filename, byte_count, detected.file_type.value,
        )

    # Update manifest
    now = datetime.now(timezone.utc).isoformat()
    manifest.add(ManifestEntry(
        filename=filename,
        source_path=f"{'sources/' if routed_to == 'sources' else ''}{filename}",
        file_type=detected.file_type.value,
        mime_type=detected.mime_type,
        byte_count=byte_count,
        sha256=file_hash,
        routed_to=routed_to,
        ingested_at=now,
    ))
    manifest.save(canon_dir)

    return IngestResult(
        filename=filename,
        routed_to=routed_to,
        file_type=detected.file_type,
        mime_type=detected.mime_type,
        byte_count=byte_count,
        sha256=file_hash,
        signal_emitted=signal_emitted,
    )


def _emit_synthesis_signal(
    universe_path: Path,
    filename: str,
    detected: DetectedType,
    byte_count: int,
) -> None:
    """Append a synthesize_source signal for the enrichment phase.

    Signals are stored in enrichment_signals.json. During the deprecation
    window, legacy worldbuild_signals.json is read and migrated forward.
    """
    signal = {
        "type": "synthesize_source",
        "topic": Path(filename).stem.replace("-", "_").replace(" ", "_"),
        "detail": f"New source file: {filename} ({byte_count} bytes, {detected.file_type.value})",
        "source_file": filename,
        "file_type": detected.file_type.value,
        "mime_type": detected.mime_type,
    }

    try:
        append_enrichment_signals(universe_path, [signal])
        logger.info("Emitted synthesize_source signal for %s", filename)
    except OSError:
        logger.debug("Failed to emit synthesis signal", exc_info=True)
