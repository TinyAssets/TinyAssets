"""Owner-scoped BYO-LLM subscription deposit.

The chatbot deposit path for the ``byo-llm-deposit-surface`` capability:
``write_graph target=connection operation=connect_llm``. An authenticated
universe **owner/founder** places their own Claude or Codex subscription material
into that universe's credential vault as a single owned ``llm_subscription``
record, so the downstream custody/serving spine can serve on it.

This handler is the canonical ``llm_subscription`` vault writer. It performs only
the owner-scoped vault write; custody adoption and the serving re-point stay in
``bind_serving_provider`` (owned by ``byo-llm-provider-connect``). It adds no new
advertised MCP handle — it is an operation under the pinned ``write_graph``.

Security honesty (MVP, chatbot transport): the base64 ``auth_material_b64`` the
caller submits unavoidably enters the MCP request and the model/connector context
on the chatbot path, and the decoded Claude token is stored in the per-universe
vault, which is 0600 JSON and **not** encrypted at rest (credential-vault task
1.8). The app's own connect flows (the OpenAI device sign-in calls this handler
directly) keep the material out of any chat. Beyond that inherent transport
exposure, this handler never returns, logs, or
echoes the material, and its exceptions carry no secret.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The only subscription services whose material the vault can inject into a
# CLI-subprocess provider. Any other service would never reach a provider, so a
# deposit for it is rejected loudly (Hard Rule #8) rather than stored dead.
_SUPPORTED_SERVICES = ("claude", "codex")

# A subscription token / auth.json base64 blob is small. This bounds the transport
# before any decode so an oversize paste is refused cheaply, not materialized.
_MAX_MATERIAL_CHARS = 200_000

# Uniform absent-resource envelope. Reused for "not authenticated", "not the
# owner", and "unknown universe" so a caller cannot probe universe/credential
# existence through the deposit surface (mirrors cloud_connections.py:105).
_NOT_FOUND: dict[str, Any] = {"error": "not_found", "resource": "connection"}


def _payload(value: Any) -> dict[str, Any]:
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("payload_json must be a JSON object")
    return document


def _serving_hint(base: Path, *, universe_id: str, actor: str) -> tuple[str | None, int | None]:
    """Best-effort ``(agent_binding_id, revision)`` for the serving re-point.

    Names the owner's own agent binding so the caller can chain
    ``bind_serving_provider`` -> ``set_serving`` without a separate lookup. Never
    raises into the deposit result — the deposit itself has already succeeded, and
    a universe may legitimately have no binding yet.
    """
    try:
        from tinyassets.commons_catalog import list_bindings

        # Every binding: the owner's own is looked for AMONG them, so a page
        # of 30 newer ones by other creators used to hide it.
        bindings = list_bindings(base, universe_id=universe_id, limit=None)
    except Exception:  # noqa: BLE001 - hint is advisory; never fail the deposit
        return None, None
    owned = [b for b in bindings if b.get("created_by") == actor]
    # list_bindings is ordered newest-first; prefer a configured binding.
    for binding in owned:
        if binding.get("status") == "configured":
            return str(binding.get("agent_binding_id")), int(binding.get("revision", 0))
    if owned:
        binding = owned[0]
        return str(binding.get("agent_binding_id")), int(binding.get("revision", 0))
    return None, None


def _build_record(service: str, material: str) -> dict[str, Any]:
    """Build one ``llm_subscription`` record from the base64 transport value.

    Claude: decode the base64 to the plaintext OAuth token and store it in
    ``oauth_token`` (what ``resolve_claude_oauth_token`` reads).

    Codex: keep the value as a base64 **string** in ``auth_json_b64`` (both
    transport and the at-rest field). ``write_credential_vault`` ->
    ``_normalize_record`` -> ``_decode_codex_auth_json`` validates it at write time.

    Raises ``ValueError`` with a secret-free message on malformed input. The
    base64-decode exceptions are deliberately not chained: a ``UnicodeDecodeError``
    carries the decoded credential bytes in ``.object``, so re-raising a fresh
    error with no ``__cause__`` keeps the token out of any traceback.
    """
    if service == "claude":
        import base64

        try:
            # binascii.Error (raised on invalid base64) is a ValueError subclass.
            decoded = base64.b64decode(material, validate=True)
        except ValueError:
            raise ValueError("auth_material_b64 base64 decode failed") from None
        try:
            token = decoded.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise ValueError("auth_material_b64 is not a UTF-8 token") from None
        if not token:
            raise ValueError("auth_material_b64 decoded content is empty")
        # Match the dedicated browser deposit's existing token contract here,
        # before ANY vault write. The in-app form uses this handler directly;
        # accepting the browser authorization code overwrote working credentials
        # and falsely reported a successful connection. Shape is not validity.
        if not token.startswith("sk-ant-") or any(c.isspace() for c in token) or "#" in token:
            raise ValueError(
                "Paste the final token printed by the setup command, not the "
                "browser authorization code. Enter that code back in the terminal "
                "first, then copy its token (starting with sk-ant-). "
                "Your existing connection has not changed."
            )
        return {
            "credential_type": "llm_subscription",
            "service": "claude",
            "oauth_token": token,
        }
    # codex: the base64 string is the at-rest field; strip transport whitespace.
    normalized = material.translate(str.maketrans("", "", " \t\r\n"))
    # Through the shared builder, so the deposit is STAMPED. Both newest-wins
    # comparators fall back to the record's stamp when the stored document carries
    # none, and an unstamped deposit gave them nothing to read -- so a
    # yesterday-stamped document already on disk could be restored over a
    # credential deposited moments ago (Codex refute-review, P1 #4).
    from tinyassets.credential_vault import llm_subscription_credential_record

    return llm_subscription_credential_record(
        service="codex", auth_json_b64=normalized,
    )


def connect_llm(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Deposit the authenticated owner's own Claude/Codex subscription material.

    Returns a non-secret projection on success and a sanitized error otherwise.
    Every refusal leaves zero vault / ownership / custody / binding / serving
    mutation.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe, _universe_dir
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import list_universe_acl

    # 1. Server-derived authenticated principal. No env fallback (permissions.py).
    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}

    # 2. Resolve the target universe and require the explicit `admin` ACL row.
    #    Read directly from list_universe_acl (the platform's per-universe
    #    ownership predicate), NOT universe_access_permission, whose public->
    #    "read" short-circuit would misjudge a public universe. A write-only
    #    collaborator holds "write", never "admin", so this excludes them.
    uid = _request_universe(universe_id)
    base = _base_path()
    admin_rows = [
        row
        for row in list_universe_acl(base, universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin_rows:
        return dict(_NOT_FOUND)

    # 3. Parse the payload and validate the service before any vault touch.
    try:
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    service = str(document.get("service") or "").strip().lower()
    if service not in _SUPPORTED_SERVICES:
        return {
            "error": "unsupported_service",
            "detail": "service must be claude or codex",
            "allowed_services": list(_SUPPORTED_SERVICES),
        }
    material = document.get("auth_material_b64")
    if not isinstance(material, str) or not material.strip():
        return {
            "error": "connection_setup_invalid",
            "detail": "auth_material_b64 is required",
        }
    if len(material) > _MAX_MATERIAL_CHARS:
        return {
            "error": "connection_setup_invalid",
            "detail": "auth_material_b64 is too large",
        }

    # 4. Decode base64 transport -> one llm_subscription record.
    try:
        record = _build_record(service, material.strip())
    except ValueError as exc:
        # str(exc) is one of the fixed, secret-free messages built above.
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    # 5. Owner-scoped vault write. Record wrapped in a LIST — a bare dict is read
    #    as {}.get("credentials", []) -> [], which would clear the vault. The
    #    vault binds `actor` as the first depositor and refuses to transfer an
    #    existing different owner (PermissionError). A malformed record (e.g. bad
    #    Codex base64) raises ValueError before any file/DB mutation.
    udir = _universe_dir(uid)
    try:
        write_credential_vault(udir, [record], owner_user_id=actor, universe_id=uid)
    except PermissionError:
        # This service already has a record owned by a different principal.
        # Refuse the transfer; the existing owned record is unchanged. No secret.
        return {
            "error": "credential_ownership_transfer_unsupported",
            "detail": (
                "this service's credential is owned by another principal; "
                "ownership transfer requires a dedicated flow"
            ),
        }
    except ValueError as exc:
        # Vault-authored, sanitized message (credential_vault never echoes the
        # rejected secret material).
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    except Exception:  # noqa: BLE001 - fail closed on any unexpected storage error
        # e.g. a storage/integrity error mid-write. write_credential_vault is
        # atomic (it restores the prior vault file on failure), so nothing was
        # mutated. Do not surface the raw error — an arbitrary exception string
        # is not a vetted non-secret channel.
        return {
            "error": "deposit_failed",
            "detail": "the deposit could not be completed",
        }

    # 6. The owner just replaced the secret, so the "sign in again" card must go.
    #    Cleared AFTER the write and before the projection: a card still standing
    #    over a working connection is how a fixed setup keeps reading as broken.
    from tinyassets.credential_vault import clear_refresh_rejected

    clear_refresh_rejected(base, universe_id=uid, service=service)

    # 7. Non-secret projection + the serving re-point hint. Never the token,
    #    decoded bytes, or any digest. Deposit is write-only; serving stays held.
    agent_binding_id, expected_revision = _serving_hint(base, universe_id=uid, actor=actor)
    return {
        "status": "deposited",
        "service": service,
        "agent_binding_id": agent_binding_id,
        "expected_revision": expected_revision,
        "next": "write_graph target=agent_binding operation=bind_serving_provider",
        "note": (
            "Deposit is write-only and does not enable serving. Re-point serving "
            "with bind_serving_provider (which bumps the binding revision), then "
            'set_serving {"enabled": true} using the post-bind revision. There is '
            "no switch_provider operation."
        ),
    }


__all__ = ["connect_llm"]
