"""Graph-handle adapter for a person's user-authored UI library and choice.

The row is keyed by the AUTHENTICATED caller and the universe, never by anything
the caller names: a request can only ever read or write its own row. Universe
access uses the same check as agent bindings (``_binding_access``), so who may
keep a UI in a universe is exactly who may keep an app experience there.
"""

from __future__ import annotations

from typing import Any

from tinyassets.api.commons_catalog import (
    _authenticated_actor,
    _binding_access,
    _binding_universe,
    _payload,
)
from tinyassets.api.helpers import _base_path
from tinyassets.app_ui_store import (
    APP_UI_ENTRY_OPERATIONS,
    app_ui_etag,
    app_ui_index,
    app_ui_renderability,
    change_app_ui_entry,
    get_app_ui,
    put_app_ui_asset,
    read_app_ui_asset,
    save_app_ui,
)
from tinyassets.commons_catalog import AgentConflictError, AgentNotFoundError, AgentValidationError
from tinyassets.storage_accounting import StorageRefused

#: ``read_app_ui(ui_id=INDEX)`` is the row without any UI body.
INDEX = "index"

#: The public authoring page ``read_page page="app_ui"`` returns. The seven
#: component fields and ``version`` 1 are the contract ``add_ui`` stores.
APP_UI_AUTHORING_PAGE = """\
# App UI

One private library per person and command center. Read and write one UI at a
time. `read_page page="app_ui"` is this page.

Read:

- `read_graph target="app_ui" query="index"` — the index: each UI's ui_id,
  name, etag and size, plus ui_selection and revision. No markup, style or
  script.
- `read_graph target="app_ui" query="<ui_id>"` — that one UI.
- `read_graph target="app_ui" query="<ui_id>" field_name="script" output_offset=0`
  — one chunk of one field, with next_offset when more remains.

Write with `write_graph target="app_ui"`. `operation="add_ui"` takes exactly:

```
{"component": {"kind": "tinyassets.app-ui.v1", "version": 1,
 "ui_id": "office-tower", "name": "Office tower",
 "markup": "<div id=lobby></div>", "style": "", "script": ""}}
```

`version` is the format version and must be 1. It is not a revision or a
cache-buster; anything else is refused and the UI cannot be shown.
`ui_id` is lowercase letters, digits and dashes, starting with a letter or
digit, at most 64 characters. `markup` is body markup only (no html or head).
`replace_ui` takes the same `{"component": {...}}`.

Optional component fields, and no others: `assets`, `libraries`,
`script_type`, `workflow_refs`, `agent_refs`.

Other operations: `activate` `{"ui_id": "..."}`, `use_default` `{}`,
`edit_ui` `{"ui_id", "set" and/or "edits"}`, `remove_ui` `{"ui_id"}`.
"""
#: Chunk bound for one field of one UI, the unit a bounded model reads.
_MAX_FIELD_CHUNK = 32768


def read_app_ui(
    *, universe_id: str = "", ui_id: str = "", field_name: str = "",
    output_offset: int = 0, output_max_chars: int = 8192,
) -> dict[str, Any]:
    """The caller's own UI row: whole, as an index, the choice, one UI, or a chunk.

    No ``ui_id`` is the whole row (the app's read). ``ui_id="index"`` is every
    UI's id, name, etag and field sizes with no bodies, plus the choice.
    ``ui_id="ui_selection"`` is the choice only: no library. Any other
    ``ui_id`` is that one UI; with ``field_name`` it is one chunk of that
    field, from ``output_offset``, with ``next_offset`` to continue.
    """

    uid = _binding_universe(universe_id)
    denial = _binding_access(uid, write=False)
    if denial is not None:
        return denial
    actor = _authenticated_actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "app_ui"}
    try:
        document = get_app_ui(_base_path(), owner_user_id=actor, universe_id=uid)
    except AgentValidationError as exc:
        return {"error": "app_ui_validation_error", "detail": str(exc)}
    selector = (ui_id or "").strip()
    if not selector:
        from tinyassets.command_center_picker import PLATFORM_DEFAULT_UI

        return {"app_ui": {**document, "platform_default": PLATFORM_DEFAULT_UI}}
    if selector == INDEX:
        return {"app_ui": app_ui_index(document)}
    if selector == "ui_selection":
        # The choice is a few fields. Returning the row here handed the model
        # the whole library (live: 569KB, then cut). ui_id cannot contain "_",
        # so this name cannot be a real UI.
        return {"app_ui": {
            "universe_id": document.get("universe_id"),
            "revision": document.get("revision"),
            "updated_at": document.get("updated_at"),
            "ui_selection": document.get("ui_selection"),
        }}
    entry = next((e for e in document["ui_library"]
                  if isinstance(e, dict) and e.get("ui_id") == selector), None)
    if entry is None:
        return {"error": "app_ui_not_found", "ui_id": selector,
                "installed": [u["ui_id"] for u in app_ui_index(document)["uis"]]}
    field = (field_name or "").strip()
    if not field:
        reply = {"ui": entry, "etag": app_ui_etag(entry), "revision": document["revision"]}
        # Why the app refuses this one, if it does, next to the body that causes
        # it -- so a read of the broken UI answers the question it raises.
        refusal = app_ui_renderability(entry)
        reply["renderable"] = not refusal
        if refusal:
            reply["reason"] = refusal["reason"]
            reply["fix"] = refusal["hint"]
        return reply
    text = entry.get(field)
    if not isinstance(text, str):
        return {"error": "app_ui_field_not_found", "ui_id": selector, "field_name": field}
    start = max(0, int(output_offset or 0))
    size = max(1, min(int(output_max_chars or 8192), _MAX_FIELD_CHUNK))
    chunk = text[start:start + size]
    end = start + len(chunk)
    return {"ui_id": selector, "field_name": field, "etag": app_ui_etag(entry),
            "offset": start, "chunk": chunk, "total_chars": len(text),
            "next_offset": end if end < len(text) else None}


def write_app_ui(
    *, universe_id: str = "", payload: Any = None, expected_revision: int = 0,
) -> dict[str, Any]:
    uid = _binding_universe(universe_id)
    denial = _binding_access(uid, write=True)
    if denial is not None:
        return denial
    actor = _authenticated_actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "app_ui"}
    try:
        saved = save_app_ui(
            _base_path(),
            owner_user_id=actor,
            universe_id=uid,
            expected_revision=expected_revision,
            changes=_payload(payload),
        )
    except AgentConflictError as exc:
        return {"error": "app_ui_conflict", "detail": str(exc)}
    except AgentValidationError as exc:
        return {"error": "app_ui_validation_error", "detail": str(exc)}
    except StorageRefused as refused:
        # At the account's storage quota: the visible refusal, with its inline
        # Upgrade link. Nothing was saved.
        return _visible_refusal(refused, actor)
    return {"status": "saved", "app_ui": saved}


def change_app_ui(
    *, universe_id: str = "", operation: str = "", payload: Any = None,
) -> dict[str, Any]:
    """One targeted change to the caller's own UI row; see ``change_app_ui_entry``."""

    op = (operation or "").strip().lower()
    if op not in APP_UI_ENTRY_OPERATIONS:
        return {"error": "unknown_app_ui_operation", "operation": operation,
                "allowed_operations": ["save", *APP_UI_ENTRY_OPERATIONS]}
    uid = _binding_universe(universe_id)
    denial = _binding_access(uid, write=True)
    if denial is not None:
        return denial
    actor = _authenticated_actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "app_ui"}
    try:
        document = _payload(payload) if payload not in (None, "") else {}
        if op == "put_asset":
            outcome = _put_asset(actor, uid, document)
        else:
            outcome = change_app_ui_entry(
                _base_path(), owner_user_id=actor, universe_id=uid,
                operation=op, payload=document,
            )
    except AgentNotFoundError as exc:
        return {"error": "app_ui_not_found", "detail": str(exc)}
    except AgentConflictError as exc:
        return {"error": "app_ui_conflict", "detail": str(exc)}
    except AgentValidationError as exc:
        return {"error": "app_ui_validation_error", "detail": str(exc)}
    except StorageRefused as refused:
        return _visible_refusal(refused, actor)
    return {"status": "saved", "operation": op, **outcome}


_PUT_SOURCES = ("text", "base64", "from_file")


def _put_asset(actor: str, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """``put_asset``: bytes from exactly one source, then one targeted change.

    ``from_file`` is a file the command center's agents already wrote under
    ``/u``, read through the owner's own gate, so an image rendered by code in
    the agent's box becomes an asset without passing through the model.
    """
    import base64
    import binascii

    from tinyassets.api.universe_file_reads import read_whole_file

    unknown = sorted(set(payload) - {"ui_id", "path", "expected_etag", *_PUT_SOURCES})
    if unknown:
        raise AgentValidationError(f"put_asset field {unknown[0]!r} is not recognised")
    given = [source for source in _PUT_SOURCES if source in payload]
    if len(given) != 1:
        raise AgentValidationError(
            "put_asset needs exactly one of text, base64 or from_file"
        )
    source, value = given[0], payload[given[0]]
    if not isinstance(value, str):
        raise AgentValidationError(f"put_asset {source} must be a string")
    if source == "text":
        data = value.encode("utf-8")
    elif source == "base64":
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise AgentValidationError("put_asset base64 is not valid base64") from None
    else:
        data = read_whole_file(universe_id=uid, path=value)
        if data is None:
            raise AgentNotFoundError(
                f"no file {value!r} in this command center's folder (/u) that you can read"
            )
    ui_id, path = payload.get("ui_id"), payload.get("path")
    if not isinstance(ui_id, str) or not ui_id.strip():
        raise AgentValidationError("ui_id is required")
    etag = payload.get("expected_etag") or ""
    return put_app_ui_asset(
        _base_path(), owner_user_id=actor, universe_id=uid, ui_id=ui_id.strip(),
        path=path if isinstance(path, str) else "", data=data,
        expected_etag=etag if isinstance(etag, str) else "",
    )


def read_app_ui_asset_bytes(*, universe_id: str, sha256: str) -> bytes | dict:
    """One of the caller's own UI blobs for the app to hand its frame.

    The same universe gate as reading the row; the blob is keyed by the caller,
    so the hash reaches only bytes the caller stored.
    """
    uid = _binding_universe(universe_id)
    denial = _binding_access(uid, write=False)
    if denial is not None:
        return denial
    actor = _authenticated_actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "app_ui"}
    found = read_app_ui_asset(_base_path(), owner_user_id=actor, sha256=sha256)
    if found is None:
        return {"error": "app_ui_asset_not_found"}
    return found


def preview_app_ui(*, universe_id: str = "", ui_id: str = "") -> dict[str, Any]:
    """Render the caller's own UI headlessly; the report, screenshot in /u.

    The PNG is written to ``/u/previews/<ui_id>.png`` for the agent to look at
    with ``read`` (which shows images); the report says what a person would
    see and what went wrong. See :mod:`tinyassets.ui_preview`.
    """
    from tinyassets import ui_preview
    from tinyassets.api.helpers import _universe_dir

    uid = _binding_universe(universe_id)
    denial = _binding_access(uid, write=True)
    if denial is not None:
        return denial
    actor = _authenticated_actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "app_ui"}
    selector = (ui_id or "").strip()
    if not selector:
        return {"error": "app_ui_validation_error",
                "detail": "query must name the ui_id to preview"}
    try:
        if selector.startswith("publication:"):
            report = _preview_publication(uid, actor, selector.removeprefix("publication:"))
            if "error" in report:
                return report
        else:
            report = ui_preview.preview_app_ui(
                _base_path(), owner_user_id=actor, universe_id=uid, ui_id=selector)
            report["screenshot"] = ui_preview.write_preview(
                _universe_dir(uid), selector, report.pop("png"))
    except AgentNotFoundError as exc:
        return {"error": "app_ui_not_found", "detail": str(exc)}
    except ui_preview.PreviewUnavailable as exc:
        reason = str(exc)
        return {"error": reason.split(":", 1)[0], "detail": reason}
    report["see_it"] = f'read path="{report["screenshot"]}"'
    return report


def _preview_publication(uid: str, actor: str, definition_id: str) -> dict[str, Any]:
    """Owner-only preview of immutable public content, never the private UI row."""
    import hashlib

    from tinyassets import ui_preview
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.commons_catalog import get_definition

    definition = get_definition(_base_path(), definition_id)
    if definition is None or definition["author_id"] != actor:
        return {"error": "app_ui_not_found"}
    screen = next((c for c in definition["components"].values()
                   if c.get("kind") == "tinyassets.app-ui.v1"), None)
    if screen is None:
        return {"error": "app_ui_not_found"}
    report = ui_preview.preview_public_component(screen, universe_dir=_universe_dir(uid))
    name = "publication-" + hashlib.sha256(definition_id.encode()).hexdigest()[:32]
    report["screenshot"] = ui_preview.write_preview(_universe_dir(uid), name, report.pop("png"))
    return report


__all__ = [
    "INDEX",
    "change_app_ui",
    "preview_app_ui",
    "read_app_ui",
    "read_app_ui_asset_bytes",
    "write_app_ui",
]


def _visible_refusal(refused, viewer=None):
    """The refusal the CALLER may see: the charged account's full record only
    if the caller is that account (storage_accounting.visible_record)."""
    from tinyassets.storage_accounting import visible_record

    return visible_record(refused, viewer)
