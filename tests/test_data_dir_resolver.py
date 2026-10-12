"""Tests for tinyassets.storage.data_dir — canonical TINYASSETS_DATA_DIR resolver.

Per 2026-04-20-selfhost-uptime-migration.md (archived in git) Row B.
The 2026-04-19 P0 had a container CWD-drift class: pre-Row-B, the daemon
wrote to `/app/output` (CWD-relative) rather than `/data` (bind-mount).
This resolver fixes that by refusing CWD-relative defaults and rooting
every fallback at either an explicit env var or a platform-appropriate
absolute path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Why these two tests are Windows-only rather than "simulated" with a faked
# os.name: monkeypatching os.name to "nt" on POSIX poisons pathlib globally.
# Path() then dispatches to WindowsPath, which raises NotImplementedError on
# Linux — and it raises again inside pytest's own failure reporting
# (`Path(os.getcwd())` in nodes._repr_failure_py), which turns an ordinary test
# failure into an xdist INTERNALERROR that kills the worker and aborts the whole
# session. That is not hypothetical: it silently stopped this entire file from
# running in CI while the summary still reported thousands of passes.
_NT_FAKE_UNSAFE = (
    "Must run on real Windows: faking os.name='nt' on POSIX poisons pathlib "
    "globally and crashes the pytest worker mid-report, silently dropping "
    "unrelated tests from the run."
)


@pytest.fixture
def clean_env(monkeypatch):
    """Strip all env vars the resolver reads so tests start from a known state."""
    for name in ("TINYASSETS_DATA_DIR",):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---- precedence -----------------------------------------------------------


def test_workflow_data_dir_takes_precedence(clean_env, tmp_path):
    """Explicit TINYASSETS_DATA_DIR wins over the platform default."""
    from tinyassets.storage import data_dir

    target = tmp_path / "canonical"
    clean_env.setenv("TINYASSETS_DATA_DIR", str(target))

    result = data_dir()
    assert result == target.resolve()


# ---- platform defaults ----------------------------------------------------


def test_default_is_absolute(clean_env):
    """Default path MUST be absolute (no CWD-relative drift)."""
    from tinyassets.storage import data_dir
    assert data_dir().is_absolute()


def test_explicit_env_is_absolute(clean_env, tmp_path):
    """Explicit TINYASSETS_DATA_DIR is always resolved to absolute."""
    from tinyassets.storage import data_dir

    # Set a relative-looking path; resolver must absolute-ize it.
    clean_env.setenv("TINYASSETS_DATA_DIR", "relative/path")
    result = data_dir()
    assert result.is_absolute()


def test_expanduser_honored(clean_env):
    """Tilde expansion works for shell-style paths."""
    from tinyassets.storage import data_dir

    clean_env.setenv("TINYASSETS_DATA_DIR", "~/workflow-test")
    result = data_dir()
    expected = (Path.home() / "workflow-test").resolve()
    assert result == expected


# ---- platform-appropriate default path ------------------------------------


def test_linux_mac_default_dot_tinyassets_under_home(clean_env):
    """On non-Windows, default is ~/.tinyassets."""
    from tinyassets.storage import data_dir

    if os.name == "nt":
        pytest.skip("test targets non-Windows default branch")

    result = data_dir()
    assert result == (Path.home() / ".tinyassets").resolve()


@pytest.mark.skipif(os.name != "nt", reason=_NT_FAKE_UNSAFE)
def test_windows_default_uses_appdata(clean_env, monkeypatch):
    """On Windows with APPDATA, default is %APPDATA%\\TinyAssets."""
    from tinyassets.storage import data_dir

    monkeypatch.setenv("APPDATA", "/fake/appdata")

    result = data_dir()
    assert result == Path("/fake/appdata/TinyAssets").resolve()


@pytest.mark.skipif(os.name != "nt", reason=_NT_FAKE_UNSAFE)
def test_windows_default_without_appdata_falls_back(clean_env, monkeypatch):
    """Windows without APPDATA uses Path.home() / AppData / Roaming / TinyAssets."""
    from tinyassets.storage import data_dir

    monkeypatch.delenv("APPDATA", raising=False)

    result = data_dir()
    expected = (Path.home() / "AppData" / "Roaming" / "TinyAssets").resolve()
    assert result == expected


# ---- empty-string robustness ----------------------------------------------


def test_empty_string_env_treated_as_unset(clean_env):
    """TINYASSETS_DATA_DIR="" must not resolve to CWD — fall through to default."""
    from tinyassets.storage import data_dir

    clean_env.setenv("TINYASSETS_DATA_DIR", "")
    result = data_dir()
    assert result.is_absolute()
    # Should NOT be CWD — the CWD-drift bug we're guarding against.
    assert result != Path("").resolve()


def test_whitespace_only_env_treated_as_unset(clean_env):
    """Whitespace-only TINYASSETS_DATA_DIR doesn't resolve to the CWD."""
    from tinyassets.storage import data_dir

    clean_env.setenv("TINYASSETS_DATA_DIR", "   ")
    result = data_dir()
    assert result.is_absolute()


# ---- integration with the server entry points -----------------------------


def test_universe_server_base_path_uses_data_dir(clean_env, tmp_path):
    """tinyassets.api.helpers._base_path() delegates to data_dir()."""
    from tinyassets.api.helpers import _base_path

    target = tmp_path / "universe-server-root"
    clean_env.setenv("TINYASSETS_DATA_DIR", str(target))

    assert _base_path() == target.resolve()


def test_mcp_server_universe_dir_uses_data_dir(clean_env, tmp_path):
    """tinyassets.mcp_server._universe_dir() roots at data_dir() / default-universe."""
    from tinyassets.mcp_server import _universe_dir

    target = tmp_path / "mcp-root"
    clean_env.setenv("TINYASSETS_DATA_DIR", str(target))
    # TINYASSETS_UNIVERSE must be unset so we hit the default branch.
    clean_env.delenv("TINYASSETS_UNIVERSE", raising=False)

    assert _universe_dir() == (target / "default-universe").resolve()


def test_mcp_server_workflow_universe_overrides(clean_env, tmp_path):
    """TINYASSETS_UNIVERSE env override still works (per-universe explicit path)."""
    from tinyassets.mcp_server import _universe_dir

    override = tmp_path / "explicit-universe"
    clean_env.setenv("TINYASSETS_UNIVERSE", str(override))
    clean_env.setenv("TINYASSETS_DATA_DIR", "/should/be/ignored")

    assert _universe_dir() == override.resolve()


# ---- regression guards ---------------------------------------------------


def test_no_cwd_drift_when_env_unset(clean_env, tmp_path, monkeypatch):
    """The 2026-04-19 P0 class — resolver must not return a CWD-relative path.

    Even if the CWD changes between resolver calls (e.g., process
    chdir after startup), the returned path must be stable.
    """
    from tinyassets.storage import data_dir

    monkeypatch.chdir(tmp_path)
    first = data_dir()
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    second = data_dir()

    assert first == second, "data_dir() returned a CWD-relative path"
    assert first.is_absolute()


def test_data_dir_exported_from_workflow_storage(clean_env):
    """data_dir is reachable via `from tinyassets.storage import data_dir`."""
    import tinyassets.storage

    assert "data_dir" in tinyassets.storage.__all__
    assert callable(tinyassets.storage.data_dir)
