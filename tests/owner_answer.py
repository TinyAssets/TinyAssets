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
