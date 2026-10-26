"""The one refusal both owner doors give a scope that is not HTTP or lifespan.

No door forwards a websocket: the proposed real-time relay (ADR-027) is the only
socket path, and it never reaches a box. Shared by the router and the box
ingress, so it imports nothing from either.
"""

#: RFC 6455 policy violation. Sent before accept, so the server rejects the
#: handshake itself (uvicorn answers 403); the code never reaches the client.
WEBSOCKET_REFUSED = 1008


async def refuse_non_http(scope, send):
    """Close a websocket before any handler runs; fail loudly on other types."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": WEBSOCKET_REFUSED})
        return
    raise RuntimeError(f"unsupported ASGI scope type: {scope['type']!r}")
