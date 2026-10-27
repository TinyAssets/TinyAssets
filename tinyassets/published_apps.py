"""Published app route bindings. The shared layer stores slug and owner only.

The app document stays in the owner's box; its room and player records are a
collection in the owner's broker store (S34). This file is the copy T10 makes
so the router can select that owner without opening an owner volume.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from tinyassets.app_access import validate_name, validate_slug

_LOCK = threading.Lock()


def _path(root: Path) -> Path:
    return Path(root) / ".published" / "apps.json"


def _read(root: Path) -> dict:
    path = _path(root)
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("published app directory is unreadable") from exc
    if not isinstance(document, dict):
        raise RuntimeError("published app directory is unreadable")
    return document


def _write(root: Path, document: dict) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


def owner_for(root, slug: str) -> str | None:
    """The app owner bound to ``slug``, or None when the slug is not published."""
    if not isinstance(slug, str):
        return None
    row = _read(Path(root)).get(slug)
    if not isinstance(row, dict):
        return None
    owner = row.get("owner")
    if not isinstance(owner, str) or not owner or set(row) - {"owner", "name"}:
        raise RuntimeError("published app directory is unreadable")
    return owner


def bind(root, owner: str, slug: str, name: str) -> dict:
    """Record one slug for this owner. Another owner's slug is refused."""
    slug = validate_slug(slug)
    if not isinstance(owner, str) or not owner:
        raise ValueError("invalid app publication")
    name = validate_name(name)
    root = Path(root)
    with _LOCK:
        document = _read(root)
        current = document.get(slug)
        if isinstance(current, dict) and current.get("owner") not in {None, owner}:
            raise PermissionError("app slug is published by another owner")
        document[slug] = {"owner": owner, "name": name}
        _write(root, document)
    return {"slug": slug, "owner": owner, "name": name}


def unbind(root, owner: str, slug: str) -> dict:
    slug = validate_slug(slug)
    root = Path(root)
    with _LOCK:
        document = _read(root)
        current = document.get(slug)
        if current is None or isinstance(current, dict) and current.get("owner") != owner:
            # Idempotent: nothing of this owner's is bound; another owner's stays.
            return {"slug": slug, "owner": owner, "published": False}
        if not isinstance(current, dict):
            raise PermissionError("app slug is not this owner's")
        del document[slug]
        _write(root, document)
    return {"slug": slug, "owner": owner, "published": False}


def apply(root, owner: str, payload: object):
    """Broker publication of a route binding. None leaves the commons path alone."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    if kind == "published_app" and set(payload) == {"kind", "slug", "name"}:
        return bind(root, owner, payload["slug"], payload["name"])
    if kind == "published_app_withdrawal" and set(payload) == {"kind", "slug"}:
        return unbind(root, owner, payload["slug"])
    if kind in {"published_app", "published_app_withdrawal"}:
        raise ValueError("invalid app publication")
    return None
