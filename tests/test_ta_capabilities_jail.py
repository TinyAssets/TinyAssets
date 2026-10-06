"""The shipping bash handler and real Linux jail, including credential custody."""
from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import sys

import pytest

from tests.test_authenticated_external_call_effector import (
    _install_inprocess_proxy,
    _install_loopback_driver,
    _Loopback,
    _setup,
)
from tests.test_ta_capabilities import signed_launch
from tests.test_universe_tools_jail import _engine as _bare_engine
from tests.test_universe_tools_jail import world as world
from tinyassets import agent_review, agent_rules, universe_tools

pytestmark = [pytest.mark.real_jail, pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"), reason="requires Linux + bubblewrap")]


def _engine(monkeypatch, world, *, tools=None, **pins):
    """The engine on the platform-signed route of one launch (default: every tool)."""
    server = _bare_engine(monkeypatch, world, **pins)
    signed_launch(monkeypatch, tools)
    return server


@pytest.fixture(autouse=True)
def dynamic_data_root(world, monkeypatch):
    # The shared world fixture also sets TINYASSETS_DATA_DIR. Use that dynamic
    # resolver: nested platform tools import helpers lazily, and must not retain
    # the fixture's lambda pointing at a deleted world after this test returns.
    from tinyassets.api import helpers
    from tinyassets.storage import data_dir

    monkeypatch.setattr(helpers, "_base_path", data_dir)


def bash(server, command, exit_code=0):
    result = asyncio.run(server.run_bash(command=command))
    trailer = f"[exit code {exit_code}]"
    assert result.endswith(trailer), result
    return result.removesuffix(trailer).strip()


def test_real_platform_search_describe_and_call(world, monkeypatch):
    server = _engine(monkeypatch, world)
    found = json.loads(bash(server, "ta search read_graph"))
    assert any(item["name"] == "read_graph" for item in found)
    schema = json.loads(bash(server, "ta describe read_graph"))
    assert "target" in schema["arguments"]["properties"]
    result = json.loads(bash(server, 'ta read_graph --json \'{"target":"connections"}\''))
    assert not result.get("error"), result
    assert "credential" not in json.dumps(result).lower()
    # No raw connector bearer or platform source tree accompanies ta.
    assert bash(server, "test ! -w /ta/bin/ta && test ! -e /app && test ! -e /u/.runtime; echo OK")


def test_ta_runs_with_only_production_local_python(world, monkeypatch):
    """Oracle's python:3.11-slim interpreter, with Debian python absent in-jail."""
    server = _engine(monkeypatch, world)
    original = universe_tools.TOOL_JAIL_ARGV
    # On the actual slim image no Debian Python is found on the host either,
    # so bash does not prepend its unrelated Debian-Python egress forwarder.
    monkeypatch.setattr(universe_tools, "_egress_socket", lambda _root: None)

    def without_debian_python(*args, **kwargs):
        argv = original(*args, **kwargs)
        # Hide Debian binaries in this jail only; restore exactly the shell,
        # resource-limit tools and env needed to execute the shipping client.
        mounts = []
        for directory in ("/usr/bin", "/bin"):
            mounts += ["--tmpfs", directory]
            for name in ("env", "bash", "sh", "prlimit", "nice"):
                mounts += ["--ro-bind", f"/usr/bin/{name}", f"{directory}/{name}"]
        offset = argv.index("--")
        return argv[:offset] + mounts + argv[offset:]

    monkeypatch.setattr(universe_tools, "TOOL_JAIL_ARGV", without_debian_python)
    result = bash(server, "test ! -e /usr/bin/python3 && test ! -e /bin/python3 && "
                  "test -x /usr/local/bin/python3 && "
                  "test \"$(command -v python3)\" = /usr/local/bin/python3 && ta search read_graph")
    assert any(item["name"] == "read_graph" for item in json.loads(result))


def test_agent_writes_extension_and_runs_it_without_deploy(world, monkeypatch):
    server = _engine(monkeypatch, world)
    manifest = {"executable": "run", "tools": [{"name": "hello", "description": "Greet a person",
                "arguments": {"type": "object", "required": ["name"],
                              "properties": {"name": {"type": "string"}}}}]}
    program = ('#!/usr/bin/python3\nimport json,sys\n'
               'print(json.dumps({"hello":json.loads(sys.argv[2])["name"]}))\n')
    # Both shared and agent-scoped units are agent-authored in the jail itself.
    for scope, directory in (("shared", "extensions/greet"),
                             ("agent", "agents/main/extensions/greet")):
        bash(server, f"mkdir -p {directory}; printf %s {shlex.quote(json.dumps(manifest))} "
                     f"> {directory}/extension.json; printf %s {shlex.quote(program)} "
                     f"> {directory}/run; chmod +x {directory}/run")
        name = f"ext:{scope}:greet:hello"
        assert name in bash(server, "ta search Greet")
        assert json.loads(bash(server, f"ta describe {name}"))["arguments"] == (
            manifest["tools"][0]["arguments"])
        result = json.loads(bash(server, f"ta {name} --json '{{\"name\":\"Ada\"}}'"))
        assert result == {"hello": "Ada"}


def test_jailed_call_uses_agent_rules_and_never_receives_vault_secret(world, monkeypatch):
    secret = "D6-JAIL-SYNTHETIC-SECRET"
    _, root, db = _setup(world.data_root, universe_id=world.universe_a.name, token=secret)
    agent_review.set_review(root, "app.write", False, confirm=True, agent="worker")
    server = _engine(monkeypatch, world, actor="user-1")
    monkeypatch.setattr(server, "_acting_agent", lambda: "worker")
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=world.data_root / "runtime")
    command = 'ta connection:conn-http:POST --json \'{"request":{"path":"/v1/messages"}}\''
    agent_rules.set_rule(root, "app.write", agent_rules.HAND_OFF, agent="main")
    try:
        for behaviour, expected in ((agent_rules.DO, None), (agent_rules.HAND_OFF, "rule_hand_off"),
                                    (agent_rules.ASK_FIRST, "rule_ask_first")):
            agent_rules.set_rule(root, "app.write", behaviour, agent="worker")
            raw = bash(server, command, exit_code=1 if expected else 0)
            assert secret not in raw
            result = json.loads(raw)
            if expected:
                assert result["error_kind"] == expected
            else:
                assert result["delivered"] is True
                assert loop.recorded[0]["headers"]["Authorization"] == "Bearer " + secret
        assert len(loop.recorded) == 1
        assert secret not in bash(server, "ta search; env; ls -a /u")
        assert "OK" in bash(server, "test ! -e /u/.credential-vault.json && echo OK")
        # Same owner pin for a different user's jail cannot discover/call the grant.
        other = _engine(monkeypatch, world, actor="actor-b", graph=world.universe_b.name)
        assert "conn-http" not in bash(other, "ta search")
        denied = asyncio.run(other.run_bash(command=command))
        assert "unknown capability" in denied and "[exit code 1]" in denied
        assert len(loop.recorded) == 1
    finally:
        loop.stop()


def test_bridge_is_revoked_after_bash(world, monkeypatch):
    server = _engine(monkeypatch, world)
    original = universe_tools.RUNNER
    sockets = []

    def observe(*args, **kwargs):
        sockets.append(kwargs["ta_socket"])
        return original(*args, **kwargs)

    monkeypatch.setattr(universe_tools, "RUNNER", observe)
    bash(server, "ta search")
    assert sockets and all(not path.exists() for path in sockets)


def test_unapproved_v2_cannot_execute_as_legacy_extension(world, monkeypatch):
    server = _engine(monkeypatch, world)
    manifest = {
        "schema_version": 2, "executable": "run",
        "tools": [{"name": "steal", "description": "Read owner data", "arguments": {}}],
        "hooks": [{"event": "input"}],
    }
    program = '#!/bin/sh\nprintf executed > /u/unapproved-effect\nprintf \'{}\'\n'
    directory = "extensions/unapproved"
    bash(server, f"mkdir -p {directory}; printf %s {shlex.quote(json.dumps(manifest))} "
                 f"> {directory}/extension.json; printf %s {shlex.quote(program)} "
                 f"> {directory}/run; chmod +x {directory}/run")
    # stderr diagnostics are intentionally preserved by bash; stdout stays JSON.
    found = json.loads(bash(server, "ta search 2>/dev/null"))
    assert not any(item["name"].startswith("ext:shared:unapproved:") for item in found)
    denied = bash(server, "ta ext:shared:unapproved:steal --json '{}' 2>/dev/null", exit_code=1)
    assert "unknown capability" in json.loads(denied)["error"]
    assert "NOT-EXECUTED" in bash(server, "test ! -e /u/unapproved-effect && echo NOT-EXECUTED")


def test_node_grant_bounds_ta_inside_the_jail(world, monkeypatch):
    server = _engine(monkeypatch, world, tools=["read", "read_graph", "bash"])
    names = [item["name"] for item in json.loads(bash(server, "ta search"))]
    assert "read_graph" in names
    assert not {"write_graph", "run_graph", "write_brain", "source_channel",
                "connect_compute"} & set(names)
    denied = asyncio.run(server.run_bash(command="ta write_graph --json '{}'"))
    assert "unknown capability" in denied and "[exit code 1]" in denied
    # An unsigned launch still runs bash, with no ta socket bound.
    signed_launch(monkeypatch, url="http://127.0.0.1:8790/mcp")
    assert "NO-TA" in bash(server, "test -e /tmp/ta.sock || echo NO-TA")
