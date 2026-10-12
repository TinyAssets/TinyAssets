"""A command center read as an app.

An app is not a second store. The command center already has the identity
(its id), the owner (``universe_owner``, or the signed-in home when that row
was never written), the name (the universe row's learned display name) and
the screen (``universe_app_ui``). This module projects those bytes into one
manifest and lists them for the signed-in account. It does not write.

``kind`` is ``command_center`` for every existing command center: a command
center is an app for the owner's own agents. Other buildable kinds are not
this projection. Launching the signed-in home opens the command center the
app already shows, on the screen it already mounts. Launching any other copy
would switch that command center, which this projection does not do.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tinyassets.command_center_picker import PLATFORM_DEFAULT_UI
from tinyassets.principals import named_principal

logger = logging.getLogger(__name__)

KIND_COMMAND_CENTER = "command_center"
#: What the signed-in app mounts when no chosen screen can be shown.
BLANK_SCREEN = {
    "ui_id": PLATFORM_DEFAULT_UI["ui_id"],
    "name": PLATFORM_DEFAULT_UI["name"],
}
_NOT_THE_SIGNED_IN_HOME = (
    "This copy is not the command center you are signed into. "
    "Opening it here would switch that command center, which this version does not do."
)
_MAX_TEXT = 4000


def app_manifest(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_id: str,
) -> dict[str, Any]:
    """The manifest of one command center the caller already proved is theirs.

    Read-only. A missing universe row or a missing UI row is the empty command
    center the app already opens, not an error and not a write.
    """

    owner = named_principal(owner_id)
    uid = _require_id(universe_id)
    if not owner:
        raise ValueError("an app manifest needs the account it belongs to")
    name, description = _name_and_description(base_path, uid)
    screen, version = _screen_and_version(base_path, owner, uid)
    return {
        "name": name,
        "description": description,
        "kind": KIND_COMMAND_CENTER,
        "default_screen": screen,
        "owner": owner,
        "author": owner,
        "version": version,
    }


def list_owned_apps(base_path: str | Path, owner_id: str) -> dict[str, Any]:
    """Every command center charged to this account, as an app.

    The signed-in home is included when no account is charged for it, because
    that binding is the command center the person already uses. A home charged
    to someone else is not this account's app. Nothing here is written.
    """

    from tinyassets.daemon_server import get_founder_home
    from tinyassets.universe_owner import owned_universes, owner_of

    owner = named_principal(owner_id)
    if not owner:
        return {"apps": []}
    base = Path(base_path)
    ids = list(owned_universes(base, owner))
    home = get_founder_home(base, owner)
    if home and home not in ids and owner_of(base, home) is None and _safe_id(home):
        ids.append(home)
    apps = [_app_record(base, uid, owner, home) for uid in ids]
    apps.sort(key=lambda app: (not app["home"], app["manifest"]["name"].casefold(), app["id"]))
    return {"apps": apps}


def _app_record(base: Path, universe_id: str, owner: str, home: str) -> dict[str, Any]:
    manifest = app_manifest(base, universe_id=universe_id, owner_id=owner)
    signed_in = bool(home) and universe_id == home
    opened: dict[str, Any]
    if signed_in:
        opened = {
            "method": "signed_in_command_center",
            "href": "/app",
            "screen": manifest["default_screen"],
        }
    else:
        opened = {"method": "unavailable", "reason": _NOT_THE_SIGNED_IN_HOME}
    return {"id": universe_id, "manifest": manifest, "home": signed_in, "open": opened}


def _screen_and_version(base: Path, owner: str, uid: str) -> tuple[dict[str, str], int]:
    from tinyassets.custom_agents import app_ui_renderability, get_app_ui

    document = get_app_ui(base, owner_user_id=owner, universe_id=uid)
    version = int(document.get("revision") or 0)
    selection = document.get("ui_selection")
    if not isinstance(selection, dict) or selection.get("state") != "active":
        return dict(BLANK_SCREEN), version
    ui_id = selection.get("ui_id")
    if not isinstance(ui_id, str) or not ui_id:
        return dict(BLANK_SCREEN), version
    entry = next(
        (
            item
            for item in document.get("ui_library") or []
            if isinstance(item, dict) and item.get("ui_id") == ui_id
        ),
        None,
    )
    if entry is None or app_ui_renderability(entry):
        return dict(BLANK_SCREEN), version
    raw_name = entry.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else ui_id
    return {"ui_id": ui_id, "name": name}, version


def _name_and_description(base: Path, uid: str) -> tuple[str, str]:
    from tinyassets.daemon_server import get_universe

    display = ""
    metadata: dict[str, Any] = {}
    try:
        row = get_universe(base, universe_id=uid)
    except KeyError:
        row = None
    if row is not None:
        display = str(row.get("display_name") or "")
        raw_meta = row.get("metadata")
        if isinstance(raw_meta, dict):
            metadata = raw_meta
    file_meta = _universe_file(base, uid)
    name = _learned(display, uid) or _learned(file_meta.get("name"), uid)
    description = _text(metadata.get("description")) or _text(file_meta.get("description"))
    return name, description


def _universe_file(base: Path, uid: str) -> dict[str, Any]:
    from tinyassets.universe_files import MAX_CONFIG_BYTES, read_data_path

    path = (base / uid / "universe.json").resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return {}
    try:
        raw = read_data_path(path, max_bytes=MAX_CONFIG_BYTES)
    except OSError:
        return {}
    if raw is None:
        return {}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _learned(value: Any, universe_id: str) -> str:
    """A display name. The id the row stores until a name is learned is not one."""

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text == universe_id:
        return ""
    return text[:_MAX_TEXT]


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_MAX_TEXT]


def _safe_id(universe_id: str) -> bool:
    uid = (universe_id or "").strip()
    return bool(uid) and uid not in {".", ".."} and Path(uid).name == uid


def _require_id(universe_id: str) -> str:
    uid = (universe_id or "").strip()
    if not _safe_id(uid):
        raise ValueError("command center id is not a single path segment")
    return uid


async def handle_apps(_request: Any) -> Any:
    """``GET /app/apps``: the signed-in account's apps. No body, no owner field."""

    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, PlainTextResponse

    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding import _app_identity_required, onboarding_enabled

    if not onboarding_enabled():
        return PlainTextResponse("Not Found", status_code=404)
    denied = _app_identity_required()
    if denied is not None:
        return denied
    owner = str(getattr(current_identity(), "user_id", "") or "")

    def _read() -> dict[str, Any]:
        from tinyassets.api.helpers import _base_path

        return list_owned_apps(_base_path(), owner)

    try:
        document = await run_in_threadpool(_read)
    except Exception:
        logger.exception("app list failed")
        return JSONResponse(
            {"error": "apps_unavailable"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(document, headers={"Cache-Control": "no-store"})
