# TinyAssets deploy — build + run

Artifacts for running the TinyAssets daemon on any Linux host. Per
`openspec/specs/uptime-and-alarms/spec.md`, this
directory holds provider-agnostic deploy material (Row A container
image, Row C tunnel config, future Row D provider-specific wiring).

## Build

```bash
# From repo root
docker build -t tinyassets-daemon .
```

Multi-stage build. Builder stage installs build-essential + rust
toolchain (lancedb native wheel is sometimes source-only). Final stage
is `python:3.11-slim` + runtime-only C deps (`libgomp1`, `tini`,
`ca-certificates`), non-root user `tinyassets:1001`.

First build takes ~5-10 min on a cold cache (rust toolchain + lancedb
wheel compilation dominate). Subsequent builds hit the layer cache
for pyproject.toml changes only.

## Run (local smoke)

```bash
# Create a host-side data dir so state persists across container
# restarts (SQLite checkpoint, LanceDB indexes, universe output).
mkdir -p ./data

docker run --rm \
    -p 8001:8001 \
    -v "$(pwd)/data:/data" \
    -e TINYASSETS_DATA_DIR=/data \
    --name tinyassets-daemon \
    tinyassets-daemon
```

- `-p 8001:8001` — MCP streamable-http binds to `0.0.0.0:8001` by
  default (see `tinyassets.universe_server.main`).
- `-v $(pwd)/data:/data` — host bind-mount for daemon state. Required
  once Row B lands (paths routed through `TINYASSETS_DATA_DIR`); until
  then, best-effort — the daemon may still write to hardcoded host
  paths that won't persist.
- `-e TINYASSETS_DATA_DIR=/data` — anchors all on-disk state to the
  bind-mounted volume.

### Verify MCP initialize

```bash
curl -sS -X POST http://localhost:8001/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1.0"}
        }
    }'
```

Expect an SSE-framed JSON-RPC response with `result.serverInfo.name` =
`"TinyAssets"` and `result.protocolVersion` echoed back. This is the
same shape `scripts/mcp_public_canary.py` exercises.

Or use the repo's canary directly:

```bash
python scripts/mcp_public_canary.py --url http://localhost:8001/mcp --verbose
```

Exit 0 + `[canary] OK http://localhost:8001/mcp` = the image is
healthy end-to-end.

## Image surface (what ships)

- `tinyassets/` — engine + MCP server.
- `domains/` — registered domain skills.
- `pyproject.toml` — dep manifest (installed via `pip install -e .` in the builder stage).
- Virtual env at `/opt/venv` (from builder stage).
- Non-root user `tinyassets` (uid 1001).
- `EXPOSE 8001`, `ENTRYPOINT` via `tini`, `CMD python -m tinyassets.serve` (a launcher for `tinyassets.universe_server:main` whose import is empty, so spawn children stay cheap).

## What does NOT ship

Per `.dockerignore`: `tests/`, `docs/`, `packaging/`, `.claude/`,
`.agents/`, `prototype/`, `.git/`, `output/`, IDE + OS noise, any
`*.db*` state files. Secrets never baked — supply via `-e` or provider
secret injection.

## Provider deploy

Row A (this commit) ships the image. Provider-specific deploy is
Row D (pending Q-uptime-1 answer):

- **Fly.io** — `fly.toml` + `fly deploy` targeting this image.
- **Hetzner CX11** — `docker compose up -d` with this image + a
  compose file that ships in Row D.
- **GoDaddy VPS** — similar compose-based deploy.

Until Row D lands, this image is runnable locally for validation +
for any provider-agnostic test you want to run.

## Data-dir contract

`TINYASSETS_DATA_DIR` is the canonical env var for the on-disk state
root. Resolution order (see `tinyassets.storage.data_dir`):

1. `$TINYASSETS_DATA_DIR` if set + non-empty.
2. Platform default: `%APPDATA%\TinyAssets` on Windows, `~/.tinyassets`
   elsewhere.

All three paths resolve to absolute paths — no CWD-relative drift.
In a container, set `-e TINYASSETS_DATA_DIR=/data` + `-v host:/data`
and every write lands in the bind mount.

See AGENTS.md §Configuration for the full env-var table.

## Health check (containerized)

Inside the container, the same canary works:

```bash
docker exec tinyassets-daemon /usr/local/libexec/ta-op canary
```

Exit 0 = container-local MCP reachable. Use this as a liveness probe
in whichever orchestrator deploys the image.

## Stopping

```bash
docker stop tinyassets-daemon
# SIGTERM → tini → uvicorn graceful shutdown → exit 0
```

`tini` ensures signal forwarding works; without it `docker stop` has
to `SIGKILL` after the grace window because Python doesn't handle
PID-1 signal semantics natively.
