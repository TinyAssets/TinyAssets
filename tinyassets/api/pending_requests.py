"""The agent asks its user something; the app shows it as a tab; the user answers.

Founder, 2026-08-27:

    "pending-request should show up as tabs on the left side screen of the app,
    the hedder notates what it is like api in this case you tap/click them to
    expand and in this case paist in the api right there. the agent can
    construct these pending requests really how ever he likes so they can be
    used in clever ways by the agent. it should also have a way for it to be a
    notification on the phone and addressible from there also"

One primitive, not a credential feature
---------------------------------------
The agent composes ``kind`` (the tab header), ``title``, ``body`` and ``fields``,
so a kind nobody wrote code for still renders and still works. "I need an API
key" is simply the first kind.

Two actions exist, and the difference is where the answer goes:

* ``{"type": "connect_http", ...}`` — the answer is a credential. It goes
  straight to the vault through :func:`~tinyassets.api.http_connection.connect_http`
  under the endpoint policy stored ON THE REQUEST, so what the user was shown is
  exactly what gets granted. Nothing secret is recorded here.
* ``{"type": "connect", ...}`` — ``connect_http`` plus what the connection is
  USED for (``uses.call`` / ``uses.model{wire, models, billing}``) and its
  non-secret ``constant_headers``. One answer deposits the key, creates the
  connection and grant, records the uses, and - when the universe has no model
  yet and this one serves models - makes it the universe's model. An LLM is
  just another connection (founder, 2026-09-24). When the provider offers
  OAuth for what the connection needs (found by standard discovery, or from
  ``oauth`` connection data on the ask), signing in is the request's primary
  action and key paste the fallback (founder, 2026-09-24: "prefer OAuth when
  the provider allows for what the request is trying to accomplish").
* ``{"type": "answer"}`` — the answer is ordinary data the agent reads back.

Nothing is inferred anywhere in this flow. For a credential the agent already
knows the endpoint it is about to call, so it states it; there is no model
guessing a host from a pasted secret (contrast
:mod:`tinyassets.api.connection_inference`), and therefore nothing to fence
against a paste steering it.

The boundary that makes "however he likes" safe
-----------------------------------------------
A ``secret`` field is accepted ONLY on a deposit request (``connect_http`` or
``connect``), and a secret value is never stored. Without that, an agent — including one steered by
injected content — could compose a friendly-looking request that asks for a
password and lands it in readable storage. Generality is the feature; this is
what keeps it from being a harvesting primitive.

Addressable from any surface, including a phone, because the request lives
server-side and is read through the same ``read_graph`` every surface already
speaks. A phone *notification* additionally needs device registration, which
does not exist yet — see the concern filed alongside this module.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from tinyassets.credential_shape import looks_like_credential

#: One definition, imported rather than repeated. ``tinyassets.patch_intake``
#: pulls in nothing from ``tinyassets`` at import time, so this import
#: cannot cycle.
from tinyassets.patch_intake import ACTION_TYPE as PATCH_INTAKE_ACTION

logger = logging.getLogger(__name__)

# Authority-bearing answers require the protected HTTP owner's proof, including
# refusal/recovery decisions. Never derive this proof from an answer payload.
CONSENT_ACTIONS = frozenset({
    "publish", "install", "connect", "connect_http", "extend_http", "rotate_http",
    "remove_http", "grant_workspace_consent", "bind_model_access", PATCH_INTAKE_ACTION,
    "start_activity", "approve_action",
})
# System-created approve_action and notify use dedicated branches before the
# general gate; classify them too so creation paths cannot escape the inventory.
NON_CONSENT_ACTIONS = frozenset({"answer", "notify"})
REQUEST_RECOVERY_DETAIL = (
    "Clear or decline closes this ask; it is not a mute, for any ask kind. "
    "When the need recurs or the user asks again, raise a new pending_request "
    "with operation=ask and the same action and fields. Only don't ask again "
    "mutes: respect the muted list until the user lifts that choice. "
    "Connect and reconnect can also be started from the connection controls. "
    "Do not promise never to ask again after a plain clear."
)
CONSENT_REQUIRED_DETAIL = (
    "Open the approval sheet in the app to answer this request in the protected owner session. "
    "Bearer, chatbot, MCP and CLI answers are not consent."
)

_MAX_KIND_CHARS = 24
_MAX_TITLE_CHARS = 120
_MAX_BODY_CHARS = 600
#: How many fields one request may carry.
#:
#: Was 6, which is fewer than several real services need: an OAuth 1.0a deposit
#: (X/Twitter) is an API key, its secret, an access token, ITS secret and often
#: a bearer token -- five before anything optional. A cap that cannot express
#: the ask forces the agent back to one box labelled "paste the key", which is
#: the guessing this is meant to end. Still bounded, because the rail renders
#: these to a person.
_MAX_FIELDS = 16

#: Room for "Settings -> Developer portal -> Keys and tokens -> Generate", which
#: a 120-character label cannot hold.
_MAX_HELP_CHARS = 400

#: Auth schemes whose stored credential holds SEVERAL values, mapped to the
#: field names the deposit reads. `bearer` and `header` are one token and are
#: absent on purpose.
#:
#: `basic` is here because refusing it stranded ordinary username/password
#: services outright: the ask was refused, the inference swallowed that into an
#: empty list, and the fieldless ask was refused in turn — so a Basic API could
#: not be asked for at all (Codex round 2, Q4). It is two boxes joined as
#: `username:password`, not JSON, because that is the encoding the vault string
#: has always used.
#:
#: The names are FIXED for these schemes and the deposit reads them: label them
#: however the service words them, but name them these. `oauth1a` mirrors
#: `_OAUTH1A_FIELDS` in `api/http_connection.py`, and a test pins the two
#: together so they cannot drift.
_MULTI_VALUE_FIELD_NAMES = {
    "oauth1a": ("api_key", "api_secret", "access_token", "access_token_secret"),
    "basic": ("username", "password"),
}
_MULTI_VALUE_AUTH_SCHEMES = frozenset(_MULTI_VALUE_FIELD_NAMES)

#: The asks whose answer is a credential for the vault. ``connect`` is
#: ``connect_http`` with the connection's uses declared on it.
_DEPOSIT_TYPES = frozenset({"connect_http", "connect"})

#: Every ask whose answer is a secret, which is a WIDER set than the asks that
#: create a connection: ``rotate_http`` replaces the secret of one that already
#: exists. The boundary a ``secret`` field is allowed on is this set, and the
#: reason it exists is unchanged -- the value goes to the vault through a typed
#: handler and is never recorded as an answer. Keeping ``_DEPOSIT_TYPES`` to mean
#: "creates a connection" leaves everything that branches on it unchanged.
_SECRET_FIELD_TYPES = _DEPOSIT_TYPES | {"rotate_http"}

#: A plain https link, no userinfo (`https://user:pw@host`), bounded.
_MAX_URL_CHARS = 8192
_SAFE_URL_RE = re.compile(r"^https://[^\s/@]+(?:/[^\s]*)?$")
#: A dotted-quad or bracketed-v6 host. See :func:`_unusable_field_url`.
_IP_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
#: This platform's own hosts. A field link to one of these is checked against
#: the pages the site serves, because an invented first-party page reads as
#: platform help (live 2026-09-30: an agent offered ``/settings``, a 404).
_FIRST_PARTY_HOSTS = frozenset({"tinyassets.io", "www.tinyassets.io"})
#: Every route ``WebSite/site-react/app`` serves, plus the app itself. Kept in
#: sync by ``tests/test_request_card_layout_and_links.py``, which reads that
#: directory and fails when a page is added or removed without this list.
_FIRST_PARTY_PATHS = frozenset({
    "", "/", "/account", "/alliance", "/build", "/catalog", "/commons",
    "/connect", "/contribute", "/developers", "/economy", "/fine-print",
    "/goal", "/goals", "/graph", "/host", "/legal", "/loop", "/notebook",
    "/patch-loop", "/patterns", "/proof", "/soul", "/start", "/status",
    "/wiki", "/mcp", "/app",
})
_MAX_ANSWER_CHARS = 2000
#: Every verb the egress layer knows. The owner reads each one on the tab and
#: decides; a cap below the full set only made the agent raise a second ask.
_MAX_REQUEST_METHODS = 5
#: Bounded because the rail renders these to a person, not to keep an ask
#: small: the owner decides what is too much (founder 2026-09-02, on limits).
_MAX_REQUEST_ENDPOINTS = 40
#: Git scopes one ask may carry.
_MAX_REQUEST_GIT_SCOPES = 40

#: Feedback, reasons and notes are free text stored in the clear, so they get
#: the same screen the resolver applies -- a SHAPE screen, not a word screen.
#: What was here (``[A-Za-z0-9_\-]{16,}``) had ``-`` inside its class, so
#: ``self-authenticating`` was a "16+ character unbroken run" and a universe was
#: refused twice, live 2026-09-30, for explaining in plain words that there was
#: no token to paste. See :mod:`tinyassets.credential_shape`.


def _payload(value: Any) -> dict[str, Any]:
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("payload_json must be a JSON object")
    return document


def _bad(detail: str) -> dict[str, Any]:
    return {"error": "request_invalid", "detail": detail}


def _owner_gate(universe_id: str):
    """``(uid, udir, None)`` when the caller owns this universe, else an envelope.

    Mirrors ``connect_http``: an explicit ``admin`` ACL row for THIS actor, never
    the permissive ``universe_access_allows`` helper, and the same uniform
    absent-resource envelope so this surface cannot be used to probe existence.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe, _universe_dir
    from tinyassets.api.http_connection import _NOT_FOUND
    from tinyassets.daemon_server import list_universe_acl

    unauth = {"error": "authentication_required", "resource": "pending_request"}
    if not permissions.is_authenticated_request():
        return None, None, unauth
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return None, None, unauth
    uid = _request_universe(universe_id)
    admin = [
        row
        for row in list_universe_acl(_base_path(), universe_id=uid)
        if row.get("actor_id") == actor and row.get("permission") == "admin"
    ]
    if not admin:
        return None, None, dict(_NOT_FOUND)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return None, None, dict(_NOT_FOUND)
    return uid, udir, None


def _coordinated(fn):
    @wraps(fn)
    def wrapped(*, universe_id="", **kwargs):
        from tinyassets.owner_control import ControlUnavailable, control
        from tinyassets.storage.request_migration import ensure_protected

        _, home, denied = _owner_gate(universe_id)
        if denied is not None:
            return denied
        try:
            with control(home):
                ensure_protected(home)
                return fn(universe_id=universe_id, **kwargs)
        except ControlUnavailable as exc:
            return {"error": exc.kind, "retryable": True, "detail": str(exc)}
    return wrapped


def _validated_action(raw: Any) -> dict[str, Any]:
    """Normalize the action, validating a credential policy the same way the
    deposit will — so a request can never describe a grant the deposit refuses,
    and a user is never shown a tab that cannot be honoured."""
    from tinyassets.api.http_connection import (
        _DEPOSITABLE_AUTH_SCHEMES,
        _DESTINATION_RE,
    )
    from tinyassets.storage.outbound_connections import _URL_SECRET_SCHEME

    action = raw if isinstance(raw, dict) else {"type": "answer"}
    kind = str(action.get("type") or "answer").strip().lower()
    if kind == "answer":
        return {"type": "answer"}
    if kind == "bind_model_access":
        from tinyassets.api.model_access_requests import validate_action

        return validate_action({**action, "type": kind})
    if kind == "grant_workspace_consent":
        return _validated_workspace_consent(action)
    if kind == "publish":
        from tinyassets.api.publish_requests import validate_action as _validate_publish

        return _validate_publish({**action, "type": kind})
    if kind == "install":
        from tinyassets.api.package_requests import validate_action as _validate_install

        return _validate_install({**action, "type": kind})
    if kind == PATCH_INTAKE_ACTION:
        return _validated_patch_intake(action)
    if kind == "extend_http":
        # Widening a grant the user already funded. No secret is involved: the
        # vault keeps the one they deposited, and answering this request IS the
        # authorization. Endpoints are validated by the same allow-list.
        destination = str(action.get("destination") or "").strip().lower()
        if not _DESTINATION_RE.match(destination):
            raise ValueError(
                "destination must be 2-127 chars of [a-z0-9._:-] starting "
                "alphanumeric"
            )
        if _validated_access(action) == "full":
            # One yes for the whole channel (full-channel-access D1). It carries
            # no endpoints and no scopes: naming some would say the owner is
            # granting those, and they are granting everything the key can do.
            if action.get("endpoints") or action.get("scopes"):
                raise ValueError(
                    'access "full" covers the whole channel, so it may not also '
                    "carry endpoints or scopes"
                )
            return {
                "type": "extend_http",
                "destination": destination,
                "endpoints": [],
                "scopes": [],
                "access": "full",
            }
        # A scope-only widening carries no endpoints: a git scope needs none,
        # and the served rail documents exactly that shape. The deposit
        # validates it against the endpoints the connection ALREADY has, which
        # is the only set that could vouch for the host anyway.
        raw_endpoints = action.get("endpoints")
        scope_only = (
            (not isinstance(raw_endpoints, list) or not raw_endpoints)
            and action.get("scopes")
            and not action.get("host")
        )
        endpoints = [] if scope_only else _validated_endpoint_list(action)
        return {
            "type": "extend_http",
            "destination": destination,
            "endpoints": endpoints,
            "scopes": _validated_git_scopes(action, endpoints, host_checked=not scope_only),
            "access": "exact",
        }
    if kind == "remove_http":
        # TAKING BACK a key the owner deposited. No secret and no endpoints: the
        # destination names what goes, and answering the request IS the
        # authorization -- the same shape as extend_http, pointed the other way.
        #
        # This is an ASK rather than a served write_graph operation on purpose.
        # The served surface refuses every target but `branch` and
        # `pending_request` precisely so the agent cannot touch a connection on
        # its own, and a destructive act is the last one to carve an exception
        # for. Deposit and take-back now land in the same rail, so the owner
        # confirms the removal where they confirmed the deposit.
        destination = str(action.get("destination") or "").strip().lower()
        if not _DESTINATION_RE.match(destination):
            raise ValueError(
                "destination must be 2-127 chars of [a-z0-9._:-] starting "
                "alphanumeric"
            )
        return {"type": "remove_http", "destination": destination}
    if kind == "rotate_http":
        # REPLACING the secret of a key the owner already deposited, because the
        # far side stopped accepting it. The destination names which one; nothing
        # else is on the ask, and that is the point. No endpoints (they are not
        # changing), no scopes (same), and NO auth_scheme: a caller who could name
        # one could turn a multi-value connection into a single-token one wearing
        # the same name. The stored scheme is read when this ask is raised and
        # recorded then, so what the owner is shown is what the write reads.
        destination = str(action.get("destination") or "").strip().lower()
        if not _DESTINATION_RE.match(destination):
            raise ValueError(
                "destination must be 2-127 chars of [a-z0-9._:-] starting "
                "alphanumeric"
            )
        for unwanted in ("endpoints", "scopes", "access", "auth_scheme", "hosts"):
            if action.get(unwanted):
                raise ValueError(
                    "a rotate_http ask replaces only the key: it carries a "
                    f"destination and nothing else (got {unwanted!r}). To change "
                    "what the key may reach, that is extend_http"
                )
        return {"type": "rotate_http", "destination": destination}
    if kind == "connect":
        return _validated_connect(action)
    if kind != "connect_http":
        raise ValueError(
            "action type must be answer, connect, connect_http, extend_http, "
            "rotate_http, remove_http, grant_workspace_consent, "
            f"{PATCH_INTAKE_ACTION}, bind_model_access or publish"
        )

    destination = str(action.get("destination") or "").strip().lower()
    if not _DESTINATION_RE.match(destination):
        raise ValueError(
            "destination must be 2-127 chars of [a-z0-9._:-] starting alphanumeric"
        )
    scheme = str(action.get("auth_scheme") or "bearer").strip().lower()
    if scheme not in _DEPOSITABLE_AUTH_SCHEMES:
        raise ValueError(
            "auth_scheme must be one of " + ", ".join(sorted(_DEPOSITABLE_AUTH_SCHEMES))
        )
    # One request may cover SEVERAL exact endpoints. A GitHub pull request needs
    # three calls (create a ref, put contents, open the pull), and one path per
    # request would mean pasting the same key three times. A list of named exact
    # paths is still least privilege -- it is not a widening, and the user sees
    # every line before pasting once.
    if _validated_access(action) == "full":
        if scheme == _URL_SECRET_SCHEME:
            # A capability URL's authority IS one declared path. `full` admits
            # every other path on the host once the host matches, so the
            # reserved placeholder would never be enforced and the secret would
            # have nowhere to live. Refused at the ask so the owner never reads
            # a tab the deposit will not honour.
            raise ValueError(
                f'a {_URL_SECRET_SCHEME} ask names the endpoint the link points '
                'at, so it cannot be "full"'
            )
        # A new key has no stored hosts yet, so a full deposit names the
        # channel's host(s). One GET endpoint per host is recorded so the
        # existing host derivation and the SSRF host pin have something to read;
        # the AUTHORITY is the mode, not those rows (full-channel-access D1).
        if action.get("endpoints"):
            raise ValueError(
                'access "full" names the channel with "hosts", not endpoints'
            )
        if action.get("scopes"):
            raise ValueError(
                'access "full" covers every repository the key reaches, so it '
                "may not also carry scopes"
            )
        endpoints = _validated_full_host_endpoints(action)
        return {
            "type": "connect_http",
            "destination": destination,
            "auth_scheme": scheme,
            "endpoints": endpoints,
            "scopes": [],
            "access": "full",
            "hosts": [endpoint["host"] for endpoint in endpoints],
            **_validated_git_host(action),
        }
    endpoints = _validated_endpoint_list(action)
    git_host = _validated_git_host(action)
    if git_host and not action.get("scopes"):
        # A declared git host is where the owner's key is SENT for git. On an
        # exact ask with no git scope it authorizes nothing, so it would be a
        # destination stored without a purpose -- and later scope-only
        # extensions would inherit it (Tier 2 review round 1, BLOCK).
        raise ValueError(
            "git_host names where git sends this key; an exact ask with no git "
            "scope has no use for it -- add the git scopes it is for, or drop it"
        )
    return {
        "type": "connect_http",
        "destination": destination,
        "auth_scheme": scheme,
        "endpoints": endpoints,
        "scopes": _validated_git_scopes(
            action, endpoints, git_host=git_host.get("git_host", "")
        ),
        "access": "exact",
        **git_host,
    }


def _validated_patch_intake(action: dict[str, Any]) -> dict[str, Any]:
    """The platform's own "may I report gaps to <intake>?" ask.

    It carries a receiver id and a display label and NOTHING else -- no
    endpoints, no scheme, no secret -- because the connection it creates is one
    grant naming one receiver. An extra key is refused rather than dropped: the
    tab's promise is what gets granted, so a field nobody validated must not
    ride along on the row the answer executes.

    The receiver id is validated for SHAPE here. Whether it is the intake this
    deployment offers is re-checked at answer time against the configuration,
    because a stored row outlives the value it was created under.
    """
    from tinyassets.patch_intake import (
        _MAX_LABEL_CHARS,
        _RECEIVER_ID_RE,
        DEFAULT_LABEL,
    )

    receiver_id = str(action.get("receiver_id") or "").strip()
    if not _RECEIVER_ID_RE.match(receiver_id):
        raise ValueError(
            "receiver_id must be 8-128 characters of [A-Za-z0-9._:-] naming one "
            "receiver"
        )
    label = str(action.get("label") or DEFAULT_LABEL).strip()
    if not label or len(label) > _MAX_LABEL_CHARS or not label.isprintable():
        raise ValueError(
            f"label must be 1-{_MAX_LABEL_CHARS} printable characters on one line"
        )
    extra = sorted(set(action) - {"type", "receiver_id", "label"})
    if extra:
        raise ValueError(
            f"a {PATCH_INTAKE_ACTION} ask carries a receiver_id and a label and "
            "nothing else (got " + ", ".join(repr(name) for name in extra) + ")"
        )
    return {"type": PATCH_INTAKE_ACTION, "receiver_id": receiver_id, "label": label}


def _validated_git_host(action: dict[str, Any]) -> dict[str, str]:
    """``{"git_host": host}`` when the ask declares where git goes, else ``{}``.

    Optional on any deposit, for any service: a forge whose git transport is
    not its API host says so here, and the owner reads it in the grant. There
    is no per-service default.
    """
    from tinyassets.storage.workspace_authority import GitScopeError, normalize_git_host

    try:
        host = normalize_git_host(action.get("git_host"))
    except GitScopeError as exc:
        raise ValueError(str(exc)) from None
    return {"git_host": host} if host else {}


def _validated_connect(action: dict[str, Any]) -> dict[str, Any]:
    """``connect`` = the ``connect_http`` deposit + ``uses`` + ``constant_headers``.

    The deposit half is validated by the very same code as ``connect_http``, so
    the two cannot drift. A model use needs somewhere to POST inference.
    """
    from tinyassets.api.connection_uses import (
        ConnectionUseError,
        validate_constant_headers,
        validate_uses,
    )
    from tinyassets.connection_oauth.discovery import validate_request

    deposit = _validated_action({**action, "type": "connect_http"})
    try:
        uses = validate_uses(action.get("uses"))
        headers = validate_constant_headers(action.get("constant_headers"))
    except ConnectionUseError as exc:
        raise ValueError(str(exc)) from None
    # What the agent knows about signing in (endpoints, issuer, scopes). It is
    # REPLACED by the resolved offer before the ask is stored, so a caller can
    # never claim an offer was discovered.
    oauth_request = validate_request(action.get("oauth"))
    if "model" in uses and deposit["access"] == "exact" and not any(
        "POST" in (endpoint.get("methods") or []) for endpoint in deposit["endpoints"]
    ):
        raise ValueError(
            "a model use needs a POST endpoint for inference (the model URL path)"
        )
    return {**deposit, "type": "connect", "uses": uses, "constant_headers": headers,
            "oauth_request": oauth_request}


def _has_sign_in(action: dict[str, Any]) -> bool:
    """A connect ask resolved through trusted directory data or host discovery.

    ``source`` is written only by ``resolve_offer`` (a requester cannot supply
    it), so an offer without it is never trusted.
    """
    offer = action.get("oauth") if isinstance(action, dict) else None
    return (action.get("type") == "connect" and isinstance(offer, dict)
            and offer.get("source") in ("discovered", "directory")
            and bool(offer.get("authorize_url")) and bool(offer.get("token_url")))


def _with_sign_in_offer(
    action: dict[str, Any], sign_in_hosts: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve whether the provider offers OAuth for this connection.

    Returns the action to store (with ``oauth`` = the offer when there is one)
    and what to tell the requester. Discovery never fails the ask: without an
    offer the ask is a key paste, and the reason says why.
    """
    from tinyassets.connection_oauth.discovery import resolve_offer
    from tinyassets.connection_oauth.flow import configured_redirect_uri

    requested = action.pop("oauth_request", {}) or {}
    # ``sign_in_hosts`` are installed data from the platform's own source card
    # (an issuer that is not the inference host), tried first. Server-set like
    # ``origin``: never read from the payload, so a requester cannot root
    # discovery anywhere its declared endpoints do not already reach.
    hosts = list(dict.fromkeys(
        [str(h) for h in sign_in_hosts]
        + [str(e.get("host") or "") for e in action.get("endpoints") or []]
        + [str(h) for h in action.get("hosts") or []]
    ))
    offer, reason = resolve_offer(requested, [h for h in hosts if h])
    if offer is None:
        return action, {"oauth_unavailable": reason}
    note: dict[str, Any] = {"primary": "sign_in"}
    callback = configured_redirect_uri()
    if callback:
        # A client registered by hand must list exactly this redirect URI.
        note["redirect_uri"] = callback
    return {**action, "oauth": offer}, note


#: A channel is 1-4 hosts. More than that is not one channel; it is a request
#: to reach several services on one key, which the owner should see separately.
_MAX_FULL_HOSTS = 4

#: What a full deposit is missing when it names no host.
HOSTS_REQUIRED = 'access "full" requires "hosts": the channel\'s host(s)'


def _validated_access(action: dict[str, Any]) -> str:
    """``"full"`` or ``"exact"``. Any other value is a refusal rather than a
    silent downgrade: an agent that mistypes the field must be told, not
    quietly given less than it asked the owner for."""
    raw = action.get("access")
    if raw is None:
        return "exact"
    text = str(raw).strip().lower()
    if text not in ("full", "exact"):
        raise ValueError('access must be "full" or "exact"')
    return text


def _validated_full_host_endpoints(action: dict[str, Any]) -> list[dict[str, Any]]:
    """The 1-4 hosts a full deposit declares, as endpoint rows the deposit
    parser has already accepted.

    Running them through that parser is the point: "full" can never reach a
    host the endpoint validator would have refused (an IP literal, a private
    name, a percent-encoded host), because the same function decides. The rows
    exist so host derivation and the SSRF host pin have something to read; the
    authority is the connection's mode, never these rows.
    """
    from tinyassets.api.http_connection import _parse_allowed_endpoints

    raw = action.get("hosts")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError(HOSTS_REQUIRED)
    if len(raw) > _MAX_FULL_HOSTS:
        raise ValueError(f"a channel may name at most {_MAX_FULL_HOSTS} hosts")
    rows = [
        {
            "host": str(entry or "").strip().lower(),
            "path_template": "/{path+}",
            "methods": ["GET"],
            "param_patterns": {"path": ".*"},
        }
        for entry in raw
    ]
    parsed = _parse_allowed_endpoints(rows)   # raises on anything the deposit refuses
    seen: list[dict[str, Any]] = []
    for endpoint in parsed:
        if any(row["host"] == endpoint.host for row in seen):
            continue
        seen.append({
            "host": endpoint.host,
            "path_template": endpoint.path_template,
            "methods": list(endpoint.methods),
            "param_patterns": {name: pattern for name, pattern in endpoint.param_patterns},
        })
    return seen


def _validated_git_scopes(
    action: dict[str, Any],
    endpoints: list[dict[str, Any]],
    *,
    host_checked: bool = True,
    git_host: str = "",
) -> list[str]:
    """The git scopes an http ask may carry, validated the way the deposit will.

    A git scope is the one authority an endpoint list cannot express: which
    repository a credentialed clone or push may touch. It is checked against the
    SAME endpoint hosts here as at the deposit, so a tab can never promise a
    scope the deposit would then refuse - the user would have answered a
    question that grants nothing.
    """
    from tinyassets.storage.workspace_authority import (
        endpoints_allow_git_scopes,
        format_git_scope,
        is_git_scope,
        require_git_scope,
    )

    raw = action.get("scopes")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("scopes must be a list of git scopes")
    if len(raw) > _MAX_REQUEST_GIT_SCOPES:
        raise ValueError(
            f"a request may cover at most {_MAX_REQUEST_GIT_SCOPES} git scopes"
        )
    scopes: list[str] = []
    for value in raw:
        if not is_git_scope(value):
            raise ValueError(
                "scopes accepts git scopes only (git_read:owner/name, "
                "git_write:owner/name); the HTTP methods come from the endpoints"
            )
        scopes.append(format_git_scope(*require_git_scope(value)))
    # ``host_checked=False`` is the scope-only widening: this ask names no
    # endpoints, so there is nothing here to check the host against and the
    # DEPOSIT checks the connection's stored set instead. Skipping it here
    # cannot widen anything - the ledger refuses the write either way.
    if host_checked and scopes and not endpoints_allow_git_scopes(
        [str(endpoint.get("host") or "") for endpoint in endpoints], git_host
    ):
        raise ValueError(
            "a git scope needs every endpoint of the same ask to be on ONE host "
            "(any host), or a git_host on the connect ask naming where git lives"
        )
    return sorted(set(scopes))


def _validated_workspace_consent(action: dict[str, Any]) -> dict[str, Any]:
    """``grant_workspace_consent``: the typed yes for one repository.

    The scope says the credential MAY reach the repository; this says the owner
    agreed to this kind of work on it. Separate on purpose - a key deposited for
    an API call is not a standing agreement to check the repository out, run its
    code and push to it.
    """
    from tinyassets.storage.workspace_authority import (
        WORKSPACE_CONSENTS,
        normalize_repo,
    )

    connection_id = str(action.get("connection_id") or "").strip()
    if not connection_id or len(connection_id) > 200:
        raise ValueError(
            "grant_workspace_consent needs the connection_id the key was "
            "deposited under"
        )
    repo = normalize_repo(action.get("repo"))
    raw = action.get("consents")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "consents must name at least one of " + ", ".join(WORKSPACE_CONSENTS)
        )
    consents = sorted({str(value).strip().lower() for value in raw})
    unknown = [value for value in consents if value not in WORKSPACE_CONSENTS]
    if unknown:
        raise ValueError(
            "unknown consent(s) " + ", ".join(unknown) + "; expected "
            + ", ".join(WORKSPACE_CONSENTS)
        )
    return {
        "type": "grant_workspace_consent",
        "connection_id": connection_id,
        "repo": repo,
        "consents": consents,
    }


def _validated_endpoint_list(action: dict[str, Any]) -> list[dict[str, Any]]:
    """Shared endpoint validation for connect_http and extend_http."""
    from tinyassets.api.http_connection import (
        _parse_allowed_endpoints,
        embedded_secret_refusal,
    )

    raw_endpoints = action.get("endpoints")
    if not isinstance(raw_endpoints, list) or not raw_endpoints:
        raw_endpoints = [action]          # the single-endpoint shorthand
    if len(raw_endpoints) > _MAX_REQUEST_ENDPOINTS:
        raise ValueError(
            f"a request may cover at most {_MAX_REQUEST_ENDPOINTS} endpoints; "
            "ask for the calls you actually need"
        )
    endpoints = []
    for raw in raw_endpoints:
        if not isinstance(raw, dict):
            raise ValueError("each endpoint must be an object")
        methods = raw.get("methods")
        if not isinstance(methods, list) or not methods:
            methods = ["POST"]
        methods = sorted({str(m).strip().upper() for m in methods if str(m).strip()})
        if len(methods) > _MAX_REQUEST_METHODS:
            raise ValueError(
                f"an endpoint may cover at most {_MAX_REQUEST_METHODS} methods; "
                "ask for the one action you need on it"
            )
        endpoint = {
            "host": str(raw.get("host") or action.get("host") or "").strip().lower(),
            "path_template": str(raw.get("path_template") or "").strip(),
            "methods": methods,
        }
        # Carry the pattern keys through. This rebuild used to keep only the
        # three fields above, which silently stripped ``param_patterns`` - and
        # a ``{name}`` / ``{name+}`` placeholder WITHOUT its pattern is refused
        # by the deposit parser ("must declare exactly the path placeholders").
        # So the exact job-scoped ask the agent is taught to raise
        # (``contents/{path+}``) was rejected at ask time, and the agent was told
        # its correctly shaped request was invalid (found 2026-08-29, before it
        # reached a live test). The parser below still validates every one of
        # these; nothing is trusted here, only forwarded.
        for key in ("param_patterns", "allowed_query", "query_patterns", "required_query"):
            if raw.get(key) is not None:
                endpoint[key] = raw[key]
        # Presence matters: null/unknown modes must fail validation, not be
        # silently dropped and displayed as a different permission request.
        if "redirect_mode" in raw:
            endpoint["redirect_mode"] = raw["redirect_mode"]
        endpoints.append(endpoint)
    # The hardcoded-secret refusal at the ASK door, which is the door the agent
    # meets first. Raising it here means the correction reaches the agent while
    # it is still composing the card, not after a person has read it: live
    # 2026-09-30 a universe put a friend's webhook secret into path_template and
    # nothing in the chain said a word.
    hardcoded = embedded_secret_refusal(endpoints)
    if hardcoded is not None:
        raise ValueError(str(hardcoded["detail"]))
    parsed = _parse_allowed_endpoints(endpoints)   # same validation as deposit
    if any(endpoint.redirect_mode == "public_https_get" for endpoint in parsed):
        from tinyassets.api.http_connection import _canonical_policy

        # Consent for the additional capability is semantic, not input-order
        # dependent. Keep legacy no-follow request identities byte-compatible.
        return json.loads(_canonical_policy([endpoint.as_dict() for endpoint in parsed]))
    for endpoint in endpoints:
        if endpoint.get("redirect_mode") == "none":
            endpoint.pop("redirect_mode")
    return endpoints


def _unusable_field_url(url: str) -> str:
    """Why this agent-composed field link cannot be offered, or ``""``.

    Cheap, local checks only -- no fetch of an arbitrary third-party URL from
    this process. Two classes, both live findings on 2026-09-30:

    * a **raw address**. "Get it from 203.0.113.7" tells the owner nothing about
      who they are about to trust while they hold a secret.
    * an **invented first-party page**. The agent rendered "Get it from
      tinyassets.io" over ``https://tinyassets.io/settings``, a path that does
      not exist -- a dead end, and, styled as though the platform said it, a
      phishing shape. First-party links are allow-listed against the pages the
      site actually serves; everything else is the agent's own suggestion and is
      labelled as such in the app.
    """
    host = urlsplit(url).hostname or ""
    host = host.strip().strip(".").lower()
    if not host:
        return "url must name a host"
    if _IP_HOST_RE.match(host) or ":" in host:
        return (
            "url must name a hostname, not a raw address -- the owner has to be "
            "able to see whose page they are opening"
        )
    if host in _FIRST_PARTY_HOSTS:
        path = "/" + urlsplit(url).path.strip("/")
        if path.rstrip("/").lower() not in _FIRST_PARTY_PATHS:
            return (
                f"there is no {path} page on this site; a credential for another "
                "service is not found here, so link that service's own page (or "
                "leave url out and say where to look in 'help')"
            )
    return ""


def _validated_fields(
    raw: Any, action: dict[str, Any], *, has_items: bool = False,
) -> list[dict[str, Any]]:
    from tinyassets.api.http_connection import URL_SECRET_FIELD_NAME
    from tinyassets.storage.outbound_connections import _URL_SECRET_SCHEME
    from tinyassets.storage.pending_requests import FIELD_TYPES

    fields = raw if isinstance(raw, list) else []
    if action["type"] == "bind_model_access":
        if raw not in (None, []):
            raise ValueError("model access is a fieldless owner confirmation")
        return []
    if not fields and has_items:
        # The answerable parts ARE the items, each with its own fields, so an
        # itemised note has nothing to type at the top level. Without this an
        # ask with items and no top-level fields is refused outright ("a
        # request needs at least one field"), which is every multi-item
        # request (verified against the real handler, 2026-09-30).
        return []
    if not fields and _has_sign_in(action):
        # Signing in IS the answer; key fields, when present, are the fallback.
        return []
    if not fields:
        # NO unlabelled fallback for a credential ask.
        #
        # This used to synthesise one box labelled "Paste the key", which is
        # exactly the guessing the founder ruled out on 2026-08-31: "no more the
        # user having to guess what they need to put where. each single
        # indevidual credential will have its own indevidually labled request".
        # Leaving it in place would also make the new path untestable -- an ask
        # that forgot its fields would still LOOK fine, so a green test would
        # prove nothing about whether the agent had done the work.
        #
        # The agent knows what the service needs; if it does not, that is the
        # thing to fix, not paper over with a box the owner has to interpret.
        if action["type"] in _SECRET_FIELD_TYPES:
            raise ValueError(
                "a credential request needs one field per value the service "
                "asks for, each with the label THAT SERVICE uses (and ideally "
                "'help' saying where to find it and a 'url' to that page) -- "
                "not one unlabelled box for the owner to work out"
            )
        if action["type"] in ("extend_http", "remove_http", "grant_workspace_consent",
                              PATCH_INTAKE_ACTION, "publish", "install"):
            # Nothing to type. For extend_http the key is already in the vault
            # and for remove_http it is on its way out; either way this is a
            # yes/no, and a paste box on a removal would be nonsense.
            return []
        else:
            raise ValueError("a request needs at least one field")
    if len(fields) > _MAX_FIELDS:
        raise ValueError(f"a request may have at most {_MAX_FIELDS} fields")
    out: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("each field must be an object")
        name = str(field.get("name") or "").strip()
        if not name or len(name) > 40:
            raise ValueError("each field needs a name of at most 40 chars")
        if any(existing["name"] == name for existing in out):
            # Duplicate names collide as DOM ids: the browser clears the first
            # control twice and leaves the second holding its value, which for a
            # secret field means a credential left in the page (Codex 2026-08-27,
            # reproduced headless).
            raise ValueError("field names must be unique within a request")
        ftype = str(field.get("type") or "text").strip().lower()
        if ftype not in FIELD_TYPES:
            raise ValueError("field type must be one of " + ", ".join(sorted(FIELD_TYPES)))
        if ftype == "secret" and action["type"] not in _SECRET_FIELD_TYPES:
            # THE boundary. Without it, "compose requests however you like"
            # becomes a way to ask for a password and store it in the clear.
            raise ValueError(
                "a secret field is only allowed on a connect_http request (or "
                "connect, or rotate_http), so the value goes to the vault "
                "instead of being recorded as an answer"
            )
        entry = {
            "name": name,
            "label": str(field.get("label") or name).strip()[:120],
            "type": ftype,
        }
        # `help` and `url` exist so a credential ask can be ANSWERABLE.
        #
        # Founder, 2026-08-31: "no more the user having to guess what they need
        # to put where. each single indevidual credential will have its own
        # indevidually labled request that uses what the agent found online as
        # what that sight uses for calling its credentials". A label of at most
        # 120 characters cannot carry "Settings -> Developer -> Keys and tokens,
        # then Generate", and there was nowhere at all to put the link.
        #
        # The AGENT fills these in from what it knows about the service, which
        # is why there is no table of services here: a site nobody has heard of
        # gets the same quality of ask as a famous one.
        help_text = str(field.get("help") or "").strip()
        if help_text:
            entry["help"] = help_text[:_MAX_HELP_CHARS]
        url = str(field.get("url") or "").strip()
        if url:
            # HTTPS only, and no credentials in the URL. This is rendered to the
            # owner as something to click while they are being asked for a
            # secret, so a `javascript:` or `data:` value, or a link carrying a
            # userinfo section, is refused rather than sanitised -- the caller
            # is composing it and should be told it was wrong.
            # LENGTH FIRST. `_MAX_URL_CHARS` was defined and never enforced,
            # so a 10,000-character hostname matched the pattern and was stored
            # and rendered while the owner typed a secret (Codex, Q6).
            if len(url) > _MAX_URL_CHARS or not _SAFE_URL_RE.match(url):
                raise ValueError(
                    f"field {name!r}: url must be a plain https:// link "
                    "(no credentials in it, at most "
                    f"{_MAX_URL_CHARS} chars)"
                )
            unusable = _unusable_field_url(url)
            if unusable:
                raise ValueError(f"field {name!r}: {unusable}")
            entry["url"] = url
        if ftype == "choice":
            options = [str(o).strip()[:60] for o in (field.get("options") or []) if str(o).strip()]
            if not options:
                raise ValueError("a choice field needs options")
            entry["options"] = options[:8]
        out.append(entry)
    if action["type"] in _SECRET_FIELD_TYPES:
        if not any(f["type"] == "secret" for f in out):
            raise ValueError(
                f"a {action['type']} request needs a secret field for the key"
            )
        # EVERY field on a credential ask is a secret. A non-secret field's
        # answer is recorded in `answer_json` and relayed back into chat, so an
        # ask that labelled one value `text` -- "API token", typed as text --
        # would persist that credential in the clear. Harmless when a credential
        # ask was one box; a live hazard now that asks carry four or five
        # (Codex, Q1). If a request genuinely needs a non-secret answer, that is
        # a different ask.
        plain = [f["name"] for f in out if f["type"] != "secret"]
        if plain:
            raise ValueError(
                "every field on a credential request must be type 'secret' -- "
                + ", ".join(repr(name) for name in plain)
                + " would be stored and shown in the clear"
            )
        # And several values only make sense where the vault string HAS a
        # multi-value encoding. `basic` is `username:password` and `bearer` /
        # `header` are one token, so assembling several into JSON would hand the
        # service `Basic base64({"user":...})` -- a credential that cannot
        # authenticate, failing at the far end with nothing to point at.
        secrets = [f for f in out if f["type"] == "secret"]
        scheme = str(action.get("auth_scheme") or "bearer").strip().lower()
        if scheme == _URL_SECRET_SCHEME:
            # ONE box, and it holds the WHOLE LINK. The live failure asked for
            # "the code at the end of the link", which makes a person parse a
            # URL and then sent that value as a header. The platform parses it
            # (`extract_url_secret`), so the field must be the link -- and it is
            # named the way the deposit reads it, like `oauth1a`'s four.
            if len(secrets) != 1 or secrets[0]["name"] != URL_SECRET_FIELD_NAME:
                raise ValueError(
                    f"a {_URL_SECRET_SCHEME} card has exactly ONE secret field, "
                    f"named {URL_SECRET_FIELD_NAME!r}, and the owner pastes the "
                    "WHOLE link into it -- label it the way the service words it "
                    '("Webhook URL"), and never ask them to pick the code out '
                    "of it themselves"
                )
        if len(secrets) > 1 and scheme not in _MULTI_VALUE_AUTH_SCHEMES:
            raise ValueError(
                f"auth_scheme {scheme!r} takes a single value, so ask for one "
                "field; several are only meaningful for "
                + ", ".join(sorted(_MULTI_VALUE_AUTH_SCHEMES))
            )
        expected = _MULTI_VALUE_FIELD_NAMES.get(scheme)
        if expected and len(secrets) > 1:
            # Caught HERE, not at deposit: the deposit reads these names, and
            # finding out afterwards means the owner has already filled the form
            # and is told "oauth1a secret is missing: api_secret" about a box
            # they cannot see. The LABEL stays the service's wording.
            got = tuple(f["name"] for f in secrets)
            if sorted(got) != sorted(expected):
                raise ValueError(
                    f"auth_scheme {scheme!r} reads fixed field names "
                    + ", ".join(expected)
                    + " -- got " + ", ".join(got)
                    + " (label them the service's way, name them these)"
                )
        if expected and action["type"] == "rotate_http" and len(secrets) != len(expected):
            # For a ROTATION the scheme is not the agent's claim -- it was read
            # off the connection when the ask was raised. So the box count is
            # knowable here, and a one-box card for a four-value scheme must not
            # reach the owner: the write would refuse the assembled value only
            # after they had pasted it.
            raise ValueError(
                f"this connection stores a {scheme!r} credential, so the card "
                "needs one secret field per value, named " + ", ".join(expected)
            )
    return out


def _notify_owner(uid: str, row: dict[str, Any]) -> None:
    """Tell the owner's devices about a request that was just STORED.

    Only a genuinely new pending row reaches here: a deduplicated ask returns
    the existing row and a settled one returns a decision, and neither is a
    new thing to be told about. The actor is the one ``_owner_gate`` already
    verified, and dispatch re-checks it against the universe's admin owner
    rather than trusting it.

    Best effort by construction. Delivery is additive to a request that is
    already durable and already in the rail, so nothing here may fail, delay
    or alter the ask that caused it.
    """
    try:
        from tinyassets.api import permissions
        from tinyassets.api.helpers import _base_path
        from tinyassets.owner_notifications import notify_request_raised

        notify_request_raised(
            _base_path(), universe_id=uid,
            raised_by=permissions.current_actor_id(), request=row,
        )
    except Exception:  # noqa: BLE001 - the ask is already stored; telling is a bonus
        logger.warning(
            "pending_requests: could not notify the owner of %s",
            row.get("request_id"), exc_info=True,
        )


_ITEM_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_MAX_ITEM_TITLE_CHARS = 120
_MAX_ITEM_BODY_CHARS = 400


def _validated_items(raw: Any, action: dict[str, Any]) -> list[dict[str, Any]]:
    """The request's answerable items, or ValueError naming what is wrong.

    One request that holds several things -- a note listing today's tasks --
    instead of one tab per thing, which is what destroyed the grouping that
    made it a note. Each item is answerable on its own.

    ``item_id`` is the AGENT's, kept verbatim, because the universe has to
    correlate an answer back to the thing it planned without re-reading the
    request to learn what it just asked. It is a handle and never authority:
    every read and write re-derives the principal, exactly as ``request_id``
    does.

    Items are only available on an ``answer`` action, and an item's fields go
    through the same validator the request's own do -- which is what refuses a
    ``secret`` field inside one. That is THE boundary from this module's
    docstring, one level down: an item can never be the thing that deposits a
    credential or widens a grant, so "compose them however you like" stays
    safe at item granularity too.
    """
    from tinyassets.storage.pending_requests import MAX_ITEMS

    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("items must be a list of objects")
    if str(action.get("type") or "answer") != "answer":
        raise ValueError(
            "items are only available on an 'answer' request; an action that "
            "deposits or changes a grant is one decision, not a checklist"
        )
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"a request may have at most {MAX_ITEMS} items")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each item must be an object")
        item_id = str(item.get("item_id") or "").strip()
        if not _ITEM_ID_RE.fullmatch(item_id):
            raise ValueError(
                "each item needs an item_id of 1-64 lowercase letters, digits, "
                "'_', '.' or '-', starting with a letter or digit -- you choose "
                f"it and you read it back; got {item_id!r}"
            )
        if any(existing["item_id"] == item_id for existing in out):
            # Duplicate ids would make one answer ambiguous, and the answer
            # table's primary key would silently drop the second.
            raise ValueError(f"item ids must be unique within a request: {item_id!r}")
        title = str(item.get("title") or "").strip()[:_MAX_ITEM_TITLE_CHARS]
        if not title:
            raise ValueError(f"item {item_id!r} needs a title")
        entry: dict[str, Any] = {"item_id": item_id, "title": title}
        body = str(item.get("body") or "").strip()[:_MAX_ITEM_BODY_CHARS]
        if body:
            entry["body"] = body
        if item.get("fields"):
            try:
                entry["fields"] = _validated_fields(item["fields"], {"type": "answer"})
            except ValueError as exc:
                raise ValueError(f"item {item_id!r}: {exc}") from exc
        else:
            # Nothing to type: an item the owner accepts, denies or replies to.
            entry["fields"] = []
        out.append(entry)
    return out


def try_package(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Raise the existing install ask; only the trusted owner surface can answer it."""
    uid, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    try:
        definition_id = _payload(payload).get("agent_definition_id")
    except (ValueError, TypeError) as exc:
        return _bad(str(exc))
    if not isinstance(definition_id, str) or not definition_id.strip():
        return _bad("agent_definition_id is required")
    # A share link may name an older immutable release outside the discovery
    # shortlist. The normal capture below validates that exact public package
    # or system and pins its owner approval; catalogue pagination is not auth.
    ask = request_from_user(universe_id=uid, payload=json.dumps({"action": {
        "type": "install", "agent_definition_id": definition_id,
    }}))
    if "error" in ask:
        return ask
    return {"request_id": ask["request_id"], "title": ask["title"]}


@_coordinated
def request_from_user(
    *, universe_id: str = "", payload: Any = None, origin: str = "agent",
    sign_in_hosts: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The agent raises a tab. Writes no credential.

    ``origin`` is server-set (keyword only, never read from ``payload``): the
    platform's own asks pass ``"platform"`` so the agent cannot withdraw them.
    ``sign_in_hosts`` is server-set too: an installed source card's issuer host,
    where sign-in discovery starts (see :func:`_with_sign_in_offer`).
    """
    from tinyassets.storage.pending_requests import create_request

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    try:
        document = _payload(payload)
    except ValueError as exc:
        return _bad(str(exc))

    kind = str(document.get("kind") or "").strip()[:_MAX_KIND_CHARS]
    title = str(document.get("title") or "").strip()[:_MAX_TITLE_CHARS]
    body = str(document.get("body") or "").strip()[:_MAX_BODY_CHARS]
    raw_type = str((document.get("action") or {}).get("type") or "").strip().lower() \
        if isinstance(document.get("action"), dict) else ""
    if raw_type == "approve_action":
        from tinyassets.bound_requests import RequestRefused, capture
        try:
            return capture(udir, document["action"].get("pending_action"))
        except (RequestRefused, ValueError) as exc:
            return _bad(str(exc))
    if raw_type in _PINNED_ACTIONS:
        # The platform writes these tabs itself; whatever the agent sent is replaced.
        kind, title = kind or raw_type, title or raw_type
    if not kind:
        return _bad("kind is the tab header (e.g. 'API'); it is required")
    if not title:
        return _bad("title is required; the user is being asked for something")
    sign_in: dict[str, Any] = {}

    def _refused(exc: Exception) -> dict[str, Any]:
        reason = sign_in.get("oauth_unavailable")
        return _bad(str(exc) + (f" (no sign-in is offered: {reason})" if reason else ""))

    try:
        action = _validated_action(document.get("action"))
        if action.get("type") == "connect":
            action, sign_in = _with_sign_in_offer(action, tuple(sign_in_hosts))
    except ValueError as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001 - endpoint validator
        return {"error": "endpoint_not_permitted", "detail": str(exc)}
    if action.get("type") == "rotate_http":
        # BEFORE the fields are validated, because the connection's stored auth
        # scheme is what decides how many boxes the card has and what they are
        # named -- and the fields check reads it off the action.
        verdict = _rotate_ask_verdict(_uid, action)
        if verdict.get("error"):
            return verdict
        action = {**action, **verdict}
    try:
        items = _validated_items(document.get("items"), action)
    except ValueError as exc:
        return _bad(str(exc))
    try:
        fields = _validated_fields(
            document.get("fields"), action, has_items=bool(items),
        )
    except ValueError as exc:
        return _refused(exc)

    if action.get("type") == "bind_model_access":
        from tinyassets.api.model_access_requests import capture_action
        from tinyassets.storage.current_home import CurrentHomeChanged

        try:
            action = capture_action(_uid, action)
        except (ValueError, LookupError, PermissionError, CurrentHomeChanged) as exc:
            return _bad(str(exc))
    if action.get("type") == "publish":
        # The consent is the PLATFORM's words about what it pinned: the agent's
        # own kind/title/body are replaced, and the ask carries no fields.
        from tinyassets.api.publish_requests import capture_action as _capture_publish
        from tinyassets.api.publish_requests import tab_text, toggle_fields

        if fields:
            return _bad("a publish ask's fields are the platform's, not the agent's")
        try:
            action = _capture_publish(_uid, action)
        except (ValueError, LookupError, PermissionError) as exc:
            return _bad(str(exc))
        kind, title, body = tab_text(action)
        fields, toggles = toggle_fields(action)
        if toggles:
            action = {**action, "toggles": toggles}
    if action.get("type") == "install":
        # Quarantine: the package is verified and planned, and nothing lands in
        # this command center until the owner answers. The tab is the platform's.
        from tinyassets.api.package_requests import capture_action as _capture_install
        from tinyassets.api.package_requests import tab_text as _install_tab

        if fields:
            return _bad("an install ask is a fieldless owner confirmation")
        try:
            action = _capture_install(_uid, action)
        except (ValueError, LookupError, PermissionError) as exc:
            return _bad(str(exc))
        kind, title, body = _install_tab(action)

    if action.get("type") == "connect" and "model" in (action.get("uses") or {}):
        refused = _model_use_refusal(_uid, action)
        if refused is not None:
            return refused
    if action.get("type") == "grant_workspace_consent":
        host = _owned_connection_git_host(action["connection_id"])
        if not host:
            # Uniform with the answer path: never name "the connection's host".
            return {"error": "not_found", "resource": "connection"}
        action = {**action, "host": host}
    if action.get("type") == "remove_http":
        from tinyassets.api.helpers import _base_path
        from tinyassets.api.http_connection import _ids
        from tinyassets.storage.outbound_connections import ConnectionLedger

        connection_id, _ = _ids(universe_id=_uid, destination=action["destination"])
        action = {**action, "incarnation": ConnectionLedger(
            _base_path() / "outbound.db").incarnation(connection_id) or "absent"}
    if action.get("type") == "extend_http":
        captured_preview: dict[str, Any] = {}
        held = _extend_ask_verdict(_uid, action, captured_preview=captured_preview)
        if held is not None:
            return held
        if _grants_git(action):
            # The owner must read WHERE git will send the key before saying yes
            # to a git scope (Tier 2 review round 1, BLOCK): the stored
            # connection's resolved git host, from the same preview that
            # admitted the ask.
            scope_host = str(captured_preview.get("git_host") or "").strip()
            if not scope_host:
                return _bad(
                    "this connection names no git host, so a git scope on it "
                    "cannot be granted; reconnect it with git_host"
                )
            action = {**action, "git_scope_host": scope_host}
        if any(e.get("redirect_mode") == "public_https_get" for e in action["endpoints"]):
            # Capture the same preview that admitted the ask. Agent-supplied
            # snapshots are never accepted by action normalization.
            snapshot = {
                "access_mode": captured_preview.get("expected_access_mode"),
                "endpoints_json": captured_preview.get("stored_json"),
                "scopes_json": captured_preview.get("stored_scopes_json"),
                "incarnation": captured_preview.get("stored_incarnation"),
            }
            if not all(isinstance(v, str) and v for v in snapshot.values()):
                return _bad("Could not capture this connection's redirect approval policy.")
            action = {**action, "policy_snapshot": snapshot}
        if action.get("access") == "full":
            # The sentence has to name the hosts this key actually reaches, and
            # only the CONNECTION knows them -- a full ask carries no endpoints
            # by design. Read once, here, so the row the owner sees and the row
            # stored for the audit trail say the same thing.
            action = {**action, **_full_channel_reach(_uid, action)}

    if action.get("type") in {"connect_http", "connect", "extend_http"} and any(
        e.get("redirect_mode") == "public_https_get" for e in action.get("endpoints", [])
    ):
        # This server version generated the redirect disclosure. Older pending
        # rows cannot acquire new authority merely by being answered after an
        # upgrade. Action normalization discards any caller-supplied marker.
        action = {**action, "redirect_consent_version": 1}

    # Include body AND fields. With only (kind, title, action), muting "Approve
    # this?" about a harmless draft also silenced "Approve this?" about deleting
    # production data — the `answer` action normalizes to a bare {"type":"answer"},
    # so those two asks shared a key (Codex 2026-08-27, reproduced).
    #
    # ITEMS are in the key for the same reason, and it is load-bearing: a daily
    # note reuses its kind, title and body every day, so without the items
    # today's note would dedupe onto yesterday's pending row -- no new request,
    # and therefore no notification.
    #
    # An ITEMLESS request keeps the original FIVE-element key. Appending an
    # empty list to every key changed the identity of every request that
    # already exists: a live pending row stops deduplicating, so the agent
    # opens a second identical tab, and every standing "don't ask me this
    # again" -- looked up by EXACT key in `create_request` -- stops matching,
    # so a question the owner already settled is asked again (gpt-6-astra,
    # 2026-09-29). Items only extend the identity of requests that have items.
    identity = [kind, title, body, fields, action]
    if items:
        identity.append(items)
    dedupe = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    request_id = None
    if action.get("type") in _PINNED_ACTIONS:
        # The consent record lives outside this command center's folder (which
        # the agent writes with bash), under a request id the PLATFORM mints;
        # the row is created under that id and only references the record. An
        # id is never adopted from the writable store (gpt-6-astra, code r1 #2).
        reused = _open_pinned_row(_uid, udir, action)
        if reused is not None:
            return {**reused, "grant_sentence": _grant_sentence(reused)}
        request_id = _pin_consent(_uid, action, (kind, title, body), fields)
    row = create_request(
        udir, kind=kind, title=title, body=body, fields=fields,
        action=action, dedupe_key=dedupe, origin=origin, items=items,
        request_id=request_id,
    )
    if row is None:
        return {"error": "request_storage_unavailable"}
    if row.get("error"):
        return row
    if row.get("settled"):
        # Already decided in a past interaction. This is the "it might know from
        # past interaction what it is allowed" case: a standing ALLOW means go
        # ahead, a standing decline means do not, and neither costs the user a
        # second answer.
        return {
            "status": "settled",
            "decision": row["decision"],
            "may_proceed": row["decision"] == "allowed",
            "answer": row.get("answer"),
            "feedback": row.get("feedback", ""),
            "note": (
                "You already asked this and they settled it. Act on the standing "
                "decision rather than asking again."
            ),
        }
    # `created` describes THIS call, not the request, so it is stripped before
    # the agent sees the row -- and it is the one thing that separates "a
    # request was raised" from "the one you raised before is still waiting".
    # Only the first is something to put on the owner's phone.
    created = row.pop("created", True)
    if action.get("type") in {"connect", "connect_http"}:
        from tinyassets.connection_continuations import bind

        try:
            row["server_continuation"] = bind(udir, row["request_id"]) or row["server_continuation"]
        except Exception:
            logger.warning("Connection ask saved but continuation binding failed", exc_info=True)
            row["continuation_status"] = "unavailable"

    if created:
        _notify_owner(_uid, row)
    return {**row, "grant_sentence": _grant_sentence(row), **sign_in}


#: Asks whose consent record is platform-owned (`tinyassets.command_center_packages`
#: pins): the rail renders them from the pin and the answer executes the pin.
_PINNED_ACTIONS = frozenset({"publish", "install"})


def _ask_agent(action: dict[str, Any]) -> str:
    from tinyassets.command_center_packages import agent_id

    return action.get("agent") or (action.get("package") or {}).get("agent") or agent_id(None)


def _pin_consent(uid: str, action: dict[str, Any], tab: tuple[str, str, str],
                 fields: list[dict[str, Any]]) -> str:
    """Pin the consent record; returns the platform-minted request id."""
    from tinyassets.api.custom_agents import _authenticated_actor
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import pin

    kind, title, body = tab
    owner = _authenticated_actor()
    if not owner:
        raise PermissionError("authenticated consent owner required")
    return pin(_base_path(), universe_id=uid, kind=action["type"], agent=_ask_agent(action),
               owner_id=owner,
               digest=action["snapshot_digest"],
               record={"action": action, "tab": {"kind": kind, "title": title, "body": body,
                                                 "fields": fields}})


def _open_pinned_row(uid: str, udir: Any, action: dict[str, Any]) -> dict[str, Any] | None:
    """The same ask, still up: its row rendered from its pin, or None."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import open_pins
    from tinyassets.storage.pending_requests import get_request

    for request_id in open_pins(_base_path(), universe_id=uid, kind=action["type"],
                                agent=_ask_agent(action), digest=action["snapshot_digest"]):
        row = get_request(udir, request_id)
        if row is not None and row.get("status") == "pending":
            return _rendered_from_pin(uid, row)
    return None


def _consent_pin(uid: str, request_id: str) -> dict[str, Any] | None:
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import pin_for_request

    return pin_for_request(_base_path(), universe_id=uid, request_id=request_id)


#: What the rail shows for a pinned ask whose record is gone: it cannot be
#: confirmed, and says so rather than showing words nothing vouches for.
_UNPINNED_BODY = (
    "This request can no longer be confirmed: the platform has no record of what "
    "it showed you. Clear it and ask your agent to raise it again."
)


def _rendered_from_pin(uid: str, row: dict[str, Any]) -> dict[str, Any]:
    """A pinned ask as the platform wrote it, whatever its row now says.

    The pin is looked up by request id FIRST, before any row field is read: a
    row rewritten to look like an ordinary question still renders as the
    publish or install it is (gpt-6-astra, code r1 #1).
    """
    pinned = _consent_pin(uid, str(row.get("request_id") or ""))
    if pinned is None:
        if str((row.get("action") or {}).get("type") or "") in _PINNED_ACTIONS:
            return {**row, "body": _UNPINNED_BODY, "confirmable": False}
        return row
    tab = pinned["record"]["tab"]
    return {**row, "kind": tab["kind"], "title": tab["title"], "body": tab["body"],
            "fields": tab.get("fields") or [], "action": pinned["record"]["action"]}


def _owned_connection_git_host(connection_id: str) -> str:
    """The resolved git host of the caller's own live connection, or ``""``."""
    from pathlib import Path

    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage.outbound_connections import ConnectionLedger
    from tinyassets.storage.workspace_authority import connection_git_host

    actor = permissions.current_actor_id().strip()
    connection = ConnectionLedger(
        Path(_base_path()) / "outbound.db",
        verify_authenticated_principal=lambda: actor,
    ).get_connection(connection_id)
    if (
        connection is None
        or connection.owner_user_id != actor
        or connection.revoked_at is not None
    ):
        return ""
    return connection_git_host(connection)


def _full_channel_reach(universe_id: str, action: dict[str, Any]) -> dict[str, Any]:
    """``{"hosts": [...], "git_host": "..."}`` for the connection a full
    ``extend_http`` names, or ``{}`` when it cannot be read.

    Empty is fine: the sentence falls back to "the hosts it already reaches",
    which is vague but never wrong. Inventing a host would be worse.
    """
    from tinyassets.api.http_connection import preview_extend_http

    try:
        preview = preview_extend_http(universe_id=universe_id, payload={
            "destination": action.get("destination"),
            "access": "full",
        })
    except Exception:  # noqa: BLE001 - a sentence must never break the ask
        return {}
    reach: dict[str, Any] = {}
    # The policy this sentence is written from. The answer swaps against it, so
    # a yes can only land on the reach the owner actually read (Codex code
    # review round 2, left open). Same single read: the words and the snapshot
    # cannot disagree.
    snapshot = {
        "endpoints_json": preview.get("stored_json"),
        "scopes_json": preview.get("stored_scopes_json"),
        "access_mode": preview.get("expected_access_mode"),
        # Which deposit this is. The id and the credential reference are both
        # derived from (universe, destination), so neither changes when a key
        # is removed and a different one put in its place.
        "incarnation": preview.get("stored_incarnation"),
    }
    if all(isinstance(v, str) and v for v in snapshot.values()):
        reach["policy_snapshot"] = snapshot
    hosts = preview.get("hosts")
    if isinstance(hosts, list) and hosts:
        reach["hosts"] = [str(h) for h in hosts]
    # Only a git host the OWNER declared is named. A host merely derived from
    # the endpoints is repeated to nobody: it made a full grant on a Slack key
    # read as "clone or push on slack.com" (Codex code review round 1). An
    # undeclared host gets the conditional clause.
    declared_git_host = str(preview.get("declared_git_host") or "").strip().lower()
    if declared_git_host:
        reach["declared_git_host"] = declared_git_host
    return reach


def _extend_ask_verdict(
    universe_id: str, action: dict[str, Any], *,
    captured_preview: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Check an ``extend_http`` ask against the connection the owner already
    holds, when the agent RAISES it. ``None`` means "raise the tab".

    Two outcomes never reach the owner as a tab:

    * the ask adds nothing -- the agent is told ``already_held`` with the
      grant it has, so it acts instead of asking again (live 2026-09-02: the
      universe re-asked for reach its git scopes already gave it);
    * answering it would be refused -- the agent is told the exact reason
      (``ask_cannot_be_granted``), because the agent can fix the ask and the
      owner cannot. Before this the owner saw the ledger's sentence after
      clicking yes, and the click was recorded as a decline.
    """
    from tinyassets.api.http_connection import preview_extend_http

    preview = preview_extend_http(universe_id=universe_id, payload={
        "destination": action.get("destination"),
        "endpoints": action.get("endpoints") or None,
        "scopes": action.get("scopes") or [],
        # The ask's MODE. Without it a full ask was previewed as
        # exact-with-no-endpoints and could not raise at all (Codex code
        # review round 1, P0).
        "access": action.get("access") or "exact",
    })
    if captured_preview is not None:
        captured_preview.update(preview)
    if preview.get("error") == "not_found":
        return _bad(
            f'no key is deposited as "{action.get("destination")}" to extend; '
            "raise a connect_http ask for it instead"
        )
    if preview.get("error"):
        detail = str(preview.get("detail") or preview["error"])
        git_host = str(preview.get("git_host") or "")
        asked_hosts = [str(h) for h in (preview.get("asked_hosts") or [])]
        if git_host and git_host in asked_hosts:
            detail += (
                f" The key already reaches {git_host} for git: a clone or push "
                "uses the connection's git scopes and needs no HTTP endpoint on "
                "that host."
            )
        elif asked_hosts and not preview.get("declared_git_host"):
            # An HTTP endpoint never moves git. Where git goes is the
            # connection's declared git_host, set when the owner connects.
            detail += (
                " An HTTP endpoint does not change where git goes. If this "
                "service serves git on another host, the connection must "
                "declare it: remove this key and raise a connect ask with "
                '"git_host" naming that host.'
            )
        return {
            "error": "ask_cannot_be_granted",
            "detail": detail,
            "note": (
                "Answering this ask would fail with exactly this reason, so no "
                "tab was raised. Fix the ask, or drop it."
            ),
        }
    if preview.get("status") == "unchanged":
        return {
            "status": "already_held",
            "destination": preview.get("destination"),
            "allowed_endpoints": preview.get("allowed_endpoints") or [],
            "scopes": preview.get("scopes") or [],
            "note": (
                "You already hold everything this ask would grant, so nothing "
                "was asked. Act on the grant you have."
            ),
        }
    return None


def _rotate_ask_verdict(universe_id: str, action: dict[str, Any]) -> dict[str, Any]:
    """What to record on a ``rotate_http`` ask, or the refusal to raise it.

    A rotation names a destination and nothing else, so everything the card needs
    comes from the connection the owner already has: the auth scheme (which
    decides the card's boxes) and the incarnation (which deposit this card is
    for). Read ONCE, here, when the ask is raised, so the tab the owner reads and
    the write that follows cannot disagree.

    Two things never reach the owner as a tab, on the same grounds as
    ``_extend_ask_verdict``: there is no key to replace, or its scheme cannot be
    replaced by pasting. The agent can fix both; the owner cannot.
    """
    from tinyassets.api.http_connection import preview_rotate_http

    unraisable = (
        "Answering this ask would fail with exactly this reason, so no tab was "
        "raised. Fix the ask, or drop it."
    )
    preview = preview_rotate_http(universe_id=universe_id, payload={
        "destination": action.get("destination"),
    })
    if preview.get("error") == "not_found":
        return {
            "error": "ask_cannot_be_granted",
            "detail": (
                f'no key is deposited as "{action.get("destination")}" to '
                "replace; raise a connect_http ask to deposit one instead"
            ),
            "note": unraisable,
        }
    if preview.get("error") == "rotation_not_supported":
        # The preview applies every refusal the write makes, so this covers a
        # sign-in connection (renew it by signing in again) and a scheme with no
        # pasted key at all. Reported as ask_cannot_be_granted with the write's
        # own reason: the agent can act on it, the owner could not.
        return {
            "error": "ask_cannot_be_granted",
            "detail": str(preview.get("detail") or preview["error"]),
            "note": unraisable,
        }
    if preview.get("error"):
        # Passed through under its OWN name rather than flattened into
        # `ask_cannot_be_granted`: a legacy record with no recorded depositor is
        # not an ask the agent can fix, and calling it one would send it round the
        # same loop. The note is added so it still knows no tab is pending.
        return {**preview, "note": unraisable}
    return {
        "auth_scheme": str(preview.get("auth_scheme") or "").strip().lower(),
        # WHICH deposit this card replaces. The connection id and the credential
        # reference are both derived from (universe, destination), so neither
        # changes when a key is removed and a different one put in its place --
        # this is the only value that does, and the answer re-checks it.
        "incarnation": str(preview.get("incarnation") or ""),
    }


def _granted_lines(action: dict[str, Any]) -> list[str]:
    """Everything an ask grants, one phrase each, endpoints AND git scopes.

    ONE builder, because there are two action types that grant and they were
    rendered by two hand-written string assemblies. Teaching the deposit branch
    about git scopes left the EXTENSION branch silently granting repository
    write with nothing about it on screen -- and a scope-only extension rendered
    the empty phrase "reach ." (Codex, W4).

    Scopes are folded into the same list rather than appended after it, so the
    "and nothing else" that closes the sentence stays true. Appending a tail
    after "nothing else" produced a sentence that contradicted itself in the
    same breath (Codex, W3).
    """
    lines = [
        f"{'/'.join(e.get('methods') or [])} {e.get('host')}{e.get('path_template')}"
        + (
            " (may follow public HTTPS redirects without sharing this key with another origin)"
            if e.get("redirect_mode") == "public_https_get" else ""
        )
        for e in (action.get("endpoints") or [])
    ]
    for scope in (action.get("scopes") or []):
        text = str(scope).strip()
        if not text or ":" not in text:
            continue
        kind, _, repo = text.partition(":")
        # The owner is told WHERE git sends the key. A deposit names the host it
        # declares; an extension names the stored connection's resolved git
        # host, captured when the ask was raised (never a caller-supplied value:
        # action normalization drops both fields).
        host = (
            action.get("git_scope_host")
            if action.get("type") == "extend_http"
            else action.get("git_host")
        )
        on = f" on {host}" if host else ""
        if kind == "git_read":
            lines.append(f"use git to READ {repo}{on}")
        elif kind == "git_write":
            lines.append(f"use git to WRITE to {repo}{on}")
    return lines


def _full_channel_sentence(action: dict[str, Any]) -> str:
    """The ONE sentence a full grant is (full-channel-access D5).

    It says the whole thing plainly -- every host, every verb, and the git
    clause when a git host resolves for the channel -- because "full" is
    exactly the grant an owner must not have to infer. It never renders a
    wildcard row: there is no wildcard, only a mode.
    """
    destination = action.get("destination")
    hosts = [str(h).strip().lower() for h in (action.get("hosts") or []) if str(h).strip()]
    if not hosts:
        hosts = sorted({
            str(e.get("host") or "").strip().lower()
            for e in (action.get("endpoints") or [])
            if isinstance(e, dict) and str(e.get("host") or "").strip()
        })
    where = ", ".join(hosts) if hosts else "the hosts it already reaches"
    deposit = action.get("type") in _DEPOSIT_TYPES
    opening = (
        f'Full access to the {destination} key you are about to paste'
        if deposit
        else f'Full access to your {destination} key'
    )
    # Only a git host the owner DECLARED gets named, because naming one is a
    # claim: a full grant on a Slack key would otherwise read as "git clone or
    # push on slack.com". Any other single host gets the general clause below,
    # which is true for a Gitea box and harmless for a key that serves no git.
    # Re-validated here: a value that is not a hostname is not rendered.
    from tinyassets.storage.workspace_authority import GitScopeError, normalize_git_host

    # A deposit's `git_host` is the field the owner's ask declared (validated
    # when it was raised). An extension names the STORED connection's declared
    # host under `declared_git_host`; a legacy extension row's `git_host` was
    # derived from the endpoints and is never rendered (Codex code review
    # round 2).
    field = "git_host" if deposit else "declared_git_host"
    try:
        recognised = normalize_git_host(action.get(field))
    except GitScopeError:
        recognised = ""
    if recognised:
        git_clause = (
            f", and git clone or push to any repository it can reach on {recognised}, "
            "including checking that repository out and running its build in your "
            "universe's sandbox"
        )
    elif len(hosts) == 1:
        # One host, not a forge we recognise -- it may still be a Gitea or
        # GitLab box, and a full grant does cover git there.
        git_clause = (
            ". Where that serves git, this covers clone and push to any "
            "repository the key can reach there, including checking it out and "
            "running its build in your command center's sandbox"
        )
    else:
        # Several hosts: a git scope binds ONE host, so this channel carries no
        # git authority at all and the sentence must not imply otherwise.
        git_clause = ""
    closing = (
        " You paste it once."
        if deposit
        else " You do not need to paste it again."
    )
    return f"{opening}: anything the key itself can do at {where}{git_clause}.{closing}"


def _grants_git(action: dict[str, Any]) -> bool:
    """Whether this ask carries git authority, which "reach" does not describe."""
    return any(
        str(scope).strip().startswith(("git_read:", "git_write:"))
        for scope in (action.get("scopes") or [])
    )


def _grant_sentence(row: dict[str, Any]) -> str:
    """For a credential ask, the exact grant in one line. Empty otherwise."""
    from tinyassets.storage.outbound_connections import _URL_SECRET_SCHEME

    action = row.get("action") or {}
    if action.get("type") == "publish":
        return f"Accepting publishes \"{action.get('name', '')}\" for anyone to copy."
    if action.get("type") == "bind_model_access":
        from tinyassets.api.model_access_requests import grant_sentence

        try:
            return grant_sentence(action)
        except (KeyError, TypeError, ValueError):
            return "This model-access request is invalid; ask again."
    if action.get("type") == "grant_workspace_consent":
        from tinyassets.storage.workspace_authority import CONSENT_OPERATIONS

        operations = [
            CONSENT_OPERATIONS.get(consent, consent)
            for consent in (action.get("consents") or [])
        ]
        # The host is the connection's RESOLVED git host, captured when the ask
        # was raised. A row without one (older than the field) names no host,
        # so it cannot be granted: the owner must see where the key goes.
        host = str(action.get("host") or "").strip()
        if not host:
            return (
                "This request does not say which host the key would be used "
                "against, so it cannot be granted. Ask again."
            )
        return (
            "Let this command center " + ", ".join(operations) + " "
            f"{action.get('repo')} on {host} with the key you already "
            "gave. Nothing to paste; this is the yes."
        )
    if action.get("type") == PATCH_INTAKE_ACTION:
        label = str(action.get("label") or "").strip()
        if not label:
            return (
                "This request does not say which intake it would connect to, so "
                "it cannot be granted. Ask again."
            )
        return (
            f"Let this command center send problem reports to {label} -- what it was "
            "trying to do and what was missing. Only that one place, only what it "
            "sends, and nothing else of yours. Nothing to paste; this is the yes, "
            "and you can take it back."
        )
    if action.get("type") == "connect" and "setup" in action:
        # The synthesized setup entry grants nothing itself; each shape it
        # completes raises (or answers) its own exact request.
        return ""
    if action.get("type") == "connect":
        base = _grant_sentence({**row, "action": {**action, "type": "connect_http"}})
        return (base + _uses_sentence(action) + _sign_in_sentence(row)) if base else ""
    if action.get("type") in ("extend_http", "connect_http") and action.get("access") == "full":
        return _full_channel_sentence(action)
    if action.get("type") == "extend_http":
        lines = _granted_lines(action)
        if not lines:
            return ""
        verb = "do" if _grants_git(action) else "reach"
        return (
            f'Also let the key you already gave as "{action.get("destination")}" '
            f"{verb} " + "; ".join(lines) + ". You do not need to paste it again."
        )
    if action.get("type") == "remove_http":
        return (
            f'Delete the key you gave as "{action.get("destination")}", and '
            "everything it was allowed to reach. Nothing to paste; this is the "
            "yes. You can deposit that name again whenever you like."
        )
    if action.get("type") == "rotate_http":
        # PLAIN, because the owner is being told something broke and what to do
        # about it, and because the failure this replaces was a removal card they
        # read as deletion. Nothing is being granted that was not granted before,
        # so this sentence promises the opposite of a grant sentence: nothing
        # changes except the key.
        return (
            f'{action.get("destination")} stopped accepting its key. Paste a new '
            "one. Nothing else changes: the same connection, the same access you "
            "already approved, and nothing new to allow."
        )
    if action.get("type") != "connect_http":
        return ""
    lines = _granted_lines(action)
    if not lines:
        return ""
    # Name the connection. Two asks can differ ONLY by destination — the agent
    # re-raised the same endpoint under a new name when the first would have
    # conflicted — and with the destination hidden both tabs read identically,
    # so a user cannot tell the one that works from the one that fails
    # (observed live, 2026-08-28).
    where = f' as "{action.get("destination")}"' if action.get("destination") else ""
    git_to = _git_host_clause(action.get("git_host"))
    if str(action.get("auth_scheme") or "").strip().lower() == _URL_SECRET_SCHEME:
        # The owner is pasting a whole link, so say what happens to it. The
        # `{secret}` in the endpoint line is the platform's placeholder, not a
        # thing they have to fill in, and without this the tab reads like a
        # template they are supposed to complete.
        kept = (
            " The code in the link is kept in your vault and put back into the "
            "address only as the call is made; this request stores the rest of "
            "the link, never the code."
        )
        joined = "; ".join(lines)
        return (
            f"This link{where} will be able to {joined} - nothing else."
            f"{kept}{git_to}"
        )
    if len(lines) == 1:
        return f"This key{where} will be able to {lines[0]} - nothing else.{git_to}"
    # "reach" is the established wording and describes an endpoint list. It does
    # NOT describe "use git to WRITE to owner/repo", so the verb widens only
    # when a git scope is actually present -- every ask without one reads
    # exactly as it always has.
    verb = "do" if _grants_git(action) else "reach"
    return (
        f"This key{where} will be able to {verb} exactly these, and nothing "
        "else: " + "; ".join(lines) + "." + git_to
    )


def _sign_in_sentence(row: dict[str, Any]) -> str:
    """How a sign-in-capable ask is completed, naming where the click goes."""
    action = row.get("action") or {}
    if not _has_sign_in(action):
        return ""
    from urllib.parse import urlsplit

    from tinyassets.connection_oauth.discovery import offer_hosts

    offer = action["oauth"]
    host = urlsplit(offer["authorize_url"]).hostname or "the provider"
    token_host = urlsplit(offer["token_url"]).hostname or "the provider"
    scopes = offer.get("scopes") or []
    asks = f" It asks for: {', '.join(scopes)}." if scopes else ""
    paste = " You can paste a key instead." if row.get("fields") else ""
    # EVERY host the sign-in contacts, not only where the owner clicks: the
    # token host receives the code and every refresh token.
    return (f" Sign in at {host} to connect it - no key to copy.{asks} Tokens come "
            f"from {token_host}; sign-in talks only to {', '.join(offer_hosts(offer))}, "
            "found from the connection's own host. Its access renews itself, and "
            "only your agent can use it." + paste)


def _git_host_clause(value: Any) -> str:
    """" Git operations with this key go to <host>." for a declared git host.

    Rendered on EVERY deposit that declares one, not only beside a git scope:
    it is a place the owner's key is sent, and the owner approves it here.
    """
    if not value:
        return ""
    return f" Git operations with this key go to {value}."


def _uses_sentence(action: dict[str, Any]) -> str:
    """What a ``connect`` ask adds beyond reach: model use and constant headers."""
    parts = []
    model = (action.get("uses") or {}).get("model")
    if isinstance(model, dict):
        names = ", ".join(str(m.get("id")) for m in model.get("models") or [])
        billing = ("free of charge" if model.get("billing") == "free"
                   else "flat-rate (a plan you already pay for)")
        parts.append(
            f" Your command center may also run its model on it ({model.get('wire')} wire): "
            f"{names}. The requester declared these {billing}; TinyAssets cannot "
            "check that. Calls use your key, so anything the provider charges is "
            "billed to your account there, and TinyAssets grants no spending on "
            "them. If nothing powers your command center yet, this becomes its model."
        )
    headers = action.get("constant_headers") or {}
    if headers:
        parts.append(
            " Every call on it also sends "
            + ", ".join(f"{name}: {value}" for name, value in sorted(headers.items()))
            + "."
        )
    return "".join(parts)


#: The one ask the platform raises itself. Everything else comes from the agent —
#: but the agent cannot ask for the thing it needs in order to think at all, so
#: this one is synthesized (founder, 2026-08-27).
_LLM_REQUEST_ID = "sys_connect_llm"


def _serving_llm_bound(base_path, universe_id: str, actor: str) -> bool:
    """Whether this owner's serving connection still has current authority.

    A binding row survives grant revocation and credential rotation. Use the
    same local custody checks as execution, never host auth or an upstream probe
    on every rail poll. This does not claim remote quota or provider health.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.provider_serving_binding import (
        serving_connection_is_current,
    )

    try:
        return serving_connection_is_current(
            base_path,
            universe_dir=_universe_dir(universe_id),
            universe_id=universe_id,
            owner_user_id=actor,
        )
    except Exception:  # noqa: BLE001 - unavailable authority must not hide recovery
        return False


#: The shapes the rail can complete itself for a model connection, in the order
#: it offers them. Each is answered through the ONE ``connect`` action with
#: explicit fields; nothing is inferred by an LLM, because an unpowered universe
#: has none. A ``command`` runner joins this list when one exists to run it.
_MODEL_CONNECT_SHAPES = ("api_key", "local")

def _first_power_preset() -> dict[str, object] | None:
    """The bundled guided sign-in the setup request offers first, as display data.

    It is installed data (``acquisition_presets.json``), not code: the app shows
    whatever preset is installed and names no provider itself. An unreadable
    preset drops the button rather than offering one that cannot work.
    """
    import json as _json
    from pathlib import Path

    from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset

    try:
        path = Path(__file__).parent.parent / "providers" / "acquisition_presets.json"
        docs = _json.loads(path.read_text(encoding="utf-8"))
        preset_id = next(iter(docs))
        preset = load_preset(preset_id)
        manual = docs[preset_id].get("manual_key_entry") is True
    except (OSError, ValueError, StopIteration, HostedAuthError, KeyError, TypeError):
        return None
    return {
        "preset_id": preset.id,
        "name": preset.display_name,
        "label": f"Continue with {preset.display_name}",
        "manage_url": preset.manage_url,
        "manual_key": manual,
    }


def _connect_llm_request(*, connected: bool = False) -> dict[str, object]:
    """A blocking setup entry, or an optional additional-source entry when ready.

    It is the whole model setup (founder, 2026-09-24): the app completes every
    shape inside this one request, through the one ``connect`` action.

    ``status`` is what tells the two apart. A connected universe's entry used to
    say ``pending`` like any real ask, so the rail listed "Connect another LLM"
    under "Waiting on you" with nothing actually waiting (live 2026-09-25). The
    entry stays -- it is the only route to a second source -- but it is
    ``optional``: offered, answerable, and outstanding to nobody.
    """
    from tinyassets.onboarding import DEVICE_SIGN_IN_SERVICE
    from tinyassets.providers.free_sources import (
        daily_cap_offers,
        sign_in_cards,
        source_cards,
        subscription_cards,
    )

    # One connect screen: the guided sign-in (``primary``), the sources completed
    # by signing in, the key cards, the subscriptions, and the provider-stated daily
    # limits the app's daily-cap card is worded from. All data, none in the page.
    setup: dict[str, object] = {"shapes": list(_MODEL_CONNECT_SHAPES), "sources": source_cards(),
                                "sign_in_sources": sign_in_cards(),
                                "subscriptions": [{**s, "service": DEVICE_SIGN_IN_SERVICE}
                                                  for s in subscription_cards()],
                                "daily_caps": daily_cap_offers()}
    primary = None if connected else _first_power_preset()
    if primary is not None:
        setup["primary"] = primary
    return {
        "request_id": _LLM_REQUEST_ID,
        "kind": "LLM",
        "title": ("Connect another LLM" if connected
                  else "Connect the model your command center runs on"),
        "body": (
            "Add another model source. Your command center keeps running on the one it has."
            if connected else
            "Your agent needs a model to think with. It only ever uses "
            "connections you authorize."
        ),
        "fields": [],
        "action": {"type": "connect", "use": "model", "setup": setup},
        "status": "optional" if connected else "pending",
        "sticky": not connected,
        "created_at": 0.0,
        "resolved_at": None,
        "answer": None,
        "feedback": None,
        "dedupe_key": _LLM_REQUEST_ID,
        "grant_sentence": "",
    }


_RECONNECT_REQUEST_PREFIX = "reconnect-source"

#: The shape the app renders for a source that is completed by SIGNING IN rather
#: than by pasting anything. ``api_key``/``local`` cannot answer this: the owner has
#: nothing to paste, and asking them to produce a token by hand is the thing the
#: one-tap flow exists to avoid.
SIGN_IN_SHAPE = "sign_in"


def _reconnect_requests(base: Any, uid: str, udir: Any) -> list[dict[str, object]]:
    """One card per source whose stored sign-in the provider has refused.

    Derived, never stored, exactly like the connect entry above: it follows the
    recorded rejection, so it appears the moment a refresh is refused, disappears the
    moment a deposit or a successful refresh fixes it, cannot be dismissed into a
    state with no way back, and needs no migration.

    The source is NAMED from the record, and nothing else about it is: the title is
    assembled from the service the vault reports, so no provider appears in platform
    text.
    """
    from tinyassets.credential_vault import load_credential_vault, refresh_rejected_sources
    from tinyassets.onboarding import DEVICE_SIGN_IN_SERVICE

    rejected = refresh_rejected_sources(base, universe_id=uid)
    if not rejected:
        return []
    # A rejection outlives nothing. The row says a stored sign-in was refused; if that
    # credential has since been REMOVED there is no longer anything to sign back in to,
    # and the card would ask the owner to repair a connection they deleted (Codex
    # refute-review round 2, item 3).
    try:
        deposited = {
            str(record.get("service") or "").strip().lower()
            for record in load_credential_vault(udir)
            if record.get("credential_type") == "llm_subscription"
        }
    except (ValueError, OSError):
        deposited = set(rejected)   # unreadable vault: keep the cards rather than hide them
    live = sorted(
        ((service, at) for service, at in rejected.items()
         if service and service in deposited),
        key=lambda row: (row[1], row[0]),
    )
    # AT MOST ONE. There is a single connect panel and `connectBody` MOVES it, so a
    # second card re-configured it on every render -- and the rail re-renders every 15
    # seconds, which reset a sign-in the owner was part-way through and left the second
    # card with no panel at all (Codex refute-review round 2, P1). One card at a time is
    # the shape the surface can actually honour: the oldest rejection first, the next
    # once that one is resolved.
    cards: list[dict[str, object]] = []
    for service, rejected_at in live[:1]:
        cards.append({
            "request_id": f"{_RECONNECT_REQUEST_PREFIX}:{service}",
            "kind": "LLM",
            "title": f"{service} needs you to sign in again",
            "body": (
                f"Your {service} connection's saved sign-in is no longer accepted, so "
                "your command center cannot use it. "
                + (
                    "Signing in again takes one tap and replaces it."
                    if service == DEVICE_SIGN_IN_SERVICE
                    else "Reconnect it below to replace it."
                )
                + " Nothing else about the connection changes."
            ),
            "fields": [],
            # The SAME `connect` action the setup card uses, so the app answers it
            # through the one model-connect surface instead of a second one.
            #
            # The sign-in shape is offered ONLY for a source this daemon can complete
            # by brokered sign-in; the onboarding module that implements that flow is
            # asked, so nothing is named here. Any other source falls back to the
            # ordinary shapes, because a card offering a sign-in that does not exist
            # is a card the owner cannot answer (Codex refute-review, P1 #5).
            "action": {
                "type": "connect", "use": "model",
                "setup": {
                    "shapes": (
                        [SIGN_IN_SHAPE] if service == DEVICE_SIGN_IN_SERVICE
                        else list(_MODEL_CONNECT_SHAPES)
                    ),
                    "service": service,
                    # The universe this card is FOR. The sign-in route resolves it
                    # against the caller's admin ACL rather than trusting it, so this
                    # names the target and proves nothing.
                    "universe_id": uid,
                },
            },
            "status": "pending",
            "sticky": True,
            "created_at": 0.0,
            "resolved_at": None,
            "answer": None,
            "feedback": None,
            "dedupe_key": f"{_RECONNECT_REQUEST_PREFIX}:{service}",
            "grant_sentence": "",
            "rejected_at": rejected_at,
        })
    return cards


def list_requests(*, universe_id: str = "") -> dict[str, Any]:
    """What the app's rail renders, and what the phone reads too: EVERY pending row.

    Complete, with no page: the owner door returns it whole, and the model door
    bounds the whole document visibly. A default page here hid a waiting request.

    Carries the agent's asks plus a derived connection entry: required without
    current serving authority, optional once powered. No agent is needed to ask.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.patch_intake import rail_entry
    from tinyassets.storage.pending_requests import (
        list_pending,
        list_resolved,
        list_suppressions,
        list_unmutes,
    )

    uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    # The offered patch intake, and whether this universe holds its grant. On the
    # rail because the agent polls the rail anyway: current, costs no per-round
    # tool-description bytes, and specific enough that a universe never invents a
    # credential ask for an address that needs no credential.
    #
    # Resolved BEFORE the listing, so the entry the platform owes this universe
    # is in the very first rail read of a new account (the same reason the
    # model-connect entry is synthesized here) and an existing user gets it on
    # their next sign-in with no migration. One call seeds and describes, so the
    # block cannot contradict the rail it is describing. Never raises.
    intake = rail_entry(uid, udir)
    rows = [_rendered_from_pin(uid, r) for r in list_pending(udir)]
    # Prepended, not stored: derived from current serving authority, so it
    # cannot go stale, cannot be dismissed into a state where the universe is
    # mute with no way back, and needs no migration.
    connected = _serving_llm_bound(_base_path(), uid, permissions.current_actor_id().strip())
    entry = _connect_llm_request(connected=connected)
    rows = [row for row in rows if row["request_id"] != _LLM_REQUEST_ID]
    if connected:
        from tinyassets.provider_assignment import load_provider_assignment

        assignment = load_provider_assignment(_base_path(), universe_id=uid)
        if assignment is not None and len(assignment.candidates) == 1:
            entry["suggestion"] = (
                "Add another free source to keep going when one reaches its limit."
            )
    if connected:
        from tinyassets.request_budget import budget_for_rail

        budget = budget_for_rail(_base_path(), permissions.current_actor_id().strip(), udir)
        if budget is not None and budget.remaining < 10:
            entry["status"] = "pending"
            entry["suggestion"] = budget.connect_suggestion()
    rows = [*rows, entry] if connected else [entry, *rows]
    # FIRST in the rail: a refused sign-in is the reason a powered universe is not
    # working, so it outranks both the agent's asks and the optional
    # "connect another" entry. Derived the same way, for the same reasons.
    rows = [*_reconnect_requests(_base_path(), uid, udir), *rows]
    return {
        "universe_id": uid,
        "pending": [{**r, "grant_sentence": _grant_sentence(r)} for r in rows],
        "count": len(rows),
        "request_recovery": REQUEST_RECOVERY_DETAIL,
        **({"patch_intake": intake} if intake is not None else {}),
        "recently_answered": [
            {k: v for k, v in r.items() if k != "action"}
            for r in list_resolved(udir, limit=5)
        ],
        # Visible, so a standing "don't ask again" is undoable rather than a trap.
        "muted": list_suppressions(udir),
        # …and lifts are visible too, because the agent shares the user's
        # principal and can lift one itself.
        "mutes_lifted": list_unmutes(udir),
    }


@_coordinated
def unmute_request(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Lift a "don't ask again". A standing refusal a user cannot undo is a trap."""
    from tinyassets.storage.pending_requests import record_unmute, unsuppress

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    try:
        document = _payload(payload)
    except ValueError as exc:
        return _bad(str(exc))
    key = str(document.get("dedupe_key") or "")
    if not key:
        return _bad("dedupe_key is required; read it from the muted list")
    lifted = unsuppress(udir, key)
    if lifted:
        # The agent runs as the user's own principal, so nothing at this gate can
        # tell "the user lifted a mute" from "the universe lifted the mute the
        # user set" — Codex reproduced mute -> agent reads key -> agent unmutes.
        # A distinction the auth model cannot make must not be faked here; record
        # it so the lift is visible in the rail rather than silent.
        record_unmute(udir, key)
    return {"status": "unmuted" if lifted else "not_muted"}


@_coordinated
def withdraw_request(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """The agent takes down an ask of its own that it knows is stale.

    Owner-gated like every rail operation. Only a still-pending ask the agent
    raised moves; an answered one, a platform ask, and the synthesized model
    entry are refused with the reason. Records the withdrawal (status
    ``withdrawn`` with the reason) rather than deleting, and writes no standing
    decision.
    """
    from tinyassets.storage.pending_requests import list_pending
    from tinyassets.storage.pending_requests import withdraw_request as _withdraw

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    try:
        document = _payload(payload)
    except ValueError as exc:
        return _bad(str(exc))
    request_id = str(document.get("request_id") or "").strip()
    if not request_id:
        return _bad("request_id is required; read_graph target=pending_requests lists it")
    if request_id == _LLM_REQUEST_ID:
        return {
            "error": "not_withdrawable",
            "detail": (
                "this entry is derived from whether a model is connected; it "
                "clears itself when one is, and nobody raised it"
            ),
        }
    reason = str(document.get("reason") or "").strip()[:_MAX_ANSWER_CHARS]
    if reason and looks_like_credential(reason):
        return _bad(
            "that reason looks like it contains a credential; it is stored in "
            "the clear, so say it in words instead"
        )
    row = _withdraw(udir, request_id, reason=reason)
    if row.get("error"):
        return row
    on_rail = any(r["request_id"] == request_id for r in list_pending(udir))
    return {**{k: v for k, v in row.items() if k != "action"},
            "still_on_rail": on_rail}


def _grant_workspace_consent(
    *,
    udir: Any,
    row: dict[str, Any],
    action: dict[str, Any],
    request_id: str,
    answer: dict[str, Any],
    feedback: str,
    dont_ask_again: bool,
) -> dict[str, Any]:
    """Write the typed workspace consents the owner just agreed to.

    One row per operation under the ``workspace`` sink, at the destination
    :func:`workspace_consent_destination` spells - the same string the sink
    checks, from the same function, so the two cannot drift apart.

    The named connection must exist, belong to this owner and not be revoked:
    consent to check out a repository through a connection that is not theirs is
    not a thing the owner can give. It is also IN the key, so a second key
    deposited under another destination label starts with no consent of its own.

    It does NOT require the git scope to exist
    yet - the scope ask and the consent ask are two tabs and the owner may
    answer them in either order, and a consent with no scope behind it grants
    nothing, because the sink requires both.
    """
    from pathlib import Path

    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.storage.outbound_connections import ConnectionLedger
    from tinyassets.storage.pending_requests import resolve_request
    from tinyassets.storage.workspace_authority import (
        WORKSPACE_SINK,
        connection_git_host,
        workspace_consent_destination,
    )

    actor = permissions.current_actor_id().strip()
    connection_id = action["connection_id"]
    ledger = ConnectionLedger(
        Path(_base_path()) / "outbound.db",
        verify_authenticated_principal=lambda: actor,
    )
    connection = ledger.get_connection(connection_id)
    if (
        connection is None
        or connection.owner_user_id != actor
        or connection.revoked_at is not None
    ):
        # Leave the request PENDING, exactly as a failed deposit does: the answer
        # did not land, and closing the tab would lose the ask with nothing
        # written. Uniform envelope so this cannot probe which ids exist.
        return {"error": "not_found", "resource": "connection"}

    repo = action["repo"]
    # The host the consent is keyed under is the connection's own, exactly as
    # the sink derives it. A default here (it used to be github.com) writes the
    # row at a key the sink never looks up for any other forge: the owner says
    # yes and nothing is authorized.
    host = connection_git_host(connection)
    if not host:
        return {"error": "not_found", "resource": "connection"}
    if host != str(action.get("host") or "").strip():
        # The owner said yes to a named host. A row that names none, or a
        # connection that now resolves elsewhere, is not that yes. PENDING.
        return {
            "error": "connection_conflict",
            "resource": "connection",
            "detail": (
                "this request no longer names the host the key would be used "
                "against; ask again and the tab will name it"
            ),
        }
    destinations = [
        workspace_consent_destination(
            consent, repo, connection_id=connection_id, host=host
        )
        for consent in action["consents"]
    ]
    for destination in destinations:
        grant_consent(
            udir,
            sink=WORKSPACE_SINK,
            destination=destination,
            granted_by=actor,
        )
    resolve_request(
        udir,
        request_id,
        status="answered",
        answer=answer,
        feedback=feedback,
        dont_ask_again=dont_ask_again,
        decision="allowed",
    )
    return {
        "status": "answered",
        "request_id": request_id,
        "connection_id": connection_id,
        "repo": repo,
        "consents": list(action["consents"]),
        "destinations": destinations,
        "receipt": _grant_sentence(row),
        "secret_reused": True,
    }


def _grant_patch_intake(
    *,
    uid: str,
    udir: Any,
    row: dict[str, Any],
    action: dict[str, Any],
    request_id: str,
    answer: dict[str, Any],
    feedback: str,
) -> dict[str, Any]:
    """Record the one send-only grant the owner just gave, and prove it can work.

    Three things are checked before anything is written, and each one leaves the
    request PENDING rather than consuming the owner's yes on a grant that would
    do nothing (the shape ``_grant_workspace_consent`` established):

    * the row still names the intake this deployment offers -- a stored ask
      outlives the configuration it was created under, and a grant for a
      retired address authorizes nothing;
    * it accepts THIS sender. An intake that is merely discoverable lets a
      sender read its terms while delivery refuses, so granting against one
      would hand the owner a connection that cannot send. Refused with the
      reason instead. Asked through ``sender_is_permitted``, which is the same
      question delivery asks -- NOT reconstructed from the receiver view, whose
      sender-facing form omits ``allowed_senders`` and would therefore refuse an
      explicitly enumerated sender who can in fact deliver;
    * its contract can be read, which is also how "it is actually there and not
      revoked" is established.

    Both reads run as the answering owner's own principal through the ordinary
    receiver surfaces, so they disclose exactly what any sender may see and
    nothing about the intake owner's graph.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.patch_intake import (
        _intake_or_none,
        grant_send_consent,
    )
    from tinyassets.storage import receiver_links as receiver_store
    from tinyassets.storage.pending_requests import resolve_request

    intake = _intake_or_none("answering the seeded request")
    receiver_id = str(action.get("receiver_id") or "")
    if intake is None or intake["receiver_id"] != receiver_id:
        return {
            "error": "patch_intake_changed",
            "detail": (
                "this request names an intake this platform no longer offers, so "
                "approving it would grant nothing; it will be re-offered with the "
                "current one"
            ),
            "request_pending": True,
        }
    actor = permissions.current_actor_id().strip()
    base = _base_path()
    if not receiver_store.sender_is_permitted(
        base, receiver_id=receiver_id, sender_id=actor
    ):
        return {
            "error": "patch_intake_closed",
            "detail": (
                f"the {intake['label']} intake is not accepting reports from "
                "this account, so approving this would grant a connection that "
                "cannot send; the request stays open"
            ),
            "request_pending": True,
        }
    try:
        receiver = receiver_store.inspect_receiver(
            base, receiver_id=receiver_id, principal_id=actor,
        )
    except (receiver_store.ReceiverAccessDenied, ValueError):
        return {
            "error": "patch_intake_unreachable",
            "detail": (
                f"the {intake['label']} intake is not reachable from this "
                "account right now, so there is nothing to connect to; the "
                "request stays open and you can approve it once it is back"
            ),
            "request_pending": True,
        }
    # RESOLVE FIRST, then grant. `resolve_request` is a guarded UPDATE that moves
    # only a still-pending row, so it is the election: exactly one caller wins,
    # and a concurrent Clear or a second tap loses. Granting first inverted that
    # -- the loser's consent was written and committed while the request stayed
    # pending, which is authority with no recorded decision behind it
    # (gpt-6-astra refute round on PR #4121, P1).
    #
    # The residual asymmetry is deliberate. If the grant then fails, the owner
    # has an answered request and no connection, and they are TOLD so; the
    # alternative leaves authority nobody can see. Both stores are files in the
    # same universe directory, so a failure that hits one almost certainly hits
    # the other first, which is the resolution call above.
    if not resolve_request(
        udir, request_id, status="answered", answer=answer, feedback=feedback,
        dont_ask_again=False, decision="allowed",
    ):
        return {"error": "request_resolution_unconfirmed", "request_pending": True}
    try:
        grant = grant_send_consent(udir, receiver_id=receiver_id, granted_by=actor)
    except Exception as exc:  # noqa: BLE001 - say what happened; grant nothing
        logger.exception("patch intake: the grant did not land after the owner's yes")
        return {
            "error": "patch_intake_grant_unavailable",
            "detail": (
                "your approval was recorded but the connection could not be "
                f"saved ({exc}); nothing can be sent yet -- ask your command center to "
                "raise the request again"
            ),
        }
    return {
        "status": "answered",
        "decision": "allowed",
        "request_id": request_id,
        "universe_id": uid,
        # The address and its contract, so the universe can wire its own step to
        # it in the same turn instead of going looking for the id again.
        "receiver_id": receiver_id,
        "receiver_generation": receiver.get("generation"),
        "contract": receiver.get("contract"),
        "grant": {"sink": grant["sink"], "destination": grant["destination"]},
        "receipt": _grant_sentence(row),
        "secret_reused": True,
        "suppressed": False,
    }


def _answer_item(
    *,
    udir: Any,
    row: dict[str, Any],
    item_id: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    """The owner answered ONE item of a request.

    Items only exist on an ``answer`` action, so there is no act to perform
    here and nothing to re-validate against a stored credential policy: the
    answer IS the data. That is why this branch runs before the action
    dispatch rather than inside it.

    ``dont_ask_again`` is refused: the standing decision hangs on the request's
    dedupe key, which covers the whole tuple, so remembering "allowed" for one
    item of a daily note would replay that item's answer to every future note.
    """
    from tinyassets.storage.pending_requests import resolve_item

    # BIND the row that resolves to the row that was displayed, exactly as the
    # whole-request path does. This branch returned before that check, so an
    # item answer skipped the pin entirely: an item edited after the tab was
    # rendered still answered, and still closed the request (gpt-6-astra,
    # 2026-09-29). The pin covers `items`, so this is the check that makes
    # putting them inside it mean anything.
    if not displayed_row_matches(row):
        return {
            "error": "request_changed",
            "detail": (
                "this request was edited after it was shown; it was not "
                "answered -- read it again"
            ),
            "request_pending": True,
        }
    if str((row.get("action") or {}).get("type") or "answer") != "answer":
        return _bad("this request is one decision, not a checklist")
    if not row.get("items"):
        return {"error": "not_found", "resource": "request_item"}
    if document.get("dont_ask_again") is True:
        return _bad(
            "'don't ask again' settles a whole request, not one of its items"
        )
    feedback = str(document.get("feedback") or "").strip()[:_MAX_ANSWER_CHARS]
    if feedback and looks_like_credential(feedback):
        return _bad(
            "that feedback looks like it contains a credential; it is stored "
            "in the clear, so say it in words instead"
        )
    item = next(
        (i for i in row["items"]
         if isinstance(i, dict) and str(i.get("item_id") or "") == item_id),
        None,
    )
    if item is None:
        return {"error": "not_found", "resource": "request_item"}

    dismissed = document.get("dismiss") is True
    answer: dict[str, Any] | None = None
    if not dismissed:
        values = document.get("values")
        if values is None:
            values = {}
        if not isinstance(values, dict):
            return _bad("values must be an object of field name -> value")
        declared = {str(f.get("name") or "") for f in (item.get("fields") or [])
                    if isinstance(f, dict)}
        unknown = sorted(set(map(str, values)) - declared)
        if unknown:
            # The item's own fields are the only thing it asked for. Accepting
            # extras would store data the owner was never shown a box for.
            return _bad(
                f"item {item_id!r} does not ask for " + ", ".join(map(repr, unknown))
            )
        answer = {
            name: str(values[name])[:_MAX_ANSWER_CHARS]
            for name in sorted(values)
        } or None

    result = resolve_item(
        udir, row["request_id"], item_id,
        status="dismissed" if dismissed else "answered",
        answer=answer, feedback=feedback,
    )
    if result.get("error"):
        return result
    # The clear rides `resolve_item`'s own emit seam, so it is not repeated
    # here: one definition of when a notification comes off the other devices.
    return {
        "status": result["status"],
        "request_id": row["request_id"],
        "item_id": item_id,
        "request_status": result["request_status"],
        "remaining": result["remaining"],
        "feedback": feedback,
    }


def propose(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """Store one proposed action for this owner; never accept a request shape.

    Research sessions only: see ``research_capability``.
    """
    from tinyassets.proposals import dedupe_key, validate
    from tinyassets.research_capability import non_research_proposal_refusal
    from tinyassets.storage.pending_requests import create_request

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    refusal = non_research_proposal_refusal()
    if refusal is not None:
        return refusal
    try:
        document = validate(_payload(payload))
    except ValueError as exc:
        return _bad(str(exc))
    title = document["action"]
    brief = document["why"] + "\n\n" + document["evidence"]
    row = create_request(
        udir, kind="proposal", title=title, body=brief, fields=[],
        action={"type": "start_activity", "title": title, "brief": brief},
        dedupe_key=dedupe_key(title), origin="agent",
    )
    return row if row is not None else {"error": "proposal_storage_failed"}


def _start_approved_proposal(universe_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """TODO(#4221): start the approved activity, idempotently by request_id.

    Call api.activities.write(operation="start", payload={"title": ..., "brief": ...})
    with the stored action and approval id when that subsystem lands.

    Until then this REFUSES rather than raising. ``answer_request`` returns an
    error from here unchanged and leaves the request pending, so the owner's
    Approve reads as a truthful "not yet" they can retry once #4221 lands. A
    raise reached them as an unhandled error instead, which is the crash Hard
    Rule 8 rules out -- loud, but never a crash and never a silent success.
    """
    return {"error": "proposal_start_unavailable",
            "detail": "approved proposals can't start activities yet; D2a #4221 wires this",
            "request_pending": True}


def answer_request(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """The user's answer.

    ``{"request_id": ..., "values": {...}}`` submits; ``"dismiss": true`` closes
    the tab with nothing written. For a ``connect_http`` request the secret value
    is deposited under the policy stored ON THE REQUEST — never one supplied
    here — so the tab's promise is what gets granted.
    """
    return _answer_request(universe_id=universe_id, payload=payload)


@_coordinated
def _answer_request(*, universe_id: str = "", payload: Any = None,
                    owner_session: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shared executor; only the protected HTTP route supplies owner proof.

    Never take owner_session from the answer payload or expose it on the public
    bearer handler. The route verifies the cookie, origin and authenticated owner.
    """
    from tinyassets.storage.pending_requests import get_request, resolve_request

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    try:
        document = _payload(payload)
    except ValueError as exc:
        return _bad(str(exc))

    request_id = str(document.get("request_id") or "").strip()
    if request_id == _LLM_REQUEST_ID:
        # Synthesized, not stored. It is satisfied by connecting a model, and it
        # disappears because the check that raises it stops being true — there is
        # nothing here to answer or dismiss.
        return {
            "error": "not_answerable",
            "detail": (
                "connect a model to clear this; it is not a question with an "
                "answer, it is the thing your agent needs in order to think"
            ),
        }
    row = get_request(udir, request_id) if request_id else None
    if row is None:
        return {"error": "not_found", "resource": "pending_request"}
    if row["action"].get("type") == "approve_action" and (
            "reply" not in document or owner_session is None):
        # Even a reply needs the owner session here: words in the asker's
        # thread must not stand in for the protected card's decision.
        return {"error": ("preview_required" if owner_session is not None
                          else "interactive_approval_required"),
                "detail": "Open the protected inline owner card to decide this action."}
    from tinyassets import request_answers

    try:
        request_answers.check(udir, row)
    except request_answers.UnrecordedAskerAmbiguous as exc:
        # No recorded asker and no sole owner: an answer would have to guess
        # whose agent to wake. Any admin may still clear the card; a dismissal
        # of an unrecorded ask enqueues no answer delivery.
        if not (document.get("dismiss") is True and "reply" not in document
                and not str(document.get("item_id") or "").strip()):
            return {"error": "unrecorded_asker_ambiguous", "detail": exc.detail,
                    "request_pending": row["status"] == "pending"}
    except PermissionError:
        return {"error": "not_found", "resource": "pending_request"}
    if "reply" in document:
        text = document["reply"]
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_ANSWER_CHARS:
            return _bad("reply must be nonempty text within the answer limit")
        if looks_like_credential(text):
            return _bad("Use words rather than credentials in a reply")
        reply_id = document.get("reply_id")
        if not isinstance(reply_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reply_id):
            return _bad("reply_id must identify this reply for safe retries")
        item_id = str(document.get("item_id") or "")
        if item_id and item_id not in {item["item_id"] for item in row.get("items", [])}:
            return {"error": "not_found", "resource": "request_item"}
        return request_answers.reply(udir, row, text.strip(), reply_id, item_id=item_id)
    if row["action"].get("type") == "notify":
        return {"error": "not_answerable",
                "detail": "This notification needs no answer; dismiss it with withdraw."}
    # Consult the immutable pin too: editing a publish/install row into a plain
    # question must not let a bearer reach its pinned executable action.
    pinned = _consent_pin(_uid, request_id)
    action_type = (pinned["record"]["action"] if pinned else row["action"]).get("type")
    if action_type not in NON_CONSENT_ACTIONS and owner_session is None:
        return {"error": "interactive_approval_required",
                "detail": CONSENT_REQUIRED_DETAIL,
                "request_pending": row["status"] == "pending"}
    if row["status"] != "pending":
        return {"error": "already_resolved", "status": row["status"]}

    item_id = str(document.get("item_id") or "").strip()
    if item_id:
        return _answer_item(
            udir=udir, row=row, item_id=item_id, document=document,
        )

    if document.get("dismiss") is True:
        fb = str(document.get("feedback") or "").strip()[:_MAX_ANSWER_CHARS]
        if fb and looks_like_credential(fb):
            return _bad(
                "that feedback looks like it contains a credential; it is stored "
                "in the clear, so say it in words instead"
            )
        again = document.get("dont_ask_again") is True
        if not resolve_request(udir, request_id, status="dismissed", feedback=fb,
                               dont_ask_again=again, decision="declined"):
            return {"error": "request_storage_unavailable", "request_pending": True}
        return {
            "status": "dismissed",
            "request_id": request_id,
            "feedback": fb,
            "suppressed": again,
        }

    if str(document.get("decision") or "").strip().lower() == "declined":
        # Deny (founder 2026-09-02: "accept or denial or clear or send reply").
        # A recorded no, distinct from Clear: the agent reads a decision, and
        # with "don't ask me this again" it is a standing one. Nothing below
        # runs -- for an action-bearing ask the answer IS the act, so a deny
        # that fell through would extend the grant it was refusing.
        fb = str(document.get("feedback") or "").strip()[:_MAX_ANSWER_CHARS]
        if fb and looks_like_credential(fb):
            return _bad(
                "that feedback looks like it contains a credential; it is stored "
                "in the clear, so say it in words instead"
            )
        again = document.get("dont_ask_again") is True
        if not resolve_request(udir, request_id, status="answered", answer=None,
                               feedback=fb, dont_ask_again=again, decision="declined"):
            return {"error": "request_storage_unavailable", "request_pending": True}
        return {
            "status": "answered",
            "decision": "declined",
            "request_id": request_id,
            "answer": None,
            "feedback": fb,
            "suppressed": again,
        }

    # An approval the user disagrees with is worth more than a silent no, and a
    # user who never wants this ask again should not have to keep declining it
    # (founder 2026-08-27). Both ride the answer rather than being a separate
    # surface, because the moment of answering is when the user has the opinion.
    feedback = str(document.get("feedback") or "").strip()[:_MAX_ANSWER_CHARS]
    dont_ask_again = document.get("dont_ask_again") is True
    # Matching the other half of the same rule (see create_request): an
    # action-bearing ask is the only way its act happens, so a standing "stop
    # asking" would silently disable the capability rather than express a
    # preference. Recording one is refused here as well as ignored there --
    # either alone leaves the hole reachable from the other direction.
    if dont_ask_again and str((row["action"] or {}).get("type") or "answer") != "answer":
        dont_ask_again = False

    values = document.get("values")
    if not isinstance(values, dict):
        return _bad("values must be an object of field name -> value")

    action = row["action"]
    # A pinned ask executes its platform record, whatever the row now says, and
    # the record is looked up before any row field decides the dispatch.
    pinned = _consent_pin(_uid, request_id)
    if pinned is not None:
        action = pinned["record"]["action"]
    elif str(action.get("type") or "") in _PINNED_ACTIONS:
        return _bad(_UNPINNED_BODY)
    # Fields were validated when the ask was CREATED, and a row stored before
    # these rules existed carries a shape they would refuse: a `text` field
    # beside the secret (whose answer is recorded in the clear), or several
    # secrets under a single-value scheme (which would assemble into JSON and be
    # deposited as a bearer token). Answering does not re-run the validator, so
    # a legacy row bypassed both fixes entirely (Codex round 2, Q2/Q5).
    #
    # Revalidate the STORED fields here, against the stored action. A row that
    # no longer passes is refused with the reason rather than half-honoured:
    # the ask is re-raisable in seconds and the owner has typed nothing yet at
    # the moment this runs.
    # BIND the row that executes to the row that was displayed. The dedupe key
    # is a hash of exactly [kind, title, body, fields, action] -- the tuple the
    # tab renders from -- so a stored row whose action_json was rewritten after
    # rendering no longer reproduces it. Without this, an owner could confirm a
    # tab that reads "also let me write one more file" and have it delete their
    # credential instead: the fields check below never ran for a fieldless row,
    # and nothing ever compared the action to what was on screen.
    if not displayed_row_matches(row):
        return _bad(
            "this request no longer matches what it was created as, so what "
            "you were shown is not what would happen; ask again"
        )
    if action.get("type") in {"connect_http", "connect", "extend_http"} and any(
        e.get("redirect_mode") == "public_https_get" for e in action.get("endpoints", [])
    ):
        if type(action.get("redirect_consent_version")) is not int or (
            action["redirect_consent_version"] != 1
        ):
            return _bad("This older request did not disclose redirect permission; ask again.")
    if row["fields"]:
        try:
            _validated_fields(row["fields"], action)
        except ValueError as exc:
            return _bad(
                "this request was created before the current rules and cannot "
                f"be answered safely ({exc}); ask again and it will be built "
                "correctly"
            )
    secret_names = {f["name"] for f in row["fields"] if f["type"] == "secret"}
    # Record ONLY values for fields the request actually declared as non-secret.
    # Excluding known secret names was not enough: Codex (2026-08-27) submitted
    # the credential under an UNDECLARED key and it was persisted verbatim. An
    # allow-list of declared names has nowhere for an extra key to land.
    recordable = {
        f["name"] for f in row["fields"] if f["type"] != "secret"
    }
    answer = {
        str(k): str(v)[:_MAX_ANSWER_CHARS]
        for k, v in values.items()
        if str(k) in recordable
    }
    # Feedback is free text the user types, so it can hold anything — including a
    # credential pasted into the wrong box. Same shape screen the resolver uses.
    if feedback and looks_like_credential(feedback):
        return _bad(
            "that feedback looks like it contains a credential; it is stored in "
            "the clear, so say it in words instead"
        )

    if row["kind"] == "proposal":
        if values:
            return _bad("proposal approval has no fields")
        if document.get("decline") is True:
            result = {}
            decision = "declined"
        else:
            result = _start_approved_proposal(_uid, row)
            if result.get("error"):
                return result
            decision = "allowed"
        if not resolve_request(udir, request_id, status="answered", answer=answer,
                               feedback=feedback, decision=decision):
            return {"error": "request_resolution_unconfirmed", "request_pending": True}
        return {**result, "status": "answered", "decision": decision,
                "request_id": request_id}

    if action.get("type") == "bind_model_access":
        from tinyassets.api.model_access_requests import execute_action
        from tinyassets.exceptions import ProviderError
        from tinyassets.storage.current_home import CurrentHomeChanged
        from tinyassets.storage.model_preferences import PreferenceStoreUnavailable

        try:
            if row["fields"] or values:
                return _bad("model access is a fieldless owner confirmation")
            result = execute_action(_uid, action)
        except (ValueError, LookupError, PermissionError, ProviderError,
                PreferenceStoreUnavailable, CurrentHomeChanged) as exc:
            return {"error": "provider_authority_denied", "detail": str(exc),
                    "request_pending": True}
        except (sqlite3.Error, OSError):
            # Storage was interrupted mid-setup; the consent stays to retry.
            logger.warning("Model setup could not be confirmed; request remains pending",
                           exc_info=True)
            return {"error": "model_setup_unavailable", "request_pending": True}
        except Exception as exc:  # noqa: BLE001 - a bug, not a setup outage; never mislabel it
            logger.exception("Model setup failed unexpectedly (%s) for request %s in %s",
                             type(exc).__name__, request_id, _uid)
            return {"error": "internal_error", "request_pending": True}
        if not resolve_request(udir, request_id, status="answered", answer=answer,
                               feedback=feedback, dont_ask_again=False, decision="allowed"):
            return {"error": "request_resolution_unconfirmed", "request_pending": True}
        return {**result, "status": "answered", "request_id": request_id,
                "receipt": _grant_sentence(row), "secret_reused": True, "suppressed": False}
    if pinned is not None and action.get("type") == "install" and values:
        return _bad("installing is a fieldless owner confirmation")
    if action.get("type") == "install":
        from tinyassets.api.package_requests import execute_action as _execute_install

        try:
            result = _execute_install(_uid, pinned)
        except (ValueError, LookupError, PermissionError) as exc:
            return {"error": "install_refused", "detail": str(exc), "request_pending": True}
        if not resolve_request(udir, request_id, status="answered", answer=answer,
                               feedback=feedback, dont_ask_again=False, decision="allowed"):
            return {"error": "request_resolution_unconfirmed", "request_pending": True}
        from tinyassets.api.command_center_update_surface import after_install

        result = {**result, **after_install(universe_id=_uid, request_id=request_id, result=result)}
        return {**result, "status": "answered", "request_id": request_id,
                "receipt": f"Installed \"{action['plan']['name']}\" as your own copy.",
                "suppressed": False}
    if action.get("type") == "publish":
        from tinyassets.api.publish_requests import after_publish, answer_publish

        try:
            result = answer_publish(_uid, pinned, values, request_id=request_id)
        except (ValueError, LookupError, PermissionError) as exc:
            return {"error": "publish_refused", "detail": str(exc), "request_pending": True}
        from tinyassets.publication_completion import receipt_completion, start_receipt_preview

        result["completion"] = receipt_completion(result, universe_id=_uid, action=action)
        answer = {**answer, "completion": result["completion"]}
        if not resolve_request(udir, request_id, status="answered", answer=answer,
                               feedback=feedback, dont_ask_again=False, decision="allowed"):
            return {"error": "request_resolution_unconfirmed", "request_pending": True}
        result = {**result, **after_publish(_uid, action, result, request_id=request_id)}
        start_receipt_preview(result["completion"], universe_id=_uid, request_id=request_id)
        release_note = (" " + result["release_registration_detail"]
                        if result.get("release_registration") == "unavailable" else "")
        return {**result, "status": "answered", "request_id": request_id,
                "receipt": f"Published \"{action['name']}\" for anyone to copy.{release_note}",
                "suppressed": False}
    if action.get("type") == PATCH_INTAKE_ACTION:
        if row["fields"] or values:
            return _bad(
                "connecting the patch intake is a fieldless owner confirmation; "
                "there is nothing to paste"
            )
        return _grant_patch_intake(
            uid=_uid,
            udir=udir,
            row=row,
            action=action,
            request_id=request_id,
            answer=answer,
            feedback=feedback,
        )
    if action.get("type") == "grant_workspace_consent":
        return _grant_workspace_consent(
            udir=udir,
            row=row,
            action=action,
            request_id=request_id,
            answer=answer,
            feedback=feedback,
            dont_ask_again=dont_ask_again,
        )
    if action.get("type") == "extend_http":
        from tinyassets.api.http_connection import extend_http

        widened = extend_http(
            universe_id=universe_id,
            payload=json.dumps({
                "destination": action["destination"],
                "endpoints": action["endpoints"],
                "scopes": action.get("scopes") or [],
                # The mode the owner read on the tab and accepted. Dropping it
                # here recorded an exact widening for a full yes.
                "access": action.get("access") or "exact",
                # ...and the policy that mode was read against, so the write
                # cannot land on a reach that grew while the tab was open.
                "policy_snapshot": action.get("policy_snapshot") or None,
                # The git host the owner read beside the git scope. The write
                # refuses if the connection now resolves somewhere else.
                **({"expected_git_host": action.get("git_scope_host") or ""}
                   if _grants_git(action) else {}),
            }),
        )
        if widened.get("error"):
            return widened
        resolve_request(udir, request_id, status="answered", answer=answer,
                        feedback=feedback, dont_ask_again=dont_ask_again)
        return {
            "status": "answered",
            "request_id": request_id,
            "destination": action["destination"],
            "access": widened.get("access") or "exact",
            "receipt": _grant_sentence(row),
            "secret_reused": True,
        }
    if action.get("type") == "remove_http":
        from tinyassets.api.http_connection import remove_http

        gone = remove_http(
            universe_id=universe_id,
            payload=json.dumps({"destination": action["destination"],
                                "incarnation": action.get("incarnation", "uncaptured")}),
        )
        if gone.get("error"):
            return gone
        resolve_request(udir, request_id, status="answered", answer=answer,
                        feedback=feedback, dont_ask_again=dont_ask_again)
        return {
            "status": "answered",
            "request_id": request_id,
            "destination": action["destination"],
            "secrets_removed": gone.get("secrets_removed", 0),
            # Carry the readback THROUGH. Added to remove_http and dropped here,
            # which meant it did not exist on the only surface the owner drives
            # (Codex, R3) -- the same "API has it, the served path does not"
            # shape as the round-3 defect.
            "auth_scheme": gone.get("auth_scheme", ""),
            "removed_endpoints": gone.get("removed_endpoints", []),
            "removed_scopes": gone.get("removed_scopes", []),
            "receipt": (
                f'"{action["destination"]}" is gone -- the key, the connection '
                "and its grants. That name is free to deposit again. To put it "
                "back, reuse 'auth_scheme', 'removed_endpoints' and "
                "'removed_scopes' rather than asking what they were."
            ),
        }
    if action.get("type") == "connect" and "model" in (action.get("uses") or {}):
        # Checked again at answer time, BEFORE the deposit: a priced catalogue
        # or non-free accepted access may have appeared since the ask.
        refused = _model_use_refusal(_uid, action)
        if refused is not None:
            return {**refused, "request_pending": True}
    if action.get("type") in _DEPOSIT_TYPES and not secret_names and _has_sign_in(action):
        return _bad(
            "this connection is completed by signing in: use the request's "
            "Sign in button, which returns here connected"
        )
    if action.get("type") == "rotate_http":
        return _rotate_answer(
            universe_id=universe_id, udir=udir, row=row,
            secret_names=secret_names, values=values, answer=answer,
            feedback=feedback, dont_ask_again=dont_ask_again,
        )
    if action.get("type") in _DEPOSIT_TYPES:
        secret, refusal = _assembled_secret(
            scheme=str(action.get("auth_scheme") or "bearer").strip().lower(),
            secret_names=secret_names, values=values,
        )
        if refusal is not None:
            return refusal
        return _deposit_answer(
            universe_id=universe_id, uid=_uid, udir=udir, row=row, secret=secret,
            auth_scheme=action["auth_scheme"], answer=answer, feedback=feedback,
            dont_ask_again=dont_ask_again,
        )

    # For a plain answer the user's own words decide it: an explicit decline
    # field, else answering at all is a yes.
    # Send means allowed, Not now means declined — that is all the surface knows,
    # and it must not try to read intent out of a field value. A caller that DOES
    # know (a choice field it defined) can say so outright.
    stated = str(document.get("decision") or "").strip().lower()
    if stated in {"allowed", "declined"}:
        decided = stated
    else:
        decided = "declined" if document.get("decline") is True else "allowed"
    resolve_request(udir, request_id, status="answered", answer=answer,
                    feedback=feedback, dont_ask_again=dont_ask_again,
                    decision=decided)
    return {
        "status": "answered",
        "decision": decided,
        "request_id": request_id,
        "answer": answer,
        "feedback": feedback,
        "suppressed": dont_ask_again,
    }


def displayed_row_matches(row: dict[str, Any]) -> bool:
    """Whether the stored row still reproduces what the owner was shown.

    The dedupe key is a hash of exactly
    [kind, title, body, fields, action, items] -- the tuple the tab renders
    from -- so a row whose action was rewritten after rendering no longer
    reproduces it and must not execute. ``items`` is projected VERBATIM (the
    owner's answers live under ``item_answers``) precisely so that answering
    one item does not make the row stop reproducing itself.

    An itemless request's key is the original five elements, unchanged by items
    existing, so a row stored before them still reproduces itself and every
    standing decision keyed on it still matches. Only a request that HAS items
    carries the sixth. One shape per row -- "try a few shapes" would defeat the
    pin.
    """
    if row.get("kind") == "proposal":
        from tinyassets.proposals import displayed_row_matches as proposal_matches

        return proposal_matches(row)
    stored = row.get("dedupe_key")
    if not stored:
        return True
    identity = [row["kind"], row["title"], row["body"], row["fields"], row["action"]]
    if row.get("items"):
        identity.append(row["items"])
    from tinyassets.storage.pending_requests import scoped_dedupe_key

    expected = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    # A non-main agent's key carries its agent (harness §4.18); main's is bare.
    return stored == scoped_dedupe_key(expected, str(row.get("agent") or "main"))


def _assembled_secret(
    *, scheme: str, secret_names: set[str], values: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """The vault string a card's secret boxes make, or ``("", refusal)``.

    ONE secret field -> its value. SEVERAL -> a JSON object keyed by field name,
    which is the encoding a multi-value scheme's vault string uses.

    This used to be `next(...)`: the FIRST secret field's value, with every other
    one silently discarded. Harmless while a credential ask was one unlabelled
    box, and broken the moment asks became one field per value -- an OAuth 1.0a
    owner would fill four boxes, three would vanish, and the deposit would refuse
    a malformed bundle with nothing to explain it. Found on 2026-08-31 by checking
    this seam rather than assuming it.

    One function, because a deposit and a rotation are the same assembly: two
    copies would drift, and the copy that drifted would be the one that stores a
    credential that cannot sign.
    """
    supplied = {
        name: str(values.get(name) or "")
        for name in sorted(secret_names)
        if str(values.get(name) or "").strip()
    }
    if not supplied:
        return "", _bad("the key is required")
    # Completeness is judged against what the ask DECLARED, never against what
    # came back. Keying off the supplied count meant filling one box of four took
    # the single-value branch and deposited that one value as the whole
    # credential, skipping this check entirely (Codex, Q4).
    missing = sorted(secret_names - set(supplied))
    if missing:
        # Partial is worse than refused: a bundle short one value deposits a
        # credential that cannot sign, and the owner is told later, by a failing
        # call, with no idea which box was empty.
        return "", _bad(
            "this needs every value: still missing "
            + ", ".join(repr(name) for name in missing)
        )
    if len(secret_names) == 1:
        return next(iter(supplied.values())), None
    if scheme == "basic":
        # The vault string for basic has always been `username:password`; JSON
        # here would hand the service `Basic base64({...})`.
        return f"{supplied['username']}:{supplied['password']}", None
    return json.dumps(supplied), None


def _rotate_answer(
    *, universe_id: str, udir: Any, row: dict[str, Any], secret_names: set[str],
    values: dict[str, Any], answer: dict[str, Any], feedback: str,
    dont_ask_again: bool,
) -> dict[str, Any]:
    """The owner pasted a replacement key. Swap it and leave everything else.

    The scheme is the one READ OFF THE CONNECTION when this ask was raised, so
    the boxes the owner filled and the string the vault receives agree. The
    incarnation captured then rides along: if the key was removed and a different
    one deposited while this tab sat open, the write refuses rather than replacing
    a key the owner never saw this card for.
    """
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.storage.pending_requests import resolve_request

    action = row["action"]
    request_id = row["request_id"]
    secret, refusal = _assembled_secret(
        scheme=str(action.get("auth_scheme") or "bearer").strip().lower(),
        secret_names=secret_names, values=values,
    )
    if refusal is not None:
        return refusal
    rotated = rotate_http(
        universe_id=universe_id,
        payload=json.dumps({
            "destination": action["destination"],
            "secret": secret,
            "incarnation": action.get("incarnation", ""),
        }),
    )
    if rotated.get("error"):
        # Leave it PENDING: the key did not land, and closing the tab here would
        # lose the ask with the connection still dead.
        return {**rotated, "request_pending": True}
    if not resolve_request(udir, request_id, status="answered", answer=answer,
                           feedback=feedback, dont_ask_again=dont_ask_again,
                           decision="allowed"):
        # The key IS replaced and the card did NOT close (`resolve_request`
        # catches a storage fault and returns False). Reporting "answered" here
        # would leave a pending card the owner believes is done, answerable again
        # later — and because a rotation does not move the incarnation, a replay
        # would overwrite a LATER replacement with this older value. So say what
        # actually happened. Re-answering with the same value is idempotent, which
        # is why this is recoverable rather than an error to undo (Codex
        # refute-review, P1 #1; same shape as the model-access branch).
        return {
            "error": "request_resolution_unconfirmed",
            "request_pending": True,
            "destination": action["destination"],
            "detail": (
                "the key was replaced, but this request could not be closed. "
                "Answer it again with the same key to settle it; nothing is "
                "replaced twice."
            ),
        }
    return {
        "status": "answered",
        "request_id": request_id,
        "suppressed": dont_ask_again,
        "destination": action["destination"],
        "connection_id": rotated.get("connection_id"),
        "allowed_endpoints": rotated.get("allowed_endpoints") or [],
        "git_scopes": rotated.get("git_scopes") or [],
        "receipt": (
            f'The key for "{action["destination"]}" is replaced. Same connection, '
            "same access you already approved; nothing new was allowed and "
            "nothing needs re-approving."
        ),
        "in_flight": rotated.get("in_flight", ""),
    }


def _deposit_answer(
    *, universe_id: str, uid: str, udir: Any, row: dict[str, Any], secret: str,
    auth_scheme: str, answer: dict[str, Any], feedback: str, dont_ask_again: bool,
) -> dict[str, Any]:
    """Deposit under the policy stored ON THE REQUEST, then finish a connect.

    One path for a pasted key and for a completed sign-in (``oauth2`` tokens),
    so what the owner was shown is what gets granted either way.
    """
    from tinyassets.api.http_connection import connect_http
    from tinyassets.storage.pending_requests import resolve_request

    action = row["action"]
    request_id = row["request_id"]
    deposited = connect_http(
        universe_id=universe_id,
        payload=json.dumps(
            {
                "destination": action["destination"],
                "secret": secret,
                "auth_scheme": auth_scheme,
                "allowed_endpoints": action["endpoints"],
                "scopes": action.get("scopes") or [],
                # As above: the owner accepted a full channel, so the
                # connection is created full. It was stored exact, and the
                # first call outside the recorded endpoints was refused.
                "access": action.get("access") or "exact",
                # Where git goes, as the owner read it in the grant.
                "git_host": action.get("git_host") or "",
            }
        ),
        allow_oauth2=auth_scheme == "oauth2",
    )
    if deposited.get("error"):
        # Leave it PENDING: the answer did not land, and closing the tab
        # here would lose the ask with nothing deposited.
        return deposited
    extra: dict[str, Any] = {}
    if action.get("type") == "connect":
        extra = _complete_connect(uid, action, deposited)
        if extra.get("error"):
            # The key is in the vault, but the uses did not land. Leave the
            # ask PENDING: answering again re-deposits idempotently and
            # retries the uses, so nothing is half-granted for long.
            return {**extra, "request_pending": True}
    if not resolve_request(udir, request_id, status="answered", answer=answer,
                           feedback=feedback, dont_ask_again=dont_ask_again,
                           decision="allowed"):
        return {"error": "request_storage_unavailable", "request_pending": True}
    return {
        "status": "answered",
        "request_id": request_id,
        "suppressed": dont_ask_again,
        "destination": action["destination"],
        "receipt": _grant_sentence(row).replace("will be able to", "may"),
        "connection_id": deposited.get("connection_id"),
        "server_continuation": bool(row.get("server_continuation")),
        **({"signed_in": True} if auth_scheme == "oauth2" else {}),
        **extra,
    }


@_coordinated
def answer_connect_with_token(
    *, universe_id: str = "", request_id: str = "", token: str = "",
    owner_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The owner answered a sign-in-capable ``connect`` by signing in.

    Called only by the sign-in flow after a code exchange, with the token bundle
    as the secret. The same checks as a pasted answer run first: owner, still
    pending, still what was shown, and the money floor for a model use.
    """
    from tinyassets.storage.pending_requests import get_request

    uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    row = get_request(udir, request_id) if request_id else None
    if row is None:
        return {"error": "not_found", "resource": "pending_request"}
    if owner_session is None:
        return {"error": "interactive_approval_required", "detail": CONSENT_REQUIRED_DETAIL,
                "request_pending": row["status"] == "pending"}
    if row["status"] != "pending":
        return {"error": "already_resolved", "status": row["status"]}
    action = row["action"]
    if not _has_sign_in(action) or not displayed_row_matches(row):
        return _bad("this request no longer offers sign-in as it was shown; ask again")
    # PIN: the bundle's token URL (where every refresh token will go) must be
    # the discovered one the owner approved on this request.
    from tinyassets.connection_oauth.tokens import decode

    try:
        pinned = decode(token).token_url == action["oauth"]["token_url"]
    except (TypeError, ValueError, KeyError):
        pinned = False
    if not pinned:
        return _bad("the sign-in's token endpoint is not the one this request showed")
    if "model" in (action.get("uses") or {}):
        refused = _model_use_refusal(uid, action)
        if refused is not None:
            return {**refused, "request_pending": True}
    return _deposit_answer(
        universe_id=universe_id, uid=uid, udir=udir, row=row, secret=token,
        auth_scheme="oauth2", answer={}, feedback="", dont_ask_again=False,
    )


def _model_use_refusal(uid: str, action: dict[str, Any]) -> dict[str, Any] | None:
    """The money-floor refusal for a declared model use on this destination, if any."""
    from tinyassets.api import permissions
    from tinyassets.api.connection_uses import model_use_refusal
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.http_connection import _ids
    from tinyassets.principals import named_principal

    connection_id, grant_id = _ids(universe_id=uid, destination=action["destination"])
    return model_use_refusal(
        base=_base_path(), uid=uid, actor=named_principal(permissions.current_actor_id()),
        connection_id=connection_id, grant_id=grant_id,
    )


def _complete_connect(
    uid: str, action: dict[str, Any], deposited: dict[str, Any],
) -> dict[str, Any]:
    """The rest of one ``connect`` answer, after the deposit landed.

    Records the uses and constant headers on the new connection, registers its
    model source, and serves the universe on it if nothing powers it yet.
    """
    from tinyassets.api import permissions
    from tinyassets.api.connection_uses import (
        apply_connection_uses,
        select_model_if_unpowered,
    )
    from tinyassets.api.helpers import _base_path
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    base = _base_path()
    applied = apply_connection_uses(
        base=base, uid=uid, actor=actor, grant_id=str(deposited.get("grant_id") or ""),
        uses=action.get("uses") or {"call": {}},
        constant_headers=action.get("constant_headers") or {},
        owner_confirmed=True,
    )
    if applied.get("error"):
        return applied
    out: dict[str, Any] = {
        "grant_id": applied["grant_id"],
        "uses": applied["uses"],
    }
    if "constant_headers" in applied:
        out["constant_headers"] = applied["constant_headers"]
    if "provider" in applied:
        out["provider"] = applied["provider"]
        out["definition_id"] = applied["definition_id"]
        from tinyassets.exceptions import ProviderError

        try:
            out["serving"] = select_model_if_unpowered(
                base=base, uid=uid, actor=actor, definition_id=applied["definition_id"],
                model=applied["model"],
            )
        except (PermissionError, ValueError, LookupError, ProviderError, OSError):
            # The deposit succeeded, but it must not look like accepted model
            # access. Keep the request pending and report actual serving state.
            return {**out, "error": "model_source_acceptance_failed",
                    "detail": "Connection saved, but the model source was not accepted. "
                              "In model setup, confirm explicit model access for your existing "
                              "source and ensure exactly one owned agent is serving; "
                              "then retry this request.",
                    "serving": {"status": "unchanged" if _serving_llm_bound(base, uid, actor)
                                else "disabled", "reason": "model_source_acceptance_failed"}}
    return out


__all__ = [
    "answer_connect_with_token",
    "answer_request",
    "list_requests",
    "request_from_user",
    "unmute_request",
    "withdraw_request",
]
