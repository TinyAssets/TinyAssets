"""Real Streamable-HTTP coverage for the scoped wiki canary authority."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.auth.middleware import set_provider
from tinyassets.auth.provider import DevAuthProvider, OptionalOAuthProvider
from tinyassets.universe_server import create_streamable_http_app

pytestmark = pytest.mark.usefixtures("cloud_runtime")

_CANARY_TOKEN = "transport-canary-token-value-32-bytes"


@pytest.fixture
def transport_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    set_provider(OptionalOAuthProvider(tmp_path / "auth.db"))
    try:
        with TestClient(create_streamable_http_app()) as client:
            yield client
    finally:
        set_provider(DevAuthProvider())


def _post_mcp(
    client: TestClient,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
    bearer_token: str | None = None,
):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2024-11-05",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return client.post("/mcp", headers=headers, json=payload)


def _response_json(response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    return response.json()


def test_canary_token_reaches_write_page_over_stateful_streamable_http(
    transport_client: TestClient,
    tmp_path,
) -> None:
    anonymous = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "anonymous-probe", "version": "1"},
            },
        },
    )
    assert anonymous.status_code == 401
    assert anonymous.json() == {"error": "authentication_required"}
    assert anonymous.headers["www-authenticate"].startswith("Bearer ")

    initialize = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "wiki-canary-transport-test",
                    "version": "1.0",
                },
            },
        },
        bearer_token=_CANARY_TOKEN,
    )
    assert initialize.status_code == 200
    session_id = initialize.headers["mcp-session-id"]

    initialized = _post_mcp(
        transport_client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        bearer_token=_CANARY_TOKEN,
    )
    assert initialized.status_code in {200, 202}

    write = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "write_page",
                "arguments": {
                    "category": "notes",
                    "filename": "uptime-probe",
                    "content": "real-transport-roundtrip",
                    "dry_run": False,
                },
            },
        },
        session_id=session_id,
        bearer_token=_CANARY_TOKEN,
    )

    assert write.status_code == 200
    result = _response_json(write)["result"]["structuredContent"]
    assert result == {
        "path": "drafts/notes/uptime-probe.md",
        "status": "drafted",
        "note": "Drafted reserved uptime canary page.",
    }
    assert (
        tmp_path / ".wiki" / "drafts" / "notes" / "uptime-probe.md"
    ).read_text(encoding="utf-8") == "real-transport-roundtrip"

    read = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "read_page",
                "arguments": {"page": "uptime-probe"},
            },
        },
        session_id=session_id,
        bearer_token=_CANARY_TOKEN,
    )
    assert read.status_code == 200
    assert _response_json(read)["result"]["structuredContent"]["content"].endswith(
        "real-transport-roundtrip"
    )


def test_canary_get_status_keeps_uptime_fields_without_a_home_universe(
    transport_client: TestClient,
) -> None:
    initialize = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "deploy-canary", "version": "1.0"},
            },
        },
        bearer_token=_CANARY_TOKEN,
    )
    assert initialize.status_code == 200
    session_id = initialize.headers["mcp-session-id"]

    initialized = _post_mcp(
        transport_client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        bearer_token=_CANARY_TOKEN,
    )
    assert initialized.status_code in {200, 202}

    status = _post_mcp(
        transport_client,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        },
        session_id=session_id,
        bearer_token=_CANARY_TOKEN,
    )

    assert status.status_code == 200
    payload = _response_json(status)["result"]["structuredContent"]
    assert payload["first_contact"]["event"] == "no_universe_yet"
    assert set(payload["active_host"]) >= {
        "host_id",
        "served_llm_type",
        "llm_endpoint_bound",
        "api_key_providers_enabled",
    }
    assert "release_state" in payload
