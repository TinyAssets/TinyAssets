"""Tests for tinyassets.preferences — tray runtime defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets import preferences


@pytest.fixture(autouse=True)
def _isolated_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the prefs file to a tmp path and clear the module cache."""
    path = tmp_path / ".tray_preferences.json"
    monkeypatch.setattr(preferences, "_PREFS_PATH", path)
    preferences.reset_cache()
    yield path
    preferences.reset_cache()


def test_missing_file_returns_defaults(_isolated_prefs: Path) -> None:
    assert not _isolated_prefs.exists()
    prefs = preferences.load_preferences()
    assert prefs == {
        "default_providers": ["ollama-local"],
        "auto_start_default": True,
    }


def test_malformed_file_returns_defaults(_isolated_prefs: Path) -> None:
    _isolated_prefs.write_text("{ not valid json", encoding="utf-8")
    prefs = preferences.load_preferences()
    assert prefs["default_providers"] == ["ollama-local"]
    assert prefs["auto_start_default"] is True


def test_malformed_types_fall_back_per_key(_isolated_prefs: Path) -> None:
    _isolated_prefs.write_text(
        json.dumps(
            {"default_providers": "not-a-list", "auto_start_default": "yes"}
        ),
        encoding="utf-8",
    )
    prefs = preferences.load_preferences()
    assert prefs["default_providers"] == ["ollama-local"]
    assert prefs["auto_start_default"] is True


def test_save_round_trip(_isolated_prefs: Path) -> None:
    written = preferences.save_preferences(
        {"default_providers": ["codex", "ollama-local"], "auto_start_default": False}
    )
    assert written["default_providers"] == ["codex", "ollama-local"]
    assert written["auto_start_default"] is False

    # Reload from disk via a fresh cache.
    preferences.reset_cache()
    loaded = preferences.load_preferences()
    assert loaded == written

    # File contents are valid JSON matching what we wrote.
    on_disk = json.loads(_isolated_prefs.read_text(encoding="utf-8"))
    assert on_disk == written


def test_save_creates_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "output" / "nested" / ".tray_preferences.json"
    monkeypatch.setattr(preferences, "_PREFS_PATH", nested)
    preferences.reset_cache()
    preferences.save_preferences({"default_providers": ["grok-free"]})
    assert nested.exists()


def test_save_atomic_no_tmp_leftover(_isolated_prefs: Path) -> None:
    preferences.save_preferences({"default_providers": ["codex"]})
    leftovers = [
        p for p in _isolated_prefs.parent.iterdir()
        if p.name.startswith(".tray_preferences.") and p.suffix == ".tmp"
    ]
    assert leftovers == []


def test_save_unknown_keys_dropped(_isolated_prefs: Path) -> None:
    written = preferences.save_preferences(
        {
            "default_providers": ["codex"],
            "auto_start_default": False,
            "bogus_key": 123,
        }
    )
    assert "bogus_key" not in written
    on_disk = json.loads(_isolated_prefs.read_text(encoding="utf-8"))
    assert "bogus_key" not in on_disk


def test_cache_hit_avoids_disk(_isolated_prefs: Path) -> None:
    preferences.save_preferences({"default_providers": ["codex"]})
    # Mutate the file directly on disk; cache should still return saved value.
    _isolated_prefs.write_text(
        json.dumps({"default_providers": ["grok-free"]}), encoding="utf-8"
    )
    prefs = preferences.load_preferences()
    assert prefs["default_providers"] == ["codex"]

    # refresh=True re-reads.
    prefs2 = preferences.load_preferences(refresh=True)
    assert prefs2["default_providers"] == ["grok-free"]
