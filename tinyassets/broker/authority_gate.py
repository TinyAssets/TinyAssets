"""N10: the binding owner-authority gate inside a box's OPEN.

The box's own rule and consent checks are advisory; this gate is the one an
agent's shell cannot skip by writing OPEN to its relay socket. Nothing it
relies on is the box's to write: no call is exempt by a ledger fact the box
channel can set (a model call is gated like any other; connecting a model
source on the router is the owner's consent to call it).

Rules apply to every call by the broker's classification; consent applies to
non-read calls only. A GET or HEAD the owner has not declared as anything else
is a read, unless it carries a body or a method-override header.

The box's labels are distrusted: an agent label the owner never configured
decides as ``main``; a task id or binding revision is not accepted at all, so
a task-scoped standing grant never matches here (that call is asked again)
and a standing grant matches on the broker's own facts only.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from urllib.parse import urlsplit

import rfc8785

#: Never trust the box's agent label for these: the strictest rule of any agent.
STRICTEST_AGENT = frozenset({"security.change", "access.grant", "money.move"})
REFUSALS = frozenset({"policy_ask", "policy_hand_off", "consent_missing",
                      "authority_import_pending"})
AUTHORITY_FIELDS = frozenset({"agent", "approval"})
CALL_SINK = "authenticated_external_call"
WORKSPACE_SINK = "workspace"
MAIN_AGENT = "main"
_READ_METHODS = frozenset({"GET", "HEAD"})
_METHOD_OVERRIDES = frozenset({"x-http-method-override", "x-method-override", "x-http-method"})


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


def require_grantable(ledger, owner: str, sink: str, destination: str) -> None:
    """A workspace consent names a live connection of this owner at its own git host."""
    if sink != WORKSPACE_SINK:
        return
    from tinyassets.broker.ledger_queries import owner_connection
    from tinyassets.storage.workspace_authority import (
        connection_git_host,
        parse_workspace_consent_destination,
    )

    parsed = parse_workspace_consent_destination(destination)
    resource = (owner_connection(ledger, owner, parsed["connection_id"])
                if parsed is not None else None)
    if resource is None or connection_git_host(resource) != parsed["host"]:
        raise PermissionError("workspace consent names no live connection at its host")


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


def check(store, ledger, *, owner: str, grant_id: str, connection_id: str, verb: str,
          request: dict, request_digest: str, authority: dict) -> str | None:
    """None when the owner's authority admits this OPEN, else a refusal class."""
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

    if store.import_pending(owner):
        # Retryable: boxhostd lifts the old authority on the next wake.
        return "authority_import_pending"
    scope = dict(principal=owner, command_center=owner, grant_id=grant_id,
                 connection_id=connection_id)
    grant, resource, incarnation = authority_from_facts(
        local_query(ledger, query=AUTHORIZED_CONNECTION, **scope),
        principal=owner, grant_id=grant_id, connection_id=connection_id)
    view = resource.to_view()
    agent = authority.get("agent") or MAIN_AGENT
    if verb.startswith(("git_read:", "git_write:")):
        from tinyassets.broker.git_http import git_target

        try:
            kind, repo = git_target(resource, verb, request)
        except (PermissionError, ValueError):
            store.receipt_gate(owner, request_digest, f"git_target agent={agent}"[:512], "deny")
            return "refused"
        action_class, operation, read = (
            ("app.read", "GIT_READ", True) if kind == "git_read"
            else ("app.write", "GIT_WRITE", False))
        destination = ""
        if not read and str(view.access_mode).lower() != "full":
            from tinyassets.storage.workspace_authority import (
                CONSENT_PUSH,
                connection_git_host,
                workspace_consent_destination,
            )

            destination = workspace_consent_destination(
                CONSENT_PUSH, repo, connection_id=connection_id,
                host=connection_git_host(view))
        sink, origin = WORKSPACE_SINK, ""
    else:
        url = str(request.get("url") or "")
        path = urlsplit(url).path or "/"
        method = str(verb).upper()
        action_class, operation = store.classify(owner, connection_id, method, path)
        if smuggles_write(method, request):
            read = False
            if action_class == "app.read":
                action_class = "app.write"
        else:
            read = action_class == "app.read" or (
                method in _READ_METHODS
                and store.declared_kind(owner, connection_id, method, path) is None)
        sink, destination, origin = CALL_SINK, "" if read else view.destination, _origin(url)
    agents, label = _agents(store, owner, agent, action_class)
    decision = max((store.decide(owner, center=owner, action_class=action_class,
                                 connection=connection_id, operation=operation,
                                 destination=origin, agent=candidate)
                    for candidate in agents),
                   key=lambda result: ("allow", "hold", "ask", "deny").index(
                       result["decision"]))
    if decision["decision"] == "deny":
        refusal = "policy_hand_off"
    elif destination and not store.consent_active(owner, sink, destination):
        # Before any once-approval is spent: a refused call keeps its approval.
        refusal = "consent_missing"
    elif decision["decision"] == "allow":
        refusal = None
    elif store.consume_once(owner, authority.get("approval"), request_digest):
        refusal = None
    else:
        consent = [row for row in store.consents(owner)
                   if row["destination"] == view.destination]
        candidate = {
            "owner": owner, "agent": label, "action_class": action_class,
            "operation": operation,
            "connection": connection_id, "origin": origin,
            "connection_revision": digest([view.as_dict(), incarnation]),
            "consent_digest": digest([asdict(grant), consent]),
        }
        matched = store.standing_grant(owner, candidate)
        refusal = None if matched is not None else "policy_ask"
    store.receipt_gate(owner, request_digest,
                       f"{refusal or decision['rule']} agent={agent}"[:512],
                       "allow" if refusal is None else "deny")
    return refusal
