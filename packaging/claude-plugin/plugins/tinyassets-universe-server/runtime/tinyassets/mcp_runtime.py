"""Remote MCP lifecycle and ta adapter on the existing connection authority."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from urllib.parse import urlsplit

from tinyassets.broker.aclient import AsyncBrokerClient
from tinyassets.broker.capabilities import capability_operation
from tinyassets.broker.ledger_queries import authorized_connection
from tinyassets.mcp_attachment import Attachment, metadata
from tinyassets.mcp_remote import Binding, McpError, RemoteMcp, requires_approval


def binding(home, owner, grant_id, connection_id):
    _, _, incarnation = authorized_connection(
        home.parent, principal=owner, command_center=home.name,
        grant_id=grant_id, connection_id=connection_id)
    raw = metadata(home.parent, principal=owner, command_center=home.name,
                   grant_id=grant_id, connection_id=connection_id, incarnation=incarnation)
    if raw is None:
        raise McpError("MCP attachment unavailable")
    return Binding(grant_id, connection_id, incarnation, Attachment.parse(raw))


def activation_complete(home, bound):
    from tinyassets.storage.pending_requests import get_request

    request_id = bound.attachment.activation_request_id
    if not request_id:
        return False
    row = get_request(home, request_id)
    return bool(row and row["status"] == "answered"
                and row["action"].get("mcp_url") == bound.attachment.endpoint
                and (row.get("answer") or {}).get("mcp_incarnation") == bound.incarnation
                and (row.get("answer") or {}).get("mcp_connection") == bound.connection_id)


async def remote_work(home, owner, bound, work, *, check_execution=None):
    client = AsyncBrokerClient.for_owner(home.parent, principal=owner, command_center=home.name)

    def check(expected):
        if check_execution is not None:
            check_execution()
        current = binding(home, owner, expected.grant_id, expected.connection_id)
        if current != expected or current.attachment.state not in {"active", "connecting"}:
            raise McpError("MCP attachment authority changed")
        if current.attachment.state == "active" and not activation_complete(home, current):
            raise McpError("MCP activation has not completed")

    remote = RemoteMcp(client, bound, check_authority=check)
    try:
        return await work(remote)
    finally:
        await client.close()


def activate(home, owner, grant_id, connection_id, endpoint, display_name, request_id):
    """Called under the connect coordinator's owner lock after protected consent.

    A repeated answer reconciles the same incarnation and draft. Failure leaves
    the request pending; only successful discovery can complete it and wake it.
    """
    _, _, incarnation = authorized_connection(
        home.parent, principal=owner, command_center=home.name,
        grant_id=grant_id, connection_id=connection_id)
    scope = dict(principal=owner, command_center=home.name, grant_id=grant_id,
                 connection_id=connection_id, incarnation=incarnation)
    raw = metadata(home.parent, **scope)
    if raw is None:
        raw = metadata(home.parent, **scope, value=asdict(Attachment(
            endpoint, display_name, activation_request_id=request_id)))
    item = Attachment.parse(raw)
    if item.endpoint != endpoint or item.activation_request_id != request_id:
        raise McpError("MCP endpoint changed; reconnect with a new connection")
    if item.state == "active":
        return {"mcp": {"state": "active", "catalog_hash": item.catalog_hash,
                        "incarnation": incarnation}}
    if item.state == "draft":
        raw = metadata(home.parent, **scope, expected=raw,
                       value=asdict(replace(item, state="connecting", revision=item.revision + 1)))
        item = Attachment.parse(raw)
    if item.state != "connecting":
        raise McpError("MCP attachment cannot activate; reconnect")
    bound = Binding(grant_id, connection_id, incarnation, item)

    async def discover(remote):
        tools = await remote.discover()
        return replace(item, state="active", revision=item.revision + 1,
                       protocol_version=remote._version, catalog_hash=remote.catalog_hash,
                       tools=tools)

    active = asyncio.run(remote_work(home, owner, bound, discover))
    capability_operation(
        home.parent, principal=owner, command_center=home.name, grant_id=grant_id,
        connection_id=connection_id, capability_kind="mcp", action="activate", enabled=True,
        descriptor={"incarnation": incarnation, "expected": raw, "value": asdict(active)})
    return {"mcp": {"state": "active", "catalog_hash": active.catalog_hash,
                    "incarnation": incarnation}}


def catalog(home, owner):
    from tinyassets.broker.catalog import connections

    found = {}
    for grant, view, incarnation in connections(home.parent, principal=owner,
                                                command_center=home.name):
        raw = metadata(home.parent, principal=owner, command_center=home.name,
                       grant_id=grant.grant_id, connection_id=view.connection_id,
                       incarnation=incarnation)
        if raw is None:
            continue
        item = Attachment.parse(raw)
        bound = Binding(grant.grant_id, view.connection_id, incarnation, item)
        if item.state != "active" or not activation_complete(home, bound):
            continue
        for tool in item.tools:
            name = f"mcp:{view.connection_id}:{tool['name']}"
            found[name] = (bound, tool)
    return found


async def refresh_catalog(home, owner, *, errors=None):
    """Refresh discovery safely; calls prepared against old schemas stay stale."""
    from tinyassets.owner_control import control

    found = await asyncio.to_thread(catalog, home, owner)
    unique = {bound.connection_id: bound for bound, _ in found.values()}
    unavailable = set()
    for bound in unique.values():
        async def discover(remote):
            tools = await remote.discover()
            return tools, remote.catalog_hash, remote._version

        try:
            tools, checksum, version = await remote_work(home, owner, bound, discover)
        except Exception:
            unavailable.add(bound.connection_id)
            if errors is not None:
                errors.append({"connection_id": bound.connection_id,
                               "error": "MCP catalog unavailable; retry this connection"})
            continue
        if checksum == bound.attachment.catalog_hash:
            continue

        def commit():
            with control(home):
                updated = replace(bound.attachment, revision=bound.attachment.revision + 1,
                                  tools=tools, catalog_hash=checksum, protocol_version=version)
                capability_operation(home.parent, principal=owner, command_center=home.name,
                    grant_id=bound.grant_id, connection_id=bound.connection_id,
                    capability_kind="mcp", action="activate", enabled=True,
                    descriptor={"incarnation": bound.incarnation,
                                "expected": asdict(bound.attachment), "value": asdict(updated)})

        try:
            await asyncio.to_thread(commit)
        except Exception:
            unavailable.add(bound.connection_id)
            if errors is not None:
                errors.append({"connection_id": bound.connection_id,
                               "error": "MCP catalog changed; refresh again"})
    current = await asyncio.to_thread(catalog, home, owner)
    return {name: entry for name, entry in current.items()
            if entry[0].connection_id not in unavailable}


def packet(bound, tool, arguments):
    endpoint = urlsplit(bound.attachment.endpoint)
    return {"connection_id": bound.connection_id, "grant_id": bound.grant_id, "verb": "POST",
            "request": {"host": endpoint.netloc, "path": endpoint.path or "/",
                        "body": arguments},
            "mcp": {"tool": tool["name"], "incarnation": bound.incarnation,
                    "catalog_hash": bound.attachment.catalog_hash}}


def validate_packet(home, owner, args):
    bound = binding(home, owner, args["grant_id"], args["connection_id"])
    ref = args.get("mcp")
    if (not isinstance(ref, dict) or set(ref) != {"tool", "incarnation", "catalog_hash"}
            or ref["incarnation"] != bound.incarnation
            or ref["catalog_hash"] != bound.attachment.catalog_hash
            or bound.attachment.state != "active" or not activation_complete(home, bound)):
        raise McpError("stale MCP catalog; discover tools again")
    tool = next((t for t in bound.attachment.tools if t["name"] == ref["tool"]), None)
    if tool is None or packet(bound, tool, args["request"]["body"]) != args:
        raise McpError("MCP call does not match its attachment")
    return bound, tool


def policy(home, owner, agent, args):
    from tinyassets import agent_rules

    _, tool = validate_packet(home, owner, args)
    operation = ("MCP:" + tool["name"]).upper()
    cls = "app.write" if requires_approval(tool) else "app.read"
    # Explicit owner classifications take precedence over upstream hints.
    path = args["request"]["path"]
    declared = any(k.connection == args["connection_id"] and k.method in ("", "POST")
                   and (k.path_prefix == "/" or path == k.path_prefix
                        or path.startswith(k.path_prefix.rstrip("/") + "/"))
                   for k in agent_rules.list_kinds(home))
    if declared:
        cls, _ = agent_rules.classify(home, args["connection_id"], "POST", path)
    decision = agent_rules.decide(home, cls, connection=args["connection_id"],
                                  operation=operation, agent=agent)
    # Unknown/destructive tools start at ask-first, but explicit owner rules
    # for this connection or tool can change that default.
    explicit = declared or any(r.id == decision.rule_id and not r.seeded
                               for r in agent_rules.list_rules(home, agent))
    behaviour = decision.behaviour
    if requires_approval(tool) and not explicit and decision.proceeds:
        behaviour = agent_rules.ASK_FIRST
    return cls, operation, behaviour


async def call(home, owner, agent, args, *, approved=False, op_id=None, check_execution=None):
    from tinyassets import agent_rules, bound_requests
    from tinyassets.broker.ops import new_op_id
    from tinyassets.effectors.authenticated_external_call import _check_consent
    from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

    bound, tool = validate_packet(home, owner, args)
    _, view, _ = authorized_connection(home.parent, principal=owner, command_center=home.name,
                                       grant_id=bound.grant_id, connection_id=bound.connection_id)
    if not _check_consent(home, view.destination):
        return {"error_kind": "missing_consent", "dry_run": True}
    cls, operation, behaviour = policy(home, owner, agent, args)
    from tinyassets.effectors.authenticated_external_call import (
        SOUL_AUTHORITY_DENIED,
        resolve_soul_effect_authority,
    )

    if resolve_soul_effect_authority(home, "authenticated_external_call",
                                    view.destination) == SOUL_AUTHORITY_DENIED:
        return {"error_kind": "soul_authority_denied", "dry_run": True}
    if behaviour == agent_rules.HAND_OFF:
        return {"error_kind": "rule_hand_off", "dry_run": True}
    if behaviour in (agent_rules.ASK_FIRST, agent_rules.DO_IF_PREAPPROVED) and not approved:
        from tinyassets.effectors.authenticated_external_call import _initiating_agent

        if _initiating_agent(home) != agent:
            return {"error_kind": "execution_context_mismatch", "dry_run": True}
        pending = await asyncio.to_thread(bound_requests.capture, home,
                                           {"executor": "mcp_call", "arguments": args})
        return {"error_kind": "rule_ask_first", "dry_run": True,
                "request_id": pending["request_id"]}
    from tinyassets.agent_review import review_refusal

    refusal = await asyncio.to_thread(
        review_refusal, home, action={"action_class": cls, "connection": bound.connection_id,
                                    "operation": operation, "path": args["request"]["path"]},
        rule=behaviour, evidence=bound_requests.digest(args), agent=agent)
    if refusal is not None:
        return refusal
    operation_id = op_id or new_op_id()
    from tinyassets import turn_interrupt

    live = turn_interrupt.current()

    def guard():
        if live is not None:
            live.check()
        if check_execution is not None:
            check_execution()
        if (policy(home, owner, agent, args) != (cls, operation, behaviour)
                or not _check_consent(home, view.destination)):
            raise McpError("MCP execution authority changed")

    async def invoke(remote):
        await remote.discover()
        return await remote.call(tool["name"], args["request"]["body"],
                                 catalog_hash=args["mcp"]["catalog_hash"], op_id=operation_id)

    try:
        work = remote_work(home, owner, bound, invoke, check_execution=guard)
        result = await live.cancel_on_stop(work) if live is not None else await work
        return {"response": result, "op_id": operation_id}
    except AmbiguousProxyOutcome:
        return {"error_kind": "mcp_outcome_unknown", "op_id": operation_id,
                "hint": "Reconcile this operation; do not repeat the call."}


def approved_call(home, args, agent, request_id, op_id):
    from contextlib import closing

    from tinyassets import bound_requests
    from tinyassets.auth.middleware import current_identity
    from tinyassets.owner_control import control

    owner = current_identity().user_id

    def check():
        with control(home), closing(bound_requests.connect(home)) as conn:
            bound_requests._current(home, conn, request_id, owner)

    from tinyassets.owner_control import ControlUnavailable
    from tinyassets.storage.outbound_connections import GrantResolutionError

    try:
        return asyncio.run(call(home, owner, agent, args, approved=True,
                                check_execution=check, op_id=op_id))
    except (McpError, GrantResolutionError, bound_requests.RequestRefused, ControlUnavailable):
        # RemoteMcp converts any post-send call failure into AmbiguousProxyOutcome.
        return {"error_kind": "mcp_call_refused", "dry_run": True, "op_id": op_id}
    except Exception:
        # Fixed receipt even for a crashed transport; never persist exception text.
        return {"error_kind": "mcp_outcome_unknown", "op_id": op_id}


def reconcile(home, owner, op_id):
    async def query():
        client = AsyncBrokerClient.for_owner(home.parent, principal=owner, command_center=home.name)
        try:
            return await client.status(op_id)
        finally:
            await client.close()

    return asyncio.run(query())
