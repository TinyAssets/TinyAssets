"""Compare-and-swap room and player records inside one owner's box.

Records are one JSON file under that owner's root. Shared services do not
import this module. The writer of a record is the verified caller, never a
field the client put in the body.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from tinyassets.app_access import validate_name, validate_slug

_LOCAL = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_DOCUMENT = 256 * 1024
_MAX_VALUE = 64 * 1024


class Conflict(Exception):
    """The presented etag is not the stored etag. The row is unchanged."""

    def __init__(self, etag: str):
        self.etag = etag
        super().__init__("conflict")


def _app_path(root: Path, slug: str) -> Path:
    return Path(root) / "apps" / validate_slug(slug) / "app.json"


def _state_path(root: Path, slug: str) -> Path:
    return _app_path(root, slug).with_name("state.json")


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


@contextmanager
def _locked(root: Path, slug: str, *, exclusive: bool):
    directory = _app_path(root, slug).parent
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(directory / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(directory / "state.lock", 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_records(path: Path) -> dict:
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("records") if isinstance(document, dict) else None
    if not isinstance(records, dict):
        raise ValueError("app state is invalid")
    return records


def _write_records(path: Path, records: dict) -> None:
    temporary = path.with_name(".state.json.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.chmod(temporary, 0o600)
        os.write(descriptor, json.dumps({"records": records}, sort_keys=True).encode())
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    os.replace(temporary, path)


def _highest(records: dict) -> int:
    seqs = [row["seq"] for row in records.values()
            if isinstance(row, dict) and type(row.get("seq")) is int]
    return max(seqs, default=0)


def cas(root, slug: str, *, scope: str, key: str, value, expected_etag: str,
        caller: str, owner: str) -> dict:
    """Commit ``value`` only when ``expected_etag`` is still current.

    An empty etag creates the record. A mismatch leaves the file unchanged.
    """
    if not isinstance(expected_etag, str) or not isinstance(caller, str) or not caller:
        raise ValueError("invalid state write")
    if not isinstance(value, dict):
        raise ValueError("state value must be an object")
    root = Path(root)
    _require_player(root, slug, caller, owner)
    _load(root, slug)
    stored_scope, stored_key = _stored_key(scope, key, caller)
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(body.encode()) > _MAX_VALUE:
        raise ValueError("state value is too large")
    path = _state_path(root, slug)
    with _locked(root, slug, exclusive=True):
        records = _read_records(path)
        row = records.get(stored_key)
        current = row.get("etag") if isinstance(row, dict) else ""
        if not isinstance(current, str):
            current = ""
        if current != expected_etag:
            raise Conflict(current)
        seq = _highest(records) + 1
        etag = hashlib.sha256(f"{seq}:{body}".encode()).hexdigest()
        records[stored_key] = {
            "scope": stored_scope, "body": body, "etag": etag,
            "writer": caller, "seq": seq,
        }
        _write_records(path, records)
    return {"scope": stored_scope, "key": key, "etag": etag, "seq": seq, "writer": caller}


def sync(root, slug: str, *, since: int, caller: str, owner: str) -> dict:
    """Room records, plus this caller's own player records, after ``since``."""
    if type(since) is not int or since < 0:
        raise ValueError("invalid sync cursor")
    root = Path(root)
    _require_player(root, slug, caller, owner)
    path = _state_path(root, slug)
    if not path.parent.is_dir():
        return {"seq": 0, "records": []}
    with _locked(root, slug, exclusive=False):
        records = _read_records(path)
        chosen = [
            (key, row) for key, row in records.items()
            if isinstance(row, dict) and type(row.get("seq")) is int and row["seq"] > since
            and (row.get("scope") == "room" or row.get("writer") == caller)
        ]
        chosen.sort(key=lambda item: item[1]["seq"])
        visible = []
        for key, row in chosen:
            scope = row["scope"]
            local = key.split(":", 2)[-1] if scope == "player" else key.split(":", 1)[-1]
            visible.append({
                "scope": scope, "key": local, "value": json.loads(row["body"]),
                "etag": row["etag"], "writer": row["writer"], "seq": row["seq"],
            })
        return {"seq": _highest(records), "records": visible}
