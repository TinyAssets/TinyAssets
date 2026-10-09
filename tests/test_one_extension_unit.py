"""Revision and authority boundaries of the unified extension lifecycle."""
import json

import pytest

from tinyassets.extension_manifest import ExtensionError, build_revision, parse_manifest
from tinyassets.extension_state import ExtensionStore


def files(**extra):
    manifest = {
        "schema_version": 2, "name": "sample", "executable": "run.py",
        "tools": [{"name": "hello", "description": "Hello", "arguments": {}}],
        "hooks": [{"name": "start", "description": "Start", "arguments": {},
                   "event": "turn_start"}],
        "commands": [{"name": "greet", "description": "Greet", "arguments": {}}],
        "cards": [{"name": "view", "description": "View", "asset": "view.html"}],
        "connections": [{"name": "api", "description": "API", "verbs": ["GET"]}],
        "mcp_servers": [{"name": "remote", "description": "Remote", "transport": "remote",
                         "url": "https://example.com/mcp", "slot": "api"},
                        {"name": "stdio", "description": "Stdio", "transport": "stdio",
                         "executable": "run.py", "args": ["serve"]}],
        **extra,
    }
    return {"extension.json": json.dumps(manifest).encode(), "run.py": b"print('hello')",
            "view.html": b"<h1>Hello</h1>", "helper.txt": b"first"}


def store(tmp_path, owner="user-1", universe="home", agent="main"):
    return ExtensionStore(tmp_path, owner=owner, universe=universe, agent=agent)


def test_complete_package_revision_uses_existing_sharing_format():
    from tinyassets.command_center_packages import check_blob

    original = files()
    rev = build_revision(original)
    manifest, content = check_blob(rev.blob)
    assert manifest["needs"]["connections"] == ["api"]
    assert content["extensions/sample/helper.txt"] == b"first"
    assert rev.content()[1] == original
    assert build_revision(dict(reversed(list(original.items())))).digest == rev.digest
    original["helper.txt"] = b"second"
    assert build_revision(original).digest != rev.digest


@pytest.mark.parametrize("fields", [
    {"schema_version": True}, {"schema_version": 1}, {"unknown": []},
    {"executable": "../run"}, {"executable": "/host/run"},
    {"tools": [{"name": "x", "description": "X", "arguments": {"type": "bogus"}}]},
    {"tools": [{"name": "x", "description": "X", "arguments": {"$ref": "https://x"}}]},
    {"hooks": [{"name": "x", "description": "X", "arguments": {}, "event": "approve"}]},
    {"cards": [{"name": "x", "description": "X", "asset": "missing"}]},
    {"connections": [{"name": "api", "description": "X", "verbs": ["GET"], "key": "secret"}]},
    {"connections": [{"name": "api", "description": "X", "verbs": ["GET", "GET"]}]},
    {"mcp_servers": [{"name": "x", "description": "X", "transport": "remote",
                      "url": "https://user:secret@example.com/mcp"}]},
    {"mcp_servers": [{"name": "x", "description": "X", "transport": "stdio",
                      "executable": "run.py", "env": {"KEY": "secret"}}]},
    {"mcp_servers": [{"name": "x", "description": "X", "transport": "remote",
                      "url": "https://example.com/mcp", "slot": "absent"}]},
])
def test_invalid_contributions_fail_atomically(fields):
    with pytest.raises(ExtensionError):
        build_revision(files(**fields))


@pytest.mark.parametrize("raw", [b'{"name":"x","name":"y"}', b'{"name":NaN}', b'[]'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(ExtensionError):
        parse_manifest(raw)


@pytest.mark.parametrize("path", ["../escape", "RUN.py", "view.html/child", "nested/.key"])
def test_invalid_trees(path):
    content = files()
    content[path] = b"bad"
    with pytest.raises(ExtensionError):
        build_revision(content)


def test_install_is_inert_and_update_cannot_change_activation(tmp_path):
    state = store(tmp_path)
    content = files()
    first = state.install(content)
    assert state.list()[0]["state"] == "installed"
    with pytest.raises(ExtensionError, match="activation unavailable"):
        state.active("sample", first["revision"], 0, current_capabilities=[])
    activated = state.transition("sample", first["revision"], expected_generation=0,
                                 active=True, ceiling=["read_graph"])
    content["helper.txt"] = b"edited"
    second = state.install(content)
    assert first["revision"] != second["revision"]
    assert state.load("sample", first["revision"]).content()[1]["helper.txt"] == b"first"
    assert activated["generation"] == 1
    assert state.active("sample", first["revision"], 1,
                        current_capabilities=["read_graph", "write_graph"]) == {"read_graph"}
    assert state.active("sample", first["revision"], 1, current_capabilities=[]) == set()
    with pytest.raises(ExtensionError, match="generation changed"):
        state.transition("sample", second["revision"], expected_generation=0, active=True)
    state.transition("sample", second["revision"], expected_generation=1, active=True)
    with pytest.raises(ExtensionError, match="activation unavailable"):
        state.active("sample", first["revision"], 1, current_capabilities=["read_graph"])
    state.transition("sample", second["revision"], expected_generation=2, active=False)
    with pytest.raises(ExtensionError, match="activation unavailable"):
        state.active("sample", second["revision"], 2, current_capabilities=["read_graph"])
    revoked = next(row for row in state.list() if row["revision"] == second["revision"])
    assert revoked["state"] == "revoked"


@pytest.mark.parametrize("other", [
    {"owner": "user-2"}, {"universe": "other"}, {"agent": "other"},
])
def test_identity_isolation_denies_digest_before_blob_lookup(tmp_path, monkeypatch, other):
    from tinyassets import command_center_packages

    installed = store(tmp_path).install(files())
    foreign = store(tmp_path, **other)
    assert foreign.list() == []
    def forbidden(*args):
        pytest.fail("foreign revision reached blob lookup")
    monkeypatch.setattr(command_center_packages, "read_blob", forbidden)
    with pytest.raises(ExtensionError, match="not installed here"):
        foreign.load("sample", installed["revision"])
    with pytest.raises(ExtensionError, match="not installed here"):
        foreign.transition("sample", installed["revision"], expected_generation=0, active=True)


def backend(tmp_path, **context):
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    root = tmp_path / "home"
    root.mkdir(exist_ok=True)
    return Capabilities(root, ExecutionContext("home", "user-1", "main", **context),
                        [], None, lambda: None, connections_granted=False,
                        capability_grant=("write",))


def call(service, name, arguments):
    import asyncio

    return asyncio.run(service.dispatch({"op": "call", "name": name, "arguments": arguments}))


@pytest.mark.parametrize("kind", ["tools", "commands", "hooks"])
def test_new_connection_does_not_break_mounted_contributions(tmp_path, monkeypatch, kind):
    from tinyassets.extension_capabilities import ExtensionCapabilities

    service = backend(tmp_path)
    ext = ExtensionCapabilities(service)
    installed = ext.store.install(files())
    state = call(service, "extension:activate", {"name": "sample",
        "revision": installed["revision"], "expected_generation": 0})["result"]
    import contextvars

    launch = contextvars.copy_context()
    launch.run(ext.materialize, tmp_path / "mount")
    monkeypatch.setattr(service, "connections", lambda: {"connection:new:GET": None})
    key = next(row["name"] for row in ext.catalog() if row.get("kind") == kind)
    assert "extension_execution" in launch.run(call, service, key, {})["result"]
    assert ext.store.active("sample", installed["revision"], state["generation"],
                            current_capabilities=ext._current()) == set()


def test_ta_lifecycle_and_settings_only_narrow(tmp_path):
    import asyncio
    import base64

    service = backend(tmp_path)
    result = call(service, "extension:install", {"files": {
        path: base64.b64encode(data).decode() for path, data in files().items()}})["result"]
    pin = {"name": "sample", "revision": result["revision"], "expected_generation": 0}
    assert call(service, "extension:activate", pin)["result"]["state"] == "active"
    def entries():
        return asyncio.run(service.dispatch({"op": "catalog"}))["extension_capabilities"]
    contributions = [row for row in entries() if "kind" in row]
    assert {row["kind"] for row in contributions} == {
        "tools", "commands", "hooks", "cards", "connections", "mcp_servers"}
    connection = next(row for row in contributions if row["kind"] == "connections")
    assert call(service, connection["name"], {})["result"]["grants_created"] is False
    tool = next(row for row in contributions if row["kind"] == "tools")
    assert call(service, tool["name"], {})["result"]["error"] == "extension_runtime_unavailable"
    settings = service.root / "settings.yaml"
    settings.write_text("schema_version: 1\nextensions:\n  enabled: []\n", encoding="utf-8")
    assert not any("kind" in row for row in entries())
    assert "error" in call(service, tool["name"], {})
    settings.write_text("schema_version: 1\nextensions:\n  enabled: [sample]\n", encoding="utf-8")
    assert any("kind" in row for row in entries())
    pin["expected_generation"] = 1
    assert call(service, "extension:revoke", pin)["result"]["state"] == "revoked"
    assert not any("kind" in row for row in entries())
    assert "error" in call(service, tool["name"], {})


@pytest.mark.parametrize("context", [
    {"research": True}, {"delegated_authority": "outside-agent"}, {"approval_id": "request"},
])
def test_delegated_and_research_cannot_mutate_lifecycle(tmp_path, context):
    service = backend(tmp_path, **context)
    assert "error" in call(service, "extension:install", {"files": {}})
    assert not store(tmp_path).list()


def test_owner_erasure_tables_keep_foreign_rows(tmp_path):
    from tinyassets.account_deletion import deletion_plan

    own = store(tmp_path)
    foreign = store(tmp_path, owner="user-2")
    installed = own.install(files())
    foreign.install(files())
    own.transition("sample", installed["revision"], expected_generation=0, active=True)
    with own._db() as conn:
        plan = deletion_plan(conn, principal="user-1", home="home")
        assert "extension_revisions" in plan
        assert "extension_activations" in plan
        # The existing erasure planner must use the owner, not the shared home.
        assert plan["extension_revisions"] == [("owner_id", "principal")]
        assert plan["extension_activations"] == [("owner_id", "principal")]
        rows = conn.execute("SELECT owner_id FROM extension_revisions WHERE owner_id=?",
                            ("user-1",)).fetchall()
        assert [row[0] for row in rows] == ["user-1"]


def test_corrupt_revision_remains_revocable_and_does_not_hide_healthy_package(tmp_path):
    import asyncio

    from tinyassets import command_center_packages
    from tinyassets.extension_capabilities import ExtensionCapabilities

    service = backend(tmp_path)
    adapter = ExtensionCapabilities(service)
    first = adapter.store.install(files())
    second = adapter.store.install(files(name="healthy"))
    for item in (first, second):
        adapter.store.transition(item["name"], item["revision"], expected_generation=0, active=True)
    command_center_packages._blob_path(tmp_path, first["revision"]).write_bytes(b"corrupt")
    assert adapter.materialize(tmp_path / "mount")
    entries = asyncio.run(service.dispatch({"op": "catalog"}))["extension_capabilities"]
    assert any(row["name"] == "extension:revoke" for row in entries)
    assert any(row.get("availability") == "invalid" for row in entries)
    assert any(row["name"].startswith("extension:healthy:") for row in entries)
    result = call(service, "extension:revoke", {"name": first["name"],
                  "revision": first["revision"], "expected_generation": 1})
    assert result["result"]["state"] == "revoked"


def test_malformed_settings_preserve_lifecycle_recovery(tmp_path):
    import asyncio

    from tinyassets.extension_capabilities import ExtensionCapabilities

    service = backend(tmp_path)
    adapter = ExtensionCapabilities(service)
    installed = adapter.store.install(files())
    adapter.store.transition("sample", installed["revision"], expected_generation=0, active=True)
    (service.root / "settings.yaml").write_text("schema_version: INVALID", encoding="utf-8")
    assert adapter.materialize(tmp_path / "mount")
    assert not list((tmp_path / "mount").iterdir())
    entries = asyncio.run(service.dispatch({"op": "catalog"}))["extension_capabilities"]
    assert any(row["name"] == "extension:revoke" for row in entries)
    assert any(row.get("availability") == "invalid" for row in entries)
    result = call(service, "extension:revoke", {"name": "sample", "revision": installed["revision"],
                                               "expected_generation": 1})
    assert result["result"]["state"] == "revoked"


def test_mount_availability_is_private_to_each_launch_context(tmp_path):
    import contextvars

    from tinyassets.extension_capabilities import ExtensionCapabilities

    service = backend(tmp_path)
    adapter = ExtensionCapabilities(service)
    installed = adapter.store.install(files())
    state = adapter.store.transition("sample", installed["revision"], expected_generation=0,
                                     active=True)
    first, second = contextvars.copy_context(), contextvars.copy_context()
    first.run(adapter.materialize, tmp_path / "first")
    assert first.run(adapter._mounted, state)
    adapter.store.transition("sample", installed["revision"], expected_generation=1,
                             active=False)
    second.run(adapter.materialize, tmp_path / "second")
    assert not second.run(adapter._mounted, state)
    assert first.run(adapter._mounted, state)


@pytest.mark.parametrize("context", [{"research": True}, {"delegated_authority": "outside"}])
def test_no_mount_when_lifecycle_authority_missing(tmp_path, context):
    from tinyassets.extension_capabilities import ExtensionCapabilities

    adapter = ExtensionCapabilities(backend(tmp_path, **context))
    assert adapter.materialize(tmp_path / "mount") is False
    assert not (tmp_path / "mount").exists()
