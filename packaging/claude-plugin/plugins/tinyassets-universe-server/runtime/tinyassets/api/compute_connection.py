"""Owner-scoped registration of an open COMPUTE provider for a universe.

``write_graph target=connection operation=connect_compute`` — the user-facing keystone
of the compute-agnostic provider system, the sibling of ``connect_llm`` /
``connect_http``. It lets a universe owner register ANY compute provider (Kimi,
OpenRouter, any OpenAI-compatible endpoint, or a CLI subscription) as a
:class:`~tinyassets.providers.definition.ProviderDefinition` — no per-provider code,
no allowlist.

**Registration only — never a secret (custody boundary).** Unlike ``connect_llm`` /
``connect_http``, this handler accepts NO credential material. Per
``retire-mcp-provider-secret-deposit``, an LLM API key must never cross the
chatbot/MCP/control-plane boundary. So for ``api_key_http`` the credential is
deposited OUT OF BAND — the owner first creates an http connection (endpoint +
credential) and grants it to the universe (via the secure WorkOS browser deposit
form / ``connect_http``), then passes that grant's id here as ``ref``. This handler
only validates the grant is real, bound to THIS universe, and owned by the caller,
then records the compute descriptor on top of it. For ``subscription_cli`` the ``ref``
is the vendor CLI provider name (``codex`` / ``claude-code``); the subscription is
deposited via ``connect_llm``.

Registration creates a CANDIDATE only (design §1): it does not enroll, select, or make
the provider routable — the owner selects it for automations via per-node
``llm_policy`` and the router bridge. Owner-gated exactly like ``connect_http`` (an
explicit ``admin`` ACL row; unbound/non-admin/unknown-universe get the uniform
``not_found`` envelope). Never echoes a secret (there is none to echo).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path, _request_universe

# Uniform absent-resource envelope (existence-probe safe) — mirrors connect_http.
_NOT_FOUND: dict[str, Any] = {"error": "not_found", "resource": "connection"}

# subscription_cli refs we can run in-daemon (CLI subprocess). Everything else is
# api_key_http (open). Kept in lockstep with provider_resolver._CLI_PROVIDER_CLASSES.
_CLI_REFS = ("codex", "claude-code")


def _payload(value: Any) -> dict[str, Any]:
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("payload_json must be a JSON object")
    return document


def _project(definition: Any) -> dict[str, Any]:
    """Redacted projection — there is no secret in a definition; ``ref`` is a
    grant_id / provider name, not credential material."""
    return {
        "status": "registered",
        "definition_id": definition.id,
        "access_method": definition.access_method,
        "protocol": definition.protocol,
        "model": definition.model,
        "visibility": definition.visibility,
        "next": [
            "registration is not selection: to make a command center RUN on this "
            "provider, select it as the serving provider; a workflow node normally "
            "needs no llm_policy at all and uses whatever the command center serves. To "
            "pin one node to a provider, use the NAME, not this definition_id: "
            "llm_policy={'preferred': {'provider': 'codex'}}",
            "interactive tool-using agents can use owned CLI subscriptions or "
            "supported api_key_http models. HTTP agent selection requires "
            "accepted model_access, fresh tool-capable discovery, and enabled "
            "engine-tool execution; registration alone does not enable serving "
            "or grant spending",
        ],
    }


def _validate_http_grant(
    *, base: Path, universe_id: str, actor: str, grant_id: str
) -> dict[str, Any] | None:
    """Confirm ``grant_id`` names a real http connection grant bound to THIS universe
    and owned by the caller. Returns an error envelope, or None on success.

    Isolation: a grant for another universe/owner returns the uniform ``not_found``
    (never confirm a foreign grant's existence). The credential itself is never
    touched here — only the grant's binding metadata (a plain read)."""
    # An EMPTY ref is a request-shape error (no specific grant is referenced, so it
    # reveals no grant's existence) — safe to be specific and helpful.
    if not grant_id:
        return {
            "error": "connection_setup_invalid",
            "detail": (
                "api_key_http requires ref = the grant_id of an http connection "
                "already granted to this command center (deposit the credential via the "
                "secure browser form / connect_http first)"
            ),
        }
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.storage.outbound_connections import GrantResolutionError
    # Uniform absent-resource envelope for EVERY inaccessible grant (Codex adapt #1):
    # absent, revoked, foreign-universe, foreign-owner, or backed by a
    # non-http/revoked connection ALL return the SAME not_found — so the surface is
    # never an existence/ownership oracle (a non-empty ref must not reveal WHICH
    # condition failed).
    try:
        resource = granted_resource_row(base, principal=actor, command_center=universe_id,
                                    grant_id=grant_id)
    except GrantResolutionError:
        return dict(_NOT_FOUND)
    # Connection-class + liveness gate (Codex adapt #2): a grant is only valid as an
    # api_key_http compute ref if it is backed by a LIVE, HTTP-class connection.
    # Without this, any same-owner/same-universe grant (e.g. one issued for a
    # different, non-http connection type) could be confused into a compute ref.
    if (
        resource is None
        or resource.get("connection_type", "") != "http"
        or resource.get("revoked_at") is not None
    ):
        return dict(_NOT_FOUND)
    return None


def connect_compute(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Register an open compute provider for the owner's universe (no secret).

    Returns a redacted projection on success, a sanitized error otherwise. Every
    refusal happens before any write.
    """
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.providers import definition as pd

    # 1. Server-derived authenticated principal (no env fallback).
    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}

    # 2. Require an explicit admin ACL row for THIS actor on THIS universe.
    uid = _request_universe(universe_id)
    base = _base_path()
    admin = [
        row
        for row in list_universe_acl(base, universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return dict(_NOT_FOUND)

    # 3. Parse + read the descriptor fields (validation done by register_definition).
    try:
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    access_method = str(document.get("access_method") or "").strip()
    protocol = str(document.get("protocol") or "").strip()
    model = str(document.get("model") or "").strip()
    ref = str(document.get("ref") or "").strip()
    visibility = str(document.get("visibility") or "private").strip()

    # 4. Access-method-specific ref validation (BEFORE any write).
    if access_method == "api_key_http":
        gate = _validate_http_grant(
            base=Path(base), universe_id=uid, actor=actor, grant_id=ref
        )
        if gate is not None:
            return gate
    elif access_method == "subscription_cli":
        if ref not in _CLI_REFS:
            return {
                "error": "connection_setup_invalid",
                "detail": f"subscription_cli ref must be one of {list(_CLI_REFS)}",
            }
    # An unknown access_method falls through to register_definition, which rejects it.

    # 5. Register the descriptor (candidate only; no secret, no authority side-effect).
    try:
        definition = pd.register_definition(
            universe_id=uid,
            owner_user_id=actor,
            access_method=access_method,
            protocol=protocol,
            model=model,
            ref=ref,
            visibility=visibility,
        )
    except pd.ProviderDefinitionError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    return _project(definition)


def _project_read(definition: Any) -> dict[str, Any]:
    """Owner-facing view of one registered provider (no secret exists to redact —
    ``ref`` is a grant_id / CLI name). Distinct from ``_project`` (the post-write
    receipt): a listing row carrying the durable descriptor fields."""
    return {
        "definition_id": definition.id,
        "access_method": definition.access_method,
        "protocol": definition.protocol,
        "model": definition.model,
        "ref": definition.ref,
        "visibility": definition.visibility,
        "created_at": getattr(definition, "created_at", ""),
    }


def read_compute_providers(*, universe_id: str = "") -> dict[str, Any]:
    """List the compute providers registered for the owner's universe (candidates).

    The read sibling of :func:`connect_compute` — so a user can SEE what they (or a
    remix) registered, from any surface. Owner-gated exactly like registration (an
    explicit ``admin`` ACL row; unbound/non-admin/unknown-universe get the uniform
    ``not_found``). Integrity is enforced by ``list_definitions`` (each row's id must
    content-address its own fields, else it fails closed). No secret is ever returned.
    """
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.providers import definition as pd

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}
    uid = _request_universe(universe_id)
    base = _base_path()
    admin = [
        row
        for row in list_universe_acl(base, universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return dict(_NOT_FOUND)
    try:
        definitions = pd.list_definitions(uid)
    except pd.ProviderDefinitionError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    return {
        "universe_id": uid,
        "providers": [_project_read(d) for d in definitions],
        "count": len(definitions),
    }
