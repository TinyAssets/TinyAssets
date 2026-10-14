"""Protected owner control of REST API keys; no bearer can mint or revoke one.

POST /app/api-keys with one of:
  {"operation": "list"}
  {"operation": "create", "name": str, "scopes": [{"universe", "agent", "capability"}]}
  {"operation": "revoke", "key_id": str}
"""
import json
import sqlite3

from starlette.responses import JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require
from tinyassets.owner_control import ControlUnavailable


async def handle(request):
    from tinyassets.api_keys import ApiKeyRefused
    from tinyassets.api_keys import current_store as key_store
    from tinyassets.onboarding import _read_small_json
    from tinyassets.outside_authority import current_store as outside_store

    try:
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        data = await _read_small_json(request, limit=65536)
        if not isinstance(data, dict):
            raise ValueError("invalid API key request")
        keys, authority = key_store(), outside_store()
        operation = data.get("operation")
        if operation in {"request_preview", "request_create", "request_revoke", "request_finish",
                         "request_decline",
                         "owner_list", "owner_revoke"}:
            from tinyassets.api.credential_requests import owner_operation
            from tinyassets.auth.middleware import current_identity

            identity = current_identity()
            if identity is None or identity.user_id != owner:
                raise PermissionError("Owner session and app account must match")
            if operation == "owner_list" and set(data) == {"operation"}:
                result = keys.list(owner, authority=authority)
            elif operation == "owner_revoke" and set(data) == {"operation", "key_id"}:
                result = keys.revoke(owner, str(data["key_id"]), authority=authority)
            else:
                result = owner_operation(owner, data, keys, authority)
        elif operation == "list" and set(data) == {"operation"}:
            result = keys.list(owner, authority=authority)
        elif operation == "revoke" and set(data) == {"operation", "key_id"}:
            result = keys.revoke(owner, str(data["key_id"]), authority=authority)
        elif operation == "create" and set(data) == {"operation", "name", "scopes"}:
            from tinyassets.api.helpers import _base_path
            from tinyassets.daemon_server import get_founder_home, universe_access_permission

            if not isinstance(data["scopes"], list):
                raise ValueError("scopes must be a list")
            for scope in data["scopes"]:
                uid = scope["universe"]
                if (get_founder_home(_base_path(), owner) != uid and universe_access_permission(
                        _base_path(), universe_id=uid, actor_id=owner) != "admin"):
                    raise PermissionError("API key grants require current owner authority")
            result = keys.mint(owner, name=data["name"], scopes=data["scopes"],
                               authority=authority)
        else:
            raise ValueError("invalid API key operation")
        return JSONResponse(result, headers=HEADERS)
    except ControlUnavailable:
        return JSONResponse({"error": "api_key_busy"}, status_code=503, headers=HEADERS)
    except (ApiKeyRefused, PermissionError, ValueError, KeyError, TypeError, sqlite3.Error):
        return JSONResponse({"error": "api_key_refused"}, status_code=403, headers=HEADERS)
