"""Owner-scoped provisioning of a generic outbound ``http`` connection.

``write_graph target=connection operation=connect_http`` — the keystone that lets
a universe owner build an outbound channel to ANY HTTPS API their universe can act
on via the ``authenticated_external_call`` effector. This handler is channel-
agnostic by construction: it hard-codes no service. The owner supplies the host,
path, and secret at build time, so a channel we never anticipated works the same
way as any other — that is the whole point. It is the sibling of ``connect_llm``:
an authenticated **admin** deposits a bearer secret into the per-universe vault and
binds a validated ``http`` ``ConnectionLedger`` connection to the universe.

Slice 1 scope + security posture (grounded in the outbound substrate):
- **bearer only.** Most HTTPS APIs authenticate with ``Authorization: Bearer``.
  This keeps the credential single-secret (what the general vault resolver returns)
  and avoids the ``none``/``basic``/``header``/``oauth1a`` edge cases. Others are
  deferred.
- **SSRF is already enforced by the substrate, not re-implemented here.**
  ``create_connection``/``_parse_allowed_endpoints`` reject IP-literals,
  single-label/``localhost`` hosts, wildcards, userinfo, traversal paths, and
  unsafe methods (``CONNECT``/``TRACE`` excluded) at creation; the SSRF-hardened
  broker enforces HTTPS-only, private/loopback/link-local/metadata-IP blocking
  (IPv4+IPv6), DNS-rebinding revalidation, disabled redirects, and per-request
  endpoint match at request time. This handler passes the caller's endpoints
  through and maps validation failures to a clean, secret-free error.
- **Identity is (universe, destination)**, never the actor — so a second admin
  cannot mint a rival connection under the same consent key.
- **Provision-or-rotate, policy-immutable.** A repeat call for the same
  destination rotates the secret and reuses the (idempotent) connection/grant ONLY
  when every immutable field matches (owner, type, class, auth scheme, scopes,
  destination, credential_ref, and the endpoint allow-list — the last compared as
  an unordered set, so a reorder alone is not a change). Any real change to the
  policy — a different endpoint allow-list included — is refused as a conflict
  before any vault write, so a re-provision can never silently keep the old egress
  policy under a rotated secret. Changing an existing connection's policy is
  possible through the dedicated extension operation or by explicit removal
  followed by a new deposit. Removal fences dependent model authority and erases
  the old grants/custody; it never silently revives them under a replacement key.
- **Never echoes the secret or the credential_ref.** Errors carry no secret.

A live outbound call additionally requires the owner's effector consent for the
destination AND the daemon flag ``TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED``;
this handler returns those as explicit next steps rather than implying a live
channel. Exposing this on the served surface (so the universe builds channels
itself) is Slice 2.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path, _request_universe, _universe_dir
from tinyassets.storage.outbound_connections import (
    _SSRF_ALLOWED_METHODS,
    _URL_SECRET_SCHEME,
    ACCESS_EXACT,
    ACCESS_FULL,
    ActionCap,
    ConnectionLedger,
    SsrfValidationError,
    _parse_allowed_endpoints,
    normalize_access_mode,
    url_secret_token,
    validate_url_secret_binding,
    validate_url_secret_value,
)
from tinyassets.storage.workspace_authority import (
    GitScopeError,
    connection_git_scopes,
    format_git_scope,
    is_git_scope,
    normalize_git_host,
    require_git_scope,
)

# Default when the caller names no scheme (the common single-token API case).
_DEFAULT_AUTH_SCHEME = "bearer"
# Sentinel distinguishing an ABSENT auth_scheme key from an explicit null/falsy
# value: only absence takes the bearer default; any explicit non-string is refused.
_ABSENT = object()
#: Auth schemes this deposit door accepts — exactly the set the broker child can
#: sign (see ``_SUPPORTED_HTTP_AUTH_SCHEMES`` / ``_build_http_secret_bundle`` in
#: storage/outbound_connections.py), minus ``none``: a no-credential connection
#: has nothing to deposit.
#:
#: ``header`` was excluded on the stated grounds that it "needs a per-connection
#: header NAME the ledger does not yet persist". That was stale. The header NAME
#: is a per-CALL field on the request packet
#: (``authenticated_external_call``: ``"header_name": "X-Api-Key"``), and the
#: stored credential for ``header`` is a single token — byte-identical to
#: ``bearer`` (``_build_http_secret_bundle``: ``if scheme in ("bearer",
#: "header")``). So nothing had to be persisted and nothing had to be built; the
#: door was simply shut.
#:
#: It matters because an API keyed by a custom header (``X-API-Key``,
#: ``apikey``, ...) is extremely common, and every one of them was undepositable
#: — which is exactly the "another service, another patch" the acceptance test
#: forbids. The header name stays validated where it is used:
#: ``_reject_forbidden_header_name`` and ``_SSRF_FORBIDDEN_HEADER_CHARS`` refuse
#: a smuggling attempt (``Content-Length`` was the one that prompted them).
#:
#: Generic on purpose: ``oauth1a`` is what makes every OAuth 1.0a API
#: depositable with no service code, ``header`` does the same for every
#: custom-header API, and ``url_secret`` for every capability URL — a webhook
#: link whose secret is a path segment, which is how Slack, Discord, Zapier,
#: Make and this platform's own ``/mcp/hooks/<token>`` all work. Before it, a
#: universe handed such a link had two options and both were wrong: ask for a
#: bearer token that does not exist, or hardcode the secret into
#: ``path_template`` where the grant stores and shows it in the clear. It did
#: the second, live, on 2026-09-30.
_DEPOSITABLE_AUTH_SCHEMES = frozenset(
    {"bearer", "basic", "header", "oauth1a", _URL_SECRET_SCHEME}
)
#: The ONE field name a ``url_secret`` card uses. Fixed for the same reason
#: ``oauth1a``'s four are: the deposit reads it. Label it the way the service
#: words it ("Webhook URL"), name it this.
URL_SECRET_FIELD_NAME = "capability_url"
#: ``oauth2`` is deposited ONLY by a completed sign-in (``connection_oauth.flow``
#: through ``pending_requests.answer_connect_with_token``), never pasted: its
#: token bundle names the token URL a refresh token is sent to, so only the
#: owner's own sign-in may write it.
_SIGN_IN_AUTH_SCHEME = "oauth2"
_OAUTH1A_FIELDS = ("api_key", "api_secret", "access_token", "access_token_secret")


#: A fixed path segment this long, this mixed and this varied is not a path.
#: 20 characters with both a letter and a digit and at least 10 DISTINCT
#: characters clears every ordinary API path segment (``completions``,
#: ``messages``, ``contents``, ``pulls``, ``v1``, ``2``) and catches every
#: capability secret in the table in ``proposal.md`` -- a 43-character
#: ``secrets.token_urlsafe(32)`` hook token, Slack's 24-character webhook token,
#: Discord's 68. It also catches an opaque PUBLIC id (a Google Sheets id), which
#: is a deliberate false positive: hardcoding one is still the wrong shape, and
#: the refusal names the right one (a ``{param}`` with a pattern).
_EMBEDDED_SECRET_MIN_CHARS = 20
_EMBEDDED_SECRET_MIN_DISTINCT = 10


def looks_like_embedded_secret(segment: str) -> bool:
    """Whether a FIXED path segment reads as a credential or an opaque id.

    A heuristic, on purpose, and deliberately biased toward the shape the
    handbook already teaches: whichever of the two it is, the repair is a
    placeholder, so a false positive costs an agent one better-shaped endpoint
    and a false negative costs a user their secret stored in the clear.
    """
    text = segment if isinstance(segment, str) else ""
    if len(text) < _EMBEDDED_SECRET_MIN_CHARS:
        return False
    if not any(c.isdigit() for c in text) or not any(c.isalpha() for c in text):
        return False
    return len(set(text)) >= _EMBEDDED_SECRET_MIN_DISTINCT


def embedded_secret_refusal(endpoints: Any) -> dict[str, Any] | None:
    """Refuse a hardcoded secret in a ``path_template``, or return ``None``.

    THE live 2026-09-30 failure: with no shape for a capability URL, the universe
    put the friend's webhook secret into ``path_template`` -- a grant field that
    is stored in the clear, projected to ``read_graph target="connections"``,
    rendered in the owner's grant sentence and copied into a remixed connector
    artifact.

    Runs at the AUTHORING doors only (this ask, this deposit, this extension),
    never in ``_validate_path_template``, which also re-parses STORED templates.
    Putting it there would make an already-deposited connection unreadable and
    unremovable, which is a data-loss bug wearing a security fix's name.
    """
    if not isinstance(endpoints, list):
        return None
    for endpoint in endpoints:
        template = (
            endpoint.get("path_template")
            if isinstance(endpoint, dict)
            else getattr(endpoint, "path_template", None)
        )
        if not isinstance(template, str):
            continue
        for index, segment in enumerate(template.split("/")[1:], start=1):
            if segment.startswith("{") or not looks_like_embedded_secret(segment):
                continue
            return {
                "error": "connection_setup_invalid",
                "detail": (
                    f"path_template segment {index} looks like a secret or an "
                    "opaque id, not a fixed path. If it is a credential (the "
                    "code in a webhook link), do not put it here: use "
                    f'"auth_scheme": "{_URL_SECRET_SCHEME}" and write '
                    "{secret} in its place, and the owner pastes the whole "
                    "link into the card -- the code goes to the vault instead "
                    "of into this grant, where it would be stored and shown in "
                    "the clear. If it is a public identifier, make it a {name} "
                    "placeholder with a param_patterns regex."
                ),
            }
    return None


def extract_url_secret(
    secret: str, endpoints: tuple[Any, ...]
) -> tuple[str, str]:
    """``(stored_segment, error)`` for a ``url_secret`` deposit.

    The owner pastes the WHOLE LINK they were given, because that is what they
    have -- the live failure asked them to "paste the code at the end of the
    link", which is the platform making a person do a parse. So this parses it:
    the URL must be plain https, and its host and path must match exactly ONE
    declared endpoint whose template carries the reserved placeholder. The
    captured segment(s) become the credential. A bare segment is still accepted
    for an owner who pasted only the code.

    NEVER echoes the pasted value. A refusal names the declared TEMPLATE, which
    is the thing the owner can compare against what they were given.
    """
    text = (secret or "").strip()
    carriers = [
        (endpoint, url_secret_token(endpoint))
        for endpoint in endpoints
        if url_secret_token(endpoint)
    ]
    if not carriers:
        return "", (
            f"a {_URL_SECRET_SCHEME} deposit needs an endpoint whose "
            "path_template carries {secret}"
        )
    expected = ", ".join(
        f"https://{endpoint.host}{endpoint.path_template}"
        for endpoint, _token in carriers
    )
    if not text.lower().startswith(("http://", "https://")):
        # A bare segment. Validated against the grammar of the single carrier;
        # with several, which one it belongs to would be a guess.
        if len(carriers) != 1:
            return "", (
                "this connection declares several capability endpoints, so "
                f"paste the whole link (expected one of: {expected})"
            )
        try:
            return validate_url_secret_value(text, carriers[0][1]), ""
        except SsrfValidationError as exc:
            return "", str(exc)
    try:
        parts = urllib.parse.urlsplit(text)
        # `parts.port` is lazy AND raises on a non-numeric port, so read it
        # inside the same guard as the parse.
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        # `urlsplit` QUOTES ITS INPUT: a netloc that changes under NFKC
        # normalization (a full-width solidus, U+FF0F) raises
        # "netloc '<the whole thing>' contains invalid characters", and the
        # whole thing is the link with the secret in it. Swallowed to fixed
        # text, with `from None` so no `__context__` carries it either
        # (gpt-6-astra refute round 1, FINDING 5).
        return "", (
            "that link could not be read as a plain https URL -- paste it again "
            f"exactly as you were given it (expected {expected})"
        )
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.query
        or parts.fragment
        or not hostname
    ):
        # A PORT is refused, not ignored. Reading `hostname` alone accepted
        # `https://host:8443/mcp/hooks/<secret>` against an endpoint that is
        # dialed on 443 — so the owner's secret for one origin would be sent to
        # a different one on the same name (astra round 1, FINDING 6). The
        # endpoint grammar carries no port, so there is nothing to match against.
        return "", (
            "paste the plain https link you were given -- no sign-in prefix, no "
            f"port, no query string and no #fragment (expected {expected})"
        )
    host = hostname.strip().lower()
    # Zapier and Make show a trailing slash in their own UI; the endpoint
    # template does not carry one, so tolerate exactly that.
    path = parts.path.rstrip("/") or "/"
    matches: list[str] = []
    for endpoint, token in carriers:
        if endpoint.host != host:
            continue
        prefix, _sep, suffix = endpoint.path_template.partition(token)
        if suffix.strip("/"):
            # The placeholder is not the tail of the template: the captured
            # value is bounded by the fixed segments on both sides.
            if not (path.startswith(prefix) and path.endswith(suffix)):
                continue
            captured = path[len(prefix):len(path) - len(suffix)]
        else:
            if not path.startswith(prefix):
                continue
            captured = path[len(prefix):]
        try:
            matches.append(validate_url_secret_value(captured, token))
        except SsrfValidationError:
            continue
    unique = sorted(set(matches))
    if not unique:
        return "", (
            "that link does not match the endpoint this request declared "
            f"(expected {expected}). Check you pasted the right link, or raise "
            "the ask for the link you have."
        )
    if len(unique) > 1:
        return "", (
            "that link matches more than one declared endpoint, so which part "
            f"is the secret would be a guess (declared: {expected})"
        )
    return unique[0], ""


def _secret_shape_error(scheme: str, secret: str) -> str:
    """Return a secret-free error string if ``secret`` is malformed for ``scheme``.

    Mirrors the broker's ``_build_http_secret_bundle`` contract so the door and the
    request-time parser agree; never includes any part of the secret in the message.
    """
    if scheme == "basic":
        # Mirror the broker exactly: BOTH halves must be non-empty ("user:", ":pw",
        # and ":" are refused here, not written and rejected at dispatch later).
        username, sep, password = secret.partition(":")
        if not sep or not username or not password:
            return "basic secret must be username:password (both non-empty)"
        return ""
    from tinyassets.connection_oauth.tokens import decode, looks_like_bundle

    if scheme == _SIGN_IN_AUTH_SCHEME:
        try:
            decode(secret)
        except (TypeError, ValueError):
            return "oauth2 secret must be a token bundle from a completed sign-in"
        return ""
    if looks_like_bundle(secret):
        return "a sign-in token bundle is only valid on an oauth2 connection"
    if scheme == "oauth1a":
        try:
            values = json.loads(secret)
        except (TypeError, ValueError):
            return (
                "oauth1a secret must be a JSON object with api_key, api_secret, "
                "access_token, access_token_secret"
            )
        if not isinstance(values, dict):
            return "oauth1a secret must be a JSON object"
        missing = [
            name
            for name in _OAUTH1A_FIELDS
            if not isinstance(values.get(name), str) or not values.get(name)
        ]
        if missing:
            return "oauth1a secret is missing: " + ", ".join(missing)
    return ""

# Strict destination grammar: this one value keys the vault record (service +
# destination), the connection identity, and — downstream — effector consent and
# soul authority. Bounded ASCII, no whitespace/control/normalization aliases.
_DESTINATION_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,126}$")

_MAX_SECRET_CHARS = 200_000
_MAX_ENDPOINTS = 200
#: A connection may hold at most this many git scopes.
_MAX_GIT_SCOPES = 200


def _answered_policy_snapshot(document: dict) -> dict[str, str] | None:
    """The policy an ``extend_http`` ask recorded when it rendered its sentence.

    ``None`` when the ask carries none -- an older pending row, or a direct
    call that never went through a tab. The caller then swaps against its own
    read, which is what it did before this existed: no worse, and a stored ask
    that DOES carry one is held to it.

    Every field must be a non-empty string. A partial snapshot is a broken one,
    and swapping against a broken snapshot would refuse every answer.
    """
    raw = document.get("policy_snapshot")
    if not isinstance(raw, dict):
        return None
    snapshot = {
        key: raw.get(key)
        for key in ("access_mode", "endpoints_json", "scopes_json")
    }
    if not all(isinstance(v, str) and v for v in snapshot.values()):
        return None
    # The incarnation is OPTIONAL within a snapshot that has the rest: a row
    # raised before it existed still gets the ask/answer drift check. Requiring
    # it would silently downgrade those rows to no snapshot at all, which is
    # the weaker guarantee wearing the stronger one's name.
    incarnation = raw.get("incarnation")
    snapshot["incarnation"] = incarnation if isinstance(incarnation, str) else ""
    return snapshot  # type: ignore[return-value]


def _redirect_permission_requested(endpoints: Any) -> bool:
    """Recognize the additional permission before strict endpoint validation."""
    return isinstance(endpoints, list) and any(
        isinstance(endpoint, dict) and "redirect_mode" in endpoint
        and endpoint["redirect_mode"] != "none" for endpoint in endpoints
    )


def _requested_git_scopes(document: dict[str, Any]) -> frozenset[str]:
    """The git scopes a caller asked for, canonicalized.

    ``scopes`` accepts GIT scopes only. The HTTP verbs stay derived from the
    endpoint list - a caller that could name its own verbs would be able to
    widen the HTTP surface without widening the endpoint allow-list, which is
    the one thing the deposit's least-privilege story rests on.
    """
    raw = document.get("scopes")
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise GitScopeError("scopes must be a list of git scopes")
    if len(raw) > _MAX_GIT_SCOPES:
        raise GitScopeError(
            f"a connection may carry at most {_MAX_GIT_SCOPES} git scopes"
        )
    found: set[str] = set()
    for value in raw:
        if not is_git_scope(value):
            raise GitScopeError(
                f"scopes accepts git scopes only (git_read:owner/name, "
                f"git_write:owner/name); HTTP verbs come from the endpoints. "
                f"Got {value!r}"
            )
        found.add(format_git_scope(*require_git_scope(value)))
    return frozenset(found)


def _stored_git_scopes(resource: Any) -> frozenset[str]:
    """The git scopes already on the row, as canonical scope strings."""
    return frozenset(
        format_git_scope(kind, repo) for kind, repo in connection_git_scopes(resource)
    )


def _git_scopes_in(scopes_json: str) -> frozenset[str]:
    """The git scopes in a stored ``scopes_json`` text, canonical.

    Used wherever a write's CAS baseline is that same text, so the scopes the
    write carries forward and the snapshot it is guarded by come from ONE
    read (Codex round 3 on the 2026-09-02 rail change: the re-provision path
    took them from an earlier parsed read and could drop a scope an
    extension had just added).
    """
    from tinyassets.storage.workspace_authority import is_git_scope, parse_git_scope

    try:
        raw = json.loads(scopes_json)
    except (TypeError, ValueError):
        return frozenset()
    found: set[str] = set()
    for value in raw if isinstance(raw, list) else []:
        if not is_git_scope(value):
            continue
        parsed = parse_git_scope(value)
        if parsed:
            found.add(format_git_scope(*parsed))
    return frozenset(found)

# Conservative fixed unprompted cap for an MVP outbound channel; tune later.
_HTTP_ACTION_CAP = ActionCap("http_requests", 10_000, "requests")

# Uniform absent-resource envelope for not-authenticated / not-admin / unknown
# universe — a caller cannot probe existence through this surface (mirrors
# connect_llm / cloud_connections).
_NOT_FOUND: dict[str, Any] = {"error": "not_found", "resource": "connection"}


def _payload(value: Any) -> dict[str, Any]:
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("payload_json must be a JSON object")
    return document


def _ids(*, universe_id: str, destination: str) -> tuple[str, str]:
    """Deterministic (connection_id, grant_id) from (universe, destination).

    Length-prefixed canonical serialization (not ambiguous concatenation) so no
    two distinct (universe, destination) pairs can collide, and the actor is
    deliberately excluded so one destination has exactly one connection per
    universe regardless of which admin provisions it.
    """
    material = (
        f"{len(universe_id)}:{universe_id}\0{len(destination)}:{destination}\0http"
    ).encode()
    digest = hashlib.sha256(material).hexdigest()[:32]
    return f"http_{digest}", f"http_grant_{digest}"


def _project(resource: Any, grant: Any) -> dict[str, Any]:
    """Redacted projection — never the credential_ref/secret."""
    return {
        "status": "provisioned",
        "connection_id": resource.connection_id,
        "grant_id": grant.grant_id,
        "provider": resource.provider,
        "destination": resource.destination,
        "connection_class": resource.connection_class,
        "auth_scheme": resource.auth_scheme,
        "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
        # The owner-declared git host, or "" (git then uses the endpoint host).
        "git_host": getattr(resource, "git_host", "") or "",
        "action_cap": (
            grant.unprompted_action_cap.as_dict()
            if grant.unprompted_action_cap is not None
            else None
        ),
        "next": [
            "grant effector consent for this destination "
            "(write_graph target=source_channel operation=approve)",
            "for a live post, TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED must be "
            "on for the daemon",
            "build a node whose effect is authenticated_external_call: its "
            "source_code must define a function named exactly run(state) — the only "
            "entry point the runtime calls (any other name silently runs nothing) — "
            "and return (under one of its output_keys) a json.dumps "
            "packet of EXACTLY {\"sink\":\"authenticated_external_call\", "
            "\"connection_id\":\"<this connection_id>\", "
            "\"grant_id\":\"<this grant_id>\", \"verb\":\"<HTTP method, e.g. POST>\", "
            "\"request\":{\"method\":\"<HTTP method>\", \"host\":\"<an allowed host>\", "
            "\"path\":\"<an allowed path>\", \"body\":{...}}} — connection_id and "
            "grant_id are REQUIRED (do not use 'destination'/'payload' keys)",
        ],
    }


def _canonical_endpoint_set(endpoints: list[dict[str, Any]]) -> set[str]:
    """Each endpoint in its own canonical form, as a set.

    ``_canonical_policy`` answers "is this the same policy?"; extension needs
    "does this policy CONTAIN that one?", which needs the endpoints separable.
    Both normalize the same way, so the two answers cannot disagree.
    """
    return {_canonical_policy([endpoint]) for endpoint in endpoints}


def _canonical_policy(endpoints: list[dict[str, Any]]) -> str:
    """Order-insensitive canonical form of an endpoint allow-list, for the
    idempotency conflict-check ONLY.

    Runtime authorization treats endpoints, methods, and ``allowed_query`` names
    as UNORDERED sets, so a re-provision that reorders an otherwise-identical
    policy must compare equal — else idempotency breaks with a false
    ``connection_conflict``. Storage does NOT sort the endpoint list, the
    ``methods``, or ``allowed_query`` (``_parse_allowed_endpoints`` /
    ``_validate_endpoint_methods`` preserve input order), so BOTH sides are
    normalized here: sort ``methods`` / ``allowed_query`` / ``required_query``
    within each endpoint, then sort the endpoints by a stable serialization.
    ``param_patterns`` / ``query_patterns`` are already order-independent (dicts
    emitted by ``as_dict``); ``sort_keys`` canonicalizes them.

    This ONLY collapses set-identical reorderings — any genuinely different host,
    path, method, or query name changes the canonical string, so no distinct
    policy can falsely MATCH. Endpoint duplicates are preserved (a list with a
    repeated endpoint is a different input, treated as a conflict), so equality
    is never over-broad.
    """
    normalized = [
        {
            "host": endpoint.get("host"),
            "path_template": endpoint.get("path_template"),
            "methods": sorted(set(endpoint.get("methods") or ())),
            "param_patterns": endpoint.get("param_patterns") or {},
            "allowed_query": sorted(set(endpoint.get("allowed_query") or ())),
            "query_patterns": endpoint.get("query_patterns") or {},
            "required_query": sorted(set(endpoint.get("required_query") or ())),
            **(
                {"redirect_mode": endpoint["redirect_mode"]}
                if endpoint.get("redirect_mode", "none") != "none" else {}
            ),
        }
        for endpoint in endpoints
    ]
    normalized.sort(key=lambda endpoint: json.dumps(endpoint, sort_keys=True))
    return json.dumps(normalized, sort_keys=True)


def connect_http(
    *, universe_id: str = "", payload: Any = None, allow_oauth2: bool = False,
    mcp_draft: dict | None = None,
) -> dict[str, Any]:
    from tinyassets.onboarding.serving import _gesture_lock

    with _gesture_lock(_request_universe(universe_id)):
        return _connect_http(universe_id=universe_id, payload=payload,
                             allow_oauth2=allow_oauth2, mcp_draft=mcp_draft)


def _deposit_http(*, uid, actor, destination, secret):
    from tinyassets.credential_vault import http_credential_record, write_credential_vault

    udir = _universe_dir(uid)
    try:
        write_credential_vault(
            udir,
            [http_credential_record(destination=destination, token=secret)],
            owner_user_id=actor,
            universe_id=uid,
        )
    except PermissionError:
        return {
            "error": "credential_ownership_transfer_unsupported",
            "detail": (
                "this destination's credential is owned by another principal"
            ),
        }
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    except Exception:  # noqa: BLE001 - fail closed, never leak the secret
        return {"error": "deposit_failed", "resource": "connection"}

    return None


def _connect_plan(*, resource, raw_policy, existing_grant, actor, uid, destination,
                  connection_id, grant_id, scheme, credential_ref, git_host,
                  requested_endpoints, http_scopes):
    """Pure existing-policy conflict checks, shared with broker preparation/commit."""
    legacy_scope_upgrade = False
    endpoints_extend = False
    if resource is not None and raw_policy is not None:
        # Scopes are otherwise a PROJECTION of the endpoint methods, so anything
        # not derivable from endpoints - a git scope - would silently vanish on
        # the next deposit and the sink would start refusing checkouts nobody
        # revoked. Carry the stored ones forward explicitly.
        http_scopes = tuple(
            sorted(set(http_scopes) | _git_scopes_in(raw_policy[1]))
        )
        # Every immutable field EXCEPT scopes must match for either idempotent reuse
        # or the bounded legacy-scope upgrade applied at the END of this handler.
        non_scope_mismatch = (
            resource.owner_user_id != actor
            or resource.connection_type != "http"
            or resource.connection_class != "http"
            or resource.provider != "http"
            or resource.auth_scheme != scheme
            or resource.destination != destination
            or resource.credential_ref != credential_ref
            or resource.revoked_at is not None
            # A different git host is a different place the key goes: never a
            # silent rotation. Remove and reconnect to change it.
            or resource.git_host != git_host
            or _canonical_policy([e.as_dict() for e in resource.allowed_endpoints])
            != _canonical_policy(requested_endpoints)
        )
        stored_endpoints = [e.as_dict() for e in resource.allowed_endpoints]
        # A credential is deposited ONCE and extended as the work needs it
        # (founder, 2026-08-27: "not for each action with that credential").
        # Before this, a deterministic connection id plus ANY policy difference
        # read as a hard conflict, so adding one endpoint meant a whole new
        # connection under a new name — and another paste of the same key.
        #
        # Only ADDITION is an extension. Removal or replacement stays a conflict:
        # silently dropping an endpoint another graph depends on is the dangerous
        # direction, and it is a different intent from "also let it do this".
        endpoints_extend = (
            _canonical_endpoint_set(requested_endpoints)
            > _canonical_endpoint_set(stored_endpoints)
        )
        non_scope_mismatch = non_scope_mismatch and not (
            endpoints_extend
            and _canonical_policy(stored_endpoints)
            != _canonical_policy(requested_endpoints)
            and resource.owner_user_id == actor
            and resource.connection_type == "http"
            and resource.connection_class == "http"
            and resource.provider == "http"
            and resource.auth_scheme == scheme
            and resource.destination == destination
            and resource.credential_ref == credential_ref
            and resource.revoked_at is None
            and resource.git_host == git_host
        )
        scopes_match = tuple(resource.scopes) == http_scopes
        # A connection provisioned BEFORE the scope fix carries the legacy ("http",)
        # token, which the authenticated_external_call effector can never match
        # (it checks the HTTP verb against resource.scopes). Deterministic ids +
        # no policy-update path would otherwise strand such a row forever behind the
        # conflict check. When it is OTHERWISE policy-identical, its scope is UPGRADED
        # to the method union — a bounded, one-directional migration to the very
        # methods its own endpoints already permit (widens nothing: the per-endpoint
        # methods gate is unchanged). Codex ADAPT, #2521. The upgrade is DEFERRED to
        # the end of this handler (after the grant-conflict check AND a successful
        # credential deposit) so a deposit failure or grant refusal leaves the legacy
        # row inert — never activating a formerly-unusable connection with the stale,
        # un-rotated secret (Codex ADAPT re-review: fail-open ordering).
        legacy_scope_upgrade = (
            not non_scope_mismatch
            and not scopes_match
            and tuple(resource.scopes) == ("http",)
        )
        if non_scope_mismatch or (
            not scopes_match and not legacy_scope_upgrade and not endpoints_extend
        ):
            return {"error": "connection_conflict", "resource": "connection"}
    if existing_grant is not None and (
        existing_grant.connection_id != connection_id
        or existing_grant.owner_user_id != actor
        or existing_grant.universe_id != uid
        or existing_grant.revoked_at is not None
    ):
        return {"error": "connection_conflict", "resource": "grant"}

    return {"http_scopes": http_scopes, "legacy_scope_upgrade": legacy_scope_upgrade,
            "endpoints_extend": endpoints_extend}


def _connect_http(
    *, universe_id: str = "", payload: Any = None, allow_oauth2: bool = False,
    mcp_draft: dict | None = None,
) -> dict[str, Any]:
    """Provision (or rotate) a generic http connection for the owner's universe.

    Returns a redacted projection on success and a sanitized error otherwise.

    Fail-closed model: every *refusal* (auth, validation, conflict) happens before
    any write, so a refusal leaves zero vault / connection / grant mutation. A rare
    mid-provision infrastructure fault (e.g. the grant write fails after the vault +
    connection landed) leaves only INERT partial state — a connection with no grant,
    or a vault record with no connection, neither of which can authorize a call —
    which the idempotent, deterministic-id retry completes (self-heal). It never
    leaves a usable half-connection, and it never rotates a live credential on a
    conflicting re-provision (the conflict-check below refuses first).
    """
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl

    # 1. Server-derived authenticated principal (no env fallback).
    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}

    # 2. Resolve universe; require an explicit admin ACL row for THIS actor on
    #    THIS universe (mirror connect_llm — not the public->read short-circuit).
    uid = _request_universe(universe_id)
    base = _base_path()
    admin = [
        row
        for row in list_universe_acl(base, universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return dict(_NOT_FOUND)

    # 3. Validate the whole payload BEFORE any write.
    try:
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    destination = str(document.get("destination") or "").strip().lower()
    try:
        asked_access = normalize_access_mode(document.get("access"))
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": (
                "destination must be 2-127 chars of [a-z0-9._:-] starting "
                "alphanumeric"
            ),
        }

    # Any auth scheme the engine already signs is accepted at the deposit door —
    # channel-agnostically. The broker signs bearer/basic/header/oauth1a/none per
    # connection (`_build_http_secret_bundle` + `_apply_auth`); until now this door
    # was bearer-only, which silently blocked every OAuth 1.0a service (X/Twitter
    # posting, and any other 1.0a API) even though the engine handled it end-to-end.
    # Unlocking the scheme here (NOT adding a per-service path) is what keeps
    # "add a channel we haven't tried" working with zero service-specific code.
    # Key PRESENCE decides the default, not the value: only an ABSENT key takes
    # bearer. An explicit `"auth_scheme": null` is a malformed request and must be
    # refused like any other non-string (Codex: null was treated as absent).
    raw_scheme = document.get("auth_scheme", _ABSENT)
    if raw_scheme is _ABSENT:
        scheme = _DEFAULT_AUTH_SCHEME
    elif isinstance(raw_scheme, str) and raw_scheme.strip():
        scheme = raw_scheme.strip().lower()
    else:
        # An explicit non-string / empty scheme is a malformed request, NOT an
        # invitation to silently default to bearer (Codex: falsy schemes defaulted).
        scheme = ""
    if scheme not in _DEPOSITABLE_AUTH_SCHEMES and not (
        allow_oauth2 and scheme == _SIGN_IN_AUTH_SCHEME
    ):
        return {
            "error": "unsupported_auth_scheme",
            "detail": (
                "auth_scheme must be one of "
                + ", ".join(sorted(_DEPOSITABLE_AUTH_SCHEMES))
            ),
            "allowed_auth_schemes": sorted(_DEPOSITABLE_AUTH_SCHEMES),
        }

    secret = document.get("secret")
    if not isinstance(secret, str) or not secret.strip():
        return {"error": "connection_setup_invalid", "detail": "secret is required"}
    if len(secret) > _MAX_SECRET_CHARS:
        return {"error": "connection_setup_invalid", "detail": "secret is too large"}
    # Validate the secret's SHAPE for the scheme at the door, mirroring exactly what
    # the broker child will demand at request time — so a malformed multi-value
    # credential is rejected BEFORE anything is written, not discovered as a failed
    # outbound call later. The vault stores one opaque string per connection; for
    # oauth1a that string is a JSON object of the four OAuth values, for basic it is
    # "username:password". The values themselves are never inspected or echoed.
    shape_error = _secret_shape_error(scheme, secret)
    if shape_error:
        return {"error": "connection_setup_invalid", "detail": shape_error}

    endpoints = document.get("allowed_endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        return {
            "error": "connection_setup_invalid",
            "detail": "allowed_endpoints must be a non-empty list",
        }
    if len(endpoints) > _MAX_ENDPOINTS:
        return {
            "error": "connection_setup_invalid",
            "detail": f"allowed_endpoints exceeds {_MAX_ENDPOINTS}",
        }

    # Deep-validate the endpoints (the SSRF allow-list boundary) BEFORE any write,
    # so invalid input mutates nothing. This is the same validator create_connection
    # applies; running it first turns a post-deposit failure into a pre-deposit
    # rejection. Runtime SSRF (private-IP/rebinding/redirects/HTTPS) stays enforced
    # by the broker at request time — not re-implemented here.
    try:
        parsed_endpoints = _parse_allowed_endpoints(endpoints)
    except SsrfValidationError as exc:
        return {"error": "endpoint_not_permitted", "detail": str(exc)}
    except (ValueError, TypeError) as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    requested_endpoints = [e.as_dict() for e in parsed_endpoints]
    # A secret hardcoded into an endpoint would be stored in the clear in the
    # grant. Refused HERE, at the authoring door, with the shape that works.
    hardcoded = embedded_secret_refusal(requested_endpoints)
    if hardcoded is not None:
        return hardcoded
    # The capability-URL binding, before anything is written, so the owner reads
    # a precise refusal rather than the storage layer's. The asked access mode
    # rides along rather than being re-checked here: two statements of one rule
    # is how the two drift, and the validator is the one the storage boundary
    # and dispatch also call.
    try:
        validate_url_secret_binding(
            scheme, parsed_endpoints, access_mode=asked_access
        )
    except SsrfValidationError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    if scheme == _URL_SECRET_SCHEME:
        # THE extraction: the owner pasted the whole link, the platform finds the
        # secret in it, and only the segment is written. Replaces `secret` for
        # every write below, so no code path can store the full URL.
        extracted, extract_error = extract_url_secret(secret, parsed_endpoints)
        if extract_error:
            return {"error": "connection_setup_invalid", "detail": extract_error}
        secret = extracted
    # The connection SCOPE for an http connection is the set of HTTP methods it
    # permits — that is the "connection scope string" the authenticated_external_call
    # effector matches the packet ``verb`` against (proxy/broker check
    # ``verb in resource.scopes``). It MUST be the verbs, not a literal ("http",)
    # type token: the latter admits NO verb, so every outbound POST failed
    # "verb outside granted connection scope" and the whole http channel was dead on
    # arrival (found by the first end-to-end live channel test, 2026-08-24). Methods
    # are already uppercased + de-duped + guaranteed non-empty by
    # ``_validate_endpoint_methods``; sort for a deterministic, idempotency-stable
    # scope tuple. connection_type/connection_class/provider ("http") carry the type
    # discrimination, so scopes is free to hold the verbs.
    if asked_access == ACCESS_FULL:
        # A full channel is every verb the platform will put on a socket. The
        # scope tuple is a FIFTH reader of the grant (`ScopedConnectionProxy`
        # refuses an out-of-scope verb before the allowlist is consulted at
        # all), so deriving it from the synthesized GET endpoint left a full
        # connection unable to POST -- the headline promise, inert (Codex code
        # review round 2). Not a wildcard: the five verbs, named.
        http_scopes = tuple(sorted(_SSRF_ALLOWED_METHODS))
    else:
        http_scopes = tuple(sorted({m for e in parsed_endpoints for m in e.methods}))
    # A GIT scope is the one scope a caller supplies rather than the deposit
    # deriving it: nothing about an endpoint list says which repository a git
    # credential may clone. Only git scopes may be passed - HTTP verbs stay
    # derived from the endpoints, so this can never widen the HTTP surface.
    try:
        requested_git_scopes = _requested_git_scopes(document)
    except GitScopeError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    http_scopes = tuple(sorted(set(http_scopes) | requested_git_scopes))
    # Where git operations on this key go, when the owner said so. Optional and
    # never defaulted per service: an empty value means the endpoint host.
    try:
        git_host = normalize_git_host(document.get("git_host"))
    except GitScopeError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    credential_ref = f"vault://http/{destination}"
    connection_id, grant_id = _ids(universe_id=uid, destination=destination)
    from tinyassets.broker.supervisor import broker_selected

    if broker_selected():
        from tinyassets.broker.http_connect import connect_operation

        policy = {"auth_scheme": scheme, "scopes": list(http_scopes),
                  "endpoints": requested_endpoints, "access_mode": asked_access,
                  "git_host": git_host}
        if mcp_draft is not None:
            policy["mcp_draft"] = mcp_draft
        prepared = connect_operation(base, principal=actor, command_center=uid,
                                     destination=destination, policy=policy, action="prepare")
        if "error" in prepared:
            return prepared
        deposit_error = _deposit_http(uid=uid, actor=actor, destination=destination, secret=secret)
        if deposit_error is not None:
            return deposit_error
        committed = connect_operation(base, principal=actor, command_center=uid,
                                      destination=destination, policy=policy, action="commit",
                                      expected=prepared["revision"])
        return committed if "error" in committed else committed["projection"]
    if mcp_draft is not None:
        return {"error": "mcp_broker_required"}
    ledger = ConnectionLedger(
        Path(base) / "outbound.db",
        verify_authenticated_principal=lambda: actor,
    )

    # 4. Conflict-check the connection AND grant BEFORE depositing anything, so a
    #    mismatch never rotates a credential. Compare EVERY immutable field (Codex
    #    review): a re-provision may only reuse+rotate a policy-identical connection
    #    (same owner/type/class/scheme/scopes/destination/ref/endpoints). The
    #    endpoint allow-list is compared as an unordered set (`_canonical_policy`),
    #    so a pure reorder stays idempotent; any real change (a different endpoint
    #    list, or any field) is a conflict, never a silent reuse of the old policy
    #    under a rotated secret. Changing an existing connection's policy is
    #    done through explicit extension or remove/redeposit, not silent rotation.
    #    Credential-bearing read (trusted
    #    server code); the ref never reaches the projection.
    resource = ledger._get_connection_resource(connection_id)
    # The ONE raw snapshot the extension at the end is guarded by. The git
    # scopes carried forward come from it too, so a scope an extension added
    # between this read and the write makes the CAS fail instead of being
    # dropped by a payload derived from an older read (Codex round 3).
    raw_policy = ledger.policy_json(connection_id) if resource is not None else None
    existing_grant = ledger.get_grant(grant_id)
    plan = _connect_plan(
        resource=resource, raw_policy=raw_policy, existing_grant=existing_grant,
        actor=actor, uid=uid, destination=destination, connection_id=connection_id,
        grant_id=grant_id, scheme=scheme, credential_ref=credential_ref, git_host=git_host,
        requested_endpoints=requested_endpoints, http_scopes=http_scopes)
    if "error" in plan:
        return plan
    http_scopes = plan["http_scopes"]
    legacy_scope_upgrade = plan["legacy_scope_upgrade"]
    endpoints_extend = plan["endpoints_extend"]

    # 5. Deposit (or rotate) the bearer secret into the per-universe vault. The
    #    single `destination` value is both the upsert service key and the
    #    resolver lookup key, so there is exactly one http record per destination.
    #    write_credential_vault is atomic + self-compensating (owner-row txn then
    #    atomic file swap); a malformed record mutates nothing.
    deposit_error = _deposit_http(uid=uid, actor=actor, destination=destination, secret=secret)
    if deposit_error is not None:
        return deposit_error

    # 6. Idempotent create — the ledger validates endpoints (SSRF boundary) and
    #    the http credential-scheme biconditional. Map its errors secret-free.
    if resource is None:
        try:
            resource = ledger.create_connection(
                connection_id=connection_id,
                owner_user_id=actor,
                connection_class="http",
                connection_type="http",
                auth_scheme=scheme,
                scopes=http_scopes,
                provider="http",
                destination=destination,
                credential_ref=credential_ref,
                allowed_endpoints=endpoints,
                # The mode the owner accepted. Defaulting it here stored an
                # exact connection for a full yes (Codex code review round 1).
                access_mode=asked_access,
                git_host=git_host,
            )
        except SsrfValidationError as exc:
            return {"error": "endpoint_not_permitted", "detail": str(exc)}
        except ValueError as exc:
            return {"error": "connection_setup_invalid", "detail": str(exc)}

    # 7a. A full deposit on a connection that already exists moves its mode.
    #     `asked_access` used to be read only inside the create branch, so
    #     rotating a key with a full ask left the connection exact: the owner
    #     read "full access" and got the endpoints they already had (Codex code
    #     review round 2). Compare-and-swap on the whole policy, like every
    #     other mode move.
    if asked_access == ACCESS_FULL and resource is not None:
        # The SAME snapshot the re-provision above is guarded by. A second read
        # here would be a second snapshot, which is the class the one-snapshot
        # rule exists to prevent: a write landing between the two would pass
        # one CAS and be lost by the other.
        if raw_policy is not None and resource.access_mode == ACCESS_EXACT:
            stored_endpoints_json, stored_scopes_json = raw_policy
            if not ledger.set_access_mode(
                connection_id=connection_id,
                access_mode=ACCESS_FULL,
                expected_mode=ACCESS_EXACT,
                expected_endpoints_json=stored_endpoints_json,
                expected_scopes_json=stored_scopes_json,
            ):
                return {"error": "connection_conflict", "resource": "connection"}

    # 7. Idempotent grant bound to the universe.
    grant = existing_grant
    if grant is None:
        grant = ledger.grant_connection(
            grant_id=grant_id,
            connection_id=connection_id,
            owner_user_id=actor,
            universe_id=uid,
            unprompted_action_cap=_HTTP_ACTION_CAP,
        )

    # 8. Bounded legacy-scope migration — applied ONLY now that the grant-conflict
    #    check passed and the credential deposit succeeded above, so any earlier
    #    failure left the legacy ("http",) scope untouched and the connection inert.
    #    The UPDATE is CAS-guarded on the exact legacy token (never a real scope set).
    if legacy_scope_upgrade:
        ledger._upgrade_http_connection_scopes(
            connection_id=connection_id, scopes=http_scopes
        )
        resource = ledger._get_connection_resource(connection_id)

    # 9. Endpoint EXTENSION, applied only now — after the grant-conflict check and
    #    a successful credential deposit — so a failure above leaves the stored
    #    policy exactly as it was. CAS-guarded on the endpoints we read, so a
    #    concurrent deposit that moved the policy makes this a no-op rather than
    #    a clobber; the caller sees the row as it actually stands.
    if endpoints_extend and resource is not None:
        if raw_policy is None:
            return dict(_NOT_FOUND)
        try:
            ledger.extend_http_connection_endpoints(
                connection_id=connection_id,
                endpoints=requested_endpoints,
                scopes=http_scopes,
                expected_endpoints_json=raw_policy[0],
                expected_scopes_json=raw_policy[1],
                git_host=resource.git_host,
            )
        except GitScopeError as exc:
            return {"error": "connection_setup_invalid", "detail": str(exc)}
        except SsrfValidationError as exc:
            return {"error": "endpoint_not_permitted", "detail": str(exc)}
        resource = ledger._get_connection_resource(connection_id)

    return _project(resource, grant)


def remove_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    from tinyassets.onboarding.serving import _gesture_lock

    with _gesture_lock(_request_universe(universe_id)):
        return _remove_http(universe_id=universe_id, payload=payload)


def _remove_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Remove a deposited http connection: the secret, the connection, its grants.

    Shared by approved agent requests and the unpowered Account controls.

    DELETES rather than revokes, and that is the whole design decision. A
    connection id is deterministic on ``(universe_id, destination)``, and
    ``connect_http`` refuses any re-provision of a row whose ``revoked_at`` is
    set, so stamping a revoke would burn that destination name for that universe
    FOREVER: remove ``github`` and you could never deposit ``github`` again. A
    remove the user cannot undo is not a remove, it is a trap.

    First fence dependent authority and custody, then erase the secret before
    deleting ledger rows. A failed cleanup remains denied and reachable for
    retry. In-flight effects may finish; removal cannot cancel upstream work.

    Idempotent: removing something already gone reports ``removed`` with zero
    counts rather than an error, because "take this away" and "it is already
    away" are the same outcome to the caller.
    """
    from tinyassets.api import permissions
    from tinyassets.credential_vault import forget_credential
    from tinyassets.daemon_server import list_universe_acl

    # Same gate as connect_http, deliberately: removing a credential is at
    # least as sensitive as depositing one.
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
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    destination = str(document.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": "destination must name the connection to remove",
        }

    connection_id, grant_id = _ids(universe_id=uid, destination=destination)

    from tinyassets.broker.disconnect import disconnect
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.storage.outbound_connections import GrantResolutionError, _resource_from_row

    selected = broker_selected()
    if selected:
        try:
            snapshot = disconnect(base, principal=actor, command_center=uid,
                                  destination=destination)
        except GrantResolutionError:
            return dict(_NOT_FOUND)
        resource = _resource_from_row(snapshot["resource"]) if snapshot["resource"] else None
        incarnation = snapshot["incarnation"]
    else:
        ledger = ConnectionLedger(
            Path(base) / "outbound.db", verify_authenticated_principal=lambda: actor)
        resource = ledger._get_connection_resource(connection_id)
        incarnation = ledger.incarnation(connection_id)
    if resource is not None and resource.owner_user_id != actor:
        # Mirrors extend_http: an admin may act on the universe, but not on
        # another principal's deposited credential.
        return dict(_NOT_FOUND)

    observed = document.get("incarnation")
    if observed is not None and resource is not None and observed != incarnation:
        return {"error": "connection_changed", "resource": "connection"}
    if document.get("attachment_only") is True:
        if not observed or resource is None:
            return {"error": "connection_changed", "resource": "connection"}
        from tinyassets.mcp_remote import McpError
        from tinyassets.mcp_runtime import detach

        try:
            return detach(_universe_dir(uid), actor, grant_id, connection_id, observed)
        except (McpError, GrantResolutionError):
            return {"error": "connection_changed", "resource": "connection"}
    from tinyassets.providers.connection_lifecycle import complete_disconnect, fence_connection

    if resource is not None:
        fence_connection(base, _universe_dir(uid), owner=actor, uid=uid,
                         connection_id=connection_id, grant_id=grant_id,
                         incarnation=incarnation or "", destination=destination)
        # Deny new direct HTTP dispatch before secret/ledger cleanup; an already
        # dispatched request may still finish, which the receipt states explicitly.
        if not selected:
            ledger.revoke_connection(connection_id)

    # Read the SHAPE before destroying it. Endpoints and git scopes are the two
    # things a re-deposit has to reproduce, and scopes in particular die with
    # the grant -- forget them and the connection comes back looking healthy
    # while every checkout fails. None of this is secret: the owner saw all of
    # it in the grant sentence before they pasted anything.
    removed_endpoints: list[dict[str, Any]] = []
    removed_scopes: list[str] = []
    if resource is not None:
        from tinyassets.storage.workspace_authority import connection_git_scopes

        # `as_dict` is the endpoint's OWN serialization, and it is exactly the
        # shape `_validate_endpoint` parses -- it round-trips by construction.
        #
        # This was hand-written first, and the hand-written version drifted the
        # moment it existed: it dropped `allowed_query` and `required_query`
        # entirely and emitted the pattern maps as stored TUPLES, which the
        # validator refuses with "query_patterns must be an object". A readback
        # that cannot be re-deposited is worse than none, because it looks like
        # a rotation right up until the deposit is refused (Codex, W2).
        #
        # The rule this is an instance of: never hand-write the inverse of a
        # parser that already publishes one.
        removed_endpoints = [
            endpoint.as_dict() for endpoint in (resource.allowed_endpoints or ())
        ]
        try:
            removed_scopes = sorted(
                f"{kind}:{repo}" for kind, repo in connection_git_scopes(resource)
            )
        except Exception:  # noqa: BLE001 - never fail a removal over a readback
            removed_scopes = []

    secrets_removed = forget_credential(
        _universe_dir(uid), credential_type="http", destination=destination
    )
    if selected:
        rows_removed = disconnect(base, principal=actor, command_center=uid,
                                  destination=destination, action="erase",
                                  incarnation=incarnation)["removed"]
    else:
        rows_removed = ledger.delete_connection(connection_id)
    # Everything this key authorized goes with it. The connection id is
    # deterministic per (universe, destination), so a re-deposit under the same
    # name used to inherit the old repository consents -- a grant the owner
    # revoked by removing the key, still active (full-channel-access D6).
    from tinyassets.storage.effector_consents import revoke_consents_for_connection

    removed_consents = revoke_consents_for_connection(
        _universe_dir(uid), connection_id=connection_id, destination=destination
    )
    complete_disconnect(base, owner=actor, uid=uid, connection_id=connection_id)

    return {
        "status": "removed",
        "destination": destination,
        "connection_id": connection_id,
        "grant_id": grant_id,
        "secrets_removed": secrets_removed,
        "connection_removed": bool(rows_removed),
        "in_flight": "Already dispatched requests may finish; their outcomes are unchanged.",
        "upstream": ("Disconnected from TinyAssets; your provider account "
                     "and independent key are unchanged."),
        # What the removal took back, so a ROTATION can re-ask for exactly
        # these and the owner is never asked to remember them. Inheriting them
        # silently was the alternative, and it also survives a removal the
        # owner meant as a revocation.
        "removed_consents": removed_consents,
        # What it looked like, so putting it back does not start from memory.
        # The scheme too: an oauth1a connection re-deposited as the default
        # bearer is a different connection wearing the same name.
        "auth_scheme": getattr(resource, "auth_scheme", "") if resource else "",
        "removed_endpoints": removed_endpoints,
        "removed_scopes": removed_scopes,
        # Say it plainly: the point of deleting rather than revoking is that
        # the name is free again, and the user should not have to infer that.
        "next": (
            f"the destination {destination!r} is free -- deposit it again "
            "whenever you like. To restore it exactly, reuse "
            "'removed_endpoints' and 'removed_scopes' in the new ask: the "
            "scopes went with the grant, and a deposit without them looks "
            "healthy but cannot check anything out"
        ),
    }


def _rotation_target(
    *, uid: str, actor: str, destination: str,
) -> tuple[Any, Any, str, str, Any] | dict[str, Any]:
    """The connection a rotation would act on, or the refusal, with NO write.

    One reader for the write and for the raise-time preview, so a card the rail
    admits is a card the write will honour. Every refusal is the uniform absent
    envelope: this is reachable by any admin of the universe, and a distinct
    "wrong owner" answer would say which destinations exist and who deposited
    them.
    """
    base = _base_path()
    connection_id, grant_id = _ids(universe_id=uid, destination=destination)
    from tinyassets.broker.supervisor import broker_selected

    if broker_selected():
        from tinyassets.broker.ledger_queries import authorized_connection
        from tinyassets.storage.outbound_connections import GrantResolutionError

        try:
            grant, resource, incarnation = authorized_connection(
                base, principal=actor, command_center=uid, grant_id=grant_id,
                connection_id=connection_id)
        except GrantResolutionError:
            return dict(_NOT_FOUND)
    else:
        ledger = ConnectionLedger(
            Path(base) / "outbound.db", verify_authenticated_principal=lambda: actor)
        resource = ledger._get_connection_resource(connection_id)
        grant = ledger.get_grant(grant_id)
        incarnation = ledger.incarnation(connection_id)
    if resource is None or resource.revoked_at is not None:
        # Nothing to rotate. A revoked row is not rotatable either: the deposit
        # door refuses to re-provision one, so a key put into it would be inert.
        return dict(_NOT_FOUND)
    if resource.owner_user_id != actor:
        # Mirrors extend_http and remove_http: an admin may act on the universe,
        # but not on another principal's deposited credential.
        return dict(_NOT_FOUND)
    if (
        resource.connection_type != "http"
        or resource.connection_class != "http"
        or resource.provider != "http"
        or resource.credential_ref != f"vault://http/{destination}"
    ):
        # The rotation writes ONE vault slot: `(http, destination)`. A row that
        # does not read that slot would be reported as rotated while the key it
        # actually presents was untouched -- a success that changed nothing,
        # which is the worst outcome available here (hard rule 8). The deposit
        # door already compares every one of these as an immutable field; not
        # comparing them here would be the inconsistency, not the check.
        return dict(_NOT_FOUND)
    # The connection id is DERIVED from (universe, destination), so another
    # universe naming this destination already addresses its own row. The grant
    # is compared anyway: a derivation is not a check, and a connection with no
    # live grant for this universe is not this universe's to rotate.
    if (
        grant is None
        or grant.connection_id != connection_id
        or grant.owner_user_id != actor
        or grant.universe_id != uid
        or grant.revoked_at is not None
    ):
        return dict(_NOT_FOUND)
    return resource, grant, connection_id, grant_id, incarnation


def _rotation_git_scopes(resource: Any) -> list[str]:
    try:
        return sorted(format_git_scope(kind, repo)
                      for kind, repo in connection_git_scopes(resource))
    except Exception:  # noqa: BLE001 - never fail a rotation over a readback
        return []


def _unpasteable_scheme(scheme: str) -> dict[str, Any] | None:
    """The refusal for an auth scheme no pasted value may replace, or None.

    Fail closed on the SET the deposit door accepts rather than on a list of
    known exceptions: a scheme the engine learns to sign later is not rotatable
    by paste until someone decides it is. Two land here today, and they fail for
    different reasons worth saying out loud --

    * ``oauth2``: its stored value is a token bundle naming the URL every refresh
      token is sent to, so only the owner's own sign-in may write it;
    * ``none``: there is no credential in the request at all, so a pasted value
      would be stored, never sent, and reported as a repair. Codex refute-review,
      P2 #5 -- ``connect_http`` cannot create one, a lower-level path can, and a
      false repair receipt is worse than a refusal (hard rule 8).
    """
    if scheme in _DEPOSITABLE_AUTH_SCHEMES:
        return None
    return {
        "error": "rotation_not_supported",
        "detail": (
            "this connection is completed by signing in, so its authorization "
            "cannot be replaced by pasting one; sign in again"
            if scheme == _SIGN_IN_AUTH_SCHEME else
            f"a {scheme!r} connection has no pasted key to replace"
        ),
        "auth_scheme": scheme,
    }


def preview_rotate_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """What a rotation would act on, read for an ask that has not been raised yet.

    The rail calls this when the agent raises the card, for two reasons. The owner
    must never see a tab that cannot be honoured (the rule ``extend_http`` already
    follows), and the card's boxes have to be named after the auth scheme the
    connection actually STORES -- one for a single-token scheme, the fixed names
    for a multi-value one. Reads only; writes nothing.
    """
    from tinyassets.api import permissions
    from tinyassets.credential_vault import http_deposit_refusal
    from tinyassets.daemon_server import list_universe_acl

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}
    uid = _request_universe(universe_id)
    admin = [
        row
        for row in list_universe_acl(_base_path(), universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return dict(_NOT_FOUND)
    try:
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    destination = str(document.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": "destination must name the connection whose key is being replaced",
        }
    found = _rotation_target(uid=uid, actor=actor, destination=destination)
    if isinstance(found, dict):
        return found
    resource, _grant, connection_id, grant_id, incarnation = found
    # EVERY refusal the write makes for reasons the owner cannot type their way
    # out of, applied here too. The rule this module already follows is that the
    # owner never sees a tab that cannot be honoured; a preview that admitted one
    # would be the same defect as no preview at all (Codex refute-review, P2
    # #4/#5).
    scheme = str(resource.auth_scheme or "").strip().lower()
    refusal = _unpasteable_scheme(scheme)
    if refusal:
        return refusal
    legacy = http_deposit_refusal(
        _universe_dir(uid), destination=destination, owner_user_id=actor,
    )
    if legacy:
        return {
            "error": "credential_ownership_transfer_unsupported",
            "detail": (
                "this destination's credential is owned by another principal"
                if legacy == "foreign_owner" else
                "this destination's stored credential has no recorded depositor, "
                "so it cannot be proved to be yours to replace; removing and "
                "depositing it again is the way back"
            ),
        }
    return {
        "destination": destination,
        "connection_id": connection_id,
        "grant_id": grant_id,
        "auth_scheme": scheme,
        "incarnation": incarnation or "",
        "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
        "git_scopes": _rotation_git_scopes(resource),
        "access": getattr(resource, "access_mode", ACCESS_EXACT) or ACCESS_EXACT,
        "git_host": getattr(resource, "git_host", "") or "",
    }


def rotate_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    from tinyassets.onboarding.serving import _gesture_lock

    with _gesture_lock(_request_universe(universe_id)):
        return _rotate_http(universe_id=universe_id, payload=payload)


def _rotate_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Replace the SECRET of an existing connection, and nothing else.

    Founder-visible failure, 2026-09-16: a connection's key died on the provider
    side (one expired, one was revoked by its user) and the owner had no way to
    put a new one in. The repair paths were ``remove_http`` + ``connect_http``
    re-carrying every endpoint and scope, or a ``connect_http`` whose whole
    allow-list matched the stored one exactly -- and the owner read "remove" as
    deletion, dismissed three such cards, and the connection stayed dead for ten
    days.

    This is the one-step replace. It makes EXACTLY ONE state change: the vault
    upsert for ``(http, destination)``. No ``create_connection``, no
    ``grant_connection``, no ``set_access_mode``, no endpoint extension -- so the
    connection id, grant, endpoints, scopes, access mode, git host, effector
    consents and workspace consents survive by the ABSENCE of a code path rather
    than by care. The next outbound call presents the new secret with no
    invalidation, because the broker child resolves the vault per request.

    Two narrower statements than "it never touches the ledger", because that one
    is not quite true and a claim a reviewer can falsify is worse than a smaller
    one (Codex refute-review, P2 #8): constructing ``ConnectionLedger`` runs the
    schema migration and backfills an empty incarnation, as it does for every
    reader; and the vault upsert READS the existing record in order to merge it,
    though this handler never looks at the old secret itself.

    Every refusal happens before that write, so a refused rotation leaves the old
    secret exactly as it was. The auth scheme is the STORED one: a rotation
    carries none, because a caller who could name it could turn a multi-value
    connection into a bearer one wearing the same name -- and a scheme outside the
    deposit door's own set cannot be replaced by pasting at all
    (``_unpasteable_scheme``).
    """
    from tinyassets.api import permissions
    from tinyassets.credential_vault import (
        http_credential_record,
        http_deposit_refusal,
        write_credential_vault,
    )
    from tinyassets.daemon_server import list_universe_acl

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}

    # Same gate as the deposit and the removal: an explicit admin ACL row for
    # THIS actor on THIS universe, and the uniform absent envelope, so this
    # surface cannot be used to probe which destinations exist.
    uid = _request_universe(universe_id)
    admin = [
        row
        for row in list_universe_acl(_base_path(), universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return dict(_NOT_FOUND)

    try:
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    destination = str(document.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": "destination must name the connection whose key is being replaced",
        }

    secret = document.get("secret")
    if not isinstance(secret, str) or not secret.strip():
        return {"error": "connection_setup_invalid", "detail": "secret is required"}
    if len(secret) > _MAX_SECRET_CHARS:
        return {"error": "connection_setup_invalid", "detail": "secret is too large"}

    found = _rotation_target(uid=uid, actor=actor, destination=destination)
    if isinstance(found, dict):
        return found
    resource, _grant, connection_id, grant_id, incarnation = found

    scheme = str(resource.auth_scheme or "").strip().lower()
    refusal = _unpasteable_scheme(scheme)
    if refusal:
        return refusal
    legacy = http_deposit_refusal(
        _universe_dir(uid), destination=destination, owner_user_id=actor,
    )
    if legacy:
        # The write would refuse this on its own; refusing HERE means the
        # preview refuses it too, so the owner is never shown a card that
        # cannot be honoured (Codex refute-review, P2 #4).
        return {
            "error": "credential_ownership_transfer_unsupported",
            "detail": (
                "this destination's credential is owned by another principal"
                if legacy == "foreign_owner" else
                "this destination's stored credential has no recorded depositor, "
                "so it cannot be proved to be yours to replace; removing and "
                "depositing it again is the way back"
            ),
        }
    shape_error = _secret_shape_error(scheme, secret)
    if shape_error:
        return {"error": "connection_setup_invalid", "detail": shape_error}
    if scheme == _URL_SECRET_SCHEME:
        # A capability URL is rotated by pasting the NEW link, which is what a
        # service hands out when one is regenerated -- so the same extraction as
        # the deposit runs here, against the STORED endpoints. Without it the
        # whole URL would land in the vault and every later call would send
        # `https://host/mcp/hooks/https://host/mcp/hooks/<token>`.
        extracted, extract_error = extract_url_secret(
            secret, resource.allowed_endpoints
        )
        if extract_error:
            return {"error": "connection_setup_invalid", "detail": extract_error}
        secret = extracted

    # Which DEPOSIT this card was raised against. The id and credential_ref are
    # both derived from (universe, destination), so neither changes when a key is
    # removed and a different one put in its place -- the incarnation is the only
    # thing that does.
    observed = document.get("incarnation")
    if observed is not None and observed != incarnation:
        return {"error": "connection_changed", "resource": "connection"}

    try:
        write_credential_vault(
            _universe_dir(uid),
            [http_credential_record(destination=destination, token=secret)],
            owner_user_id=actor,
            universe_id=uid,
        )
    except PermissionError:
        return {
            "error": "credential_ownership_transfer_unsupported",
            "detail": "this destination's credential is owned by another principal",
        }
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    except Exception:  # noqa: BLE001 - fail closed, never leak the secret
        return {"error": "deposit_failed", "resource": "connection"}

    return {
        "status": "rotated",
        "destination": destination,
        "connection_id": connection_id,
        "grant_id": grant_id,
        "auth_scheme": scheme,
        # Read from the row this call did NOT write, so the receipt is evidence
        # that the policy survived rather than a restatement of the request.
        "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
        "git_scopes": _rotation_git_scopes(resource),
        "access": getattr(resource, "access_mode", ACCESS_EXACT) or ACCESS_EXACT,
        "git_host": getattr(resource, "git_host", "") or "",
        "unchanged": (
            "Only the key changed. The connection, its endpoints, its scopes and "
            "its consents are the same ones; nothing needs re-approving."
        ),
        "in_flight": (
            "A request already dispatched with the old key may still finish; "
            "every later call uses the new one."
        ),
    }


def extend_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Add endpoints to an EXISTING connection, reusing the stored credential.

    Founder, 2026-08-28, on being asked to paste the same key a second time:
    *"why would we have the user need to reput the api in again? you said it was
    safe in the vault so why would the user give it again?"*

    They were right, and the answer was that nothing could widen a grant without
    a secret. ``connect_http`` stores one secret PER DESTINATION and requires
    ``secret`` on every call, so both routes to "let it also reach X" — a second
    destination, or extending the first — demanded a key the vault already held.

    This is the missing verb. It touches no vault record at all: the connection
    keeps its existing ``credential_ref``, and only the endpoint allow-list
    grows. The user's authorization is answering the request that asks for it —
    which the agent cannot do for itself, because ``answer_request`` is not on
    the served surface.

    ADDITIVE ONLY, exactly like the deposit path: the new set must be a strict
    superset. Narrowing and replacement stay unsupported here, because taking
    access away is a different intent from granting it and must not ride in on
    an "extend" verb.
    """
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl

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
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    destination = str(document.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": "destination must be 2-127 chars of [a-z0-9._:-] starting "
                      "alphanumeric",
        }
    added = document.get("endpoints")
    # A SCOPE-ONLY widening carries no endpoints at all, which is exactly what
    # the served rail documents for a git scope: the endpoints a workspace
    # checkout needs are none - it does not make an HTTP call. Refusing that
    # shape meant the documented action could never execute (Codex code round
    # 2, new #14). The stored set is what the scopes are then validated
    # against, so nothing is widened by leaving them out.
    try:
        requested_git_scopes = _requested_git_scopes(document)
    except GitScopeError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    try:
        asked_access = normalize_access_mode(document.get("access"))
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    # The same authoring-door refusal as the deposit: a widening must not be the
    # way a hardcoded secret gets into a grant.
    hardcoded = embedded_secret_refusal(added)
    if hardcoded is not None:
        return hardcoded
    scope_only = not isinstance(added, list) or not added
    if scope_only and not requested_git_scopes and asked_access != ACCESS_FULL:
        # A full ask names neither: it is the channel, not a list.
        return {
            "error": "connection_setup_invalid",
            "detail": (
                "endpoints must be a non-empty list, or scopes must name at "
                "least one git scope"
            ),
        }

    preview = _extend_preview(
        actor=actor, base=base, uid=uid, destination=destination,
        added=added, requested_git_scopes=requested_git_scopes,
        access=asked_access,
    )
    if preview.get("error"):
        return preview
    if "expected_git_host" in document and preview.get("status") == "extends":
        # The owner approved a git scope beside a NAMED git host. If the
        # connection now resolves somewhere else, that is not their yes.
        expected_git_host = str(document.get("expected_git_host") or "").strip().lower()
        if not expected_git_host or expected_git_host != preview.get("git_host"):
            return {
                "error": "connection_conflict",
                "resource": "connection",
                "detail": (
                    "this connection's git host is not the one the request "
                    "named; ask again and the tab will name it"
                ),
            }

    redirect_extension = _redirect_permission_requested(added)
    expected_redirect = None
    if redirect_extension:
        expected_redirect = _answered_policy_snapshot(document)
        current_snapshot = {
            "access_mode": preview.get("expected_access_mode"),
            "endpoints_json": preview.get("stored_json"),
            "scopes_json": preview.get("stored_scopes_json"),
            "incarnation": preview.get("stored_incarnation"),
        }
        if (
            not expected_redirect or not expected_redirect.get("incarnation")
            or expected_redirect != current_snapshot
        ):
            return {
                "error": "connection_conflict", "resource": "connection",
                "detail": "Redirect permission needs fresh approval of the displayed connection.",
            }
    if preview.get("status") == "unchanged":
        return preview

    if preview["ledger"] is None:
        from tinyassets.broker.http_policy import read_policy, update_policy

        full = preview.get("access") == ACCESS_FULL and not redirect_extension
        expected = ((_answered_policy_snapshot(document) if full else expected_redirect)
                    or {"access_mode": preview["expected_access_mode"],
                        "endpoints_json": preview["stored_json"],
                        "scopes_json": preview["stored_scopes_json"],
                        "incarnation": preview["stored_incarnation"]})
        updated = update_policy(
            base, principal=actor, command_center=uid, destination=destination,
            expected=expected, action="full" if full else "extend",
            endpoints=() if full else preview["merged"],
            scopes=() if full else preview["scopes"],
            git_host=preview.get("declared_git_host") or "")
        if not updated:
            return {"error": "connection_conflict", "resource": "connection"}
        resource, _grant, _snapshot = read_policy(
            base, principal=actor, command_center=uid, destination=destination)
        return {"status": "extended", "destination": destination,
                "access": resource.access_mode,
                "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
                "scopes": list(resource.scopes), "secret_reused": True}

    ledger = preview["ledger"]
    connection_id = preview["connection_id"]
    if preview.get("access") == ACCESS_FULL and not redirect_extension:
        # The whole write: one mode, compare-and-swapped on the policy THE
        # OWNER READ. When the ask carries the snapshot it rendered its
        # sentence from, that is what the swap compares -- not this call's own
        # fresh read, which would happily grant full on a host added while the
        # tab sat open (Codex code review round 2, left open then).
        expected = _answered_policy_snapshot(document) or {
            "access_mode": preview["expected_access_mode"],
            "endpoints_json": preview["stored_json"],
            "scopes_json": preview["stored_scopes_json"],
            "incarnation": preview.get("stored_incarnation") or "",
        }
        if not ledger.set_access_mode(
            connection_id=connection_id,
            access_mode=ACCESS_FULL,
            expected_mode=expected["access_mode"],
            expected_endpoints_json=expected["endpoints_json"],
            expected_scopes_json=expected["scopes_json"],
            # Which DEPOSIT the owner was answering about. Without it a key
            # removed and replaced under the same destination with an identical
            # policy matched every predicate (Codex code review round 3, P0).
            expected_incarnation=expected["incarnation"] or None,
        ):
            return {
                "error": "connection_conflict",
                "resource": "connection",
                "detail": (
                    "this connection changed while the request was open, so the "
                    "yes was not applied to a reach you did not see; ask again "
                    "and the tab will name what is there now"
                ),
            }
        resource = ledger._get_connection_resource(connection_id)
        return {
            "status": "extended",
            "destination": destination,
            "access": ACCESS_FULL,
            "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
            "scopes": list(resource.scopes),
            "secret_reused": True,
        }
    try:
        widened = ledger.extend_http_connection_endpoints(
            connection_id=connection_id,
            endpoints=preview["merged"],
            scopes=preview["scopes"],
            expected_endpoints_json=preview["stored_json"],
            expected_scopes_json=preview["stored_scopes_json"],
            git_host=preview.get("declared_git_host") or "",
            **({
                "expected_access_mode": expected_redirect["access_mode"],
                "expected_incarnation": expected_redirect["incarnation"],
                "expected_grant_id": _ids(universe_id=uid, destination=destination)[1],
            } if expected_redirect else {}),
        )
    except GitScopeError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    except SsrfValidationError as exc:
        return {"error": "endpoint_not_permitted", "detail": str(exc)}
    if not widened:
        # A concurrent deposit moved the policy after we read it.
        return {"error": "connection_conflict", "resource": "connection"}

    resource = ledger._get_connection_resource(connection_id)
    return {
        "status": "extended",
        "destination": destination,
        "access": preview.get("access", ACCESS_EXACT),
        "allowed_endpoints": [e.as_dict() for e in resource.allowed_endpoints],
        "scopes": list(resource.scopes),
        "secret_reused": True,
    }


def _extend_preview(
    *,
    actor: str,
    base: Any,
    uid: str,
    destination: str,
    added: Any,
    requested_git_scopes: frozenset[str],
    access: str = "exact",
) -> dict[str, Any]:
    """Everything ``extend_http`` decides BEFORE it writes, as one function.

    The request rail calls this when the agent RAISES an ask, and
    ``extend_http`` calls it when the owner answers -- so the two cannot
    disagree. They did: on 2026-09-02 the founder's universe raised an ask that
    added a ``github.com`` endpoint to a connection whose endpoints were all on
    ``api.github.com``; the ask was accepted, the tab rendered, and the
    founder's click on yes was refused by the ledger's one-host rule for git
    scopes -- worded for the agent, shown to the founder, and recorded as a
    "Not now" they never chose.

    Returns an error envelope, ``{"status": "unchanged", ...}`` when the ask
    adds nothing the connection does not already hold, or ``{"status":
    "extends", ...}`` carrying exactly what the write needs.
    """
    from tinyassets.storage.workspace_authority import (
        git_host_for_endpoints,
        validate_git_scopes,
    )

    connection_id, grant_id = _ids(universe_id=uid, destination=destination)
    from tinyassets.broker.supervisor import broker_selected

    selected = broker_selected()
    redirect_snapshot = None
    if selected:
        from tinyassets.broker.http_policy import read_policy
        from tinyassets.storage.outbound_connections import GrantResolutionError

        try:
            resource, grant, redirect_snapshot = read_policy(
                base, principal=actor, command_center=uid, destination=destination)
        except GrantResolutionError:
            return dict(_NOT_FOUND)
        ledger = None
    else:
        ledger = ConnectionLedger(
            Path(base) / "outbound.db", verify_authenticated_principal=lambda: actor)
        resource = ledger._get_connection_resource(connection_id)
        grant = ledger.get_grant(grant_id)
    redirect_extension = _redirect_permission_requested(added)
    if redirect_extension:
        try:
            _parse_allowed_endpoints(added)
        except (SsrfValidationError, ValueError, TypeError) as exc:
            return {"error": "endpoint_not_permitted", "detail": str(exc)}
        if normalize_access_mode(access) == ACCESS_FULL:
            return {
                "error": "connection_setup_invalid",
                "detail": "Request redirect endpoints separately from full channel access.",
            }
        if not selected:
            captured = ledger._resource_policy_snapshot(connection_id)
            if captured is None:
                return dict(_NOT_FOUND)
            resource, redirect_snapshot = captured
    if resource is None or resource.owner_user_id != actor:
        # Nothing to extend, or not this principal's connection. Uniform
        # envelope so this cannot be used to probe which destinations exist.
        return dict(_NOT_FOUND)
    # The connection must ALSO be reachable through an active grant for this
    # owner on this universe -- the same condition `read_graph
    # target=connections` lists by. Without it an orphaned or revoked
    # connection, invisible to the inventory, answered `already_held` with its
    # endpoints and scopes: a new oracle for the served agent (Codex on the
    # 2026-09-02 rail change).
    if (
        grant is None
        or grant.revoked_at is not None
        or grant.owner_user_id != actor
        or grant.universe_id != uid
        or grant.connection_id != connection_id
    ):
        return dict(_NOT_FOUND)
    if resource.revoked_at is not None:
        # The inventory still lists a revoked resource behind an active grant;
        # this path names the state instead, because there is nothing to
        # extend on a revoked key and "not found" would send the agent to
        # re-deposit under the same name, which the ledger refuses.
        return {"error": "connection_conflict", "resource": "connection"}
    # ONE snapshot. Everything the write is derived from -- the stored
    # endpoints, the stored scopes, the host the git rule binds to -- comes
    # from the same raw read the CAS compares against. Deriving the union from
    # an earlier parsed read and the CAS from a later raw read let a write
    # that landed between them pass the CAS and be lost (Codex round 2).
    raw_policy = (
        (redirect_snapshot["endpoints_json"], redirect_snapshot["scopes_json"])
        if redirect_snapshot else ledger.policy_json(connection_id)
    )
    if raw_policy is None:
        return dict(_NOT_FOUND)
    stored_json, stored_scopes_json = raw_policy
    try:
        stored = list(json.loads(stored_json))
        stored_scope_list = [str(s) for s in json.loads(stored_scopes_json)]
    except (TypeError, ValueError) as exc:
        return {"error": "connection_setup_invalid", "detail": f"stored policy unreadable: {exc}"}

    # The mode verdicts, derived from that SAME snapshot -- never from the
    # parsed resource, which is a second read (full-channel-access D4, and the
    # one-snapshot rule Codex established for this function).
    stored_hosts = sorted({
        str(e.get("host") or "").strip().lower()
        for e in stored
        if isinstance(e, dict) and str(e.get("host") or "").strip()
    })
    stored_mode = normalize_access_mode(resource.access_mode)
    asked_mode = normalize_access_mode(access)
    if stored_mode == ACCESS_FULL and not redirect_extension:
        # The channel is already granted whole, so nothing an extension could
        # name adds anything -- a full re-ask and an exact ask alike. The agent
        # is told it holds the channel and acts, instead of asking the owner a
        # question with no answer.
        return {
            "status": "unchanged",
            "destination": destination,
            "access": ACCESS_FULL,
            "hosts": stored_hosts,
            "allowed_endpoints": stored,
            "scopes": stored_scope_list,
        }
    if stored_mode == ACCESS_FULL and redirect_extension:
        if requested_git_scopes or any(
            e.host not in stored_hosts for e in _parse_allowed_endpoints(added)
        ):
            return {
                "error": "connection_setup_invalid",
                "detail": "Redirect permission cannot add authenticated hosts or scopes "
                          "to full channel access.",
            }
    if asked_mode == ACCESS_FULL:
        # exact -> full: the WRITE is the mode, compare-and-swapped on the mode
        # this snapshot read. No endpoint or scope row changes, which is the
        # whole point of the shape: nothing is stored as a wildcard.
        return {
            "status": "extends",
            "destination": destination,
            "connection_id": connection_id,
            "ledger": ledger,
            "access": ACCESS_FULL,
            "expected_access_mode": stored_mode,
            "stored_json": stored_json,
            "stored_scopes_json": stored_scopes_json,
            "stored_incarnation": (redirect_snapshot["incarnation"] if redirect_snapshot
                                   else ledger.incarnation(connection_id) or ""),
            "git_host": git_host_for_endpoints(stored_hosts, resource.git_host),
            "declared_git_host": resource.git_host,
            "hosts": stored_hosts,
            "allowed_endpoints": stored,
            "scopes": stored_scope_list,
        }

    scope_only = not isinstance(added, list) or not added
    try:
        # Scope-only: the connection keeps exactly the endpoints it has. They
        # still go through the parser, because they are what the ledger will
        # validate the git scopes' host rule against.
        merged = _parse_allowed_endpoints(stored if scope_only else [*stored, *added])
    except SsrfValidationError as exc:
        return {"error": "endpoint_not_permitted", "detail": str(exc)}
    except (ValueError, TypeError) as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    # One row per distinct endpoint. The union used to keep duplicates, so an
    # ask that repeated an endpoint the connection already had stored it twice
    # (the founder's github connection carried three such pairs on 2026-09-02).
    merged_dicts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for endpoint in merged:
        as_dict = endpoint.as_dict()
        key = _canonical_policy([as_dict])
        if key in seen:
            continue
        seen.add(key)
        merged_dicts.append(as_dict)
    stored_git_scopes = _git_scopes_in(stored_scopes_json)
    new_git_scopes = requested_git_scopes - stored_git_scopes
    scopes = tuple(
        sorted(
            {m for e in merged for m in e.methods}
            | requested_git_scopes
            | stored_git_scopes
        )
    )
    if redirect_extension:
        scopes = tuple(stored_scope_list) if stored_mode == ACCESS_FULL else tuple(
            sorted(set(stored_scope_list) | set(scopes))
        )
    snapshot_fields = {
        "expected_access_mode": redirect_snapshot["access_mode"],
        "stored_json": redirect_snapshot["endpoints_json"],
        "stored_scopes_json": redirect_snapshot["scopes_json"],
        "stored_incarnation": redirect_snapshot["incarnation"],
    } if redirect_snapshot else {}
    # The ledger's own rule, run here so an ask that would fail at the write
    # fails at the RAISE, with the reason going to the agent that can act on it.
    try:
        validate_git_scopes(
            scopes, hosts=[e.host for e in merged], git_host=resource.git_host
        )
    except GitScopeError as exc:
        asked_hosts = sorted({
            str(e.get("host") or "").strip().lower()
            for e in (added if isinstance(added, list) else [])
            if isinstance(e, dict)
        } - {""})
        return {
            "error": "connection_setup_invalid",
            "detail": str(exc),
            "git_host": git_host_for_endpoints(
                [str(e.get("host") or "") for e in stored], resource.git_host
            ),
            "declared_git_host": resource.git_host,
            "asked_hosts": asked_hosts,
        }
    # "Nothing new" has to account for a scope-only widening: adding
    # git_read:owner/name to a connection whose endpoints already cover what it
    # needs changes no endpoint at all, and short-circuiting on endpoints alone
    # left that ask with no route through this verb.
    if (
        _canonical_endpoint_set(merged_dicts) <= _canonical_endpoint_set(stored)
        and not new_git_scopes
    ):
        return {"status": "unchanged", "destination": destination,
                "access": stored_mode if redirect_extension else ACCESS_EXACT,
                "allowed_endpoints": stored,
                "scopes": stored_scope_list, **snapshot_fields}
    return {
        "status": "extends",
        "access": stored_mode if redirect_extension else ACCESS_EXACT,
        "destination": destination,
        "connection_id": connection_id,
        "ledger": ledger,
        "stored": stored,
        "stored_json": stored_json,
        "stored_scopes_json": stored_scopes_json,
        "merged": merged_dicts,
        "scopes": scopes,
        "allowed_endpoints": merged_dicts,
        "git_host": git_host_for_endpoints(
            [str(e.get("host") or "") for e in stored], resource.git_host
        ),
        "declared_git_host": resource.git_host,
        **snapshot_fields,
    }


def preview_extend_http(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """What answering an ``extend_http`` ask WOULD do, without doing it.

    Same authentication, same admin gate, same parse and the same one-host
    rule as :func:`extend_http`; nothing is written. The request rail uses it
    to refuse an ask when it is raised (with the reason, to the agent) and to
    answer an ask that adds nothing with ``unchanged`` instead of a tab.
    """
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl

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
        document = _payload(payload)
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    destination = str(document.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        return {
            "error": "connection_setup_invalid",
            "detail": "destination must be 2-127 chars of [a-z0-9._:-] starting "
                      "alphanumeric",
        }
    try:
        requested_git_scopes = _requested_git_scopes(document)
    except GitScopeError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    try:
        asked_access = normalize_access_mode(document.get("access"))
    except ValueError as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    preview = _extend_preview(
        actor=actor, base=base, uid=uid, destination=destination,
        added=document.get("endpoints"), requested_git_scopes=requested_git_scopes,
        access=asked_access,
    )
    # Serializable projection only: never the ledger or the resource. The
    # three policy-snapshot fields ARE included: the raise records what it
    # rendered its sentence from so the answer can swap against exactly that,
    # and they carry no more than `allowed_endpoints` and `scopes` already do.
    return {
        key: value for key, value in preview.items()
        if key in ("error", "resource", "detail", "status", "destination",
                   "allowed_endpoints", "scopes", "git_host", "asked_hosts",
                   "access", "hosts",
                   "expected_access_mode", "stored_json", "stored_scopes_json",
                   "stored_incarnation")
    }


__all__ = ["connect_http", "extend_http", "preview_extend_http"]
