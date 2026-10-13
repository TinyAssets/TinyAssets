"""A person's own app UIs: the library, the choice, and the assets they load.

Deliberately NOT an agent binding: a UI choice has no definition, is never
served, and must not appear to any binding reader. The rows live in the same
owner database as the commons catalogue (``commons_catalog``), which owns the
connection and the schema.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tinyassets.commons_catalog import (
    AgentConflictError,
    AgentNotFoundError,
    AgentValidationError,
    _agent_connect,
    _canonical_json,
)

#: There is NO bound on a person's UI library, by count OR by bytes. The 4 MiB
#: ``MAX_APP_UI_LIBRARY_BYTES`` that used to live here was a separate storage
#: number, refusing an install with "remove one first"; its own comment said it
#: should be charged against a per-universe storage quota once one existed. One
#: does, so it is: these bytes count toward tier storage, which is one of the two
#: limits an account has (founder, 2026-09-30 -- the other is concurrent agent
#: seats). Per-UI bundle validation is unchanged: that is payload validation of
#: one document, not an account limit.
_APP_UI_FIELDS = frozenset({"ui_library", "ui_selection"})
_MAX_APP_UI_SELECTION_BYTES = 1024

#: Payload bounds on ONE UI, not account limits: the account's limit is its
#: storage, which every byte below is charged to. They exist because a UI is
#: held whole in the viewer's tab and its text is read by a bounded model. The
#: 49,152-byte component bound these replace made a game impossible (founder's
#: village, 2026-10-02); code past 1 MiB of text belongs in a JS asset.
APP_UI_MAX_COMPONENT_TEXT_BYTES = 1024 * 1024
APP_UI_MAX_ASSET_BYTES = 16 * 1024 * 1024
APP_UI_MAX_UI_ASSET_BYTES = 128 * 1024 * 1024
APP_UI_MAX_ASSET_FILES = 500
APP_UI_SCRIPT_TYPES = ("classic", "module")
_ASSET_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")
_ASSET_PATH_MAX = 200
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
#: The closed table of what a UI may load. The type is what the frame's Blob is
#: given; the HTTP response that carries the bytes is always octet-stream.
APP_UI_ASSET_MEDIA_TYPES = MappingProxyType({
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".gltf": "model/gltf+json", ".glb": "model/gltf-binary",
    ".bin": "application/octet-stream", ".ktx2": "image/ktx2",
    ".hdr": "image/vnd.radiance",
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".txt": "text/plain", ".csv": "text/csv",
    ".xml": "application/xml", ".atlas": "text/plain", ".fnt": "text/plain",
})
#: A blob no UI references is kept this long before an asset write sweeps it,
#: so a `put_asset` never races its own reference away.
_ASSET_GC_GRACE_SECONDS = 3600.0


def app_ui_asset_media_type(path: str) -> str:
    """The media type ``path``'s extension maps to, or AgentValidationError."""
    suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    media_type = APP_UI_ASSET_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise AgentValidationError(
            f"asset {path!r}: extension {suffix or '(none)'} is not one a UI can load; "
            f"use one of {sorted(APP_UI_ASSET_MEDIA_TYPES)}"
        )
    return media_type


def _check_asset_path(path: Any) -> str:
    if (not isinstance(path, str) or len(path) > _ASSET_PATH_MAX
            or not _ASSET_PATH_RE.match(path) or "/../" in f"/{path}/"):
        raise AgentValidationError(
            f"asset path {path!r} must be up to {_ASSET_PATH_MAX} characters of "
            "slash-separated segments of letters, digits, dot, dash or underscore"
        )
    return path


def app_ui_workflow_refs(entry: dict[str, Any]) -> dict[str, str]:
    """Validate the explicit portable bindings; never interpret script text."""
    refs = entry.get("workflow_refs", {})
    if not isinstance(refs, dict) or len(refs) > 100:
        raise AgentValidationError("workflow_refs must be an object of at most 100 references")
    for alias, workflow in refs.items():
        if (not isinstance(alias, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", alias)
                or not isinstance(workflow, str) or not workflow
                or workflow != workflow.strip() or _utf16_units(workflow) > 200):
            raise AgentValidationError("workflow_refs contains an invalid alias or workflow id")
    return dict(refs)


def app_ui_agent_refs(entry: dict[str, Any]) -> dict[str, str]:
    """Declared agent aliases have the same shape as workflow aliases."""
    try:
        return app_ui_workflow_refs({"workflow_refs": entry.get("agent_refs", {})})
    except AgentValidationError as exc:
        raise AgentValidationError(str(exc).replace("workflow", "agent")) from None


def _check_component(entry: dict[str, Any]) -> None:
    """The bounds and shape of the fields the server stores for one UI.

    The seven rendering fields are the app's to read (it names what it cannot
    render); what the server must own is anything its own stores depend on --
    the asset manifest, the library names, and the text a model reads.
    """
    ui_id = entry["ui_id"]
    text_bytes = len(_canonical_json(entry).encode("utf-8"))
    if text_bytes > APP_UI_MAX_COMPONENT_TEXT_BYTES:
        raise AgentValidationError(
            f"UI {ui_id!r} is {text_bytes} bytes of text; the bound is "
            f"{APP_UI_MAX_COMPONENT_TEXT_BYTES}. Move code into a JS asset (put_asset)"
        )
    if "script_type" in entry and entry["script_type"] not in APP_UI_SCRIPT_TYPES:
        raise AgentValidationError(
            f"UI {ui_id!r}: script_type must be one of {list(APP_UI_SCRIPT_TYPES)}"
        )
    if "libraries" in entry:
        from tinyassets.onboarding import ui_library_set

        try:
            ui_library_set.check_names(entry["libraries"])
        except ValueError as exc:
            raise AgentValidationError(f"UI {ui_id!r}: {exc}") from None
    app_ui_workflow_refs(entry)
    app_ui_agent_refs(entry)
    if "assets" not in entry:
        return
    assets = entry["assets"]
    if not isinstance(assets, dict):
        raise AgentValidationError(f"UI {ui_id!r}: assets must be an object of path -> blob")
    if len(assets) > APP_UI_MAX_ASSET_FILES:
        raise AgentValidationError(
            f"UI {ui_id!r} has {len(assets)} assets; the bound is {APP_UI_MAX_ASSET_FILES}"
        )
    total = 0
    for path, ref in assets.items():
        _check_asset_path(path)
        if (not isinstance(ref, dict) or set(ref) != {"sha256", "size", "media_type"}
                or not isinstance(ref["sha256"], str) or not _SHA256_RE.match(ref["sha256"])
                or type(ref["size"]) is not int
                or not 0 <= ref["size"] <= APP_UI_MAX_ASSET_BYTES
                or ref["media_type"] != app_ui_asset_media_type(path)):
            raise AgentValidationError(
                f"UI {ui_id!r}: asset {path!r} must be exactly "
                '{"sha256": <hex>, "size": <bytes>, "media_type": <its extension\'s type>}; '
                "put_asset writes it for you"
            )
        total += ref["size"]
    if total > APP_UI_MAX_UI_ASSET_BYTES:
        raise AgentValidationError(
            f"UI {ui_id!r} assets total {total} bytes; the bound is {APP_UI_MAX_UI_ASSET_BYTES}"
        )


def _begin_write(conn: sqlite3.Connection) -> None:
    """Take the database write lock NOW, before anything is read.

    A deferred transaction reads without a lock, so an asset sweep could commit
    between a writer's check that a blob is held and its row write (Codex,
    2026-10-02: a save returned success naming a blob already deleted). With the
    lock taken first, the check and the write are one serial step: a sweep that
    ran first leaves the check to refuse; one that runs after sees the reference.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _referenced_assets(library: list[Any]) -> list[tuple[str, str, dict[str, Any]]]:
    refs = []
    for entry in library:
        if isinstance(entry, dict) and isinstance(entry.get("assets"), dict):
            for path, ref in entry["assets"].items():
                refs.append((str(entry.get("ui_id")), path, ref))
    return refs


def _check_assets_held(conn: sqlite3.Connection, owner: str, library: list[Any]) -> None:
    """Every blob ``library`` names is stored for ``owner``, as named.

    A row can never point at bytes its owner does not hold -- not another
    person's blob by hash, not one swept away -- whichever path wrote it. The
    caller holds the write lock (:func:`_begin_write`), so no sweep can delete a
    blob between this check and the row write that references it.
    """
    refs = _referenced_assets(library)
    if not refs:
        return
    hashes = sorted({ref["sha256"] for _, _, ref in refs})
    held: dict[str, int] = {}
    for start in range(0, len(hashes), 500):
        chunk = hashes[start:start + 500]
        for row in conn.execute(
            "SELECT sha256, size_bytes FROM universe_app_ui_asset "
            f"WHERE owner_user_id = ? AND sha256 IN ({','.join('?' * len(chunk))})",
            (owner, *chunk),
        ):
            held[str(row["sha256"])] = int(row["size_bytes"])
    for ui_id, path, ref in refs:
        if held.get(ref["sha256"]) != ref["size"]:
            raise AgentValidationError(
                f"UI {ui_id!r}: asset {path!r} names bytes that are not in your UI storage; "
                "write it with put_asset"
            )


def _app_ui_document(row: sqlite3.Row | None, universe_id: str) -> dict[str, Any]:
    if row is None:
        return {"universe_id": universe_id, "ui_library": [], "ui_selection": None,
                "revision": 0, "updated_at": None}
    selection = row["ui_selection_json"]
    return {
        "universe_id": universe_id,
        "ui_library": json.loads(str(row["ui_library_json"])),
        "ui_selection": None if selection is None else json.loads(str(selection)),
        "revision": int(row["revision"]),
        "updated_at": float(row["updated_at"]),
    }


def _app_ui_scope(owner_user_id: str, universe_id: str) -> tuple[str, str]:
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    uid = (universe_id or "").strip()
    if not owner:
        raise AgentValidationError("an authenticated owner is required")
    if not uid:
        raise AgentValidationError("universe_id is required")
    return owner, uid


def _check_app_ui_fields(changes: dict[str, Any]) -> None:
    unknown = sorted(set(changes) - _APP_UI_FIELDS)
    if unknown:
        raise AgentValidationError(
            f"app UI payload field {unknown[0]!r} is not one of {sorted(_APP_UI_FIELDS)}"
        )
    if not changes:
        raise AgentValidationError("app UI payload must set ui_library or ui_selection")
    if "ui_library" in changes:
        library = changes["ui_library"]
        if not isinstance(library, list):
            raise AgentValidationError("ui_library must be a list")
        seen: set[str] = set()
        for entry in library:
            ui_id = entry.get("ui_id") if isinstance(entry, dict) else None
            if not isinstance(ui_id, str) or not ui_id:
                raise AgentValidationError("every ui_library entry must be an object with a ui_id")
            if ui_id in seen:
                raise AgentValidationError(f"ui_id {ui_id!r} is listed twice")
            seen.add(ui_id)
            _check_component(entry)
    if "ui_selection" in changes and not isinstance(changes["ui_selection"], dict):
        raise AgentValidationError("ui_selection must be an object")


def get_app_ui(
    base_path: str | Path, *, owner_user_id: str, universe_id: str,
) -> dict[str, Any]:
    """The caller's own UI library and choice in ``universe_id``.

    An absent row reads as an empty library at revision 0 -- the state a fresh
    account is in, and the revision a first save names.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    with _agent_connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
            (owner, uid),
        ).fetchone()
    from tinyassets.extension_ui import fence

    return fence(Path(base_path), owner, uid, _app_ui_document(row, uid))


def save_app_ui(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Compare-and-set the caller's UI library and/or choice.

    Only the fields in ``changes`` are written; an omitted field keeps its stored
    value, so saving a choice can never erase the library. ``expected_revision``
    is the revision last read -- 0 when no row exists yet. Each branch is ONE
    statement, so there is no read-then-write window: two first saves racing
    both name revision 0, the primary key admits one row, and the other gets
    :class:`AgentConflictError`.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise AgentValidationError("expected_revision must be a non-negative integer")
    if not isinstance(changes, dict):
        raise AgentValidationError("app UI payload must be a JSON object")
    _check_app_ui_fields(changes)
    # The library has no size bound -- its bytes are the universe's tier storage.
    # The SELECTION still does: it is one small pointer document, and a payload
    # bound on a single document is not an account limit.
    selection_bytes = len(_canonical_json(changes.get("ui_selection")).encode("utf-8"))
    if selection_bytes > _MAX_APP_UI_SELECTION_BYTES:
        raise AgentValidationError(
            f"ui_selection exceeds {_MAX_APP_UI_SELECTION_BYTES} bytes of canonical JSON"
        )
    library = (_canonical_json(changes["ui_library"])
               if "ui_library" in changes else None)
    selection = (_canonical_json(changes["ui_selection"])
                 if "ui_selection" in changes else None)
    from tinyassets import storage_accounting

    # The library's bytes are the saver's account storage (account-storage-quota
    # D7): at the quota this raises `StorageRefused` before anything is written.
    account = owner if storage_accounting.is_account(base_path, owner) else None
    reservation = storage_accounting.reserve(
        base_path, account_id=account, scope_id=account or "", store="ui_library",
        nbytes=sum(len(s.encode("utf-8")) for s in (library or "", selection or "")),
    )
    try:
        saved = _save_app_ui_row(
            base_path, owner=owner, uid=uid, expected_revision=expected_revision,
            library=library, selection=selection,
            library_doc=changes.get("ui_library"),
        )
    except BaseException:
        storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return saved


def _save_app_ui_row(
    base_path: str | Path,
    *,
    owner: str,
    uid: str,
    expected_revision: int,
    library: str | None,
    selection: str | None,
    library_doc: list[Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    with _agent_connect(base_path) as conn:
        _begin_write(conn)
        if library_doc is not None:
            _check_assets_held(conn, owner, library_doc)
        if expected_revision == 0:
            written = conn.execute(
                """
                INSERT INTO universe_app_ui (
                    owner_user_id, universe_id, ui_library_json, ui_selection_json,
                    revision, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(owner_user_id, universe_id) DO NOTHING
                """,
                (owner, uid, library if library is not None else "[]", selection, now),
            ).rowcount
        else:
            written = conn.execute(
                """
                UPDATE universe_app_ui
                   SET ui_library_json = COALESCE(?, ui_library_json),
                       ui_selection_json = COALESCE(?, ui_selection_json),
                       revision = revision + 1,
                       updated_at = ?
                 WHERE owner_user_id = ? AND universe_id = ? AND revision = ?
                """,
                (library, selection, now, owner, uid, expected_revision),
            ).rowcount
        row = conn.execute(
            "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
            (owner, uid),
        ).fetchone()
    if written != 1:
        current = 0 if row is None else int(row["revision"])
        raise AgentConflictError(
            f"app UI changed: expected revision {expected_revision}, current is {current}; "
            "read it again before saving"
        )
    return _app_ui_document(row, uid)


# ---- One UI at a time --------------------------------------------------------
# A universe edits a person's UIs by TALKING, which means through a model whose
# tool results are bounded (engine_result_bounds). A whole-row compare-and-set
# needs the whole library read first, and a library larger than that bound is
# cut before its revision: live 2026-09-30, the founder asked to try "GTM Village"
# and their universe could not switch the screen. These operations name ONE UI
# (or only the choice) and never need the library or its revision. Each one
# re-reads the row and writes it back under ``revision = <the one it read>``, and
# re-runs on a lost race, so nothing another writer saved in between -- the
# app's whole-library save included -- is overwritten. The row revision still
# advances, so the app's own compare-and-set sees the change.

#: Fields ``edit_ui`` may change. ``kind``, ``version`` and ``ui_id`` are what
#: the component IS; changing those is a ``replace_ui``. ``assets`` changes one
#: path at a time through ``put_asset`` / ``remove_asset``.
APP_UI_EDITABLE_FIELDS = ("name", "markup", "style", "script", "libraries", "script_type",
                          "workflow_refs", "agent_refs")
_APP_UI_TEXT_FIELDS = ("name", "markup", "style", "script")
APP_UI_ENTRY_OPERATIONS = (
    "activate", "use_default", "add_ui", "replace_ui", "edit_ui", "remove_ui",
    "put_asset", "remove_asset",
)
#: The second half of ``put_asset``: the blob is stored, now point the path at
#: it. Internal -- a caller cannot name a hash it has not just written.
_SET_ASSET = "_set_asset"
_APP_UI_ENTRY_ATTEMPTS = 8


def app_ui_etag(component: Any) -> str:
    """A short digest of one UI, so an edit can require the version it read."""
    return hashlib.sha256(_canonical_json(component).encode("utf-8")).hexdigest()[:16]


#: The app's rendering contract, mirrored here so a READ can say which stored
#: UI the app will refuse and why. The app stays the authority on rendering
#: (see ``_check_component``: the server owns only what its own stores depend
#: on); this adds no refusal, it only reports.
#: ``tests/test_app_ui_renderability.py`` holds these equal to app_ui.js.
APP_UI_KIND = "tinyassets.app-ui.v1"
#: The FORMAT version of the component, not a revision and not a cache-buster.
#: It is always 1. An agent that set it to a timestamp hid every UI the person
#: had built (founder, P1, 2026-10-03).
APP_UI_FORMAT_VERSION = 1
APP_UI_COMPONENT_FIELDS = ("kind", "markup", "name", "script", "style", "ui_id", "version")
APP_UI_OPTIONAL_COMPONENT_FIELDS = ("assets", "libraries", "script_type", "workflow_refs",
                                    "agent_refs")
#: Matched with ``fullmatch``, never ``match``: Python's ``$`` also matches
#: BEFORE a trailing newline, so ``"x" * 64 + "\n"`` passed here while the app
#: refused it -- a mirror saying "renderable" about a UI the app will not show
#: is worse than no report at all (Codex, 2026-10-03).
_APP_UI_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
APP_UI_MAX_NAME = 120


def _utf16_units(text: str) -> int:
    """The length JavaScript measures: UTF-16 code units, not code points.

    ``name.length <= MAX_NAME`` in the app counts surrogate pairs twice, so 61
    emoji are 122 units there and 61 characters here. Python said renderable
    about a name the app refuses (Codex, 2026-10-03).
    """
    return len(text.encode("utf-16-le")) // 2


def app_ui_renderability(entry: Any) -> dict[str, str]:
    """``{}`` when the app can render ``entry``, else its reason and a fix.

    Only the checks the WRITE path does not already make, which is exactly the
    set a stored entry can still fail: the field list, ``kind``, the format
    ``version``, and the shape of ``ui_id``, ``name`` and the three text
    fields. Bounds, assets, libraries and ``script_type`` are enforced by
    ``_check_component`` on the way in, so a stored entry has passed them.
    """
    if not isinstance(entry, dict):
        return {"reason": "UI component is not an object",
                "hint": "replace_ui with a JSON object component"}
    keys = set(entry)
    extra = sorted(keys - set(APP_UI_COMPONENT_FIELDS) - set(APP_UI_OPTIONAL_COMPONENT_FIELDS))
    if extra:
        return {"reason": "UI component carries fields this app does not render: "
                          + ", ".join(extra),
                "hint": f"remove {', '.join(extra)}; the app refuses any field outside "
                        f"{list(APP_UI_COMPONENT_FIELDS)} plus "
                        f"{list(APP_UI_OPTIONAL_COMPONENT_FIELDS)}"}
    missing = [f for f in APP_UI_COMPONENT_FIELDS if f not in keys]
    if missing:
        return {"reason": "UI component is missing " + ", ".join(missing),
                "hint": f"replace_ui with all of {list(APP_UI_COMPONENT_FIELDS)} present"}
    if entry["kind"] != APP_UI_KIND:
        return {"reason": f"not a {APP_UI_KIND} component",
                "hint": f'set "kind" to "{APP_UI_KIND}"'}
    if entry["version"] != APP_UI_FORMAT_VERSION or isinstance(entry["version"], bool):
        return {"reason": f"UI version {entry['version']!r} is not supported; this app renders "
                          f"version {APP_UI_FORMAT_VERSION}",
                "hint": f'"version" is the component FORMAT version and is always '
                        f'{APP_UI_FORMAT_VERSION}; it is not a revision or a cache-buster. '
                        f'Set it back to {APP_UI_FORMAT_VERSION} with replace_ui. Nothing needs '
                        "busting: the app re-reads this row whenever its revision moves, and an "
                        "asset is addressed by its own sha256"}
    if (not isinstance(entry["ui_id"], str) or _utf16_units(entry["ui_id"]) > 64
            or not _APP_UI_ID_RE.fullmatch(entry["ui_id"])):
        return {"reason": "ui_id must be lowercase letters, digits or dashes",
                "hint": "use up to 64 characters of lowercase letters, digits or dashes"}
    if (not isinstance(entry["name"], str) or not entry["name"].strip()
            or _utf16_units(entry["name"]) > APP_UI_MAX_NAME):
        return {"reason": "name must be a non-empty string of at most "
                          f"{APP_UI_MAX_NAME} characters",
                "hint": f"set a name of 1 to {APP_UI_MAX_NAME} characters"}
    for field in ("markup", "style", "script"):
        if not isinstance(entry[field], str):
            return {"reason": f"{field} must be a string",
                    "hint": f'set "{field}" to a string (empty is fine)'}
    # The rest of the contract -- script_type, library names, the asset manifest
    # and the text bound -- is already defined once, by the check the write path
    # makes. Delegating keeps this a COMPLETE mirror of what the app renders
    # rather than a partial one: a differential test compares every verdict
    # against app_ui.js, and a partial mirror that says "renderable" about a UI
    # the app refuses is worse than no report (Codex, 2026-10-03, which found
    # script_type and libraries diverging exactly here).
    try:
        _check_component(entry)
    except AgentValidationError as exc:
        return {"reason": str(exc), "hint": "correct it with replace_ui"}
    return {}


def app_ui_index(document: dict[str, Any]) -> dict[str, Any]:
    """The row without any UI body: what a model reads to pick a target."""
    entries = []
    for entry in document.get("ui_library") or []:
        if not isinstance(entry, dict):
            continue
        summary = {
            "ui_id": entry.get("ui_id"),
            "name": entry.get("name"),
            "etag": app_ui_etag(entry),
            "chars": {field: len(entry[field]) for field in ("markup", "style", "script")
                      if isinstance(entry.get(field), str)},
        }
        # Per UI, so one bad component is diagnosable instead of making the
        # whole library read as broken (founder, P1, 2026-10-03).
        refusal = app_ui_renderability(entry)
        summary["renderable"] = not refusal
        if refusal:
            summary["reason"] = refusal["reason"]
            summary["fix"] = refusal["hint"]
        if isinstance(entry.get("assets"), dict):
            # Paths and sizes, not bodies: what a model needs to reference one.
            summary["assets"] = {path: ref.get("size") for path, ref in entry["assets"].items()
                                 if isinstance(ref, dict)}
        for field in ("libraries", "script_type"):
            if field in entry:
                summary[field] = entry[field]
        entries.append(summary)
    return {
        "universe_id": document.get("universe_id"),
        "revision": document.get("revision"),
        "ui_selection": document.get("ui_selection"),
        "uis": entries,
    }


def _entry_position(library: list[Any], ui_id: str) -> int:
    for position, entry in enumerate(library):
        if isinstance(entry, dict) and entry.get("ui_id") == ui_id:
            return position
    return -1


def _named_ui_id(payload: dict[str, Any]) -> str:
    ui_id = payload.get("ui_id")
    if not isinstance(ui_id, str) or not ui_id.strip():
        raise AgentValidationError("ui_id is required")
    return ui_id.strip()


def _missing_ui(ui_id: str, library: list[Any]) -> AgentNotFoundError:
    installed = [e.get("ui_id") for e in library if isinstance(e, dict)]
    return AgentNotFoundError(
        f"no UI with ui_id {ui_id!r} in this library; installed: {installed}"
    )


def _check_etag(payload: dict[str, Any], entry: dict[str, Any]) -> None:
    expected = payload.get("expected_etag")
    if expected in (None, ""):
        return
    if expected != app_ui_etag(entry):
        raise AgentConflictError(
            f"UI {entry.get('ui_id')!r} changed since it was read "
            f"(etag is now {app_ui_etag(entry)}); read it again before editing"
        )


def _refuse_unrenderable(component: Any) -> None:
    """Refuse a component no app can ever render, naming the reason and the fix.

    The write and the read now share ONE definition of a valid component. They
    did not: the write checked only what the server's own stores depend on, so
    ``replace_ui`` ACCEPTED ``"version": 1791005187`` and answered with a
    success receipt (revision 50 -> 51), while the app refused to render it.
    The agent, told it had saved, assured the founder the UI was intact
    (founder, P1, 2026-10-03).

    Only ``add_ui`` and ``replace_ui`` go through here -- a whole component
    supplied by the caller. ``save`` deliberately does NOT: it writes the whole
    library, and the app carries entries it cannot render through that write so
    they are not destroyed. Refusing there would make a stored bad entry
    impossible to write back, which is the data loss this guards against.
    """
    refusal = app_ui_renderability(component)
    if refusal:
        raise AgentValidationError(f"{refusal['reason']}. {refusal['hint']}")


def _component(payload: dict[str, Any]) -> dict[str, Any]:
    component = payload.get("component")
    if not isinstance(component, dict):
        raise AgentValidationError("component must be an object")
    _check_app_ui_fields({"ui_library": [component]})
    _refuse_unrenderable(component)
    return component


def _edited_entry(entry: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    changes = payload.get("set") or {}
    edits = payload.get("edits") or []
    if not isinstance(changes, dict) or not isinstance(edits, list):
        raise AgentValidationError("set must be an object and edits a list")
    if not changes and not edits:
        raise AgentValidationError("edit_ui needs set and/or edits")
    edited = dict(entry)
    for field, value in changes.items():
        if field not in APP_UI_EDITABLE_FIELDS:
            raise AgentValidationError(
                f"set field {field!r} is not one of {list(APP_UI_EDITABLE_FIELDS)}"
            )
        if field == "libraries" and not isinstance(value, list):
            raise AgentValidationError("set.libraries must be a list of library names")
        if field == "workflow_refs":
            app_ui_workflow_refs({"workflow_refs": value})
        if field == "agent_refs":
            app_ui_agent_refs({"agent_refs": value})
        if field not in {"libraries", "workflow_refs", "agent_refs"} and not isinstance(value, str):
            raise AgentValidationError(f"set.{field} must be a string")
        edited[field] = value
    for number, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise AgentValidationError(f"edits[{number}] must be an object")
        field, old, new = edit.get("field"), edit.get("old"), edit.get("new")
        if field not in _APP_UI_TEXT_FIELDS:
            raise AgentValidationError(
                f"edits[{number}].field must be one of {list(_APP_UI_TEXT_FIELDS)}"
            )
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise AgentValidationError(
                f"edits[{number}] needs a non-empty old string and a new string"
            )
        current = edited.get(field)
        count = current.count(old) if isinstance(current, str) else 0
        if count != 1:
            raise AgentValidationError(
                f"edits[{number}]: the old text occurs {count} times in {field}; "
                "it must occur exactly once (quote more of it)"
            )
        edited[field] = current.replace(old, new, 1)
    _check_component(edited)
    return edited


def _apply_app_ui_entry_operation(
    library: list[Any], selection: Any, operation: str, payload: dict[str, Any],
) -> tuple[list[Any] | None, Any, dict[str, Any]]:
    """Pure: the new (library or None if unchanged, selection, outcome)."""

    if operation == "use_default":
        return None, {"version": 1, "state": "default"}, {"ui_selection": "default"}
    if operation == "put_asset":
        # Bytes are stored by `put_app_ui_asset`, which then applies _SET_ASSET.
        raise AgentValidationError("put_asset is applied through put_app_ui_asset")
    if operation == "add_ui":
        component = _component(payload)
        if _entry_position(library, component["ui_id"]) >= 0:
            raise AgentConflictError(
                f"a UI with ui_id {component['ui_id']!r} is already installed; "
                "use replace_ui or edit_ui"
            )
        return [*library, component], selection, {
            "ui_id": component["ui_id"], "etag": app_ui_etag(component)}
    ui_id = _named_ui_id(payload) if operation != "replace_ui" else _component(payload)["ui_id"]
    position = _entry_position(library, ui_id)
    if position < 0:
        raise _missing_ui(ui_id, library)
    entry = library[position]
    if operation == "activate":
        return None, {"version": 1, "state": "active", "ui_id": ui_id}, {
            "ui_selection": "active", "ui_id": ui_id}
    _check_etag(payload, entry)
    if operation == "remove_ui":
        remaining = library[:position] + library[position + 1:]
        if isinstance(selection, dict) and selection.get("ui_id") == ui_id:
            selection = {"version": 1, "state": "default"}
        return remaining, selection, {"ui_id": ui_id, "removed": True}
    if operation in (_SET_ASSET, "remove_asset"):
        path = _check_asset_path(payload.get("path"))
        assets = dict(entry.get("assets") or {}) if isinstance(entry.get("assets"), dict) else {}
        if operation == _SET_ASSET:
            assets[path] = payload["ref"]
        elif path not in assets:
            raise AgentNotFoundError(
                f"UI {ui_id!r} has no asset {path!r}; it has {sorted(assets)}"
            )
        else:
            del assets[path]
        replacement = {k: v for k, v in entry.items() if k != "assets"}
        if assets:
            replacement["assets"] = assets
        _check_component(replacement)
        updated = list(library)
        updated[position] = replacement
        outcome = {"ui_id": ui_id, "path": path, "etag": app_ui_etag(replacement)}
        if operation == _SET_ASSET:
            outcome.update(payload["ref"])
        else:
            outcome["removed"] = True
        return updated, selection, outcome
    replacement = (_component(payload) if operation == "replace_ui"
                   else _edited_entry(entry, payload))
    # An edit may not BREAK a UI that rendered. It is not refused for a fault
    # the stored entry already had, because then the agent could not edit its
    # way out of one -- `replace_ui` is the way back, and it is checked above.
    if operation == "edit_ui" and not app_ui_renderability(entry):
        _refuse_unrenderable(replacement)
    updated = list(library)
    updated[position] = replacement
    return updated, selection, {"ui_id": ui_id, "etag": app_ui_etag(replacement)}


def change_app_ui_entry(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply ONE targeted change to the caller's UI row, atomically.

    ``operation`` is one of :data:`APP_UI_ENTRY_OPERATIONS`. No revision is
    named: the change is computed against whatever is stored now and written
    under that row's revision, so a concurrent writer is never overwritten --
    a lost race re-reads and re-applies. A change that no longer applies after
    the re-read (the UI was removed, an ``expected_etag`` no longer matches)
    fails loudly rather than landing on content it was not made for.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    if operation not in APP_UI_ENTRY_OPERATIONS and operation != _SET_ASSET:
        raise AgentValidationError(
            f"app UI operation {operation!r} is not one of {list(APP_UI_ENTRY_OPERATIONS)}"
        )
    if not isinstance(payload, dict):
        raise AgentValidationError("app UI payload must be a JSON object")
    # The library's bytes are the saver's account storage, on this path as on
    # `save_app_ui`: the change can add at most its payload, so that is charged
    # before anything is written (`StorageRefused` at the quota). An edit that
    # replaces bytes is an over-count the next measurement clears.
    from tinyassets import storage_accounting

    storage_accounting.charge_now(
        base_path,
        account_id=owner if storage_accounting.is_account(base_path, owner) else None,
        store="ui_library",
        nbytes=len(_canonical_json(payload).encode("utf-8")),
    )
    with _agent_connect(base_path) as conn:
        for _attempt in range(_APP_UI_ENTRY_ATTEMPTS):
            row = conn.execute(
                "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
                (owner, uid),
            ).fetchone()
            current = _app_ui_document(row, uid)
            library, selection, outcome = _apply_app_ui_entry_operation(
                current["ui_library"], current["ui_selection"], operation, payload,
            )
            # The read above is lock-free and the write below is compare-and-set
            # on the revision it read; the held-check sits between them under the
            # write lock, so no sweep can land between the check and the write.
            _begin_write(conn)
            if library is not None:
                _check_assets_held(conn, owner, library)
            library_json = None if library is None else _canonical_json(library)
            selection_json = _canonical_json(selection) if selection is not None else None
            now = time.time()
            if row is None:
                written = conn.execute(
                    """
                    INSERT INTO universe_app_ui (
                        owner_user_id, universe_id, ui_library_json, ui_selection_json,
                        revision, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(owner_user_id, universe_id) DO NOTHING
                    """,
                    (owner, uid, library_json or "[]", selection_json, now),
                ).rowcount
            else:
                written = conn.execute(
                    """
                    UPDATE universe_app_ui
                       SET ui_library_json = COALESCE(?, ui_library_json),
                           ui_selection_json = ?,
                           revision = revision + 1,
                           updated_at = ?
                     WHERE owner_user_id = ? AND universe_id = ? AND revision = ?
                    """,
                    (library_json, selection_json, now, owner, uid, int(row["revision"])),
                ).rowcount
            if written == 1:
                conn.commit()
                return {**outcome, "revision": current["revision"] + 1}
            conn.rollback()
    raise AgentConflictError(
        "app UI kept changing while this change was applied; try it again"
    )


def read_app_ui_asset(
    base_path: str | Path, *, owner_user_id: str, sha256: str,
) -> bytes | None:
    """The bytes of one of the owner's own UI blobs, else None.

    Keyed by the owner: a hash names bytes only within one person's storage, so
    knowing another person's hash reaches nothing.
    """
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    if not owner or not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        return None
    with _agent_connect(base_path) as conn:
        row = conn.execute(
            "SELECT content FROM universe_app_ui_asset "
            "WHERE owner_user_id = ? AND sha256 = ?",
            (owner, sha256),
        ).fetchone()
    return None if row is None else bytes(row["content"])


def store_app_ui_asset(
    base_path: str | Path, *, owner_user_id: str, data: bytes, media_type: str,
) -> dict[str, Any]:
    """Store ``data`` in the owner's UI blob storage; ``{sha256, size, media_type}``.

    ``media_type`` is the reference's, returned for the caller's manifest; the
    stored row is bytes only, so storing the same bytes under another type
    changes nothing another reference depends on. Charged to the owner's storage
    before anything is written (``StorageRefused`` at the quota); bytes already
    held are free. Unreferenced blobs past their grace period are swept in the
    same transaction.
    """
    from tinyassets import storage_accounting
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    if not owner:
        raise AgentValidationError("an authenticated owner is required")
    if not isinstance(data, (bytes, bytearray)):
        raise AgentValidationError("asset content must be bytes")
    if len(data) > APP_UI_MAX_ASSET_BYTES:
        raise AgentValidationError(
            f"asset is {len(data)} bytes; the bound per file is {APP_UI_MAX_ASSET_BYTES}"
        )
    if media_type not in APP_UI_ASSET_MEDIA_TYPES.values():
        raise AgentValidationError(f"media type {media_type!r} is not one a UI can load")
    data = bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    ref = {"sha256": sha, "size": len(data), "media_type": media_type}
    with _agent_connect(base_path) as conn:
        # Already held: free, and the grace period restarts so the reference
        # that follows cannot lose a race with a sweep.
        if conn.execute(
            "UPDATE universe_app_ui_asset SET created_at = ? "
            "WHERE owner_user_id = ? AND sha256 = ?",
            (time.time(), owner, sha),
        ).rowcount == 1:
            return ref
    account = owner if storage_accounting.is_account(base_path, owner) else None
    reservation = storage_accounting.reserve(
        base_path, account_id=account, scope_id=account or "", store="ui_library",
        nbytes=len(data),
    )
    try:
        with _agent_connect(base_path) as conn:
            conn.execute(
                """
                INSERT INTO universe_app_ui_asset (
                    owner_user_id, sha256, size_bytes, content, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, sha256) DO UPDATE SET
                    created_at = excluded.created_at
                """,
                (owner, sha, len(data), sqlite3.Binary(data), time.time()),
            )
            _sweep_app_ui_assets(conn, owner)
    except BaseException:
        storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return ref


def _sweep_app_ui_assets(conn: sqlite3.Connection, owner: str) -> int:
    """Delete the owner's blobs no row of theirs references, past the grace."""
    referenced: set[str] = set()
    for row in conn.execute(
        "SELECT ui_library_json FROM universe_app_ui WHERE owner_user_id = ?", (owner,),
    ):
        try:
            library = json.loads(str(row["ui_library_json"]))
        except ValueError:
            # An unreadable row may still reference anything: sweep nothing.
            return 0
        if not isinstance(library, list):
            return 0
        referenced.update(str(ref.get("sha256")) for _, _, ref in _referenced_assets(library)
                          if isinstance(ref, dict))
    cutoff = time.time() - _ASSET_GC_GRACE_SECONDS
    stale = [str(row["sha256"]) for row in conn.execute(
        "SELECT sha256 FROM universe_app_ui_asset WHERE owner_user_id = ? AND created_at < ?",
        (owner, cutoff),
    ).fetchall() if str(row["sha256"]) not in referenced]
    for sha in stale:
        conn.execute(
            "DELETE FROM universe_app_ui_asset WHERE owner_user_id = ? AND sha256 = ?",
            (owner, sha),
        )
    return len(stale)


def put_app_ui_asset(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    ui_id: str,
    path: str,
    data: bytes,
    expected_etag: str = "",
) -> dict[str, Any]:
    """Store ``data`` and point ``path`` in UI ``ui_id`` at it, as one change.

    The media type comes from the path's extension. The pointer is applied like
    every targeted change: to the row as stored, re-applied on a lost race.
    """
    _check_asset_path(path)
    ref = store_app_ui_asset(
        base_path, owner_user_id=owner_user_id, data=data,
        media_type=app_ui_asset_media_type(path),
    )
    payload: dict[str, Any] = {"ui_id": ui_id, "path": path, "ref": ref}
    if expected_etag:
        payload["expected_etag"] = expected_etag
    return change_app_ui_entry(
        base_path, owner_user_id=owner_user_id, universe_id=universe_id,
        operation=_SET_ASSET, payload=payload,
    )


__all__ = [
    "APP_UI_ASSET_MEDIA_TYPES",
    "APP_UI_COMPONENT_FIELDS",
    "APP_UI_EDITABLE_FIELDS",
    "APP_UI_ENTRY_OPERATIONS",
    "APP_UI_FORMAT_VERSION",
    "APP_UI_KIND",
    "APP_UI_MAX_ASSET_BYTES",
    "APP_UI_MAX_ASSET_FILES",
    "APP_UI_MAX_COMPONENT_TEXT_BYTES",
    "APP_UI_MAX_NAME",
    "APP_UI_MAX_UI_ASSET_BYTES",
    "APP_UI_OPTIONAL_COMPONENT_FIELDS",
    "APP_UI_SCRIPT_TYPES",
    "app_ui_agent_refs",
    "app_ui_asset_media_type",
    "app_ui_etag",
    "app_ui_index",
    "app_ui_renderability",
    "app_ui_workflow_refs",
    "change_app_ui_entry",
    "get_app_ui",
    "put_app_ui_asset",
    "read_app_ui_asset",
    "save_app_ui",
    "store_app_ui_asset",
]
