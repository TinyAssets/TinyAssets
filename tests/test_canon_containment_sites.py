"""Per-site canon-containment tests for the guarded call sites.

For every file Codex flagged (plus the additional sites found during the
end-to-end audit), prove that a symlinked ``.md`` whose target lives outside
the canon directory is NOT read/enumerated, while a legitimate sibling file
still is. Symlink-creation skips on platforms that forbid it (Windows without
Developer Mode); the containment primitive itself is exercised live in
``test_canon_io.py`` via ``.resolve()``-lands-outside checks.

Each site is verified through its real public/private function so a future
refactor that bypasses the chokepoint regresses a test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _canon_with_escape(tmp_path: Path) -> tuple[Path, Path]:
    """Build canon/ with one legit file and one symlink to an external secret.

    Returns ``(canon_dir, secret_path)``. Skips if symlinks are unavailable.

    Both bodies keep their prose paragraph at or above 50 characters. Some
    readers drop shorter paragraphs as noise (``raptor._MIN_PARAGRAPH_LEN``),
    which would silently empty the legitimate side of the comparison and leave
    the test asserting containment against nothing at all.
    """
    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "real_topic.md").write_text(
        "# Real Topic\n\nLegitimate canon content lives here, and is long "
        "enough to survive paragraph filtering.",
        encoding="utf-8",
    )
    secret = tmp_path / "SECRET.md"
    secret.write_text(
        "# SECRET\n\nThis external content must never be read into canon.",
        encoding="utf-8",
    )
    try:
        (canon / "real_topic_evil.md").symlink_to(secret)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")
    return canon, secret


def _assert_no_secret(text: str) -> None:
    assert "SECRET" not in text
    assert "must never be read" not in text


# =====================================================================
# orient.py -- _read_canon_context
# =====================================================================




# =====================================================================
# writer_tools.py -- _render_canon_files
# =====================================================================




# =====================================================================
# plan.py -- _try_constraint_synthesis (canon docs as source)
# =====================================================================




# =====================================================================
# commit.py -- _read_canon_excerpts
# =====================================================================




# =====================================================================
# reflect.py -- _collect_reviewable_canon (enumeration)
#            -- rewrite path rejects LLM ../ filename
# =====================================================================






# =====================================================================
# worldbuild.py -- _scan_existing_canon + premise-context reader
# =====================================================================




# =====================================================================
# raptor.py -- _read_canon_paragraphs
# =====================================================================


def test_raptor_read_canon_paragraphs_skips_symlink(tmp_path):
    from tinyassets.knowledge.raptor import _read_canon_paragraphs

    canon, _ = _canon_with_escape(tmp_path)
    paragraphs = _read_canon_paragraphs(str(canon))
    joined = "\n".join(paragraphs)
    assert "Legitimate canon content" in joined
    _assert_no_secret(joined)


# =====================================================================
# memory/ingestion.py -- ProgressiveIngestor._find_canon_files (recursive)
# =====================================================================


def test_memory_ingestion_find_files_skips_symlinked_subdir(tmp_path):
    from tinyassets.memory.ingestion import ProgressiveIngestor

    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "real.md").write_text("real canon", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("LEAK", encoding="utf-8")
    try:
        (canon / "linkdir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    ingestor = ProgressiveIngestor(canon, universe_id="u1")
    files = ingestor._find_canon_files()
    names = {p.name for p in files}
    assert "real.md" in names
    # The symlinked subdir's file must not be surfaced by the recursive walk.
    contents = {p.read_text(encoding="utf-8", errors="replace") for p in files}
    assert "LEAK" not in contents


# =====================================================================
# universe.py API -- read_canon / list_canon / read_source
# =====================================================================


def _setup_universe(tmp_path, monkeypatch):
    """Point the universe API base path at tmp_path and return a universe id."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "test_universe"
    udir = tmp_path / uid
    (udir / "canon").mkdir(parents=True)
    return uid, udir


def test_universe_read_canon_rejects_symlink(tmp_path, monkeypatch):
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    canon = udir / "canon"
    (canon / "good.md").write_text("good content", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("SECRET CONTENT", encoding="utf-8")
    try:
        (canon / "evil.md").symlink_to(secret)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    # Legit read works.
    good = json.loads(uni._action_read_canon(universe_id=uid, filename="good.md"))
    assert good["content"] == "good content"
    # Symlinked canon file is rejected (not read).
    evil = json.loads(uni._action_read_canon(universe_id=uid, filename="evil.md"))
    assert "error" in evil
    assert "SECRET CONTENT" not in json.dumps(evil)


def test_universe_list_canon_skips_symlink(tmp_path, monkeypatch):
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    canon = udir / "canon"
    (canon / "good.md").write_text("good", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("SECRET", encoding="utf-8")
    try:
        (canon / "evil.md").symlink_to(secret)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    listing = json.loads(uni._action_list_canon(universe_id=uid))
    names = {f["filename"] for f in listing["canon_files"]}
    assert "good.md" in names
    assert "evil.md" not in names


def test_universe_read_source_rejects_traversal(tmp_path, monkeypatch):
    """Traversal filename to read_source is rejected (runs live, no symlink)."""
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    (udir / "canon" / "sources").mkdir(parents=True)
    (tmp_path / "outside.txt").write_text("OUTSIDE", encoding="utf-8")
    # ``..`` is stripped by Path(...).name first, but assert no leak regardless.
    res = json.loads(uni._action_read_source(universe_id=uid, filename="../outside.txt"))
    assert "OUTSIDE" not in json.dumps(res)


def test_universe_read_source_legit_file_still_works(tmp_path, monkeypatch):
    """A real ``canon/sources/<name>`` file must still be readable after the
    containment-root fix (no regression of the happy path)."""
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    sources = udir / "canon" / "sources"
    sources.mkdir(parents=True)
    (sources / "doc.txt").write_text("REAL SOURCE BODY", encoding="utf-8")

    res = json.loads(uni._action_read_source(universe_id=uid, filename="doc.txt"))
    assert res.get("content") == "REAL SOURCE BODY"


def test_universe_read_source_rejects_symlinked_sources_dir(tmp_path, monkeypatch):
    """A *symlinked* ``canon/sources`` directory must not become a trusted root.

    Even when ``read_source`` is asked for a plain basename that exists at the
    symlink target outside canon, containment is measured against the canon
    ROOT, so the read is rejected and no external content leaks.
    """
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    canon = udir / "canon"
    outside = tmp_path / "outside_sources"
    outside.mkdir()
    (outside / "leak.txt").write_text("SECRET LEAK", encoding="utf-8")
    try:
        (canon / "sources").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    res = json.loads(uni._action_read_source(universe_id=uid, filename="leak.txt"))
    assert "error" in res
    assert "SECRET LEAK" not in json.dumps(res)


def test_universe_list_sources_skips_symlinked_sources_dir(tmp_path, monkeypatch):
    """A symlinked ``canon/sources`` dir must surface no source files."""
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    canon = udir / "canon"
    outside = tmp_path / "outside_sources"
    outside.mkdir()
    (outside / "leak.txt").write_text("SECRET LEAK", encoding="utf-8")
    try:
        (canon / "sources").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    listing = json.loads(uni._action_list_sources(universe_id=uid))
    assert "SECRET LEAK" not in json.dumps(listing)
    assert listing.get("source_files", []) == []


def test_universe_list_sources_legit_files_still_listed(tmp_path, monkeypatch):
    """Real ``canon/sources/*`` files must still be enumerated (no regression)."""
    from tinyassets.api import helpers
    from tinyassets.api import universe as uni

    uid, udir = _setup_universe(tmp_path, monkeypatch)
    monkeypatch.setattr(helpers, "_default_universe", lambda: uid)
    monkeypatch.setattr(uni, "_universe_dir", lambda _id: udir)

    sources = udir / "canon" / "sources"
    sources.mkdir(parents=True)
    (sources / "one.txt").write_text("one", encoding="utf-8")
    (sources / "two.txt").write_text("two", encoding="utf-8")

    listing = json.loads(uni._action_list_sources(universe_id=uid))
    names = {f["filename"] for f in listing["source_files"]}
    assert names == {"one.txt", "two.txt"}


# =====================================================================
# work_targets.py -- manifest read + manifest-supplied source_path is_file
# =====================================================================










# =====================================================================
# select_task.py -- _count_canon_files enumeration
# =====================================================================




