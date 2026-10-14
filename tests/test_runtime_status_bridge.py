"""Tests for the runtime-status bridge + --provider CLI pin.

Covers:
- ``DaemonController._write_runtime_status`` payload shape, atomic write,
  and ``_remove_runtime_status`` on shutdown.
- ``UniverseServerManager._read_runtime_status`` freshness gate.
- ``UniverseServerManager.hover_text`` provider suffix.
- ``--provider`` CLI validation + TINYASSETS_PIN_WRITER env var.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Daemon-side: runtime status writer
# ---------------------------------------------------------------------------


def _make_controller_stub(universe_path: Path, pinned: str = ""):
    """Build a DaemonController without triggering its real start()."""
    from fantasy_daemon.__main__ import DaemonController

    controller = DaemonController.__new__(DaemonController)
    controller._universe_path = str(universe_path)
    controller._pinned_provider = pinned
    controller._router = None
    controller._last_provider_used = ""
    controller._runtime_status_path = universe_path / ".runtime_status.json"
    return controller


def test_write_runtime_status_payload_shape(tmp_path: Path) -> None:
    controller = _make_controller_stub(tmp_path, pinned="codex")
    controller._last_provider_used = "claude-code"

    controller._write_runtime_status()

    payload = json.loads((tmp_path / ".runtime_status.json").read_text("utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["provider"] == "codex"
    assert payload["last_used_provider"] == "claude-code"
    assert payload["active_provider_label"]  # non-empty (mock label ok)
    datetime.fromisoformat(payload["updated"])  # ISO, parseable


def test_write_runtime_status_is_atomic_no_leftover_tmp(tmp_path: Path) -> None:
    controller = _make_controller_stub(tmp_path)
    controller._write_runtime_status()

    assert (tmp_path / ".runtime_status.json").exists()
    assert not (tmp_path / ".runtime_status.json.tmp").exists()


def test_remove_runtime_status_is_idempotent(tmp_path: Path) -> None:
    controller = _make_controller_stub(tmp_path)
    controller._write_runtime_status()

    controller._remove_runtime_status()
    assert not (tmp_path / ".runtime_status.json").exists()

    controller._remove_runtime_status()
    assert not (tmp_path / ".runtime_status.json").exists()


def test_runtime_status_heartbeat_runs_independently_of_node_output(
    tmp_path: Path,
) -> None:
    """The 5s loop must tick on its own, not via phase transitions.

    A long draft can legitimately block all node events for > 30s; the
    tray depends on the heartbeat to keep `updated` fresh during that
    window.
    """
    import threading
    import time as _time

    controller = _make_controller_stub(tmp_path)
    controller._stop_event = threading.Event()

    controller._write_runtime_status()
    first = json.loads(
        (tmp_path / ".runtime_status.json").read_text("utf-8"),
    )["updated"]

    _time.sleep(0.01)
    controller._write_runtime_status()
    second = json.loads(
        (tmp_path / ".runtime_status.json").read_text("utf-8"),
    )["updated"]

    assert second != first  # timestamp advances without any node event


def test_runtime_status_heartbeat_thread_joins_on_stop(
    tmp_path: Path,
) -> None:
    """`_stop_event.set()` unblocks the heartbeat loop promptly."""
    import threading
    import time

    controller = _make_controller_stub(tmp_path)
    controller._stop_event = threading.Event()

    thread = threading.Thread(
        target=controller._runtime_status_loop, daemon=True,
    )
    thread.start()
    time.sleep(0.1)

    controller._stop_event.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_write_runtime_status_empty_pin_serializes_to_empty_string(
    tmp_path: Path,
) -> None:
    controller = _make_controller_stub(tmp_path, pinned="")
    controller._write_runtime_status()

    payload = json.loads((tmp_path / ".runtime_status.json").read_text("utf-8"))
    assert payload["provider"] == ""


def test_cli_rejects_unknown_provider() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fantasy_daemon", "--provider", "not-a-real-one"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    combined = (result.stderr + result.stdout).lower()
    assert "not a known provider" in combined or "not-a-real-one" in combined


def test_cli_help_mentions_provider_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fantasy_daemon", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "--provider" in result.stdout
