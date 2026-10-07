"""Observational lifecycle events use the same pinned ta dispatch and current jail."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import shlex

from tinyassets.extension_state import ExtensionStore

_RUNNING = contextvars.ContextVar("extension_hook_running", default=False)


def has_hooks(root, owner, agent, event):
    from tinyassets.command_center_packages import database_path

    if not database_path(root.parent).is_file():
        return False
    store = ExtensionStore(root.parent, owner=owner, universe=root.name, agent=agent)
    for state in store.list():
        if state["state"] == "active":
            doc, _ = store.load(state["name"], state["revision"]).content()
            if any(row["event"] == event for row in doc.get("hooks", [])):
                return True
    return False


def command(event, payload):
    envelope = json.dumps({"version": 1, "event": event, "payload": payload})
    if len(envelope.encode()) > 65536:
        raise ValueError("extension event exceeds 64 KiB")
    return "ta extension:event --json " + shlex.quote(envelope)


async def _engine_event(server, event, payload):
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.ta_capabilities import engine_dispatch
    from tinyassets.universe_tools import bash

    if _RUNNING.get():
        return
    root = _universe_dir(server._GRAPH_ID)
    agent = server._acting_agent()
    if not has_hooks(root, server._ACTOR_ID, agent, event):
        return
    dispatch = await engine_dispatch(server)
    if dispatch is None:
        return "skipped: triggering turn has no bash grant"
    token = _RUNNING.set(True)
    try:
        result = await asyncio.to_thread(bash, root, command(event, payload),
                                         agent_id=agent, timeout=30, ta_dispatch=dispatch)
        if "[exit code 0]" not in result:
            raise RuntimeError("extension hook failed: " + result)
    finally:
        _RUNNING.reset(token)


async def _turn_event(coordinator, event, payload):
    """Turn events have no authority beyond the triggering signed tool session."""
    from tinyassets.engine_tool_client import open_engine_tools
    from tinyassets.served_tools import granted_tools

    config, ctx = coordinator.config, coordinator.context
    agent = getattr(getattr(ctx, "acting_agent", None), "agent_id", None) or "main"
    owner = coordinator.owner
    if not owner or not has_hooks(ctx.universe_dir, owner, agent, event):
        return
    if coordinator._interrupted():
        return  # Stop is authoritative; a hook never restarts a stopped turn.
    if "bash" not in granted_tools(config):
        return "skipped: triggering turn has no bash grant"
    actor, graph = coordinator.adapter.engine_identity(ctx, config)
    async with open_engine_tools(actor_id=actor, graph_id=graph,
                                 enabled_tools=granted_tools(config), timeout=30,
                                 **coordinator.steering()) as session:
        result = await session.call("bash", {"command": command(event, payload), "timeout": 30})
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        if result.isError or "[exit code 0]" not in text:
            raise RuntimeError("extension hook failed: " + text)


_SCHEMA = """CREATE TABLE IF NOT EXISTS extension_hook_events (
 id INTEGER PRIMARY KEY, owner_id TEXT, universe_id TEXT, agent_id TEXT,
 event TEXT, diagnostic TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)"""


def evidence(root, owner, agent, event, diagnostic):
    from tinyassets.command_center_packages import _db

    logging.getLogger(__name__).warning("extension hook %s: %s", event, diagnostic)
    try:
        with _db(root.parent) as conn:
            conn.execute(_SCHEMA)
            conn.execute("INSERT INTO extension_hook_events "
                         "(owner_id,universe_id,agent_id,event,diagnostic) VALUES (?,?,?,?,?)",
                         (owner, root.name, agent, event, diagnostic[:4000]))
    except Exception:
        logging.getLogger(__name__).exception("extension hook evidence unavailable")


def read_evidence(store):
    from tinyassets.command_center_packages import _db

    with _db(store.base) as conn:
        conn.execute(_SCHEMA)
        return [dict(row) for row in conn.execute(
            "SELECT event,diagnostic,at FROM extension_hook_events WHERE owner_id=? "
            "AND universe_id=? AND agent_id=? ORDER BY id DESC LIMIT 50", store.identity)]


async def engine_event(server, event, payload):
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.auth.middleware import _current_identity

    token = None
    try:
        token = server._bind_founder_identity(server._RUN_CAPABILITIES)
        diagnostic = await _engine_event(server, event, payload)
    except Exception as exc:
        diagnostic = f"failed: {type(exc).__name__}: {exc}"
    finally:
        if token is not None:
            _current_identity.reset(token)
    if diagnostic:
        evidence(_universe_dir(server._GRAPH_ID), server._ACTOR_ID,
                 server._acting_agent(), event, diagnostic)
    return diagnostic


async def turn_event(coordinator, event, payload):
    try:
        diagnostic = await _turn_event(coordinator, event, payload)
    except Exception as exc:
        diagnostic = f"failed: {type(exc).__name__}: {exc}"
    if diagnostic:
        agent = getattr(getattr(coordinator.context, "acting_agent", None), "agent_id", None)
        evidence(coordinator.context.universe_dir, coordinator.owner,
                 agent or "main", event, diagnostic)
    return diagnostic
