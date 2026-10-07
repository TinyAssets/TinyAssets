"""Protected owner control of outside grants; bearer calls cannot approve them."""
import json
import sqlite3

from starlette.responses import JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require


async def handle(request):
    from tinyassets.onboarding import _read_small_json
    from tinyassets.outside_authority import current_store

    try:
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        data = await _read_small_json(request, limit=65536)
        if not isinstance(data, dict):
            raise ValueError("invalid outside grant request")
        store = current_store()
        if data.get("operation") == "list" and set(data) == {"operation"}:
            result = store.inspect(owner)
        else:
            if set(data) != {"operation", "client", "family", "scopes", "expected_generation"}:
                raise ValueError("invalid outside grant fields")
            if data["operation"] not in {"grant", "revoke"}:
                raise ValueError("invalid outside grant operation")
            from tinyassets.api.helpers import _base_path
            from tinyassets.daemon_server import get_founder_home, universe_access_permission

            for scope in data["scopes"]:
                uid = scope["universe"]
                if (get_founder_home(_base_path(), owner) != uid and universe_access_permission(
                        _base_path(), universe_id=uid, actor_id=owner) != "admin"):
                    raise PermissionError("outside grants require current owner authority")
            result = store.change(owner, data["client"], family=data["family"],
                                  scopes=data["scopes"],
                                  expected_generation=data["expected_generation"],
                                  revoke=data["operation"] == "revoke")
        return JSONResponse(result, headers=HEADERS)
    except (PermissionError, ValueError, KeyError, TypeError, sqlite3.Error):
        return JSONResponse({"error": "outside_grant_refused"}, status_code=403, headers=HEADERS)
