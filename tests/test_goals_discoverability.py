"""Goals discoverability and canonical control-station routing invariants."""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def us_env(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us
    importlib.reload(us)


# ─── control_station prompt invariants ──────────────────────────────────


def test_control_station_mentions_every_advertised_handle_by_name(us_env):
    us = us_env
    prompt = us.control_station()
    advertised = {
        tool.name
        for tool in asyncio.run(us.mcp.list_tools(run_middleware=True))
    }
    for name in advertised:
        assert f"`{name}`" in prompt, f"control_station omits {name}"


def test_control_station_has_tool_catalog_section(us_env):
    """Prompt should have an explicit full-tool framing so
    the bot enumerates the full surface, not action-by-action."""
    us = us_env
    prompt = us.control_station()
    assert "describe every advertised handle" in prompt
    assert "FIVE tools" not in prompt
    # The catalog should describe goals' purpose, not just name it.
    assert "Goal" in prompt
    assert "discover" in prompt.lower() or "discovery" in prompt.lower()


def test_control_station_routes_intent_to_goals(us_env):
    """Routing rules section should tell the bot when to use goals."""
    us = us_env
    prompt = us.control_station()
    assert 'write_graph target="goal"' in prompt
    assert 'read_graph target="goals"' in prompt
    assert 'read_graph target="goal"' in prompt
    assert "Binding a workflow to a Goal is not exposed" in prompt
    assert "Goal leaderboards are not exposed" in prompt


def test_control_station_enumerate_directive_is_explicit(us_env):
    """Bot should enumerate the whole dynamic advertised catalog."""
    us = us_env
    prompt = us.control_station()
    # The directive language should appear near the catalog.
    catalog_pos = prompt.find("Tool Catalog")
    assert catalog_pos >= 0, "Tool Catalog section header missing"
    catalog_section = prompt[catalog_pos:catalog_pos + 1500]
    assert "enumerate every handle" in catalog_section


# ─── goals docstring still leads with intent ────────────────────────────
