"""The Claude share plugin must connect to the hosted MCP door."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "packaging" / "claude-plugin"
SERVER_URL = "https://tinyassets.io/mcp"


def test_plugin_uses_hosted_server_instead_of_local_runtime() -> None:
    marketplace = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    plugin = json.loads(
        (PLUGIN_ROOT / "plugins" / "tinyassets-universe-server" / ".claude-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )
    version = (PLUGIN_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert marketplace["metadata"]["version"] == version
    assert marketplace["plugins"][0]["version"] == version
    assert plugin["version"] == version
    assert plugin["mcpServers"] == {"TinyAssets": {"type": "http", "url": SERVER_URL}}
    assert "userConfig" not in plugin
