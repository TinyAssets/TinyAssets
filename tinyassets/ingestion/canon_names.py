"""Sandbox-safe canon filename helpers."""

from __future__ import annotations

import re
from pathlib import Path

_CANON_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def resolve_within_canon(canon_dir: Path, name: str, kind: str = "path") -> Path:
    """Resolve ``name`` inside ``canon_dir`` and enforce containment.

    Returns the resolved absolute path. ``.resolve()`` follows symlinks and
    collapses ``..`` segments, so a symlinked canon file/marker/source or a
    ``../`` traversal that points outside ``canon_dir`` is rejected here before
    any read or write. This is the single containment primitive reused by every
    canon I/O path (new files, contradiction / expansion overwrites, provenance
    markers, synthesis source reads, manifest reads/writes, and KG/premise
    canon reads). Legitimate subdirectories (e.g. ``sources/foo.txt``) stay
    allowed because they still resolve under ``canon_root``. ``kind`` only
    shapes the error message ("filename", "marker", "source file") for
    debuggability.
    """
    # A linked canon ROOT moves the containment root along with the target:
    # ``canon -> /data/<other>/canon`` (a workflow provider jail binds the
    # universe read-write and allows ``symlink``) would make every name
    # "contained" in another universe's canon. Refused, not resolved.
    if canon_dir.is_symlink():
        raise ValueError(f"canon directory is a link; refusing canon {kind} {name!r}")
    canon_root = canon_dir.resolve()
    resolved = (canon_dir / name).resolve()
    if not resolved.is_relative_to(canon_root):
        raise ValueError(f"canon {kind} escapes canon directory: {name!r}")
    return resolved


def safe_canon_slug(value: str) -> str:
    """Return a sandbox-safe canon topic slug.

    LLM-synthesized topic names are not trusted path components. Canon topic
    slugs are restricted to lowercase ASCII letters, digits, and underscores.
    """
    slug = _CANON_SLUG_RE.sub("_", str(value).lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("canon topic must resolve to a non-empty safe slug")
    return slug


def safe_canon_filename(value: str) -> str:
    """Return a safe ``.md`` filename for a canon topic or filename."""
    raw = str(value).strip()
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    return f"{safe_canon_slug(raw)}.md"
