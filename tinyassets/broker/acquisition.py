"""Preset PKCE key exchange. Only the credential broker calls this transport."""
import asyncio
import hmac
import json
import time
from typing import Callable

import httpx

from tinyassets.onboarding.hosted_model_auth import (
    _VERIFIER,
    HostedAuthError,
    PendingFlow,
    _challenge,
    load_preset,
)

MAX_RESPONSE_BYTES = 16384
EXCHANGE_TIMEOUT = 20.0


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT, follow_redirects=False)


async def exchange_key(*, flow: PendingFlow, code: str, verifier: str,
                       client_factory: Callable[[], httpx.AsyncClient] = _default_client) -> str:
    """Exchange exactly once; returns a SERVER-ONLY key, never user-facing JSON."""
    if (not isinstance(code, str) or not 1 <= len(code) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in code)):
        raise HostedAuthError("invalid_authorization_code")
    if not isinstance(verifier, str) or not _VERIFIER.fullmatch(verifier):
        raise HostedAuthError("invalid_pkce_verifier")
    if not hmac.compare_digest(_challenge(verifier), flow.challenge):
        raise HostedAuthError("invalid_pkce_verifier")
    preset = load_preset(flow.preset_id)
    if preset.digest != flow.preset_digest or flow.expires_at <= time.time():
        raise HostedAuthError("model_connection_expired", 409)
    try:
        async with asyncio.timeout(EXCHANGE_TIMEOUT):
            async with client_factory() as client:
                async with client.stream(
                    "POST", preset.exchange_url, follow_redirects=False,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                    json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
                ) as response:
                    if response.status_code != 200:
                        # No retry: a provider may already have consumed the code.
                        raise HostedAuthError("model_authorization_not_completed", 502)
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise HostedAuthError("model_authorization_response_invalid", 502)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=MAX_RESPONSE_BYTES + 1):
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise HostedAuthError("model_authorization_response_invalid", 502)
                        body.extend(chunk)
    except (httpx.HTTPError, TimeoutError):
        raise HostedAuthError("model_authorization_outcome_unknown", 502) from None
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HostedAuthError("model_authorization_response_invalid", 502) from None
    key = doc.get("key") if isinstance(doc, dict) else None
    if (not isinstance(key, str) or not 1 <= len(key) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise HostedAuthError("model_authorization_response_invalid", 502)
    return key
