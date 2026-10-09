"""Opt-in first-person Branch turns using the conversation harness.

The capability is declared in the immutable, owner-authored Branch, never in
run inputs. Provider admission and cancellation remain owned by the run session.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

SHARED_SELF_TOOL = "universe_self"


def _node_list(snapshot: dict | None) -> list:
    nodes = (snapshot or {}).get("node_defs", [])
    if isinstance(nodes, dict):
        nodes = list(nodes.values())
    return [n for n in nodes if isinstance(n, dict)]


def _agent_nodes(snapshot: dict | None) -> dict[str, dict]:
    """Every agent node in the snapshot, each checked for a runnable shape."""
    from tinyassets.served_tools import AGENT_NODE_MARKERS, node_tool_grant

    agents = {}
    for node in _node_list(snapshot):
        if not AGENT_NODE_MARKERS.intersection(node.get("tools_allowed") or []):
            continue
        if node.get("source_code") or not str(node.get("prompt_template") or "").strip():
            raise ValueError("agent_node_requires_prompt_node")
        if node.get("model_hint") not in (None, "", "writer"):
            raise ValueError("agent_node_requires_writer")
        node_tool_grant(node.get("tools_allowed"))
        agents[str(node.get("node_id") or "")] = node
    return agents


def shared_self_requested(snapshot: dict | None) -> bool:
    """Whether the branch holds any agent node (tool readiness, run allowance)."""
    return bool(_agent_nodes(snapshot))


def agent_node_key(branch_def_id: str, node) -> str:
    """Which branch's node, with which instructions and grant, an agent call is.

    The compiler stamps it on the call; the run session recomputes it from the
    node it resolved in its admitted snapshot. A blocking ``invoke_branch`` child
    shares its parent's session, so an id alone would hand a child's node (maybe
    another user's) the parent's same-named node's grant.
    """
    def field(name):
        return node.get(name) if isinstance(node, dict) else getattr(node, name, None)

    return hashlib.sha256(json.dumps([
        str(branch_def_id or ""), str(field("node_id") or ""),
        str(field("prompt_template") or ""), list(field("tools_allowed") or []),
    ]).encode("utf-8")).hexdigest()


def agent_node(snapshot: dict | None, node_id: str, principal_id: str, *,
               node_key: str) -> dict | None:
    """The agent node making this call, resolved from the ADMITTED snapshot.

    ``node_id`` comes from the compiler and only selects; an id that is not an
    agent node here refuses, and so does a node that is not this snapshot's own
    (``node_key``). A branch another user authored never drives the owner's
    tools, whichever run admitted it.
    """
    if not node_id:
        return None
    node = _agent_nodes(snapshot).get(node_id)
    if node is None:
        raise PermissionError("agent_node_not_declared")
    if node_key != agent_node_key((snapshot or {}).get("branch_def_id"), node):
        raise PermissionError("agent_node_not_in_admitted_branch")
    if str((snapshot or {}).get("author") or "").strip() != principal_id:
        raise PermissionError("agent_node_requires_owner_authored_branch")
    return node


def require_founder_home(base_path: Path, universe_id: str, principal_id: str) -> Path:
    """Recheck the stored owner against current home and admin before disclosure."""
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.principals import has_named_principal

    base = Path(base_path).resolve()
    if not universe_id or Path(universe_id).name != universe_id or universe_id in {".", ".."}:
        raise PermissionError("shared_self_invalid_universe")
    root = base / universe_id
    if root.resolve() != root or not root.is_dir():
        raise PermissionError("shared_self_invalid_universe")
    if (
        not has_named_principal(principal_id)
        or get_founder_home(base, principal_id) != universe_id
        or universe_access_permission(
            base, universe_id=universe_id, actor_id=principal_id,
        ) != "admin"
    ):
        raise PermissionError("shared_self_requires_current_founder")
    return root


def prepare_shared_self_turn(base_path, universe_id, principal_id, prompt, config=None,
                             node=None, activity=None):
    """Use the SAME persona, memory formatter and tool config as converse.

    No learning extractor runs: a scheduled direction is not a new founder fact.
    History and run outputs remain evidence; this function does not record a
    synthetic founder message in the conversation. ``node`` is the agent node
    resolved from the admitted snapshot; its grant narrows the served tools.
    ``activity`` is the record the run session found naming this run (harness
    D2): the turn continues that activity's own session instead of the node's,
    without the owner's conversation, which is not part of the activity.
    """
    from tinyassets import universe_intelligence as intelligence
    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.config import load_universe_config
    from tinyassets.conversation_store import load_recent_readonly
    from tinyassets.providers.base import UniverseContext

    root = require_founder_home(Path(base_path), universe_id, principal_id)
    ctx = UniverseContext(universe_dir=root, config=load_universe_config(root))
    # The disclosure ceiling reads the REQUEST actor, and a background wake has
    # none bound. Read as the principal `require_founder_home` just proved owns
    # this universe -- never as whoever happens to be bound.
    with owner_run_identity(Path(base_path), universe_id, principal_id):
        system = intelligence._build_persona_system_prompt(
            root, universe_id=universe_id, tier=intelligence.interlocutor.FOUNDER,
        )
    history = [] if activity else load_recent_readonly(root, f"principal:{principal_id}")
    history_block = intelligence._conversation_history_block(history) if history else ""
    system += "\n\n" + intelligence._turn_input_method_context("unknown")
    shared_config = intelligence._sandboxed_config(
        ctx, founder_principal=principal_id, universe_id=universe_id, granted=True,
    )
    if not shared_config.engine_mcp_enabled:
        raise PermissionError("shared_self_engine_tools_unavailable")
    if config is not None:
        shared_config = replace(
            shared_config, timeout=config.timeout,
            reasoning_effort=config.reasoning_effort, temperature=config.temperature,
        )
        caps = [c for c in (shared_config.absolute_cap_s, config.absolute_cap_s) if c]
        if caps:
            # The compiler's slot for an agent node is this same served cap, so
            # the tighter of the two is the node's remaining share of it.
            shared_config = replace(shared_config, absolute_cap_s=min(caps))
    if node is not None:
        shared_config = _granted_config(shared_config, node)
    # An agent node continues its own session from wake to wake (change
    # `universe-agent-harness`): a resumable adapter resumes it and is sent only
    # the conversation that arrived since its last wake, then this wake's prompt.
    node_key = getattr(config, "agent_node_key", "") if config is not None else ""
    if activity:
        message = prompt
        if int(activity.get("runner_generation") or 0) > 1:
            message = ("[Platform] This activity was paused, waited on your owner, or was "
                       "interrupted; continue it where you left off.\n\n" + prompt)
        shared_config = replace(shared_config, agent_session=intelligence.session_ref(
            root, activity["session_key"], prompt, message, [],
        ))
    elif node_key:
        shared_config = replace(shared_config, agent_session=intelligence.session_ref(
            root, f"node:{node_key}", history_block + prompt, prompt, history,
        ))
    return history_block + prompt, system, shared_config


def _granted_config(config, node):
    """Narrow every tool surface to the node's grant (``None`` keeps them all)."""
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES, model_tools, node_tool_grant

    grant = node_tool_grant(node.get("tools_allowed"))
    if grant is None:
        return config
    visible = model_tools(replace(config, engine_tool_grant=grant))
    withheld = {f"mcp__tinyassets__{t}" for t in BACKEND_ENGINE_CAPABILITIES
                if t not in grant and t not in visible}
    return replace(
        config,
        engine_tool_grant=grant,
        allowed_tools=tuple(t for t in (config.allowed_tools or ()) if t not in withheld),
        disallowed_tools=tuple(config.disallowed_tools or ()) + tuple(sorted(withheld)),
    )
