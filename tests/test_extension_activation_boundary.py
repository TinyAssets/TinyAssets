"""Unsupported executable protocols must not fall through to legacy execution."""
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


@pytest.mark.parametrize("version", [2, 3, 0, -1, True, False, None, "1", 1.0, [], {}])
def test_unsupported_manifest_never_becomes_a_legacy_tool(
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
    assert "unsupported extension schema_version" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["hooks", "commands", "cards"])
@pytest.mark.parametrize("version", [None, 1])
def test_v2_contributions_cannot_claim_legacy_activation(tmp_path, capsys, field, version):
    fields = {field: []}
    if version is not None:
        fields["schema_version"] = version
    package(tmp_path, "unapproved", **fields)
    assert ta_cli.extensions({"shared": str(tmp_path)}) == {}
    assert "require activated schema_version 2" in capsys.readouterr().err


def test_explicit_and_implicit_v1_remain_discoverable(tmp_path, capsys):
    package(tmp_path, "implicit")
    package(tmp_path, "explicit", schema_version=1)
    found = ta_cli.extensions({"shared": str(tmp_path)})
    assert set(found) == {"ext:shared:implicit:hello", "ext:shared:explicit:hello"}
    assert capsys.readouterr().err == ""
