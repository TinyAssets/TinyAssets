"""Read discovery JSON through existing owner-scoped, credential-blind HTTP.

Internal transport only: no profile publication, inference, ranking or cache.
The caller supplies server-derived owner/universe context and a configured URL;
remote catalogue links are never followed. A successful read is evidence, not
inference authority or proof of account-filtered catalogue semantics.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tinyassets.exceptions import (
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from tinyassets.providers.definition import ProviderDefinition
from tinyassets.storage.outbound_connections import (
    _SSRF_MAX_BODY_BYTES,
    ConnectionLedger,
    GrantResolutionError,
    ProxyRequestError,
    SsrfValidationError,
    _broker_channel,
    _canonical_request_url,
    _enforce_endpoint_allowlist,
    _parse_canonical_https_url,
    _resource_from_row,
    _verb_within_scopes,
)


class ModelDiscoveryUnavailable(ProviderUnavailableError):
    """Fixed, credential-blind reasons for discovery and repair displays."""

    _MESSAGES = {
        "discovery_unavailable": "model discovery context is unavailable",
        "source_revoked": "model discovery context changed or is unavailable",
        "missing_discovery_scope": "model discovery permission is unavailable",
        "protocol_mismatch": "model discovery protocol is incompatible",
        "discovery_expired": "model discovery is outside its freshness window",
    }

    def __init__(self, reason: str):
        if reason not in self._MESSAGES:
            raise ValueError("invalid model discovery failure reason")
        super().__init__(self._MESSAGES[reason])
        self.reason = reason


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def read_http_discovery_document(
    *,
    db_path: Path,
    definition: ProviderDefinition,
    owner_user_id: str,
    universe_id: str,
    url: str,
    json_mode: str = "legacy",
) -> dict[str, Any] | list[Any]:
    """Make one granted GET; never retry, redirect or change connection state.

    `definition` comes from the verified provider store; registration is not
    authority. The current grant/resource and supplied authenticated context
    must agree, both here and in the exact proxy resolver. The broker rechecks
    live authority, scope, endpoint restrictions and SSRF at dispatch. No secret
    resolver or header override is exposed to the caller.

    New compiled contracts may request exact numbers and array-root documents.
    The default retains the legacy object-root/float interpretation. This only
    changes parsing after the same granted GET; remote metadata cannot choose it.
    """
    if type(json_mode) is not str or json_mode not in {"legacy", "exact"}:
        raise ValueError("invalid discovery JSON mode")
    if (
        not owner_user_id
        or not universe_id
        or definition.owner_user_id != owner_user_id
        or definition.universe_id != universe_id
        or definition.access_method != "api_key_http"
    ):
        raise ModelDiscoveryUnavailable("source_revoked")
    return read_granted_discovery_document(
        db_path=db_path, grant_id=definition.ref, owner_user_id=owner_user_id,
        universe_id=universe_id, url=url, json_mode=json_mode,
    )


def read_granted_discovery_document(
    *, db_path: Path, grant_id: str, owner_user_id: str, universe_id: str,
    url: str, json_mode: str = "legacy",
) -> dict[str, Any] | list[Any]:
    """Same exact broker authority, usable before a model descriptor exists.

    An owned HTTP grant is sufficient for its allowed catalogue GET. Requiring
    a model descriptor first would force bootstrap to invent a model identifier.
    This grants no inference, follows no links and never handles credentials.
    """
    if type(json_mode) is not str or json_mode not in {"legacy", "exact"}:
        raise ValueError("invalid discovery JSON mode")
    if not owner_user_id or not universe_id or not grant_id:
        raise ModelDiscoveryUnavailable("source_revoked")
    from tinyassets.broker.supervisor import broker_selected

    selected = broker_selected()
    ledger = None
    if selected:
        from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, query_ledger

        try:
            facts = query_ledger(Path(db_path).parent, query=GRANTED_RESOURCE,
                                 principal=owner_user_id, command_center=universe_id,
                                 grant_id=grant_id)
            if not isinstance(facts.get("resource"), dict):
                raise ValueError("invalid broker resource projection")
            view = _resource_from_row(facts["resource"]).to_view()
        except GrantResolutionError:
            raise ModelDiscoveryUnavailable("source_revoked") from None
        except (OSError, LookupError, TypeError, ValueError, ProxyRequestError):
            raise ModelDiscoveryUnavailable("discovery_unavailable") from None
    else:
        ledger = ConnectionLedger(
            Path(db_path), verify_authenticated_principal=lambda: owner_user_id)
        grant = ledger.get_grant(grant_id)
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.owner_user_id != owner_user_id
            or grant.universe_id != universe_id
        ):
            raise ModelDiscoveryUnavailable("source_revoked")
        view = ledger.get_connection_view(grant.connection_id)
    if (
        view is None
        or view.revoked_at is not None
        or view.owner_user_id != owner_user_id
        or view.connection_type != "http"
    ):
        raise ModelDiscoveryUnavailable("source_revoked")
    if not _verb_within_scopes("GET", view.scopes, view.access_mode):
        raise ModelDiscoveryUnavailable("missing_discovery_scope")
    try:
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError("invalid URL")
        canonical = _parse_canonical_https_url(url, allowed_ports=frozenset({443}))
        _enforce_endpoint_allowlist(canonical, "GET", view.allowed_endpoints, view.access_mode)
    except (SsrfValidationError, ValueError):
        raise ModelDiscoveryUnavailable("missing_discovery_scope") from None

    # A bounded, blocking broker operation. Async ingress must offload this call;
    # cancellation must not start a replacement until this operation settles.
    try:
        if selected:
            proxy = _broker_channel(Path(db_path).parent, principal=owner_user_id,
                                    command_center=universe_id, grant_id=grant_id,
                                    connection_id=view.connection_id)
        else:
            proxy = ledger.resolve_exact_scoped_proxy(
                universe_id=universe_id, grant_id=grant_id,
                connection_id=view.connection_id,
            )
        try:
            result = proxy.request("GET", {"url": _canonical_request_url(canonical)})
        finally:
            proxy.close()
    except Exception:
        # Never return upstream/broker exception strings, URLs, headers or bodies.
        raise ProviderUnavailableError("discovery transport failed") from None

    if not isinstance(result, dict) or type(result.get("status")) is not int:
        raise ProviderUnavailableError("discovery proxy returned no valid HTTP status")
    status = result["status"]
    if status == 429:
        raise ProviderRateLimitedError("discovery provider rate limited (429)")
    if 500 <= status < 600:
        raise ProviderOverloadedError(f"discovery provider error (HTTP {status})")
    # 206 is partial evidence; redirects are not a second discovery destination.
    if status != 200:
        raise ProviderProtocolError(f"discovery provider returned HTTP {status}")
    body = result.get("body")
    try:
        if (
            not isinstance(body, str)
            or not body
            or len(body) > _SSRF_MAX_BODY_BYTES
            or len(body.encode("utf-8")) > _SSRF_MAX_BODY_BYTES
        ):
            raise ValueError("invalid response size")
        parsed = json.loads(
            body, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            **({"parse_float": Decimal} if json_mode == "exact" else {}),
        )
        if not isinstance(parsed, (dict, list) if json_mode == "exact" else dict):
            raise ValueError("invalid response shape")
    except (ValueError, RecursionError, InvalidOperation):
        message = ("discovery response is not a bounded JSON object" if json_mode == "legacy"
                   else "discovery response is not a bounded JSON document")
        raise ProviderProtocolError(message) from None
    return parsed
