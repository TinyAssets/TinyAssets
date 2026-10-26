"""N10: the binding owner-authority gate inside a box's OPEN.

The box's own rule and consent checks are advisory; this gate is the one an
agent's shell cannot skip by writing OPEN to its relay socket. A model source
is exempt only by the broker's own ledger facts. Rules apply to every other
call by the broker's classification; consent applies to non-read calls only
(a GET or HEAD the owner has not declared as anything else is a read).
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from urllib.parse import urlsplit

import rfc8785

#: Never trust the box's agent label for these: the strictest rule of any agent.
STRICTEST_AGENT = frozenset({"security.change", "access.grant", "money.move"})
REFUSALS = frozenset({"policy_ask", "policy_hand_off", "consent_missing"})
AUTHORITY_FIELDS = frozenset({"agent", "approval", "task_id", "binding_revision"})
CALL_SINK = "authenticated_external_call"
WORKSPACE_SINK = "workspace"
_READ_METHODS = frozenset({"GET", "HEAD"})


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


def check(store, ledger, *, owner: str, grant_id: str, connection_id: str, verb: str,
          request: dict, request_digest: str, authority: dict) -> str | None:
    """None when the owner's authority admits this OPEN, else a refusal class."""
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        HAS_MODEL_SOURCE,
        authority_from_facts,
        local_query,
    )

    store.abandon_import(owner)
    scope = dict(principal=owner, command_center=owner, grant_id=grant_id,
                 connection_id=connection_id)
    if (ledger.grant_requires_inference_usage(grant_id)
            or local_query(ledger, query=HAS_MODEL_SOURCE, **scope)["model_source"]):
        return None
    grant, resource, incarnation = authority_from_facts(
        local_query(ledger, query=AUTHORIZED_CONNECTION, **scope),
        principal=owner, grant_id=grant_id, connection_id=connection_id)
    view = resource.to_view()
    agent = authority.get("agent") or "*"
    if verb.startswith(("git_read:", "git_write:")):
        kind, repo = verb.split(":", 1)
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
        read = action_class == "app.read" or (
            method in _READ_METHODS
            and store.declared_kind(owner, connection_id, method, path) is None)
        sink, destination, origin = CALL_SINK, "" if read else view.destination, _origin(url)
    agents = [agent]
    if action_class in STRICTEST_AGENT:
        # Every agent's effective rule, defaults included; the strictest decides.
        agents = sorted({"main"} | {
            entry["values"].get("agent", "main")
            for entry in store.read(owner).document["entries"]
            if entry["table"] in ("rules", "review_on")})
    decision = max((store.decide(owner, center=owner, action_class=action_class,
                                 connection=connection_id, operation=operation,
                                 destination=origin, agent=candidate)
                    for candidate in agents),
                   key=lambda result: ("allow", "hold", "ask", "deny").index(
                       result["decision"]))
    if decision["decision"] == "deny":
        refusal = "policy_hand_off"
    elif decision["decision"] == "allow":
        refusal = None
    elif store.consume_once(owner, authority.get("approval"), request_digest):
        refusal = None
    else:
        consent = [row for row in store.consents(owner)
                   if row["destination"] == view.destination]
        candidate = {
            "owner": owner, "agent": agent, "action_class": action_class,
            "operation": operation, "connection": connection_id, "origin": origin,
            "connection_revision": digest([view.as_dict(), incarnation]),
            "consent_digest": digest([asdict(grant), consent]),
            "binding_revision": authority.get("binding_revision", ""),
        }
        matched = store.standing_grant(owner, candidate, task_id=authority.get("task_id", ""))
        refusal = None if matched is not None else "policy_ask"
    if refusal is None and destination and not store.consent_active(owner, sink, destination):
        refusal = "consent_missing"
    store.receipt_gate(owner, request_digest,
                       f"{refusal or decision['rule']} agent={agent}"[:512],
                       "allow" if refusal is None else "deny")
    return refusal
