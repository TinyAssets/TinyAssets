"""Which platform secrets each process may hold.

The host env file ``/etc/tinyassets/env`` carries every secret the box needs,
and until 2026-10-02 the daemon container received all of it, and handed all of
it to the per-universe engine MCP children (``engine_mcp_http``). A daemon RCE
or an environment leak then meant the whole DigitalOcean account and live
billing (docs/concerns/2026-10-02-platform-secrets-in-daemon-env.md).

Credential groups classified by their readers:

``DAEMON_FORBIDDEN_ENV``
    Nothing under ``tinyassets/`` reads these. The host and the sidecars do
    (the tunnel token, the log-shipping token), or nothing does at all. They
    never enter the daemon container: ``deploy/install-tinyassets-env.sh``
    renders ``/etc/tinyassets/daemon.env`` without them, and the deploy refuses
    a daemon whose environment carries one. The shell copy of this list lives
    in that script; ``tests/test_platform_secret_scope.py`` holds them equal.

``DAEMON_ONLY_ENV``
    The daemon's own HTTP routes need these (checkout, the Stripe webhook and
    account deletion, all in ``tinyassets/onboarding``). No child does, so
    :func:`child_env` removes them before any child launch that inherits the
    daemon's environment.

``DAEMON_AUTH_ENV``
    Admission/interchange signing, ingress, canary and session credentials.
    Engine tools do not serve these daemon authentication surfaces.

This limits environment inheritance, not credential recovery after an engine
compromise: engines still share the daemon's UID and PID namespace, including
access to its exec-time environment through procfs. See
docs/concerns/2026-10-04-engine-mcp-shares-daemon-uid.md.
"""
from __future__ import annotations

from collections.abc import Mapping

from tinyassets.billing import SECRET_ENV_NAMES as _BILLING_SECRET_ENV

DAEMON_FORBIDDEN_ENV: frozenset[str] = frozenset({
    # Account-wide DigitalOcean token. Only GitHub workflows use one, and they
    # read their own repository secret.
    "DO_API_TOKEN",
    # The cloudflared sidecar's credential; compose interpolates it there.
    "CLOUDFLARE_TUNNEL_TOKEN",
    # The logs sidecar's Better Stack ingest token.
    "BETTERSTACK_SOURCE_TOKEN",
    # Template placeholders no code reads (``host_pool`` is never imported).
    "SUPABASE_DB_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
})
# A name is removed from this list only once it cannot exist: deleted from
# deploy/tinyassets-env.template so it is never set again, AND deleted from the
# live host env by the deploy, which then asserts it absent. This list is a
# DENY list filtered out of daemon.env (`install-tinyassets-env.sh
# render-daemon-env`), so de-listing a key that is still in /etc/tinyassets/env
# would hand it to the daemon rather than withhold it -- the opposite of the
# point. ``GITHUB_OAUTH_CLIENT_SECRET`` went that way on 2026-10-03: nothing
# read it anywhere, so the whole name is gone instead of guarded.

DAEMON_ONLY_ENV: frozenset[str] = frozenset({
    "TINYASSETS_BILLING_ENTITLEMENT_KEY",
    "WORKOS_API_KEY",
}) | _BILLING_SECRET_ENV  # the payment processor's keys, named by its adapter

DAEMON_AUTH_ENV: frozenset[str] = frozenset({
    "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY",
    "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY",
    "TINYASSETS_APP_INGRESS_HMAC_KEY",
    "TINYASSETS_WIKI_CANARY_TOKEN",
    # Normally popped by session_store.arm(); also protect launches before arm.
    "TINYASSETS_SESSION_SEAL_KEY",
})

# Explicit exceptions in the deployment-inventory test, with actual engine
# consumers. These are inherited credentials, not an isolation guarantee.
ENGINE_REQUIRED_SECRET_ENV: Mapping[str, str] = {
    "TINYASSETS_IDENTITY_FINGERPRINT_KEY":
        "engine get_status -> api.status principal fingerprint",
    "TINYASSETS_FCM_SERVICE_ACCOUNT_JSON":
        "engine request_from_user -> pending_requests -> owner_notifications -> FCM",
    "TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY":
        "engine request_from_user -> pending_requests -> owner_notifications -> web push",
}

CHILD_FORBIDDEN_ENV: frozenset[str] = (
    DAEMON_FORBIDDEN_ENV | DAEMON_ONLY_ENV | DAEMON_AUTH_ENV
)


def child_env(source: Mapping[str, str]) -> dict[str, str]:
    """Remove classified unnecessary credentials; preserve runtime configuration.

    Shared by engine and broker launches. Engine handlers delegate throughout
    the package, so a runtime-config allowlist needs a separate consumer audit.
    The Compose/template inventory test rejects unclassified injected names.
    """
    return {name: value for name, value in source.items() if name not in CHILD_FORBIDDEN_ENV}
