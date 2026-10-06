"""Owner-only personal documents, using the existing bounded file history."""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse

from tinyassets import harness_history as history
from tinyassets.universe_files import read_universe_file

FILES = ("soul.md", "identity.md", "MEMORY.md")


def _read(root, path):
    try:
        return read_universe_file(root, path, max_bytes=history.MAX_PRIOR_BYTES)
    except FileNotFoundError:
        return None


async def handle_soul(request):
    from tinyassets import onboarding as app
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding.owner_sessions import require

    def reply(body, status=200):
        return JSONResponse(body, status_code=status, headers=app._NO_STORE)

    if not app.onboarding_enabled():
        return PlainTextResponse("Not Found", status_code=404)
    denied = app._app_identity_required()
    if denied is not None:
        return denied
    identity = current_identity()
    if request.method == "POST":
        try:
            require(request, owner=identity.user_id)
        except PermissionError:
            return reply({"error": "interactive_approval_required"}, 403)
    home = await run_in_threadpool(app._read_home, identity)
    if not home:
        return reply({"error": "no_home"}, 404)
    if any(request.query_params.get(key, home) != home for key in ("universe", "universe_id")):
        return reply({"error": "not_your_home"}, 403)

    def listing():
        root = _universe_dir(home)
        documents = []
        for path in FILES:
            content = _read(root, path)
            documents.append({"path": path, "text": (content or b"").decode("utf-8"),
                              "revision": history.digest(content)})
        return {"universe_id": home, "documents": documents}

    try:
        if request.method != "POST":
            return reply(await run_in_threadpool(listing))
        data = await app._read_small_json(request, limit=6 * history.MAX_PRIOR_BYTES + 4096)
        if data is None:
            raise ValueError("Invalid JSON")
        if any(data.get(key, home) != home for key in ("universe", "universe_id")):
            return reply({"error": "not_your_home"}, 403)
        path, content = data.get("path"), data.get("text")
        if path not in FILES or not isinstance(content, str) or "\x00" in content:
            raise ValueError("Choose Soul, Identity or Memory and supply text.")
        encoded = content.encode("utf-8")
        if len(encoded) > history.MAX_PRIOR_BYTES:
            raise ValueError("Document is too large to edit here.")

        def save():
            root = _universe_dir(home)
            with history.transaction(root) as conn:
                if data.get("revision") != history.digest(_read(root, path)):
                    raise history.HistoryConflict("This file changed. Reload before saving again.")
                history._write(conn, root, path, encoded, "owner")
            return listing()

        return reply(await run_in_threadpool(save))
    except history.HistoryConflict as exc:
        return reply({"error": "history_conflict", "detail": str(exc)}, 409)
    except (ValueError, TypeError) as exc:
        return reply({"error": "invalid_document", "detail": str(exc)}, 400)
    except OSError:
        return reply({"error": "document_unavailable",
                      "detail": "The document could not be read or saved safely."}, 409)
