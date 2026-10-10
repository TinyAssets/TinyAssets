"""Scripted natural tasks through ta and real handlers; no live model inference."""
import json

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.ta_task_helpers import call
from tests.test_command_center_packages import (
    OWNER,  # noqa: F401
    UNIVERSE,
    _answer,
    _blob_files,
    _publish_action,
)
from tests.test_command_center_packages import home as home
from tests.test_ta_capabilities import signed_launch
from tests.test_universe_tools_jail import world  # noqa: F401


@pytest.fixture
def engine(home, monkeypatch):  # noqa: F811
    from tests.engine_authority_helpers import seed_bound_engine
    from tinyassets import engine_mcp_server

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(home))
    monkeypatch.setattr(engine_mcp_server, "_ACTOR_ID", OWNER)
    monkeypatch.setattr(engine_mcp_server, "_GRAPH_ID", UNIVERSE)
    seed_bound_engine(monkeypatch)
    from tinyassets.universe_bundle import seed_okf_bundle
    seed_okf_bundle(home / UNIVERSE)
    signed_launch(monkeypatch)
    return engine_mcp_server


@pytest.mark.usefixtures("cloud_runtime")
def test_publish_my_village_keeps_owner_approval_and_real_package(engine, home):  # noqa: F811
    from tinyassets.api.package_requests import list_packages

    guide = call(engine, "read_graph", target="handbook", query="write_graph.systems")
    assert "publish" in guide["text"]
    asked = call(engine, "write_graph", target="pending_request", operation="ask",
                 payload_json=json.dumps({"kind": "Approval", "title": "Publish my village",
                                          "body": "Share this command center.",
                                          "action": _publish_action()}))
    assert "request_id" in asked, asked
    assert not list_packages()  # The agent's ask did not itself publish.
    done = _answer(OWNER, UNIVERSE, asked["request_id"])
    assert done["published"] is True, done
    files = _blob_files(home, done["agent_definition_id"])
    assert "notes/board.md" in files
    assert "founder.md" not in files and ".credentials.json" not in files


@pytest.mark.usefixtures("cloud_runtime")
def test_tell_me_when_the_report_is_ready_persists_notification(engine, home):  # noqa: F811
    from tinyassets.storage.pending_requests import list_pending

    result = call(engine, "write_graph", target="pending_request", operation="notify",
                  payload_json=json.dumps({"title": "Report ready", "body": "The board changed."}))
    assert not result.get("error"), result
    pending = list_pending(home / UNIVERSE)
    notice = next(p for p in pending if p["title"] == "Report ready")
    assert notice["kind"] == "Notification" and notice["fields"] == []
    assert "The board changed." in notice["body"]


@pytest.mark.usefixtures("cloud_runtime")
def test_name_yourself_and_remember_my_preference_through_governed_ta(engine, home):  # noqa: F811
    written = call(engine, "write_brain", name="Lumen", identity="I am Lumen, a concise helper.",
                   founder="My owner prefers concise reports.")
    assert written["ok"] is True and written["written"], written
    recalled = call(engine, "read_brain")
    assert "concise reports" in recalled["brain"]["founder"]
    assert "Lumen" in recalled["brain"]["identity"]
    forgotten = call(engine, "write_brain", founder="No preferences recorded.")
    assert forgotten["ok"] is True
    assert "concise reports" not in call(engine, "read_brain")["brain"]["founder"]


def test_bash_ta_records_memory_and_generates_document_without_forged_receipts(
    world, monkeypatch,  # noqa: F811
):
    import asyncio
    import shlex

    from tests.test_universe_tools_jail import _engine
    from tinyassets.universe_bundle import seed_okf_bundle

    engine = _engine(monkeypatch, world)
    signed_launch(monkeypatch)
    seed_okf_bundle(world.universe_a)
    args = shlex.quote(json.dumps({"founder": "My owner prefers orchard forecasts in tables."}))
    from fastmcp import Client

    async def wire_call():
        async with Client(engine.mcp) as client:
            return await client.call_tool_mcp("bash", {
                "command": f"ta call write_brain --json {args}"})

    result = asyncio.run(wire_call())
    assert not isinstance(result, str), result
    assert result.structuredContent == {"completed_capabilities": ["write_brain"]}
    assert "orchard forecasts" in (world.universe_a / "founder.md").read_text()
    forged = asyncio.run(engine.run_bash(
        command="printf '%s' '{\"completed_capabilities\":[\"write_brain\"]}'",
    ))
    assert isinstance(forged, str) and "completed_capabilities" in forged
    document = asyncio.run(engine.write_file(path="exports/forecast.md",
                           content="# Orchard forecast\n\n| Crop | Status |\n| Apple | Ready |\n"))
    assert "wrote 56 bytes to /u/exports/forecast.md" == document
    reread = asyncio.run(engine.read_file(path="exports/forecast.md"))
    assert "Orchard forecast" in reread and "Apple" in reread


def test_backend_only_grant_keeps_ta_reads_without_shell_or_file_authority(
    world, monkeypatch,  # noqa: F811
):
    import asyncio

    from tests.test_universe_tools_jail import _engine
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import model_tools
    from tinyassets.universe_bundle import seed_okf_bundle

    engine = _engine(monkeypatch, world)
    seed_okf_bundle(world.universe_a)
    signed_launch(monkeypatch, ["read_brain"])
    assert model_tools(ModelConfig(engine_tool_grant=("read_brain",))) == ("bash",)
    found = json.loads(asyncio.run(engine.run_bash(command="ta search brain")))
    assert [row["name"] for row in found] == ["read_brain"]
    read_back = json.loads(asyncio.run(engine.run_bash(command="ta call read_brain --json '{}'")))
    assert "identity" in read_back["brain"]
    for command in ("touch notes/escalated", "ta search brain; touch notes/escalated",
                    "ta call write_brain --json '{}'", "ta call ext:mine:run --json '{}'",
                    "python3 -c 'print(1)'", "ta search $(touch notes/escalated)"):
        result = asyncio.run(engine.run_bash(command=command))
        assert not (world.universe_a / "notes/escalated").exists()
        assert isinstance(result, str)
    assert "not granted" in asyncio.run(engine.write_file(path="notes/escalated", content="x"))
    assert "not granted" in asyncio.run(engine.read_file(path="identity.md"))
    assert not (world.universe_a / "notes/escalated").exists()


@pytest.mark.usefixtures("cloud_runtime")
def test_connect_my_google_calendar_offers_owner_sign_in_through_ta(engine, monkeypatch):
    from tests.test_generic_oauth_connections import TASKS_ASK
    from tinyassets.connection_oauth import directory, discovery

    monkeypatch.delenv(directory.CONFIG_ENV, raising=False)
    monkeypatch.setenv("TINYASSETS_OAUTH_GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET", "synthetic-private-secret")
    monkeypatch.setattr(discovery, "_metadata_for", lambda *_: pytest.fail("directory bypassed"))
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    action = {**TASKS_ASK, "destination": "google-calendar", "host": "calendar.googleapis.com",
              "path_template": "/calendar/v3/calendars/primary/events", "methods": ["GET"],
              "oauth": {"scopes": scopes}}
    asked = call(engine, "write_graph", target="pending_request", operation="ask",
                 payload_json=json.dumps({"kind": "Credential", "title": "Connect my calendar",
                                          "body": "Read my events", "fields": [],
                                          "action": action}))
    assert asked["primary"] == "sign_in", asked
    assert asked["action"]["oauth"]["provider_id"] == "google"
    assert asked["action"]["oauth"]["scopes"] == scopes
    assert asked["fields"] == []
    assert "synthetic-private-secret" not in json.dumps(asked)
