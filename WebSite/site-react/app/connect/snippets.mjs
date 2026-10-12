/** Copy-paste blocks for /connect. One hosted address, four clients. */

export const MCP_URL = "https://tinyassets.io/mcp";
export const CONTACT = "info@tinyassets.io";
export const API_KEYS_PR = "https://github.com/TinyAssets/TinyAssets/pull/4602";

/** Cursor install link. `config` is base64 of `{"url":"<MCP_URL>"}`. */
export const CURSOR_DEEPLINK =
  "cursor://anysphere.cursor-deeplink/mcp/install?name=TinyAssets&config=eyJ1cmwiOiJodHRwczovL3Rpbnlhc3NldHMuaW8vbWNwIn0%3D";

export const CLAUDE_CODE_JSON = `{
  "mcpServers": {
    "TinyAssets": {
      "type": "http",
      "url": "${MCP_URL}"
    }
  }
}`;

export const CURSOR_JSON = `{
  "mcpServers": {
    "TinyAssets": {
      "url": "${MCP_URL}"
    }
  }
}`;

export const CODEX_COMMANDS = `codex mcp add TinyAssets --url ${MCP_URL}
codex mcp login TinyAssets`;

export const CODEX_TOML = `[mcp_servers.TinyAssets]
url = "${MCP_URL}"`;

export const GENERIC_JSON = `{
  "mcpServers": {
    "TinyAssets": {
      "type": "streamable-http",
      "url": "${MCP_URL}"
    }
  }
}`;
