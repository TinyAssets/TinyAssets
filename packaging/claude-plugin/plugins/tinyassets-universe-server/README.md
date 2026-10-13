# TinyAssets

Packaged Claude Code plugin for the hosted TinyAssets MCP server at
`https://tinyassets.io/mcp`. Enabling the plugin connects to that address.
Sign-in is the server's OAuth flow. The plugin does not start a local server.

## Packaged Skills

- [tinyassets-premise/SKILL.md](skills/tinyassets-premise/SKILL.md)
- [tinyassets-progress/SKILL.md](skills/tinyassets-progress/SKILL.md)
- [tinyassets-status/SKILL.md](skills/tinyassets-status/SKILL.md)
- [tinyassets-steer/SKILL.md](skills/tinyassets-steer/SKILL.md)

## Structure

- `.claude-plugin/` - plugin metadata. Version matches `packaging/claude-plugin/VERSION`.
- `skills/` - plugin-local slash commands. Each name starts with `tinyassets-`.

## Notes

- These skills are intentionally scoped to the packaged plugin experience.
- They should be read together as a plugin surface, not mixed into the main
  engineering skill graph.
