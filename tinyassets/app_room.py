"""One app's document inside its owner's box.

The document is one JSON file under that owner's root. Shared services do not
import this module. The app's room is not here: it is a collection in the
publisher's broker store, answered by the router (``room_door``, S34).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tinyassets.app_access import validate_name, validate_slug

_MAX_DOCUMENT = 256 * 1024


def _app_path(root: Path, slug: str) -> Path:
    return Path(root) / "apps" / validate_slug(slug) / "app.json"


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
