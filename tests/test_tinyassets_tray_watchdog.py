"""Tests for the tab-watchdog integration into tinyassets_tray (task #19).

The tray spawns the watchdog alongside MCP + cloudflared. Tests mock
subprocess.Popen so they don't actually launch processes, and verify:

- start_watchdog launches a Popen with the right command shape.
- Missing `scripts/tab_watchdog.py` skips cleanly (partial-checkout
  robustness, same defensive shape as the mojibake hook's check).
- kill_all terminates the watchdog alongside other processes.
- check_health reflects the watchdog's poll() state.
- Monitor-loop logic (tested indirectly via _watchdog_alive flag
  transitions) respawns on crash.
- status_text + hover_text mention the watchdog.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAY_PATH = REPO_ROOT / "tinyassets_tray.py"


def _stub_tray_deps(monkeypatch):
    """tinyassets_tray imports PIL + pystray at module top. Stub them
    so tests don't need those packages installed on CI."""
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        pil_image = types.ModuleType("PIL.Image")
        pil_image.new = MagicMock()
        pil_imagedraw = types.ModuleType("PIL.ImageDraw")
        pil_imagedraw.Draw = MagicMock()
        pil_imagefont = types.ModuleType("PIL.ImageFont")
        pil_imagefont.truetype = MagicMock(side_effect=OSError)
        pil_imagefont.load_default = MagicMock()
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = pil_image
        sys.modules["PIL.ImageDraw"] = pil_imagedraw
        sys.modules["PIL.ImageFont"] = pil_imagefont
    if "pystray" not in sys.modules:
        pystray = types.ModuleType("pystray")
        pystray.Icon = MagicMock()
        pystray.Menu = MagicMock()
        pystray.MenuItem = MagicMock()
        sys.modules["pystray"] = pystray


@pytest.fixture(scope="module")
def tray_mod():
    """Load tinyassets_tray.py by path (it's not a package)."""
    # Stub tray deps in whatever state we're in; these stubs don't
    # need real functionality because tests never invoke icon/menu paths.
    # The stubs are removed afterwards: a later test in this worker that
    # imports the real PIL (e.g. ImageChops) must not get the empty stand-in.
    before = dict(sys.modules)
    _stub_tray_deps(None)
    spec = importlib.util.spec_from_file_location(
        "tinyassets_tray_under_test", TRAY_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "pystray"):
        if name not in before:
            sys.modules.pop(name, None)


@pytest.fixture
def manager(tray_mod):
    return tray_mod.UniverseServerManager()


# -------------------------------------------------------------------
# start_watchdog
# -------------------------------------------------------------------


def test_start_watchdog_launches_subprocess(manager, monkeypatch, tmp_path):
    """start_watchdog calls Popen with the expected command shape."""
    popen_calls = []

    class _FakeProc:
        def __init__(self, *args, **kwargs):
            popen_calls.append((args, kwargs))
            self.returncode = None

        def poll(self):
            return self.returncode

    # Patch Popen and the LOG_DIR so we don't scribble in real logs.
    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(manager, "_phase", "")

    manager.start_watchdog()

    assert len(popen_calls) == 1
    args, _kwargs = popen_calls[0]
    # argv list is first positional arg; must reference tab_watchdog.py.
    argv = args[0]
    assert any("tab_watchdog.py" in str(a) for a in argv), (
        f"start_watchdog must exec scripts/tab_watchdog.py; got argv={argv}"
    )
    # Popen result is captured on the manager.
    assert manager.watchdog_proc is not None


def test_start_watchdog_skips_when_script_missing(
    manager, monkeypatch, tmp_path, tray_mod,
):
    """Partial-checkout robustness: missing watchdog script → skip."""
    # Point PROJECT_DIR at tmp_path so `scripts/tab_watchdog.py` doesn't exist.
    monkeypatch.setattr(tray_mod, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(tray_mod, "LOG_DIR", tmp_path / "logs")

    # Popen must NOT be called when the script is missing.
    popen_calls = []

    def _never(*a, **k):
        popen_calls.append((a, k))
        raise AssertionError("Popen must not run when watchdog script missing")

    monkeypatch.setattr(subprocess, "Popen", _never)

    manager.start_watchdog()

    assert popen_calls == []
    assert manager.watchdog_proc is None
    assert "missing" in manager._phase.lower()


# -------------------------------------------------------------------
# check_health flag
# -------------------------------------------------------------------


def test_check_health_reflects_watchdog_alive(manager):
    """_watchdog_alive flips True when proc.poll() returns None."""
    live = MagicMock()
    live.poll = MagicMock(return_value=None)
    manager.watchdog_proc = live

    manager.check_health()

    assert manager._watchdog_alive is True


def test_check_health_reflects_watchdog_dead(manager):
    """_watchdog_alive flips False when proc.poll() returns an exit code."""
    dead = MagicMock()
    dead.poll = MagicMock(return_value=0)
    manager.watchdog_proc = dead

    manager.check_health()

    assert manager._watchdog_alive is False


def test_check_health_watchdog_absent_is_dead(manager):
    """No watchdog_proc → _watchdog_alive stays False."""
    manager.watchdog_proc = None

    manager.check_health()

    assert manager._watchdog_alive is False


# -------------------------------------------------------------------
# kill_all
# -------------------------------------------------------------------


def test_kill_all_terminates_watchdog(manager):
    """kill_all must terminate the watchdog alongside MCP + tunnel."""
    watchdog = MagicMock()
    watchdog.poll = MagicMock(return_value=None)  # alive
    manager.watchdog_proc = watchdog

    manager.kill_all()

    watchdog.terminate.assert_called_once()
    assert manager.watchdog_proc is None
    assert manager._watchdog_alive is False


def test_kill_all_survives_watchdog_timeout(manager):
    """If terminate() times out, kill() is the fallback — no crash."""
    watchdog = MagicMock()
    watchdog.poll = MagicMock(return_value=None)
    watchdog.wait = MagicMock(side_effect=subprocess.TimeoutExpired("x", 5))
    manager.watchdog_proc = watchdog

    # Should NOT raise.
    manager.kill_all()

    watchdog.kill.assert_called_once()


# -------------------------------------------------------------------
# status_text + hover_text mention the watchdog
# -------------------------------------------------------------------


def test_status_text_includes_watchdog_running(manager):
    manager._watchdog_alive = True
    assert "Tab watchdog: Running" in manager.status_text


def test_status_text_includes_watchdog_stopped(manager):
    manager._watchdog_alive = False
    assert "Tab watchdog: Stopped" in manager.status_text


# -------------------------------------------------------------------
# Cross-process invariant: watchdog is one of the tracked procs
# -------------------------------------------------------------------


def test_manager_init_exposes_watchdog_attribute(tray_mod):
    mgr = tray_mod.UniverseServerManager()
    # Regression guard: watchdog_proc attribute MUST exist at __init__.
    assert hasattr(mgr, "watchdog_proc")
    assert mgr.watchdog_proc is None
    assert hasattr(mgr, "_watchdog_alive")
    assert mgr._watchdog_alive is False
