"""Claude plugin manifests share one version and point at the hosted MCP server."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "packaging" / "claude-plugin"
VERSION_PATH = PLUGIN_ROOT / "VERSION"
MARKETPLACE_PATH = PLUGIN_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_JSON_PATH = (
    PLUGIN_ROOT
    / "plugins"
    / "tinyassets-universe-server"
    / ".claude-plugin"
    / "plugin.json"
)
SKILLS_ROOT = PLUGIN_ROOT / "plugins" / "tinyassets-universe-server" / "skills"
HOSTED_MCP = "https://tinyassets.io/mcp"
LEGAL_OWNER = "TinyAssets, a trade name of Simkal Holdings LLC"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_claude_plugin_versions_share_one_source() -> None:
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    assert version == "0.2.0"
    assert VERSION_PATH.read_text(encoding="utf-8") == version + "\n"

    marketplace = _read(MARKETPLACE_PATH)
    plugin = _read(PLUGIN_JSON_PATH)
    found = [marketplace["metadata"]["version"]]
    found.extend(entry["version"] for entry in marketplace["plugins"])
    found.append(plugin["version"])
    assert found, "no version fields found"
    assert found == [version] * len(found)


def test_claude_plugin_connects_to_hosted_oauth_mcp() -> None:
    plugin = _read(PLUGIN_JSON_PATH)
    assert plugin["displayName"] == "TinyAssets"
    assert plugin["author"] == {"name": LEGAL_OWNER}
    assert "userConfig" not in plugin
    assert plugin["mcpServers"] == {
        "TinyAssets": {
            "type": "http",
            "url": HOSTED_MCP,
        }
    }
    rendered = json.dumps(plugin)
    assert "bootstrap.py" not in rendered
    assert '"command"' not in rendered


def test_share_manifests_use_the_trade_name_for_publisher_fields() -> None:
    mcpb = _read(REPO_ROOT / "packaging" / "mcpb" / "manifest.json")
    assert mcpb["display_name"] == "TinyAssets"
    assert mcpb["author"] == {"name": LEGAL_OWNER}
    conway = _read(REPO_ROOT / "packaging" / "conway" / "panel-metadata.json")
    assert conway["display_name"] == "TinyAssets"
    registry = _read(REPO_ROOT / "packaging" / "registry" / "server.json")
    assert registry["title"] == "TinyAssets"


def test_claude_plugin_names_are_tinyassets_specific() -> None:
    marketplace = _read(MARKETPLACE_PATH)
    assert marketplace["name"] == "tinyassets"
    assert marketplace["name"] != "workflow-plugins"
    assert marketplace["owner"] == {"name": LEGAL_OWNER}
    plugin_entry = marketplace["plugins"][0]
    assert plugin_entry["displayName"] == "TinyAssets"
    assert plugin_entry["author"] == {"name": LEGAL_OWNER}

    skill_dirs = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    assert skill_dirs
    for directory in skill_dirs:
        assert directory.name.startswith("tinyassets-"), directory.name
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith(f"---\nname: {directory.name}\n")
        assert "action=" not in text
        assert "bootstrap.py" not in text
