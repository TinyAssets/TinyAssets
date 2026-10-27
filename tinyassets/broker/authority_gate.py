"""N10: the binding owner-rules gate inside a box's OPEN.

The box's own rule checks are advisory; this gate is the one an agent's shell
cannot skip by writing OPEN to its relay socket. Nothing it relies on is the
box's to write: the call's meaning is the owner's declarations and the broker's
own reading of the request, never a ledger flag the box channel can set.

The box's labels are distrusted: an agent label the owner never configured
decides as ``main``; a task id is not accepted at all, so a task-scoped
standing grant never matches here (that call is asked again) and a standing
grant matches on the broker's own facts only.

The label's scope is the box's word: once the owner configures an agent, its
rules join ``main``'s (strictest wins), and a standing grant naming that agent
covers whatever the box labels as that agent. The broker cannot tell which
process inside the box made the call, so a per-agent grant is a grant to the
box under that name.

An OPEN made for an outside client (P0-3) carries its grant as stamped:
``client:generation:authenticated_at``. The broker refuses it once that grant has
changed or been revoked, and a scoped client's own rules join the decision. Like the
agent label, it is the box's word: an omitted label decides as the owner, and the
binding control on an outside client is the router's admission.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

import rfc8785

#: Never trust the box's agent label for these: the strictest rule of any agent.
STRICTEST_AGENT = frozenset({"security.change", "access.grant", "money.move"})
REFUSALS = frozenset({"policy_ask", "policy_hand_off"})
AUTHORITY_FIELDS = frozenset({"agent", "approval", "outside"})
MAIN_AGENT = "main"
_READ_METHODS = frozenset({"GET", "HEAD"})
_METHOD_OVERRIDES = frozenset({"x-http-method-override", "x-method-override", "x-http-method"})
_ORDER = ("allow", "hold", "ask", "deny")


def digest(value) -> str:
    """``bound_requests.digest``: the preview's revisions, recomputed here."""
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def authority_fields(value: object) -> dict:
    """The box-asserted labels an OPEN may carry; anything else is refused."""
    if value is None:
        return {}
    if (not isinstance(value, dict) or not set(value) <= AUTHORITY_FIELDS
            or any(not isinstance(item, str) or len(item) > 512 for item in value.values())):
        raise ValueError("invalid OPEN authority")
    return value


def _origin(url: str) -> str:
    parts = urlsplit(url)
    origin = f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}"
    if parts.port and parts.port != 443:
        origin += f":{parts.port}"
    return origin


def _header_names(headers: object) -> set[str]:
    if isinstance(headers, dict):
        names = headers.keys()
    elif isinstance(headers, (list, tuple)):
        names = [pair[0] for pair in headers if isinstance(pair, (list, tuple)) and pair]
    else:
        names = ()
    return {str(name).strip().lower() for name in names}


def smuggles_write(method: str, request: dict) -> bool:
    """A GET or HEAD that carries a body or a method override is not a read."""
    return method in _READ_METHODS and (
        request.get("body") is not None
        or bool(_header_names(request.get("headers")) & _METHOD_OVERRIDES))


def once_wire(url: str, request: dict) -> dict:
    """The wire request an approved call sends: the router digests exactly this.

    The effector builds its OPEN request with this same function, so the
    once-approval minted on the card and the call it approves cannot drift.
    """
    wire: dict = {"url": url}
    if request.get("headers") is not None:
        wire["headers"] = request["headers"]
    if "body" in request:
        wire["body"] = request["body"]
    header_name = request.get("header_name")
    if isinstance(header_name, str) and header_name.strip():
        wire["header_name"] = header_name.strip()
    return wire


def once_request_digest(*, grant_id: str, connection_id: str, verb: str, request: dict,
                        destination: str) -> str:
    """The OPEN digest of the call a card shows: what a once-approval is minted for."""
    from tinyassets.broker.server import request_digest

    return request_digest(grant_id=grant_id, connection_id=connection_id, verb=verb,
                          request=once_wire(destination, request))


def _agents(store, owner: str, asserted: str, action_class: str) -> tuple[list[str], str]:
    """The agents whose rules decide, and the label a standing grant must name.

    ``main`` always decides; the asserted agent joins it only if the owner
    configured that agent (a rule, a review setting or a standing grant for
    it), so a label the owner never configured is ``main``. Joining only adds a
    candidate to the strictest-wins decision; it never makes one laxer.
    """
    configured = {entry["values"].get("agent", MAIN_AGENT)
                  for entry in store.read(owner).document["entries"]
                  if entry["table"] in ("rules", "review_on", "approval_grants")}
    label = asserted if asserted in configured else MAIN_AGENT
    if action_class in STRICTEST_AGENT:
        # Every agent's effective rule, defaults included; the strictest decides.
        return sorted({MAIN_AGENT} | configured), label
    return sorted({MAIN_AGENT, label}), label


def _outside(store, owner: str, label: str) -> tuple[str, str]:
    """(client, mode) of the outside grant an OPEN is labelled with, unchanged since."""
    client, _, rest = label.partition(":")
    generation, _, authenticated = rest.partition(":")
    grant = store.outside_check(owner, client, int(authenticated) if authenticated else None)
    if str(grant["generation"]) != generation:
        raise PermissionError("outside client authority changed")
    return client, grant["mode"]


def _meaning(store, owner: str, connection_id: str, verb: str,
             request: dict) -> tuple[str, str, str]:
    """(action class, operation, origin) by the broker's own reading of the call."""
    if verb.startswith(("git_read:", "git_write:")):
        from tinyassets.storage.workspace_authority import require_git_scope

        kind, repo = require_git_scope(verb)
        # Only a git_read reaching exactly its repo's upload-pack is a read.
        reads = kind == "git_read" and (request.get("method"), request.get("target")) in {
            ("GET", f"/{repo}.git/info/refs?service=git-upload-pack"),
            ("POST", f"/{repo}.git/git-upload-pack")}
        origin = "https://" + str(request.get("host") or "").lower()
        return (("app.read", "GIT_READ", origin) if reads
                else ("app.write", "GIT_WRITE", origin))
    url = str(request.get("url") or "")
    method = str(verb).upper()
    action_class, operation = store.classify(owner, connection_id, method,
                                             urlsplit(url).path or "/")
    if action_class == "app.read" and smuggles_write(method, request):
        action_class = "app.write"
    return action_class, operation, _origin(url)


def check(store, ledger, *, owner: str, grant_id: str, connection_id: str, verb: str,
          request: dict, request_digest: str, authority: dict) -> str | None:
    """None when the owner's rules admit this OPEN, else a refusal class."""
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

    agent = authority.get("agent") or MAIN_AGENT
    try:
        action_class, operation, origin = _meaning(store, owner, connection_id, verb, request)
        client, mode = (_outside(store, owner, authority["outside"])
                        if authority.get("outside") else ("", "full"))
    except (PermissionError, ValueError):
        store.receipt_gate(owner, request_digest, f"unreadable agent={agent}"[:512], "deny")
        return "refused"
    agents, label = _agents(store, owner, agent, action_class)
    if mode == "scoped":  # the owner's rules for this outside agent join, strictest wins
        agents = sorted({*agents, "outside:" + client})
    decision = max((store.decide(owner, center=owner, action_class=action_class,
                                 connection=connection_id, operation=operation,
                                 destination=origin, agent=candidate)
                    for candidate in agents),
                   key=lambda result: _ORDER.index(result["decision"]))
    if decision["decision"] == "deny":
        refusal = "policy_hand_off"
    elif decision["decision"] == "allow":
        refusal = None
    elif store.consume_once(owner, authority.get("approval"), request_digest):
        refusal = None
    else:
        _grant, resource, incarnation = authority_from_facts(
            local_query(ledger, query=AUTHORIZED_CONNECTION, principal=owner,
                        command_center=owner, grant_id=grant_id,
                        connection_id=connection_id),
            principal=owner, grant_id=grant_id, connection_id=connection_id)
        candidate = {
            "owner": owner, "agent": label, "action_class": action_class,
            "operation": operation, "connection": connection_id, "origin": origin,
            "connection_revision": digest([resource.to_view().as_dict(), incarnation]),
        }
        refusal = None if store.standing_grant(owner, candidate) is not None else "policy_ask"
    store.receipt_gate(owner, request_digest,
                       f"{refusal or decision['rule']} agent={agent}"[:512],
                       "allow" if refusal is None else "deny")
    return refusal
