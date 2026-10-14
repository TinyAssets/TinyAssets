"""Private, preview-bound exports through the existing owner door.

Previews are short-lived server state, never client assertions of approval.
Restart or provider replacement invalidates them. Only the protected owner
session may turn a preview into a download; no route publishes or runs code.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import threading
import time
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from tinyassets.boxes.provider import BoxAuthError, BoxError, StaleHandle, WriteConflict
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.transfer import Limits, Preview, export_zip, ingest_zip, preview
from tinyassets.owner_door.routes import _BYTE_HEADERS, _HEADERS

_TTL = 600
_CAPACITY = 128 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(4)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Pending:
    provider: object
    view: Preview
    expires: float


_PENDING: dict[str, Pending] = {}


def _prune():
    for token, pending in list(_PENDING.items()):
        if pending.expires <= time.monotonic():
            del _PENDING[token]


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def _arguments(args):
    if not isinstance(args, dict) or not isinstance(args.get("command_center"), str):
        raise InvalidBundle("Name the command center")
    if not args["command_center"] or len(args["command_center"]) > 256:
        raise InvalidBundle("Invalid command center")
    operation = args.get("operation")
    if operation == "preview":
        if set(args) != {"operation", "command_center", "paths"} or not _strings(args["paths"]):
            raise InvalidBundle("Preview needs a list of file paths")
    elif operation == "export":
        if (
            set(args) != {
                "operation", "command_center", "preview", "selected", "named_binaries",
                "included_contacts", "profile",
            }
            or not isinstance(args["preview"], str)
            or not _strings(args["selected"])
            or not _strings(args["named_binaries"])
            or not isinstance(args["included_contacts"], dict)
            or args["profile"] not in ("share", "export")
        ):
            raise InvalidBundle("Export needs the preview and exact content selections")
    elif operation == "import":
        if set(args) != {"operation", "command_center", "archive"} or not isinstance(
            args["archive"], str
        ):
            raise InvalidBundle("Import needs a base64 archive")
    else:
        raise InvalidBundle("Unknown transfer operation")
    return args


def _transfer(provider, handle, args):
    if args["operation"] == "import":
        from tinyassets.api.helpers import _base_path
        from tinyassets.command_center_packages import quarantine_import

        try:
            raw = base64.b64decode(args["archive"], validate=True)
        except (ValueError, binascii.Error):
            raise InvalidBundle("Invalid base64 archive") from None
        bundle = ingest_zip(raw)
        # Detect stale placement before retaining an import for this center.
        provider.read_many(handle, [], max_total=0)
        return quarantine_import(
            _base_path(), owner=handle.account_id, command_center=handle.command_center_id,
            epoch=handle.epoch, bundle=bundle,
        )
    if args["operation"] == "preview":
        view = preview(provider, handle, args["paths"])
        pending = Pending(provider, view, time.monotonic() + _TTL)
        token = secrets.token_urlsafe(32)
        with _LOCK:
            _prune()
            previous = [t for t, p in _PENDING.items() if p.view.scope[1] == handle.account_id]
            used = sum(
                sum(map(len, p.view.files.values())) for t, p in _PENDING.items()
                if t not in previous
            )
            if (
                used + sum(map(len, view.files.values())) > _CAPACITY
                or len(_PENDING) - len(previous) >= 256
            ):
                raise BufferError("Preview capacity is busy")
            for old in previous:
                del _PENDING[old]
            _PENDING[token] = pending
        return {
            "preview": token,
            "expires_in": _TTL,
            "limitations": view.limitations,
            "files": [
                {
                    "path": path, "size": len(data), "selected": False,
                    "binary": path in view.binary,
                    "blocked": view.blocked.get(path),
                    "content": None if path in view.binary else data.decode("utf-8"),
                    "contacts": [
                        {"index": i, "value": value, "included": False}
                        for i, value in enumerate(view.contacts.get(path, ()))
                    ],
                }
                for path, data in view.files.items()
            ],
        }
    with _LOCK:
        _prune()
        pending = _PENDING.get(args["preview"])
    if (
        pending is None or pending.provider is not provider
        or pending.view.scope != (handle.command_center_id, handle.account_id, handle.epoch)
    ):
        raise InvalidBundle("Preview expired or unavailable; preview again")
    return export_zip(
        provider, handle, pending.view, selected=args["selected"],
        named_binaries=args["named_binaries"], included_contacts=args["included_contacts"],
        profile=args["profile"],
    )


async def handle_transfer(request):
    from tinyassets import onboarding
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.onboarding.owner_sessions import require
    from tinyassets.storage_accounting import StorageRefused, visible_record

    if not onboarding.onboarding_enabled():
        return Response(status_code=404, headers=_HEADERS)
    denied = onboarding._app_identity_required()
    if denied is not None:
        denied.headers.update(_HEADERS)
        return denied
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return JSONResponse({"error": "json_required"}, 415, headers=_HEADERS)
    identity = current_identity()
    try:
        await run_in_threadpool(require, request, owner=identity.user_id)
    except PermissionError:
        return JSONResponse({"error": "protected_owner_session_required"}, 403, headers=_HEADERS)
    if not _SLOTS.acquire(blocking=False):
        return JSONResponse({"error": "transfer_busy"}, 503, headers=_HEADERS)
    try:
        raw = await onboarding._read_bounded_body(request, 4 * Limits().compressed // 3 + 4096)
    finally:
        _SLOTS.release()
    if raw is None:
        return JSONResponse({"error": "arguments_too_large"}, 413, headers=_HEADERS)
    try:
        args = _arguments(json.loads(raw))
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse({"error": "invalid_arguments"}, 400, headers=_HEADERS)
    provider, _ = configured_box_provider()
    if provider is None:
        return JSONResponse({"error": "box_provider_unavailable"}, 503, headers=_HEADERS)
    if not _SLOTS.acquire(blocking=False):
        return JSONResponse({"error": "transfer_busy"}, 503, headers=_HEADERS)

    def run():
        with identity_context(identity):
            handle = provider.bind(args["command_center"], account_id=identity.user_id)
            return _transfer(provider, handle, args)

    try:
        result = await run_in_threadpool(run)
    except (BoxAuthError, StaleHandle):
        return JSONResponse({"error": "transfer_not_authorized"}, 403, headers=_HEADERS)
    except WriteConflict:
        return JSONResponse({"error": "source_changed_preview_again"}, 409, headers=_HEADERS)
    except StorageRefused as refused:
        return JSONResponse(visible_record(refused), 413, headers=_HEADERS)
    except InvalidBundle as exc:
        return JSONResponse(
            {"error": "invalid_transfer", "detail": str(exc)}, 400, headers=_HEADERS
        )
    except (BoxError, OSError, BufferError):
        return JSONResponse({"error": "transfer_unavailable"}, 503, headers=_HEADERS)
    finally:
        _SLOTS.release()
    if isinstance(result, bytes):
        headers = dict(_BYTE_HEADERS)
        headers["Content-Disposition"] = 'attachment; filename="command-center.zip"'
        return Response(result, headers=headers)
    return JSONResponse(result, headers=_HEADERS)
