# Claude Plugin Packaging

This directory contains the TinyAssets plugin marketplace surface plus the
packaged `tinyassets-universe-server` plugin.

## Core

- [Plugin README](plugins/tinyassets-universe-server/README.md)

## Structure

- `.claude-plugin/marketplace.json` - marketplace manifest
- `plugins/tinyassets-universe-server/` - packaged plugin contents

## Notes

- `python packaging/claude-plugin/build_plugin.py --pack` builds the ignored
  runtime and writes `packaging/dist/tinyassets-claude-plugin.zip`. The release
  workflow uploads that archive; the marketplace installs it from the latest release.
- The packaged plugin carries its own end-user slash-command skills. Those are
  plugin assets, not main project workflow skills.
