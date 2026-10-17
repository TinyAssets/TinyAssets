"""Browser test transport for credentials: shared door, then box ledger polling."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest.mock import patch

from starlette.requests import Request


def post(operation, body):
    from tests.owner_answer import session_cookie
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.onboarding.model_connect import handle_model_connect

    base = _base_path()
    center = body["center"]
    # Existing owner fixtures describe their admission with an admin ACL.
    # Translate that setup into broker admission before entering the router.
    admins = [row["actor_id"] for row in list_universe_acl(base, universe_id=center)
              if row["permission"] == "admin"]
    if admins:
        seed_owner_admission(base, principal=admins[0], center=center)
        from tinyassets.broker.owner_identities import OwnerIdentities
        from tinyassets.broker.supervisor import get_supervisor

        broker = get_supervisor(base)
        # A rail read may already have started this fixture's broker.
        if hasattr(broker, "server"):
            broker.server._owner_identities = OwnerIdentities(
                base / ".broker" / "owner-identities.db")
    raw = json.dumps(body).encode()
    from tinyassets.api.permissions import is_authenticated_request
    cookie = session_cookie() if is_authenticated_request() else ""

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": "/app/model-connect/" + operation,
        "path_params": {"operation": operation}, "query_string": b"",
        "headers": [(b"origin", b"https://tinyassets.io"), (b"host", b"tinyassets.io"),
                    (b"content-type", b"application/json"), (b"cookie", cookie.encode())],
    }, receive)
    with patch("tinyassets.onboarding.onboarding_enabled", return_value=True), patch(
        "tinyassets.onboarding.app_config", return_value={"resource": "https://tinyassets.io/mcp"}
    ):
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(copy_context().run,
                lambda: json.loads(asyncio.run(handle_model_connect(request)).body)).result()


def connect_http(*, universe_id="", payload=None):
    from tinyassets.api.http_connection import connect_http as box_connect

    document = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    if document.get("auth_scheme") == "none":
        return box_connect(universe_id=universe_id, payload=document)
    secret = document.pop("secret", "")
    ref = document.pop("request_ref", "")
    endpoints = document.pop("allowed_endpoints", [])
    return post("deposit", {"center": universe_id, "request_ref": ref or "fixture-request",
        "secret": secret, "definition": {**document, "endpoints": endpoints}})


def rotate_http(*, universe_id="", payload=None):
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.http_connection import _ids
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.ledger_queries import granted_resource_row

    document = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    _, grant = _ids(universe_id=universe_id, destination=document["destination"])
    from tinyassets.storage.outbound_connections import GrantResolutionError

    try:
        row = granted_resource_row(_base_path(), principal=current_identity().user_id,
                                   command_center=universe_id, grant_id=grant)
    except GrantResolutionError:
        row = {"incarnation": "missing"}
    return post("rotate", {"center": universe_id, **document,
                          "incarnation": document.get("incarnation", row["incarnation"])})


def answer_connect(universe_id, row, values):
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.pending_requests import reconcile_connections
    from tinyassets.storage.pending_requests import get_request

    action = row["action"]
    known = {field["name"] for field in row["fields"]}
    if set(values) - known:
        return {"error": "invalid_connection_fields"}
    supplied = {k: v for k, v in values.items() if isinstance(v, str) and v.strip()}
    secret = (next(iter(supplied.values())) if len(supplied) == 1
              else json.dumps(supplied) if supplied else "")
    if action["auth_scheme"] == "basic" and "username" in supplied:
        secret = supplied.get("username", "") + ":" + supplied.get("password", "")
    common = {"center": universe_id, "request_ref": row["request_id"], "secret": secret}
    if action["type"] == "rotate_http":
        result = post("rotate", {**common, "destination": action["destination"],
                                "incarnation": action["incarnation"]})
    else:
        result = post("deposit", {**common, "definition": {
            "destination": action["destination"], "auth_scheme": action["auth_scheme"],
            "endpoints": action["endpoints"], "access": action.get("access", "exact"),
            "scopes": action.get("scopes", []), "git_host": action.get("git_host", "")}})
    if result.get("error"):
        return {**result, "request_pending": True}
    home = _base_path() / universe_id
    completed = reconcile_connections(universe_id, home).get(row["request_id"], {})
    return {**result, **get_request(home, row["request_id"]), **completed}
