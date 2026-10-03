"""The universe -> command center rename at the public edge: one authority.

Founder decision 2026-10-01: the product word "universe" is now "command
center" (`openspec/changes/rename-universe-to-command-center`, C1), as a CLEAN
CUTOVER: "no old ids do not keep working ... current testers ... need to
cleanly move to the new system". So:

* **Inputs** (`refuse_retired_arguments`): a retired argument name or enum value
  is refused loudly, naming its replacement. It is never silently accepted.
* **Values** (`internal_value`): a current public value (``target="command_center"``)
  maps to the value handlers still dispatch on (``"universe"``) until the code
  rename (C3) changes the handlers themselves.
* **Responses** (`public_response`): keys and error codes that spell the old
  word are emitted in their current spelling only, and a stored actor id
  ``universe:<id>`` is presented as ``command_center:<id>`` until the storage
  migration (C4) rewrites the stored value.

Every name is derived by one rule (``universe`` -> ``command_center``), not a
hand list, so a field added tomorrow is covered the day it lands. The internal
translation disappears with C3/C4; nothing here is a compatibility window.
"""

from __future__ import annotations

import json
import re
from typing import Any

RETIRED = "universe"
CURRENT = "command_center"

#: Argument names the public handles took before the rename.
RETIRED_ARGUMENTS = ("universe_id",)

#: Enum-valued arguments a client sends.
#: (A workspace packet's ``storage: "universe"`` is NOT here: it lives inside
#: stored branch node configs, so it moves with the storage migration, C4.)
VALUE_FIELDS = ("target", "scope")

#: Current public value -> the value handlers dispatch on until C3.
_INTERNAL_VALUES = {
    "command_center": "universe",
    "command_center_files": "universe_files",
    "command_center_file": "universe_file",
}

_ACTOR = re.compile(r"^universe:(?=\S)")
_CODE = re.compile(r"^[a-z_]*universe[a-z_]*$")
_ERROR_KEYS = ("error", "code", "error_code")
#: Only identity fields carry a stored actor id; any other string (a person's
#: own text, a file's bytes) is never rewritten (Hard Rule 9).
_ACTOR_KEY = re.compile(
    r"(?:^|_)(?:actor|actor_id|author|author_id|owner_actor|created_by|run_actor)$")

#: Keys whose VALUE is something a person (or their agent) authored -- a branch's
#: graph, mappings and state schema, a payload, UI bundles, page content. Their
#: subtree is passed through untouched: a user's state field named ``universe``
#: is data, and respelling it would break the definition on its round trip
#: (gpt-6-astra repro: ``output_mapping={"universe": ...}`` stopped validating).
USER_CONTENT_KEYS = frozenset({
    "graph_nodes", "node_defs", "edges", "conditional_edges", "conditions",
    "state_schema", "io_manifest", "input_keys", "output_keys", "output_mapping",
    "input_mapping", "config", "configuration", "payload", "inputs", "outputs",
    "inputs_json", "content", "components", "ui_library", "ui_selection", "library",
    "source_code", "skills", "metadata", "shape", "definition", "state", "values",
    "fields", "output", "result_value", "text", "body", "message", "reply",
})

#: Reads whose payload is the person's own content, returned verbatim.
VERBATIM_TARGETS = frozenset({
    "run_output", "run_file", "command_center_file", "conversation", "conversation_turn",
})
#: Handles whose result is arbitrary content (a file's bytes, a command's output).
_RAW_CONTENT_TOOLS = frozenset({"read", "write", "edit", "bash", "read_page"})


def current_name(name: str) -> str:
    """The current spelling of a key, code or argument name."""
    return name.replace(RETIRED, CURRENT) if RETIRED in name else name


class RetiredName(ValueError):
    """A client used a name the rename retired."""

    def __init__(self, retired: str, current: str):
        super().__init__(f"renamed: {retired} is now {current}")
        self.retired = retired
        self.current = current

    def document(self) -> dict[str, Any]:
        return {"error": "renamed", "retired": self.retired, "current": self.current,
                "detail": str(self)}


def refuse_retired_arguments(arguments: dict[str, Any] | None) -> None:
    """Raise RetiredName for a retired argument name or enum value."""
    for name in arguments or {}:
        if name in RETIRED_ARGUMENTS:
            raise RetiredName(name, current_name(name))
    for field in VALUE_FIELDS:
        refuse_retired_value(field, (arguments or {}).get(field))


def refuse_retired_value(field: str, value: Any) -> None:
    if isinstance(value, str) and RETIRED in value.strip().lower():
        retired = value.strip().lower()
        raise RetiredName(f"{field}={retired}", f"{field}={current_name(retired)}")


def internal_value(value: Any) -> Any:
    """A current public enum value -> the value handlers dispatch on (until C3).

    A retired value is NOT translated: it reaches the router as an unknown value,
    so a caller that skipped the boundary still fails rather than half-working.
    """
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    if RETIRED in key:
        return "retired:" + key
    return _INTERNAL_VALUES.get(key, value)


def public_value(value: str) -> str:
    """The advertised spelling of a handler's internal enum value."""
    for current, internal in _INTERNAL_VALUES.items():
        if value == internal:
            return current
    return value


def present_actor(actor: Any) -> Any:
    """``universe:<id>`` (stored until C4) -> ``command_center:<id>`` (presented)."""
    if isinstance(actor, str) and _ACTOR.match(actor):
        return CURRENT + ":" + actor[len(RETIRED) + 1:]
    return actor


def public_response(node: Any) -> Any:
    """Current spellings only: keys, error codes, presented actor ids."""
    if isinstance(node, list):
        return [public_response(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if isinstance(key, str) and key in USER_CONTENT_KEYS:
            out[key] = value
            continue
        value = public_response(value)
        if isinstance(key, str):
            if _ACTOR_KEY.search(key):
                value = present_actor(value)
            if key in _ERROR_KEYS and isinstance(value, str) and _CODE.match(value):
                value = current_name(value)
            key = current_name(key)
        out[key] = value
    return out


def public_response_text(text: str) -> str:
    """The same, for a tool result that is a JSON document in text form."""
    if not text.lstrip().startswith(("{", "[")):
        return text
    try:
        document = json.loads(text)
    except (TypeError, ValueError):
        return text
    public = public_response(document)
    if public == document:
        return text
    return json.dumps(public, ensure_ascii=False)


def verbatim(tool: str, arguments: dict[str, Any]) -> bool:
    """Is this call's result the person's own content, never rewritten?"""
    if tool in _RAW_CONTENT_TOOLS:
        return True
    target = arguments.get("target")
    return isinstance(target, str) and target.strip().lower() in VERBATIM_TARGETS


def _middleware_base():
    from fastmcp.server.middleware import Middleware

    return Middleware


class CommandCenterNames(_middleware_base()):
    """The public edge of the rename, on both MCP servers.

    Registered INNERMOST: the retired-name refusal runs before the tool's own
    validation (so the refusal names the replacement instead of FastMCP's
    generic "unexpected keyword"), and the response is respelled before the
    result ceiling measures it.
    """

    def __init__(self, handles: frozenset[str] | None = None):
        #: The handles this edge governs; ``None`` means every tool. The connector
        #: passes its advertised set: a hidden legacy tool keeps its own schema.
        self.handles = None if handles is None else frozenset(handles)

    async def on_call_tool(self, context, call_next):
        from fastmcp.exceptions import ToolError

        message = getattr(context, "message", None)
        tool = getattr(message, "name", "") or ""
        if self.handles is not None and tool not in self.handles:
            return await call_next(context)
        arguments = dict(getattr(message, "arguments", None) or {})
        try:
            refuse_retired_arguments(arguments)
        except RetiredName as exc:
            raise ToolError(json.dumps(exc.document())) from None
        result = await call_next(context)
        if verbatim(tool, arguments):
            return result
        blocks, changed = [], False
        for block in result.content or ():
            text = getattr(block, "text", None)
            public = public_response_text(text) if isinstance(text, str) else text
            if public is not text and public != text:
                changed = True
                blocks.append(block.model_copy(update={"text": public}))
            else:
                blocks.append(block)
        if changed:
            result.content = blocks
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            result.structured_content = {
                key: public_response_text(value) if isinstance(value, str) else value
                for key, value in public_response(structured).items()
            }
        return result
