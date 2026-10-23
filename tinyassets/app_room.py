"""Compare-and-swap room and player records inside one owner's box.

The database file is under that owner's root. Shared services do not import
this module. The writer of a record is the verified caller, never a field
the client put in the body.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from tinyassets.app_access import validate_name, validate_slug

_LOCAL = __import__("re").compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_DOCUMENT = 256 * 1024
_MAX_VALUE = 64 * 1024


class Conflict(Exception):
    """The presented etag is not the stored etag. The row is unchanged."""

    def __init__(self, etag: str):
        self.etag = etag
        super().__init__("conflict")


def _app_path(root: Path, slug: str) -> Path:
    return Path(root) / "apps" / validate_slug(slug) / "app.json"


def _db_path(root: Path, slug: str) -> Path:
    return _app_path(root, slug).with_name("state.sqlite")


def _published(root: Path, slug: str) -> bool:
    document = _load(root, slug)
    return document.get("published") is True


def _load(root: Path, slug: str) -> dict:
    path = _app_path(root, slug)
    if not path.is_file():
        raise FileNotFoundError(slug)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("slug") != slug:
        raise ValueError("app document is invalid")
    return document


def _require_player(root: Path, slug: str, caller: str, owner: str) -> None:
    if caller != owner and not _published(root, slug):
        raise PermissionError("app is not published")


def write_app(root, slug: str, *, name: str, markup: str = "") -> dict:
    slug = validate_slug(slug)
    name = validate_name(name)
    if not isinstance(markup, str) or len(markup.encode()) > _MAX_DOCUMENT:
        raise ValueError("app document is too large")
    path = _app_path(Path(root), slug)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    current = {}
    if path.is_file():
        current = _load(root, slug)
    document = {
        "slug": slug,
        "name": name,
        "markup": markup,
        "published": current.get("published") is True,
    }
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    os_chmod(path)
    return public_app(document)


def os_chmod(path: Path) -> None:
    import os

    os.chmod(path, 0o600)


def public_app(document: dict) -> dict:
    return {
        "slug": document["slug"],
        "name": document["name"],
        "markup": document.get("markup", ""),
        "published": document.get("published") is True,
    }


def read_app(root, slug: str, *, caller: str, owner: str) -> dict:
    _require_player(root, slug, caller, owner)
    return public_app(_load(root, slug))


def mark_published(root, slug: str, *, published: bool) -> dict:
    document = _load(root, slug)
    document["published"] = published
    path = _app_path(Path(root), slug)
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return public_app(document)


def _stored_key(scope: str, key: str, caller: str) -> tuple[str, str]:
    if not isinstance(key, str) or _LOCAL.fullmatch(key) is None:
        raise ValueError("invalid state key")
    if scope == "room":
        return "room", f"room:{key}"
    if scope == "player":
        digest = hashlib.sha256(caller.encode()).hexdigest()[:16]
        return "player", f"player:{digest}:{key}"
    raise ValueError("invalid state scope")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        "key TEXT PRIMARY KEY, scope TEXT NOT NULL, body TEXT NOT NULL, "
        "etag TEXT NOT NULL, writer TEXT NOT NULL, seq INTEGER NOT NULL)"
    )
    return db


def cas(root, slug: str, *, scope: str, key: str, value, expected_etag: str,
        caller: str, owner: str) -> dict:
    """Commit ``value`` only when ``expected_etag`` is still current.

    An empty etag creates the row. A mismatch leaves the row unchanged.
    """
    if not isinstance(expected_etag, str) or not isinstance(caller, str) or not caller:
        raise ValueError("invalid state write")
    if not isinstance(value, dict):
        raise ValueError("state value must be an object")
    _require_player(root, slug, caller, owner)
    _load(root, slug)
    stored_scope, stored_key = _stored_key(scope, key, caller)
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(body.encode()) > _MAX_VALUE:
        raise ValueError("state value is too large")
    db = _connect(_db_path(Path(root), slug))
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT etag FROM records WHERE key=?", (stored_key,)).fetchone()
        current = row[0] if row else ""
        if current != expected_etag:
            db.execute("ROLLBACK")
            raise Conflict(current)
        seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM records").fetchone()[0]
        etag = hashlib.sha256(f"{seq}:{body}".encode()).hexdigest()
        db.execute(
            "INSERT INTO records (key, scope, body, etag, writer, seq) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "body=excluded.body, etag=excluded.etag, writer=excluded.writer, "
            "seq=excluded.seq",
            (stored_key, stored_scope, body, etag, caller, seq),
        )
        db.execute("COMMIT")
    except Conflict:
        raise
    except Exception:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()
    return {"scope": stored_scope, "key": key, "etag": etag, "seq": seq, "writer": caller}


def sync(root, slug: str, *, since: int, caller: str, owner: str) -> dict:
    """Room records, plus this caller's own player records, after ``since``."""
    if type(since) is not int or since < 0:
        raise ValueError("invalid sync cursor")
    _require_player(root, slug, caller, owner)
    path = _db_path(Path(root), slug)
    if not path.is_file():
        return {"seq": 0, "records": []}
    db = _connect(path)
    try:
        rows = db.execute(
            "SELECT scope, key, body, etag, writer, seq FROM records "
            "WHERE seq > ? AND (scope = 'room' OR writer = ?) ORDER BY seq",
            (since, caller),
        ).fetchall()
        highest = db.execute("SELECT COALESCE(MAX(seq), 0) FROM records").fetchone()[0]
    finally:
        db.close()
    records = []
    for scope, key, body, etag, writer, seq in rows:
        local = key.split(":", 2)[-1] if scope == "player" else key.split(":", 1)[-1]
        records.append({
            "scope": scope, "key": local, "value": json.loads(body),
            "etag": etag, "writer": writer, "seq": seq,
        })
    return {"seq": highest, "records": records}
