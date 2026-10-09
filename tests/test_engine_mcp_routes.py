"""Shared private tool routes: actual publisher/CLI consumers, no live secrets."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from tests.engine_authority_helpers import seed_engine_authority
from tinyassets.providers.base import ModelConfig
from tinyassets.storage import DB_FILENAME


@pytest.fixture(autouse=True)
def _enable_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_engine_authority(tmp_path)


def _config():
    return ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id="actor-a", engine_mcp_graph_id="u-a",
    )


def _entry(**changes):
    return {
        "version": 1, "actor_id": "actor-a", "url": "http://127.0.0.1:8790/mcp",
        "port": 8790, "secret": "s" * 43, **changes,
    }


def _write(root, entry):
    (root / ".engine_mcp_http_routes.json").write_text(
        json.dumps({"u-a": entry}), encoding="utf-8",
    )


def _codex_dials(config=None, *, child_env=None):
    """The routes the codex adapter dials for a served turn.

    The real ``_served_engine_tools`` and ``open_engine_tools`` (route read,
    owner check, signed dial); only the MCP client is a stand-in. The child
    process environment plays no part: the platform process dials.
    """
    import asyncio
    import contextlib

    from mcp.types import ListToolsResult, Tool

    from tinyassets import engine_tool_client
    from tinyassets.exceptions import ProviderUnavailableError
    from tinyassets.providers import codex_provider
    from tinyassets.served_tools import FOUR_MODEL_TOOLS

    dialled = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def is_connected(self):
            return False

        async def list_tools_mcp(self, *, cursor=None):
            return ListToolsResult(tools=[Tool(name=name, inputSchema={"type": "object"})
                                          for name in FOUR_MODEL_TOOLS])

    def make(route, timeout):
        dialled.append(route)
        return Client()

    async def go():
        async with contextlib.AsyncExitStack() as stack:
            await codex_provider._served_engine_tools(stack, config or _config(), timeout=0.2)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine_tool_client, "_make_client", make)
        for name, value in (child_env or {}).items():
            patch.setenv(name, value)
        with contextlib.suppress(ProviderUnavailableError):
            asyncio.run(go())
    return dialled


def _cli_uses_http(kind, tmp_path, *, root=None):
    if kind == "codex":
        return bool(_codex_dials())
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    _engine_mcp_flags(_config(), tmp_path)
    config_path = tmp_path / ".runtime" / "engine-mcp-config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    server = data["mcpServers"]["tinyassets"]
    if "url" not in server:
        assert server["env"]["TINYASSETS_ENGINE_ACTOR_ID"] == "actor-a"
        assert server["env"]["TINYASSETS_ENGINE_GRAPH_ID"] == "u-a"
    return "url" in server


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_cli_refuses_route_for_different_owner(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _write(tmp_path, _entry(actor_id="actor-b"))
    assert not _cli_uses_http(kind, tmp_path, root=tmp_path)


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_cli_never_uses_route_from_current_working_directory(kind, tmp_path, monkeypatch):
    from tinyassets import storage

    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.delenv("TINYASSETS_DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "data_dir", lambda: root)
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, _entry())
    assert not _cli_uses_http(kind, tmp_path)


def _read(**changes):
    from tinyassets.engine_mcp_http import read_engine_mcp_route

    return read_engine_mcp_route(**{"actor_id": "actor-a", "graph_id": "u-a", **changes})


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_cli_uses_canonical_root_from_another_cwd(kind, tmp_path, monkeypatch):
    other = tmp_path / "unrelated"
    other.mkdir()
    monkeypatch.chdir(other)
    _write(tmp_path, _entry())
    assert _cli_uses_http(kind, tmp_path)


def test_publish_readback_binds_owner_and_hides_secret_in_repr(tmp_path):
    from tinyassets.engine_mcp_http import _EngineServer, _write_routes

    server = _EngineServer("u-a", "actor-a", 8790, str(tmp_path))
    _write_routes(tmp_path, [server])
    route = _read()
    assert (route.actor_id, route.graph_id, route.url) == (
        "actor-a", "u-a", "http://127.0.0.1:8790/mcp",
    )
    assert route.secret == server.secret and route.secret not in repr(route)
    with pytest.raises(FrozenInstanceError):
        route.secret = "changed"
    raw = json.loads((tmp_path / ".engine_mcp_http_routes.json").read_text(encoding="utf-8"))
    assert raw["u-a"] == _entry(secret=server.secret, grant_key=server.grant_key)
    assert route.grant_key == server.grant_key and route.grant_key not in repr(route)
    if os.name != "nt":
        assert (tmp_path / ".engine_mcp_http_routes.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("changes", [
    {"version": None}, {"version": True}, {"version": "1"}, {"version": 2},
    {"actor_id": None}, {"actor_id": "actor-b"}, {"actor_id": " actor-a"},
    {"port": None}, {"port": True}, {"port": "8790"}, {"port": 0}, {"port": -1},
    {"port": 65536}, {"port": 8790.0}, {"secret": ""}, {"secret": "short"},
    {"secret": "s" * 43 + "\r\nX: bad"}, {"secret": "s" * 43 + '"'},
    {"secret": ["s" * 43]}, {"secret": "s" * 43 + "ü"},
])
def test_reader_rejects_invalid_owner_port_version_or_secret(tmp_path, changes):
    _write(tmp_path, _entry(**changes))
    assert _read() is None


@pytest.mark.parametrize("raw", [
    "not json", "null", "[]", '{"u-a":null}', '{"u-a":[]}',
    json.dumps({"u-a": _entry()}).replace(
        '"actor_id": "actor-a"', '"actor_id": "actor-b", "actor_id": "actor-a"',
    ),
    '{"u-a":null,"u-a":' + json.dumps(_entry()) + '}',
])
def test_reader_rejects_invalid_or_ambiguous_document(tmp_path, raw):
    (tmp_path / ".engine_mcp_http_routes.json").write_text(raw, encoding="utf-8")
    assert _read() is None


def test_legacy_record_is_not_owner_proof(tmp_path):
    _write(tmp_path, {"url": "http://127.0.0.1:8790/mcp", "secret": "s" * 43})
    assert _read() is None


@pytest.mark.parametrize("url", [
    "https://foreign.example/mcp", "http://user:pass@127.0.0.1:8790/mcp",
    'http://127.0.0.1:8790/mcp?x="', "http://127.0.0.1:8790/mcp#other", None,
])
def test_reader_constructs_loopback_url_and_ignores_legacy_url(tmp_path, url):
    _write(tmp_path, _entry(url=url))
    assert _read().url == "http://127.0.0.1:8790/mcp"


@pytest.mark.parametrize("port", [1, 65535])
def test_valid_port_boundaries(tmp_path, port):
    _write(tmp_path, _entry(port=port))
    assert _read().url == f"http://127.0.0.1:{port}/mcp"


def test_reader_ignores_stale_routes_when_disabled_or_revoked(tmp_path, monkeypatch):
    _write(tmp_path, _entry())
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    assert _read() is None
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        conn.execute("DELETE FROM universe_acl")
    assert _read() is None


@pytest.mark.parametrize("changes", [
    {"actor_id": ""}, {"actor_id": None}, {"actor_id": "actor-a\n"},
    {"graph_id": ""}, {"graph_id": " u-a"}, {"graph_id": "u-b"},
    {"root": "."},
])
def test_reader_does_not_infer_or_normalize_missing_scope(tmp_path, changes):
    _write(tmp_path, _entry())
    assert _read(**changes) is None


def test_opaque_unicode_owner_and_graph(tmp_path, monkeypatch):
    from tinyassets.engine_mcp_http import _EngineServer, _write_routes

    seed_engine_authority(tmp_path, actor="人/🪐", graph="宇宙")
    _write_routes(tmp_path, [_EngineServer("宇宙", "人/🪐", 8790, str(tmp_path))])
    assert _read(actor_id="人/🪐", graph_id="宇宙").actor_id == "人/🪐"


def test_replacement_record_cannot_be_used_by_previous_owner(tmp_path):
    from tinyassets.engine_mcp_http import _EngineServer, _write_routes

    first = _EngineServer("u-a", "actor-a", 8790, str(tmp_path))
    second = _EngineServer("u-a", "actor-b", 8790, str(tmp_path))
    _write_routes(tmp_path, [first])
    assert _read().secret == first.secret
    _write_routes(tmp_path, [second])
    assert _read() is None
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        conn.execute("UPDATE agent_bindings SET created_by = 'actor-b'")
        conn.execute("UPDATE universe_acl SET actor_id = 'actor-b'")
    assert _read(actor_id="actor-b").secret == second.secret != first.secret


def test_codex_drops_stale_bearer_and_ignores_child_env_root(tmp_path):
    """A stale bearer in the environment never picks the route: the adapter
    dials the canonical owner-checked route from the platform process, and
    nothing once that route belongs to someone else. (The child has no route,
    bearer or data root to give: test_codex_app_server's launch tests.)"""
    _write(tmp_path, _entry())
    stale = {"TINYASSETS_ENGINE_MCP_BEARER": "stale-" + "b" * 40}
    (route,) = _codex_dials(child_env=stale)
    assert route.secret == "s" * 43 and route.actor_id == "actor-a"
    assert route.url.startswith("http://127.0.0.1:8790/mcp") and route.graph_id == "u-a"
    assert "stale-" not in repr(route) + route.url
    _write(tmp_path, _entry(actor_id="actor-b"))
    assert _codex_dials(child_env=stale) == []


def test_conflicting_serving_owners_are_not_picked_by_order(tmp_path, monkeypatch):
    from tinyassets import engine_mcp_http as http

    seed_engine_authority(tmp_path, graph="u-b", actor="actor-c")
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        # A second current creator must make u-a unavailable, regardless of row order.
        conn.execute("UPDATE agent_bindings SET status = 'configured' WHERE universe_id = 'u-a'")
    seed_engine_authority(tmp_path, graph="u-a", actor="actor-b")
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        conn.execute("UPDATE agent_bindings SET status = 'serving' WHERE universe_id = 'u-a'")
    assert http._desired_owners(tmp_path) == {"u-b": "actor-c"}


def test_supervisor_uses_one_root_for_database_routes_and_child(tmp_path, monkeypatch):
    from tinyassets import engine_mcp_http as http

    chosen = tmp_path / "chosen"
    chosen.mkdir()
    seed_engine_authority(chosen)
    observed = []
    original = http._serving_universe_owners
    def observed_owners(root, **kwargs):
        observed.append(root)
        return original(root, **kwargs)
    monkeypatch.setattr(http, "_serving_universe_owners", observed_owners)
    child_envs = []

    def spawn_without_process(*args, **kwargs):
        # Record only fixture identity/root values, not the inherited environment
        # or generated bearer. No real subprocess is launched.
        child_envs.append({key: kwargs["env"][key] for key in (
            "TINYASSETS_DATA_DIR", "TINYASSETS_ENGINE_ACTOR_ID", "TINYASSETS_ENGINE_GRAPH_ID",
        )})
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(http.subprocess, "Popen", spawn_without_process)
    # Capture the supervisor without starting a background thread or a real CLI.
    monkeypatch.setattr(http.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    [server] = http.start_engine_mcp_http_servers(chosen)
    assert observed == [chosen]
    assert server._data_dir == str(chosen)
    assert child_envs == [{
        "TINYASSETS_DATA_DIR": str(chosen), "TINYASSETS_ENGINE_ACTOR_ID": "actor-a",
        "TINYASSETS_ENGINE_GRAPH_ID": "u-a",
    }]
    assert _read(root=chosen).actor_id == "actor-a"


def test_supervisor_rejects_relative_root():
    from tinyassets.engine_mcp_http import start_engine_mcp_http_servers

    with pytest.raises(ValueError, match="must be absolute"):
        start_engine_mcp_http_servers("relative")
