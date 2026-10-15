"""Phase 7.3 H1 — backend factory + dirty-file response shape.

Covers:

- ``tinyassets.catalog.get_backend`` env-var selection + auto-probe.
- Memoization + ``invalidate_backend_cache`` contract.
- ``_format_dirty_file_conflict`` response shape.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tinyassets.catalog import (
    DirtyFileError,
    SqliteCachedBackend,
    SqliteOnlyBackend,
    get_backend,
    invalidate_backend_cache,
)


@pytest.fixture(autouse=True)
def _reset_factory_state(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with a clean cache + no backend env override."""
    invalidate_backend_cache()
    monkeypatch.delenv("TINYASSETS_STORAGE_BACKEND", raising=False)
    yield
    invalidate_backend_cache()


def _init_git_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main"], cwd=str(path),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"], cwd=str(path),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Bot"], cwd=str(path),
        check=True, capture_output=True,
    )


@pytest.fixture
def bound_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A repository path inside a command center whose owner box session is bound.

    Owner git runs only in an admitted owner box, so the auto-probe sees a
    repository only there.
    """
    from tinyassets.control_plane import binding as binding_module

    data_root = tmp_path / "data"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
    center = data_root / "cc-catalog"
    center.mkdir(parents=True)
    binding = binding_module.file_binding(center)
    assert binding is not None, "the owner box double binds a test center"
    repo = center / "repo"
    repo.mkdir()
    with binding_module.use_binding(binding):
        yield repo


# ─── get_backend — env-var selection ─────────────────────────────────────


def test_env_var_forces_sqlite_only(tmp_path, monkeypatch):
    """Env var wins even when we're inside a real git repo."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    _init_git_repo(tmp_path)
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_only")

    backend = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(backend, SqliteOnlyBackend)


def test_env_var_forces_sqlite_cached(tmp_path, monkeypatch):
    """Cached variant can be forced even with git disabled."""
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_cached")

    backend = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(backend, SqliteCachedBackend)


def test_env_var_unknown_value_falls_through_to_autoprobe(
    tmp_path, monkeypatch,
):
    """A typo'd env var shouldn't silently pick the wrong backend."""
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "typo")
    # Not a git repo → should get SqliteOnly via auto-probe
    backend = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(backend, SqliteOnlyBackend)


# ─── get_backend — auto-probe ────────────────────────────────────────────


def test_autoprobe_chooses_cached_when_git_enabled(tmp_path, bound_repo):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    _init_git_repo(bound_repo)
    backend = get_backend(tmp_path / "output", repo_root=bound_repo)
    assert isinstance(backend, SqliteCachedBackend)


def test_autoprobe_falls_back_to_sqlite_only_when_git_disabled(tmp_path):
    backend = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(backend, SqliteOnlyBackend)


# ─── Memoization ─────────────────────────────────────────────────────────


def test_get_backend_memoizes(tmp_path):
    b1 = get_backend(tmp_path / "output", repo_root=tmp_path)
    b2 = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert b1 is b2, "subsequent calls must return the SAME instance"


def test_invalidate_backend_cache_reprobes(tmp_path, monkeypatch):
    """After invalidate, env-var change must take effect."""
    b_first = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(b_first, SqliteOnlyBackend)

    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_cached")
    # Without invalidate, still cached as SqliteOnly
    assert get_backend(tmp_path / "output", repo_root=tmp_path) is b_first

    invalidate_backend_cache()
    b_second = get_backend(tmp_path / "output", repo_root=tmp_path)
    assert isinstance(b_second, SqliteCachedBackend)
    assert b_second is not b_first


def test_invalidate_also_clears_git_bridge_cache(tmp_path, bound_repo):
    """So a fresh get_backend() picks up a newly-initialized repo."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    # First call: no repo → sqlite_only + git_bridge.is_enabled cached False
    b_first = get_backend(tmp_path / "output", repo_root=bound_repo)
    assert isinstance(b_first, SqliteOnlyBackend)

    _init_git_repo(bound_repo)
    invalidate_backend_cache()
    # After invalidate, git_bridge probes fresh → cached
    b_second = get_backend(tmp_path / "output", repo_root=bound_repo)
    assert isinstance(b_second, SqliteCachedBackend)


# ─── _format_dirty_file_conflict ─────────────────────────────────────────


def test_format_dirty_file_conflict_shape():
    # Reload universe_server to ensure the helper is imported fresh
    # (other tests in the suite reload this module; do it once here).
    from tinyassets.api import engine_helpers as eh
    importlib.reload(eh)

    paths = [Path("branches/foo.yaml"), Path("nodes/foo/n1.yaml")]
    exc = DirtyFileError(paths)
    payload = eh._format_dirty_file_conflict(exc)

    assert payload["status"] == "local_edit_conflict"
    assert payload["conflicting_path"] == str(paths[0])
    assert payload["all_conflicts"] == [str(p) for p in paths]
    assert payload["options"] == [
        "pass force=True to overwrite",
        "commit or stash local edits first",
    ]


def test_format_dirty_file_conflict_handles_empty_paths():
    """DirtyFileError with no paths shouldn't crash the formatter."""
    from tinyassets.api import engine_helpers as eh
    exc = DirtyFileError([])
    payload = eh._format_dirty_file_conflict(exc)
    assert payload["status"] == "local_edit_conflict"
    assert payload["conflicting_path"] == ""
    assert payload["all_conflicts"] == []
