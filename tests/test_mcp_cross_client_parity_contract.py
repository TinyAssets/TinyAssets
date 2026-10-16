"""Guards for MCP response-shape parity and live cross-client proof.

These are static contract tests for the community patch request that absorbed
the BUG-069 class: every public MCP server surface should use the structured
tool adapter, and ui-test should require rendered proof on both ChatGPT and
Claude before shape-changing tool work ships.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MCP_SERVER_SURFACES = (
    "tinyassets/universe_server.py",
)


def _read(rel_path: str) -> str:
    return (REPO / rel_path).read_text(encoding="utf-8")


def test_all_mcp_server_surfaces_register_tools_through_structured_adapter() -> None:
    """ChatGPT and Claude must see the same text + structuredContent shape."""
    failures: list[str] = []
    registration = _read("tinyassets/control_plane/registration.py")
    if registration.count("server.add_tool(tool)") != 1:
        failures.append("control_plane/registration.py: expected one tool installation boundary")
    for rel_path in MCP_SERVER_SURFACES:
        text = _read(rel_path)

        if "def _register_structured_tool" not in text:
            failures.append(f"{rel_path}: missing _register_structured_tool")
        # Both facts, matched independently of how the call is laid out: the
        # adapter returns through `_structured_return`, and what it wraps is the
        # handler's OWN result. Pinning the single-line spelling
        # `_structured_return(fn(*args, **kwargs))` made this fail the moment the
        # call grew a keyword argument, which is formatting, not a parity change.
        if "return _structured_return(" not in text:
            failures.append(f"{rel_path}: adapter does not wrap with _structured_return")
        if "fn(*args, **kwargs)" not in text:
            failures.append(f"{rel_path}: adapter does not wrap the handler's own result")
        if "install_profile_tool(mcp, tool)" not in text:
            failures.append(f"{rel_path}: adapter does not use the shared registration boundary")

    assert failures == []
