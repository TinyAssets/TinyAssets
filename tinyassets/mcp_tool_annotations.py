"""Titles and behavior hints for the universe server's MCP tools.

One table, keyed by wire name. ``_register_structured_tool`` reads it and
refuses a tool that has no row. The four hints are explicit booleans so a
directory client never has to guess an omitted one.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

#: Advertised hint names, in the order directory checklists name them.
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "read_graph": ToolAnnotations(
        title="Read Graph",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    "write_graph": ToolAnnotations(
        title="Write Graph",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    "run_graph": ToolAnnotations(
        title="Run Graph",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    # The wiki is shared with other users, so this read is open-world.
    "read_page": ToolAnnotations(
        title="Read Page",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    "write_page": ToolAnnotations(
        title="Write Page",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    "converse": ToolAnnotations(
        title="Talk With Your Command Center",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    "get_status": ToolAnnotations(
        title="Daemon Status + Routing Evidence",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
}


def annotations_for(wire_name: str) -> ToolAnnotations:
    """Return a fresh title-and-hints row for ``wire_name``.

    A missing row is a ``LookupError``. A row with no title, or with any hint
    left unset, is a ``ValueError``. The returned object is a copy, so a
    later registration cannot rewrite the table.
    """
    row = TOOL_ANNOTATIONS.get(wire_name)
    if row is None:
        raise LookupError(f"MCP tool {wire_name!r} has no entry in TOOL_ANNOTATIONS")
    title = row.title
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"MCP tool {wire_name!r} is missing a title")
    hints: dict[str, bool] = {}
    for hint in HINTS:
        value = getattr(row, hint)
        if not isinstance(value, bool):
            raise ValueError(
                f"MCP tool {wire_name!r} hint {hint} must be an explicit boolean"
            )
        hints[hint] = value
    return ToolAnnotations(title=title, **hints)
