"""An owner's UI preferences, so a layout follows them across devices.

The chat cloud (the floating chat with an agent) remembers where its owner put
it. Until this store it remembered that only in the browser, so a second
browser or a reinstall started over. One row per owner, agent, viewport class
and preference key, in the canonical root database beside ``account_timezone``.

A closed key space, not a settings bag: only known keys, only the ``main`` agent
for now, two viewport classes and a value of at most 2 KiB validated to that
key's shape. An owner therefore holds at most two rows. Opening ``agent_id`` to
an owner's custom agents means resolving it against their roster
(openspec/changes/owner-ui-prefs, D2).

The owner is always the authenticated subject the caller passes in. Account
deletion removes the rows through its schema-derived plan, because the column
is ``owner_user_id``.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from tinyassets.storage import DB_FILENAME

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_ui_prefs (
    owner_user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT 'main',
    viewport TEXT NOT NULL CHECK (viewport IN ('phone', 'wide')),
    pref_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner_user_id, agent_id, viewport, pref_key)
);
"""

AGENTS = frozenset({"main"})
VIEWPORTS = frozenset({"phone", "wide"})
MAX_VALUE_BYTES = 2048
MAX_COORDINATE = 100_000


class PrefRefused(ValueError):
    """A preference write this store will not accept, with the reason."""


def _chat_cloud(value: Any) -> dict[str, Any]:
    """The chat cloud's ``{v, mode, open, bubble}``, exactly as the page parses it."""
    def num(x: Any) -> bool:
        # A layout coordinate, not arithmetic: finite and of screen magnitude. A
        # huge JSON integer would make float() overflow; it is refused, not a 500.
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            return False
        try:
            return math.isfinite(float(x)) and abs(x) <= MAX_COORDINATE
        except OverflowError:
            return False

    if not isinstance(value, dict) or value.get("v") != 1:
        raise PrefRefused("chat_cloud needs v: 1")
    if value.get("mode") not in ("open", "bubble"):
        raise PrefRefused("chat_cloud mode must be open or bubble")
    rect, bubble = value.get("open"), value.get("bubble")
    if not isinstance(rect, dict) or not isinstance(bubble, dict):
        raise PrefRefused("chat_cloud needs open and bubble positions")
    numbers = [rect.get(k) for k in ("x", "y", "w", "h")] + [bubble.get(k) for k in ("x", "y")]
    if not all(num(n) for n in numbers):
        raise PrefRefused("chat_cloud positions must be finite numbers")
    return {"v": 1, "mode": value["mode"],
            "open": {k: rect[k] for k in ("x", "y", "w", "h")},
            "bubble": {k: bubble[k] for k in ("x", "y")}}


KEYS = {"chat_cloud": _chat_cloud}


def _subject(owner_user_id: str) -> str:
    subject = str(owner_user_id or "").strip()
    if not subject or len(subject) > 400 or not subject.isprintable():
        raise PrefRefused("invalid account subject")
    return subject


def _scope(agent_id: str, viewport: str) -> tuple[str, str]:
    agent = str(agent_id or "main").strip()
    if agent not in AGENTS:
        raise PrefRefused("agent must be main")
    if viewport not in VIEWPORTS:
        raise PrefRefused("viewport must be phone or wide")
    return agent, viewport


def _connect(base_path: str | Path, *, create: bool) -> sqlite3.Connection | None:
    path = Path(base_path) / DB_FILENAME
    if not create and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(_SCHEMA)
    return conn


def write_pref(
    base_path: str | Path, *, owner_user_id: str, agent_id: str, viewport: str,
    key: str, value: Any,
) -> dict[str, Any]:
    """Store one preference for the owner. Returns the stored value.

    Raises ``PrefRefused`` for an unknown key, agent or viewport, a value over
    ``MAX_VALUE_BYTES`` or one that does not match the key's shape; a refused
    write leaves the stored value as it was.
    """
    subject = _subject(owner_user_id)
    agent, view = _scope(agent_id, viewport)
    shape = KEYS.get(key)
    if shape is None:
        raise PrefRefused(f"unknown preference {key!r}")
    stored = shape(value)
    encoded = json.dumps(stored, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise PrefRefused("preference value is too large")
    conn = _connect(base_path, create=True)
    assert conn is not None
    try:
        with conn:
            conn.execute(
                "INSERT INTO owner_ui_prefs (owner_user_id, agent_id, viewport, pref_key, "
                "value_json, updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(owner_user_id, agent_id, viewport, pref_key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at",
                (subject, agent, view, key, encoded, time.time()),
            )
    finally:
        conn.close()
    return stored


def read_prefs(
    base_path: str | Path, *, owner_user_id: str, agent_id: str, viewport: str,
) -> dict[str, Any]:
    """The owner's preferences for one agent and viewport class: ``{key: value}``.

    Empty is a real answer (nothing saved yet). A stored value that no longer
    parses is left out rather than raising.
    """
    subject = _subject(owner_user_id)
    agent, view = _scope(agent_id, viewport)
    conn = _connect(base_path, create=False)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT pref_key, value_json FROM owner_ui_prefs "
            "WHERE owner_user_id = ? AND agent_id = ? AND viewport = ?",
            (subject, agent, view),
        ).fetchall()
    finally:
        conn.close()
    prefs: dict[str, Any] = {}
    for key, raw in rows:
        shape = KEYS.get(key)
        if shape is None:
            continue
        try:
            prefs[key] = shape(json.loads(raw))
        except (ValueError, PrefRefused):
            continue
    return prefs
