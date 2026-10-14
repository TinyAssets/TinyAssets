"""REST transport (``/api``) over the same logic the MCP handles run.

For programmatic agents, scripts and integrations that have no MCP client and
cannot do the connector OAuth flow. Authentication is a per-user API key
(``tinyassets.api_keys``); the auth middleware has already resolved it into a
confined outside identity before any route here runs.

Every route names its command center and the MCP handle it runs (its
capability). The key's exact ``{universe, agent, capability}`` grant is checked
first; then the route calls the very function the handle calls, under the same
identity, so ownership gates, run capture and effect admission are unchanged.
No route adds a gate of its own or bypasses one.
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

HEADERS = {"Cache-Control": "no-store"}
MAX_BODY = 262144
_NOT_FOUND = {"not_found", "Run not found.", "Branch not found.", "Branch version not found."}


def _respond(raw: Any) -> JSONResponse:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {"text": raw}
    if isinstance(raw, dict) and raw.get("error"):
        error = str(raw["error"])
        status = 404 if error in _NOT_FOUND or error.lower().endswith("not found.") else 400
        return JSONResponse(raw, status_code=status, headers=HEADERS)
    return JSONResponse(raw, headers=HEADERS)


def _error(status: int, code: str, detail: str = "") -> JSONResponse:
    body = {"error": code, **({"detail": detail} if detail else {})}
    return JSONResponse(body, status_code=status, headers=HEADERS)


def _limit(request) -> int | None:
    raw = request.query_params.get("limit", "30")
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 100 else None


async def _body(request) -> dict[str, Any] | None:
    """The JSON object body, or ``None`` for anything malformed, oversized or cut off.

    Every failure is the caller's request, so it is a 400 here, never a 500."""
    from tinyassets.onboarding import _read_small_json

    try:
        return await _read_small_json(request, limit=MAX_BODY)
    except Exception:  # noqa: BLE001 - a body we cannot read is a bad request
        return None


def _api_key_identity():
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None or identity.metadata.get("auth_provider") != "api_key":
        return None
    return identity


async def _dispatch(request, *, capability: str, universe: str, fn, agent: str = "main"):
    """Admit the key for exactly (universe, agent, capability), then run ``fn``."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.outside_authority import check_identity

    identity = _api_key_identity()
    if identity is None:
        return _error(401, "api_key_required")
    resource = {"universe": universe, "agent": agent or "main", "capability": capability}
    try:
        check_identity(identity, **resource)
    except PermissionError:
        return _error(403, "capability_not_granted",
                      f"this key does not hold {capability} on {universe}")
    identity.metadata["outside_resource"] = resource

    def _run():
        with identity_context(identity):
            return fn(universe)

    try:
        return _respond(await run_in_threadpool(_run))
    except PermissionError:
        return _error(403, "forbidden")
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_request", str(exc)[:200])


def _read(target: str, **arguments):
    """The function the ``read_graph`` MCP handle runs (the model door), not the owner's."""
    def read(universe):
        from tinyassets.universe_server import read_graph

        return read_graph(target=target, graph_id=universe, **arguments)

    return read


async def list_command_centers(request):
    """Only the command centers this key holds ``read_graph`` on.

    A listing names no single command center, so no one grant can admit it.
    The key must be live; the owner's listing is then cut to the command
    centers where the key's exact ``read_graph`` grant is admitted, one by one.
    Nothing outside the grant -- not even its existence -- is returned."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.outside_authority import check_identity

    limit = _limit(request)
    if limit is None:
        return _error(400, "invalid_limit", "limit must be 1-100")
    identity = _api_key_identity()
    if identity is None:
        return _error(401, "api_key_required")
    try:
        check_identity(identity)
    except PermissionError:
        return _error(401, "invalid_api_key")

    def granted(universe: str) -> bool:
        try:
            check_identity(identity, universe=universe, agent="main", capability="read_graph")
        except PermissionError:
            return False
        return True

    def listing():
        with identity_context(identity):
            # The whole listing (the handle returns every visible command
            # center, unpaged); grant-filter first, then cut to ``limit``.
            document = json.loads(_read("graphs")(""))
            rows = [row for row in document.get("universes") or []
                    if isinstance(row, dict) and granted(str(row.get("id") or ""))]
        return {"universes": rows[:limit], "count": len(rows[:limit])}

    try:
        return _respond(await run_in_threadpool(listing))
    except PermissionError:
        return _error(403, "forbidden")
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_request", str(exc)[:200])


async def read_command_center(request):
    return await _dispatch(request, capability="read_graph", universe=request.path_params["cc"],
                           fn=_read("graph"))


async def read_status(request):
    return await _dispatch(request, capability="read_graph", universe=request.path_params["cc"],
                           fn=_read("status"))


async def list_connections(request):
    return await _dispatch(request, capability="read_graph", universe=request.path_params["cc"],
                           fn=_read("connections"))


async def runs(request):
    cc = request.path_params["cc"]
    if request.method == "GET":
        limit = _limit(request)
        if limit is None:
            return _error(400, "invalid_limit", "limit must be 1-100")
        return await _dispatch(request, capability="read_graph", universe=cc, fn=_read(
            "runs", limit=limit, run_status=request.query_params.get("status", "")))
    data = await _body(request)
    if (data is None or not isinstance(data.get("branch_def_id"), str)
            or not data["branch_def_id"] or set(data) - {"branch_def_id", "inputs", "run_name"}
            or not isinstance(data.get("inputs", {}), dict)
            or not isinstance(data.get("run_name", ""), str)):
        return _error(400, "invalid_request",
                      "body is {branch_def_id, inputs?: object, run_name?: string}")

    def start(universe):
        from tinyassets.universe_server import run_graph

        return run_graph(branch_def_id=data["branch_def_id"],
                         inputs_json=json.dumps(data.get("inputs", {})),
                         run_name=data.get("run_name", ""), graph_id=universe)

    return await _dispatch(request, capability="run_graph", universe=cc, fn=start)


async def read_run(request):
    return await _dispatch(request, capability="read_graph", universe=request.path_params["cc"],
                           fn=_read("run", run_id=request.path_params["run_id"]))


async def read_run_output(request):
    """The existing bounded MCP output reader, under the same per-center grant."""
    from tinyassets.api.run_outputs import DEFAULT_OUTPUT_CHARS, MAX_OUTPUT_CHARS

    query = request.query_params
    allowed = {"field_name", "output_offset", "output_max_chars"}
    if set(query) - allowed or any(len(query.getlist(key)) != 1 for key in query):
        return _error(400, "invalid_request", "unknown or duplicate output query parameter")
    numbers = {}
    for key, default in (("output_offset", 0), ("output_max_chars", DEFAULT_OUTPUT_CHARS)):
        raw = query.get(key, str(default))
        if not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
            return _error(400, "invalid_request", key + " must be an ASCII decimal integer")
        numbers[key] = int(raw)
    if not 1 <= numbers["output_max_chars"] <= MAX_OUTPUT_CHARS:
        return _error(400, "invalid_request", f"output_max_chars must be 1-{MAX_OUTPUT_CHARS}")
    return await _dispatch(
        request, capability="read_graph", universe=request.path_params["cc"],
        fn=_read("run_output", run_id=request.path_params["run_id"],
                 field_name=query.get("field_name", ""), **numbers))


async def cancel_run(request):
    def cancel(universe):
        from tinyassets.universe_server import run_graph

        return run_graph(operation="cancel", run_id=request.path_params["run_id"],
                         graph_id=universe)

    return await _dispatch(request, capability="run_graph", universe=request.path_params["cc"],
                           fn=cancel)


async def send_message(request):
    """One agent turn, exactly as ``converse``; the reply returns when it ends."""
    data = await _body(request)
    if (data is None or not isinstance(data.get("message"), str) or not data["message"].strip()
            or set(data) - {"message", "agent_id"}
            or not isinstance(data.get("agent_id", ""), str)):
        return _error(400, "invalid_request", "body is {message: string, agent_id?: string}")
    agent = data.get("agent_id") or "main"

    def turn(universe):
        from tinyassets.auth.middleware import (
            claim_provider_request,
            current_identity,
            reserve_provider_request,
            revoke_provider_request,
        )
        from tinyassets.universe_server import converse

        identity = current_identity()
        # The same one-request provider authority the MCP middleware mints.
        reserve = reserve_provider_request(
            principal_id=identity.user_id,
            session_id=f"rest:{identity.metadata['api_key_id']}",
            request_id=secrets.token_hex(16), tool_name="converse")
        capability = claim_provider_request(reserve, tool_name="converse")
        try:
            return converse(message=data["message"], graph_id=universe,
                            agent_id="" if agent == "main" else agent, input_method="unknown")
        finally:
            revoke_provider_request(capability)

    return await _dispatch(request, capability="converse", universe=request.path_params["cc"],
                           fn=turn, agent=agent)


def rest_api_routes() -> list[Any]:
    from starlette.routing import Route

    base = "/api/command-centers"
    return [
        Route(base, list_command_centers, methods=["GET"]),
        Route(base + "/{cc}", read_command_center, methods=["GET"]),
        Route(base + "/{cc}/status", read_status, methods=["GET"]),
        Route(base + "/{cc}/connections", list_connections, methods=["GET"]),
        Route(base + "/{cc}/runs", runs, methods=["GET", "POST"]),
        Route(base + "/{cc}/runs/{run_id}", read_run, methods=["GET"]),
        Route(base + "/{cc}/runs/{run_id}/output", read_run_output, methods=["GET"]),
        Route(base + "/{cc}/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route(base + "/{cc}/messages", send_message, methods=["POST"]),
    ]
