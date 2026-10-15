"""Two command centers under one data root, admitted to the owner box double.

Shared by the engine-tool, remote ``ta`` and starter-task proofs: the shipping
engine handlers (``tinyassets.engine_mcp_server``) pinned to an owner and a
center, with that owner's session binding registered the way
``control_plane.session.start`` registers it, so ``read``/``write``/``edit``/
``bash`` run through the box tools. Every byte involved is synthetic.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.engine_authority_helpers import seed_engine_authority

OWN_MARKER = "POSITIVE-CONTROL-OWN-UNIVERSE"
FOREIGN_MARKER = "SYNTHETIC-UNIVERSE-B-CONTENT"
CRED_MARKER = "SYNTHETIC-OWN-LAUNCH-CREDENTIAL"
BEARER_MARKER = "SYNTHETIC-ENGINE-ROUTE-BEARER"


@dataclass
class World:
    data_root: Path
    universe_a: Path
    universe_b: Path
    _stack: ExitStack = field(default_factory=ExitStack, repr=False)
    _admitted: dict = field(default_factory=dict, repr=False)

    def admit(self, owner: str, center: str):
        """Register ``owner``'s session on ``center`` for the rest of the test."""
        from tinyassets.agent_loop.served_chat import configured_box_provider
        from tinyassets.control_plane.binding import bind_session, use_binding

        key = (owner, center)
        if key in self._admitted:
            return self._admitted[key]
        provider, limits = configured_box_provider()
        assert provider is not None, "the owner box double is installed on Linux only"
        binding = bind_session(provider=provider, limits=limits,
                               universe_dir=self.data_root / center, owner=owner,
                               execution_id=f"world:{owner}:{center}")
        self._stack.enter_context(use_binding(binding))
        self._admitted[key] = binding
        return binding


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, _owner_box_double):
    """Two centers under one data root; ``actor-a`` owns ``u-alpha``."""
    import tinyassets.api.helpers as helpers

    if _owner_box_double is None:
        pytest.skip("the owner box double needs Linux")
    root = Path(tempfile.mkdtemp(prefix="ta-box-world-", dir="/tmp"))
    try:
        data_root = root / "data"
        universe_a = data_root / "u-alpha"
        universe_b = data_root / "u-bravo"
        (universe_a / "notes").mkdir(parents=True)
        universe_b.mkdir(parents=True)
        (universe_a / "notes" / "own.txt").write_text(OWN_MARKER + "\n", encoding="utf-8")
        (universe_b / "founder.md").write_text(FOREIGN_MARKER + "\n", encoding="utf-8")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
        monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
        monkeypatch.setattr(helpers, "_base_path", lambda: data_root)
        current = World(data_root, universe_a, universe_b)
        with current._stack:
            yield current
    finally:
        shutil.rmtree(root, ignore_errors=True)


def bare_engine(monkeypatch, world: World, *, actor="actor-a", graph="u-alpha"):
    """The shipping engine handlers, pinned to (actor, graph) with REAL authority."""
    from tinyassets import engine_mcp_server as s

    seed_engine_authority(world.data_root, actor=actor, graph=graph)
    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    world.admit(actor, graph)
    return s


def engine(monkeypatch, world: World, *, tools=None, **pins):
    """The engine on the platform-signed route of one launch (default: every tool)."""
    from tests.test_ta_capabilities import signed_launch

    server = bare_engine(monkeypatch, world, **pins)
    signed_launch(monkeypatch, tools)
    return server


@pytest.fixture
def dynamic_data_root(world, monkeypatch):
    """Resolve the data root dynamically for tools that import helpers lazily."""
    from tinyassets.api import helpers
    from tinyassets.storage import data_dir

    monkeypatch.setattr(helpers, "_base_path", data_dir)


def bash(server, command, exit_code=0):
    result = asyncio.run(server.run_bash(command=command))
    trailer = f"[exit code {exit_code}]"
    assert result.endswith(trailer), result
    return result.removesuffix(trailer).strip()


def run(coro):
    return asyncio.run(coro)
