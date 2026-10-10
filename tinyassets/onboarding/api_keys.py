"""First-party key management: only the protected interactive owner can change grants."""
import json
import secrets
import sqlite3
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require


async def manage(request):
    from tinyassets.api.helpers import _base_path
    from tinyassets.api_keys import current_store
    from tinyassets.onboarding import _read_small_json
    from tinyassets.universe_owner import owned_universes

    try:
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        body = await _read_small_json(request, limit=65536)
        if not isinstance(body, dict):
            raise ValueError("object required")
        operation = body.get("operation")
        fields = {
            "list": {"operation"}, "create": {"operation", "name", "scopes"},
            "update": {"operation", "key_id", "expected_generation", "name", "scopes"},
            "revoke": {"operation", "key_id", "expected_generation"},
        }
        if operation not in fields or set(body) != fields[operation]:
            raise ValueError("invalid management request")
        store = current_store()
        if operation == "list":
            result = {"keys": await run_in_threadpool(store.inspect_keys, owner),
                      "command_centers": await run_in_threadpool(
                          owned_universes, _base_path(), owner)}
        elif operation == "create":
            result = await run_in_threadpool(store.create, owner, body["name"], body["scopes"])
        else:
            result = await run_in_threadpool(
                store.change_key, owner, body["key_id"], body["expected_generation"],
                name=body.get("name"), scopes=body.get("scopes"), revoke=operation == "revoke")
        return JSONResponse(result, headers=HEADERS)
    except PermissionError:
        return JSONResponse({"error": "protected_owner_session_required_or_scope_changed"},
                            status_code=403, headers=HEADERS)
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "invalid_key_request"}, status_code=400, headers=HEADERS)
    except (OSError, sqlite3.Error):
        return JSONResponse({"error": "key_store_unavailable"}, status_code=503, headers=HEADERS)


async def page(request):
    from tinyassets.universe_files import read_data_path

    nonce = secrets.token_urlsafe(24)
    source = read_data_path(Path(__file__).with_name("api_keys.html"))
    if source is None:
        raise FileNotFoundError("API key page is not packaged")
    html = source.decode("utf-8")
    return HTMLResponse(html.replace("{{NONCE}}", nonce), headers={**HEADERS,
        "Content-Security-Policy": "default-src 'none'; style-src 'nonce-" + nonce +
        "'; script-src 'nonce-" + nonce + "'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'", "X-Content-Type-Options": "nosniff"})
