"""Outbound connection resources, per-universe grants, and scoped proxies."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import math
import multiprocessing
import os
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from tinyassets import __version__ as _tinyassets_version
from tinyassets.storage.workspace_authority import (
    is_git_scope,
    normalize_git_host,
    validate_git_scopes,
)

AuthenticatedPrincipalVerifier = Callable[[], str]
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundEndpoint:
    """One allowlisted egress target for an ``http`` connection (design.md D2/D3).

    ``host`` is an exact hostname (lower-cased), ``path_template`` a ``/``-rooted
    template, ``methods`` the HTTP verbs permitted. The allowlist is the REAL
    confidentiality/egress boundary — a caller-supplied URL that does not match
    one of these is refused before any socket is opened.

    A ``{param}`` segment does NOT match "any non-empty segment": every
    placeholder MUST carry a declared value pattern in ``param_patterns`` (name →
    anchored regex the whole segment must full-match), so a tenant/target/id in a
    path segment cannot silently address a different account (Codex FIX 3).
    ``allowed_query`` names the ONLY query parameters permitted — an undeclared
    query parameter is REFUSED, never dropped — and ``query_patterns`` optionally
    constrains a declared query value. ``required_query`` names query parameters
    that MUST be present EXACTLY ONCE (a subset of ``allowed_query``), so an
    endpoint whose semantics depend on a validated parameter (github's contents
    ``?ref=`` — Codex FIX: "require exactly one validated ref query") cannot be
    called without it or with a duplicate. ``param_patterns``/``query_patterns``
    are stored as sorted ``(name, regex)`` pairs so the dataclass stays hashable.
    """

    host: str
    path_template: str
    methods: tuple[str, ...]
    param_patterns: tuple[tuple[str, str], ...] = ()
    allowed_query: tuple[str, ...] = ()
    query_patterns: tuple[tuple[str, str], ...] = ()
    required_query: tuple[str, ...] = ()
    redirect_mode: str = "none"

    def as_dict(self) -> dict[str, object]:
        # The reserved capability-URL pattern is DERIVED from the template, so it
        # is never written out: the template is the one definition of that fact,
        # and round-tripping the derived copy through the validator (which
        # refuses a declared `secret` pattern) would make a stored endpoint
        # unreadable the moment it was read back.
        patterns = {
            name: pat
            for name, pat in self.param_patterns
            if not (
                name == _URL_SECRET_PLACEHOLDER_NAME
                and (
                    _URL_SECRET_TOKEN in self.path_template.split("/")
                    or _URL_SECRET_REST_TOKEN in self.path_template.split("/")
                )
            )
        }
        document = {
            "host": self.host,
            "path_template": self.path_template,
            "methods": list(self.methods),
            "param_patterns": patterns,
            "allowed_query": list(self.allowed_query),
            "query_patterns": {name: pat for name, pat in self.query_patterns},
            "required_query": list(self.required_query),
        }
        # Preserve legacy policy bytes and consent identity for no-follow.
        # Only an explicit added permission appears in stored/projected policy.
        if self.redirect_mode != "none":
            document["redirect_mode"] = self.redirect_mode
        return document


#: A connection is granted one of two ways (full-channel-access D2).
#: ``exact``: the declared endpoints, verbs and repositories, and nothing else.
#: ``full``: anything the key itself can do on the channel's declared hosts.
#: The mode is the authority; nothing is ever STORED as a wildcard, so no
#: ``*/*`` reaches the git transport or a consent row.
ACCESS_EXACT = "exact"
ACCESS_FULL = "full"
ACCESS_MODES = (ACCESS_EXACT, ACCESS_FULL)


def normalize_access_mode(value: Any) -> str:
    """``exact`` or ``full``; anything else raises. Empty means ``exact`` so a
    row written before the column existed reads as least privilege."""
    text = ("" if value is None else str(value)).strip().lower()
    if not text:
        return ACCESS_EXACT
    if text not in ACCESS_MODES:
        raise ValueError(f"access_mode must be one of {ACCESS_MODES}, got {value!r}")
    return text


@dataclass(frozen=True, repr=False)
class ConnectionResource:
    """Credential-BEARING connection record — for trusted server-internal use.

    Carries ``credential_ref`` (the vault reference) and MUST NOT be returned to
    an adapter/graph/CRUD/list/evidence surface — those use :class:`ConnectionView`
    (no ``credential_ref``). Redaction is made STRUCTURAL, not conventional
    (Codex): ``__repr__`` masks ``credential_ref`` so stringifying/logging a
    resource (into an exception, evidence dict, or log line) never reveals it,
    while the attribute stays reachable for the broker child and the internal
    ownership/conflict checks that must compare it explicitly.
    """

    connection_id: str
    owner_user_id: str
    connection_class: str
    scopes: tuple[str, ...]
    provider: str
    destination: str
    credential_ref: str
    revoked_at: float | None
    #: The channel-type registry key (design.md D2). Empty for legacy
    #: github/slack connections that predate the descriptor; "http" selects the
    #: general SSRF-hardened driver.
    connection_type: str = ""
    #: How the child applies the credential (design.md D5): "bearer" | "basic" |
    #: "header" | "none". Empty for legacy connections.
    auth_scheme: str = ""
    #: The per-connection egress allowlist (design.md D3). Empty ⇒ no call.
    allowed_endpoints: tuple[OutboundEndpoint, ...] = ()
    #: ``exact`` | ``full`` (full-channel-access D2). Read by the egress
    #: allowlist, the git-scope check and the workspace consents; never by the
    #: SSRF safety checks, which run for both modes.
    access_mode: str = ACCESS_EXACT
    #: The host git operations use, when the owner declared one at connect time
    #: (a forge whose git transport is not its API host). Empty: the connection's
    #: own endpoint host. Never defaulted per service.
    git_host: str = ""

    def __repr__(self) -> str:
        return (
            "ConnectionResource("
            f"connection_id={self.connection_id!r}, "
            f"owner_user_id={self.owner_user_id!r}, "
            f"connection_class={self.connection_class!r}, "
            f"scopes={self.scopes!r}, "
            f"provider={self.provider!r}, "
            f"destination={self.destination!r}, "
            "credential_ref='***redacted***', "
            f"revoked_at={self.revoked_at!r}, "
            f"connection_type={self.connection_type!r}, "
            f"auth_scheme={self.auth_scheme!r}, "
            f"allowed_endpoints={self.allowed_endpoints!r}, "
            f"access_mode={self.access_mode!r}, "
            f"git_host={self.git_host!r})"
        )

    def to_view(self) -> ConnectionView:
        """The ONLY shape any caller/CRUD/list/evidence path may see (design.md D2).

        ``credential_ref`` — the vault reference — is deliberately dropped so no
        projection can leak it.
        """
        return ConnectionView(
            connection_id=self.connection_id,
            owner_user_id=self.owner_user_id,
            connection_class=self.connection_class,
            scopes=self.scopes,
            provider=self.provider,
            connection_type=self.connection_type,
            auth_scheme=self.auth_scheme,
            allowed_endpoints=self.allowed_endpoints,
            destination=self.destination,
            revoked_at=self.revoked_at,
            access_mode=self.access_mode,
            git_host=self.git_host,
        )


@dataclass(frozen=True)
class ConnectionView:
    """Redacted connection projection — carries NO ``credential_ref``/secret.

    Everything a caller/CRUD/list/evidence path may see EXCEPT the vault
    ``credential_ref``: there is no such field, so ``vars()``/``asdict()``/repr
    cannot expose it (Codex FIX 3, structural redaction). ``provider`` is not a
    secret and is included so existing owner projections keep working.
    """

    connection_id: str
    owner_user_id: str
    connection_class: str
    scopes: tuple[str, ...]
    provider: str
    connection_type: str
    auth_scheme: str
    allowed_endpoints: tuple[OutboundEndpoint, ...]
    destination: str
    revoked_at: float | None
    #: ``exact`` | ``full`` (full-channel-access D2). Rendered to the owner as
    #: the ONE sentence that says what a full grant means; never as a wildcard
    #: endpoint row, because none is stored.
    access_mode: str = ACCESS_EXACT
    #: The declared git host, or empty (see :class:`ConnectionResource`).
    git_host: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "owner_user_id": self.owner_user_id,
            "connection_class": self.connection_class,
            "scopes": list(self.scopes),
            "provider": self.provider,
            "connection_type": self.connection_type,
            "auth_scheme": self.auth_scheme,
            "allowed_endpoints": [ep.as_dict() for ep in self.allowed_endpoints],
            "destination": self.destination,
            "revoked_at": self.revoked_at,
            "access_mode": self.access_mode,
            "git_host": self.git_host,
        }


@dataclass(frozen=True)
class ConnectionGrant:
    grant_id: str
    connection_id: str
    owner_user_id: str
    universe_id: str
    granted_at: float
    revoked_at: float | None
    unprompted_action_cap: ActionCap | None


@dataclass(frozen=True, slots=True)
class ConnectionCapability:
    """Bounded, credential-free metadata attached to one connection."""

    connection_id: str
    capability_kind: str
    protocol: str
    session_url: str
    service_name: str
    privacy_url: str

    def descriptor(self) -> dict[str, str]:
        result = {
            "protocol": self.protocol,
            "session_url": self.session_url,
            "service_name": self.service_name,
        }
        if self.privacy_url:
            result["privacy_url"] = self.privacy_url
        return result


@dataclass(frozen=True, slots=True)
class ModelDiscoveryCapability:
    """Connection-scoped discovery metadata, not model or endpoint authority."""

    connection_id: str
    capability_kind: str
    protocol: str
    catalogue_url: str
    benchmark_url: str
    contract_json: str = ""

    def descriptor(self) -> dict[str, Any]:
        result = (
            {"schema_version": 1, "catalogue_url": self.catalogue_url,
             "contract": json.loads(self.contract_json)}
            if self.contract_json else
            {"protocol": self.protocol, "catalogue_url": self.catalogue_url}
        )
        if self.benchmark_url:
            result["benchmark_url"] = self.benchmark_url
        return result

    def execution_contract(self):
        """Resolve validated metadata, never an execution or semantic-trust grant."""
        if self.contract_json:
            from tinyassets.providers.discovery_contract import SourceContract

            return SourceContract.compile(json.loads(self.contract_json))
        from tinyassets.providers.discovery_protocols import discovery_protocol

        return discovery_protocol(self.protocol)


@dataclass(frozen=True, slots=True)
class ModelUseCapability:
    """A connection's ``model`` use: which wire it speaks and which models it serves.

    Declared by the owner (or their agent) as data. It names a bundled wire
    dialect by structure, a static model list, and a billing class. It is
    metadata, never authority: serving still needs the owner's accepted model
    access, and ``free``/``flat`` billing admits no spending.
    """

    connection_id: str
    capability_kind: str
    wire: str
    models: tuple[tuple[str, bool, int], ...]
    billing: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "wire": self.wire,
            "models": [
                {"id": model_id, "tools": tools, "context": context}
                for model_id, tools, context in self.models
            ],
            "billing": self.billing,
        }


@dataclass(frozen=True, slots=True)
class ConstantHeadersCapability:
    """Non-secret headers the broker adds to every call on this connection.

    For the version and API-shape headers a service requires on each request
    (``X-Api-Version: 2``), so a workflow node never retypes them. Never a
    credential: auth stays in the vault and the auth scheme, which the driver
    applies after these, so a constant header cannot replace it.
    """

    connection_id: str
    capability_kind: str
    headers: tuple[tuple[str, str], ...]

    def descriptor(self) -> dict[str, Any]:
        return {"headers": dict(self.headers)}


@dataclass(frozen=True)
class ActionCap:
    name: str
    maximum: float
    unit: str

    def __post_init__(self) -> None:
        _required("cap name", self.name)
        _required("cap unit", self.unit)
        if not math.isfinite(self.maximum):
            raise ValueError("cap maximum must be finite")
        if self.maximum < 0:
            raise ValueError("cap maximum must be non-negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "maximum": self.maximum,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class CapDecision:
    status: str
    cap: ActionCap | None
    action_value: float
    action_unit: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "cap": self.cap.as_dict() if self.cap is not None else None,
            "action_value": self.action_value,
            "action_unit": self.action_unit,
            "authorization_axis": "unprompted_action",
        }


@dataclass(frozen=True)
class ConnectorArtifact:
    artifact_id: str
    owner_user_id: str
    connector_definition: dict[str, Any]
    mcp_client_config: dict[str, Any]
    parent_artifact_id: str | None
    attribution: tuple[str, ...]
    created_at: float


class GrantResolutionError(RuntimeError):
    """No single current grant can authorize the requested connection."""


class ProxyRequestError(RuntimeError):
    """A scoped proxy request failed without exposing destination internals."""


class AmbiguousProxyOutcome(RuntimeError):
    """The destination may have applied the request before transport failed."""


class BrokerStreamStop(Exception):
    """The broker stopped a stream (cancelled, past its deadline, fenced).

    Raised from the broker's own checks inside a send. Never converted into a
    destination failure by the transport, so the broker reports its real cause.
    """


class _InferenceAccountingStop(BrokerStreamStop):
    """Carry the typed parent stop through transport cleanup without flattening it."""

    def __init__(self, cause):
        self.cause = cause
        super().__init__("parent inference accounting stopped")


def _settle_usage_after_error(usage, outcome):
    if usage is None:
        return
    try:
        usage.settle(outcome)
    except Exception:  # noqa: BLE001 - durable dispatched state remains conservative
        _LOG.warning("could not finalize inference usage after transport cleanup")


class SsrfValidationError(ProxyRequestError):
    """A general outbound HTTP request was refused by the strict egress guard.

    Subclasses ``ProxyRequestError`` so it flows through the broker's existing
    secret-free error hygiene (``_adapter_safe_proxy_error`` reduces it to a
    fixed string across the process boundary). Its own messages are FIXED —
    they never echo the offending URL, header, or address, because those can
    themselves carry credential material (e.g. ``https://user:pass@host``).
    """


class OutboundDeadlineExceeded(SsrfValidationError):
    """The destination did not finish answering inside the request's time budget.

    Crosses the process boundary TYPED, with a fixed message, so the caller can
    say "the model took too long" instead of "we could not identify why" (live
    2026-09-29, turn b804819f: a free model writing an app ran past the old 30s
    total and the owner was told the cause was unknown).
    """


class ConnectionAuthorizationError(ProxyRequestError):
    """The connection's authorization could not be made current.

    Raised when an ``oauth2`` refresh fails (the token endpoint refused, the
    provider issued no refresh token, or the rotated token could not be
    saved). It is an ordinary connection failure, recorded as structured
    fields rather than a vendor message: stage ``connection``, class ``auth``,
    and the token endpoint's own bounded, secret-free words as the detail.
    """

    STAGE = "connection"
    CLASS = "auth"

    def __init__(self, detail: str = "") -> None:
        super().__init__("outbound request failed: connection authorization failed")
        self.detail = str(detail or "")[:200]

    @property
    def failure(self) -> dict[str, str]:
        record = {"stage": self.STAGE, "class": self.CLASS}
        if self.detail:
            record["provider_detail"] = self.detail
        return record


_MAX_PROXY_FRAME_BYTES = 16 * 1024 * 1024


def _adapter_safe_proxy_error(exc: BaseException) -> str:
    """Return the only error text allowed across the adapter process boundary."""
    if isinstance(exc, AmbiguousProxyOutcome):
        return "destination outcome ambiguous"
    if isinstance(exc, GrantResolutionError):
        return "outbound connection grant unavailable"
    if isinstance(exc, PermissionError):
        return "outbound request not permitted"
    if isinstance(exc, OutboundDeadlineExceeded):
        return "outbound request exceeded its time budget"
    return "outbound request failed"


def _send_message(channel: Any, value: object) -> None:
    try:
        # UTF-8 as UTF-8: ``\uXXXX`` escaping made non-ASCII text up to six
        # times larger, so a reply under its body cap could still overflow the
        # frame (Codex, 2026-10-02). Quote and backslash escaping still double.
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProxyRequestError(
            "outbound proxy messages must use the redacted JSON contract"
        ) from exc
    if len(payload) > _MAX_PROXY_FRAME_BYTES:
        raise ProxyRequestError("outbound proxy message exceeds the size limit")
    channel.send_bytes(payload)


def _receive_message(channel: Any) -> object:
    try:
        payload = channel.recv_bytes(_MAX_PROXY_FRAME_BYTES)
        return json.loads(payload.decode("utf-8"))
    except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProxyRequestError("outbound proxy received an invalid message") from exc


def _load_dispatch_factory(
    factory_reference: str,
    config: dict[str, Any],
) -> Callable[[str, str, object], Any]:
    factory = _TRUSTED_DISPATCH_FACTORIES.get(factory_reference)
    if factory is None:
        raise ValueError("dispatch factory is not in the trusted registry")
    dispatch = factory(config)
    if not callable(dispatch):
        raise TypeError("dispatch factory must return a callable")
    return dispatch


#: Environment variables the spawned broker child must never carry: TLS
#: key-logging (would leak the outbound TLS session key, defeating
#: credential-blindness) and ambient proxy routing (an SSRF/exfil vector the
#: transport disables structurally; dropping it here also keeps any other TLS
#: context created in the child — e.g. the GitHub read driver's plain urlopen —
#: from honoring an ambient proxy or logging keys). The child is a spawned
#: process, so this pop never touches the parent's environment.
_SSRF_CHILD_ENV_DENYLIST = (
    "SSLKEYLOGFILE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
    "no_proxy",
)


def _sanitize_child_environment() -> None:
    for name in _SSRF_CHILD_ENV_DENYLIST:
        os.environ.pop(name, None)


#: Default-OFF flag gating the general ``http`` connection path. Until a
#: deployment sets this truthy, an ``http`` connection fails closed even if one
#: is created — nothing routes through the general driver by default.
_OUTBOUND_HTTP_FLAG = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"

#: Handshake budget for the spawned broker child, and the var that overrides it.
_PROXY_STARTUP_TIMEOUT_VAR = "TINYASSETS_OUTBOUND_PROXY_STARTUP_TIMEOUT_S"
#: Measured: the broker module is pure-stdlib and a fresh interpreter imports it
#: in ~0.13s, so this is ~100x headroom, not a guess. Kept well under the cap
#: because the wait occupies a run-executor thread.
_DEFAULT_PROXY_STARTUP_TIMEOUT_S = 15.0
_MAX_PROXY_STARTUP_TIMEOUT_S = 120.0

#: The only connection_type values a connection may be created with. Empty is the
#: legacy/untyped github/slack shape; "http" is the general typed connection. Any
#: other value is refused at creation AND fails closed at dispatch (FIX 1).
_KNOWN_CONNECTION_TYPES = frozenset({"", "http"})

#: The capability-URL scheme: the credential is a PATH SEGMENT, not a header.
#: A Slack/Discord/Zapier incoming webhook and this platform's own
#: ``/mcp/hooks/<token>`` all carry their secret in the URL, which is the most
#: common way an agent posts a message anywhere -- and the one place every other
#: scheme had nothing to say. Live 2026-09-30 a universe handed a webhook link
#: asked for a bearer token that does not exist, then hardcoded the secret into
#: ``path_template`` (stored in the clear, projected to the owner's grant) and
#: sent the pasted value as a useless ``Authorization`` header.
_URL_SECRET_SCHEME = "url_secret"

#: Auth schemes an ``http`` connection may declare. ``oauth1a`` (Twitter) signs
#: with the four OAuth secrets carried in the bundle; the rest use one token.
#: ``oauth2`` sends a Bearer access token the broker keeps current from the
#: connection's refreshable token bundle (``connection_oauth.tokens``).
#: ``url_secret`` sends NO auth header at all -- see ``_substitute_url_secret``.
_SUPPORTED_HTTP_AUTH_SCHEMES = frozenset(
    {"none", "bearer", "basic", "header", "oauth1a", "oauth2", _URL_SECRET_SCHEME}
)

#: The reserved path placeholder a capability URL's secret fills. ``{secret}`` is
#: exactly one segment (``/mcp/hooks/{secret}``, Discord's token); ``{secret+}``
#: is the final tail of one or more (Slack's secret is ``T…/B…/token``, three
#: segments). RESERVED: the name may not be used as an ordinary ``{param}``, and
#: its value pattern is the platform's, never the caller's (see
#: ``_validate_param_patterns``).
_URL_SECRET_PLACEHOLDER_NAME = "secret"
_URL_SECRET_TOKEN = "{secret}"
_URL_SECRET_REST_TOKEN = "{secret+}"

#: The grammar a stored capability secret must satisfy. This is
#: ``_SSRF_ENDPOINT_LITERAL_RE``'s character set MINUS ``%`` (a capability
#: secret is never percent-encoded -- encoding one would trip the existing
#: double-encoding refusal) and minus the empty match. So ``/``, ``?``, ``#``,
#: ``\``, control bytes and dot-segments are unrepresentable, which is what
#: makes byte-verbatim substitution into the path safe. Minimum 8 because a
#: capability secret shorter than that is not one; 512 is
#: ``_SSRF_MAX_MATCH_SEGMENT``.
_URL_SECRET_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@-]{8,512}$")
#: The multi-segment form: up to 8 segments, each individually safe, and the
#: joined tail bounded the way ``_path_matches_template`` bounds a rest tail.
_URL_SECRET_MAX_TAIL_SEGMENTS = 8
_URL_SECRET_MAX_TAIL_CHARS = 1024

#: The ONLY credential_ref scheme an ``http`` connection may reference. Binding
#: the credential's scheme to the connection type is what stops a confused-deputy
#: exfil (an http connection referencing a `workos-pipes://github/...` token and
#: POSTing it to an attacker's endpoint) — Codex FIX 1.
_HTTP_CREDENTIAL_REF_PREFIX = "vault://http/"


def _validate_connection_credential_scheme(
    connection_type: str, credential_ref: str
) -> None:
    """Enforce the type<->credential-scheme biconditional (creation AND dispatch).

    An ``http`` connection may reference ONLY a ``vault://http/`` credential, and
    a non-http (legacy) connection may NEVER reference one. Applying the SAME rule
    at DISPATCH to the freshly re-read row — not only at creation — closes the
    connection-row mutation TOCTOU: a proxy started for one type whose row is
    later mutated to a different type/scheme (e.g. legacy-github -> http with an
    attacker allowlist) is refused before any credential is resolved, so no
    foreign-scheme token can be vended to the current-type driver (Codex FIX 1,
    TOCTOU).
    """
    ctype = (connection_type or "").strip().lower()
    is_http_ref = (credential_ref or "").strip().startswith(_HTTP_CREDENTIAL_REF_PREFIX)
    if ctype == "http" and not is_http_ref:
        raise SsrfValidationError(
            "an http connection credential_ref must be a vault://http/ reference"
        )
    if ctype != "http" and is_http_ref:
        raise SsrfValidationError(
            "a non-http connection must not use a vault://http/ credential_ref"
        )


def url_secret_token(endpoint: Any) -> str:
    """``{secret}`` / ``{secret+}`` if this endpoint carries the reserved
    placeholder, else ``""``.

    The reserved name is recognized in BOTH spellings from the one place, so the
    validator, the binding check and the substituter can never disagree about
    what a capability endpoint looks like.
    """
    template = str(getattr(endpoint, "path_template", "") or "")
    if _URL_SECRET_REST_TOKEN in template.split("/"):
        return _URL_SECRET_REST_TOKEN
    if _URL_SECRET_TOKEN in template.split("/"):
        return _URL_SECRET_TOKEN
    return ""


def validate_url_secret_value(value: str, token: str) -> str:
    """Return the stored capability secret, or raise. NEVER echoes the value.

    The grammar (``_URL_SECRET_SEGMENT_RE``) is what makes byte-verbatim
    substitution into a path safe: a value that satisfies it cannot carry a
    separator, a dot-segment, a percent-encoding or a control byte, so it can
    only ever occupy the segment(s) the template reserved for it. Checked at the
    deposit door AND again in the broker child immediately before substitution —
    a mutated or corrupted vault record must fail closed, not reach a socket.
    """
    text = value if isinstance(value, str) else ""
    if token == _URL_SECRET_REST_TOKEN:
        segments = text.split("/")
        if not 1 <= len(segments) <= _URL_SECRET_MAX_TAIL_SEGMENTS:
            raise SsrfValidationError(
                "a capability url secret may span 1-"
                f"{_URL_SECRET_MAX_TAIL_SEGMENTS} path segments"
            )
        if len(text) > _URL_SECRET_MAX_TAIL_CHARS:
            raise SsrfValidationError("capability url secret is too long")
    else:
        segments = [text]
    for segment in segments:
        if not _URL_SECRET_SEGMENT_RE.match(segment):
            raise SsrfValidationError(
                "a capability url secret is 8-512 characters of an opaque path "
                "segment (letters, digits and ._~-!$&'()*+,;=:@) — it may not "
                "contain a slash, a percent-encoding or whitespace"
            )
    return text


def validate_url_secret_binding(
    auth_scheme: str,
    endpoints: tuple[OutboundEndpoint, ...],
    *,
    access_mode: str | None = None,
) -> None:
    """Enforce ``url_secret`` <-> the reserved placeholder, BOTH directions.

    Both directions leak. A ``url_secret`` connection with no ``{secret}``
    anywhere has nowhere to put the vault segment, so it would put a
    credential-free request on the wire under a name that says otherwise. A
    ``{secret}`` template on a HEADER scheme is worse: the literal ``{secret}``
    token goes out in the path *and* the real credential goes out in an
    ``Authorization`` header the receiver never asked for.

    EVERY endpoint must carry exactly one reserved placeholder, not "at least
    one": a mixed connection has endpoints reachable without the secret, and the
    owner's grant sentence could not say which.

    Called at the deposit door (a user-facing refusal), at ``create_connection``
    (the storage boundary every issuer passes), and at dispatch against the row
    as RE-READ, which is what refuses a row mutated after a proxy opened.

    ``access_mode`` is optional ONLY so a caller with no mode in hand can still
    check the placeholder half; every call site in this repo passes it, and one
    that cannot must not be inventing ``exact`` on the row's behalf.
    """
    scheme = (auth_scheme or "").strip().lower()
    carriers = [endpoint for endpoint in endpoints if url_secret_token(endpoint)]
    if scheme == _URL_SECRET_SCHEME:
        if access_mode is not None and normalize_access_mode(access_mode) != ACCESS_EXACT:
            # `full` admits on the HOST alone, so no template is consulted and
            # there is no reserved position: the credential would go into every
            # path on the host. Creation and `set_access_mode` refuse the
            # combination; passing the mode here refuses a row that reached it
            # some other way, at DISPATCH, against the row as re-read (astra
            # round 1, FINDING 4).
            raise SsrfValidationError(
                f"a {_URL_SECRET_SCHEME} connection cannot be granted full access"
            )
        if not endpoints or len(carriers) != len(endpoints):
            raise SsrfValidationError(
                f"every endpoint of a {_URL_SECRET_SCHEME} connection must carry "
                f"the reserved {_URL_SECRET_TOKEN} (or {_URL_SECRET_REST_TOKEN}) "
                "path placeholder — that is where the vault secret goes"
            )
        # The reserved placeholder must be the ONLY placeholder on the endpoint.
        #
        # Every real capability URL is a FIXED path plus a secret -- Slack's
        # `/services/{secret+}`, Discord's `/api/webhooks/{secret+}`, Zapier's
        # `/hooks/catch/{secret+}`, this platform's `/mcp/hooks/{secret}`. So
        # this costs nothing real, and it buys two things:
        #
        # 1. It removes the shape astra's round-1 FINDING 3 attacked. There can
        #    be no `{tail+}` beside the secret for a reserved token to ride in,
        #    so the positioned substituter and the stray-token invariant are
        #    now defence in depth rather than the only line.
        # 2. It makes the pasted-link parser CORRECT rather than approximately
        #    correct. `extract_url_secret` splits the template on the reserved
        #    token and matches the pasted path's prefix as a literal string --
        #    which silently failed on `/hooks/{room}/{secret}`: the ask
        #    validated, the owner pasted the RIGHT link, and the deposit refused
        #    it. Telling an owner their correct answer is wrong is the failure
        #    this whole change exists to stop.
        stray = [
            name
            for endpoint in endpoints
            for name in (
                _placeholder_names(endpoint.path_template)
                + [_rest_placeholder_name(endpoint.path_template) or ""]
            )
            if name and name != _URL_SECRET_PLACEHOLDER_NAME
        ]
        if stray:
            raise SsrfValidationError(
                f"a {_URL_SECRET_SCHEME} endpoint is a fixed path plus the "
                f"secret, so {_URL_SECRET_TOKEN} must be its only placeholder "
                "(got " + ", ".join(sorted(set(stray))) + "); use "
                f"{_URL_SECRET_REST_TOKEN} if the secret itself spans several "
                "segments, or deposit one connection per fixed path"
            )
        if any(endpoint.redirect_mode != "none" for endpoint in endpoints):
            # Refused LOUDLY rather than left to fail silently. The redirect
            # chain re-matches the allowlist, which cannot match a substituted
            # path, so a redirect endpoint here would quietly behave as
            # no-follow. It should also not be wanted: a 3xx off a capability
            # URL hands the secret path to the next origin via Location/Referer.
            raise SsrfValidationError(
                f"a {_URL_SECRET_SCHEME} endpoint may not follow redirects"
            )
        return
    if carriers:
        raise SsrfValidationError(
            f"the reserved {_URL_SECRET_TOKEN} path placeholder needs "
            f'"auth_scheme": "{_URL_SECRET_SCHEME}"; a header scheme would send '
            "the placeholder literally and the credential in a header"
        )


def _outbound_http_enabled() -> bool:
    return os.environ.get(_OUTBOUND_HTTP_FLAG, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _proxy_startup_timeout_seconds() -> float:
    """Seconds to wait for the spawned broker child's ready handshake.

    The child is a ``spawn`` process, so it pays a FULL cold re-import of the
    package chain before it can answer. The original 5s budget was measured
    against nothing, and a cold container under load can exceed it — which
    surfaced as a proxy that "failed to start" with no further detail. Tunable
    so a slow host can be corrected without a redeploy.
    """
    raw = os.environ.get(_PROXY_STARTUP_TIMEOUT_VAR, "").strip()
    if not raw:
        return _DEFAULT_PROXY_STARTUP_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        # An explicitly-set unusable value is a misconfiguration, and silently
        # swallowing it is what Hard Rule 8 forbids. It still must not take
        # egress down, so: say so loudly, then use the default (Codex FIX C).
        print(
            f"{_PROXY_STARTUP_TIMEOUT_VAR}={raw!r} is not a positive number; "
            f"using the {_DEFAULT_PROXY_STARTUP_TIMEOUT_S:g}s default",
            file=sys.stderr,
        )
        return _DEFAULT_PROXY_STARTUP_TIMEOUT_S
    # Cap the range. The startup blocks a run-executor thread, and the top-level
    # pool is small (4 workers), so an unbounded budget lets a handful of hung
    # startups stall all top-level graph progress for that long (Codex FIX C).
    if value > _MAX_PROXY_STARTUP_TIMEOUT_S:
        print(
            f"{_PROXY_STARTUP_TIMEOUT_VAR}={raw!r} exceeds the "
            f"{_MAX_PROXY_STARTUP_TIMEOUT_S:g}s cap; clamping",
            file=sys.stderr,
        )
        return _MAX_PROXY_STARTUP_TIMEOUT_S
    return value


def _describe_child_exit(exitcode: int | None) -> str:
    """Render a broker child's exit status for an operator-facing error."""
    if exitcode is None:
        return ""
    # A negative code is the signal that killed it — -9 is the OOM killer, which
    # is the difference between "misconfigured" and "the box is out of memory".
    if exitcode < 0:
        return f" (killed by signal {-exitcode})"
    return f" (exitcode {exitcode})"


def _verb_within_scopes(
    verb: object,
    scopes: Iterable[str],
    access_mode: str = ACCESS_EXACT,
) -> bool:
    """Whether an HTTP verb is one this connection may dispatch.

    Authorization here is a membership test against the ``scopes`` tuple, and
    that tuple now also holds git scopes (``git_read:owner/name``), which are a
    DIFFERENT KIND of authority: they say a credentialed git operation may run
    against one repository, not that an arbitrary HTTP request may be dispatched.
    Without this check a caller could pass ``verb="git_read:owner/name"``, match
    by membership, and reach the credentialed dispatcher through the HTTP path.

    ON A FULL CHANNEL the verb list is not the grant. Upgrading a GET-only
    connection moves the mode and touches no scope row -- nothing is stored as
    a wildcard, by design -- so testing membership left the owner's POST
    refused on a channel they had granted in full, and the follow-up ask came
    back ``already_held``: a stranded connection (Codex code review round 3,
    P0). A full channel carries every verb the transport will send, and
    nothing outside that set.
    """
    if not isinstance(verb, str) or not verb:
        return False
    if is_git_scope(verb):
        return False
    if normalize_access_mode(access_mode) == ACCESS_FULL:
        return verb in _SSRF_ALLOWED_METHODS
    return verb in scopes


def _run_proxy_worker(
    channel: Any,
    dispatch_factory: str,
    dispatch_config: dict[str, Any],
    grant_id: str,
    scopes: tuple[str, ...],
) -> None:
    """Run the trusted dispatcher in a separate spawned process."""
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.request_budget import RequestBudgetExceeded
    from tinyassets.storage.agent_request_usage import InferenceUsageRequired

    _sanitize_child_environment()
    try:
        dispatch = _load_dispatch_factory(dispatch_factory, dispatch_config)
    except Exception as exc:
        # Hard Rule 8. The startup path runs BEFORE any credential is resolved
        # (`_load_dispatch_factory` only builds the ledger/driver/audit objects),
        # so the failure carries no credential material. Only the exception CLASS
        # crosses the wire, which is enough to discriminate the real causes —
        # PermissionError (runtime_root mkdir), OperationalError (ledger open),
        # ImportError — which a fixed string never was.
        #
        # The traceback is OPERATOR-VISIBLE, NOT host-only: daemon stderr goes to
        # Docker's fluentd driver (deploy/compose.yml:24,:46) into Vector, which
        # forwards unredacted to Better Stack (deploy/vector-betterstack.yaml:10).
        # It reaches no MCP user, but it does reach a third-party log sink, and
        # exception messages here can carry absolute host paths. Do not widen this
        # to dump locals or the config dict (Codex FIX B).
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        _send_message(
            channel,
            {
                "op": "startup_failed",
                "message": "trusted proxy failed to start",
                "cause": type(exc).__name__,
            },
        )
        channel.close()
        return
    _send_message(channel, {"op": "ready"})
    try:
        while True:
            message = _receive_message(channel)
            if message == {"op": "close"}:
                return
            if not isinstance(message, dict) or message.get("op") != "request":
                _send_message(
                    channel,
                    {
                        "ok": False,
                        "error_type": "ProxyRequestError",
                        "message": "outbound proxy rejected an invalid request",
                    },
                )
                continue
            verb = message.get("verb")
            if not _verb_within_scopes(verb, scopes):
                _send_message(
                    channel,
                    {
                        "ok": False,
                        "error_type": "PermissionError",
                        "message": "verb is outside the granted connection scope",
                    },
                )
                continue
            try:
                accounting = ({"inference_usage": message["inference_usage"],
                               "operation_id": message.get("operation_id")}
                              if "inference_usage" in message else {})
                result = dispatch(grant_id, verb, message.get("request"), **accounting)
            except RequestBudgetExceeded as exc:
                _send_message(channel, {"ok": False, "error_type": "InferenceUsageStopped",
                                        "reason": exc.reason,
                                        "usage_id": exc.request_receipt.get("usage_id")})
            except InferenceUsageRequired:
                _send_message(channel, {"ok": False, "error_type": "InferenceUsageRequired"})
            except ProviderAuthorityHeldError:
                _send_message(channel, {"ok": False, "error_type": "ProviderAuthorityHeldError",
                                        "message": "inference usage authority refused"})
            except ConnectionAuthorizationError as exc:
                _send_message(
                    channel,
                    {
                        "ok": False,
                        "error_type": "ConnectionAuthorizationError",
                        "message": str(exc),
                        "failure": exc.failure,
                    },
                )
            except (
                AmbiguousProxyOutcome,
                GrantResolutionError,
                PermissionError,
                ProxyRequestError,
            ) as exc:
                _send_message(
                    channel,
                    {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "message": _adapter_safe_proxy_error(exc),
                    },
                )
            except Exception:
                _send_message(
                    channel,
                    {
                        "ok": False,
                        "error_type": "ProxyRequestError",
                        "message": "outbound request failed",
                    },
                )
            else:
                _send_message(channel, {"ok": True, "result": result})
    except (OSError, ProxyRequestError):
        return
    finally:
        channel.close()


class _ProxyChannel:
    """Adapter-side transport; contains no dispatcher or credential material."""

    __slots__ = ("_channel", "_closed", "_lock", "_process", "_oauth_service")

    def __init__(self, channel: Any, process: Any, oauth_service=None) -> None:
        self._channel = channel
        self._closed = False
        self._lock = threading.Lock()
        self._process = process
        self._oauth_service = oauth_service

    def request(self, verb: str, request: object, *, inference_usage=None) -> Any:
        with self._lock:
            if self._closed:
                raise ProxyRequestError("outbound proxy is closed")
            _send_message(
                self._channel,
                {"op": "request", "verb": verb, "request": request,
                 **({"inference_usage": inference_usage.document(),
                     "operation_id": inference_usage.operation_id}
                    if inference_usage is not None else {})},
            )
            response = _receive_message(self._channel)
        if not isinstance(response, dict):
            raise ProxyRequestError("outbound proxy returned an invalid response")
        if response.get("ok") is True:
            return response.get("result")
        message = str(response.get("message") or "outbound request failed")
        error_type = response.get("error_type")
        if error_type == "InferenceUsageStopped":
            from tinyassets.storage.agent_request_usage import InferenceUsageStopped

            raise InferenceUsageStopped(response.get("reason"), response.get("usage_id"))
        if error_type == "InferenceUsageRequired":
            from tinyassets.storage.agent_request_usage import InferenceUsageRequired

            raise InferenceUsageRequired()
        if error_type == "ProviderAuthorityHeldError":
            from tinyassets.exceptions import ProviderAuthorityHeldError

            raise ProviderAuthorityHeldError("inference usage authority refused")
        if error_type == "PermissionError":
            raise PermissionError(message)
        if error_type == "GrantResolutionError":
            raise GrantResolutionError(message)
        if error_type == "AmbiguousProxyOutcome":
            raise AmbiguousProxyOutcome(message)
        if error_type == "OutboundDeadlineExceeded":
            raise OutboundDeadlineExceeded(message)
        if error_type == "ConnectionAuthorizationError":
            failure = response.get("failure")
            detail = failure.get("provider_detail", "") if isinstance(failure, dict) else ""
            raise ConnectionAuthorizationError(str(detail))
        raise ProxyRequestError(message)

    def close(self) -> None:
        from tinyassets.connection_oauth.service import release_client

        release_client(self._oauth_service)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                _send_message(self._channel, {"op": "close"})
            except (OSError, ProxyRequestError):
                pass
            self._channel.close()
        self._process.join(timeout=1.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)


class _BrokerChannel:
    """``ScopedConnectionProxy``'s channel when the broker process serves it.

    Each ``request`` is one broker stream, collected (``BrokerClient``), with a
    fresh ``op_id``: the same document and typed errors as the worker's
    channel. Holds no credential.
    """

    __slots__ = ("_client", "_closed", "_connection_id", "_grant_id")

    def __init__(self, client: Any, *, grant_id: str, connection_id: str) -> None:
        self._client = client
        self._grant_id = grant_id
        self._connection_id = connection_id
        self._closed = False

    def request(self, verb: str, request: object, *, inference_usage=None) -> Any:
        from tinyassets.broker.ops import new_op_id

        if self._closed:
            raise ProxyRequestError("outbound proxy is closed")
        if not isinstance(request, dict):
            raise ProxyRequestError("outbound proxy rejected an invalid request")
        return self._client.request(grant_id=self._grant_id, connection_id=self._connection_id,
                                    verb=verb, request=request,
                                    op_id=(inference_usage.operation_id
                                           if inference_usage is not None else new_op_id()),
                                    **({"inference_usage": inference_usage.document()}
                                       if inference_usage is not None else {}))

    def close(self) -> None:
        self._closed = True


def _broker_channel(data_root: Path, *, principal: str, command_center: str, grant_id: str,
                    connection_id: str) -> _BrokerChannel | None:
    """The broker's channel when the broker is selected; ``None`` keeps the worker.

    Selected but not running is a loud refusal, never a silent fall back to the
    worker: a switch that quietly does nothing cannot be proven on.
    """
    from tinyassets.broker.supervisor import broker_selected, read_owner

    if not broker_selected():
        return None
    owner = read_owner(Path(data_root))
    if owner is None:
        raise ProxyRequestError("the credential broker is selected but not running")
    from tinyassets.broker.client import BrokerClient

    def fence() -> tuple[int, str]:
        current = read_owner(Path(data_root)) or owner
        return int(current["generation"]), str(current["token"])

    client = BrokerClient(Path(owner["socket"]), principal=principal,
                          command_center=command_center, fence=fence)
    return _BrokerChannel(client, grant_id=grant_id, connection_id=connection_id)


@dataclass(frozen=True, slots=True)
class ScopedConnectionProxy:
    """Credential-blind adapter surface bound to one exact grant."""

    grant_id: str
    provider: str
    destination: str
    scopes: tuple[str, ...]
    #: The mode read when the proxy opened. Defence in depth only -- the broker
    #: re-reads the row and is the authority -- so a stale `exact` here refuses
    #: a since-upgraded channel until the caller opens a new proxy, which is
    #: the safe direction to be wrong in.
    access_mode: str = ACCESS_EXACT
    _channel: _ProxyChannel = field(repr=False, compare=False, default=None)  # type: ignore[assignment]

    def request(self, verb: str, request: object, *, inference_usage=None) -> Any:
        if not _verb_within_scopes(verb, self.scopes, self.access_mode):
            raise PermissionError(
                f"verb {verb!r} is outside the granted connection scope"
            )
        return self._channel.request(
            verb, request, **({"inference_usage": inference_usage}
                             if inference_usage is not None else {}),
        )

    def close(self) -> None:
        self._channel.close()


def _status_of(response: Any) -> int | None:
    if isinstance(response, dict):
        return response.get("status")
    return getattr(response, "status", None)


class BrokerStream:
    """A streamed response as the broker hands it out (I14 decisions 3 and 5).

    The status, reason and headers were already scanned by the driver; they
    are scanned again here against the broker's own held values, which include
    the OAuth tokens the driver never saw. The body goes through a
    :class:`~tinyassets.broker.scan.StreamScanner` over the union of both sets,
    so :meth:`read` only ever returns bytes that cannot belong to a held value,
    and raises once one appears. Holds no credential itself.
    """

    __slots__ = ("_on_unsafe", "_scanner", "_upstream", "headers", "reason", "redirect_count",
                 "status", "_usage", "_finished")

    def __init__(self, upstream: UpstreamStream, held: tuple[str, ...],
                 on_unsafe: Callable[[], None] = lambda: None, usage=None) -> None:
        from tinyassets.broker.scan import StreamScanner, contains_sensitive

        values = tuple(dict.fromkeys((*held, *upstream.sensitive)))
        head = {"status": upstream.status, "reason": upstream.reason,
                "headers": upstream.headers}
        if contains_sensitive(head, values):
            upstream.close()
            raise ProxyRequestError("outbound request failed: unsafe destination response")
        self._upstream = upstream
        self._usage, self._finished = usage, False
        self._on_unsafe = on_unsafe
        self._scanner = StreamScanner(values)
        self.status = upstream.status
        self.reason = upstream.reason
        self.headers = dict(upstream.headers)
        self.redirect_count = upstream.redirect_count

    def read(self, max_bytes: int) -> bytes | None:
        """Scanned body bytes; ``b""`` while held back, ``None`` at the end."""
        from tinyassets.broker.scan import SensitiveValueInResponse

        try:
            piece = self._upstream.read(max_bytes)
            released = self._scanner.feed(piece) if piece else self._scanner.finish()
        except SensitiveValueInResponse:
            self.close()
            self._on_unsafe()
            raise ProxyRequestError(
                "outbound request failed: unsafe destination response") from None
        except BaseException:
            self.close()
            raise
        if not piece and not released:
            self._finished = True
            return None
        return released

    def close(self) -> None:
        try:
            self._upstream.close()
        finally:
            if not self._finished:
                _settle_usage_after_error(self._usage, "unknown")


class CredentialBlindBroker:
    """Trusted daemon-side dispatcher; adapter-facing errors are secret-free."""

    __slots__ = (
        "_audit", "_ledger", "_network_request", "_oauth_tokens", "_resolve_credential",
        "_resolve_inference_usage",
    )

    def __init__(
        self,
        ledger: ConnectionLedger,
        *,
        resolve_credential: Callable[[str], str],
        network_request: Callable[..., Any],
        audit: Callable[[dict[str, object]], None] | None = None,
        oauth_tokens: Any = None,
        resolve_inference_usage: Callable[..., Any] | None = None,
    ) -> None:
        self._resolve_inference_usage = resolve_inference_usage
        self._ledger = ledger
        self._resolve_credential = resolve_credential
        self._network_request = network_request
        self._audit = audit or (lambda _record: None)
        # Keeps an oauth2 connection's access token current (refresh before
        # expiry and once on 401, single-flight). None refuses oauth2 loudly.
        self._oauth_tokens = oauth_tokens

    def dispatch(self, grant_id: str, verb: str, request: object, *,
                 stream: bool = False, idle_s: float | None = None,
                 guard: Callable[[], Any] | None = None,
                 on_connect: Callable[[Any], None] | None = None,
                 checkpoint: Callable[[], None] | None = None,
                 deadline_at: float | None = None, inference_usage=None,
                 operation_id: str | None = None, body=None) -> Any:
        """One request on the grant. ``stream=True`` returns a :class:`BrokerStream`
        whose body is read as it arrives (I14); every check before the response
        is identical, and the body is scanned byte by byte instead of whole."""
        if verb.startswith(("git_read:", "git_write:")):
            from tinyassets.broker.git_http import dispatch

            if not stream:
                raise PermissionError(
                    f"verb {verb!r} is outside the granted connection scope; "
                    "git requires binary broker IPC"
                )
            return dispatch(self, grant_id, verb, request, body=body, guard=guard,
                            checkpoint=checkpoint, on_connect=on_connect,
                            deadline_at=deadline_at)
        resource = self._ledger._active_resource_for_grant(grant_id)
        if resource is None:
            raise GrantResolutionError("absent or revoked outbound connection grant")
        revalidate_authority = None
        if resource.connection_type == "http" and str(verb).upper() == "GET" and any(
            endpoint.redirect_mode == "public_https_get" for endpoint in resource.allowed_endpoints
        ):
            initial = self._ledger._active_resource_snapshot_for_grant(grant_id)
            if initial is None:
                raise GrantResolutionError("absent or revoked outbound connection grant")
            resource, authority_stamp = initial

            def revalidate_authority(deadline: float) -> None:
                current = self._ledger._active_resource_snapshot_for_grant(
                    grant_id, deadline=deadline,
                )
                if current is None or current[1] != authority_stamp:
                    raise GrantResolutionError("outbound connection authority changed")
        if not _verb_within_scopes(verb, resource.scopes, resource.access_mode):
            raise PermissionError(
                f"verb {verb!r} is outside the granted connection scope"
            )
        usage = None
        if self._resolve_inference_usage is not None:
            usage = self._resolve_inference_usage(
                resource, grant_id, verb, request, inference_usage, operation_id,
            )
        else:
            from tinyassets.storage.agent_request_usage import resolve_inference_usage

            grant = self._ledger.require_active_grant(grant_id)
            usage = resolve_inference_usage(
                self._ledger._db_path.parent, grant.owner_user_id, grant.universe_id,
                self._ledger, resource, grant_id, verb, request, inference_usage, operation_id,
            )
        if usage is not None:
            usage.check()
        try:
            # Re-validate the CURRENT row's type<->credential-scheme match (the row
            # was just re-read and may have been mutated after proxy start), then
            # resolve the credential selecting the resolver by the CURRENT type —
            # not the type frozen at proxy start. Together these refuse a mutated
            # row before any foreign-scheme token can be vended (Codex FIX 1 TOCTOU).
            _validate_connection_credential_scheme(
                resource.connection_type, resource.credential_ref
            )
            credential = self._resolve_credential(
                resource.credential_ref, resource.connection_type
            )
        except Exception:
            self._record_error(resource, grant_id, verb, "credential unavailable")
            raise ProxyRequestError(
                "outbound request failed: credential unavailable"
            ) from None
        if not credential:
            self._record_error(resource, grant_id, verb, "credential unavailable")
            raise ProxyRequestError("outbound request failed: credential unavailable")
        # The request may ASK for a longer budget; whether it gets one is read
        # from the connection's own capabilities, never from the request.
        requested_budget = requested_idle = None
        if isinstance(request, dict) and (
            _REPLY_BUDGET_FIELD in request or _REPLY_IDLE_FIELD in request
        ):
            request = dict(request)
            requested_budget = request.pop(_REPLY_BUDGET_FIELD, None)
            requested_idle = request.pop(_REPLY_IDLE_FIELD, None)
        reply_budget_s = self._inference_budget_s(resource, verb, requested_budget)
        reply_stream = self._inference_stream(reply_budget_s, requested_budget, requested_idle)
        if resource.connection_type == "http":
            try:
                headers = self._ledger.get_connection_capability(
                    resource.connection_id, "constant_headers"
                )
            except Exception:
                # Fail loudly: sending without a header the owner declared would
                # reach the service with a different contract than configured.
                self._record_error(resource, grant_id, verb, "constant headers unavailable")
                raise ProxyRequestError(
                    "outbound request failed: constant headers unavailable"
                ) from None
            request = merge_constant_headers(request, headers)
        oauth = (
            resource.connection_type == "http"
            and (resource.auth_scheme or "").strip().lower() == "oauth2"
        )
        # Every value to keep out of a response. For oauth2 that is the access
        # AND refresh token, never the JSON bundle string as a whole. For a
        # capability URL it is the joined credential AND each of its path
        # segments: the check matches substrings, so a destination echoing one
        # segment of a multi-segment secret would otherwise pass (astra round 1,
        # FINDING 2).
        secrets_held: tuple[str, ...] = (credential,)
        if (
            resource.connection_type == "http"
            and (resource.auth_scheme or "").strip().lower() == _URL_SECRET_SCHEME
        ):
            secrets_held = url_secret_sensitive_values(credential)
        wire_credential = credential

        def oauth_bundle(*, rejected: str = "") -> Any:
            if usage is not None:
                usage.check()
            if guard is not None:
                with guard():
                    if deadline_at is not None and time.monotonic() >= deadline_at:
                        raise OutboundDeadlineExceeded("outbound request exceeded its time budget")
                    return self._oauth_bundle(resource, grant_id, verb, credential,
                                              rejected=rejected)
            return self._oauth_bundle(resource, grant_id, verb, credential, rejected=rejected)

        if oauth:
            from tinyassets.connection_oauth.tokens import decode

            bundle = oauth_bundle()
            original = decode(credential)
            wire_credential = bundle.access_token
            secrets_held = tuple(dict.fromkeys(
                (*original.secret_values(), *bundle.secret_values())))
        streaming = {"stream": True, "idle_s": idle_s, "on_connect": on_connect,
                     "checkpoint": checkpoint} if stream else {}
        if checkpoint is not None and revalidate_authority is not None:
            # Each redirect hop re-checks the caller's cancellation and fence too.
            authority_check = revalidate_authority

            def revalidate_authority(deadline: float) -> None:
                authority_check(deadline)
                checkpoint()
        if deadline_at is not None:
            streaming["deadline_at"] = deadline_at
        response = self._send(resource, grant_id, verb, request, wire_credential,
                              revalidate_authority, reply_budget_s, reply_stream=reply_stream,
                              guard=guard, inference_usage=usage, **streaming)
        if oauth and _status_of(response) == 401:
            # The service rejected the token before doing anything: refresh
            # once (unless another holder already did) and send once more.
            # A stream's status is known before any body byte is read, so the
            # resend happens before anything reaches the caller.
            if usage is not None:
                usage.settle("failed")
            try:
                if usage is not None:
                    usage.reserve_retry()  # Before refresh, not just before the second send.
                bundle = oauth_bundle(rejected=wire_credential)
                if bundle.access_token != wire_credential:
                    wire_credential = bundle.access_token
                    secrets_held = tuple(dict.fromkeys((*secrets_held, *bundle.secret_values())))
                    if stream:
                        response.close()
                    response = self._send(resource, grant_id, verb, request, wire_credential,
                                          revalidate_authority, reply_budget_s,
                                          reply_stream=reply_stream, guard=guard,
                                          inference_usage=usage, **streaming)
                elif usage is not None:
                    usage.settle("not_sent")
            except BaseException:
                if stream:
                    response.close()
                _settle_usage_after_error(usage, "not_sent")
                raise
        if stream:
            def unsafe_body() -> None:
                self._record_error(resource, grant_id, verb,
                                   "destination response contained credential material")

            try:
                return BrokerStream(response, secrets_held, on_unsafe=unsafe_body, usage=usage)
            except ProxyRequestError:
                self._record_error(resource, grant_id, verb,
                                   "destination response contained credential material")
                raise
        joined = _streamed_text(response)
        if any(
            _contains_secret(response, secret) or (joined and secret in joined)
            for secret in secrets_held if secret
        ):
            self._record_error(
                resource,
                grant_id,
                verb,
                "destination response contained credential material",
            )
            raise ProxyRequestError(
                "outbound request failed: unsafe destination response"
            )
        return response

    def _oauth_bundle(
        self, resource: ConnectionResource, grant_id: str, verb: str, credential: str,
        *, rejected: str = "",
    ) -> Any:
        if self._oauth_tokens is None:
            self._record_error(resource, grant_id, verb, "oauth2 tokens unavailable")
            raise ProxyRequestError("outbound request failed: credential unavailable")
        destination = (resource.credential_ref or "")[len(_HTTP_CREDENTIAL_REF_PREFIX):].strip()
        try:
            return self._oauth_tokens.current(destination, credential, rejected=rejected)
        except ConnectionAuthorizationError:
            self._record_error(resource, grant_id, verb, "connection authorization failed")
            raise

    def _inference_budget_s(
        self, resource: ConnectionResource, verb: str, requested: object,
    ) -> float | None:
        """The longer reply budget, when THIS connection is a model source.

        ``None`` keeps the driver's ordinary 30s. Eligible only for a POST on an
        ``http`` connection that carries a model capability the owner declared;
        the request's number is only an upper bound within
        ``INFERENCE_MAX_SECONDS``, and a non-number, a bool or anything not above
        the ordinary budget changes nothing.
        """
        if (
            type(requested) not in (int, float)
            # An int is finite by construction; math.isfinite would overflow on
            # one too large for a float (Codex, 2026-09-29).
            or (type(requested) is float and not math.isfinite(requested))
            or requested <= _SSRF_MAX_TOTAL_SECONDS
            or resource.connection_type != "http"
            or str(verb).upper() != "POST"
        ):
            return None
        try:
            eligible = any(
                self._ledger.get_connection_capability(resource.connection_id, kind)
                is not None
                for kind in _INFERENCE_CAPABILITIES
            )
        except Exception:
            # An unreadable capability is not evidence of one: ordinary budget.
            return None
        # Clamp before converting: float() of an enormous int overflows.
        return float(min(requested, INFERENCE_MAX_SECONDS)) if eligible else None

    @staticmethod
    def _inference_stream(
        reply_budget_s: float | None, requested_budget: object, requested_idle: object,
    ) -> tuple[float, float] | None:
        """``(idle seconds, total seconds)`` for a streamed reply, or None.

        Only where the longer budget was already granted (a model source, a
        POST), so the request can never buy more than that connection allows.
        The total is the caller's own remaining turn, clamped to
        ``INFERENCE_STREAM_MAX_SECONDS``; the idle window is clamped to
        ``[INFERENCE_IDLE_MIN_SECONDS, INFERENCE_MAX_SECONDS]``.
        """
        if (
            reply_budget_s is None
            or type(requested_idle) not in (int, float)
            or (type(requested_idle) is float and not math.isfinite(requested_idle))
            or requested_idle <= 0
        ):
            return None
        idle = float(min(max(requested_idle, INFERENCE_IDLE_MIN_SECONDS), INFERENCE_MAX_SECONDS))
        total = float(min(requested_budget, INFERENCE_STREAM_MAX_SECONDS))
        return idle, max(total, reply_budget_s)

    def _send(
        self, resource: ConnectionResource, grant_id: str, verb: str, request: object,
        credential: str, revalidate_authority: Any, reply_budget_s: float | None = None,
        reply_stream: tuple[float, float] | None = None,
        guard: Callable[[], Any] | None = None, deadline_at: float | None = None,
        inference_usage=None, **streaming: Any,
    ) -> Any:
        """One network send. ``guard`` (the broker's fence and cancellation check)
        is held across it, so every send -- the first, an OAuth resend -- is
        re-checked immediately before it leaves. ``deadline_at`` (monotonic) is
        the stream's one absolute deadline: every send gets only what is left
        of it, so a resend never starts a fresh budget."""
        if deadline_at is not None:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise OutboundDeadlineExceeded("outbound request exceeded its time budget")
            # Never wider than what was granted: no extended budget means the
            # ordinary one, now cut to what is left of the stream.
            granted = _SSRF_MAX_TOTAL_SECONDS if reply_budget_s is None else reply_budget_s
            reply_budget_s = min(granted, remaining)
        marked = False
        if inference_usage is not None:
            from tinyassets.request_budget import RequestBudgetExceeded

            prior_connect, prior_check = streaming.get("on_connect"), streaming.get("checkpoint")

            def connected(sock):
                nonlocal marked
                if prior_connect is not None:
                    prior_connect(sock)
                try:
                    inference_usage.dispatched()
                except RequestBudgetExceeded as exc:
                    raise _InferenceAccountingStop(exc) from None
                marked = True

            def checkpoint():
                if prior_check is not None:
                    prior_check()
                # Closure fences a request still in DNS/connect, while already
                # dispatched replies retain their existing cancellation policy.
                if not marked:
                    try:
                        inference_usage.check()
                    except RequestBudgetExceeded as exc:
                        raise _InferenceAccountingStop(exc) from None

            streaming.update(on_connect=connected, checkpoint=checkpoint)
        with guard() if guard is not None else contextlib.nullcontext():
            if inference_usage is not None:
                inference_usage.check()
            try:
                response = self._send_unguarded(resource, grant_id, verb, request, credential,
                                                revalidate_authority, reply_budget_s,
                                                reply_stream=reply_stream, **streaming)
                if inference_usage is not None and not marked:
                    from tinyassets.exceptions import ProviderAuthorityHeldError

                    raise ProviderAuthorityHeldError(
                        "inference transport omitted dispatch evidence")
                return response
            except BaseException as exc:
                _settle_usage_after_error(inference_usage, "unknown" if marked else "not_sent")
                if isinstance(exc, _InferenceAccountingStop):
                    raise exc.cause from None
                raise

    def _send_unguarded(
        self, resource: ConnectionResource, grant_id: str, verb: str, request: object,
        credential: str, revalidate_authority: Any, reply_budget_s: float | None = None,
        reply_stream: tuple[float, float] | None = None,
        **streaming: Any,
    ) -> Any:
        try:
            return self._network_request(
                credential=credential,
                provider=resource.provider,
                destination=resource.destination,
                connection_type=resource.connection_type,
                auth_scheme=resource.auth_scheme,
                allowed_endpoints=resource.allowed_endpoints,
                access_mode=resource.access_mode,
                verb=verb,
                request=request,
                **({"revalidate_authority": revalidate_authority} if revalidate_authority else {}),
                **({"reply_budget_s": reply_budget_s} if reply_budget_s is not None else {}),
                **streaming,
                **({"reply_stream": reply_stream} if reply_stream is not None else {}),
            )
        except BrokerStreamStop:
            raise
        except AmbiguousProxyOutcome:
            self._record_error(
                resource,
                grant_id,
                verb,
                "destination outcome ambiguous",
            )
            raise AmbiguousProxyOutcome("destination outcome ambiguous") from None
        except OutboundDeadlineExceeded:
            # Typed and fixed-text, so the caller can tell a slow answer from a
            # failed one; nothing of the destination's crosses with it.
            self._record_error(resource, grant_id, verb, "destination exceeded time budget")
            raise OutboundDeadlineExceeded(
                "outbound request exceeded its time budget"
            ) from None
        except Exception:
            self._record_error(
                resource,
                grant_id,
                verb,
                "destination request failed",
            )
            raise ProxyRequestError(
                "outbound request failed at destination"
            ) from None

    def _record_error(
        self,
        resource: ConnectionResource,
        grant_id: str,
        verb: str,
        reason: str,
    ) -> None:
        self._audit(
            {
                "event": "outbound_proxy_error",
                "grant_id": grant_id,
                "provider": resource.provider,
                "destination": resource.destination,
                "verb": verb,
                "reason": reason,
            }
        )


class _TestFixtureCredentialResolver:
    __slots__ = ("_allow_test_fixtures",)

    def __init__(self, *, allow_test_fixtures: bool) -> None:
        self._allow_test_fixtures = allow_test_fixtures

    def __call__(self, credential_ref: str) -> str:
        if not self._allow_test_fixtures:
            raise RuntimeError("credential reference has no trusted resolver")
        if credential_ref == "test-fixture://nonsecret":
            return "trusted-child-fixture"
        for prefix, fail in (
            ("test-vault-file:", False),
            ("test-vault-error:", True),
        ):
            if not credential_ref.startswith(prefix):
                continue
            path = Path(credential_ref.removeprefix(prefix))
            if not path.is_absolute() or not path.is_file():
                raise RuntimeError("trusted credential reference is unavailable")
            credential = path.read_text(encoding="utf-8")
            if fail:
                raise RuntimeError(
                    f"vault failed while loading {credential}"
                )
            return credential
        raise RuntimeError("credential reference has no trusted resolver")


class _GeneralVaultCredentialResolver:
    """Resolve a general (non-github) connection credential from the vault.

    NEVER parses the destination as a github repo (Codex FIX 4): the github
    resolver's ``__init__`` runs ``_github_repository_from_destination`` on the
    destination, which raises for a normal http host like ``api.example.com`` and
    crashed the broker at startup. An ``http`` connection's ``credential_ref``
    (``vault://http/<key>``) names a per-universe vault record; its secret is
    returned, or fail closed. A general typed-bundle resolver in
    ``credential_vault.py`` is task 1.8 (deferred); this reads the single value.
    """

    __slots__ = ("_universe_dir",)

    def __init__(self, *, universe_dir: str | Path) -> None:
        self._universe_dir = Path(universe_dir)

    def __call__(self, credential_ref: str) -> str:
        ref = (credential_ref or "").strip()
        prefix = "vault://http/"
        if not ref.startswith(prefix):
            raise RuntimeError("credential reference has no trusted resolver")
        record_key = ref[len(prefix):].strip()
        if not record_key:
            raise RuntimeError("credential reference is unavailable")
        from tinyassets.credential_vault import load_credential_vault

        for record in load_credential_vault(self._universe_dir):
            if str(record.get("credential_type") or "").strip().lower() != "http":
                continue
            if str(record.get("destination") or "").strip() != record_key:
                continue
            for key in ("token", "access_token", "secret", "api_key"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            raise RuntimeError("credential reference is unavailable")
        raise RuntimeError("credential reference is unavailable")


class _TrustedCredentialResolver:
    """Select the correct credential resolver INSIDE the broker child.

    Constructing this must never crash (Codex FIX 4): the github resolver — whose
    ``__init__`` parses the destination as a repo — is built LAZILY, only on the
    explicit github path, so an ``http`` connection never touches it. An http
    connection resolves through the GENERAL vault path, never the repo parser.
    """

    __slots__ = (
        "_allow_test_fixtures",
        "_connection_type",
        "_destination",
        "_owner_user_id",
        "_provider",
        "_universe_dir",
    )

    def __init__(self, config: dict[str, Any]) -> None:
        self._provider = str(config["provider"])
        self._connection_type = str(config.get("connection_type", "") or "").strip().lower()
        self._allow_test_fixtures = bool(config["allow_test_fixtures"])
        self._universe_dir = config["universe_dir"]
        self._destination = str(config["destination"])
        self._owner_user_id = str(config["owner_user_id"])

    def __call__(self, credential_ref: str, connection_type: str | None = None) -> str:
        ref = credential_ref or ""
        # Select by the CURRENT connection_type the caller supplies (the broker
        # passes the freshly re-read row's type), falling back to the type frozen
        # at proxy start only when none is given — so a post-start row mutation
        # cannot force resolution through the wrong (stale) resolver (Codex FIX 1
        # TOCTOU).
        effective_type = (
            connection_type if connection_type is not None else self._connection_type
        )
        effective_type = (effective_type or "").strip().lower()
        # CONNECTION-TYPE-FIRST (Codex FIX 1 — confused-deputy exfiltration). An
        # http connection resolves its credential ONLY through the general vault
        # resolver — NEVER a scheme-specific (github/workos/slack) or fixture
        # resolver. This is what stops a forged/mismatched credential_ref (e.g.
        # `workos-pipes://github/victim` on an http connection) from vending a
        # FOREIGN token that the http driver would then POST to an attacker's
        # allowlisted endpoint. The general resolver accepts only vault://http/
        # refs and fails closed on anything else.
        # CONNECTION-TYPE-FIRST (Codex FIX 1 — confused-deputy exfiltration). An
        # http connection resolves its credential ONLY through the general vault
        # resolver (vault://http/<key>), which fails closed on anything else. This
        # is the single channel-agnostic egress credential path; there is no
        # scheme-specific (github/slack/workos) resolver — channels are user-built
        # nodes over the generic http connection, not platform code.
        if effective_type == "http":
            return _GeneralVaultCredentialResolver(
                universe_dir=self._universe_dir
            )(credential_ref)
        # Test-fixture refs resolve through the fixture resolver — a REAL gated
        # component, not a mock — for the test paths only.
        if (
            self._provider.startswith("test-fixture.")
            or ref == "test-fixture://nonsecret"
            or ref.startswith(("test-vault-file:", "test-vault-error:"))
        ):
            return _TestFixtureCredentialResolver(
                allow_test_fixtures=self._allow_test_fixtures
            )(credential_ref)
        raise RuntimeError("credential reference has no trusted resolver")


class _TestFixtureNetworkDriver:
    __slots__ = ("_allow_test_fixtures", "_path")

    def __init__(
        self,
        runtime_root: Path,
        *,
        allow_test_fixtures: bool,
    ) -> None:
        self._path = runtime_root / "network.jsonl"
        self._allow_test_fixtures = allow_test_fixtures

    def __call__(
        self,
        *,
        credential: str,
        provider: str,
        destination: str,
        verb: str,
        request: object,
    ) -> dict[str, object]:
        del credential
        outcomes = {
            "test-fixture.created": "created",
            "test-fixture.issue": "issue",
            "test-fixture.fail-once": "fail_once",
            "test-fixture.ambiguous": "ambiguous",
            "test-fixture.explode": "explode",
        }
        outcome = (
            outcomes.get(provider)
            if self._allow_test_fixtures
            else None
        )
        if outcome is None:
            raise ProxyRequestError(
                "provider has no trusted outbound transport"
            )
        prior = self._path.exists()
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "provider": provider,
                        "destination": destination,
                        "verb": verb,
                        "request": request,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        if outcome == "explode":
            raise RuntimeError("destination request failed")
        if outcome == "fail_once" and not prior:
            raise RuntimeError("destination rejected once")
        if outcome == "ambiguous":
            raise AmbiguousProxyOutcome("connection dropped after send")
        if outcome == "created":
            return {"status": "created"}
        return {"issue_id": 17}


# ---------------------------------------------------------------------------
# General SSRF-hardened outbound HTTP driver
# (channel-agnostic-outbound, design.md D3/D4/D5 — SLICE 1, DARK)
#
# This is the credential-blind general ``_network_request`` driver. It lives
# inside the broker module so a later slice can select it by ``connection_type``
# from ``_TrustedNetworkDriver``. It is DARK in this slice: the dispatch in
# ``_TrustedNetworkDriver`` is UNCHANGED, no channel is migrated, and no default
# flag is flipped. Only the mechanism + its adversarial tests exist. Nothing
# routes through it yet.
# ---------------------------------------------------------------------------

#: Only https is ever dialed; http and every other scheme is refused.
_SSRF_ALLOWED_SCHEME = "https"
#: Default egress ports; a per-connection endpoint allowlist (a later slice)
#: narrows further. Empty-allowlist-permits-nothing lives at that layer.
_SSRF_DEFAULT_PORTS = frozenset({443})
#: Response bounds. Body size is capped DURING the read (streamed read of
#: cap+1 bytes). Header count/bytes are a POST-parse reject: http.client has
#: already buffered the headers when we check, bounded transiently by the
#: stdlib's own _MAXHEADERS=100 / _MAXLINE=65536 ceiling (~6.4 MiB), NOT a
#: during-read cap.
_SSRF_MAX_BODY_BYTES = 5 * 1024 * 1024
_SSRF_MAX_HEADER_COUNT = 100
_SSRF_MAX_HEADER_BYTES = 64 * 1024
_SSRF_TIMEOUT_SECONDS = 30.0
#: Total wall-clock budget across connect + send + body read, enforced with a
#: monotonic deadline so a slow-drip server cannot reset the per-operation
#: socket timeout indefinitely (slowloris + endless chunked trailers;
#: Codex-found). The body is read in bounded chunks, checking the deadline each
#: iteration and tightening the socket timeout to the remaining budget.
_SSRF_MAX_TOTAL_SECONDS = 30.0
#: The ONE longer budget: the most a model-inference request may wait for its
#: answer. A non-streaming model sends nothing until it has finished, so a turn
#: that writes a whole app needs minutes, and the 30s above ended one mid-write
#: (live 2026-09-29). Granted only to a POST on a connection that itself carries
#: a model capability (``_inference_budget_s``), never because a request asks,
#: and it replaces BOTH the per-operation and the total timeout for that one
#: request. Every other bound -- pinning, allowlist, redirects, body and header
#: caps, the slow-drip deadline itself -- is unchanged. Host-wide concurrency of
#: such requests is bounded by provider admission
#: (``TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS``). Equal to the served turn's own
#: absolute cap (``providers.base.DEFAULT_ABSOLUTE_CAP_S``).
INFERENCE_MAX_SECONDS = 600.0
#: Capabilities that make a connection a model source, and so eligible.
_INFERENCE_CAPABILITIES = ("model_use", "model_discovery")
#: The request field an inference caller uses to ask for that budget. Removed
#: by the broker before the request reaches the network driver.
_REPLY_BUDGET_FIELD = "reply_budget_s"
#: A STREAMED model reply is judged by whether it is still arriving, not by how
#: long it takes (founder, 2026-10-02: "if the model response is just slow
#: your skipping it and then that call is used up for the user" -- on a capped
#: free tier every abandoned reply costs one of the day's requests). The caller
#: asks with this field; once the response headers are in, each read may wait
#: this long for the next bytes, and a reply that keeps arriving runs on. The
#: header phase keeps ``INFERENCE_MAX_SECONDS``, so a source that ignores
#: ``stream`` and answers all at once behaves exactly as before.
_REPLY_IDLE_FIELD = "reply_idle_s"
#: Bounds on that inactivity window: never shorter than a source's usual
#: keep-alive gap, never longer than the old whole-reply ceiling.
INFERENCE_IDLE_MIN_SECONDS = 30.0
#: The outer bound on a streamed reply that keeps arriving: a slow drip must
#: still end some day (the deadline socket's reason to exist). Six hours, the
#: same horizon after which a working turn's row reads as stale.
INFERENCE_STREAM_MAX_SECONDS = 6 * 3600.0
#: Body cap for a streamed reply: event framing multiplies a reply's size, and
#: this still fits ``_MAX_PROXY_FRAME_BYTES`` when JSON escaping doubles it.
INFERENCE_STREAM_MAX_BODY_BYTES = 7 * 1024 * 1024
_SSRF_READ_CHUNK = 65536
# RESIDUALS owed before this driver is ACTIVATED (it is dark; activation is
# gated behind the endpoint-allowlist slice):
#   - The monotonic wall-clock deadline (_SSRF_MAX_TOTAL_SECONDS) is enforced at
#     the SOCKET layer (_DeadlineSocket), so it uniformly covers the status line,
#     headers, body, and chunked trailers — every phase http.client reads through
#     recv_into — not just the body loop. A slow-drip in ANY phase aborts at the
#     deadline. Header/body byte counts are still capped separately.
#   - The response substring scrub (_declassify_response) is BEST-EFFORT only: a
#     destination that transforms, splits, or re-encodes the secret before
#     echoing it evades a substring scan. The real confidentiality boundary is
#     the per-connection endpoint allowlist PLUS fixed destination-specific
#     response projections (next slice) — once a connection is bound, arbitrary
#     destination headers/body must not cross the child boundary; only the typed,
#     projected fields may. Do not treat the scrub as that boundary.
#   - Network-/org-specific NAT64 prefixes are NOT caught by prefix
#     classification: RFC 6052 permits a deployment to carve a NAT64 prefix from
#     ordinary global-unicast space (e.g. 2001:db8:1::/96 -> 10.0.0.1), which
#     reads as global here. Activation needs deployment-aware prefix rejection or
#     an egress firewall denying translated private destinations.
#   - DNS resolution is NOT inside the total deadline: getaddrinfo runs before the
#     deadline is created and is a blocking OS call a deadline cannot interrupt
#     mid-flight. TCP connect + TLS handshake ARE now deadline-bounded (see
#     _PinnedHTTPSConnection.connect), and all post-handshake parsing is bounded by
#     _DeadlineSocket, so a slow-but-returning resolver only lets DNS time escape
#     the budget; a truly hanging resolver is bounded only by the OS. Fully bounding
#     DNS needs a threaded resolver with its own timeout — an activation-slice item.
# Decompression bound: the driver NEVER decompresses. It sends no
# Accept-Encoding and does not gunzip, so a `Content-Encoding: gzip` body is
# returned as raw bytes capped by _SSRF_MAX_BODY_BYTES — a zip bomb can never
# expand. Redirect bound: redirects are disabled structurally (no
# HTTPRedirectHandler is installed on the opener), so ZERO hops are ever
# followed; a 3xx is returned as-is. If a connection ever opts into redirects,
# the full scheme/host/DNS/peer check must re-run per hop with no cross-origin
# credential forwarding — deliberately not implemented while it is off.
#: The client string every outbound call carries when nothing declared one.
#:
#: HONEST on purpose: it names this platform and links to it, and it
#: impersonates no browser. A CDN in front of a destination is entitled to know
#: who is calling.
#:
#: Sending one at all is the fix for an UNIDENTIFIED client, not for one
#: specific block. On 2026-09-30 a UA-less POST to this platform's own
#: `/mcp/hooks` was answered `error code: 1010` (403) by Cloudflare while the
#: same request carrying a User-Agent reached the application -- but that A/B
#: did NOT reproduce hours later, from either the dev host or the production
#: egress IP, with or without the header. So an edge block here is a bot score,
#: not a function of this header. Identifying ourselves removes one of its
#: inputs and is what a well-behaved client does; it is not a guarantee.
#:
#: The version is read from the package rather than repeated here, so a release
#: cannot ship a client string that lies about which build is calling.
#: ``tinyassets/__init__.py`` is deliberately side-effect free (its public API
#: is lazy), so importing it here costs nothing and cannot cycle.
OUTBOUND_USER_AGENT = f"TinyAssets/{_tinyassets_version} (+https://tinyassets.io)"

#: The one header a NODE may not set per call, over and above the security
#: denylist below: see `OUTBOUND_USER_AGENT` and the effector's
#: `_declared_user_agent_error`. An owner declares it on the connection.
OUTBOUND_USER_AGENT_HEADER = "user-agent"

#: Headers a caller may never set: auth is applied inside the child from the
#: typed bundle (D5); Host/proxy routing is owned by the transport, not the
#: packet. Any ``proxy-*`` header is also refused (prefix check below).
_SSRF_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "proxy-authorization",
        "proxy-connection",
    }
)
#: Framing / hop-by-hop headers a caller may never set. Letting a caller supply
#: its own Content-Length or Transfer-Encoding is request smuggling: the body
#: can carry a second pipelined request that the destination executes. The
#: transport computes framing itself.
_SSRF_FORBIDDEN_FRAMING_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "expect",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
#: STANDARDIZED special-use IPv6 ranges ``ipaddress.is_global`` does not
#: consistently reject across CPython versions, checked on the ORIGINAL address
#: before transition unwrapping. This blocks only the fixed IANA/RFC prefixes.
#: It does NOT close org-specific NAT64 prefixes carved from ordinary
#: global-unicast space (RFC 6052) — e.g. a deployment's own
#: ``2001:db8:.../96`` NAT64 that maps to an internal v4. That is a
#: deployment-aware egress-policy concern for the endpoint-allowlist slice, not
#: something structurally decidable here (Codex-noted).
_SSRF_EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known prefix
    ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use prefix
    ipaddress.ip_network("::/96"),  # deprecated IPv4-compatible IPv6
)
#: A strict DNS hostname with a real alphabetic TLD. Rejects ``localhost``,
#: all-numeric hosts, ``0x..``/octal spellings, and other unusual IP-literal
#: forms that ``ipaddress`` will not parse as an address.
_SSRF_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
#: Control chars / whitespace / backslash anywhere in the raw URL is refused.
_SSRF_FORBIDDEN_URL_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
#: Control chars (but NOT space) in a header name/value — CR/LF injection guard.
_SSRF_FORBIDDEN_HEADER_CHARS = re.compile(r"[\x00-\x1f\x7f]")
#: HTTP methods a connection may declare and a caller may invoke.
_SSRF_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
#: An allowlist path-template placeholder segment (``{name}``). It matches EXACTLY
#: one non-empty, non-traversal path segment when the concrete URL is checked.
_SSRF_ENDPOINT_PLACEHOLDER_RE = re.compile(r"^\{[a-z0-9_]+\}$")
#: A literal path-template segment: ordinary URL-path characters only. Encoded
#: dot/slash and dot-segments are rejected separately so a template cannot smuggle
#: traversal, and the concrete URL is already dot-segment-free (canonical parse).
_SSRF_ENDPOINT_LITERAL_RE = re.compile(r"^[A-Za-z0-9._~%!$&'()*+,;=:@-]*$")
#: DNS resolution runs in a worker thread bounded by this timeout so a hanging
#: ``getaddrinfo`` (a blocking OS call the request deadline cannot interrupt
#: mid-flight) is abandoned rather than escaping the budget (residual #3). Kept
#: at/under the per-op + total request budget.
_SSRF_DNS_TIMEOUT_SECONDS = 5.0
#: One percent-encoded octet. Used to reject any encoding of a path separator or
#: dot-segment that an origin would decode AFTER our allowlist match — `%2e` (.),
#: `%2f` (/), `%5c` (\), plus control (<0x20, except the legal `%20` space) and
#: high/overlong bytes (>=0x7f — overlong-UTF-8 forms of separators always use
#: high lead/continuation bytes). `%25` (double-encoding) is rejected separately.
_SSRF_PERCENT_ENC_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_SSRF_UNSAFE_ENCODED_BYTES = frozenset({0x2E, 0x2F, 0x5C})
#: A permitted query-parameter NAME in an endpoint allowlist declaration.
_SSRF_QUERY_NAME_RE = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,64}$")
#: A concrete path segment / query value longer than this is refused before the
#: declared regex runs — bounds catastrophic-backtracking exposure on a
#: user-declared pattern (per-universe self-inflicted at worst, but capped).
_SSRF_MAX_MATCH_SEGMENT = 512
#: A declared param/query value pattern longer than this is rejected at authoring.
_SSRF_MAX_PATTERN_LEN = 256
#: A multi-segment ("rest") placeholder — ``{name+}`` — permitted ONLY as the
#: FINAL template segment and at most once. It captures one OR MORE concrete path
#: segments (github contents sub-paths, slash-bearing branch refs) as a single
#: value. The captured tail is split on literal ``/``; each segment is rejected if
#: empty / ``.`` / ``..`` / over-long, the whole tail is bounded (segments + total
#: length), and the ``/``-joined tail must full-match the endpoint's declared
#: pattern for that rest-param. Encoded separators (%2e/%2f/%5c), ``%25``,
#: controls, and overlong bytes are ALREADY rejected on the concrete URL by
#: ``_parse_canonical_https_url`` -> ``_reject_unsafe_encoded_path`` before any
#: match, and the canonical path is already literal-dot-segment-free — so a
#: rest-tail cannot smuggle a traversal an origin would decode later. The declared
#: rest-pattern is the endpoint-specific tightening (repo-relative path for
#: contents; git ref-name shape for branches) on top of those invariants.
_SSRF_ENDPOINT_REST_PLACEHOLDER_RE = re.compile(r"^\{[a-z0-9_]+\+\}$")
#: A concrete rest-tail may span at most this many segments / this many chars.
_SSRF_MAX_REST_SEGMENTS = 40
_SSRF_MAX_REST_TAIL_LEN = 1024
#: Bounds on the raw query string parsed at allowlist time. Without these a
#: duplicate-field flood (``?ref=a&ref=a&...``) forces ``parse_qsl`` to build a
#: huge list before the exactly-once/undeclared checks can reject it — a cheap
#: memory/CPU amplifier on the egress path (Codex). ``max_num_fields`` makes
#: ``parse_qsl`` itself fail closed past the bound.
_SSRF_MAX_QUERY_LEN = 4096
_SSRF_MAX_QUERY_FIELDS = 32


class ConnectionSecretBundle:
    """A typed, per-connection-type set of named secret values (design.md D2).

    Constructed only inside the broker child from resolved credential material;
    it never crosses the process boundary and never appears in a packet. It
    exists so the driver can (a) build the auth header from the correct named
    value and (b) declassify a response against *every* secret it holds, not a
    single string (Slack keeps a bot token and a separate app token; Twitter
    carries four OAuth values).
    """

    __slots__ = ("_values",)

    def __init__(self, **values: str) -> None:
        cleaned: dict[str, str] = {}
        for name, value in values.items():
            if not isinstance(value, str):
                raise TypeError("bundle secret values must be strings")
            if value:
                cleaned[name] = value
        self._values = cleaned

    def get(self, name: str) -> str:
        value = self._values.get(name, "")
        if not value:
            raise SsrfValidationError("connection secret bundle is missing a value")
        return value

    def secret_values(self) -> tuple[str, ...]:
        return tuple(self._values.values())


def _reject_forbidden_header_name(name: str) -> None:
    """Refuse any sensitive, framing/hop-by-hop, or ``proxy-*`` header name.

    The SINGLE policy for both caller-supplied headers and a custom auth header
    name — a custom name must not be a back door around the framing denylist
    (``auth_scheme="header", header_name="Content-Length"`` was a smuggling
    vector, Codex-found).
    """
    low = name.strip().lower()
    if (
        not low
        or low in _SSRF_FORBIDDEN_REQUEST_HEADERS
        or low in _SSRF_FORBIDDEN_FRAMING_HEADERS
        or low.startswith("proxy-")
    ):
        raise SsrfValidationError("header name is not permitted")


def _oauth1a_percent(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def _oauth1a_authorization(
    bundle: ConnectionSecretBundle, *, method: str, url: str
) -> str:
    """OAuth 1.0a HMAC-SHA1 ``Authorization`` built INSIDE the child (Twitter).

    The algorithm is the standard OAuth 1.0a HMAC-SHA1 signer (its byte-identical
    reference oracle is pinned in ``tests/test_outbound_channel_migration.py``), but
    it reads the four OAuth secrets from the typed ``ConnectionSecretBundle`` — the
    auth material is applied where the credential already legitimately lives, never
    at the adapter. The bundle is ``{api_key, api_secret, access_token,
    access_token_secret}``; a missing member fails closed via ``bundle.get``.
    """
    oauth_params = {
        "oauth_consumer_key": bundle.get("api_key"),
        "oauth_nonce": secrets.token_urlsafe(24),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": bundle.get("access_token"),
        "oauth_version": "1.0",
    }
    parsed = urllib.parse.urlparse(url)
    base_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
    )
    query_params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    signature_params = {**query_params, **oauth_params}
    encoded_pairs = [
        f"{_oauth1a_percent(key)}={_oauth1a_percent(value)}"
        for key, value in sorted(signature_params.items())
    ]
    normalized = "&".join(encoded_pairs)
    base_string = "&".join([
        method.upper(),
        _oauth1a_percent(base_url),
        _oauth1a_percent(normalized),
    ])
    signing_key = (
        f"{_oauth1a_percent(bundle.get('api_secret'))}&"
        f"{_oauth1a_percent(bundle.get('access_token_secret'))}"
    )
    digest = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")
    rendered = ", ".join(
        f'{_oauth1a_percent(key)}="{_oauth1a_percent(value)}"'
        for key, value in sorted(oauth_params.items())
    )
    return f"OAuth {rendered}"


def _ssrf_auth_headers(
    auth_scheme: str,
    bundle: ConnectionSecretBundle,
    *,
    header_name: str = "",
    method: str = "",
    url: str = "",
) -> dict[str, str]:
    """Build the auth header(s) INSIDE the child from the typed bundle (D5)."""
    scheme = (auth_scheme or "none").strip().lower()
    if scheme == "none":
        result: dict[str, str] = {}
    elif scheme == _URL_SECRET_SCHEME:
        # A capability URL authenticates by the path segment the driver
        # substitutes; there is NO header. Sending one anyway is what the live
        # 2026-09-30 failure did (a bearer header of a value the receiver never
        # asked for), and it hands the credential to a second reader for nothing.
        result = {}
    elif scheme == "bearer":
        result = {"Authorization": f"Bearer {bundle.get('token')}"}
    elif scheme == "basic":
        raw = f"{bundle.get('username')}:{bundle.get('password')}".encode("utf-8")
        result = {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    elif scheme == "header":
        name = (header_name or "").strip()
        if not name or _SSRF_FORBIDDEN_HEADER_CHARS.search(name):
            raise SsrfValidationError("custom auth header name is not permitted")
        _reject_forbidden_header_name(name)
        result = {name: bundle.get("token")}
    elif scheme == "oauth2":
        # The broker already replaced the bundle with the CURRENT access token.
        result = {"Authorization": f"Bearer {bundle.get('token')}"}
    elif scheme == "oauth1a":
        # OAuth 1.0a (Twitter): the signature is over the request method + URL, so
        # they are threaded in from the driver. Signed entirely in the child.
        result = {
            "Authorization": _oauth1a_authorization(bundle, method=method, url=url)
        }
    else:
        raise SsrfValidationError("auth scheme is not supported")
    # Validate the auth-DERIVED values too, not just caller headers: a bundle
    # token carrying CR/LF (obs-fold) would otherwise emit a folded header line
    # http.client accepts verbatim, re-opening request smuggling (Codex-found).
    for value in result.values():
        if _SSRF_FORBIDDEN_HEADER_CHARS.search(value):
            raise SsrfValidationError("auth header value contains forbidden characters")
    return result


@dataclass(frozen=True, slots=True)
class _CanonicalOutboundUrl:
    hostname: str
    port: int
    path_qs: str
    is_ip_literal: bool


#: A path segment at least this long is scanned for in a response ON ITS OWN,
#: not only as part of the joined credential. The same threshold
#: ``api/pending_requests._ENTROPY_RUN_RE`` uses for "an unbroken run this long
#: is a credential, not prose", and it is a threshold rather than the grammar
#: floor (8) for a reason given in ``url_secret_sensitive_values``.
_URL_SECRET_SCANNED_SEGMENT_CHARS = 16


def url_secret_sensitive_values(credential: str) -> tuple[str, ...]:
    """Every string a response must be scanned for: the whole credential, and
    each segment long enough to be a credential on its own.

    A ``{secret+}`` credential is several path segments joined by ``/``. The
    response scanners (``_declassify_response``, and the broker's echo check)
    match SUBSTRINGS, so scanning only the joined form let a destination echo
    ONE segment back — a body carrying just the token of a ``T…/B…/token``
    secret — and that segment reached the caller, ``bounded_evidence``, and the
    run's ``external_write_errors`` preview (gpt-6-astra refute round 1,
    FINDING 2).

    But scanning EVERY segment trades a leak for a denial of service. The
    leading segments of a real multi-segment capability URL are typically PUBLIC
    ids (a workspace id, a channel id, a webhook id), they are short, and a
    legitimate response can contain one — an 8-character numeric id in a JSON
    body would then read as an echo and fail a working connection. So the
    joined credential is always scanned, and a segment is scanned individually
    only when it is long enough to be the secret rather than the address:
    a 24-character webhook token is, a 9-character workspace id is not.
    """
    text = credential if isinstance(credential, str) else ""
    if not text:
        return ()
    values = [
        text,
        *(
            part
            for part in text.split("/")
            if len(part) >= _URL_SECRET_SCANNED_SEGMENT_CHARS
        ),
    ]
    return tuple(dict.fromkeys(values))


def _reject_stray_reserved_tokens(text: str, *, what: str) -> None:
    """Refuse a reserved capability-url token ANYWHERE in ``text``.

    Whole-segment equality was not enough (gpt-6-astra refute round 2,
    FINDING 2). Three spellings survived it: a token in the QUERY string
    (``?q={secret}``, which the substituter appended unchanged), a
    percent-encoded token in a permissive ``{tail+}`` (``%7Bsecret%7D`` — legal
    bytes, so the canonical parse admits it), and one embedded in a larger
    segment (``prefix{secret}``).

    None of them substitutes a credential into the wrong slot — the positioned
    substituter settled that — but the reserved token is a PLATFORM marker, and
    a request carrying one outside its declared slot is malformed. Sending it
    tells the receiver what shape this connection is, and it leaves an invariant
    the design states ("nothing reserved survives") not actually holding. So:
    substring, on the raw text and on its percent-decoded form.
    """
    candidates = (text, urllib.parse.unquote(text))
    for candidate in candidates:
        if _URL_SECRET_TOKEN in candidate or _URL_SECRET_REST_TOKEN in candidate:
            raise SsrfValidationError(
                "the reserved capability-url placeholder may appear only where "
                f"the endpoint declares it (found in the {what})"
            )


def _positioned_url_secret_path(
    path: str, endpoint: OutboundEndpoint, secret: str, token: str
) -> str:
    """The path with the vault segment in the position the TEMPLATE reserved.

    Derived from the matched endpoint, never by searching the path (astra
    FINDING 3). ``/hooks/{secret}/{tail+}`` with ``tail: ".*"`` admits the
    concrete path ``/hooks/{secret}/echo/{secret+}``: a search would find
    ``{secret+}`` in the caller-controlled tail and put the credential there,
    at a path the owner granted for arbitrary content. Positioning by the
    template puts it only where the template says, and the closing invariant
    below — evaluated on the path with the reserved slot BLANKED — refuses any
    reserved token left anywhere else, in any spelling.
    """
    template_segments = endpoint.path_template.split("/")
    concrete = path.split("/")
    if token == _URL_SECRET_REST_TOKEN:
        # A rest placeholder is the FINAL template segment (enforced at
        # authoring), and the allowlist full-matched the joined tail against the
        # literal token — so the tail is exactly that one segment.
        prefix = len(template_segments) - 1
        if concrete[prefix:] != [token]:
            raise SsrfValidationError(
                "the capability-url placeholder is not where the endpoint declares it"
            )
        rebuilt = [*concrete[:prefix], secret]
        remainder = [*concrete[:prefix], ""]
    else:
        try:
            index = template_segments.index(token)
        except ValueError:
            raise SsrfValidationError(
                "the matched endpoint declares no capability-url placeholder"
            ) from None
        if index >= len(concrete) or concrete[index] != token:
            raise SsrfValidationError(
                "the capability-url placeholder is not where the endpoint declares it"
            )
        rebuilt = [*concrete[:index], secret, *concrete[index + 1:]]
        remainder = [*concrete[:index], "", *concrete[index + 1:]]
    # The closing invariant: nothing reserved may survive substitution.
    # Evaluated on the path WITH THE RESERVED SLOT BLANKED, so the one
    # legitimate occurrence is excluded and every other spelling — encoded,
    # embedded, or in a later segment — is caught (astra round 2, FINDING 2).
    # Checked before the secret is joined in, so no refusal can carry it.
    _reject_stray_reserved_tokens("/".join(remainder), what="request path")
    return "/".join(rebuilt)


def _substitute_url_secret(
    canonical: _CanonicalOutboundUrl,
    *,
    auth_scheme: str,
    bundle: ConnectionSecretBundle,
    endpoint: OutboundEndpoint | None = None,
    access_mode: str = ACCESS_EXACT,
) -> _CanonicalOutboundUrl:
    """Put the vault segment where the reserved placeholder is. AFTER the allowlist.

    The ORDER is the design (design.md D2). The canonical parse and
    ``_enforce_endpoint_allowlist`` have already run against the URL as the
    CALLER supplied it, carrying the literal ``{secret}`` token — so the egress
    boundary was decided with no secret material present, and no
    ``SsrfValidationError`` raised by either of them can carry one. Only then is
    the segment spliced in, and only into the path: the host is untouched, so the
    DNS resolution and globally-routable-address check that run next are
    unaffected.

    Substitution is byte-verbatim, which is safe only because
    ``validate_url_secret_value`` has already refused anything that could carry a
    separator, a percent-encoding, a dot-segment or whitespace. It is re-checked
    HERE and not merely trusted from the deposit: a mutated or corrupted vault
    record must fail closed rather than reach a socket.

    A placeholder-bearing URL under any OTHER scheme is refused rather than sent
    literally — that request has no substituter, so it would put ``{secret}`` on
    the wire while a header carried the real credential.
    """
    scheme = (auth_scheme or "none").strip().lower()
    # The PATH only. A placeholder in the query string is never substituted: the
    # secret of a capability URL lives in the path, the allowlist validated the
    # query against `query_patterns`, and splicing a credential into a query
    # would put it somewhere the owner's grant never described.
    path, sep, query = canonical.path_qs.partition("?")
    if scheme != _URL_SECRET_SCHEME:
        # No slot exists on this scheme, so no occurrence anywhere is legitimate.
        _reject_stray_reserved_tokens(canonical.path_qs, what="request")
        return canonical
    if normalize_access_mode(access_mode) != ACCESS_EXACT:
        # A `full` connection is admitted on the HOST alone, so no template was
        # consulted and there is no reserved position to substitute into: every
        # path on the host would take the credential. Creation and
        # `set_access_mode` both refuse the combination; this refuses a row that
        # reached it another way, at the last moment before the wire
        # (gpt-6-astra refute round 1, FINDING 4).
        raise SsrfValidationError(
            f"a {_URL_SECRET_SCHEME} connection cannot be granted full access"
        )
    if endpoint is None:
        raise SsrfValidationError(
            f"a {_URL_SECRET_SCHEME} request must be admitted by a declared endpoint"
        )
    token = url_secret_token(endpoint)
    if not token:
        raise SsrfValidationError(
            "the matched endpoint declares no capability-url placeholder"
        )
    # The QUERY has no reserved slot: the secret of a capability URL is in the
    # path. So no occurrence in it is legitimate, and it is refused rather than
    # appended unchanged — which is what it was (astra round 2, FINDING 2).
    _reject_stray_reserved_tokens(query, what="query string")
    secret = validate_url_secret_value(bundle.get("token"), token)
    return _CanonicalOutboundUrl(
        hostname=canonical.hostname,
        port=canonical.port,
        path_qs=_positioned_url_secret_path(path, endpoint, secret, token) + sep + query,
        is_ip_literal=canonical.is_ip_literal,
    )


def _canonical_request_url(canonical: _CanonicalOutboundUrl) -> str:
    """The exact https URL the driver puts on the wire (also what oauth1a signs)."""
    host_for_url = (
        f"[{canonical.hostname}]"
        if canonical.is_ip_literal and ":" in canonical.hostname
        else canonical.hostname
    )
    url = f"https://{host_for_url}"
    if canonical.port != 443:
        url += f":{canonical.port}"
    return url + canonical.path_qs


def _reject_unsafe_encoded_path(path: str) -> None:
    """Refuse any percent-encoding that decodes to a hidden separator (FIX 2).

    A raw dot-segment is caught by the caller; this catches the *encoded* forms
    an origin would decode AFTER the allowlist template matches — `%2e` (.),
    `%2f` (/), and `%5c`/`%5C` (\\, a Windows/IIS separator missed pre-fix), plus
    overlong-UTF-8 spellings of those (which always use high bytes >=0x7f) and
    encoded control chars. `%20` (space) stays legal so real paths still parse;
    `%25` (double-encoding) is rejected by the caller before this runs.
    """
    for match in _SSRF_PERCENT_ENC_RE.finditer(path):
        byte = int(match.group(1), 16)
        if byte in _SSRF_UNSAFE_ENCODED_BYTES or byte < 0x20 or byte >= 0x7F:
            raise SsrfValidationError(
                "outbound url path contains an unsafe encoded byte"
            )


def _parse_canonical_https_url(
    url: str,
    *,
    allowed_ports: frozenset[int],
) -> _CanonicalOutboundUrl:
    """Parse exactly ONE absolute, canonical https URL or fail closed (D3.1)."""
    if not isinstance(url, str) or not url:
        raise SsrfValidationError("outbound url is required")
    if _SSRF_FORBIDDEN_URL_CHARS.search(url):
        raise SsrfValidationError("outbound url contains forbidden characters")
    if "%25" in url:
        # An encoded percent sign is a double-encoding canonicalization smell.
        raise SsrfValidationError("outbound url must not be double-encoded")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != _SSRF_ALLOWED_SCHEME:
        raise SsrfValidationError("outbound url scheme must be https")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise SsrfValidationError("outbound url must not carry userinfo")
    if parsed.fragment:
        raise SsrfValidationError("outbound url must not carry a fragment")
    host = parsed.hostname
    if not host:
        raise SsrfValidationError("outbound url host is required")
    if "%" in host:
        raise SsrfValidationError("outbound url host must not be percent-encoded")
    try:
        port = parsed.port
    except ValueError:
        raise SsrfValidationError("outbound url port is invalid") from None
    port = 443 if port is None else port
    if port not in allowed_ports:
        raise SsrfValidationError("outbound url port is not permitted")
    path = parsed.path or "/"
    if any(segment in (".", "..") for segment in path.split("/")):
        raise SsrfValidationError("outbound url path must not contain dot-segments")
    _reject_unsafe_encoded_path(path)
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if not is_ip_literal and not _SSRF_HOSTNAME_RE.match(host):
        raise SsrfValidationError("outbound url host is not a permitted hostname")
    path_qs = f"{path}?{parsed.query}" if parsed.query else path
    return _CanonicalOutboundUrl(
        hostname=host,
        port=port,
        path_qs=path_qs,
        is_ip_literal=is_ip_literal,
    )


def _validate_endpoint_methods(methods: Any) -> tuple[str, ...]:
    if isinstance(methods, str) or not isinstance(methods, (list, tuple)):
        raise SsrfValidationError("endpoint methods must be a list")
    seen: list[str] = []
    for method in methods:
        verb = str(method).strip().upper()
        if verb not in _SSRF_ALLOWED_METHODS:
            raise SsrfValidationError("endpoint method is not permitted")
        if verb not in seen:
            seen.append(verb)
    if not seen:
        raise SsrfValidationError("endpoint must permit at least one method")
    return tuple(seen)


def _placeholder_names(path_template: str) -> list[str]:
    return [
        segment[1:-1]
        for segment in path_template.split("/")
        if _SSRF_ENDPOINT_PLACEHOLDER_RE.match(segment)
    ]


def _rest_placeholder_name(path_template: str) -> str | None:
    """The name of the final ``{name+}`` rest placeholder, or ``None``.

    A rest placeholder is permitted ONLY as the final segment and at most once —
    ``_validate_path_template`` enforces that at authoring, so at match time this
    trusts the stored template. ``{name+}`` strips to ``name`` (drop ``{`` and
    ``+}``).
    """
    segments = path_template.split("/")
    last = segments[-1] if segments else ""
    if _SSRF_ENDPOINT_REST_PLACEHOLDER_RE.match(last):
        return last[1:-2]
    return None


def _validate_path_template(path_template: Any) -> str:
    """Validate a stored allowlist path template (traversal-free, ``/``-rooted)."""
    if not isinstance(path_template, str) or not path_template.startswith("/"):
        raise SsrfValidationError("endpoint path_template must be an absolute path")
    if _SSRF_FORBIDDEN_URL_CHARS.search(path_template):
        raise SsrfValidationError("endpoint path_template contains forbidden characters")
    if "%25" in path_template:
        raise SsrfValidationError("endpoint path_template must not be double-encoded")
    # Same encoded-separator guard the concrete URL gets (FIX 2): reject
    # %2e/%2f/%5c and overlong/control encodings a stored template could smuggle.
    _reject_unsafe_encoded_path(path_template)
    segments = path_template.split("/")[1:]
    for index, segment in enumerate(segments):
        if segment in (".", ".."):
            raise SsrfValidationError("endpoint path_template must not contain dot-segments")
        if _SSRF_ENDPOINT_PLACEHOLDER_RE.match(segment):
            continue
        if _SSRF_ENDPOINT_REST_PLACEHOLDER_RE.match(segment):
            # A multi-segment tail is only sound as the FINAL segment — anywhere
            # else it would swallow later fixed segments and defeat the
            # destination pin (`/repos/<owner>/<repo>/...`).
            if index != len(segments) - 1:
                raise SsrfValidationError(
                    "endpoint rest placeholder must be the final path segment"
                )
            continue
        if not _SSRF_ENDPOINT_LITERAL_RE.match(segment):
            raise SsrfValidationError("endpoint path_template segment is not permitted")
    return path_template


def _compile_declared_pattern(pattern: Any) -> str:
    """Validate one declared value pattern (compiles, bounded), return its text."""
    if not isinstance(pattern, str) or not pattern:
        raise SsrfValidationError("endpoint value pattern must be a non-empty string")
    if len(pattern) > _SSRF_MAX_PATTERN_LEN:
        raise SsrfValidationError("endpoint value pattern is too long")
    try:
        re.compile(pattern)
    except re.error:
        raise SsrfValidationError("endpoint value pattern is invalid") from None
    return pattern


def _validate_param_patterns(
    path_template: str, raw: Any
) -> tuple[tuple[str, str], ...]:
    """Every ``{param}`` / ``{param+}`` MUST declare a value pattern; no strays (FIX 3).

    ``{secret}`` / ``{secret+}`` is the ONE exception, and the exception is the
    whole security argument for capability URLs. Its pattern is the platform's:
    the anchored LITERAL token. So a stored ``/mcp/hooks/{secret}`` endpoint
    matches the concrete path ``/mcp/hooks/{secret}`` and nothing else — the
    egress allowlist is evaluated with zero secret material in the URL, and a
    node that puts its own value (or a real secret) in the secret's position is
    refused by the allowlist rather than quietly sent. A caller-declared pattern
    for the reserved name is REFUSED: a permissive one would re-admit exactly the
    node-supplied segment this exists to exclude.
    """
    placeholders = _placeholder_names(path_template)
    rest_name = _rest_placeholder_name(path_template)
    all_names = placeholders + ([rest_name] if rest_name else [])
    if len(set(all_names)) != len(all_names):
        raise SsrfValidationError("endpoint path_template has duplicate placeholders")
    placeholder_set = set(all_names)
    reserved = placeholder_set & {_URL_SECRET_PLACEHOLDER_NAME}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SsrfValidationError("endpoint param_patterns must be an object")
    declared = {str(name) for name in raw}
    if declared & reserved:
        raise SsrfValidationError(
            f"the reserved {_URL_SECRET_TOKEN} placeholder takes no "
            "param_patterns entry: its value comes from the vault, never from a "
            "declared pattern"
        )
    if declared != placeholder_set - reserved:
        raise SsrfValidationError(
            "endpoint param_patterns must declare exactly the path placeholders"
        )
    patterns = [
        (name, _compile_declared_pattern(raw[name]))
        for name in sorted(placeholder_set - reserved)
    ]
    if reserved:
        # The literal token, escaped and anchored by `re.fullmatch` at match
        # time. `{secret+}` is a rest placeholder, so the joined tail must equal
        # the one literal segment `{secret+}` — a multi-segment tail is refused
        # here and only appears after substitution.
        token = (
            _URL_SECRET_REST_TOKEN
            if rest_name == _URL_SECRET_PLACEHOLDER_NAME
            else _URL_SECRET_TOKEN
        )
        patterns.append((_URL_SECRET_PLACEHOLDER_NAME, re.escape(token)))
    return tuple(sorted(patterns))


def _validate_query_rules(
    allowed_raw: Any, patterns_raw: Any, required_raw: Any
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Declared query names + optional value patterns + required names.

    ``required_query`` names must be a subset of ``allowed_query`` and are each
    enforced present EXACTLY ONCE at match time (Codex FIX: exactly-one ref).
    Undeclared names are refused; declared names may be pattern-constrained.
    """
    if allowed_raw is None:
        allowed_raw = []
    if isinstance(allowed_raw, str) or not isinstance(allowed_raw, (list, tuple)):
        raise SsrfValidationError("endpoint allowed_query must be a list")
    allowed: list[str] = []
    for name in allowed_raw:
        text = str(name).strip()
        if not _SSRF_QUERY_NAME_RE.match(text):
            raise SsrfValidationError("endpoint allowed_query name is not permitted")
        if text not in allowed:
            allowed.append(text)
    if patterns_raw is None:
        patterns_raw = {}
    if not isinstance(patterns_raw, dict):
        raise SsrfValidationError("endpoint query_patterns must be an object")
    patterns: list[tuple[str, str]] = []
    for name, pattern in patterns_raw.items():
        text = str(name).strip()
        if text not in allowed:
            raise SsrfValidationError(
                "endpoint query_patterns names must be in allowed_query"
            )
        patterns.append((text, _compile_declared_pattern(pattern)))
    if required_raw is None:
        required_raw = []
    if isinstance(required_raw, str) or not isinstance(required_raw, (list, tuple)):
        raise SsrfValidationError("endpoint required_query must be a list")
    required: list[str] = []
    for name in required_raw:
        text = str(name).strip()
        if text not in allowed:
            raise SsrfValidationError(
                "endpoint required_query names must be in allowed_query"
            )
        if text not in required:
            required.append(text)
    return tuple(allowed), tuple(sorted(patterns)), tuple(sorted(required))


def _validate_endpoint(raw: Any) -> OutboundEndpoint:
    """Coerce+validate one stored/authored allowlist endpoint, or fail closed."""
    if isinstance(raw, OutboundEndpoint):
        raw = raw.as_dict()
    if not isinstance(raw, dict):
        raise SsrfValidationError("endpoint must be an object")
    host = str(raw.get("host", "")).strip().lower()
    if not host or "%" in host or not _SSRF_HOSTNAME_RE.match(host):
        # Allowlist hosts are real DNS hostnames only — never IP literals, never
        # single-label names — matching the transport's own hostname policy.
        raise SsrfValidationError("endpoint host is not a permitted hostname")
    path_template = _validate_path_template(raw.get("path_template"))
    allowed_query, query_patterns, required_query = _validate_query_rules(
        raw.get("allowed_query"), raw.get("query_patterns"), raw.get("required_query")
    )
    methods = _validate_endpoint_methods(raw.get("methods"))
    redirect_mode = raw.get("redirect_mode", "none")
    if type(redirect_mode) is not str or redirect_mode not in {"none", "public_https_get"}:
        raise SsrfValidationError("endpoint redirect_mode is not permitted")
    if redirect_mode != "none" and methods != ("GET",):
        raise SsrfValidationError("redirect permission requires a GET-only endpoint")
    return OutboundEndpoint(
        host=host,
        path_template=path_template,
        methods=methods,
        param_patterns=_validate_param_patterns(path_template, raw.get("param_patterns")),
        allowed_query=allowed_query,
        query_patterns=query_patterns,
        required_query=required_query,
        redirect_mode=redirect_mode,
    )


def _parse_allowed_endpoints(raw: Any) -> tuple[OutboundEndpoint, ...]:
    """Parse the allowlist from create input or stored JSON; each is validated."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise SsrfValidationError("stored endpoint allowlist is invalid") from None
    if isinstance(raw, (list, tuple)):
        return tuple(_validate_endpoint(item) for item in raw)
    raise SsrfValidationError("endpoint allowlist must be a list")


def _segment_matches_pattern(segment: str, pattern: str) -> bool:
    """Full-match one concrete segment/value against a declared pattern, bounded."""
    if not segment or segment in (".", ".."):
        return False
    if len(segment) > _SSRF_MAX_MATCH_SEGMENT:
        return False
    return re.fullmatch(pattern, segment) is not None


def _fixed_segments_match(
    concrete: list[str], pattern: list[str], param_patterns: dict[str, str]
) -> bool:
    """Match a run of concrete segments against fixed/``{param}`` template segments.

    Each ``{param}`` must full-match its DECLARED single-segment pattern; a
    placeholder with no declared pattern fails closed (an over-broad "any
    non-empty segment" match is exactly the FIX 3 bypass). Literals must be
    byte-equal. Lengths must already be equal.
    """
    for got, want in zip(concrete, pattern):
        if _SSRF_ENDPOINT_PLACEHOLDER_RE.match(want):
            declared = param_patterns.get(want[1:-1])
            if declared is None or not _segment_matches_pattern(got, declared):
                return False
            continue
        if got != want:
            return False
    return True


def _path_matches_template(
    path: str, template: str, param_patterns: dict[str, str]
) -> bool:
    """Segment-wise match; each ``{param}``/``{param+}`` full-matches its pattern.

    Fixed-arity templates require equal segment counts. A template ending in a
    ``{name+}`` rest placeholder matches its fixed prefix segment-for-segment,
    then captures the REMAINING concrete segments (>=1) as the rest-tail — each
    tail segment rejected if empty / ``.`` / ``..`` / over-long, the whole tail
    bounded, and the ``/``-joined tail full-matched against the rest-param's
    DECLARED endpoint-specific pattern. The concrete URL is already canonical,
    literal-dot-segment-free, and stripped of encoded separators (%2e/%2f/%5c)
    upstream, so the rest-tail cannot smuggle a decoded traversal.
    """
    concrete = path.split("/")
    pattern = template.split("/")
    rest_name = _rest_placeholder_name(template)
    if rest_name is None:
        if len(concrete) != len(pattern):
            return False
        return _fixed_segments_match(concrete, pattern, param_patterns)

    prefix = pattern[:-1]
    # The rest placeholder captures one OR MORE segments.
    if len(concrete) < len(prefix) + 1:
        return False
    if not _fixed_segments_match(concrete[: len(prefix)], prefix, param_patterns):
        return False
    tail_segments = concrete[len(prefix):]
    if len(tail_segments) > _SSRF_MAX_REST_SEGMENTS:
        return False
    for seg in tail_segments:
        if not seg or seg in (".", "..") or len(seg) > _SSRF_MAX_MATCH_SEGMENT:
            return False
    tail = "/".join(tail_segments)
    if len(tail) > _SSRF_MAX_REST_TAIL_LEN:
        return False
    declared = param_patterns.get(rest_name)
    if declared is None:
        return False
    return re.fullmatch(declared, tail) is not None


def _query_permitted(query_items: list[tuple[str, str]], endpoint: OutboundEndpoint) -> bool:
    """Refuse any query parameter not DECLARED in the endpoint (FIX 3).

    Queries are no longer discarded before matching: an undeclared parameter (or
    a declared one whose value fails its pattern) refuses the whole request, so a
    tenant/target/operation cannot ride in a query string to escape the
    connection's destination. A ``required_query`` name must additionally appear
    EXACTLY ONCE (Codex FIX: exactly-one validated ref) — zero occurrences or a
    duplicate refuses the request.
    """
    allowed = set(endpoint.allowed_query)
    patterns = dict(endpoint.query_patterns)
    counts: dict[str, int] = {}
    for name, value in query_items:
        if name not in allowed:
            return False
        declared = patterns.get(name)
        if declared is not None and not _segment_matches_pattern(value, declared):
            return False
        counts[name] = counts.get(name, 0) + 1
    for required in endpoint.required_query:
        if counts.get(required, 0) != 1:
            return False
    return True


def _enforce_endpoint_allowlist(
    canonical: _CanonicalOutboundUrl,
    method: str,
    endpoints: tuple[OutboundEndpoint, ...],
    access_mode: str = ACCESS_EXACT,
) -> OutboundEndpoint | None:
    """Refuse any host/method/path/query not on the connection allowlist (design.md D3).

    Returns the endpoint that ADMITTED the request (``None`` on a ``full``
    connection, where the host match is the whole decision and no template was
    consulted). The identity of the matching endpoint is what a capability-URL
    substitution is positioned by: searching the path for the placeholder
    instead let a caller-controlled ``{tail+}`` segment carry a reserved token
    and receive the secret in a position the allowlist never reserved for it
    (gpt-6-astra refute round 1, FINDING 3).

    This is the real egress boundary: an EMPTY allowlist permits nothing, and a
    URL whose host, method, path, OR query does not match a declared endpoint is
    refused before any socket is opened.

    On a ``full`` connection (full-channel-access D3.1) the owner has said the
    universe may do anything the key itself can do on this channel, so once the
    HOST matches one the agent declared, any path, any query and any of the five
    verbs are admitted. What "full" does NOT touch: the caller has already
    refused a non-HTTPS scheme, userinfo, a port other than 443, dot segments,
    encoded separators, double encoding and a verb outside the five; and the DNS
    resolution plus the globally-routable-address check still run after this,
    immediately before the socket. Full is bounded to the 1-4 hosts the channel
    declared, never to every host the credential might reach.
    """
    if not endpoints:
        raise SsrfValidationError("connection has no permitted endpoints")
    host = canonical.hostname.strip().lower()
    verb = (method or "").strip().upper()
    if normalize_access_mode(access_mode) == ACCESS_FULL:
        if any(endpoint.host == host for endpoint in endpoints):
            return None
        raise SsrfValidationError("outbound host is not on the connection allowlist")
    raw_path, _, raw_query = canonical.path_qs.partition("?")
    if len(raw_query) > _SSRF_MAX_QUERY_LEN:
        raise SsrfValidationError("outbound url query is too long")
    try:
        query_items = (
            urllib.parse.parse_qsl(
                raw_query,
                keep_blank_values=True,
                max_num_fields=_SSRF_MAX_QUERY_FIELDS,
            )
            if raw_query
            else []
        )
    except ValueError:
        # parse_qsl raises when the field count exceeds the bound; treat a
        # flood as a refused request rather than an unbounded parse.
        raise SsrfValidationError("outbound url query has too many fields") from None
    for endpoint in endpoints:
        if endpoint.host != host:
            continue
        if verb not in endpoint.methods:
            continue
        if not _path_matches_template(
            raw_path, endpoint.path_template, dict(endpoint.param_patterns)
        ):
            continue
        if not _query_permitted(query_items, endpoint):
            continue
        return endpoint
    raise SsrfValidationError("outbound endpoint is not on the connection allowlist")


_REALTIME_VOICE_PROTOCOL = "tinyassets.voice.v1"
_CAPABILITY_SERVICE_NAME_MAX = 80
_CAPABILITY_URL_MAX = 2048


def _validate_capability_kind(value: Any) -> str:
    kind = value.strip() if isinstance(value, str) else ""
    if kind not in _CAPABILITY_SPECS:
        raise ValueError("capability_kind is not supported")
    return kind


def _capability_https_url(name: str, value: Any, *, optional: bool = False) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if optional and not text:
        return ""
    if not text or len(text) > _CAPABILITY_URL_MAX:
        raise ValueError(f"{name} must be a bounded HTTPS URL")
    try:
        canonical = _parse_canonical_https_url(text, allowed_ports=frozenset({443}))
    except SsrfValidationError as exc:
        raise ValueError(f"{name} must be a canonical HTTPS URL") from exc
    return _canonical_request_url(canonical)


def _validate_realtime_voice_capability(
    connection_id: str, descriptor: Any
) -> ConnectionCapability:
    """Validate the complete, closed metadata document for one capability."""

    if not isinstance(descriptor, dict):
        raise ValueError("capability descriptor must be an object")
    kind = "realtime_voice"
    required = {"protocol", "session_url", "service_name"}
    allowed = required | {"privacy_url"}
    if set(descriptor) - allowed or not required.issubset(descriptor):
        raise ValueError("capability descriptor fields are invalid")
    if descriptor.get("protocol") != _REALTIME_VOICE_PROTOCOL:
        raise ValueError("capability protocol is not supported")
    service_name = (
        descriptor["service_name"].strip()
        if isinstance(descriptor.get("service_name"), str)
        else ""
    )
    if (
        not 1 <= len(service_name) <= _CAPABILITY_SERVICE_NAME_MAX
        or any(ord(char) < 32 or ord(char) == 127 for char in service_name)
    ):
        raise ValueError("capability service_name is invalid")
    return ConnectionCapability(
        connection_id=_required("connection_id", connection_id),
        capability_kind=kind,
        protocol=_REALTIME_VOICE_PROTOCOL,
        session_url=_capability_https_url("session_url", descriptor.get("session_url")),
        service_name=service_name,
        privacy_url=_capability_https_url(
            "privacy_url", descriptor.get("privacy_url"), optional=True
        ),
    )


def _validate_model_discovery_capability(
    connection_id: str, descriptor: Any
) -> ModelDiscoveryCapability:
    from tinyassets.providers.discovery_protocols import discovery_protocol

    custom = isinstance(descriptor, dict) and "schema_version" in descriptor
    required = {"schema_version", "contract", "catalogue_url"} if custom else {
        "protocol", "catalogue_url",
    }
    if (
        not isinstance(descriptor, dict)
        or not required.issubset(descriptor)
        or set(descriptor) - (required | {"benchmark_url"})
    ):
        raise ValueError("discovery descriptor fields are invalid")
    contract_json = ""
    if custom:
        from tinyassets.providers.discovery_contract import SourceContract

        if type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1:
            raise ValueError("unsupported discovery descriptor version")
        contract = SourceContract.compile(descriptor["contract"])
        contract_json = contract.descriptor_json
        protocol = ""  # A custom source is not a caller-created registry identity.
    else:
        protocol = descriptor["protocol"]
        contract = discovery_protocol(protocol)
    if "benchmark_url" in descriptor and not isinstance(descriptor["benchmark_url"], str):
        raise ValueError("discovery benchmark_url must be a string when provided")
    catalogue_url = _capability_https_url("catalogue_url", descriptor["catalogue_url"])
    benchmark_url = _capability_https_url(
        "benchmark_url", descriptor.get("benchmark_url"), optional=True
    )
    contract.validate_urls(catalogue_url, benchmark_url)
    return ModelDiscoveryCapability(
        _required("connection_id", connection_id), "model_discovery", protocol,
        catalogue_url, benchmark_url, contract_json,
    )


_MODEL_USE_MAX_MODELS = 64
_MODEL_USE_MAX_CONTEXT = 100_000_000
#: Billing classes a static model list may declare. ``free`` and ``flat``
#: (a subscription or a local source) carry no prices, so they admit no
#: spending. ``metered`` needs prices, which the ``model_discovery`` source
#: contract carries; it is not declared here.
MODEL_USE_BILLING = frozenset({"free", "flat"})
MODEL_USE_PRICED_CONFLICT = (
    "this connection has a priced model catalogue; a declared model list cannot "
    "describe it, because its prices, not a label, decide what may be spent"
)


def _validate_model_use_capability(connection_id: str, descriptor: Any) -> ModelUseCapability:
    from tinyassets.providers.wire_dialects import UnknownDialect, canonical_dialect

    if not isinstance(descriptor, dict) or set(descriptor) != {"wire", "models", "billing"}:
        raise ValueError("model use needs exactly wire, models and billing")
    try:
        wire = canonical_dialect(descriptor["wire"])
    except UnknownDialect as exc:
        raise ValueError(str(exc)) from None
    billing = descriptor["billing"]
    if billing == "metered" or (isinstance(billing, dict) and "metered" in billing):
        raise ValueError(
            "metered billing needs prices: declare them with a model_discovery source "
            "contract; a static model list may be billing 'free' or 'flat'"
        )
    if billing not in MODEL_USE_BILLING:
        raise ValueError("billing must be 'free' or 'flat'")
    raw_models = descriptor["models"]
    if type(raw_models) is not list or not 1 <= len(raw_models) <= _MODEL_USE_MAX_MODELS:
        raise ValueError(
            f"models must list 1-{_MODEL_USE_MAX_MODELS} models as "
            '{"id", "tools", "context"}'
        )
    models: list[tuple[str, bool, int]] = []
    for raw in raw_models:
        if not isinstance(raw, dict) or set(raw) != {"id", "tools", "context"}:
            raise ValueError('each model needs exactly "id", "tools" and "context"')
        model_id, tools, context = raw["id"], raw["tools"], raw["context"]
        if (type(model_id) is not str or not 1 <= len(model_id) <= 200
                or not model_id.isprintable() or model_id != model_id.strip()):
            raise ValueError("model id must be 1-200 printable characters")
        if type(tools) is not bool:
            raise ValueError("model tools must be true or false")
        if type(context) is not int or not 1 <= context <= _MODEL_USE_MAX_CONTEXT:
            raise ValueError("model context must be a positive token count")
        if any(existing[0] == model_id for existing in models):
            raise ValueError("model ids must be unique")
        models.append((model_id, tools, context))
    return ModelUseCapability(
        _required("connection_id", connection_id), "model_use", wire, tuple(models), billing,
    )


_CONSTANT_HEADERS_MAX = 16
_CONSTANT_HEADER_VALUE_MAX = 256
_HEADER_TOKEN_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
#: A run this long is a credential, not a version string. Constant headers are
#: readable connection metadata, so a secret belongs in the auth scheme.
_CONSTANT_HEADER_SECRET_RUN = re.compile(r"[A-Za-z0-9_\-]{32,}")
#: Header NAMES that carry credentials or sessions. A constant header is
#: readable metadata, so it may never occupy one of these, whatever its value.
_CONSTANT_HEADER_CREDENTIAL_NAME = re.compile(
    r"key|token|secret|auth|passw|session|cookie|signature|credential", re.IGNORECASE,
)


def _validate_constant_headers_capability(
    connection_id: str, descriptor: Any
) -> ConstantHeadersCapability:
    if not isinstance(descriptor, dict) or set(descriptor) != {"headers"}:
        raise ValueError('constant headers need exactly {"headers": {name: value}}')
    raw = descriptor["headers"]
    if type(raw) is not dict or not 1 <= len(raw) <= _CONSTANT_HEADERS_MAX:
        raise ValueError(f"constant headers must name 1-{_CONSTANT_HEADERS_MAX} headers")
    headers: dict[str, str] = {}
    for name, value in raw.items():
        if type(name) is not str or not _HEADER_TOKEN_RE.match(name):
            raise ValueError("constant header name is not a valid header token")
        try:
            _reject_forbidden_header_name(name)
        except SsrfValidationError:
            raise ValueError(f"header {name!r} may not be set as a constant") from None
        if _CONSTANT_HEADER_CREDENTIAL_NAME.search(name):
            raise ValueError(
                f"header {name!r} names a credential; put the key in the connection's "
                "auth (auth_scheme header), never in a constant header"
            )
        if name.lower() in {existing.lower() for existing in headers}:
            raise ValueError("constant header names must be unique")
        if (type(value) is not str or not 1 <= len(value) <= _CONSTANT_HEADER_VALUE_MAX
                or _SSRF_FORBIDDEN_HEADER_CHARS.search(value)):
            raise ValueError(f"constant header {name!r} needs a short single-line value")
        if _CONSTANT_HEADER_SECRET_RUN.search(value):
            raise ValueError(
                f"constant header {name!r} looks like a credential; constant headers are "
                "readable metadata, so put a secret in the connection's auth instead"
            )
        headers[name] = value
    return ConstantHeadersCapability(
        _required("connection_id", connection_id), "constant_headers",
        tuple(sorted(headers.items())),
    )


def merge_constant_headers(request: Any, capability: Any) -> Any:
    """Return ``request`` with the connection's constant headers applied.

    The connection's declaration wins over a same-named caller header (case
    insensitively), so a node cannot send a different API version than the one
    the owner configured. The auth scheme is applied later by the driver and
    wins over both.
    """
    if capability is None or not isinstance(request, dict):
        return request
    caller = request.get("headers")
    if caller is not None and not isinstance(caller, dict):
        return request  # Malformed caller headers are refused by the driver as before.
    constant = dict(capability.headers)
    lowered = {name.lower() for name in constant}
    merged = {
        name: value for name, value in (caller or {}).items()
        if str(name).lower() not in lowered
    }
    merged.update(constant)
    return {**request, "headers": merged}


@dataclass(frozen=True, slots=True)
class _CapabilitySpec:
    value_type: type
    validate: Callable[[str, Any], Any]
    verb: str
    url_fields: tuple[str, ...]


_CAPABILITY_SPECS = {
    "realtime_voice": _CapabilitySpec(
        ConnectionCapability, _validate_realtime_voice_capability, "POST", ("session_url",)
    ),
    "model_discovery": _CapabilitySpec(
        ModelDiscoveryCapability, _validate_model_discovery_capability, "GET",
        ("catalogue_url", "benchmark_url"),
    ),
    # A model use sends inference as POST to the connection's own endpoint.
    "model_use": _CapabilitySpec(
        ModelUseCapability, _validate_model_use_capability, "POST", (),
    ),
    # Headers ride every verb the connection already allows; they add none.
    "constant_headers": _CapabilitySpec(
        ConstantHeadersCapability, _validate_constant_headers_capability, "", (),
    ),
}


def _validate_connection_capability(
    connection_id: str, capability_kind: str, descriptor: Any
) -> ConnectionCapability | ModelDiscoveryCapability:
    spec = _CAPABILITY_SPECS[_validate_capability_kind(capability_kind)]
    capability = spec.validate(connection_id, descriptor)
    if not isinstance(capability, spec.value_type):
        raise TypeError("capability validator returned the wrong value type")
    return capability


def _classify_global_address(ip_text: str) -> str:
    """Return the address only if it is globally routable; else fail closed.

    Unwraps IPv4-mapped / 6to4 / Teredo IPv6 so an embedded private v4 cannot
    ride in as a "global" v6, and rejects loopback, link-local (incl. the cloud
    metadata address), private, ULA, shared/CGNAT, reserved, unspecified, and
    multicast (D3.2).
    """
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        raise SsrfValidationError("resolved address is not a valid ip") from None
    for network in _SSRF_EXTRA_BLOCKED_NETWORKS:
        if ip_obj.version == network.version and ip_obj in network:
            raise SsrfValidationError("resolved address is not globally routable")
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            ip_obj = ip_obj.ipv4_mapped
        elif ip_obj.sixtofour is not None:
            ip_obj = ip_obj.sixtofour
        elif ip_obj.teredo is not None:
            ip_obj = ip_obj.teredo[1]
    if (
        not ip_obj.is_global
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_private
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        # IPv6-only; ``fec0::/10`` deprecated site-local reads as global on some
        # CPython versions (Codex-found). getattr keeps the IPv4 path working.
        or getattr(ip_obj, "is_site_local", False)
    ):
        raise SsrfValidationError("resolved address is not globally routable")
    return str(ip_obj)


def _default_dns_resolver(hostname: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            ordered.append(addr)
    return ordered


def _threaded_dns_resolve(
    hostname: str,
    port: int,
    *,
    base_resolver: Callable[[str, int], list[str]],
    timeout: float,
) -> list[str]:
    """Resolve ``hostname`` in a worker thread bounded by ``timeout`` (residual #3).

    ``getaddrinfo`` is a blocking OS call the request's monotonic deadline cannot
    interrupt once it is stuck, so a hostile/black-hole resolver could hang the
    child indefinitely BEFORE the deadline machinery (which starts at connect
    time) is armed. Running it in a daemon thread we abandon on timeout closes
    that: on timeout the thread is left to die with the process and the request
    FAILS CLOSED. The abandoned thread never mutates request state — its result
    is read only if it finished in time.
    """
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def _work() -> None:
        try:
            outcome["addresses"] = base_resolver(hostname, port)
        except BaseException as exc:  # noqa: BLE001 - carried, re-raised on the caller thread
            outcome["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_work,
        name="outbound-dns-resolve",
        daemon=True,
    )
    worker.start()
    if not done.wait(max(0.0, float(timeout))):
        # Abandon the hung getaddrinfo thread; fail closed within the budget.
        raise SsrfValidationError("outbound host resolution exceeded the deadline")
    error = outcome.get("error")
    if error is not None:
        if isinstance(error, SsrfValidationError):
            raise error
        raise SsrfValidationError("outbound host resolution failed") from None
    return list(outcome.get("addresses", []))


def _make_default_resolver(timeout: float) -> Callable[[str, int], list[str]]:
    """The production resolver: ``getaddrinfo`` wrapped in the threaded deadline."""

    def _resolver(hostname: str, port: int) -> list[str]:
        return _threaded_dns_resolve(
            hostname,
            port,
            base_resolver=_default_dns_resolver,
            timeout=timeout,
        )

    return _resolver


def _resolve_pinned_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Callable[[str, int], list[str]],
    validator: Callable[[str], str],
) -> list[str]:
    """Resolve, validate EVERY A/AAAA result, and return pinnable addresses."""
    try:
        candidates = resolver(hostname, port)
    except SsrfValidationError:
        raise
    except Exception:
        raise SsrfValidationError("outbound host resolution failed") from None
    if not candidates:
        raise SsrfValidationError("outbound host did not resolve")
    # validator raises on the FIRST non-global result — reject the whole host.
    return [validator(addr) for addr in candidates]


def _normalize_ip(text: str) -> str:
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text


def _default_open_socket(
    address: tuple[str, int],
    timeout: float | None,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    return socket.create_connection(
        address,
        timeout=timeout,
        source_address=source_address,
    )


class _TotalDeadlineExceeded(Exception):
    """The request exceeded its monotonic wall-clock budget."""


def _is_timeout(exc: BaseException) -> bool:
    """A per-read timeout anywhere on the exception chain (not the total)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, _TotalDeadlineExceeded):
            return False
        if isinstance(cur, TimeoutError) or isinstance(getattr(cur, "reason", None), TimeoutError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _looks_like_deadline_breach(exc: BaseException, deadline: float) -> bool:
    """True if a request failure is really a total-deadline breach.

    ``_DeadlineSocket`` closes a drip two ways: it raises ``_TotalDeadlineExceeded``
    when the budget is already spent, but as the deadline approaches it tightens the
    socket timeout to the tiny remaining budget, so the LAST read (e.g. during the
    status-line/header parse inside ``opener.open()``, which urllib does not wrap)
    surfaces as a ``TimeoutError`` right at the deadline. Both mean the same thing —
    detect either so a breach in ANY phase is labeled uniformly rather than as a
    generic destination failure.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, _TotalDeadlineExceeded):
            return True
        reason = getattr(cur, "reason", None)
        if isinstance(reason, _TotalDeadlineExceeded):
            return True
        # The per-operation timeout firing is the same answer -- the destination
        # did not answer in time -- and it races the total when both are equal:
        # a silent model at 30s/30s surfaced as a generic destination failure.
        if isinstance(cur, TimeoutError) or isinstance(reason, TimeoutError):
            return True
        cur = cur.__cause__ or cur.__context__
    return time.monotonic() >= deadline


class _DeadlineSocket:
    """A delegating socket proxy that enforces an absolute monotonic deadline on
    EVERY read and write.

    http.client parses the status line, headers, body, and chunked trailers by
    reading through ``sock.makefile("rb")`` -> ``recv_into`` inside stdlib loops
    that honor only the per-socket timeout. A drip that sends one byte before
    each timeout keeps those loops alive forever (Codex-found). Wrapping the
    connected socket so every ``recv_into``/``recv``/``send`` first checks the
    deadline — and tightens the socket timeout to the remaining budget — makes
    the total budget cover ALL phases uniformly, so a drip aborts at the
    deadline inside the stdlib parser too, not only in the body loop.
    """

    __slots__ = ("_deadline", "_per_op_timeout", "_sock", "_checkpoint", "bound_by_total")

    def __init__(self, sock: Any, *, deadline: float, per_op_timeout: float | None,
                 checkpoint: Callable[[], None] | None = None) -> None:
        self._sock = sock
        self._deadline = deadline
        self._per_op_timeout = per_op_timeout
        self._checkpoint = checkpoint
        #: Whether the LAST armed read was limited by the total deadline rather
        #: than the per-read window: a timeout then is the deadline, not a stall.
        self.bound_by_total = True

    def set_per_op_timeout(self, seconds: float) -> None:
        """Change the per-read window from here on (the total is unchanged)."""
        self._per_op_timeout = seconds

    def set_deadline(self, deadline: float) -> None:
        """Move the absolute deadline (a streamed reply, once its headers are in)."""
        self._deadline = deadline

    def _arm(self) -> None:
        if self._checkpoint is not None:
            self._checkpoint()
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _TotalDeadlineExceeded
        budget = remaining
        if self._per_op_timeout is not None and self._per_op_timeout > 0:
            budget = min(self._per_op_timeout, remaining)
        self.bound_by_total = budget >= remaining
        try:
            self._sock.settimeout(max(0.001, budget))
        except OSError:
            pass

    def recv_into(self, buffer: Any, *args: Any) -> int:
        self._arm()
        return self._sock.recv_into(buffer, *args)

    def recv(self, *args: Any) -> bytes:
        self._arm()
        return self._sock.recv(*args)

    def sendall(self, data: Any, *args: Any) -> None:
        self._arm()
        return self._sock.sendall(data, *args)

    def send(self, data: Any, *args: Any) -> int:
        self._arm()
        return self._sock.send(data, *args)

    def makefile(self, mode: str = "rb", buffering: int | None = None, **_kwargs: Any) -> Any:
        # Mirror socket.makefile, but bind SocketIO to THIS proxy so its reads go
        # through our deadline-armed recv_into rather than the raw socket's.
        raw = socket.SocketIO(self, "rb")
        self._sock._io_refs += 1
        if buffering is None:
            buffering = io.DEFAULT_BUFFER_SIZE
        if buffering == 0:
            return raw
        return io.BufferedReader(raw, buffering)

    def _decref_socketios(self) -> None:
        self._sock._decref_socketios()

    def settimeout(self, value: float | None) -> None:
        self._sock.settimeout(value)

    def gettimeout(self) -> float | None:
        return self._sock.gettimeout()

    def close(self) -> None:
        self._sock.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a PINNED validated address, keeping SNI/cert verification.

    Defeats DNS-rebinding/TOCTOU: the vetted address (already classified
    ``is_global``) is dialed directly, so there is no second resolution, and the
    connected peer is re-checked against the pin. TLS SNI and certificate
    hostname verification still run against the original hostname (``self.host``).
    The connected socket is wrapped in a ``_DeadlineSocket`` so the request's
    total wall-clock deadline covers status line + headers + body + trailers.
    """

    def __init__(
        self,
        host: str,
        *,
        pinned_address: str,
        open_socket: Callable[..., socket.socket],
        deadline: float,
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        sockets: list | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(host, **kwargs)
        self._pinned_address = pinned_address
        self._open_socket = open_socket
        self._deadline = deadline
        self._on_connect = on_connect
        self._checkpoint = checkpoint
        self._sockets = sockets

    def connect(self) -> None:  # noqa: D102 - overrides http.client
        # Bound the TCP connect by the remaining TOTAL budget, not just the per-op
        # timeout: the deadline otherwise only covers post-handshake reads, so a
        # slow-connect + slow-TLS peer could push the request well past it
        # (Codex-found: 29s connect + 5s handshake beats a 30s deadline).
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _TotalDeadlineExceeded
        connect_timeout = (
            remaining if self.timeout is None else min(self.timeout, remaining)
        )
        sock = self._open_socket(
            (self._pinned_address, self.port),
            connect_timeout,
            self.source_address,
        )
        try:
            peer = sock.getpeername()[0]
            if _normalize_ip(peer) != _normalize_ip(self._pinned_address):
                raise SsrfValidationError("connected peer is not the pinned address")
            # Bound the TLS handshake by the remaining budget too — the raw socket's
            # timeout governs the handshake reads before _DeadlineSocket is installed.
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise _TotalDeadlineExceeded
            sock.settimeout(
                remaining if self.timeout is None else min(self.timeout, remaining)
            )
        except BaseException:
            try:
                sock.close()
            except Exception:
                pass
            raise
        tls = self._context.wrap_socket(sock, server_hostname=self.host)
        # Enforce the total deadline at the socket layer, covering every http.client
        # read phase (status line, headers, body, chunked trailers) — not just the
        # body loop, which cannot see the deadline while parsing runs in stdlib.
        self.sock = _DeadlineSocket(
            tls,
            deadline=self._deadline,
            per_op_timeout=self.timeout,
            checkpoint=self._checkpoint,
        )
        if self._on_connect is not None:
            # Record the connection before the first request write.
            self._on_connect(tls)
        if self._sockets is not None:
            self._sockets.append(self.sock)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib HTTPS handler that drives the pinned connection above."""

    def __init__(
        self,
        *,
        context: Any,
        pinned_address: str,
        open_socket: Callable[..., socket.socket],
        deadline: float,
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        sockets: list | None = None,
    ) -> None:
        super().__init__(context=context)
        self._pinned_address = pinned_address
        self._open_socket = open_socket
        self._deadline = deadline
        self._on_connect = on_connect
        self._checkpoint = checkpoint
        self._sockets = sockets

    def https_open(self, req: Any) -> Any:
        return self.do_open(self._make_connection, req)

    def _make_connection(
        self,
        host: str,
        *,
        timeout: float | None = None,
        **_ignored: Any,
    ) -> _PinnedHTTPSConnection:
        return _PinnedHTTPSConnection(
            host,
            timeout=timeout,
            context=self._context,
            pinned_address=self._pinned_address,
            open_socket=self._open_socket,
            deadline=self._deadline,
            on_connect=self._on_connect,
            checkpoint=self._checkpoint,
            sockets=self._sockets,
        )


def _read_capped_body(
    response: Any, max_body_bytes: int, chunks: list[bytes] | None = None,
) -> bytes | None:
    """Read the body in bounded chunks, enforcing only the size cap.

    The wall-clock deadline is enforced at the socket layer (``_DeadlineSocket``),
    which raises ``_TotalDeadlineExceeded`` from inside ``read1``'s recv if the
    budget is spent — so this loop only needs the size cap. ``read1`` returns
    after at most one underlying recv, so a huge body is cut at the cap without
    being fully read. Returns the bytes, or None on a size-bound violation. ``chunks``, when
    given, receives each piece as it arrives, so a caller can keep a body that
    stopped arriving part-way.
    """
    chunks = [] if chunks is None else chunks
    total = 0
    while True:
        piece = response.read1(min(_SSRF_READ_CHUNK, max_body_bytes + 1 - total))
        if not piece:
            break
        total += len(piece)
        if total > max_body_bytes:
            return None
        chunks.append(piece)
    return b"".join(chunks)


@dataclass(repr=False)
class _HttpHopMetadata:
    """Child-local bounded hop data. Never returned in a transport result."""

    locations: tuple[str, ...] = ()
    body_bytes: int = 0


def _remaining_redirect_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise OutboundDeadlineExceeded("outbound request exceeded the total deadline")
    return remaining


def _redirect_target(
    location: str, previous: _CanonicalOutboundUrl, allowed_ports: frozenset[int],
) -> _CanonicalOutboundUrl:
    """Validate the reference BEFORE joining, then apply the full URL policy."""
    invalid = False
    target = None
    try:
        if not location or "#" in location or _SSRF_FORBIDDEN_URL_CHARS.search(location):
            raise ValueError("invalid reference")
        reference = urllib.parse.urlsplit(location)
        if reference.scheme and reference.scheme != "https":
            raise ValueError("invalid scheme")
        if any(segment in {".", ".."} for segment in reference.path.split("/")):
            raise ValueError("invalid path")
        _reject_unsafe_encoded_path(reference.path)
        target = _parse_canonical_https_url(
            urllib.parse.urljoin(_canonical_request_url(previous), location),
            allowed_ports=allowed_ports,
        )
        query = target.path_qs.partition("?")[2]
        if len(query) > _SSRF_MAX_QUERY_LEN:
            raise ValueError("query bound")
        urllib.parse.parse_qsl(query, keep_blank_values=True, max_num_fields=_SSRF_MAX_QUERY_FIELDS)
    except (ValueError, TypeError, ProxyRequestError):
        invalid = True
    if invalid or target is None:
        # Raise outside the handler so neither the URL nor a parser's exception
        # can be recovered through an exception context.
        raise SsrfValidationError("outbound redirect target is not permitted")
    return target


def _redirect_capability_material(location: str, target: _CanonicalOutboundUrl) -> set[str]:
    """Conventional capability echoes, not a proof against arbitrary encodings.

    Protect complete URLs/path/query and opaque query values/path segments. Short control
    values (e.g. version/format switches) are not individually capabilities;
    matching a one-character value would reject almost every downloaded body.
    Raw/resolved connection secrets are ALWAYS scanned separately, at any length.
    """
    url = _canonical_request_url(target)
    path, _, query = target.path_qs.partition("?")
    material = {location, url, target.path_qs}
    if path != "/":
        material.add(path)
    material.update(segment for segment in path.split("/") if len(segment) >= 16)
    if query:
        material.add(query)
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
            material.add(f"{key}={value}")
            if len(value) >= 16:
                material.add(value)
    material.discard("/")
    return {form for item in material for form in (item, urllib.parse.unquote(item)) if form}


def _redirect_auth_material(headers: dict[str, str]) -> list[str]:
    """Keep every generated authenticator, including individual OAuth signatures."""
    material: list[str] = []
    for value in headers.values():
        material.append(value)
        if " " in value:
            material.append(value.split(" ", 1)[1])
        if value.startswith("OAuth "):
            match = re.search(r'(?:^|,\s*)oauth_signature="([^"]+)"', value[6:])
            if match:
                material.extend([match.group(1), urllib.parse.unquote(match.group(1))])
    return material


def _execute_pinned_https_request(
    *,
    method: str,
    canonical: _CanonicalOutboundUrl,
    pinned_address: str,
    headers: dict[str, str],
    body: bytes | None,
    ssl_context: Any,
    open_socket: Callable[..., socket.socket],
    timeout: float,
    max_total_seconds: float,
    max_body_bytes: int,
    max_header_count: int,
    max_header_bytes: int,
    absolute_deadline: float | None = None,
    hop_metadata: _HttpHopMetadata | None = None,
    on_connect: Callable[[Any], None] | None = None,
    checkpoint: Callable[[], None] | None = None,
    body_idle_timeout: float | None = None,
) -> dict[str, Any]:
    """Fire ONE request: no ambient proxies, no redirects, bounded response.

    With ``body_idle_timeout``, a response whose headers arrived is read with
    that per-read window instead of ``timeout``, and a body that STOPS arriving
    is returned as far as it got with ``"stalled": True`` -- the partial reply
    is the owner's work and is not thrown away. The total deadline still ends a
    drip, and still raises.
    """
    deadline = (
        time.monotonic() + max_total_seconds
        if absolute_deadline is None else absolute_deadline
    )
    remaining = (
        max_total_seconds if absolute_deadline is None else _remaining_redirect_seconds(deadline)
    )
    # A streamed reply's long total starts only once its headers are in: until
    # then the ordinary inference budget is the deadline, so a header drip
    # cannot hold the worker for the stream's hours (Codex, 2026-10-02).
    stream_deadline = None
    if body_idle_timeout is not None and absolute_deadline is None:
        stream_deadline = deadline
        deadline = min(deadline, time.monotonic() + timeout)
        remaining = max(deadline - time.monotonic(), 0.001)
    url = _canonical_request_url(canonical)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)

    opener = urllib.request.OpenerDirector()
    # Ambient/env proxies disabled (D3.5): an explicit empty proxy map, and the
    # opener is built by hand so no default env-proxy handler is ever installed.
    opener.add_handler(urllib.request.ProxyHandler({}))
    # No HTTPRedirectHandler and no HTTPErrorProcessor are added, so a 3xx is
    # returned as-is (never auto-followed) and non-2xx does not raise (D3.4).
    sockets: list[_DeadlineSocket] = []
    opener.add_handler(
        _PinnedHTTPSHandler(
            context=ssl_context,
            pinned_address=pinned_address,
            open_socket=open_socket,
            deadline=deadline,
            on_connect=on_connect,
            checkpoint=checkpoint,
            sockets=sockets,
        )
    )

    response = None
    deadline_exceeded = False
    try:
        # Cap connect + header phase at the smaller of the per-op timeout and the
        # remaining total budget; the socket-layer deadline (_DeadlineSocket)
        # additionally bounds the status-line + header parse against the total.
        response = opener.open(request, timeout=min(timeout, remaining))
    except _TotalDeadlineExceeded:
        deadline_exceeded = True
    except (SsrfValidationError, GrantResolutionError, BrokerStreamStop):
        raise
    except Exception as exc:
        # A deadline breach during the status-line/header parse surfaces as a
        # urllib-wrapped TimeoutError (opener.open does not re-wrap getresponse),
        # so the bare _TotalDeadlineExceeded above misses it — recognize it here
        # so every phase's breach is labeled as the deadline, not a generic fail.
        if _looks_like_deadline_breach(exc, deadline):
            deadline_exceeded = True
        else:
            response = None
    if deadline_exceeded:
        # Raised OUTSIDE the except block: a fixed, secret-free message with a
        # clean __context__.
        raise OutboundDeadlineExceeded("outbound request exceeded the total deadline")
    if response is None:
        # Raised OUTSIDE the except block on purpose: `raise ... from None` still
        # leaves ``__context__`` populated (readable via ``exc.__context__`` — the
        # Authorization-echo leak class, where a URLError/BadStatusLine can quote
        # the reflected Authorization header). Raising here clears it.
        raise ProxyRequestError("outbound request failed at destination")

    # Collect the sanitized result (or a bound-violation reason) inside the
    # try, but raise only AFTER the block so no server-controlled exception can
    # ride out on ``__context__``.
    sanitized: dict[str, Any] | None = None
    bound_violation: str | None = None
    read_deadline_exceeded = False
    if body_idle_timeout is not None:
        if stream_deadline is not None:
            deadline = stream_deadline
        for wrapped in sockets:
            wrapped.set_deadline(deadline)
            wrapped.set_per_op_timeout(body_idle_timeout)
    received: list[bytes] = []
    try:
        status = int(response.status)
        reason = str(getattr(response, "reason", "") or "")
        raw_headers = list(response.getheaders())
        if len(raw_headers) > max_header_count:
            bound_violation = "outbound response has too many headers"
        elif sum(len(str(k)) + len(str(v)) for k, v in raw_headers) > max_header_bytes:
            bound_violation = "outbound response headers exceed the bound"
        else:
            declared = response.getheader("Content-Length")
            declared_ok = True
            if declared is not None:
                try:
                    declared_ok = int(declared) <= max_body_bytes
                except (TypeError, ValueError):
                    declared_ok = True
            if not declared_ok:
                bound_violation = "outbound response exceeds the size bound"
            else:
                body_bytes = _read_capped_body(response, max_body_bytes, received)
                if body_bytes is None:
                    bound_violation = "outbound response exceeds the size bound"
                else:
                    if hop_metadata is not None:
                        # Preserve multiplicity BEFORE the legacy dictionary
                        # projection discards duplicate header names. Bounds
                        # above apply to all raw headers and the decoded body.
                        hop_metadata.locations = tuple(
                            str(value) for name, value in raw_headers
                            if str(name).lower() == "location"
                        )
                        hop_metadata.body_bytes = len(body_bytes)
                    sanitized = {
                        "status": status,
                        "reason": reason,
                        "headers": {
                            str(name).lower(): str(value)
                            for name, value in raw_headers
                        },
                        "body": body_bytes.decode("utf-8", errors="replace"),
                    }
    except BrokerStreamStop:
        raise
    except _TotalDeadlineExceeded:
        read_deadline_exceeded = True
    except Exception as exc:
        # A deadline breach during the body / chunked-trailer read can also surface
        # as a TimeoutError from the tightened socket timeout — label it as the
        # deadline (fail-closed) rather than a generic destination failure, exactly
        # as the header-phase handler does.
        if (
            # The socket knows which bound armed the read that timed out; a
            # clock comparison here raced the deadline it was meant to exclude.
            body_idle_timeout is not None and _is_timeout(exc) and bound_violation is None
            and sockets and not any(wrapped.bound_by_total for wrapped in sockets)
        ):
            # Inactivity, not the total: the stream stopped arriving. What did
            # arrive is returned, marked, for the caller to keep.
            sanitized = {
                "status": int(response.status),
                "reason": str(getattr(response, "reason", "") or ""),
                "headers": {
                    str(name).lower(): str(value) for name, value in response.getheaders()
                },
                "body": b"".join(received).decode("utf-8", errors="replace"),
                "stalled": True,
            }
        elif _looks_like_deadline_breach(exc, deadline):
            read_deadline_exceeded = True
        else:
            sanitized = None
            bound_violation = None
    finally:
        try:
            response.close()
        except Exception:
            pass

    if read_deadline_exceeded:
        raise OutboundDeadlineExceeded("outbound request exceeded the total deadline")
    if bound_violation is not None:
        raise SsrfValidationError(bound_violation)
    if sanitized is None:
        raise ProxyRequestError("outbound request failed at destination")
    if absolute_deadline is not None:
        _remaining_redirect_seconds(deadline)
    return sanitized


def _validated_request_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise SsrfValidationError("request headers must be a mapping")
    validated: dict[str, str] = {}
    for name, value in headers.items():
        key = str(name).strip()
        _reject_forbidden_header_name(key)
        text = str(value)
        if _SSRF_FORBIDDEN_HEADER_CHARS.search(key) or _SSRF_FORBIDDEN_HEADER_CHARS.search(text):
            raise SsrfValidationError("request header contains forbidden characters")
        validated[key] = text
    return validated


def _encode_request_body(body: Any, headers: dict[str, str]) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (dict, list)):
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(body, separators=(",", ":")).encode("utf-8")
    raise SsrfValidationError("request body type is not permitted")


def _declassify_response(result: dict[str, Any], secrets: tuple[str, ...]) -> None:
    """Scan the WHOLE returned object for EVERY sensitive string (D4).

    ``secrets`` covers each raw bundle member AND the exact auth material placed
    on the wire — a ``Basic`` connection sends ``base64(user:pass)``, which no
    raw member matches, so an adversarial destination could echo that blob and a
    caller could reverse it (Codex-found). Scanning the wire value closes that.
    NOTE: substring scanning is a best-effort declassification net, not a
    complete confidentiality boundary against a fully adversarial destination
    that transforms the secret before echoing it — the per-connection endpoint
    allowlist (a later slice) is the real boundary that keeps traffic to trusted
    origins.
    """
    for secret in secrets:
        if secret and _contains_secret(result, secret):
            raise ProxyRequestError(
                "outbound request failed: unsafe destination response"
            )


def _default_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    # ssl.create_default_context() enables TLS pre-master-secret key logging when
    # SSLKEYLOGFILE is in the environment. A reader of that file could decrypt
    # the outbound TLS stream and recover the injected Authorization header —
    # defeating credential-blindness. Force it off regardless of the ambient env
    # (Codex-found). The broker child ALSO drops SSLKEYLOGFILE from its env so a
    # later-created context cannot re-enable it.
    context.keylog_filename = None
    return context


@dataclass(repr=False)
class _PreparedRequest:
    """One request, validated and authenticated, not yet sent. Never returned."""

    verb: str
    canonical: _CanonicalOutboundUrl
    headers: dict[str, str]
    auth_headers: dict[str, str]
    sensitive: list[str]
    body: bytes | None
    redirect_enabled: bool


class UpstreamStream:
    """One streamed upstream response (I14). Status, reason and headers are set
    and already scanned when it is returned; the body is read with :meth:`read`.

    It carries no credential. ``sensitive`` is the driver's own set of values to
    keep out of the body (raw bundle members and the exact auth material it put
    on the wire); the broker adds its own and scans every byte before release.
    Every error :meth:`read` raises is one of the module's fixed, secret-free
    classes, raised outside any handler so no exception context carries a
    destination's words.
    """

    __slots__ = ("_closed", "_done", "_lifetime", "_max_body", "_queued", "_read_bytes",
                 "_reading", "_response", "_deadline", "headers", "reason",
                 "redirect_count", "sensitive", "status")

    def __init__(self, *, status: int, reason: str, headers: dict[str, str],
                 sensitive: tuple[str, ...], response: Any = None, max_body_bytes: int = 0,
                 deadline: float = 0.0, queued: bytes = b"", redirect_count: int = 0,
                 sock: Any = None) -> None:
        self.status = status
        self.reason = reason
        self.headers = headers
        self.sensitive = sensitive
        self.redirect_count = redirect_count
        self._response = response
        self._max_body = max_body_bytes
        self._deadline = deadline
        self._read_bytes = 0
        self._queued = queued
        self._done = response is None
        self._closed = False
        self._reading = False
        del sock  # accepted for callers that pass it; nothing acts on it any more
        self._lifetime = threading.Lock()

    @classmethod
    def complete(cls, result: dict[str, Any], sensitive: tuple[str, ...]) -> UpstreamStream:
        """A collected response (already scanned whole) presented as a stream."""
        body = result.get("body")
        payload = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        return cls(status=int(result["status"]), reason=str(result.get("reason", "")),
                   headers=dict(result.get("headers") or {}), sensitive=sensitive,
                   queued=payload, redirect_count=int(result.get("redirect_count", 0) or 0))

    @property
    def done(self) -> bool:
        return self._done and not self._queued

    def read(self, max_bytes: int) -> bytes:
        """Up to ``max_bytes`` of body; ``b""`` only at the end of the body."""
        if self._closed:
            raise ProxyRequestError("outbound stream is closed")
        if max_bytes < 1:
            raise ValueError("read at least one byte")
        if self._queued:
            piece, self._queued = self._queued[:max_bytes], self._queued[max_bytes:]
            return piece
        if self._done:
            return b""
        failure: BaseException | None = None
        piece = b""
        self._reading = True
        try:
            piece = self._response.read1(min(max_bytes, _SSRF_READ_CHUNK))
        except BrokerStreamStop as exc:
            failure = exc
        except _TotalDeadlineExceeded:
            failure = OutboundDeadlineExceeded("outbound request exceeded the total deadline")
        except Exception as exc:
            if _looks_like_deadline_breach(exc, self._deadline):
                failure = OutboundDeadlineExceeded(
                    "outbound request exceeded its time budget")
            else:
                failure = ProxyRequestError("outbound request failed at destination")
        finally:
            self._reading = False
        if self._closed:
            self._release()  # closed from another thread while this read ran
            raise ProxyRequestError("outbound stream is closed")
        if failure is not None:
            self.close()
            raise failure
        if not piece:
            self._done = True
            self._release()
            return b""
        self._read_bytes += len(piece)
        if self._read_bytes > self._max_body:
            self.close()
            raise SsrfValidationError("outbound response exceeds the size bound")
        return piece

    def _release(self) -> None:
        with self._lifetime:
            response, self._response = self._response, None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Stop the stream without blocking. Safe from any thread, any number of times."""
        self._closed = True
        self._queued = b""
        self._done = True
        if not self._reading:
            self._release()
        # A read in progress is NOT interrupted: it returns within its bound
        # (the idle timeout, under the total deadline) and then releases the
        # response itself. Acting on the socket from another thread raced the
        # descriptor's lifetime (a reused descriptor could be shut down).


def _open_pinned_https_stream(
    *,
    method: str,
    canonical: _CanonicalOutboundUrl,
    pinned_address: str,
    headers: dict[str, str],
    body: bytes | None,
    ssl_context: Any,
    open_socket: Callable[..., socket.socket],
    timeout: float,
    max_total_seconds: float,
    max_body_bytes: int,
    max_header_count: int,
    max_header_bytes: int,
    sensitive: tuple[str, ...],
    on_connect: Callable[[Any], None] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> UpstreamStream:
    """``_execute_pinned_https_request`` up to the response headers, then a stream.

    The same opener (no ambient proxies, no redirects, a pinned and peer-checked
    socket under the total deadline), the same header bounds, and the same scan
    of everything returned so far (status line reason and headers) before the
    stream exists. ``timeout`` is the per-read bound: silence longer than it ends
    the stream as a deadline.
    """
    deadline = time.monotonic() + max_total_seconds
    url = _canonical_request_url(canonical)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.ProxyHandler({}))
    connected: list[Any] = []

    def remember(sock: Any) -> None:
        connected.append(sock)
        if on_connect is not None:
            on_connect(sock)

    opener.add_handler(
        _PinnedHTTPSHandler(
            context=ssl_context,
            pinned_address=pinned_address,
            open_socket=open_socket,
            deadline=deadline,
            on_connect=remember,
            checkpoint=checkpoint,
        )
    )
    response = None
    deadline_exceeded = False
    try:
        response = opener.open(request, timeout=min(timeout, max_total_seconds))
    except _TotalDeadlineExceeded:
        deadline_exceeded = True
    except (SsrfValidationError, GrantResolutionError, BrokerStreamStop):
        raise
    except Exception as exc:
        if _looks_like_deadline_breach(exc, deadline):
            deadline_exceeded = True
        else:
            response = None
    if deadline_exceeded:
        raise OutboundDeadlineExceeded("outbound request exceeded the total deadline")
    if response is None:
        raise ProxyRequestError("outbound request failed at destination")
    violation: str | None = None
    stream: UpstreamStream | None = None
    try:
        status = int(response.status)
        reason = str(getattr(response, "reason", "") or "")
        raw_headers = list(response.getheaders())
        declared = response.getheader("Content-Length")
        if len(raw_headers) > max_header_count:
            violation = "outbound response has too many headers"
        elif sum(len(str(k)) + len(str(v)) for k, v in raw_headers) > max_header_bytes:
            violation = "outbound response headers exceed the bound"
        elif declared is not None and declared.strip().isdigit() \
                and int(declared) > max_body_bytes:
            violation = "outbound response exceeds the size bound"
        else:
            stream = UpstreamStream(
                status=status, reason=reason,
                headers={str(name).lower(): str(value) for name, value in raw_headers},
                sensitive=sensitive, response=response, max_body_bytes=max_body_bytes,
                deadline=deadline, sock=connected[-1] if connected else None,
            )
    except BrokerStreamStop:
        raise
    except Exception:
        stream = None
    if stream is None:
        try:
            response.close()
        except Exception:
            pass
        if violation is not None:
            raise SsrfValidationError(violation)
        raise ProxyRequestError("outbound request failed at destination")
    try:
        _declassify_response(
            {"status": stream.status, "reason": stream.reason, "headers": stream.headers},
            sensitive,
        )
    except ProxyRequestError:
        stream.close()
        raise
    return stream


class _SsrfHardenedHttpDriver:
    """Credential-blind general HTTP driver (design.md D3/D4/D5).

    Applies the auth scheme from the injected typed bundle, performs ONE
    SSRF-hardened https call, and declassifies the response against every bundle
    member before returning a sanitized ``{status, reason, headers, body}``. It
    never returns the request's own auth material and raises only secret-free
    errors (with ``from None`` so no exception ``__context__`` can carry a
    secret). The resolver/validator/socket/context seams default to the secure
    production implementations; tests inject controlled ones.
    """

    __slots__ = (
        "_allowed_ports",
        "_max_body_bytes",
        "_max_header_bytes",
        "_max_header_count",
        "_max_total_seconds",
        "_open_socket",
        "_resolver",
        "_resolver_base",
        "_dns_timeout",
        "_ssl_context",
        "_timeout",
        "_validator",
    )

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], list[str]] | None = None,
        validator: Callable[[str], str] | None = None,
        open_socket: Callable[..., socket.socket] | None = None,
        ssl_context: Any = None,
        allowed_ports: frozenset[int] = _SSRF_DEFAULT_PORTS,
        timeout: float = _SSRF_TIMEOUT_SECONDS,
        max_total_seconds: float = _SSRF_MAX_TOTAL_SECONDS,
        max_body_bytes: int = _SSRF_MAX_BODY_BYTES,
        max_header_count: int = _SSRF_MAX_HEADER_COUNT,
        max_header_bytes: int = _SSRF_MAX_HEADER_BYTES,
        dns_timeout: float = _SSRF_DNS_TIMEOUT_SECONDS,
    ) -> None:
        # An injected resolver is used verbatim (tests drive controlled ones);
        # the production default wraps getaddrinfo in the threaded deadline so a
        # hanging resolver is abandoned instead of escaping the budget.
        self._resolver = resolver or _make_default_resolver(dns_timeout)
        self._resolver_base = resolver or _default_dns_resolver
        self._dns_timeout = float(dns_timeout)
        self._validator = validator or _classify_global_address
        self._open_socket = open_socket or _default_open_socket
        self._ssl_context = ssl_context if ssl_context is not None else _default_ssl_context()
        self._allowed_ports = frozenset(allowed_ports)
        self._timeout = float(timeout)
        self._max_total_seconds = float(max_total_seconds)
        self._max_body_bytes = int(max_body_bytes)
        self._max_header_count = int(max_header_count)
        self._max_header_bytes = int(max_header_bytes)

    def _redirect_address(self, canonical: _CanonicalOutboundUrl, deadline: float) -> str:
        """Resolve within the SAME chain deadline, including initial DNS.

        A stuck resolver is abandoned inside the existing broker child. This
        does not claim to cancel a native resolver thread instantly.
        """
        remaining = _remaining_redirect_seconds(deadline)
        if canonical.is_ip_literal:
            pinned = self._validator(canonical.hostname)
        else:
            def resolve(host: str, port: int) -> list[str]:
                return _threaded_dns_resolve(
                    host, port, base_resolver=self._resolver_base,
                    timeout=min(self._dns_timeout, remaining),
                )

            pinned = _resolve_pinned_addresses(
                canonical.hostname, canonical.port, resolver=resolve, validator=self._validator,
            )[0]
        _remaining_redirect_seconds(deadline)
        return pinned

    def __call__(
        self,
        *,
        bundle: ConnectionSecretBundle,
        auth_scheme: str,
        method: str,
        url: str,
        headers: Any = None,
        body: Any = None,
        header_name: str = "",
        allowed_endpoints: tuple[OutboundEndpoint, ...] | None = None,
        access_mode: str = ACCESS_EXACT,
        revalidate_authority: Callable[[float], None] | None = None,
        reply_budget_s: float | None = None,
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        reply_stream: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare(
            bundle=bundle, auth_scheme=auth_scheme, method=method, url=url,
            headers=headers, body=body, header_name=header_name,
            allowed_endpoints=allowed_endpoints, access_mode=access_mode,
        )
        if prepared.redirect_enabled:
            if revalidate_authority is None:
                raise GrantResolutionError("redirects require current connection authority")
            return self._redirect_chain(
                canonical=prepared.canonical, bundle=bundle, auth_scheme=auth_scheme,
                header_name=header_name, initial_headers=prepared.headers,
                initial_auth=prepared.auth_headers,
                sensitive=prepared.sensitive, allowed_endpoints=allowed_endpoints or (),
                access_mode=access_mode, revalidate_authority=revalidate_authority,
                on_connect=on_connect, checkpoint=checkpoint,
            )
        result = _execute_pinned_https_request(
            method=prepared.verb,
            canonical=prepared.canonical,
            pinned_address=self._pin(prepared.canonical),
            headers=prepared.headers,
            body=prepared.body,
            ssl_context=self._ssl_context,
            open_socket=self._open_socket,
            # The broker decided ``reply_budget_s`` (and only for inference);
            # the redirect chain above never takes it. A streamed reply keeps
            # that budget for its headers, then waits per read, not in total.
            timeout=self._timeout if reply_budget_s is None else reply_budget_s,
            max_total_seconds=(
                self._max_total_seconds if reply_budget_s is None
                else reply_budget_s if reply_stream is None else reply_stream[1]
            ),
            body_idle_timeout=None if reply_stream is None else reply_stream[0],
            # Event framing costs ~150-250 bytes per token, so a long streamed
            # reply outgrows the ordinary cap; the larger one still fits one
            # proxy frame once JSON-escaped.
            max_body_bytes=(
                self._max_body_bytes if reply_stream is None
                else max(self._max_body_bytes, INFERENCE_STREAM_MAX_BODY_BYTES)
            ),
            max_header_count=self._max_header_count,
            max_header_bytes=self._max_header_bytes,
            on_connect=on_connect,
            checkpoint=checkpoint,
        )
        _declassify_response(result, tuple(prepared.sensitive))
        return result

    def open_stream(
        self,
        *,
        bundle: ConnectionSecretBundle,
        auth_scheme: str,
        method: str,
        url: str,
        headers: Any = None,
        body: Any = None,
        header_name: str = "",
        allowed_endpoints: tuple[OutboundEndpoint, ...] | None = None,
        access_mode: str = ACCESS_EXACT,
        revalidate_authority: Callable[[float], None] | None = None,
        reply_budget_s: float | None = None,
        idle_s: float | None = None,
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> UpstreamStream:
        """:meth:`__call__`, but the body is read as it arrives (I14).

        Every guarantee up to the response headers is the request/close path's
        own: the same preparation, pin, endpoint allowlist, header bounds, and
        the same scan of the status, reason and headers before anything is
        returned. The body is then read incrementally under the same total
        deadline and the same cumulative size bound; ``idle_s`` bounds silence
        between reads. A redirecting download (``public_https_get``) keeps the
        collected path and is returned as an already-complete stream: held
        whole (as request/close holds it today, within the 5 MiB bound) and in
        request/close's decoded form. Redirecting downloads are not streamed in
        this version.
        """
        prepared = self._prepare(
            bundle=bundle, auth_scheme=auth_scheme, method=method, url=url,
            headers=headers, body=body, header_name=header_name,
            allowed_endpoints=allowed_endpoints, access_mode=access_mode,
        )
        if prepared.redirect_enabled:
            result = self(
                bundle=bundle, auth_scheme=auth_scheme, method=method, url=url,
                headers=headers, body=body, header_name=header_name,
                allowed_endpoints=allowed_endpoints, access_mode=access_mode,
                revalidate_authority=revalidate_authority,
                on_connect=on_connect, checkpoint=checkpoint,
            )
            return UpstreamStream.complete(result, tuple(prepared.sensitive))
        budget = self._max_total_seconds if reply_budget_s is None else reply_budget_s
        return _open_pinned_https_stream(
            method=prepared.verb,
            canonical=prepared.canonical,
            pinned_address=self._pin(prepared.canonical),
            headers=prepared.headers,
            body=prepared.body,
            ssl_context=self._ssl_context,
            open_socket=self._open_socket,
            timeout=self._timeout if idle_s is None else min(float(idle_s), budget),
            max_total_seconds=budget,
            max_body_bytes=self._max_body_bytes,
            max_header_count=self._max_header_count,
            max_header_bytes=self._max_header_bytes,
            sensitive=tuple(prepared.sensitive),
            on_connect=on_connect,
            checkpoint=checkpoint,
        )

    def _pin(self, canonical: _CanonicalOutboundUrl) -> str:
        if canonical.is_ip_literal:
            return self._validator(canonical.hostname)
        return _resolve_pinned_addresses(
            canonical.hostname,
            canonical.port,
            resolver=self._resolver,
            validator=self._validator,
        )[0]

    def _prepare(
        self,
        *,
        bundle: ConnectionSecretBundle,
        auth_scheme: str,
        method: str,
        url: str,
        headers: Any,
        body: Any,
        header_name: str,
        allowed_endpoints: tuple[OutboundEndpoint, ...] | None,
        access_mode: str,
    ) -> _PreparedRequest:
        if not isinstance(bundle, ConnectionSecretBundle):
            raise SsrfValidationError("a typed connection secret bundle is required")
        verb = (method or "").strip().upper()
        if verb not in _SSRF_ALLOWED_METHODS:
            raise SsrfValidationError("outbound method is not permitted")
        canonical = _parse_canonical_https_url(url, allowed_ports=self._allowed_ports)
        # The per-connection endpoint allowlist is the real egress boundary
        # (design.md D3). When supplied, the concrete host/method/path must match
        # a declared endpoint BEFORE any resolution/socket. ``None`` means the
        # caller is exercising the raw transport (the driver's own adversarial
        # tests); every production call through _TrustedNetworkDriver passes a
        # non-empty allowlist, and an empty one refuses.
        matched_endpoint: OutboundEndpoint | None = None
        if allowed_endpoints is not None:
            matched_endpoint = _enforce_endpoint_allowlist(
                canonical, verb, allowed_endpoints, access_mode
            )
        # A capability URL's secret enters the path HERE and not one line
        # earlier: everything above decided egress against the placeholder form
        # (design.md D2), and everything below — DNS, the routable-address
        # check, the socket — needs the real path. The host is unchanged, so the
        # pin is unaffected. The POSITION comes from the endpoint that admitted
        # the request, never from searching the path (astra round 1, FINDING 3).
        canonical = _substitute_url_secret(
            canonical,
            auth_scheme=auth_scheme,
            bundle=bundle,
            endpoint=matched_endpoint,
            access_mode=access_mode,
        )
        request_headers = _validated_request_headers(headers)
        # oauth1a signs over the method + the exact request URL, so pass the
        # reconstructed URL (identical to the one _execute_pinned_https_request
        # sends). Other schemes ignore method/url.
        auth_headers = _ssrf_auth_headers(
            auth_scheme,
            bundle,
            header_name=header_name,
            method=verb,
            url=_canonical_request_url(canonical),
        )
        # Case-insensitive: a caller or constant header spelled differently
        # (``x-api-key`` vs ``X-Api-Key``) must neither shadow nor duplicate
        # the credential header on the wire.
        auth_names = {name.lower() for name in auth_headers}
        request_headers = {
            name: value for name, value in request_headers.items()
            if name.lower() not in auth_names
        }
        request_headers.update(auth_headers)
        # Say who we are, honestly, on every outbound call. The destinations
        # users build channels to are CDN-fronted, and an unidentified client
        # is at the mercy of a bot score it gives no input to -- one of which
        # bit once here (see OUTBOUND_USER_AGENT for what did and did not
        # reproduce). This is HTTP citizenship, not a guaranteed unblock.
        #
        # A DEFAULT, not an override: the connection's declared constant
        # headers are merged before this (`merge_constant_headers`) and win, so
        # an owner whose service wants a particular client string says so once,
        # on the connection, where it is visible in the grant. A per-CALL
        # User-Agent is refused at the effector instead of silently accepted,
        # because impersonating another client is not the platform's to do on a
        # node's say-so.
        if not any(name.lower() == "user-agent" for name in request_headers):
            request_headers["User-Agent"] = OUTBOUND_USER_AGENT
        # Everything to scrub from the response: raw bundle members AND the exact
        # auth values placed on the wire (e.g. the base64 blob of a Basic
        # credential, which matches no raw member).
        # `_redirect_auth_material` is the full set: each header value, its
        # payload after the scheme, and an OAuth signature encoded and decoded.
        sensitive = list(bundle.secret_values()) + _redirect_auth_material(auth_headers)
        encoded_body = _encode_request_body(body, request_headers)
        approved_sources = tuple(
            endpoint for endpoint in (allowed_endpoints or ())
            if endpoint.redirect_mode == "public_https_get"
        )
        redirect_enabled = False
        if verb == "GET" and body is None and approved_sources:
            try:
                _enforce_endpoint_allowlist(canonical, verb, approved_sources, ACCESS_EXACT)
                redirect_enabled = True
            except SsrfValidationError:
                pass  # Full access alone does not opt in arbitrary source paths.
        return _PreparedRequest(
            verb=verb, canonical=canonical, headers=request_headers,
            auth_headers=auth_headers, sensitive=sensitive, body=encoded_body,
            redirect_enabled=redirect_enabled,
        )

    def _redirect_chain(
        self, *, canonical: _CanonicalOutboundUrl, bundle: ConnectionSecretBundle,
        auth_scheme: str, header_name: str, initial_headers: dict[str, str],
        initial_auth: dict[str, str],
        sensitive: list[str], allowed_endpoints: tuple[OutboundEndpoint, ...],
        access_mode: str, revalidate_authority: Callable[[float], None],
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._max_total_seconds  # before INITIAL DNS
        initial_origin = (canonical.hostname, canonical.port)
        crossed_origin = False
        visited = {_canonical_request_url(canonical)}
        capabilities: set[str] = set()
        remaining_bytes = self._max_body_bytes
        headers = initial_headers
        sensitive.extend(_redirect_auth_material(initial_auth))
        count = 0

        def checked_socket(*args: Any) -> socket.socket:
            revalidate_authority(deadline)  # after DNS, immediately before each actual dial
            _remaining_redirect_seconds(deadline)
            return self._open_socket(*args)

        def connected(sock: Any) -> None:
            revalidate_authority(deadline)
            if on_connect is not None:
                on_connect(sock)

        while True:
            revalidate_authority(deadline)
            pinned = self._redirect_address(canonical, deadline)
            revalidate_authority(deadline)
            metadata = _HttpHopMetadata()
            result = _execute_pinned_https_request(
                method="GET", canonical=canonical, pinned_address=pinned,
                headers=headers, body=None, ssl_context=self._ssl_context,
                open_socket=checked_socket, timeout=self._timeout,
                max_total_seconds=self._max_total_seconds, max_body_bytes=remaining_bytes,
                max_header_count=self._max_header_count, max_header_bytes=self._max_header_bytes,
                absolute_deadline=deadline, hop_metadata=metadata,
                # Connected, not yet written: authority (and a broker's
                # cancellation and fence) re-checked once more before the request.
                on_connect=connected, checkpoint=checkpoint,
            )
            remaining_bytes -= metadata.body_bytes
            _declassify_response(result, tuple(sensitive))  # ALL raw response fields
            safe = {
                **result, "headers": {
                    key: value for key, value in result["headers"].items()
                    if key.lower() not in {
                        "location", "content-location", "cookie", "cookie2",
                        "set-cookie", "set-cookie2",
                    }
                },
            }
            if result["status"] not in {301, 302, 303, 307, 308}:
                _declassify_response(safe, tuple(capabilities))
                return {**safe, "redirect_count": count}
            if len(metadata.locations) != 1:
                raise SsrfValidationError("outbound redirect requires exactly one Location")
            if count >= 5:
                raise SsrfValidationError("outbound redirect limit exceeded")
            location = metadata.locations[0]
            target = _redirect_target(location, canonical, self._allowed_ports)
            target_url = _canonical_request_url(target)
            _declassify_response(
                {
                    "target": target_url, "decoded": urllib.parse.unquote(target_url),
                    "form_decoded": urllib.parse.unquote_plus(target_url),
                },
                tuple(sensitive),
            )
            if target_url in visited:
                raise SsrfValidationError("outbound redirect loop refused")
            capabilities.update(_redirect_capability_material(location, target))
            _declassify_response(safe, tuple(capabilities))
            visited.add(target_url)
            crossed_origin = crossed_origin or (target.hostname, target.port) != initial_origin
            headers = {"Accept": "*/*", "User-Agent": "TinyAssets-download"}
            if not crossed_origin:
                authorized = False
                try:
                    _enforce_endpoint_allowlist(target, "GET", allowed_endpoints, access_mode)
                    authorized = True
                except SsrfValidationError:
                    pass  # Same origin is not itself authorization for another path.
                if authorized:
                    auth = _ssrf_auth_headers(
                        auth_scheme, bundle, header_name=header_name, method="GET", url=target_url,
                    )
                    headers.update(auth)
                    sensitive.extend(_redirect_auth_material(auth))
            canonical = target
            count += 1


_OAUTH1A_BUNDLE_KEYS = frozenset(
    {"api_key", "api_secret", "access_token", "access_token_secret"}
)


def _looks_like_oauth1a_bundle(credential: str) -> bool:
    """True iff ``credential`` is the deposited oauth1a encoding: a JSON object
    carrying the four OAuth 1.0a keys. Used to refuse re-interpreting that bundle
    under any OTHER scheme (the row-mutation leak). Never logs or returns values."""
    text = (credential or "").lstrip()
    if not text.startswith("{"):
        return False
    try:
        values = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(values, dict) and _OAUTH1A_BUNDLE_KEYS.issubset(values.keys())


def _build_http_secret_bundle(auth_scheme: str, credential: str) -> ConnectionSecretBundle:
    """Build the typed bundle for an ``http`` connection INSIDE the child (D2/D5).

    The broker resolves ONE opaque credential string from the vault; this maps it
    to the named bundle shape the auth scheme needs. ``basic`` stores the pair as
    ``username:password`` and is split on the FIRST colon (a password may itself
    contain colons). ``bearer``/``header`` carry a single token. ``oauth1a``
    (Twitter) needs FOUR named values, so its single vault string is a JSON object
    ``{api_key, api_secret, access_token, access_token_secret}`` parsed here — see
    the SHAPE FINDING in the migration notes: a first-class multi-named-secret
    vault resolver (task 1.3) is the cleaner home for this than a JSON-in-one-slot
    encoding. The bundle never crosses the process boundary and is scrubbed from
    the response.
    """
    scheme = (auth_scheme or "none").strip().lower()
    # SCHEME <-> ENCODING BINDING (Codex ADAPT, PR #2525). The vault stores ONE
    # opaque string whose ENCODING is fixed by the scheme it was deposited under
    # (oauth1a = JSON object of the four OAuth values). This builder is the single
    # choke point every dispatch passes through, and it is handed the row's
    # CURRENT scheme — so a connection row mutated from oauth1a to bearer would
    # otherwise re-interpret the whole four-value bundle as a single token and
    # emit it verbatim as `Authorization: Bearer {json…}` to an allowlisted
    # endpoint, leaking all four secrets. Refuse, fail closed, any credential
    # whose encoding is recognisably that of a DIFFERENT scheme before a header
    # is ever built. (A mismatch can only arise from row mutation or a corrupted
    # deposit — never from a legitimate connect_http, which validates shape at
    # the door with the same rules.)
    if scheme != "oauth1a" and _looks_like_oauth1a_bundle(credential):
        raise SsrfValidationError(
            "credential encoding does not match the connection's auth scheme"
        )
    # The same binding for oauth2: its vault string holds a refresh token and
    # must never be sent as a bearer/header/basic value by a mutated row. The
    # broker hands the driver the access token alone, so an oauth2 connection
    # that still sees a bundle here was bypassed and is refused too.
    from tinyassets.connection_oauth.tokens import looks_like_bundle

    if looks_like_bundle(credential):
        raise SsrfValidationError(
            "credential encoding does not match the connection's auth scheme"
        )
    if scheme == _URL_SECRET_SCHEME:
        # An opaque path segment (or a `/`-joined run of them), carried under
        # `token` like every other single-value scheme so `_substitute_url_secret`
        # reads it the same way. Validated HERE as well as at the deposit door:
        # this builder is the one choke point every dispatch passes through, so a
        # corrupted or mutated record fails closed. The multi-segment grammar is
        # used because the builder does not know which endpoint the call will
        # address; `_substitute_url_secret` re-checks against that endpoint's
        # actual token, which is the stricter one.
        #
        # EACH SEGMENT is a bundle member too, not only the joined form: the
        # response scanners match substrings, so a destination echoing one
        # segment of a Slack-shaped `T…/B…/token` back would otherwise pass both
        # checks and land in the run record (astra round 1, FINDING 2).
        whole = validate_url_secret_value(credential, _URL_SECRET_REST_TOKEN)
        values = url_secret_sensitive_values(whole)
        return ConnectionSecretBundle(
            token=whole,
            **{
                f"url_secret_segment_{index}": part
                for index, part in enumerate(values[1:])
            },
        )
    if scheme in ("bearer", "header", "oauth2"):
        return ConnectionSecretBundle(token=credential)
    if scheme == "basic":
        if ":" not in credential:
            raise SsrfValidationError("basic credential must be username:password")
        username, password = credential.split(":", 1)
        if not username or not password:
            raise SsrfValidationError("basic credential must be username:password")
        return ConnectionSecretBundle(username=username, password=password)
    if scheme == "oauth1a":
        try:
            values = json.loads(credential)
        except (TypeError, ValueError):
            raise SsrfValidationError(
                "oauth1a credential must be a JSON object of the four OAuth values"
            ) from None
        if not isinstance(values, dict):
            raise SsrfValidationError("oauth1a credential must be a JSON object")
        required = ("api_key", "api_secret", "access_token", "access_token_secret")
        if any(not isinstance(values.get(name), str) or not values.get(name) for name in required):
            raise SsrfValidationError("oauth1a credential is missing a required value")
        return ConnectionSecretBundle(**{name: values[name] for name in required})
    if scheme == "none":
        return ConnectionSecretBundle()
    raise SsrfValidationError("auth scheme is not supported")


class _TrustedNetworkDriver:
    """Select the fixture or general http transport inside the broker child.

    Routing is EXPLICIT and fails closed (Codex FIX 1). ``connection_type=="http"``
    → the general credential-blind SSRF-hardened driver (the single
    channel-agnostic egress, behind the ``allow_http_connections`` deployment
    flag); the EMPTY legacy type routes ONLY to the gated test fixture; ANY other
    (unknown/unsupported) type is REFUSED. There is no per-channel (github/slack/…)
    transport — channels are user-built graph nodes over the generic http
    connection, never platform code.
    """

    __slots__ = ("_allow_http", "_fixture", "_http")

    def __init__(self, config: dict[str, Any], runtime_root: Path) -> None:
        self._fixture = _TestFixtureNetworkDriver(
            runtime_root,
            allow_test_fixtures=bool(config["allow_test_fixtures"]),
        )
        self._allow_http = bool(config.get("allow_http_connections", False))
        self._http = _SsrfHardenedHttpDriver()

    def __call__(self, **kwargs: Any) -> Any:
        # Pop the descriptor fields so the fixture driver keeps its exact fixed
        # signature — only the http path consumes them.
        connection_type = str(kwargs.pop("connection_type", "") or "").strip().lower()
        auth_scheme = str(kwargs.pop("auth_scheme", "") or "")
        allowed_endpoints = kwargs.pop("allowed_endpoints", ()) or ()
        access_mode = kwargs.pop("access_mode", ACCESS_EXACT)
        revalidate_authority = kwargs.pop("revalidate_authority", None)
        reply_budget_s = kwargs.pop("reply_budget_s", None)
        stream = bool(kwargs.pop("stream", False))
        idle_s = kwargs.pop("idle_s", None)
        on_connect = kwargs.pop("on_connect", None)
        checkpoint = kwargs.pop("checkpoint", None)
        reply_stream = kwargs.pop("reply_stream", None)
        if connection_type == "http":
            return self._dispatch_http(
                stream=stream,
                idle_s=idle_s,
                on_connect=on_connect,
                checkpoint=checkpoint,
                auth_scheme=auth_scheme,
                allowed_endpoints=tuple(allowed_endpoints),
                access_mode=access_mode,
                credential=kwargs.get("credential", ""),
                verb=str(kwargs.get("verb", "")),
                request=kwargs.get("request"),
                revalidate_authority=revalidate_authority,
                reply_budget_s=reply_budget_s,
                reply_stream=reply_stream,
            )
        if connection_type == "":
            # Legacy untyped connections route ONLY to the gated test fixture —
            # never to any real network destination.
            provider = str(kwargs.get("provider", ""))
            if provider.startswith("test-fixture."):
                result = self._fixture(**kwargs)
                if stream:
                    return UpstreamStream.complete(result, ())
                return result
            raise ProxyRequestError("outbound provider has no trusted transport")
        # Unknown / unsupported connection_type: FAIL CLOSED.
        raise ProxyRequestError("outbound connection type is not supported")

    def _dispatch_http(
        self,
        *,
        auth_scheme: str,
        allowed_endpoints: tuple[OutboundEndpoint, ...],
        credential: str,
        verb: str,
        request: object,
        access_mode: str = ACCESS_EXACT,
        revalidate_authority: Callable[[float], None] | None = None,
        reply_budget_s: float | None = None,
        stream: bool = False,
        idle_s: float | None = None,
        on_connect: Callable[[Any], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        reply_stream: tuple[float, float] | None = None,
    ) -> Any:
        if not self._allow_http:
            # Fail closed until a deployment enables the general http path.
            raise ProxyRequestError("outbound http connections are not enabled")
        if not allowed_endpoints:
            # No allowlist ⇒ no reachable destination (the real egress boundary).
            raise SsrfValidationError("connection has no permitted endpoints")
        if not isinstance(request, dict):
            raise SsrfValidationError("outbound http request shape is not permitted")
        # The capability-URL binding, re-checked against the row as the broker
        # RE-READ it (`dispatch` reads the resource fresh on every call), not the
        # one frozen when the proxy opened. That is the TOCTOU closure: a row
        # mutated from `url_secret` to `bearer` would otherwise send the
        # placeholder literally in the path AND the secret segment in an
        # Authorization header. Refused before a bundle exists. The access mode
        # rides along: a row mutated to `full` admits every path on the host,
        # which is no place for a path-borne credential.
        validate_url_secret_binding(
            auth_scheme, allowed_endpoints, access_mode=access_mode
        )
        bundle = _build_http_secret_bundle(auth_scheme, credential)
        send = self._http.open_stream if stream else self._http
        extra: dict[str, Any] = {}
        if on_connect is not None:
            extra["on_connect"] = on_connect
        if checkpoint is not None:
            extra["checkpoint"] = checkpoint
        if stream:
            if idle_s is not None:
                extra["idle_s"] = idle_s
        elif reply_stream is not None:
            extra["reply_stream"] = reply_stream
        return send(
            **extra,
            bundle=bundle,
            auth_scheme=auth_scheme,
            method=str(verb),
            url=request.get("url"),
            headers=request.get("headers"),
            body=request.get("body"),
            header_name=str(request.get("header_name", "") or ""),
            allowed_endpoints=allowed_endpoints,
            access_mode=access_mode,
            **({"revalidate_authority": revalidate_authority} if revalidate_authority else {}),
            **({"reply_budget_s": reply_budget_s} if reply_budget_s is not None else {}),
        )


class _JsonlAuditWriter:
    __slots__ = ("_path",)

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def __call__(self, record: dict[str, object]) -> None:
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def _build_credential_broker_dispatch(
    config: dict[str, Any],
) -> Callable[[str, str, object], Any]:
    runtime_root = Path(config["runtime_root"])
    runtime_root.mkdir(parents=True, exist_ok=True)
    from tinyassets.connection_oauth.tokens import ConnectionTokens
    from tinyassets.storage.agent_request_usage import resolve_inference_usage

    ledger = ConnectionLedger(config["ledger_db_path"])
    universe = Path(config["universe_dir"])

    def accounting(resource, grant_id, verb, request, envelope, operation_id):
        return resolve_inference_usage(
            Path(config["ledger_db_path"]).parent, config["owner_user_id"], universe.name,
            ledger, resource, grant_id, verb, request, envelope, operation_id,
        )

    broker = CredentialBlindBroker(
        ledger,
        resolve_inference_usage=accounting,
        resolve_credential=_TrustedCredentialResolver(config),
        network_request=_TrustedNetworkDriver(config, runtime_root),
        audit=_JsonlAuditWriter(str(runtime_root / "audit.jsonl")),
        oauth_tokens=ConnectionTokens(
            universe_dir=config["universe_dir"],
            owner_user_id=config["owner_user_id"],
            oauth_service=config.get("oauth_service"),
        ),
    )
    return broker.dispatch


_TRUSTED_DISPATCH_FACTORIES = {
    "credential_broker_v1": _build_credential_broker_dispatch,
}


def _streamed_text(response: object) -> str:
    """Every string a streamed reply's deltas carry, joined in arrival order.

    An event stream hands a reply over in pieces, and the caller rejoins them;
    a credential split across two deltas passes a substring scan of the raw
    body and reappears whole once rejoined (Codex, 2026-10-02). Scanning the
    rejoined text closes that. Best effort, like the scan it extends.
    """
    body = response.get("body") if isinstance(response, dict) else None
    if not isinstance(body, str) or "data:" not in body:
        return ""
    pieces: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except ValueError:
            continue
        for choice in (chunk.get("choices") or []) if isinstance(chunk, dict) else ():
            if isinstance(choice, dict):
                collect(choice.get("delta"))
    return "".join(pieces)


def _contains_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, bytes):
        return secret.encode("utf-8") in value
    if isinstance(value, dict):
        return any(
            _contains_secret(key, secret) or _contains_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_secret(item, secret) for item in value)
    return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound_connections (
    connection_id   TEXT PRIMARY KEY,
    owner_user_id   TEXT NOT NULL,
    connection_class TEXT NOT NULL,
    scopes_json     TEXT NOT NULL,
    provider        TEXT NOT NULL,
    destination     TEXT NOT NULL,
    credential_ref  TEXT NOT NULL,
    revoked_at      REAL,
    connection_type TEXT NOT NULL DEFAULT '',
    auth_scheme     TEXT NOT NULL DEFAULT '',
    allowed_endpoints_json TEXT NOT NULL DEFAULT '[]',
    access_mode     TEXT NOT NULL DEFAULT 'exact',
    -- The owner-declared git host; '' means "the connection's endpoint host".
    git_host        TEXT NOT NULL DEFAULT '',
    -- Minted fresh on every deposit. The connection id and the credential_ref
    -- are both deterministic per (universe, destination), so without this a
    -- key removed and REPLACED under the same destination with an identical
    -- policy matched every compare-and-swap predicate, and a stale full answer
    -- applied the owner's decision to a key they never granted it for (Codex
    -- code review round 3, P0).
    incarnation     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS outbound_connection_grants (
    grant_id        TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL REFERENCES outbound_connections(connection_id),
    owner_user_id   TEXT NOT NULL,
    universe_id     TEXT NOT NULL,
    granted_at      REAL NOT NULL,
    revoked_at      REAL,
    unprompted_action_cap_json TEXT
);

CREATE TABLE IF NOT EXISTS connection_capabilities (
    connection_id   TEXT NOT NULL
        REFERENCES outbound_connections(connection_id) ON DELETE CASCADE,
    capability_kind TEXT NOT NULL,
    descriptor_json TEXT NOT NULL,
    configured_at   REAL NOT NULL,
    PRIMARY KEY (connection_id, capability_kind)
);

CREATE INDEX IF NOT EXISTS idx_outbound_grant_resolution
    ON outbound_connection_grants(owner_user_id, universe_id, revoked_at);

CREATE TABLE IF NOT EXISTS outbound_connector_artifacts (
    artifact_id              TEXT PRIMARY KEY,
    owner_user_id            TEXT NOT NULL,
    connector_definition_json TEXT NOT NULL,
    mcp_client_config_json   TEXT NOT NULL,
    created_at               REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_connector_artifact_edges (
    parent_artifact_id TEXT NOT NULL
        REFERENCES outbound_connector_artifacts(artifact_id),
    child_artifact_id  TEXT NOT NULL UNIQUE
        REFERENCES outbound_connector_artifacts(artifact_id),
    remixed_by_user_id TEXT NOT NULL,
    created_at         REAL NOT NULL,
    PRIMARY KEY (parent_artifact_id, child_artifact_id),
    CHECK (parent_artifact_id <> child_artifact_id)
);
"""


def _required(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _resource_from_row(row: sqlite3.Row) -> ConnectionResource:
    """Build a ``ConnectionResource`` from an ``outbound_connections`` row.

    The descriptor columns are read through ``.keys()`` guards so a row selected
    before the ALTER-migration ran (should not happen — every ledger __init__
    backfills them — but defensive) reads back as a legacy connection.
    """
    columns = set(row.keys())
    endpoints_raw = row["allowed_endpoints_json"] if "allowed_endpoints_json" in columns else "[]"
    return ConnectionResource(
        connection_id=row["connection_id"],
        owner_user_id=row["owner_user_id"],
        connection_class=row["connection_class"],
        scopes=tuple(json.loads(row["scopes_json"])),
        provider=row["provider"],
        destination=row["destination"],
        credential_ref=row["credential_ref"],
        revoked_at=row["revoked_at"],
        connection_type=(row["connection_type"] if "connection_type" in columns else "") or "",
        auth_scheme=(row["auth_scheme"] if "auth_scheme" in columns else "") or "",
        allowed_endpoints=_parse_allowed_endpoints(endpoints_raw),
        access_mode=normalize_access_mode(
            row["access_mode"] if "access_mode" in columns else ""
        ),
        git_host=(row["git_host"] if "git_host" in columns else "") or "",
    )


def _json_object(name: str, value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    _reject_secret_material(value)
    return json.loads(json.dumps(value, sort_keys=True))


def _reject_secret_material(value: object) -> None:
    secret_keys = {
        "api_key",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in secret_keys:
                raise ValueError("connector artifacts cannot contain credential material")
            _reject_secret_material(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_material(item)


class ConnectionLedger:
    """SQLite ledger for user-owned connections and universe grants."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        allow_test_fixtures: bool = False,
        verify_authenticated_principal: AuthenticatedPrincipalVerifier | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._allow_test_fixtures = allow_test_fixtures
        self._verify_authenticated_principal = verify_authenticated_principal
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            grant_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(outbound_connection_grants)"
                )
            }
            if "unprompted_action_cap_json" not in grant_columns:
                connection.execute(
                    "ALTER TABLE outbound_connection_grants "
                    "ADD COLUMN unprompted_action_cap_json TEXT"
                )
            # Clear the legacy per-connection "http_requests" cap. Accounts are
            # limited only by total storage and simultaneous agent runs, so a
            # fixed request cap on an HTTP channel is a leftover of the old
            # account model. Other caps (e.g. one_pull_request) are untouched.
            legacy_http_cap_predicate = (
                "unprompted_action_cap_json IS NOT NULL "
                "AND json_valid(unprompted_action_cap_json) "
                "AND json_extract(unprompted_action_cap_json, '$.name') "
                "= 'http_requests'"
            )
            # Even an empty UPDATE takes a writer lock. Initialized opens must
            # remain read-only, including while another connection is writing.
            if connection.execute(
                "SELECT 1 FROM outbound_connection_grants WHERE "
                + legacy_http_cap_predicate + " LIMIT 1"
            ).fetchone():
                connection.execute(
                    "UPDATE outbound_connection_grants "
                    "SET unprompted_action_cap_json = NULL WHERE "
                    + legacy_http_cap_predicate
                )
            # Backfill the channel descriptor columns onto pre-descriptor DBs.
            # Legacy rows read back as connection_type='' (routes to the existing
            # github/slack drivers) with an empty allowlist.
            connection_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(outbound_connections)"
                )
            }
            for column, ddl in (
                ("connection_type", "TEXT NOT NULL DEFAULT ''"),
                ("auth_scheme", "TEXT NOT NULL DEFAULT ''"),
                ("allowed_endpoints_json", "TEXT NOT NULL DEFAULT '[]'"),
                # full-channel-access D2. Every existing row is `exact`: a
                # migration must never widen an existing grant.
                ("access_mode", "TEXT NOT NULL DEFAULT 'exact'"),
                ("incarnation", "TEXT NOT NULL DEFAULT ''"),
                # Empty for every existing row: a migration never names a host
                # the owner did not.
                ("git_host", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in connection_columns:
                    connection.execute(
                        f"ALTER TABLE outbound_connections ADD COLUMN {column} {ddl}"
                    )
            # The column default also applies to rows inserted by older writers.
            # Adopt those rows without rotating any established deposit identity
            # or changing its policy. Ordinary opens must not take a write lock.
            if connection.execute(
                "SELECT 1 FROM outbound_connections WHERE incarnation = '' LIMIT 1"
            ).fetchone():
                connection.execute(
                    "UPDATE outbound_connections SET incarnation = lower(hex(randomblob(16))) "
                    "WHERE incarnation = ''"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def require_authenticated_principal_id(self) -> str:
        """Resolve the request principal through a trusted daemon verifier.

        The verifier is a required call-site capability for authority-bearing
        operations. It must be installed by the authenticated request boundary,
        must derive the current principal from server-owned context, and must
        never be constructed from universe/action payload fields.
        """
        verifier = self._verify_authenticated_principal
        if verifier is None:
            raise PermissionError(
                "authenticated principal verifier is required"
            )
        try:
            principal_id = verifier()
        except Exception:
            raise PermissionError(
                "authenticated principal verification failed"
            ) from None
        if not isinstance(principal_id, str):
            raise PermissionError("authenticated principal is required")
        from tinyassets.principals import named_principal

        principal_id = named_principal(principal_id)
        if not principal_id:
            raise PermissionError("authenticated principal is required")
        return principal_id

    def create_connection(
        self,
        *,
        connection_id: str,
        owner_user_id: str,
        connection_class: str,
        scopes: tuple[str, ...],
        provider: str,
        destination: str,
        credential_ref: str,
        connection_type: str = "",
        auth_scheme: str = "",
        allowed_endpoints: Any = (),
        access_mode: str = ACCESS_EXACT,
        git_host: str = "",
    ) -> ConnectionView:
        endpoints = _parse_allowed_endpoints(allowed_endpoints)
        declared_git_host = normalize_git_host(git_host)
        normalized_access = normalize_access_mode(access_mode)
        normalized_type = (connection_type or "").strip().lower()
        normalized_scheme = (auth_scheme or "").strip().lower()
        if normalized_type not in _KNOWN_CONNECTION_TYPES:
            # Reject unknown types at creation so a bogus type can never be stored
            # and later fall through to a hardcoded-destination driver (FIX 1).
            raise SsrfValidationError("connection_type is not supported")
        # The credential SCHEME must match the connection type — the SAME rule
        # dispatch re-checks against the current row (Codex FIX 1 + TOCTOU).
        _validate_connection_credential_scheme(normalized_type, credential_ref)
        if normalized_type == "http":
            # A general http connection is only safe with a declared allowlist
            # and a supported auth scheme (the bundle builder enforces the
            # per-scheme credential FORMAT later, inside the child).
            if not endpoints:
                raise SsrfValidationError(
                    "an http connection requires at least one allowed endpoint"
                )
            if normalized_scheme not in _SUPPORTED_HTTP_AUTH_SCHEMES:
                raise SsrfValidationError("auth scheme is not supported")
            # The capability-URL binding at the STORAGE boundary, for the same
            # reason `validate_git_scopes` lives here: every issuer assembles
            # its own payload, and a rule that lives in one of them is a rule
            # the next one forgets.
            validate_url_secret_binding(
                normalized_scheme, endpoints, access_mode=normalized_access
            )
        # A git scope binds one repository on one host, so it may only ride on a
        # connection that names exactly one git host. Checked HERE, at the storage
        # boundary: every issuer assembles its own scope tuple, and a rule that
        # lives in one of them is a rule the next one forgets.
        validate_git_scopes(
            scopes,
            hosts=[endpoint.host for endpoint in endpoints],
            git_host=declared_git_host,
        )
        resource = ConnectionResource(
            connection_id=_required("connection_id", connection_id),
            owner_user_id=_required("owner_user_id", owner_user_id),
            connection_class=_required("connection_class", connection_class),
            scopes=tuple(_required("scope", scope) for scope in scopes),
            provider=_required("provider", provider),
            destination=_required("destination", destination),
            credential_ref=_required("credential_ref", credential_ref),
            revoked_at=None,
            connection_type=normalized_type,
            auth_scheme=normalized_scheme,
            allowed_endpoints=endpoints,
            access_mode=normalized_access,
            git_host=declared_git_host,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_connections (
                    connection_id, owner_user_id, connection_class, scopes_json,
                    provider, destination, credential_ref, revoked_at,
                    connection_type, auth_scheme, allowed_endpoints_json,
                    access_mode, incarnation, git_host
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource.connection_id,
                    resource.owner_user_id,
                    resource.connection_class,
                    json.dumps(resource.scopes),
                    resource.provider,
                    resource.destination,
                    resource.credential_ref,
                    resource.connection_type,
                    resource.auth_scheme,
                    json.dumps([ep.as_dict() for ep in resource.allowed_endpoints]),
                    resource.access_mode,
                    uuid.uuid4().hex,
                    resource.git_host,
                ),
            )
        # Return the REDACTED view — no caller (not even the creator) gets
        # credential_ref back from the default read/create API (Codex FIX 3).
        return resource.to_view()

    def _upgrade_http_connection_scopes(
        self, *, connection_id: str, scopes: tuple[str, ...]
    ) -> None:
        """Bounded, one-directional migration of the legacy ("http",) scope token.

        connect_http originally stored an http connection's scope as the literal
        ("http",) type token, which the authenticated_external_call effector could
        never match against the packet HTTP verb (#2521). This rewrites such a row's
        scope to the concrete method-union. It is deliberately GUARDED by
        ``scopes_json = '["http"]'`` in the WHERE clause: it can ONLY ever replace
        the exact legacy token, so it can never silently widen, narrow, or alter a
        real method-scoped set — a row already carrying method scopes is untouched.
        """
        new_scopes = tuple(_required("scope", scope) for scope in scopes)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbound_connections
                SET scopes_json = ?
                WHERE connection_id = ? AND scopes_json = ?
                """,
                (json.dumps(list(new_scopes)), connection_id, json.dumps(["http"])),
            )

    def extend_http_connection_endpoints(
        self,
        *,
        connection_id: str,
        endpoints: Any,
        scopes: tuple[str, ...],
        expected_endpoints_json: str,
        expected_scopes_json: str,
        expected_access_mode: str | None = None,
        expected_incarnation: str | None = None,
        expected_grant_id: str | None = None,
        git_host: str = "",
    ) -> bool:
        """ADD endpoints to an existing http connection. Never remove or replace.

        A credential is deposited once and extended as the work needs it — the
        alternative was a fresh connection (and a fresh paste) per endpoint,
        because a deterministic id plus any policy difference read as a hard
        conflict.

        Two things keep this from being a widening primitive:

        * **Additive only.** The caller has already checked the new set is a
          superset; this re-checks nothing about intent but writes the union, so
          an endpoint another graph depends on cannot vanish here. Narrowing and
          removal stay unsupported (they are a different, destructive intent).
        * **CAS-guarded on BOTH columns it writes, always.** The UPDATE matches
          on the exact endpoint JSON AND the exact scopes JSON the caller read
          (both from one :meth:`policy_json` snapshot). Guarding endpoints alone
          let two scope-only widenings race: the first wrote scope B without
          touching the endpoints, so the second's CAS still matched and
          replaced B with A; an optional scopes guard let a caller skip it
          (Codex rounds 1-2 on the 2026-09-02 rail change).

        Returns True when the row was updated.
        """
        parsed = _parse_allowed_endpoints(endpoints)
        if not parsed:
            raise SsrfValidationError(
                "an http connection requires at least one allowed endpoint"
            )
        new_scopes = tuple(_required("scope", scope) for scope in scopes)
        # ``git_host`` is the STORED connection's declared host (the caller read
        # it); an extension never changes it.
        validate_git_scopes(
            new_scopes, hosts=[endpoint.host for endpoint in parsed], git_host=git_host
        )
        # The capability-URL binding as a PREDICATE, so an extension can never
        # produce a row whose scheme and endpoints disagree: a set where EVERY
        # endpoint carries the reserved placeholder may only land on a
        # `url_secret` connection, and a set where any endpoint does not may
        # only land on a connection that is not one. A prior read would be a
        # TOCTOU against a concurrent remove-and-redeposit.
        scheme_test = "=" if all(url_secret_token(ep) for ep in parsed) else "!="
        sql = f"""
                UPDATE outbound_connections
                SET allowed_endpoints_json = ?, scopes_json = ?
                WHERE connection_id = ? AND allowed_endpoints_json = ?
                  AND scopes_json = ? AND git_host = ?
                  AND auth_scheme {scheme_test} ?
        """
        # The git host the scopes were validated against is part of the CAS:
        # a remove-and-reconnect under a different git_host with identical
        # endpoints and scopes must not receive this widening.
        params: list[Any] = [
            json.dumps([ep.as_dict() for ep in parsed]), json.dumps(list(new_scopes)),
            connection_id, expected_endpoints_json, expected_scopes_json,
            normalize_git_host(git_host), _URL_SECRET_SCHEME,
        ]
        if expected_access_mode is not None or expected_incarnation is not None:
            if not expected_access_mode or not expected_incarnation:
                raise SsrfValidationError("complete connection policy snapshot is required")
            sql += " AND access_mode = ? AND incarnation = ? AND revoked_at IS NULL"
            params.extend([normalize_access_mode(expected_access_mode), expected_incarnation])
        if expected_grant_id is not None:
            sql += """ AND EXISTS (
                SELECT 1 FROM outbound_connection_grants AS g
                WHERE g.grant_id = ? AND g.connection_id = outbound_connections.connection_id
                  AND g.owner_user_id = outbound_connections.owner_user_id
                  AND g.revoked_at IS NULL
            )"""
            params.append(expected_grant_id)
        with self._connect() as connection:
            cursor = connection.execute(sql, tuple(params))
            return cursor.rowcount > 0

    def set_access_mode(
        self,
        *,
        connection_id: str,
        access_mode: str,
        expected_mode: str,
        expected_endpoints_json: str,
        expected_scopes_json: str,
        expected_incarnation: str | None = None,
    ) -> bool:
        """Move a connection between ``exact`` and ``full`` under CAS.

        Returns True when the row moved. False means the connection is not
        what the caller previewed any more, and the caller re-previews rather
        than applying a decision to a grant it never saw.

        The comparison is the WHOLE POLICY, not just the mode. Comparing the
        mode alone let a concurrent exact extension ride in: device A previews
        full on a connection declaring one host, device B adds a second host
        (mode still exact), and A's swap then makes BOTH hosts full although
        the owner only ever saw one (Codex code review round 1). It also closes
        the remove-and-redeposit ABA, because a replacement row cannot carry
        the same endpoint and scope text by accident.

        ``expected_incarnation`` closes the case where it CAN. The connection
        id and the credential reference are both deterministic per
        (universe, destination), so a key removed and a different one deposited
        with an identical policy matched every other predicate, and the owner's
        yes landed on a key they never saw (Codex code review round 3, P0).
        Passing ``None`` keeps the older comparison, for a caller that has no
        snapshot to offer.
        """
        wanted = normalize_access_mode(access_mode)
        expected = normalize_access_mode(expected_mode)
        sql = """
                UPDATE outbound_connections
                   SET access_mode = ?
                 WHERE connection_id = ?
                   AND access_mode = ?
                   AND allowed_endpoints_json = ?
                   AND scopes_json = ?
        """
        params: list[Any] = [
            wanted,
            connection_id,
            expected,
            expected_endpoints_json,
            expected_scopes_json,
        ]
        if wanted == ACCESS_FULL:
            # A capability URL cannot be a full channel: `full` admits any path
            # once the host matches, so the reserved placeholder would never be
            # enforced. In the predicate rather than a prior read, so no
            # concurrent deposit can slip between the check and the write.
            sql += "           AND auth_scheme != ?\n"
            params.append(_URL_SECRET_SCHEME)
        if expected_incarnation is not None:
            sql += "           AND incarnation = ?\n"
            params.append(expected_incarnation)
        with self._connect() as connection:
            cursor = connection.execute(sql, tuple(params))
            return cursor.rowcount > 0

    def access_mode(self, connection_id: str) -> str | None:
        """The stored mode, or ``None`` when there is no such connection."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT access_mode FROM outbound_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return normalize_access_mode(row["access_mode"])

    def policy_json(self, connection_id: str) -> tuple[str, str] | None:
        """The stored ``(allowed_endpoints_json, scopes_json)`` exactly as
        written, for a CAS-guarded extension to compare against. Reserialising
        the parsed policy happens to round-trip today; comparing the raw text
        cannot silently stop doing so."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT allowed_endpoints_json, scopes_json FROM outbound_connections "
                "WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["allowed_endpoints_json"]), str(row["scopes_json"])

    def incarnation(self, connection_id: str) -> str | None:
        """Which DEPOSIT this connection is, or ``None`` when there is none.

        The id and the credential reference are both derived from
        (universe, destination), so neither changes when a key is removed and a
        different one deposited in its place. This does.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT incarnation FROM outbound_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["incarnation"])

    def _resource_policy_snapshot(
        self, connection_id: str,
    ) -> tuple[ConnectionResource, dict[str, str]] | None:
        """Trusted resource and exact approval policy from ONE SQLite row read.

        Keep the credential-bearing resource internal; only the four policy
        fields may be projected into an owner-visible approval.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbound_connections WHERE connection_id = ?", (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return _resource_from_row(row), {
            "endpoints_json": str(row["allowed_endpoints_json"]),
            "scopes_json": str(row["scopes_json"]),
            "access_mode": normalize_access_mode(row["access_mode"]),
            "incarnation": str(row["incarnation"]),
        }

    def _get_connection_resource(
        self, connection_id: str
    ) -> ConnectionResource | None:
        """Credential-BEARING read for TRUSTED internal use only.

        Returns the full ``ConnectionResource`` including ``credential_ref``. The
        public ``get_connection`` returns a redacted :class:`ConnectionView`
        instead (Codex FIX 3); this method is the explicit, named seam the broker
        child and the internal ownership/conflict checks use when they must see
        the credential reference. Never expose its result to an adapter/graph/CRUD
        surface.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbound_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return _resource_from_row(row)

    def get_connection(self, connection_id: str) -> ConnectionView | None:
        """Redacted connection read — the default public projection (Codex FIX 3).

        Returns a :class:`ConnectionView` with NO ``credential_ref`` field — the
        redaction is structural, not a repr convention: the returned object has no
        attribute, ``vars()``, or ``asdict()`` key for the credential reference.
        Trusted internal code that needs the reference uses
        ``_get_connection_resource``.
        """
        resource = self._get_connection_resource(connection_id)
        return resource.to_view() if resource is not None else None

    def get_connection_view(self, connection_id: str) -> ConnectionView | None:
        """Explicit redacted-view accessor (same result as ``get_connection``)."""
        return self.get_connection(connection_id)

    def list_connection_views(
        self,
        *,
        owner_user_id: str,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[ConnectionView]:
        """List one owner's connections as redacted views (no credential_ref)."""
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM outbound_connections
                 WHERE owner_user_id = ?
                   AND (? = 0 OR revoked_at IS NULL)
                 ORDER BY connection_id LIMIT ?
                """,
                (_required("owner_user_id", owner_user_id), int(active_only), limit),
            ).fetchall()
        return [_resource_from_row(row).to_view() for row in rows]

    def configure_capability(
        self,
        *,
        connection_id: str,
        capability_kind: str,
        descriptor: Any = None,
        enabled: bool,
        expected_grant: ConnectionGrant | None = None,
        preview: bool = False,
    ) -> ConnectionCapability | ModelDiscoveryCapability | None:
        """Idempotently configure one bounded capability on existing authority.

        The descriptor is metadata, never authority: enabling succeeds only when
        the current connection is active HTTP authority whose existing scope and
        endpoint allowlist already admit the capability's exact destinations.
        Discovery additionally fences the handler's live grant context in this
        transaction. Voice retains its existing current-serving API contract.
        """

        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        connection_key = _required("connection_id", connection_id)
        kind = _validate_capability_kind(capability_kind)
        if type(preview) is not bool or (preview and (not enabled or kind != "model_discovery")):
            raise ValueError("preview requires enabled model discovery metadata")
        capability = (
            _validate_connection_capability(connection_key, kind, descriptor)
            if enabled
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM outbound_connections WHERE connection_id = ?",
                (connection_key,),
            ).fetchone()
            if row is None:
                raise LookupError("connection resource does not exist")
            resource = _resource_from_row(row)
            if resource.revoked_at is not None:
                raise PermissionError("connection resource is revoked")
            if kind == "model_discovery":
                if expected_grant is None:
                    raise PermissionError("discovery requires current grant context")
                grant_row = connection.execute(
                    "SELECT * FROM outbound_connection_grants WHERE grant_id = ?",
                    (expected_grant.grant_id,),
                ).fetchone()
                if (
                    grant_row is None
                    or grant_row["revoked_at"] is not None
                    or expected_grant.revoked_at is not None
                    or grant_row["connection_id"] != connection_key
                    or expected_grant.connection_id != connection_key
                    or grant_row["owner_user_id"] != resource.owner_user_id
                    or grant_row["owner_user_id"] != expected_grant.owner_user_id
                    or grant_row["universe_id"] != expected_grant.universe_id
                    or grant_row["granted_at"] != expected_grant.granted_at
                ):
                    raise PermissionError("discovery grant context changed")
            if not enabled:
                connection.execute(
                    "DELETE FROM connection_capabilities "
                    "WHERE connection_id = ? AND capability_kind = ?",
                    (connection_key, kind),
                )
                return None
            assert capability is not None
            spec = _CAPABILITY_SPECS[kind]
            # Preserve voice's legacy explicit-POST rule. Discovery follows the
            # existing GET/full-channel scope semantics used by its transport.
            verb_allowed = (
                spec.verb in resource.scopes if kind == "realtime_voice"
                else not spec.verb
                or _verb_within_scopes(spec.verb, resource.scopes, resource.access_mode)
            )
            if resource.connection_type != "http" or not verb_allowed:
                raise PermissionError(f"connection does not authorize capability {spec.verb}")
            if kind == "model_use" and connection.execute(
                "SELECT 1 FROM connection_capabilities WHERE connection_id = ? "
                "AND capability_kind = 'model_discovery'",
                (connection_key,),
            ).fetchone() is not None:
                # Money floor: a declared (unpriced) list may only describe a
                # connection with no priced source. Otherwise an agent-written
                # "free" label would stand in for the catalogue's real prices.
                raise ValueError(MODEL_USE_PRICED_CONFLICT)
            if isinstance(capability, ModelDiscoveryCapability):
                if resource.auth_scheme != capability.execution_contract().auth_scheme:
                    raise PermissionError(
                        "connection authentication does not match discovery protocol"
                    )
            for field_name in spec.url_fields:
                url = getattr(capability, field_name)
                if not url:
                    continue
                canonical = _parse_canonical_https_url(url, allowed_ports=frozenset({443}))
                _enforce_endpoint_allowlist(
                    canonical, spec.verb, resource.allowed_endpoints, resource.access_mode,
                )
            if preview:
                return capability  # Same current grant/endpoint checks, no metadata write.
            connection.execute(
                """
                INSERT INTO connection_capabilities (
                    connection_id, capability_kind, descriptor_json, configured_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(connection_id, capability_kind) DO UPDATE SET
                    descriptor_json = excluded.descriptor_json,
                    configured_at = excluded.configured_at
                """,
                (
                    connection_key,
                    kind,
                    json.dumps(capability.descriptor(), sort_keys=True, separators=(",", ":")),
                    time.time(),
                ),
            )
        return capability

    def get_connection_capability(
        self, connection_id: str, capability_kind: str
    ) -> ConnectionCapability | ModelDiscoveryCapability | None:
        """Return validated non-secret metadata without altering connection views."""

        connection_key = _required("connection_id", connection_id)
        kind = _validate_capability_kind(capability_kind)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT descriptor_json FROM connection_capabilities "
                "WHERE connection_id = ? AND capability_kind = ?",
                (connection_key, kind),
            ).fetchone()
        if row is None:
            return None
        try:
            descriptor = json.loads(str(row["descriptor_json"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("stored capability descriptor is invalid") from exc
        return _validate_connection_capability(connection_key, kind, descriptor)

    def grant_connection(
        self,
        *,
        grant_id: str,
        connection_id: str,
        owner_user_id: str,
        universe_id: str,
        granted_at: float | None = None,
        unprompted_action_cap: ActionCap | None = None,
    ) -> ConnectionGrant:
        resource = self._get_connection_resource(connection_id)
        if resource is None:
            raise LookupError("connection resource does not exist")
        owner = _required("owner_user_id", owner_user_id)
        if resource.owner_user_id != owner:
            raise PermissionError("grant owner does not own connection resource")
        grant = ConnectionGrant(
            grant_id=_required("grant_id", grant_id),
            connection_id=resource.connection_id,
            owner_user_id=owner,
            universe_id=_required("universe_id", universe_id),
            granted_at=time.time() if granted_at is None else granted_at,
            revoked_at=None,
            unprompted_action_cap=unprompted_action_cap,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_connection_grants (
                    grant_id, connection_id, owner_user_id, universe_id,
                    granted_at, revoked_at, unprompted_action_cap_json
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    grant.grant_id,
                    grant.connection_id,
                    grant.owner_user_id,
                    grant.universe_id,
                    grant.granted_at,
                    (
                        json.dumps(
                            grant.unprompted_action_cap.as_dict(),
                            sort_keys=True,
                        )
                        if grant.unprompted_action_cap is not None
                        else None
                    ),
                ),
            )
        return grant

    def get_grant(self, grant_id: str) -> ConnectionGrant | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbound_connection_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
        if row is None:
            return None
        cap_payload = row["unprompted_action_cap_json"]
        cap = ActionCap(**json.loads(cap_payload)) if cap_payload else None
        return ConnectionGrant(
            grant_id=row["grant_id"],
            connection_id=row["connection_id"],
            owner_user_id=row["owner_user_id"],
            universe_id=row["universe_id"],
            granted_at=row["granted_at"],
            revoked_at=row["revoked_at"],
            unprompted_action_cap=cap,
        )

    def list_grants(
        self,
        *,
        owner_user_id: str,
        universe_id: str,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[ConnectionGrant]:
        """List grants for one exact owner/universe without connection secrets."""

        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM outbound_connection_grants
                WHERE owner_user_id = ? AND universe_id = ?
                  AND (? = 0 OR revoked_at IS NULL)
                ORDER BY grant_id LIMIT ?
                """,
                (owner_user_id, universe_id, int(active_only), limit),
            ).fetchall()
        result: list[ConnectionGrant] = []
        for row in rows:
            cap_payload = row["unprompted_action_cap_json"]
            result.append(
                ConnectionGrant(
                    grant_id=row["grant_id"],
                    connection_id=row["connection_id"],
                    owner_user_id=row["owner_user_id"],
                    universe_id=row["universe_id"],
                    granted_at=row["granted_at"],
                    revoked_at=row["revoked_at"],
                    unprompted_action_cap=(
                        ActionCap(**json.loads(cap_payload)) if cap_payload else None
                    ),
                )
            )
        return result

    def revoke_grant(self, grant_id: str, *, revoked_at: float | None = None) -> bool:
        timestamp = time.time() if revoked_at is None else revoked_at
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE outbound_connection_grants
                   SET revoked_at = ?
                 WHERE grant_id = ?
                """,
                (timestamp, grant_id),
            )
        return cursor.rowcount > 0

    def revoke_connection(
        self,
        connection_id: str,
        *,
        revoked_at: float | None = None,
    ) -> bool:
        """Revoke a connection resource and thereby invalidate all of its grants."""
        timestamp = time.time() if revoked_at is None else revoked_at
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE outbound_connections
                   SET revoked_at = ?
                 WHERE connection_id = ?
                """,
                (timestamp, connection_id),
            )
        return cursor.rowcount > 0

    def delete_connection(self, connection_id: str) -> bool:
        """HARD-delete a connection and every grant on it. Returns True if a
        row went away.

        Not a revoke. ``revoke_connection`` stamps ``revoked_at``, and because
        a connection id is DETERMINISTIC on ``(universe_id, destination)``, a
        revoked row makes that destination unusable forever: every
        re-provision then trips the ``revoked_at is not None`` conflict, so a
        user who removes ``github`` could never deposit ``github`` again. That
        is documented in
        ``docs/concerns/2026-08-27-no-reachable-remove-for-http-connections.md``
        and it is why removal deletes rather than flags.

        Grants go first so no window exists where a grant outlives the
        connection it authorises. The caller is responsible for the VAULT
        record; this owns only the ledger rows.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM connection_capabilities WHERE connection_id = ?",
                (connection_id,),
            )
            connection.execute(
                "DELETE FROM outbound_connection_grants WHERE connection_id = ?",
                (connection_id,),
            )
            cursor = connection.execute(
                "DELETE FROM outbound_connections WHERE connection_id = ?",
                (connection_id,),
            )
        return cursor.rowcount > 0

    def resolve_scoped_proxy(
        self,
        *,
        universe_id: str,
        connection_class: str,
    ) -> ScopedConnectionProxy:
        """Resolve exactly one current grant; absent/revoked/ambiguous fail closed."""
        owner_user_id = self.require_authenticated_principal_id()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT g.grant_id, g.revoked_at AS grant_revoked_at,
                       c.owner_user_id,
                       c.provider, c.destination, c.scopes_json,
                       c.connection_type,
                       c.revoked_at AS connection_revoked_at
                  FROM outbound_connection_grants AS g
                  JOIN outbound_connections AS c
                    ON c.connection_id = g.connection_id
                 WHERE g.owner_user_id = ?
                   AND g.universe_id = ?
                   AND c.owner_user_id = ?
                   AND c.connection_class = ?
                """,
                (
                    _required("owner_user_id", owner_user_id),
                    _required("universe_id", universe_id),
                    _required("owner_user_id", owner_user_id),
                    _required("connection_class", connection_class),
                ),
            ).fetchall()
        if not rows:
            raise GrantResolutionError("absent outbound connection grant")
        active = [
            row
            for row in rows
            if row["grant_revoked_at"] is None
            and row["connection_revoked_at"] is None
        ]
        if not active:
            raise GrantResolutionError("revoked outbound connection grant")
        if len(active) != 1:
            raise GrantResolutionError("ambiguous outbound connection grants")
        row = active[0]
        columns = set(row.keys())
        return self._start_scoped_proxy(
            grant_id=row["grant_id"],
            universe_id=universe_id,
            provider=row["provider"],
            destination=row["destination"],
            scopes=tuple(json.loads(row["scopes_json"])),
            owner_user_id=row["owner_user_id"],
            connection_type=(
                (row["connection_type"] if "connection_type" in columns else "") or ""
            ),
        )

    def resolve_exact_scoped_proxy(
        self,
        *,
        universe_id: str,
        grant_id: str,
        connection_id: str,
    ) -> ScopedConnectionProxy:
        """Resolve one named current grant and connection for the principal."""
        grant, resource = self.authorize_exact(
            universe_id=universe_id, grant_id=grant_id, connection_id=connection_id,
        )
        # The broker serves http connections, the only production type; the
        # legacy untyped test fixture keeps its worker.
        channel = None if resource.connection_type != "http" else _broker_channel(
            self._db_path.parent, principal=resource.owner_user_id,
            command_center=grant.universe_id, grant_id=grant.grant_id,
            connection_id=resource.connection_id,
        )
        if channel is not None:
            # The broker process serves it (S6): no per-proxy worker is spawned.
            return ScopedConnectionProxy(
                grant_id=grant.grant_id, provider=resource.provider,
                destination=resource.destination, scopes=resource.scopes,
                _channel=channel,
            )
        return self._start_scoped_proxy(
            grant_id=grant.grant_id,
            universe_id=grant.universe_id,
            provider=resource.provider,
            destination=resource.destination,
            scopes=resource.scopes,
            owner_user_id=resource.owner_user_id,
            connection_type=resource.connection_type,
        )

    def authorize_exact(
        self, *, universe_id: str, grant_id: str, connection_id: str,
    ) -> tuple[ConnectionGrant, ConnectionResource]:
        """The exact checks every broker request runs (I14 decision 2).

        The authenticated principal, an active grant, the grant's owner and
        command center, the connection's identity and owner, and revocation.
        ``resolve_exact_scoped_proxy`` and the broker process both call this one
        definition.
        """
        owner_user_id = self.require_authenticated_principal_id()
        grant = self.require_active_grant(_required("grant_id", grant_id))
        resource = self._get_connection_resource(_required("connection_id", connection_id))
        if resource is None:
            raise GrantResolutionError("absent outbound connection resource")
        exact = (
            grant.connection_id == resource.connection_id,
            grant.owner_user_id == owner_user_id,
            grant.universe_id == _required("universe_id", universe_id),
            resource.owner_user_id == owner_user_id,
            resource.revoked_at is None,
        )
        if not all(exact):
            raise GrantResolutionError("outbound connection grant identity mismatch")
        return grant, resource

    def broker_dispatch_config(
        self, *, grant_id: str, universe_id: str, provider: str, destination: str,
        owner_user_id: str, connection_type: str = "",
    ) -> dict[str, Any]:
        """The trusted dispatcher's configuration for one grant (worker or broker)."""
        grant_runtime_id = hashlib.sha256(grant_id.encode("utf-8")).hexdigest()
        return {
            "allow_test_fixtures": self._allow_test_fixtures,
            "allow_http_connections": _outbound_http_enabled(),
            "ledger_db_path": str(self._db_path.resolve()),
            "universe_dir": str((self._db_path.parent / universe_id).resolve()),
            "provider": provider,
            "destination": destination,
            "connection_type": (connection_type or "").strip().lower(),
            "owner_user_id": owner_user_id,
            "runtime_root": str(
                (self._db_path.parent / ".outbound-proxy" / grant_runtime_id).resolve()
            ),
        }

    def _start_scoped_proxy(
        self,
        *,
        grant_id: str,
        universe_id: str,
        provider: str,
        destination: str,
        scopes: tuple[str, ...],
        owner_user_id: str,
        connection_type: str = "",
    ) -> ScopedConnectionProxy:
        factory_reference = "credential_broker_v1"
        factory_config = self.broker_dispatch_config(
            grant_id=grant_id, universe_id=universe_id, provider=provider,
            destination=destination, owner_user_id=owner_user_id,
            connection_type=connection_type,
        )
        # Resolve the budget BEFORE spawning: a validation failure here must not
        # leak an already-started child (Codex FIX C).
        timeout = _proxy_startup_timeout_seconds()
        from tinyassets.connection_oauth.service import client_config, release_client

        factory_config["oauth_service"] = client_config(
            Path(factory_config["universe_dir"]), owner_user_id,
        )
        context = multiprocessing.get_context("spawn")
        client_channel, server_channel = context.Pipe(duplex=True)
        worker = context.Process(
            target=_run_proxy_worker,
            args=(
                server_channel,
                factory_reference,
                factory_config,
                grant_id,
                scopes,
            ),
            daemon=True,
            name=f"outbound-proxy-{grant_id}",
        )
        try:
            worker.start()
        except Exception as exc:
            release_client(factory_config["oauth_service"])
            # A spawn that never starts used to bypass the diagnostic contract
            # entirely, surfacing as an unrelated error type (Codex FIX D).
            client_channel.close()
            server_channel.close()
            raise ProxyRequestError(
                f"outbound proxy could not be spawned: {type(exc).__name__}"
            ) from exc
        server_channel.close()

        def _abandon() -> int | None:
            """Tear the child down, reporting how it died if it died on its own.

            Reads ``exitcode`` BEFORE terminating: our own SIGTERM sets ``-15``,
            so terminating first would overwrite the child's real exit status and
            report every timeout as a process death (Codex FIX D).
            """
            # Read liveness BEFORE touching the channel. Closing our end breaks
            # the child's pipe, and a healthy-but-slow child then dies on the
            # broken pipe while trying to send "ready" — so an exitcode sampled
            # after the close can be a death the PARENT caused, which is the very
            # misattribution this helper exists to prevent.
            release_client(factory_config["oauth_service"])
            own_exit = worker.exitcode
            if own_exit is None and not worker.is_alive():
                # Already exited, just not reaped yet; is_alive() reaps it, so
                # the EOF path recovers the real code instead of losing it.
                own_exit = worker.exitcode
            client_channel.close()
            if own_exit is None:
                worker.terminate()
                worker.join(timeout=1.0)
            return own_exit

        _describe = _describe_child_exit

        if not client_channel.poll(timeout):
            exitcode = _abandon()
            if exitcode is None:
                raise ProxyRequestError(
                    "outbound proxy did not finish starting within "
                    f"{timeout:g}s"
                )
            raise ProxyRequestError(
                f"outbound proxy exited during startup{_describe(exitcode)}"
            )
        try:
            ready = _receive_message(client_channel)
        except ProxyRequestError:
            # On Linux a child that dies CLOSES the pipe, so poll() reports
            # readable and the read hits EOF. That is a child death, not a
            # malformed frame, and it was reaching the caller as an unrelated
            # generic message — the most likely production shape (Codex FIX D).
            exitcode = _abandon()
            raise ProxyRequestError(
                f"outbound proxy exited during startup{_describe(exitcode)}"
            ) from None
        if isinstance(ready, dict) and ready.get("op") == "startup_failed":
            cause = ready.get("cause")
            _abandon()
            detail = f": {cause}" if isinstance(cause, str) and cause else ""
            # The cause is the child's exception CLASS only; its full traceback is
            # on the daemon's stderr. Redacted by construction, still diagnostic.
            raise ProxyRequestError(
                f"outbound proxy failed to start{detail}"
            )
        if ready != {"op": "ready"}:
            _abandon()
            raise ProxyRequestError(
                "outbound proxy sent an unrecognized startup message"
            )
        return ScopedConnectionProxy(
            grant_id=grant_id,
            provider=provider,
            destination=destination,
            scopes=scopes,
            _channel=_ProxyChannel(client_channel, worker, factory_config["oauth_service"]),
        )

    def _active_resource_for_grant(
        self, grant_id: str
    ) -> ConnectionResource | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*
                  FROM outbound_connection_grants AS g
                  JOIN outbound_connections AS c
                    ON c.connection_id = g.connection_id
                 WHERE g.grant_id = ?
                   AND g.revoked_at IS NULL
                   AND c.revoked_at IS NULL
                   AND g.owner_user_id = c.owner_user_id
                """,
                (grant_id,),
            ).fetchone()
        if row is None:
            return None
        return _resource_from_row(row)

    def _active_resource_snapshot_for_grant(
        self, grant_id: str, *, deadline: float | None = None,
    ) -> tuple[ConnectionResource, str] | None:
        """One child-local read of active resource, grant identity and policy."""
        with self._connect() as connection:
            if deadline is not None:
                milliseconds = max(1, int(_remaining_redirect_seconds(deadline) * 1000))
                connection.execute(f"PRAGMA busy_timeout = {milliseconds}")
            row = connection.execute(
                """SELECT c.*, g.universe_id AS grant_universe,
                          g.granted_at AS grant_created_at
                     FROM outbound_connection_grants AS g
                     JOIN outbound_connections AS c ON c.connection_id = g.connection_id
                    WHERE g.grant_id = ? AND g.revoked_at IS NULL
                      AND c.revoked_at IS NULL AND g.owner_user_id = c.owner_user_id""",
                (grant_id,),
            ).fetchone()
        if deadline is not None:
            _remaining_redirect_seconds(deadline)
        if row is None:
            return None
        stamp = hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode("utf-8")).hexdigest()
        return _resource_from_row(row), stamp

    def require_active_grant(self, grant_id: str) -> ConnectionGrant:
        """Return a grant only while both it and its connection are current."""
        grant = self.get_grant(grant_id)
        if grant is None:
            raise GrantResolutionError("absent outbound connection grant")
        if grant.revoked_at is not None:
            raise GrantResolutionError("revoked outbound connection grant")
        if self._active_resource_for_grant(grant_id) is None:
            raise GrantResolutionError("revoked outbound connection resource")
        return grant

    def evaluate_unprompted_action_cap(
        self,
        *,
        grant_id: str,
        action_value: float,
        action_unit: str,
    ) -> CapDecision:
        """Evaluate only the unprompted-action axis; tool/spend gates are separate."""
        if not math.isfinite(action_value):
            raise ValueError("action_value must be finite")
        if action_value < 0:
            raise ValueError("action_value must be non-negative")
        normalized_unit = _required("action_unit", action_unit)
        grant = self.require_active_grant(grant_id)
        cap = grant.unprompted_action_cap
        if cap is not None and normalized_unit != cap.unit:
            raise ValueError(
                f"action_unit {normalized_unit!r} does not match cap unit {cap.unit!r}"
            )
        status = (
            "held"
            if cap is not None and action_value > cap.maximum
            else "automatic"
        )
        return CapDecision(
            status=status,
            cap=cap,
            action_value=action_value,
            action_unit=normalized_unit,
        )

    def create_connector_artifact(
        self,
        *,
        artifact_id: str,
        owner_user_id: str,
        connector_definition: dict[str, Any],
        mcp_client_config: dict[str, Any],
        created_at: float | None = None,
    ) -> ConnectorArtifact:
        definition = _json_object("connector_definition", connector_definition)
        config = _json_object("mcp_client_config", mcp_client_config)
        timestamp = time.time() if created_at is None else created_at
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_connector_artifacts (
                    artifact_id, owner_user_id, connector_definition_json,
                    mcp_client_config_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _required("artifact_id", artifact_id),
                    _required("owner_user_id", owner_user_id),
                    json.dumps(definition, sort_keys=True),
                    json.dumps(config, sort_keys=True),
                    timestamp,
                ),
            )
        artifact = self.get_connector_artifact(artifact_id)
        assert artifact is not None
        return artifact

    def remix_connector_artifact(
        self,
        *,
        parent_artifact_id: str,
        artifact_id: str,
        owner_user_id: str,
        connector_definition: dict[str, Any],
        mcp_client_config: dict[str, Any],
        created_at: float | None = None,
    ) -> ConnectorArtifact:
        parent = self.get_connector_artifact(parent_artifact_id)
        if parent is None:
            raise LookupError("parent connector artifact does not exist")
        timestamp = time.time() if created_at is None else created_at
        child = self.create_connector_artifact(
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            connector_definition=connector_definition,
            mcp_client_config=mcp_client_config,
            created_at=timestamp,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO outbound_connector_artifact_edges (
                        parent_artifact_id, child_artifact_id,
                        remixed_by_user_id, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        parent.artifact_id,
                        child.artifact_id,
                        _required("owner_user_id", owner_user_id),
                        timestamp,
                    ),
                )
        except Exception:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM outbound_connector_artifacts WHERE artifact_id = ?",
                    (child.artifact_id,),
                )
            raise
        remixed = self.get_connector_artifact(child.artifact_id)
        assert remixed is not None
        return remixed

    def get_connector_artifact(
        self, artifact_id: str
    ) -> ConnectorArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT artifact_id, owner_user_id, connector_definition_json,
                       mcp_client_config_json, created_at
                  FROM outbound_connector_artifacts
                 WHERE artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()
            edge = connection.execute(
                """
                SELECT parent_artifact_id
                  FROM outbound_connector_artifact_edges
                 WHERE child_artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        parent_id = edge["parent_artifact_id"] if edge is not None else None
        if parent_id is None:
            attribution = (row["owner_user_id"],)
        else:
            parent = self.get_connector_artifact(parent_id)
            if parent is None:
                raise RuntimeError("connector attribution parent is missing")
            attribution = parent.attribution + (row["owner_user_id"],)
        return ConnectorArtifact(
            artifact_id=row["artifact_id"],
            owner_user_id=row["owner_user_id"],
            connector_definition=json.loads(row["connector_definition_json"]),
            mcp_client_config=json.loads(row["mcp_client_config_json"]),
            parent_artifact_id=parent_id,
            attribution=attribution,
            created_at=row["created_at"],
        )


__all__ = [
    "ActionCap",
    "CapDecision",
    "ConnectionGrant",
    "ConnectionLedger",
    "ConnectionResource",
    "ConnectorArtifact",
    "CredentialBlindBroker",
    "GrantResolutionError",
    "ProxyRequestError",
    "ScopedConnectionProxy",
]
