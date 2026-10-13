"""Origin-ingress admission backstop.

OpenSpec change ``cloud-only-runtime-admission``, enforcement site (D). This is
the outermost ASGI wrapper on the real HTTP app: an origin that is not an
admitted cloud runtime refuses every request before it reaches any handler —
before the auth middleware, before discovery, before the MCP transport and
before any universe work.

**It is a backstop, not the prevention.** A refusing off-cloud origin has
already absorbed the public request; prevention is the removed in-repo
connector-enrollment path (PR #3913) plus tunnel-token custody, neither of which
this file can establish. Nothing here is attestation.

Three properties make it safe to sit in front of every request:

* **cached-only.** It reads the non-mutating process peek. No resolver, no
  socket, no metadata read, no storage — so a request can never become the thing
  that decides provenance, and a flood of requests cannot amplify into probes.
* **fail-closed on unknown.** "Nothing observed" is not a pass. An app built
  without ever entering its serving lifespan (so nothing resolved anything)
  refuses, exactly like an observed ``not_cloud``.
* **it widens nothing.** It only ever removes reachability. Authentication,
  principal resolution and every per-route permission behave exactly as before
  for an admitted process; there is no allowlisted path, hostname, environment
  variable or boot id that bypasses it, and no production escape hatch.
"""

from __future__ import annotations

from typing import Any

from tinyassets.platform_runtime_provenance import (
    PLATFORM_NOT_CLOUD_REASON,
    cached_process_is_cloud_admitted,
)

#: 503, not 403 and not 500: the refusal is a property of *this process* — it is
#: not an admitted platform runtime — rather than of the caller's credential, and
#: it is a deliberate answer rather than a crash. A caller (or Cloudflare) may
#: retry against an admitted origin.
PLATFORM_NOT_CLOUD_STATUS = 503

#: The whole body. A stable sanitized token and nothing else: no instance id, no
#: expected id, no metadata address, no hostname, no principal, no universe id
#: and no release state. An unadmitted origin must not become a side channel for
#: the facts it is refusing over.
_REFUSAL_BODY = b'{"error":"' + PLATFORM_NOT_CLOUD_REASON.encode("ascii") + b'"}'

_REFUSAL_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_REFUSAL_BODY)).encode("ascii")),
    (b"cache-control", b"no-store"),
]

#: Internal error, not a policy close: the peer is told the origin could not
#: serve it, in the only vocabulary a websocket handshake has.
_WEBSOCKET_CLOSE_CODE = 1011


class PlatformOriginAdmission:
    """Refuse every origin request an unadmitted process would otherwise serve."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def __getattr__(self, name: str) -> Any:
        # Transparent to `app.state`, `app.routes` and the rest of the Starlette
        # surface the callers of create_streamable_http_app() already use.
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            # `lifespan` passes through: the serving lifespan is where admission
            # is *resolved*. Refusing it here would deny the app the chance to
            # observe, and would turn an unadmitted boot into a hang rather than
            # the loud non-zero startup failure it is supposed to be.
            await self.app(scope, receive, send)
            return

        if cached_process_is_cloud_admitted():
            await self.app(scope, receive, send)
            return

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": _WEBSOCKET_CLOSE_CODE})
            return

        await send(
            {
                "type": "http.response.start",
                "status": PLATFORM_NOT_CLOUD_STATUS,
                "headers": _REFUSAL_HEADERS,
            }
        )
        await send({"type": "http.response.body", "body": _REFUSAL_BODY})
