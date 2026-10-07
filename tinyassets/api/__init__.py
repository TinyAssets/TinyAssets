"""Engine MCP API namespace.

This package is the home for FastMCP sub-apps per `docs/architecture.md`:
  api/runs.py, api/branches.py, api/judgments.py, api/goals.py, api/wiki.py

create_app() is a stub — the FastMCP submodule extraction is in-flight.
Tests for the fantasy_daemon REST app belong in fantasy_daemon/api.py scope;
import from fantasy_daemon.api directly, not from this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tinyassets.api.helpers import (
    _base_path,
    _default_universe,
    _read_json,
    _read_text,
    _universe_dir,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(registry: Any | None = None) -> FastAPI:
    """Return a bare FastAPI app shell.

    Once the FastMCP api/* submodules are built (the target layout), this will
    mount them. Until then it returns an empty app so callers have a stable
    import surface. fastapi is imported lazily so the MCPB stdio bundle —
    which doesn't ship fastapi — can import this package.
    """
    from fastapi import FastAPI

    return FastAPI(title="TinyAssets Engine API")


__all__ = [
    "create_app",
    # helpers (re-exported for submodule consumers)
    "_base_path",
    "_default_universe",
    "_read_json",
    "_read_text",
    "_universe_dir",
]
