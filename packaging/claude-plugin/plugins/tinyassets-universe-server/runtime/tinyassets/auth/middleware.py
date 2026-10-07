"""Auth middleware for the TinyAssets Server MCP.

Provides request-level auth resolution that works with FastMCP's
tool execution model. Since FastMCP tools are plain functions (not
HTTP handlers), auth is resolved via a context pattern set by the
HTTP transport layer before tool execution.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import weakref
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from tinyassets.auth.provider import (
    CANARY,
    AuthProvider,
    Identity,
    PermissionAction,
    PermissionContext,
    PermissionScope,
    action_scope_for,
    create_provider,
)
from tinyassets.auth.wiki_canary import (
    canary_request_allowed,
    is_exact_wiki_canary_request,
    reset_wiki_canary_authority,
    set_wiki_canary_authority,
    wiki_canary_token_matches,
)

logger = logging.getLogger("universe_server.auth")

# Request-local storage for per-request identity. ContextVar is required
# because Streamable HTTP handlers run concurrently on the same event-loop
# thread; thread-local storage would leak actors between async requests.
# None means "no principal bound". There is no synthetic identity to fall back
# to (founder, 2026-09-02): code that needs an actor calls current_identity(),
# which raises, and the transport has already answered 401 to any request that
# would reach it unbound.
_current_identity: ContextVar[Identity | None] = ContextVar(
    "workflow_current_identity",
    default=None,
)
_current_bearer_present: ContextVar[bool] = ContextVar(
    "tinyassets_current_bearer_present",
    default=False,
)
_current_request_boundary_id: ContextVar[str | None] = ContextVar(
    "tinyassets_current_request_boundary_id",
    default=None,
)
_current_provider_request: ContextVar[ProviderRequestCapability | None] = ContextVar(
    "tinyassets_current_provider_request",
    default=None,
)
_current_provider_reserve: ContextVar[ProviderRequestReserve | None] = ContextVar(
    "tinyassets_current_provider_reserve",
    default=None,
)

_PROVIDER_REQUEST_MECHANISM = "tinyassets.authenticated-request.v1"
_PROVIDER_REQUEST_ISSUER = "tinyassets.auth.middleware"
_PROVIDER_REQUEST_LOCK = threading.Lock()
_PROVIDER_REQUESTS: dict[str, dict[str, Any]] = {}


class ProviderRequestReserve:
    """One server-issued dispatch reserve; inert until its worker claims it."""

    __slots__ = ("_nonce", "_identity_token", "_issuer_pid", "__weakref__")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("provider request reserves are server-issued")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("provider request reserves are immutable")

    def __reduce__(self):
        raise TypeError("provider request reserves are non-serializable")


class ProviderRequestCapability:
    """A live, one-request provider capability claimed by the tool worker."""

    __slots__ = (
        "principal_id",
        "session_id",
        "request_id",
        "tool_name",
        "mechanism",
        "issuer",
        "_nonce",
        "_identity_token",
        "_issuer_pid",
        "_worker_id",
        "_context_token",
        "__weakref__",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("provider request capabilities are server-issued")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("provider request capabilities are immutable")

    def __reduce__(self):
        raise TypeError("provider request capabilities are non-serializable")


class ProviderRequestCarrier:
    """Sealed request authority threaded explicitly through provider workers."""

    __slots__ = (
        "universe_id",
        "agent_binding_id",
        "binding_revision",
        "operation",
        "_nonce",
        "_identity_token",
        "_issuer_pid",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("provider request carriers are server-issued")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("provider request carriers are immutable")

    def __reduce__(self):
        raise TypeError("provider request carriers are non-serializable")


def _reset_provider_request_state_after_fork() -> None:
    global _PROVIDER_REQUEST_LOCK, _PROVIDER_REQUESTS
    _PROVIDER_REQUEST_LOCK = threading.Lock()
    _PROVIDER_REQUESTS = {}
    _current_provider_request.set(None)
    _current_provider_reserve.set(None)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_provider_request_state_after_fork)


def _discard_unclaimed_provider_request(nonce: str, issuer_pid: int) -> None:
    if issuer_pid != os.getpid():
        return
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(nonce)
        if record is not None and record["state"] == "reserved":
            _PROVIDER_REQUESTS.pop(nonce, None)


def reserve_provider_request(
    *,
    principal_id: str,
    session_id: str,
    request_id: str,
    tool_name: str,
    mechanism: str = _PROVIDER_REQUEST_MECHANISM,
    issuer: str = _PROVIDER_REQUEST_ISSUER,
) -> ProviderRequestReserve:
    """Reserve one exact authenticated dispatch before worker selection."""

    fields = {
        "principal_id": principal_id,
        "session_id": session_id,
        "request_id": request_id,
        "tool_name": tool_name,
        "mechanism": mechanism,
        "issuer": issuer,
    }
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError("provider request fields must be non-empty strings")
    nonce = secrets.token_hex(32)
    identity_token = object()
    issuer_pid = os.getpid()
    reserve = object.__new__(ProviderRequestReserve)
    object.__setattr__(reserve, "_nonce", nonce)
    object.__setattr__(reserve, "_identity_token", identity_token)
    object.__setattr__(reserve, "_issuer_pid", issuer_pid)
    with _PROVIDER_REQUEST_LOCK:
        _PROVIDER_REQUESTS[nonce] = {
            **{key: value.strip() for key, value in fields.items()},
            "identity_token": identity_token,
            "issuer_pid": issuer_pid,
            "state": "reserved",
            "worker_id": None,
            "capability_ref": None,
            "invocations": 0,
            "launch_limit": None,
        }
    weakref.finalize(reserve, _discard_unclaimed_provider_request, nonce, issuer_pid)
    return reserve


def claim_provider_request(
    reserve: ProviderRequestReserve,
    *,
    tool_name: str,
) -> ProviderRequestCapability:
    """Claim an inert reserve exactly once in the actual tool worker."""

    if type(reserve) is not ProviderRequestReserve or reserve._issuer_pid != os.getpid():
        raise PermissionError("provider request reserve is not server-issued")
    worker_id = threading.get_ident()
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(reserve._nonce)
        if record is None or record["identity_token"] is not reserve._identity_token:
            raise PermissionError("provider request reserve is invalid")
        if record["state"] != "reserved":
            raise PermissionError("provider request reserve was already claimed or revoked")
        if record["tool_name"] != tool_name:
            raise PermissionError("provider request reserve belongs to another tool")
        capability = object.__new__(ProviderRequestCapability)
        for name in (
            "principal_id",
            "session_id",
            "request_id",
            "tool_name",
            "mechanism",
            "issuer",
        ):
            object.__setattr__(capability, name, record[name])
        object.__setattr__(capability, "_nonce", reserve._nonce)
        object.__setattr__(capability, "_identity_token", reserve._identity_token)
        object.__setattr__(capability, "_issuer_pid", os.getpid())
        object.__setattr__(capability, "_worker_id", worker_id)
        record["state"] = "claimed"
        record["worker_id"] = worker_id
        record["capability_ref"] = weakref.ref(capability)
    context_token = _current_provider_request.set(capability)
    object.__setattr__(capability, "_context_token", context_token)
    return capability


def set_provider_request_reserve(
    reserve: ProviderRequestReserve,
) -> Token[ProviderRequestReserve | None]:
    """Publish one inert middleware reserve to the actual tool worker."""

    if type(reserve) is not ProviderRequestReserve or reserve._issuer_pid != os.getpid():
        raise PermissionError("provider request reserve is not server-issued")
    return _current_provider_reserve.set(reserve)


def reset_provider_request_reserve(
    token: Token[ProviderRequestReserve | None],
) -> None:
    _current_provider_reserve.reset(token)


def provider_request_reserve() -> ProviderRequestReserve | None:
    return _current_provider_reserve.get()


def cancel_provider_request_reserve(reserve: ProviderRequestReserve) -> None:
    """Revoke an unclaimed reserve when dispatch does not reach a tool worker."""

    if type(reserve) is not ProviderRequestReserve or reserve._issuer_pid != os.getpid():
        raise PermissionError("provider request reserve is not server-issued")
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(reserve._nonce)
        if record is not None and record["identity_token"] is reserve._identity_token:
            if record["state"] == "reserved":
                _PROVIDER_REQUESTS.pop(reserve._nonce, None)


def _active_provider_request(capability: ProviderRequestCapability) -> dict[str, Any]:
    if type(capability) is not ProviderRequestCapability:
        raise PermissionError("provider request capability is not server-issued")
    if capability._issuer_pid != os.getpid():
        raise PermissionError("provider request capability belongs to another process")
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(capability._nonce)
        exact = (
            record is not None,
            record is not None and record["state"] == "claimed",
            record is not None and record["identity_token"] is capability._identity_token,
            record is not None and record["capability_ref"]() is capability,
        )
        if not all(exact):
            raise PermissionError("provider request capability is revoked")
        return dict(record)


def provider_request_capability() -> ProviderRequestCapability | None:
    """Return the exact live capability only in its claimed tool worker."""

    capability = _current_provider_request.get()
    if capability is None:
        return None
    record = _active_provider_request(capability)
    if record["worker_id"] != threading.get_ident():
        return None
    return capability


def revoke_provider_request(capability: ProviderRequestCapability) -> None:
    """Synchronously revoke a request lease before result release."""

    if type(capability) is not ProviderRequestCapability:
        raise PermissionError("provider request capability is not server-issued")
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(capability._nonce)
        if record is not None and record["identity_token"] is capability._identity_token:
            _PROVIDER_REQUESTS.pop(capability._nonce, None)
    if _current_provider_request.get() is capability:
        _current_provider_request.reset(capability._context_token)


def seal_provider_request_launch_allowance(
    capability: ProviderRequestCapability,
    *,
    limit: int,
) -> int:
    """Seal the validated turn plan's finite launch allowance exactly once.

    Only the trusted routing boundary calls this, after validating its candidate
    plan. It is not a preference setter or authority to use any provider. Legacy
    requests remain unsealed and retain the caller's existing fixed limit.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("provider request invocation limit must be positive")
    _active_provider_request(capability)
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(capability._nonce)
        if (
            record is None
            or record["state"] != "claimed"
            or record["identity_token"] is not capability._identity_token
        ):
            raise PermissionError("provider request capability is revoked")
        sealed = record["launch_limit"]
        if sealed is not None:
            if sealed != limit:
                raise PermissionError("provider request launch allowance is already sealed")
            return sealed
        if record["invocations"]:
            raise PermissionError("cannot seal provider request after a launch")
        record["launch_limit"] = limit
        return limit


def consume_provider_request_invocation(
    capability: ProviderRequestCapability,
    *,
    limit: int,
) -> int:
    """Consume one bounded provider launch from the exact live request."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("provider request invocation limit must be positive")
    _active_provider_request(capability)
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(capability._nonce)
        if (
            record is None
            or record["state"] != "claimed"
            or record["identity_token"] is not capability._identity_token
        ):
            raise PermissionError("provider request capability is revoked")
        used = int(record["invocations"])
        effective_limit = record["launch_limit"]
        if effective_limit is None:
            effective_limit = limit
        if used >= effective_limit:
            raise PermissionError("provider request invocation budget is exhausted")
        record["invocations"] = used + 1
        return used + 1


def mint_provider_request_carrier(
    *,
    universe_id: str,
    agent_binding_id: str,
    binding_revision: int,
    operation: str,
) -> ProviderRequestCarrier:
    """Seal exact served-turn selection under the current request lease."""

    capability = provider_request_capability()
    if capability is None:
        raise PermissionError("provider request capability is unavailable")
    if (
        not universe_id.strip()
        or not agent_binding_id.strip()
        or isinstance(binding_revision, bool)
        or not isinstance(binding_revision, int)
        or binding_revision < 1
        or not operation.strip()
    ):
        raise ValueError("provider request carrier target is invalid")
    carrier = object.__new__(ProviderRequestCarrier)
    object.__setattr__(carrier, "universe_id", universe_id.strip())
    object.__setattr__(carrier, "agent_binding_id", agent_binding_id.strip())
    object.__setattr__(carrier, "binding_revision", binding_revision)
    object.__setattr__(carrier, "operation", operation.strip())
    object.__setattr__(carrier, "_nonce", capability._nonce)
    object.__setattr__(carrier, "_identity_token", capability._identity_token)
    object.__setattr__(carrier, "_issuer_pid", capability._issuer_pid)
    return carrier


def validate_provider_request_carrier(
    carrier: ProviderRequestCarrier,
    *,
    universe_id: str,
    agent_binding_id: str,
    binding_revision: int,
    operation: str,
) -> ProviderRequestCapability:
    """Validate the sealed target plus its still-live server registry lease."""

    if type(carrier) is not ProviderRequestCarrier or carrier._issuer_pid != os.getpid():
        raise PermissionError("provider request carrier is not server-issued")
    with _PROVIDER_REQUEST_LOCK:
        record = _PROVIDER_REQUESTS.get(carrier._nonce)
        if (
            record is None
            or record["state"] != "claimed"
            or record["identity_token"] is not carrier._identity_token
        ):
            raise PermissionError("provider request capability is revoked")
        capability_ref = record["capability_ref"]
        capability = capability_ref() if capability_ref is not None else None
    if capability is None:
        raise PermissionError("provider request capability is revoked")
    if carrier.universe_id != universe_id:
        raise PermissionError("provider request carrier belongs to another command center")
    if carrier.agent_binding_id != agent_binding_id:
        raise PermissionError("provider request carrier belongs to another agent binding")
    if carrier.binding_revision != binding_revision:
        raise PermissionError("provider request carrier has a stale binding revision")
    if carrier.operation != operation:
        raise PermissionError("provider request carrier belongs to another operation")
    return capability

# Module-level provider (initialized once at startup)
_provider: AuthProvider | None = None


def _get_provider() -> AuthProvider:
    """Get or create the global auth provider."""
    global _provider
    if _provider is None:
        _provider = create_provider()
    return _provider


def set_provider(provider: AuthProvider) -> None:
    """Override the global auth provider (for testing)."""
    global _provider
    _provider = provider


def auth_middleware(token: str | None) -> Identity | None:
    """Resolve a Bearer token to an Identity, or to nothing.

    Call this at the transport layer before tool execution. The resolved
    identity is stored request-locally for tools to read through
    ``current_identity()``. A missing token and an invalid token both bind
    NOTHING, in every auth mode; the transport answers 401 for either on a
    non-exempt path. Nothing here ever manufactures a principal.
    """
    _current_bearer_present.set(bool(token))
    _current_request_boundary_id.set(None)
    provider = _get_provider()

    identity: Identity | None = None
    if token:
        identity = provider.resolve_token(token)
    _current_identity.set(identity)
    if identity is not None:
        _current_request_boundary_id.set(f"request_boundary_{secrets.token_hex(32)}")
    return identity


def _is_inbound_hook_path(path: str) -> bool:
    """Exactly ``/mcp/hooks/<one-segment-token>`` (non-empty, no deeper path).

    The token is variable so this can't be an equality check like ``/app``;
    it is pinned to a single non-empty segment after the fixed prefix so no
    deeper ``/mcp/hooks/...`` path is ever exempted.
    """
    prefix = "/mcp/hooks/"
    if not path.startswith(prefix):
        return False
    token = path[len(prefix):]
    return bool(token) and "/" not in token


def _inbound_hooks_enabled() -> bool:
    """Whether the inbound webhook receiver route is mounted (dark flag).

    Lazily imported so the auth middleware never hard-depends on the inbound
    module; any import failure fails closed (treated as disabled).
    """
    try:
        from tinyassets.webhook_inbound import inbound_enabled
    except Exception:
        return False
    return bool(inbound_enabled())


#: The OAuth discovery documents, EXACTLY as ``starlette_discovery_routes``
#: mounts them. A substring test on ".well-known" used to stand in for this,
#: which exempted anything containing the string -- `/mcp/not.well-known/x`
#: reached the app unchallenged and answered 404 instead of the 401 the rule
#: promises (Codex review, 2026-09-02). An exempt table is a list of paths, not
#: a pattern.
_DISCOVERY_PATHS = frozenset({
    "/.well-known/oauth-protected-resource",
    "/mcp/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/mcp/.well-known/oauth-authorization-server",
})


#: Characters that end the ``/app`` segment. ``/`` is the only one an ASGI
#: ``scope["path"]`` can actually carry -- the server splits the query and the
#: client never sends the fragment -- but a predicate that decides an auth
#: boundary fails closed on the forms it cannot receive rather than trusting the
#: layer above to keep stripping them.
_APP_SEGMENT_BOUNDARY = ("/", "?", "#")


def _is_app_path(path: str) -> bool:
    """Exactly the web-app subtree: ``/app`` and ``/app`` + a segment boundary.

    Anchored on the boundary so a sibling prefix the apex website owns
    (``/apple-touch-icon.png``, ``/app-ads.txt``, ``/apps``) is never mistaken
    for an app route -- those must keep reaching the website origin, and in dev
    (where every path routes to the daemon) they must not be swept into a 401.

    Deliberately NOT traversal-normalised: every carve-out above is an
    exact-equality test, so a ``..`` or ``//`` path can only ever be
    *challenged* here, never exempted.
    """
    if path == "/app":
        return True
    return path.startswith("/app") and path[4:5] in _APP_SEGMENT_BOUNDARY


def _auth_challenge_path(path: str) -> bool:
    """The MCP endpoint (``/mcp`` + sub-paths) and the web app (``/app`` +
    sub-paths) require auth in challenge mode.

    Discovery routes stay public so the client can still find the authorization
    server, and unrelated paths are not swept in.

    The app moved from ``/mcp/app`` to the apex ``/app`` on 2026-09-30. That move
    took its routes OUT of the ``/mcp/`` prefix this function used to sweep, so
    ``/app`` is enumerated here explicitly. Without it every app API route
    (``/app/me``, ``/app/billing/checkout``, ``/app/account/delete``, …) would
    lose its bearer 401 and reach the handler with no identity — the middleware
    is the boundary those routes rely on.

    ``/mcp/app*`` is no longer mounted and gets NO carve-out, so it is swept into
    the ordinary ``/mcp/`` rule: anonymously it answers the connector's 401,
    exactly like ``/mcp/anything``. Deliberate — an exception that made the
    retired path answer a "clean" 404 would be a special case *for* the retired
    path, which is the back-compat the move removes.
    """
    if path in _DISCOVERY_PATHS:
        return False
    from tinyassets.onboarding.public_run import public_path

    if public_path(path):
        return False
    if path.startswith("/app/model-callback/"):
        from tinyassets.onboarding.hosted_model_auth import is_callback_path

        if is_callback_path(path):
            return False  # Shell only; authenticated exchange remains challenged.
    if path in {"/app", "/app/token", "/app/owner-sign-in"}:
        # The onboarding SPA (tinyassets/onboarding) is a public page that MUST
        # load before sign-in, and /app/token is its same-origin PKCE
        # token-exchange proxy — both run BEFORE any bearer exists, so neither may
        # be swept into the /mcp bearer 401 (mirrors the .well-known carve-out).
        # Their own authenticated /mcp tool calls are still challenged; the dark
        # flag still returns a bare 404 when the app is off.
        return False
    if path == "/app/sw.js":
        # The web-push service worker. `navigator.serviceWorker.register()` is a
        # plain same-origin fetch with NO bearer — the browser, not the page,
        # issues it — so challenging this path makes web push unregisterable,
        # which is the whole notification channel that needs no third-party
        # project. Same category as /app itself: a static script that runs
        # before any bearer exists.
        #
        # It carries no secret. The VAPID *public* key is handed to
        # `pushManager.subscribe` by the already-authenticated page (GET
        # /app/notify), never baked into the worker, and the worker holds no
        # identity — so serving it openly grants nothing. Exactly one path, by
        # equality; no deeper /app/... route is opened.
        return False
    if path.startswith("/app/m/"):
        from tinyassets.onboarding.app_modules import is_module_path

        if is_module_path(path):
            # The app's ES modules (onboarding/app_modules.py). The page imports
            # them as it loads, before any bearer exists, exactly like /app and
            # /app/sw.js. Static, allowlisted files with no secret and no
            # identity. Exactly /app/m/<build>/<basename>.js; no deeper path.
            return False
    if path == "/app/ui-frame":
        # The custom-UI bootstrap. It is loaded as an <iframe src>, and a browser
        # attaches no bearer to an iframe navigation, so challenging it rendered
        # every installed UI as `{"error":"authentication_required"}` (live
        # 2026-10-01: the founder's GTM Village showed an empty panel; regression
        # from the 2026-09-30 /mcp/app -> /app move). It is a fixed static page
        # that sandboxes itself to an opaque origin from its own response header
        # and holds no identity: the bundle and every read reach it by
        # postMessage from the authenticated page. Exactly one path, by equality.
        return False
    if path == "/app/oauth/client-metadata.json":
        # The OAuth Client ID Metadata Document a sign-in source names as its
        # client id: the provider's authorization server fetches it with no
        # bearer. A constant public document (one redirect URI, no secret, built
        # from the configured resource, never the request). Exactly one path.
        return False
    # Billing webhook: Stripe POSTs here with no MCP bearer, so like /app and
    # /mcp/hooks it must not be swept into the /mcp bearer 401. The handler requires
    # both Stripe provenance (signed, replay-bounded payload) and entitlement
    # authority (our HMAC-claimed exact plan). Exactly one path is opened; no deeper
    # /app/billing/... route is exempt, so checkout and cancel stay identity-gated.
    if path == "/app/billing/webhook":
        return False
    # Inbound webhook receiver: /mcp/hooks/<token> is a public POST endpoint whose
    # UNGUESSABLE per-branch token is the sole boundary (the run is author-gated,
    # durably rate-limited, and revocable — webhook Codex findings #1/#3/#5). It
    # carries no MCP bearer, so — like /app — it must not be
    # swept into the /mcp/* bearer 401. Only exempt when inbound is enabled (the
    # route only exists then) and only the exact /mcp/hooks/<one-segment-token>
    # shape — no deeper /mcp/hooks/... path is opened.
    if _inbound_hooks_enabled() and _is_inbound_hook_path(path):
        return False
    return path == "/mcp" or path.startswith("/mcp/") or _is_app_path(path)


def _challenge_prm_url() -> str:
    """The ``resource_metadata`` URL to advertise in the 401 challenge.

    It MUST be fetchable by the client, or OAuth discovery never starts. In
    production only ``/mcp*`` is proxied to the daemon (Cloudflare Worker), so an
    apex ``/.well-known/oauth-protected-resource`` 404s. When ``WORKOS_MCP_RESOURCE``
    is set (e.g. ``https://tinyassets.io/mcp``) derive the PRM from it, yielding
    the routed ``…/mcp/.well-known/oauth-protected-resource`` (the mcp-prefixed
    variant the server also mounts). Fallback: the server root well-known, which
    is correct in dev/tunnel where every path routes to the daemon.
    """
    import os

    resource = os.environ.get("WORKOS_MCP_RESOURCE", "").strip().rstrip("/")
    if resource:
        return f"{resource}/.well-known/oauth-protected-resource"
    from tinyassets.auth.wellknown import _server_url

    return f"{_server_url()}/.well-known/oauth-protected-resource"


async def _send_auth_challenge_401(send: Any, *, invalid_token: bool) -> None:
    """Emit an RFC 9728 ``401`` with a ``WWW-Authenticate`` challenge pointing
    at our Protected Resource Metadata, so clients start/refresh OAuth.

    ``invalid_token=True`` is the present-but-bad-token case (RFC 6750
    ``error="invalid_token"``). ``False`` is a missing-credentials challenge —
    no error code, per RFC 6750 — used in require-auth mode so an unauthenticated
    client launches the OAuth flow instead of proceeding without a principal.
    """
    prm = _challenge_prm_url()
    if invalid_token:
        challenge = f'Bearer error="invalid_token", resource_metadata="{prm}"'
        body = b'{"error":"invalid_token"}'
    else:
        challenge = f'Bearer resource_metadata="{prm}"'
        body = b'{"error":"authentication_required"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", challenge.encode("latin1")),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


def _tool_linking_challenge_body(
    body: bytes,
    *,
    invalid_token: bool,
) -> bytes | None:
    """Build the bounded hosted-connector linking response for tools/call.

    A hosted connector may have cached the tool catalog before linking. This
    recognizes only a single tools/call or a batch made entirely of tools/call
    requests. It never dispatches the requested tool and never constructs a
    principal. Any other JSON-RPC shape stays on the transport-401 path.
    """
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    is_batch = isinstance(payload, list)
    items = payload if is_batch else [payload]
    if not items or any(
        not isinstance(item, dict)
        or item.get("method") != "tools/call"
        or "id" not in item
        for item in items
    ):
        return None

    prm = _challenge_prm_url()
    error = "invalid_token"
    description = (
        "The TinyAssets access token is invalid or expired; sign in again."
        if invalid_token
        else "Sign in to TinyAssets to continue."
    )
    challenge = (
        f'Bearer resource_metadata="{prm}", error="{error}", '
        f'error_description="{description}"'
    )

    def _result(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": item["id"],
            "result": {
                "content": [{
                    "type": "text",
                    "text": "Authentication required. Sign in to TinyAssets to continue.",
                }],
                "_meta": {"mcp/www_authenticate": [challenge]},
                "isError": True,
            },
        }

    response: Any = [_result(item) for item in items] if is_batch else _result(items[0])
    return json.dumps(response, separators=(",", ":")).encode("utf-8")


async def _send_tool_linking_challenge_200(send: Any, body: bytes) -> None:
    """Return an MCP tool error that makes a hosted client open OAuth linking."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


# Hard cap on how much of a PROBE request body the canary check will buffer
# (Codex review 2026-07-15: unbounded buffering of unauthenticated POSTs on a
# public endpoint is a memory-DoS vector). A probe is a few hundred bytes.
_MAX_PROBE_BODY_BYTES = 1_048_576  # 1 MiB


async def _buffer_request_body(
    receive: Any,
    *,
    cap: int = _MAX_PROBE_BODY_BYTES,
) -> tuple[bytes, list[dict], bool, bool]:
    """Drain the request body: (body, raw messages, disconnected, oversized).

    The raw messages are replayed to the inner app afterwards so buffering is
    invisible to it. Stops buffering the moment ``cap`` is exceeded and flags
    the request oversized (the caller answers 413 without reading the rest).
    """
    messages: list[dict] = []
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        messages.append(message)
        if message.get("type") != "http.request":
            return b"", messages, True, False
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > cap:
            return b"", messages, False, True
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks), messages, False, False


async def _send_forbidden_403(send: Any, reason: str) -> None:
    """A named principal that may not make THIS request (the canary outside
    its allowlist). Not a challenge: the bearer was valid."""
    body = json.dumps({"error": "forbidden", "detail": reason}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_payload_too_large_413(send: Any) -> None:
    """Reject an oversized unsigned body without buffering the rest of it."""
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b'{"error":"request_too_large"}',
        }
    )


def _replay_receive(messages: list[dict], receive: Any) -> Any:
    """A receive callable that replays buffered messages, then delegates."""
    queue = deque(messages)

    async def _receive() -> dict:
        if queue:
            return queue.popleft()
        return await receive()

    return _receive


def current_identity_or_none() -> Identity | None:
    """The bound identity, or None when the request bound nothing.

    For the few places that legitimately branch on PRESENCE: the transport's
    challenge path, status's ``bearer_present``, the app's identity gate.
    Everything else calls :func:`current_identity` and lets it refuse.
    """
    return _current_identity.get()


def current_identity() -> Identity:
    """The current request's resolved identity, or a refusal.

    There is no fallback identity (founder, 2026-09-02). If nothing is
    bound, the caller is running outside an authenticated request and gets
    ``PermissionError`` -- never a stand-in principal.
    """
    identity = _current_identity.get()
    if identity is None:
        raise PermissionError("Authentication required")
    from tinyassets.outside_authority import check_identity

    check_identity(identity)
    return identity


@contextmanager
def identity_context(identity: Identity | None) -> Iterator[None]:
    """Run a block as *identity* — set the request-local identity contextvar and
    restore the prior value after.

    For non-MCP-bearer entry points (e.g. the browser deposit form) that
    authenticate a subject out-of-band and then invoke the same identity-scoped
    tool logic the MCP path uses, so ``permissions.current_actor_id()`` resolves
    to that subject and every downstream ACL/ownership gate runs against it. The
    set/use/reset all happen synchronously in the caller's thread, so it is safe
    inside a worker thread dispatched via ``run_in_threadpool``.
    """
    token = _current_identity.set(identity)
    try:
        yield
    finally:
        _current_identity.reset(token)


#: True once this PROCESS has bound a local operator, which is what a
#: single-tenant daemon does at startup and a served multi-tenant request never
#: does. Process-wide on purpose: it describes the process, not the request.
_local_operator_process = False


def is_local_operator_process() -> bool:
    """Whether this process is the local single-tenant daemon.

    The distinction two call sites need and used to get wrong by asking "is the
    caller authenticated". Under the fail-closed boundary every caller is, so that question
    stopped separating the tray from a served request and silently disabled the
    tray's universe switch.
    """
    return _local_operator_process


def clear_identity() -> None:
    """Unbind the principal: no identity, no bearer.

    What an UNAUTHENTICATED request actually is. Tests reached for it by
    installing the dev provider and resolving a token, which produces a real
    signed-in identity -- so a test that meant "nobody is here" was driving the
    local operator and asserting a refusal that could not happen.
    """
    _current_identity.set(None)
    _current_bearer_present.set(False)


def bind_local_operator_identity() -> Identity:
    """Bind the process-wide principal for a transport that carries no bearer
    (stdio: the Claude plugin, a local `--transport stdio` run).

    The principal is the local operator: ``UNIVERSE_SERVER_DEV_USER`` when
    set, else the OS account the process runs as. Raises when neither names
    anyone. There is no fallback principal (founder,
    2026-09-02); before this the stdio server ran every call as nobody.
    """
    import getpass

    from tinyassets.auth.provider import DEV_USER_ENV, DevAuthProvider

    name = (os.environ.get(DEV_USER_ENV, "") or "").strip()
    if not name:
        try:
            name = (getpass.getuser() or "").strip()
        except Exception:  # noqa: BLE001 - no account name is a refusal below
            name = ""
    if not name:
        raise RuntimeError(
            f"a stdio server needs a named local operator: set {DEV_USER_ENV} "
            "(there is no fallback principal)"
        )
    identity = DevAuthProvider(user_id=name).resolve_token("stdio")
    if identity is None:  # pragma: no cover - the dev provider always resolves
        raise RuntimeError("the dev provider resolved no identity for the local operator")
    global _local_operator_process

    _current_identity.set(identity)
    _local_operator_process = True
    return identity


def current_bearer_present() -> bool:
    """Whether this request presented bearer material, without retaining it."""
    return _current_bearer_present.get()


def current_request_boundary_id() -> str | None:
    """Opaque identity of this exact authenticated transport request."""

    return _current_request_boundary_id.get()


def current_mcp_message_identity() -> Identity | None:
    """Re-derive bearer identity from only the low-level current MCP message."""

    from mcp.server.lowlevel.server import request_ctx

    try:
        request = request_ctx.get().request
    except LookupError:
        return None
    if request is None:
        return None
    try:
        auth_headers = request.headers.getlist("authorization")
        method = request.method.upper()
        path = request.url.path
    except (AttributeError, TypeError):
        return None
    if method != "POST" or path not in {"/mcp", "/mcp/"} or len(auth_headers) != 1:
        return None
    scheme, separator, credential = auth_headers[0].partition(" ")
    if scheme.lower() != "bearer" or not separator or not credential.strip():
        return None
    if wiki_canary_token_matches(credential.strip()):
        return None
    identity = _get_provider().resolve_token(credential.strip())
    if identity is None or not (identity.user_id or "").strip():
        return None
    return identity


class AuthContextMiddleware:
    """Resolve bearer auth into request-local identity for MCP tool calls."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        previous: Token[Identity | None] = _current_identity.set(None)
        previous_bearer: Token[bool] = _current_bearer_present.set(False)
        previous_boundary: Token[str | None] = _current_request_boundary_id.set(None)
        previous_canary = set_wiki_canary_authority(False)
        try:
            auth_header = ""
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization":
                    auth_header = value.decode("latin1")
                    break
            token = None
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
            path = scope.get("path", "")
            method = scope.get("method", "").upper()

            canary_authorized = False
            if token and wiki_canary_token_matches(token):
                # The canary service principal (change D4). Its
                # bearer is valid for the exact GET /mcp/pulse health read or
                # the probe shapes in canary_request_allowed on the MCP POST
                # endpoint. A valid bearer outside that world is refused,
                # never downgraded.
                if method == "GET" and path == "/mcp/pulse":
                    _current_identity.set(CANARY)
                    _current_bearer_present.set(True)
                    canary_authorized = True
                elif method != "POST" or path not in ("/mcp", "/mcp/"):
                    # The endpoint itself, not the app, hook or any
                    # other /mcp/* route: those have their own authentication
                    # and the canary has no business there.
                    await _send_forbidden_403(
                        send,
                        "the canary bearer may only probe /mcp or GET /mcp/pulse",
                    )
                    return
                else:
                    body, messages, disconnected, oversized = await _buffer_request_body(receive)
                    if oversized:
                        await _send_payload_too_large_413(send)
                        return
                    if disconnected or not canary_request_allowed(body):
                        await _send_forbidden_403(
                            send, "the canary bearer may only initialize, list tools, "
                            "read status, and write its own probe page"
                        )
                        return
                    receive = _replay_receive(messages, receive)
                    _current_identity.set(CANARY)
                    _current_bearer_present.set(True)
                    set_wiki_canary_authority(is_exact_wiki_canary_request(body))
                    canary_authorized = True
            # The inbound webhook receiver's sole boundary is its unguessable URL
            # token + owner-bound handler; a generic channel may POST with its OWN
            # Authorization: Bearer. Skip ALL MCP bearer interpretation/401 on an
            # enabled EXACT hook route so a foreign bearer isn't rejected before the
            # token handler runs (Codex inbound review). The hook's OWNER is bound
            # to the run by the handler from the hook row, never from this request.
            is_hook_route = _inbound_hooks_enabled() and _is_inbound_hook_path(path)
            if not canary_authorized and not is_hook_route:
                auth_middleware(token)
            identity = _current_identity.get()
            if identity is not None and identity.metadata.get("outside_origin") is not None:
                if path not in ("/mcp", "/mcp/"):
                    await _send_forbidden_403(
                        send, "outside clients use the MCP capability surface")
                    return
                if method == "POST":
                    body, messages, disconnected, oversized = await _buffer_request_body(receive)
                    if oversized:
                        await _send_payload_too_large_413(send)
                        return
                    try:
                        from tinyassets.outside_authority import check_mcp_request

                        if disconnected:
                            raise PermissionError("request disconnected")
                        check_mcp_request(identity, json.loads(body))
                    except (ValueError, PermissionError):
                        await _send_forbidden_403(send, "outside client capability not granted")
                        return
                    receive = _replay_receive(messages, receive)
            if not canary_authorized and not is_hook_route and identity is None:
                linking_body = None
                if method == "POST" and path in ("/mcp", "/mcp/"):
                    request_body, _, disconnected, oversized = await _buffer_request_body(receive)
                    if oversized:
                        await _send_payload_too_large_413(send)
                        return
                    if not disconnected:
                        linking_body = _tool_linking_challenge_body(
                            request_body,
                            invalid_token=bool(token),
                        )
                if linking_body is not None:
                    # Authentication bootstrap only: the requested handler does
                    # not run, no session state is read, and no identity exists.
                    await _send_tool_linking_challenge_200(send, linking_body)
                    return
                if token:
                    # Present-but-invalid bearer -> 401 challenge (RFC 9728), on
                    # every path: a bad credential is never ignored.
                    await _send_auth_challenge_401(send, invalid_token=True)
                    return
                if _auth_challenge_path(path):
                    # No bearer on the MCP endpoint -> 401 challenge, in EVERY auth
                    # mode: the client launches OAuth. The exempt paths (discovery,
                    # the app shell and its token route, hooks, and the
                    # billing webhook) bind their own principal.
                    await _send_auth_challenge_401(send, invalid_token=False)
                    return
            await self.app(scope, receive, send)
        finally:
            reset_wiki_canary_authority(previous_canary)
            _current_request_boundary_id.reset(previous_boundary)
            _current_bearer_present.reset(previous_bearer)
            _current_identity.reset(previous)


def require_auth(
    capability: str | PermissionAction | None = None,
    *,
    scope: PermissionScope | None = None,
    context: PermissionContext | None = None,
) -> Identity:
    """Get current identity, raising if auth is required but missing.

    Args:
        capability: Optional capability to check. If the identity
            lacks this capability, raises PermissionError.

    Returns:
        The current Identity.

    Raises:
        PermissionError: If auth is required and identity is missing
            or lacks the requested capability.
    """
    identity = current_identity()

    if capability:
        verdict = identity.can(capability, scope=scope, context=context)
    else:
        verdict = None

    if verdict is not None and not verdict.allowed:
        raise PermissionError(
            f"Missing capability: {verdict.action} "
            f"(user={identity.username}, capabilities={identity.capabilities})"
        )

    return identity


def require_action_scope(
    tool: str,
    action: str,
    *,
    scope: PermissionScope | None = None,
    context: PermissionContext | None = None,
) -> Identity:
    """Authorize one internal dispatch action against its named OAuth scope."""

    identity = current_identity()
    provider = _get_provider()
    auth_required = provider.is_auth_required()
    resolve_always = provider.resolve_always_writes()

    # Dev / optional modes: no scope enforcement (unchanged).
    if not auth_required and not resolve_always:
        return identity

    metadata = action_scope_for(tool, action)
    if metadata is None:
        raise PermissionError(
            f"No action-scope metadata for {tool}.{action}; refusing gated dispatch."
        )

    # Resolve-always (WorkOS, D0b): read-effect actions are not scope-gated;
    # the per-universe ACL layer separately denies reads of a private universe.
    # The caller is always a named principal here (the transport refused
    # anything else).
    if resolve_always and not auth_required and metadata.effect == "read":
        return identity

    if resolve_always and not auth_required:
        # Write/costly/admin: an authenticated founder passes when they hold
        # either the fine-grained action scope or the coarse effect grant
        # (read/write/costly/admin). Per-universe confinement is the ACL layer.
        grants = set(identity.capabilities)
        if metadata.oauth_scope in grants or metadata.effect in grants:
            return identity
        raise PermissionError(
            f"Missing OAuth scope: {metadata.oauth_scope} "
            f"for action {metadata.action_name} "
            f"(user={identity.username}, capabilities={identity.capabilities})"
        )

    # Legacy full-auth (OAuthProvider): exact named-scope check (unchanged).
    verdict = identity.can(
        PermissionAction(
            name=metadata.action_name,
            cost_tier=metadata.cost_tier,
            required_scope=metadata.oauth_scope,
        ),
        scope=scope,
        context=context,
    )
    if not verdict.allowed:
        raise PermissionError(
            f"Missing OAuth scope: {verdict.required_scope} "
            f"for action {metadata.action_name} "
            f"(user={identity.username}, capabilities={identity.capabilities})"
        )
    return identity


def write_gate_rejection(handle: str) -> str | None:
    """The write handles' first check: a rejection envelope when NOBODY is
    bound, else ``None``.

    Over HTTP nobody never reaches a handle (the transport answered 401), so
    this fires only for a direct caller with no identity: it refuses rather
    than letting the call reach the scope check as nobody. It no longer
    depends on the provider's mode; there is no mode in which nobody may
    write (founder, 2026-09-02).
    """
    if current_identity_or_none() is not None:
        return None
    return json.dumps({
        "status": "rejected",
        "auth_required": True,
        "tool": handle,
        "error": (
            "Authentication required: sign in through OAuth on this connector. "
            "There is no unsigned access to this handle."
        ),
    })
