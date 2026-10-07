# Repo setup — Phase 7.2

Minimum git + tooling config for a new contributor cloning TinyAssets.
Phase 7.2 treats the repo itself as the canonical shared state —
`branches/`, `goals/`, and `nodes/` YAML are the public catalog; the
SQLite DB is a local cache derived from that YAML.

## Git config

Line endings are pinned to LF by `.gitattributes`. On Windows, set
your client default to match so fresh clones don't show "modified"
files immediately:

```bash
git config --global core.autocrlf input
```

Set a commit identity once per machine:

```bash
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

The action ledger attributes ownership by author field, and downstream
tools match against commit metadata — keep the email consistent across
providers / machines.

## Prerequisites

- Python 3.11+ (AGENTS.md fact 7).
- `uv` or `pip`. The MCPB bundle uses `uv` in production.
- `gh` (GitHub CLI) if you want to open PRs from the checkout. Not
  installed on every developer host — `winget install GitHub.cli` on
  Windows, or skip it and use the web UI.

## Clone + install

```bash
git clone https://github.com/TinyAssets/TinyAssets
cd TinyAssets
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -e .
```

## First run — rebuild local cache

The SQLite cache (`output/` directory, `*.db` files) is NOT committed
— it rebuilds from YAML on first run. The TinyAssets daemon reads both
SQLite and YAML transparently; normal tool use (`extensions
action=build_branch`, `goals action=propose`, etc.) warms the cache
as a side-effect of writes.

An automated `rebuild-index` helper is Phase 7.2 G3 (see STATUS.md).
Until then, a cold clone with no cache will work — the first write
action populates what's needed.

## Run the TinyAssets MCP server locally

```bash
tinyassets-universe-server
```

MCP listens on `http://localhost:8001/mcp`. Connect any MCP-compatible
client to that URL. Other entry points in `pyproject.toml`
`[project.scripts]`: `tinyassets-cli`, `workflow` (GUI tray).

## Contributing back

To contribute a branch / goal / node:

1. Use the MCP tools as normal — SqliteCachedBackend writes YAML
   alongside SQLite.
2. `git add branches/your-slug.yaml` (or `goals/` / `nodes/`).
3. Open a PR via `gh pr create` or the web UI.

Do not commit `output/` or `*.db` files; `.gitignore` excludes them.

## Troubleshooting

- **"LF will be replaced by CRLF" warnings:** run `git config --global
  core.autocrlf input`. `.gitattributes` alone isn't enough on Windows.
- **SQLite lock errors:** one writer per DB.
  Don't run two TinyAssets MCP server processes against the same `output/` dir.
- **Empty `branches/` / `goals/` / `nodes/`:** expected on a fresh
  repo today; the catalog fills up as contributors land branches.
