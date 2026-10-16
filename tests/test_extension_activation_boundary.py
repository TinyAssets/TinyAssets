"""Only activated revision contributions may enter ta discovery and dispatch."""
from __future__ import annotations

import json

import pytest

from tinyassets import ta_cli


def package(root, name, **fields):
    directory = root / name
    directory.mkdir()
    manifest = {
        "executable": "run",
        "tools": [{"name": "hello", "description": "Hello", "arguments": {}}],
        **fields,
    }
    (directory / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("version", [1, 2, 3, 0, -1, True, False, None, "1", 1.0, [], {}])
def test_workspace_manifest_never_becomes_a_tool(
    tmp_path, monkeypatch, capsys, version,
):
    package(tmp_path, "unapproved", schema_version=version)
    monkeypatch.setattr(ta_cli, "remote", lambda _: {
        "extension_roots": {"shared": str(tmp_path)},
        "capabilities": [{"name": "read_graph", "description": "Read", "arguments": {}}],
    })
    executions = []
    monkeypatch.setattr(ta_cli.subprocess, "run", lambda *a, **kw: executions.append(a))
    assert ta_cli.main(["search"]) == [{"name": "read_graph", "description": "Read"}]
    with pytest.raises(ValueError, match="unknown capability"):
        ta_cli.main(["ext:shared:unapproved:hello", "--json", "{}"])
    assert executions == []
    assert capsys.readouterr().err == ""


def test_activated_revision_catalog_remains_discoverable(monkeypatch):
    name = "extension:approved:revision:1:tools:hello"
    messages = []

    def dispatch(message):
        messages.append(message)
        if message["op"] == "catalog":
            return {"capabilities": [], "extension_capabilities": [
                {"name": name, "description": "Hello", "arguments": {}}]}
        return {"result": {"hello": "Ada"}}

    monkeypatch.setattr(ta_cli, "remote", dispatch)
    assert ta_cli.main(["search"]) == [{"name": name, "description": "Hello"}]
    assert ta_cli.main([name, "--json", '{}']) == {"hello": "Ada"}
    assert messages[-1] == {"op": "call", "name": name, "arguments": {}}
