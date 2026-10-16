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

import importlib
import json
from pathlib import Path

import pytest

from tests.conftest import own_universe


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


def test_orient_read_canon_context_skips_symlink(tmp_path):
    from domains.fantasy_daemon.phases.orient import _read_canon_context

    canon, _ = _canon_with_escape(tmp_path)
    state = {"_universe_path": str(tmp_path)}
    out = _read_canon_context(state)
    assert "Real Topic" in out
    _assert_no_secret(out)


# =====================================================================
# writer_tools.py -- _render_canon_files
# =====================================================================


def test_writer_tools_render_canon_skips_symlink(tmp_path):
    from domains.fantasy_daemon.phases.writer_tools import _render_canon_files

    canon, _ = _canon_with_escape(tmp_path)
    state = {"_universe_path": str(tmp_path)}
    out = _render_canon_files(state)
    assert "Real Topic" in out
    _assert_no_secret(out)


# =====================================================================
# plan.py -- _try_constraint_synthesis (canon docs as source)
# =====================================================================


def test_plan_constraint_synthesis_skips_symlink(tmp_path, monkeypatch):
    # `phases/__init__.py` re-exports the `plan` FUNCTION, which shadows the
    # `plan` submodule for `from ... import plan`. Reach the module by its full
    # dotted name so this resolves to the module regardless of that re-export.
    plan_mod = importlib.import_module("domains.fantasy_daemon.phases.plan")

    canon, _ = _canon_with_escape(tmp_path)
    captured: dict[str, object] = {}

    class _FakeSurface:
        readiness_score = 0.0

    class _FakeSynth:
        def process(self, premise, source_docs):
            captured["source_docs"] = source_docs or []
            return _FakeSurface()

    monkeypatch.setattr(
        "tinyassets.constraints.constraint_synthesis.ConstraintSynthesis",
        _FakeSynth,
    )
    state = {
        "_universe_path": str(tmp_path),
        "workflow_instructions": {"premise": "Epic fantasy."},
    }
    plan_mod._try_constraint_synthesis(state)
    docs = captured.get("source_docs", [])
    joined = "\n".join(docs)
    assert "Real Topic" in joined
    _assert_no_secret(joined)


# =====================================================================
# commit.py -- _read_canon_excerpts
# =====================================================================


def test_commit_read_canon_excerpts_skips_symlink(tmp_path):
    from domains.fantasy_daemon.phases.commit import _read_canon_excerpts

    canon, _ = _canon_with_escape(tmp_path)
    out = _read_canon_excerpts(canon)
    assert "Real Topic" in out
    _assert_no_secret(out)


# =====================================================================
# reflect.py -- _collect_reviewable_canon (enumeration)
#            -- rewrite path rejects LLM ../ filename
# =====================================================================


def test_reflect_collect_reviewable_skips_symlink(tmp_path):
    from domains.fantasy_daemon.phases.reflect import _collect_reviewable_canon

    canon, _ = _canon_with_escape(tmp_path)
    result = _collect_reviewable_canon(canon, signal_topics=None)
    assert "real_topic.md" in result
    assert "real_topic_evil.md" not in result
    for content in result.values():
        _assert_no_secret(content)


def test_reflect_rewrite_rejects_llm_traversal_filename(tmp_path, monkeypatch):
    """An LLM-emitted ``../`` rewrite target must not clobber an external file."""
    import importlib

    # The package re-exports the ``reflect`` *function*, so import the module
    # by its full dotted path to monkeypatch its internals.
    reflect_mod = importlib.import_module(
        "domains.fantasy_daemon.phases.reflect"
    )

    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "good.md").write_text("# Good\n\nbody", encoding="utf-8")
    victim = tmp_path / "victim.md"
    victim.write_text("ORIGINAL", encoding="utf-8")

    monkeypatch.setattr(reflect_mod, "_read_premise", lambda *a, **k: "Premise.")
    monkeypatch.setattr(reflect_mod, "_extract_signal_topics", lambda *a, **k: None)
    # Force an LLM "issue" pointing at a traversal path and a non-empty rewrite.
    monkeypatch.setattr(
        reflect_mod,
        "_evaluate_canon",
        lambda *a, **k: [
            {"filename": "../victim.md", "reason": "x", "severity": 10}
        ],
    )
    monkeypatch.setattr(
        reflect_mod, "_rewrite_canon_file", lambda *a, **k: "CLOBBERED"
    )
    monkeypatch.setattr(reflect_mod, "_model_tier", lambda *a, **k: 99)
    monkeypatch.setattr(reflect_mod, "_rewrite_justified", lambda *a, **k: True)

    state = {"_universe_path": str(tmp_path)}
    summary = reflect_mod._review_canon_quality(state)
    # The traversal target was skipped, not written.
    assert "../victim.md" not in summary["files_rewritten"]
    assert victim.read_text(encoding="utf-8") == "ORIGINAL"


# =====================================================================
# worldbuild.py -- _scan_existing_canon + premise-context reader
# =====================================================================


def test_worldbuild_scan_existing_skips_symlink(tmp_path):
    from domains.fantasy_daemon.phases.worldbuild import _scan_existing_canon

    canon, _ = _canon_with_escape(tmp_path)
    slugs = _scan_existing_canon(canon)
    assert "real_topic" in slugs
    # The escaping symlink contributes no slug.
    assert "real_topic_evil" not in slugs


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
    own_universe(tmp_path, uid)
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


def test_work_targets_manifest_read_rejects_symlink(tmp_path):
    """A symlinked ``canon/.manifest.json`` pointing outside canon must not be
    read by the synthesis-signal rehydration path."""
    from tinyassets import work_targets

    universe_dir = tmp_path / "u1"
    own_universe(tmp_path, "u1")
    canon = universe_dir / "canon"
    canon.mkdir(parents=True)
    secret_manifest = tmp_path / "secret_manifest.json"
    secret_manifest.write_text(
        json.dumps({"leak.txt": {"routed_to": "sources"}}), encoding="utf-8"
    )
    try:
        (canon / ".manifest.json").symlink_to(secret_manifest)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")

    raw_signals: list = []
    out = work_targets._rehydrate_missing_synthesis_signals(universe_dir, raw_signals)
    # Containment escape -> treated as no manifest -> no signals rehydrated.
    assert out == []


def test_work_targets_manifest_read_legit_still_works(tmp_path):
    """A real ``canon/.manifest.json`` still rehydrates a synthesis signal."""
    from tinyassets import work_targets

    universe_dir = tmp_path / "u1"
    own_universe(tmp_path, "u1")
    sources = universe_dir / "canon" / "sources"
    sources.mkdir(parents=True)
    (sources / "doc.txt").write_text("body", encoding="utf-8")
    manifest = {
        "doc.txt": {
            "filename": "doc.txt",
            "routed_to": "sources",
            "source_path": "sources/doc.txt",
            "file_type": "text",
            "byte_count": 4,
        }
    }
    (universe_dir / "canon" / ".manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    out = work_targets._rehydrate_missing_synthesis_signals(universe_dir, [])
    topics = {s.get("source_file") for s in out}
    assert "doc.txt" in topics


def test_work_targets_needs_synthesis_rejects_traversal_source_path(tmp_path):
    """A manifest ``source_path`` with a traversal must not probe outside canon
    (runs live on Windows -- no symlink needed)."""
    from tinyassets import work_targets

    universe_dir = tmp_path / "u1"
    own_universe(tmp_path, "u1")
    (universe_dir / "canon").mkdir(parents=True)
    (tmp_path / "outside.txt").write_text("OUTSIDE", encoding="utf-8")
    entry = {
        "routed_to": "sources",
        # Collapses to tmp_path/outside.txt -> outside canon -> rejected.
        "source_path": "sources/../../outside.txt",
    }
    assert work_targets._manifest_entry_needs_synthesis(
        universe_dir, "outside.txt", entry
    ) is False


def test_work_targets_needs_synthesis_legit_source_path(tmp_path):
    """A real ``sources/<file>`` source_path is detected as present."""
    from tinyassets import work_targets

    universe_dir = tmp_path / "u1"
    own_universe(tmp_path, "u1")
    sources = universe_dir / "canon" / "sources"
    sources.mkdir(parents=True)
    (sources / "doc.txt").write_text("body", encoding="utf-8")
    entry = {"routed_to": "sources", "source_path": "sources/doc.txt"}
    assert work_targets._manifest_entry_needs_synthesis(
        universe_dir, "doc.txt", entry
    ) is True


# =====================================================================
# select_task.py -- _count_canon_files enumeration
# =====================================================================


def test_select_task_count_canon_skips_symlink(tmp_path):
    """``_count_canon_files`` must not count a symlinked ``.md`` escaping canon."""
    from domains.fantasy_daemon.phases.select_task import _count_canon_files

    canon, _ = _canon_with_escape(tmp_path)
    state = {"_universe_path": str(tmp_path)}
    # One legit .md (``real_topic.md``); the escaping symlink is skipped.
    assert _count_canon_files(state) == 1


def test_select_task_count_canon_legit_only(tmp_path):
    """``_count_canon_files`` counts only legit ``.md`` files (Windows-live,
    no symlink). Non-md and dotfiles are excluded; the count routes through the
    guarded chokepoint enumeration rather than a raw ``iterdir`` scan."""
    from domains.fantasy_daemon.phases.select_task import _count_canon_files

    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "a.md").write_text("a", encoding="utf-8")
    (canon / "b.md").write_text("b", encoding="utf-8")
    (canon / "notes.txt").write_text("x", encoding="utf-8")
    (canon / ".manifest.json").write_text("{}", encoding="utf-8")
    state = {"_universe_path": str(tmp_path)}
    assert _count_canon_files(state) == 2
