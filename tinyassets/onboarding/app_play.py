"""HTTP surface for one app that lives in its owner's box.

Visiting players arrive only after the router has checked the published slug
and attached their authenticated account. The app's room is not here: the router
answers it from the publisher's broker store (``room_door``).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from tinyassets.owner_runtime import current


def _caller(request: Request, owner: str) -> str:
    caller = request.scope.get("tinyassets_caller_user_id") or owner
    if not isinstance(caller, str) or not caller:
        raise ValueError("caller required")
    return caller


def _failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, FileNotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)
    if isinstance(exc, PermissionError):
        return JSONResponse({"error": "not_published"}, status_code=403)
    if isinstance(exc, ValueError):
        return JSONResponse({"error": "invalid"}, status_code=400)
    raise exc


async def handle_app_document(request: Request) -> JSONResponse:
    from tinyassets.app_room import read_app, write_app

    runtime = current()
    slug = request.path_params["slug"]
    caller = _caller(request, runtime.principal)
    try:
        if request.method == "POST":
            if caller != runtime.principal:
                raise PermissionError("app is not published")
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {"name", "markup"}:
                raise ValueError("invalid app document")
            document = write_app(
                runtime.root, slug, name=body.get("name", ""),
                markup=body.get("markup", ""),
            )
        else:
            document = read_app(
                runtime.root, slug, caller=caller, owner=runtime.principal,
            )
    except Exception as exc:
        return _failure(exc)
    return JSONResponse(document)


def request_publication(payload: dict) -> dict:
    """Ask the broker to copy the route binding out. The box does not write it."""
    from tinyassets.owner_channel import request

    return request("", {
        "op": "BOX_PUBLISH",
        "payload": payload,
        "idempotency_key": "app:" + str(payload.get("slug", "")),
    })


async def handle_app_publish(request: Request) -> JSONResponse:
    from tinyassets.app_room import _load, mark_published

    runtime = current()
    slug = request.path_params["slug"]
    caller = _caller(request, runtime.principal)
    if caller != runtime.principal:
        return JSONResponse({"error": "private_surface"}, status_code=403)
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"published"}:
            raise ValueError("invalid app publication")
        document = _load(runtime.root, slug)
        if body["published"] is True:
            request_publication({
                "kind": "published_app", "slug": slug, "name": document["name"],
            })
            saved = mark_published(runtime.root, slug, published=True)
        elif body["published"] is False:
            if document.get("published") is True:
                request_publication({
                    "kind": "published_app_withdrawal", "slug": slug,
                })
            saved = mark_published(runtime.root, slug, published=False)
        else:
            raise ValueError("invalid app publication")
    except Exception as exc:
        return _failure(exc)
    return JSONResponse(saved)
