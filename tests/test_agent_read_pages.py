"""Public definition metadata stays visible; bodies round-trip through real doors."""
import json

import pytest
from fastmcp import Client

from tests.engine_authority_helpers import mock_engine_admission
from tinyassets.custom_agents import get_definition, publish_definition

TAG = "tinyassets.command-center-package.v1"


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets import engine_mcp_server as engine
    monkeypatch.setattr(engine, "_ACTOR_ID", "viewer")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-viewer")
    mock_engine_admission(monkeypatch, {"u-viewer"})

    def publish(name="Village", components=None, tags=None, author="publisher"):
        return publish_definition(tmp_path, author_id=author, payload={
            "schema_version": 1, "name": name, "description": "A whole system",
            "tags": tags if tags is not None else [TAG],
            "components": components or {
                "ui": {"kind": "tinyassets.ui.v1", "html": "界\\\"\n😀" * 6000},
                "workflow": {"kind": "tinyassets.branch-ref.v1", "branch_def_id": "branch-123"},
                "package": {"kind": "tinyassets.package.v1", "version": 3,
                            "file_count": 12, "size_bytes": 9000,
                            "agents": ["main"], "needs": {"model": "own", "connections": ["mail"]}},
            },
        })
    return publish, tmp_path


def read(**kw):
    from tinyassets.universe_server import read_graph
    return json.loads(read_graph(**kw))


def test_metadata_and_lossless_unicode_components(published, monkeypatch):
    publish, base = published
    row = publish()
    aid = row["agent_definition_id"]
    monkeypatch.setenv("TINYASSETS_ENGINE_RESULT_CEILING_BYTES", "4096")
    catalog = read(target="agent", agent_definition_id=aid)
    assert catalog["agent"]["name"] == "Village"
    assert catalog["agent"]["publication_kind"] == "command_center"
    assert catalog["agent"]["package"]["file_count"] == 12
    assert catalog["agent"]["component_count"] == 3
    keys = []
    while True:
        assert len(json.dumps(catalog).encode()) < 4096
        keys += [c["key"] for c in catalog["components"]]
        if catalog["next_offset"] is None:
            break
        catalog = read(target="agent", agent_definition_id=aid,
                       output_offset=catalog["next_offset"])
    assert keys == ["package", "ui", "workflow"]
    original = get_definition(base, aid)
    for key in ["ui", "workflow", "package", "@definition"]:
        chunks, offset = [], 0
        while True:
            page = read(target="agent", agent_definition_id=aid, field_name=key,
                        output_offset=offset, output_max_chars=32768)
            assert len(json.dumps(page).encode()) < 4096
            chunks.append(page["chunk"])
            if page["next_offset"] is None:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        assert json.loads("".join(chunks)) == (original if key == "@definition"
                                              else original["components"][key])
    assert read(target="agent", agent_definition_id=aid, field_name="ui")["offset"] == 0
    from tinyassets.api.graph_reads import read_graph as domain_read
    assert json.loads(domain_read(target="agent", agent_definition_id=aid))["agent"] == original


@pytest.mark.parametrize("offset", [-1, 0.1, "1", True, 999999])
def test_invalid_offsets_refuse(published, offset):
    publish, _ = published
    aid = publish()["agent_definition_id"]
    for field in ("", "ui"):
        assert "error" in read(target="agent", agent_definition_id=aid,
                               field_name=field, output_offset=offset)
    assert "error" in read(target="agents", output_offset=offset)


def test_catalog_and_list_have_no_missing_rows(published, monkeypatch):
    publish, _ = published
    monkeypatch.setenv("TINYASSETS_ENGINE_RESULT_CEILING_BYTES", "4096")
    many = {f"part{i:03d}": {"kind": "custom", "name": "界" * 64, "body": "large" * 100}
            for i in range(70)}
    row = publish(components=many, tags=["filter"])
    offset, keys = 0, []
    while True:
        page = read(target="agent", agent_definition_id=row["agent_definition_id"],
                    output_offset=offset)
        keys += [c["key"] for c in page["components"]]
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert keys == sorted(many)
    ids = {row["agent_definition_id"]}
    for i in range(7):
        ids.add(publish(name=f"Village {i}", tags=["filter"])["agent_definition_id"])
    publish(name="Other", tags=["other"])
    offset, seen = 0, []
    while True:
        page = read(target="agents", tags="filter", limit=3, output_offset=offset)
        seen += [r["agent_definition_id"] for r in page["agents"]]
        assert all("html" not in json.dumps(r) for r in page["agents"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert len(seen) == len(set(seen)) == len(ids)
    assert set(seen) == ids
    assert read(target="agents", tags="filter", output_offset=len(ids))["agents"] == []
    assert read(target="agents", tags="filter", output_offset=0)["offset"] == 0


@pytest.mark.asyncio
async def test_real_served_component_and_oauth_schema(published):
    publish, _ = published
    aid = publish()["agent_definition_id"]
    from tinyassets import engine_mcp_server as engine
    from tinyassets import universe_server as connector
    async with Client(engine.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        props = tools["read_commons_shape"].inputSchema["properties"]
        assert {"field_name", "output_offset", "output_max_chars"} <= props.keys()
        response = await client.call_tool("read_commons_shape", {
            "agent_definition_id": aid, "field_name": "workflow", "output_max_chars": 1000,
        })
        result = json.loads(response.content[0].text)
        assert result["untrusted"] is True
        assert result["content"]["field_name"] == "workflow"
        assert json.loads(result["content"]["chunk"])["branch_def_id"] == "branch-123"
    async with Client(connector.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        tool = tools["read_graph"]
        props = tool.inputSchema["properties"]
        assert {"field_name", "output_offset", "output_max_chars"} <= props.keys()
        assert tool.model_dump(by_alias=True)["securitySchemes"][0]["type"] == "oauth2"
        response = await client.call_tool("read_graph", {
            "target": "agent", "agent_definition_id": aid, "field_name": "workflow",
        })
        assert not response.is_error
        assert "branch-123" in response.content[0].text


def test_package_pages_validate_kind_before_offsets(published):
    publish, _ = published
    from tinyassets.api.package_requests import list_packages
    first = publish(name="First")["agent_definition_id"]
    publish(name="Not a package", components={"package": {"kind": "fake"}})
    second = publish(name="Second")["agent_definition_id"]
    seen = {list_packages(limit=1, offset=i)[0]["agent_definition_id"] for i in (0, 1)}
    assert seen == {first, second}
    assert list_packages(offset=2) == []
    fake = read(target="agents", query="Not a package")["agents"][0]
    assert fake["publication_kind"] == "system"


def test_private_stage_is_not_public_definition(published):
    publish, _ = published
    aid = publish()["agent_definition_id"]
    assert read(target="agent", agent_stage_id=aid)["error"] == "not_found"
    assert read(target="agent", agent_definition_id="missing")["error"] == "not_found"
    assert "error" in read(target="agent", agent_definition_id=aid, field_name="missing")


@pytest.mark.asyncio
async def test_served_browse_pages_survive_wrapping(published, monkeypatch):
    publish, _ = published
    from tinyassets import engine_mcp_server as engine
    monkeypatch.setenv("TINYASSETS_ENGINE_RESULT_CEILING_BYTES", "4096")
    ids = set()
    for i in range(6):
        ids.add(publish(name=f"Package {i}", author="viewer" if i % 2 else "publisher",
                       components={"ui": {"kind": "tinyassets.app-ui.v1", "html": "x" * 30000},
                                   "package": {"kind": "tinyassets.package.v1", "version": i,
                                               "agents": ["agent" + str(j) for j in range(1000)],
                                               "needs": {"connections": ["mail" + str(j)
                                                                         for j in range(1000)]},
                                               "file_count": 1, "size_bytes": 30000}})
                ["agent_definition_id"])
    async with Client(engine.mcp) as client:
        for kind in ("agents", "packages"):
            offset, seen = 0, []
            while True:
                response = await client.call_tool("browse_commons", {
                    "kind": kind, "limit": 3, "output_offset": offset,
                })
                assert not response.is_error
                result = json.loads(response.content[0].text)
                assert "truncated" not in result
                assert len(response.content[0].text.encode()) <= 4096
                content = result.get("content", result)
                key = kind
                rows = content[key] + (result.get("own") or {}).get(key, [])
                seen += [row["agent_definition_id"] for row in rows]
                for row in rows:
                    assert row["publication_kind"] == "command_center"
                    if kind == "packages":
                        assert row["agent_count"] == 1000
                        assert row["needs"]["connection_count"] == 1000
                        assert row["details_field"] == "package"
                if content["next_offset"] is None:
                    break
                assert content["next_offset"] > offset
                offset = content["next_offset"]
            assert set(seen) == ids
            assert len(seen) == len(ids)


@pytest.mark.parametrize("limit", [0, -1, True, "5", 101])
def test_bad_list_limits_refuse(published, limit):
    published[0]()
    assert "error" in read(target="agents", limit=limit)


@pytest.mark.parametrize("size", [0, -1, True, "5", 32769])
def test_bad_component_sizes_refuse(published, size):
    aid = published[0]()["agent_definition_id"]
    assert "error" in read(target="agent", agent_definition_id=aid,
                           field_name="ui", output_max_chars=size)


@pytest.mark.asyncio
async def test_author_prefix_is_never_confused_with_owner(published, monkeypatch):
    publish, _ = published
    from tinyassets import engine_mcp_server as engine
    owner = "v" * 24
    monkeypatch.setattr(engine, "_ACTOR_ID", owner)
    mock_engine_admission(monkeypatch, {"u-viewer"})
    monkeypatch.setenv("TINYASSETS_ENGINE_RESULT_CEILING_BYTES", "4096")
    publish(author=owner + "-foreign")
    async with Client(engine.mcp) as client:
        for kind in ("agents", "packages"):
            response = await client.call_tool("browse_commons", {"kind": kind})
            result = json.loads(response.content[0].text)
            assert result["untrusted"] is True
            assert not result.get("own")
            assert result["content"][kind][0]["author_id"] == owner + "-foreign"


def test_branch_read_never_ignores_component_selectors(published):
    from tinyassets import engine_mcp_server as engine
    result = json.loads(engine.read_commons_shape(branch_id="branch", output_offset=10))
    assert result["error"] == "component selectors apply only to agent_definition_id"
