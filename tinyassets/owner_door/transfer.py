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
from tinyassets.okf.policy_export import preview_agents
from tinyassets.okf.transfer import Limits, Preview, discover, export_zip, ingest_zip, preview
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
    policy_version: tuple[int, str] | None = None
    structured_digest: str | None = None


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
    if operation == "list":
        if set(args) != {"operation", "command_center"}:
            raise InvalidBundle("List needs only the command center")
    elif operation == "preview":
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
    elif operation in ("import-preview", "import-adopt-preview", "import-activate"):
        key = "request" if operation == "import-activate" else "import_id"
        required = {"operation", "command_center", key}
        if operation == "import-adopt-preview":
            required.add("agent")
            if not isinstance(args.get("agent"), str):
                raise InvalidBundle("Agent adoption needs one agent name")
        if operation == "import-activate":
            required.add("named_binaries")
        if (set(args) != required or not isinstance(args[key], str)
                or not 1 <= len(args[key]) <= 100
                or operation == "import-activate" and not _strings(args["named_binaries"])):
            raise InvalidBundle("Import review needs its exact reference and binary selections")
    else:
        raise InvalidBundle("Unknown transfer operation")
    return args


def _policy(handle):
    from tinyassets.api.helpers import _base_path
    from tinyassets.broker.client import owner_policy_operation

    return owner_policy_operation(_base_path(), handle.account_id, "read")


def _version(policy):
    return policy["generation"], policy["digest"]


def _structured(handle):
    from tinyassets.api.helpers import _base_path
    from tinyassets.okf.workflow_export import capture

    return capture(_base_path(), owner=handle.account_id, center=handle.command_center_id)


def _preview_bytes(view):
    return sum(map(len, view.files.values())) + sum(map(len, view.rules.values()))


def _transfer(provider, handle, args):
    if args["operation"] == "list":
        result = discover(provider, handle)
        captured = _structured(handle)
        if {row["path"] for row in result["files"]} & captured.files.keys():
            raise InvalidBundle("Owner files conflict with generated workflow paths")
        result["files"].extend({"path": path, "size": len(data), "structured": True}
                               for path, data in captured.files.items())
        if len(result["files"]) > Limits().files:
            raise InvalidBundle("Workflow discovery exceeds its bound")
        result["files"].sort(key=lambda row: row["path"])
        return result
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
    if args["operation"] in ("import-preview", "import-adopt-preview", "import-activate"):
        from tinyassets.api.helpers import _base_path
        from tinyassets.okf.import_activation import activate_import, preview_import

        if args["operation"] != "import-activate":
            return preview_import(_base_path(), provider, handle, args["import_id"],
                                  agent=args.get("agent"))
        return activate_import(_base_path(), provider, handle, args["request"],
                               named_binaries=args["named_binaries"])
    if args["operation"] == "preview":
        from tinyassets.okf.workflow_export import PREFIX, SCHEDULES

        captured = _structured(handle) if any(
            path.startswith(PREFIX) or path == SCHEDULES for path in args["paths"]
        ) else None
        policy = _policy(handle) if preview_agents(args["paths"]) else None
        view = preview(provider, handle, args["paths"], policy=policy,
                       structured=captured.files if captured else None)
        pending = Pending(provider, view, time.monotonic() + _TTL,
                          _version(policy) if policy is not None else None,
                          captured.digest if captured else None)
        token = secrets.token_urlsafe(32)
        with _LOCK:
            _prune()
            previous = [t for t, p in _PENDING.items() if p.view.scope[1] == handle.account_id]
            used = sum(
                _preview_bytes(p.view) for t, p in _PENDING.items()
                if t not in previous
            )
            if (
                used + _preview_bytes(view) > _CAPACITY
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
            "rules": [{"path": path, "content": data.decode("utf-8")}
                      for path, data in view.rules.items()],
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
    if pending.policy_version is not None:
        policy = _policy(handle)
        # Validate ownership and shape again before comparing the version.
        from tinyassets.okf.policy_export import portable_rules

        portable_rules(policy, owner=handle.account_id, center=handle.command_center_id, agents={})
        if _version(policy) != pending.policy_version:
            raise WriteConflict("Policy changed after preview; preview again")
    if pending.structured_digest is not None:
        if _structured(handle).digest != pending.structured_digest:
            raise WriteConflict("Workflows changed after preview; preview again")
    return export_zip(
        provider, handle, pending.view, selected=args["selected"],
        named_binaries=args["named_binaries"], included_contacts=args["included_contacts"],
        profile=args["profile"],
    )


async def handle_transfer(request):
    from tinyassets import onboarding
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.command_center_packages import PackageError
    from tinyassets.onboarding.owner_sessions import require
    from tinyassets.storage.current_home import CurrentHomeChanged
    from tinyassets.storage.outbound_connections import ProxyRequestError
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
    except (BoxAuthError, StaleHandle, CurrentHomeChanged):
        return JSONResponse({"error": "transfer_not_authorized"}, 403, headers=_HEADERS)
    except WriteConflict:
        return JSONResponse({"error": "source_changed_preview_again"}, 409, headers=_HEADERS)
    except StorageRefused as refused:
        return JSONResponse(visible_record(refused), 413, headers=_HEADERS)
    except PackageError as exc:
        return JSONResponse({"error": "import_not_ready", "detail": str(exc)},
                            409, headers=_HEADERS)
    except InvalidBundle as exc:
        return JSONResponse(
            {"error": "invalid_transfer", "detail": str(exc)}, 400, headers=_HEADERS
        )
    except (BoxError, OSError, BufferError, ProxyRequestError):
        return JSONResponse({"error": "transfer_unavailable"}, 503, headers=_HEADERS)
    finally:
        _SLOTS.release()
    if isinstance(result, bytes):
        headers = dict(_BYTE_HEADERS)
        headers["Content-Disposition"] = 'attachment; filename="command-center.zip"'
        return Response(result, headers=headers)
    return JSONResponse(result, headers=_HEADERS)
