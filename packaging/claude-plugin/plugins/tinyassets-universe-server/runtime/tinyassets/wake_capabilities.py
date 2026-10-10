"""Provider-neutral ta capabilities, bound to the calling agent's authority."""
from __future__ import annotations

CATALOG = [
    {"name": "wake:register", "description": "Resume me automatically with a saved note when "
     "a time or condition is met. Bounded follow-up; uses my owner's normal budget.",
     "arguments": {"type": "object", "required": ["note"], "additionalProperties": False,
                   "properties": {
                       "note": {"type": "string"}, "at": {"type": "string"},
                       "after_seconds": {"type": "number", "minimum": 0},
                       "interval_seconds": {"type": "number", "minimum": 0},
                       "max_fires": {"type": "integer", "minimum": 1, "default": 1},
                       "expires_in_seconds": {"type": "number", "default": 604800},
                       "max_checks": {"type": "integer", "minimum": 1, "default": 100},
                       "backoff_seconds": {"type": "number", "default": 30},
                       "max_backoff_seconds": {"type": "number", "default": 3600},
                       "condition": {"type": "object", "description":
                           "kind: run_finished/run_failed + run_id (my owner's run); "
                           "request_answered + request_id (my request); connection + destination "
                           "(exact owner-granted destination); release + commit (full SHA) or pr "
                           "(integer); probe + command + optional timeout_seconds (default 60, "
                           "max 600). Probe is a repeat-safe shell check in my owner's box. "
                           "Time AND condition when both given."}}}},
    {"name": "wake:list", "description": "List my durable follow-ups and their status.",
     "arguments": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "wake:cancel", "description": "Cancel one of my saved follow-ups.",
     "arguments": {"type": "object", "required": ["wake_id"], "additionalProperties": False,
                   "properties": {"wake_id": {"type": "string"}}}},
]


def call(backend, name, arguments):
    from tinyassets import agent_wakes

    context = backend.context
    # An outside-client launch must not turn a one-call capability into durable
    # unattended authority. Serving-owner bash is the registration authority.
    identity = backend.outside_identity
    if identity is not None and identity.metadata.get("outside_origin") is not None:
        raise PermissionError("outside launches cannot register or control wakes")
    if name == "wake:list" and not arguments:
        return {"wakes": agent_wakes.listing(backend.root, context.owner,
                                             context.initiating_agent)}
    if name == "wake:cancel" and set(arguments) == {"wake_id"}:
        return agent_wakes.cancel(backend.root, context.owner, arguments["wake_id"],
                                  context.initiating_agent)
    if name == "wake:register":
        allowed = CATALOG[0]["arguments"]["properties"]
        if set(arguments) - set(allowed) or "note" not in arguments:
            raise ValueError("invalid wake registration arguments")
        condition = arguments.get("condition")
        if condition is not None and not isinstance(condition, dict):
            raise ValueError("condition must be an object")
        if (condition or {}).get("kind") == "probe" and not backend.shell_granted:
            raise PermissionError("probe requires bash authority")
        return agent_wakes.register(backend.root, context.owner, context.initiating_agent,
                                    **arguments)
    raise ValueError("invalid wake capability or arguments")
