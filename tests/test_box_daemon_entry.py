"""A committed marker chooses one serving process without a fallback."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "box_daemon_entry", ROOT / "deploy" / "box_daemon_entry.py"
)
assert SPEC and SPEC.loader
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


def test_unmigrated_data_refuses(tmp_path):
    with pytest.raises(RuntimeError, match="marker is required"):
        entry.check_layout(tmp_path)
    (tmp_path / ".layout.json").write_text('{"layout": 2, "state": "stable"}')
    with pytest.raises(RuntimeError, match="unrecognized"):
        entry.check_layout(tmp_path)


def test_owner_box_requires_unprivileged_frontend(tmp_path, monkeypatch):
    (tmp_path / ".layout.json").write_text(
        '{"layout": 2, "state": "stable", "box_layout": "owner-box"}'
    )
    monkeypatch.setattr(entry.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="unprivileged"):
        entry.check_layout(tmp_path)
    monkeypatch.setattr(entry.os, "geteuid", lambda: 1001)
    assert entry.check_layout(tmp_path) is None
    assert not hasattr(entry, "LEGACY_LAUNCHER")


def test_unfinished_or_unknown_layout_refuses(tmp_path):
    marker = tmp_path / ".layout.json"
    marker.write_text('{"layout": 2, "state": "migrating"}')
    with pytest.raises(RuntimeError, match="incomplete"):
        entry.check_layout(tmp_path)
    marker.write_text('{"layout": 2, "state": "stable", "box_layout": "unknown"}')
    with pytest.raises(RuntimeError, match="unrecognized"):
        entry.check_layout(tmp_path)


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T4')
