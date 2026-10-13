# Claude Plugin Packaging

This directory contains the TinyAssets plugin marketplace surface plus the
packaged `tinyassets-universe-server` plugin.

## Core

- [Plugin README](plugins/tinyassets-universe-server/README.md)

## Structure

- `.claude-plugin/marketplace.json` - marketplace manifest
- `plugins/tinyassets-universe-server/` - packaged plugin contents

## Notes

- `VERSION` is the only plugin version. `.claude-plugin/marketplace.json` and
  `plugins/tinyassets-universe-server/.claude-plugin/plugin.json` copy it.
  `tests/test_claude_plugin_manifests.py` fails if they disagree.
- The plugin connects Claude Code to `https://tinyassets.io/mcp`. Claude Code
  discovers OAuth from that server. The manifest does not launch a local
  Python process and does not contain a client secret.
- The packaged plugin carries its own end-user slash-command skills. Those are
  plugin assets, not main project workflow skills. Skill directory names start
  with `tinyassets-`.
