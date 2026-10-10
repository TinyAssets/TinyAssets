"""Owner-answer test transport: real cookie/origin checks, no bearer elevation."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest.mock import patch

from starlette.requests import Request


def session_cookie():
    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding import owner_sessions

    cookie = "test-interactive-owner"
    with owner_sessions.store() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO owner_sessions VALUES (?,?,?)",
            (owner_sessions.hashed(cookie), json.dumps(current_identity().to_dict()), 4102444800),
        )
    return f"{owner_sessions.COOKIE}={cookie}"


def answer_request(*, universe_id="", payload=None, cookie=None, origin="https://tinyassets.io"):
    from tinyassets.onboarding.inline_requests import handle_approval

    document = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    body = json.dumps({**document, "universe_id": universe_id}).encode()
    cookie = session_cookie() if cookie is None else cookie

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": "/app/approvals/answer",
        "path_params": {"operation": "answer"},
        "headers": [(b"origin", origin.encode()), (b"host", b"tinyassets.io"),
                    (b"content-type", b"application/json"), (b"cookie", cookie.encode())],
    }, receive)
    with patch("tinyassets.onboarding.onboarding_enabled", return_value=True), patch(
        "tinyassets.onboarding.app_config", return_value={"resource": "https://tinyassets.io/mcp"}
    ):
        # Playwright's synchronous fixture owns a running event loop. Preserve
        # the actor context while running the real ASGI handler on another thread.
        def run():
            return json.loads(asyncio.run(handle_approval(request)).body)

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(copy_context().run, run).result()


def connect_owner_provider(home, owner):
    """The owner's own connected, serving model source: what live chat turns ran on."""
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    grant, connection = "http_grant_" + "a" * 32, "http_" + "b" * 32
    ledger = ConnectionLedger(home.parent / ".broker" / "outbound.db", data_root=home.parent,
                              verify_authenticated_principal=lambda: owner)
    ledger.create_connection(
        connection_id=connection, owner_user_id=owner, connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http",), provider="http",
        destination="compute:x", credential_ref="vault://http/compute:x",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}])
    ledger.grant_connection(grant_id=grant, connection_id=connection, owner_user_id=owner,
                            universe_id=home.name,
                            unprompted_action_cap=ActionCap("http_requests", 100, "requests"))
    definition = register_definition(
        universe_id=home.name, owner_user_id=owner, access_method="api_key_http",
        protocol="openai_chat", model="moonshotai/kimi-k2", ref=grant)
    served = publish_definition(home.parent, author_id=owner, payload={
        "schema_version": 1, "name": "Served", "components": {
            "identity": {"kind": "soul", "config": {}}}})
    writer = create_binding(home.parent, universe_id=home.name,
                            definition_id=served["agent_definition_id"], created_by=owner,
                            payload={"schema_version": 1, "name": "Served", "role": "writer"})
    connected = bind_serving_provider(
        base_path=home.parent, universe_dir=home, owner_user_id=owner, universe_id=home.name,
        agent_binding_id=writer["agent_binding_id"], expected_revision=1,
        provider=definition.id)
    set_serving(base_path=home.parent, universe_dir=home, owner_user_id=owner,
                universe_id=home.name, agent_binding_id=writer["agent_binding_id"],
                expected_revision=connected["agent_binding"]["revision"], enabled=True)

