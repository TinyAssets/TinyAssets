"""Installed acquisition data and admission-bound door PKCE state.

The broker owns the exchange transport and credential custody.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3  # noqa: F401 - the flow store's driver, patched here by tests
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from tinyassets.connection_oauth import pkce
from tinyassets.providers.discovery_presets import bundled_discovery_documents

# The PKCE store, handle grammar and callback are shared with the generic
# OAuth connection flow (``connection_oauth``): one transport, two flows.
FLOW_TTL_SECONDS = pkce.FLOW_TTL_SECONDS
MAX_PENDING = 1000
MAX_PER_OWNER = 10
CALLBACK_PREFIX = pkce.CALLBACK_PREFIX
_HANDLE = pkce.HANDLE_RE
_VERIFIER = pkce.VERIFIER_RE


class HostedAuthError(Exception):
    """Secret-free error code; upstream response bodies are never messages."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class AcquisitionPreset:
    id: str
    digest: str
    display_name: str
    authorize_url: str
    exchange_url: str
    manage_url: str
    inference_url: str
    catalogue_url: str
    benchmark_url: str
    #: Fixed, non-secret query parameters the provider documents for its
    #: authorize page (e.g. a key label). Data only; the flow's own
    #: parameters can never be overridden.
    authorize_params: tuple[tuple[str, str], ...] = ()


_FLOW_PARAMS = frozenset({"callback_url", "code_challenge", "code_challenge_method"})


def _authorize_params(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > 8:
        raise HostedAuthError("invalid_acquisition_preset", 503)
    items = []
    for key, val in sorted(value.items()):
        if (not isinstance(key, str) or not isinstance(val, str) or key in _FLOW_PARAMS
                or not 0 < len(key) <= 64 or not 0 < len(val) <= 128
                or not key.isascii() or not val.isprintable()):
            raise HostedAuthError("invalid_acquisition_preset", 503)
        items.append((key, val))
    return tuple(items)


def load_preset(preset_id: str, *, require_manual_key: bool = False) -> AcquisitionPreset:
    """Only installed data, including its matching discovery contract, is trusted."""
    path = Path(__file__).parent.parent / "providers" / "acquisition_presets.json"
    docs = json.loads(path.read_text(encoding="utf-8"))
    if require_manual_key and (
        not isinstance(docs.get(preset_id), dict)
        or docs[preset_id].get("manual_key_entry") is not True
    ):
        raise HostedAuthError("invalid_model_connection")
    discovery = bundled_discovery_documents()
    if preset_id not in docs or preset_id not in discovery:
        raise HostedAuthError("unknown_model_connection", 404)
    doc = docs[preset_id]
    if doc.get("protocol") != "pkce_user_key_v1":
        raise HostedAuthError("unsupported_acquisition_protocol", 503)
    fields = ("authorize_url", "exchange_url", "manage_url", "inference_url",
              "catalogue_url", "benchmark_url")
    for field in fields:
        parts = urlsplit(doc[field])
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or parts.password or parts.fragment):
            raise HostedAuthError("invalid_acquisition_preset", 503)
        if field in {"authorize_url", "exchange_url"} and parts.query:
            raise HostedAuthError("invalid_acquisition_preset", 503)
    if require_manual_key:
        from tinyassets.providers.discovery_protocols import DiscoveryProtocol

        try:
            contract = DiscoveryProtocol.from_bundled_document(discovery[preset_id])
            contract.validate_urls(doc["catalogue_url"], doc["benchmark_url"])
            if contract.auth_scheme != "bearer" or not contract.account_filtered:
                raise ValueError("manual acquisition requires owner-filtered bearer discovery")
        except (KeyError, TypeError, ValueError):
            raise HostedAuthError("invalid_acquisition_preset", 503) from None
    digest = hashlib.sha256(json.dumps(
        [doc, discovery[preset_id]], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return AcquisitionPreset(id=preset_id, digest=digest,
                             display_name=doc["display_name"],
                             authorize_params=_authorize_params(doc.get("authorize_params")),
                             **{field: doc[field] for field in fields})


@dataclass(frozen=True)
class PendingFlow:
    owner: str
    universe_id: str
    preset_id: str
    preset_digest: str
    challenge: str
    callback_url: str
    expires_at: float
    request_ref: str = ""


@contextmanager
def _authority(owner: str, universe_id: str):
    """Resolve only broker admission; no owner store is available at the door."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.broker.owner_identities import connection_center

    base = _base_path()
    connection_center(base, principal=owner, center=universe_id)
    yield base


_flows = pkce.flows_db
_challenge = pkce.challenge_for
is_callback_path = pkce.is_callback_path


def begin_flow(*, owner: str, universe_id: str, preset_id: str,
               challenge: str, public_resource: str, request_ref: str = "") -> dict[str, str | int]:
    """No network; caller resolves home and empty state before durable binding."""
    if not owner or not universe_id:
        raise HostedAuthError("current_home_required", 409)
    if not isinstance(challenge, str) or not _HANDLE.fullmatch(challenge):
        raise HostedAuthError("invalid_pkce_challenge")
    try:
        origin = urlsplit(public_resource)
        valid = (origin.scheme == "https" and origin.hostname and not origin.username
                 and not origin.password and not origin.query and not origin.fragment)
        origin.port  # Reject malformed ports before constructing a callback.
    except ValueError:
        valid = False
    if not valid:
        raise HostedAuthError("public_callback_unavailable", 503)
    preset = load_preset(preset_id)
    with _authority(owner, universe_id) as base, _flows(base) as (conn, now):
        total, mine = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(owner_user_id = ?), 0) FROM hosted_model_flows",
            (owner,),
        ).fetchone()
        if total >= MAX_PENDING or mine >= MAX_PER_OWNER:
            raise HostedAuthError("too_many_pending_connections", 429)
        handle = secrets.token_urlsafe(32)
        callback_origin = f"{origin.scheme}://{origin.netloc}"
        callback = f"{callback_origin}{CALLBACK_PREFIX}{handle}"
        conn.execute("INSERT INTO hosted_model_flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (pkce.handle_digest(handle), owner, universe_id,
                      preset.id, preset.digest, challenge, callback_origin, now,
                      now + FLOW_TTL_SECONDS, request_ref))
    return {
        "flow": handle, "center": universe_id,
        "authorize_url": preset.authorize_url + "?" + urlencode({
            **dict(preset.authorize_params),
            "callback_url": callback, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }),
        "display_name": preset.display_name,
        "expires_in": FLOW_TTL_SECONDS,
    }


def take_flow(*, handle: str, owner: str, universe_id: str, verifier: str) -> PendingFlow:
    """One terminal exchange attempt, not a retryable provider request.

    Invalid/foreign attempts cannot consume the owner's flow. Once taken, even
    an uncertain network outcome requires explicit reauthorization, never replay.
    Current-home equality is necessary but callers must also recheck admin/setup.
    """
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle) or not owner:
        raise HostedAuthError("unknown_model_connection", 404)
    with _authority(owner, universe_id) as base, _flows(base) as (conn, now):
        digest = pkce.handle_digest(handle)
        row = conn.execute("SELECT * FROM hosted_model_flows WHERE handle_digest = ?",
                           (digest,)).fetchone()
        if row is None or owner != row["owner_user_id"]:
            raise HostedAuthError("unknown_model_connection", 404)
        flow = PendingFlow(row["owner_user_id"], row["bound_home_id"], row["preset_id"],
                           row["preset_digest"], row["challenge"],
                           row["callback_origin"] + CALLBACK_PREFIX + handle, row["expires_at"],
                           row["request_ref"])
        if not (row["created_at"] <= now < flow.expires_at
                and 0 < flow.expires_at - row["created_at"] <= FLOW_TTL_SECONDS):
            raise HostedAuthError("model_connection_expired", 409)
        if universe_id != flow.universe_id:
            raise HostedAuthError("current_home_changed", 409)
        if not isinstance(verifier, str) or not _VERIFIER.fullmatch(verifier):
            raise HostedAuthError("invalid_pkce_verifier")
        if not hmac.compare_digest(_challenge(verifier), flow.challenge):
            raise HostedAuthError("invalid_pkce_verifier")
        if load_preset(flow.preset_id).digest != flow.preset_digest:
            raise HostedAuthError("model_connection_preset_changed", 409)
        if conn.execute("DELETE FROM hosted_model_flows WHERE handle_digest = ?",
                        (digest,)).rowcount != 1:
            raise HostedAuthError("unknown_model_connection", 404)
        return flow




def endpoint_policy(preset: AcquisitionPreset) -> list[dict]:
    """Exact installed endpoints, never catalogue-provided URLs or wildcard scope."""
    import re

    endpoints = []
    for url, method in ((preset.inference_url, "POST"), (preset.catalogue_url, "GET"),
                        (preset.benchmark_url, "GET")):
        parts = urlsplit(url)
        endpoint = {"host": parts.netloc, "path_template": parts.path, "methods": [method]}
        query = parse_qs(parts.query, strict_parsing=True)
        if query:
            if any(len(values) != 1 for values in query.values()):
                raise HostedAuthError("invalid_acquisition_preset", 503)
            endpoint.update(allowed_query=sorted(query), required_query=sorted(query),
                            query_patterns={key: "^" + re.escape(value[0]) + "$"
                                            for key, value in query.items()})
        endpoints.append(endpoint)
    return endpoints
