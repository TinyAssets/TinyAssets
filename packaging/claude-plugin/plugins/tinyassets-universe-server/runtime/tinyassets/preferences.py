"""Tray runtime preferences — durable defaults for the universe tray.

Stores user-chosen defaults for which providers the tray should start by
default and whether to auto-start them. Persisted as JSON at
``~/.tinyassets/preferences.json``.

Per-host-operator state, deliberately separate from per-universe
``config.yaml`` and from Claude Code's ``~/.claude/`` directory. Cross-platform
via ``Path.home()``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Subscription-gated providers (user pays; tray shows which is active).
SUBSCRIPTION_PROVIDERS: list[str] = [
    "claude-code",
    "codex",
]

# Providers that run locally with no external subscription.
LOCAL_PROVIDERS: list[str] = ["ollama-local"]

ALL_PROVIDERS: list[str] = SUBSCRIPTION_PROVIDERS + LOCAL_PROVIDERS

_DEFAULTS: dict[str, Any] = {
    "default_providers": ["ollama-local"],
    "auto_start_default": True,
}

_TINYASSETS_USER_DIR = Path.home() / ".tinyassets"
_PREFS_PATH = _TINYASSETS_USER_DIR / "preferences.json"

# Module-level cache. ``None`` means not yet loaded.
_cache: dict[str, Any] | None = None


def _prefs_path() -> Path:
    return _PREFS_PATH


def _coerce(raw: Any) -> dict[str, Any]:
    """Merge *raw* over defaults, dropping unknown keys and bad types."""
    merged: dict[str, Any] = dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return merged

    dp = raw.get("default_providers")
    if isinstance(dp, list) and all(isinstance(p, str) for p in dp):
        merged["default_providers"] = list(dp)

    asd = raw.get("auto_start_default")
    if isinstance(asd, bool):
        merged["auto_start_default"] = asd

    return merged


def load_preferences(*, refresh: bool = False) -> dict[str, Any]:
    """Return current preferences, reading from disk the first time.

    Missing file → defaults. Malformed file → warn and return defaults
    (do not crash the tray). Pass ``refresh=True`` to bypass the cache.
    """
    global _cache
    if _cache is not None and not refresh:
        return dict(_cache)

    path = _prefs_path()
    if not path.exists():
        _cache = dict(_DEFAULTS)
        return dict(_cache)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Tray preferences at %s unreadable (%s); using defaults",
            path, exc,
        )
        _cache = dict(_DEFAULTS)
        return dict(_cache)

    _cache = _coerce(raw)
    return dict(_cache)


def save_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    """Persist *prefs* atomically and refresh the cache.

    Unknown keys are dropped; bad types fall back to the default for that
    key. Returns the coerced dict that was actually written.
    """
    global _cache
    coerced = _coerce(prefs)

    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: tmp file in same directory + os.replace.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tray_preferences.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(coerced, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    _cache = dict(coerced)
    return dict(coerced)


def reset_cache() -> None:
    """Clear the in-memory cache. Primarily for tests."""
    global _cache
    _cache = None
