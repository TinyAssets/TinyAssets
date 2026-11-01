"""The owner's outside clients and their grants (P0-3, decision A1), router-served.

POST /app/outside-clients with {"operation": "list"}, or {"operation": "set",
"client", "mode", "scopes", "expected_generation"}: ``mode`` is ``full``,
``scoped`` (exact ``{universe, agent, capability}`` scopes) or ``revoked``. A
grant changes only from the owner's protected session, on the router's owner
channel to the broker, and takes effect on the client's next call. A new MCP
client starts ``full``; bearer calls and boxes cannot change a grant.
"""
import json

from starlette.responses import JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require


async def handle(request):
    from starlette.concurrency import run_in_threadpool

    from tinyassets.onboarding import _read_small_json
    from tinyassets.owner_runtime import current

    try:
        if current() is not None:
            raise PermissionError("outside grants are served on the trusted side")
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        data = await _read_small_json(request, limit=65536)
        result = await run_in_threadpool(
            _operation, owner, {"session_hash": session["session_hash"]}, data)
        return JSONResponse(result, headers=HEADERS)
    except RuntimeError:
        return JSONResponse({"error": "outside_grant_unavailable"}, status_code=503,
                            headers=HEADERS)
    except (PermissionError, ValueError, KeyError, TypeError):
        return JSONResponse({"error": "outside_grant_refused"}, status_code=403, headers=HEADERS)


def _operation(owner: str, proof: dict, data: object) -> dict:
    from tinyassets.broker.policy_submit import submit
    from tinyassets.storage import data_dir

    root = data_dir()
    if data == {"operation": "list"}:
        return submit(root, owner, "outside_list")
    if not isinstance(data, dict) or data.get("operation") != "set" or set(data) != {
            "operation", "client", "mode", "scopes", "expected_generation"}:
        raise ValueError("invalid outside grant request")
    known = {row["client"]: row for row in submit(root, owner, "outside_list")["clients"]}
    if data["client"] not in known:
        raise PermissionError("unknown outside client")
    return submit(root, owner, "outside_set", session_proof=proof, client=data["client"],
                  source=known[data["client"]]["source"], mode=data["mode"],
                  scopes=data["scopes"], expected_generation=data["expected_generation"],
                  name="")
