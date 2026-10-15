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


def test_owner_split_uses_existing_launcher(tmp_path, monkeypatch):
    launcher = tmp_path / "ta-launch.py"
    launcher.write_text("")
    monkeypatch.setattr(entry, "LEGACY_LAUNCHER", launcher)
    assert entry.target(tmp_path)[-1] == str(launcher)
    (tmp_path / ".layout.json").write_text('{"layout": 2, "state": "stable"}')
    assert entry.target(tmp_path)[-1] == str(launcher)


def test_an_image_without_the_legacy_launcher_refuses_unmigrated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "LEGACY_LAUNCHER", tmp_path / "absent")
    with pytest.raises(SystemExit, match="startup refused: the data volume is not migrated"):
        entry.target(tmp_path)
    (tmp_path / ".layout.json").write_text('{"layout": 2, "state": "stable"}')
    with pytest.raises(SystemExit, match="owner-split marker"):
        entry.target(tmp_path)


def test_owner_box_requires_unprivileged_frontend(tmp_path, monkeypatch):
    (tmp_path / ".layout.json").write_text(
        '{"layout": 2, "state": "stable", "box_layout": "owner-box"}'
    )
    monkeypatch.setattr(entry.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="unprivileged"):
        entry.target(tmp_path)
    monkeypatch.setattr(entry.os, "geteuid", lambda: 1001)
    assert entry.target(tmp_path)[-2:] == ["-m", "tinyassets.boxes.frontend"]


def test_unfinished_or_unknown_layout_refuses(tmp_path):
    marker = tmp_path / ".layout.json"
    marker.write_text('{"layout": 2, "state": "migrating"}')
    with pytest.raises(RuntimeError, match="incomplete"):
        entry.target(tmp_path)
    marker.write_text('{"layout": 2, "state": "stable", "box_layout": "unknown"}')
    with pytest.raises(RuntimeError, match="unrecognized"):
        entry.target(tmp_path)
