"""Provider-neutral Realtime voice session broker for the shared app.

Realtime is an auxiliary speech transport, never a TinyAssets writer. It uses a
bounded capability declared on the exact user-owned HTTP connection already
serving the founder's command center. The remote bridge implements the public
TinyAssets voice protocol and may be backed by any service or local resource;
this module contains no service-specific endpoint, model, credential name, or
wire-event vocabulary. SDP signaling stays on the authenticated same-origin
route, so no temporary bearer or remote HTTP URL is exposed to the browser.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

VOICE_PROTOCOL = "tinyassets.voice.v1"
VOICE_DISCLOSURE_VERSION = 3
VOICE_SESSION_MAX_SECONDS = 30 * 60
#: No per-user rate window. ``VOICE_SESSIONS_PER_WINDOW = 10`` and
#: ``VOICE_STATUS_CHECKS_PER_WINDOW = 60`` used to answer 429 to the
#: authenticated OWNER of the universe -- their own account, asking their own
#: universe to listen. An account has exactly two limits, cloud bytes and
#: concurrent agent seats (founder, 2026-09-30), and neither of those is a count
#: of how many times you pressed the microphone button.
#:
#: ``VOICE_SESSION_MAX_SECONDS`` stays: it is the provider's own session length,
#: handed to the provider, not a budget this platform spends.
_MAX_SDP_CHARS = 64 * 1024
_TRUTHY = {"1", "true", "yes", "on"}

ProxyFactory = Callable[[Path, str, "VoiceBinding"], Any]


class RealtimeVoiceError(RuntimeError):
    """A stable, secret-free failure suitable for the app boundary."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class VoiceBinding:
    """Validated, non-secret reference to one universe-owned voice bridge."""

    connection_id: str
    grant_id: str
    protocol: str
    session_url: str
    service_name: str
    privacy_url: str


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def realtime_voice_enabled() -> bool:
    """True when user-owned HTTP transport is available.

    Voice readiness is authorized by the exact current provider capability,
    not by a second host-level product switch.  The generic outbound gate
    remains the fail-closed operational boundary for the credential-blind
    bridge request.
    """

    return _truthy("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED")


def public_voice_config() -> dict[str, Any]:
    """Non-secret configuration injected into the shared browser client."""

    return {
        "enabled": realtime_voice_enabled(),
        "protocol": VOICE_PROTOCOL,
        "disclosure_version": VOICE_DISCLOSURE_VERSION,
        "max_session_seconds": VOICE_SESSION_MAX_SECONDS,
    }


def _resolve_voice_binding(universe_dir: Path, owner_user_id: str) -> VoiceBinding:
    from tinyassets.daemon_server import get_founder_home, list_universe_acl
    from tinyassets.provider_serving_binding import (
        NoServingProvider,
        resolve_current_serving_provider_authority,
    )
    from tinyassets.storage.outbound_connections import (
        ConnectionLedger,
        _enforce_endpoint_allowlist,
        _parse_canonical_https_url,
    )

    base = universe_dir.parent
    universe_id = universe_dir.name
    if get_founder_home(base, owner_user_id) != universe_id:
        raise RealtimeVoiceError("voice_authority_invalid", 409)
    if not any(
        row.get("actor_id") == owner_user_id and row.get("permission") == "admin"
        for row in list_universe_acl(base, universe_id=universe_id)
    ):
        raise RealtimeVoiceError("voice_authority_invalid", 409)
    try:
        authority = resolve_current_serving_provider_authority(
            base,
            universe_dir=universe_dir,
            universe_id=universe_id,
            owner_user_id=owner_user_id,
        )
    except (NoServingProvider, RealtimeVoiceError):
        raise
    except Exception as exc:
        raise RealtimeVoiceError("voice_authority_invalid", 409) from exc
    if authority.access_method != "api_key_http":
        raise RealtimeVoiceError("provider_voice_unsupported", 409)

    db_path = base / "outbound.db"
    if db_path.is_symlink() or not db_path.is_file():
        raise RealtimeVoiceError("voice_authority_invalid", 409)
    ledger = ConnectionLedger(db_path)
    grant = ledger.get_grant(authority.grant_id)
    view = ledger.get_connection_view(authority.connection_id)
    authorized = bool(
        grant is not None
        and view is not None
        and grant.revoked_at is None
        and view.revoked_at is None
        and grant.connection_id == authority.connection_id
        and grant.owner_user_id == owner_user_id
        and view.owner_user_id == owner_user_id
        and grant.universe_id == universe_id
        and view.connection_type == "http"
        and "POST" in view.scopes
    )
    if not authorized:
        raise RealtimeVoiceError("voice_authority_invalid", 409)
    try:
        capability = ledger.get_connection_capability(
            authority.connection_id, "realtime_voice"
        )
    except Exception as exc:
        raise RealtimeVoiceError("voice_capability_invalid", 409) from exc
    if capability is None:
        raise RealtimeVoiceError("capability_not_declared", 409)
    try:
        canonical = _parse_canonical_https_url(
            capability.session_url, allowed_ports=frozenset({443})
        )
        _enforce_endpoint_allowlist(
            canonical,
            "POST",
            view.allowed_endpoints,
            view.access_mode,
        )
    except Exception as exc:
        raise RealtimeVoiceError("voice_capability_invalid", 409) from exc
    return VoiceBinding(
        connection_id=authority.connection_id,
        grant_id=authority.grant_id,
        protocol=capability.protocol,
        session_url=capability.session_url,
        service_name=capability.service_name,
        privacy_url=capability.privacy_url,
    )


def voice_capability(
    universe_dir: str | Path | None, owner_user_id: str = ""
) -> dict[str, Any]:
    """Return a secret-free view of one command center's bound voice capability."""

    if universe_dir is None or not owner_user_id:
        return {
            "available": False,
            "state": "unpowered",
            "reason": "no_home_universe",
            "remediation": "none",
        }
    universe = Path(universe_dir).resolve()
    try:
        from tinyassets.provider_serving_binding import NoServingProvider

        binding = _resolve_voice_binding(universe, owner_user_id)
    except RealtimeVoiceError as exc:
        remediation = (
            "existing_connection_surface"
            if exc.code
            in {
                "provider_voice_unsupported",
                "capability_not_declared",
                "voice_capability_invalid",
                "voice_authority_invalid",
            }
            else "none"
        )
        return {
            "available": False,
            "state": "incompatible",
            "reason": exc.code,
            "remediation": remediation,
        }
    except NoServingProvider:
        return {
            "available": False,
            "state": "unpowered",
            "reason": "provider_not_configured",
            "remediation": "existing_connection_surface",
        }
    except Exception:
        return {
            "available": False,
            "state": "incompatible",
            "reason": "voice_authority_invalid",
            "remediation": "none",
        }
    disclosure_id = sha256(
        "\0".join(
            (
                binding.connection_id,
                binding.protocol,
                binding.session_url,
                binding.service_name,
                binding.privacy_url,
            )
        ).encode("utf-8")
    ).hexdigest()
    if not realtime_voice_enabled():
        return {
            "available": False,
            "state": "disabled",
            "reason": "voice_disabled",
            "remediation": "none",
        }
    return {
        "available": True,
        "state": "ready",
        "remediation": "none",
        "resource": "user_bound_voice_connection",
        "disclosure_id": disclosure_id,
        "service_name": binding.service_name,
        "privacy_url": binding.privacy_url,
    }


def session_request(offer_sdp: str) -> dict[str, Any]:
    """Provider-neutral bridge contract; the browser cannot widen it."""

    return {
        "protocol": VOICE_PROTOCOL,
        "offer_sdp": offer_sdp,
        "session": {
            "instructions": (
                "Act only as the speech interface for this command center. For each "
                "completed utterance, invoke converse exactly once. Speak only "
                "the returned tool result and add nothing."
            ),
            "turn_detection": {
                "mode": "semantic",
                "eagerness": "medium",
                "interrupt_output": True,
            },
            "tool": {
                "name": "converse",
                "description": (
                    "Send the founder's complete spoken turn to their command center."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
            "output": {
                "mode": "audio",
                "source": "tool_result",
                "verbatim": True,
            },
        },
    }


def _default_proxy_factory(
    universe_dir: Path, owner_user_id: str, binding: VoiceBinding
) -> Any:
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(
        universe_dir.parent / "outbound.db",
        verify_authenticated_principal=lambda: owner_user_id,
    )
    return ledger.resolve_exact_scoped_proxy(
        universe_id=universe_dir.name,
        grant_id=binding.grant_id,
        connection_id=binding.connection_id,
    )


def _response_document(response: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(response, dict):
        raise RealtimeVoiceError("voice_resource_bad_response", 502)
    status = response.get("status", 200)
    if not isinstance(status, int) or isinstance(status, bool):
        raise RealtimeVoiceError("voice_resource_bad_response", 502)
    raw = response.get("body", response)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RealtimeVoiceError("voice_resource_bad_response", 502) from exc
    if not isinstance(raw, dict):
        raise RealtimeVoiceError("voice_resource_bad_response", 502)
    return status, raw


async def create_voice_session(
    universe_dir: str | Path,
    owner_user_id: str,
    offer_sdp: str,
    *,
    proxy_factory: ProxyFactory = _default_proxy_factory,
) -> dict[str, Any]:
    """Exchange one bounded browser SDP offer through the bound voice bridge."""

    if not realtime_voice_enabled():
        raise RealtimeVoiceError("voice_disabled", 404)
    if (
        not isinstance(offer_sdp, str)
        or not 1 <= len(offer_sdp) <= _MAX_SDP_CHARS
        or not offer_sdp.startswith("v=0")
        or "\x00" in offer_sdp
    ):
        raise RealtimeVoiceError("voice_session_offer_invalid", 400)
    from starlette.concurrency import run_in_threadpool

    universe = Path(universe_dir).resolve()

    def resolve_binding() -> VoiceBinding:
        from tinyassets.provider_serving_binding import NoServingProvider

        try:
            return _resolve_voice_binding(universe, owner_user_id)
        except NoServingProvider as exc:
            raise RealtimeVoiceError("provider_not_configured", 409) from exc

    binding = await run_in_threadpool(resolve_binding)

    def request_session() -> Any:
        proxy = None
        try:
            proxy = proxy_factory(universe, owner_user_id, binding)
            return proxy.request(
                "POST",
                {
                    "url": binding.session_url,
                    "headers": {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    "body": session_request(offer_sdp),
                },
            )
        finally:
            if proxy is not None:
                try:
                    proxy.close()
                except Exception:
                    pass

    try:
        response = await run_in_threadpool(request_session)
    except RealtimeVoiceError:
        raise
    except Exception as exc:
        raise RealtimeVoiceError("voice_resource_unreachable", 502) from exc

    status, payload = _response_document(response)
    if status == 429:
        raise RealtimeVoiceError("voice_resource_rate_limited", 503)
    if status in {401, 403}:
        raise RealtimeVoiceError("voice_resource_rejected", 409)
    if not 200 <= status < 300:
        raise RealtimeVoiceError("voice_resource_failed", 502)
    if payload.get("protocol") != VOICE_PROTOCOL:
        raise RealtimeVoiceError("voice_resource_bad_response", 502)
    answer_sdp = payload.get("answer_sdp")
    if (
        not isinstance(answer_sdp, str)
        or not 1 <= len(answer_sdp) <= _MAX_SDP_CHARS
        or not answer_sdp.startswith("v=0")
        or "\x00" in answer_sdp
    ):
        raise RealtimeVoiceError("voice_resource_bad_response", 502)
    try:
        expires_at = int(payload.get("expires_at") or 0)
        requested_limit = int(payload.get("max_session_seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise RealtimeVoiceError("voice_resource_bad_response", 502) from exc
    session_limit = max(
        60,
        min(
            requested_limit or VOICE_SESSION_MAX_SECONDS,
            VOICE_SESSION_MAX_SECONDS,
        ),
    )
    return {
        "protocol": VOICE_PROTOCOL,
        "answer_sdp": answer_sdp,
        "expires_at": expires_at,
        "max_session_seconds": session_limit,
    }


__all__ = [
    "VOICE_DISCLOSURE_VERSION",
    "VOICE_PROTOCOL",
    "RealtimeVoiceError",
    "VoiceBinding",
    "create_voice_session",
    "public_voice_config",
    "realtime_voice_enabled",
    "session_request",
    "voice_capability",
]
