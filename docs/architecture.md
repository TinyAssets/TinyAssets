# Architecture

What the system is today, as a map for finding code. Intent and principles live
in [README § Direction](../README.md#direction); why a choice stands lives in
[`docs/decisions/`](decisions/INDEX.md); as-built behaviour lives in
[`openspec/specs/`](../openspec/specs/). This file holds no rationale, history or
status. Update it when a module boundary moves.

## Names

- **TinyAssets** is the platform, site, repository, package and app.
- **Tiny** is the agent persona users talk to.
- **Command center** is a user's own agent harness, project folder and
  workspace. Code and stored data still say **universe**; the rename is
  `openspec/changes/rename-universe-to-command-center/`.
- The graph layer's nouns are `Node`, `Edge`, `State`, `Scope`, `Run` and
  `Trigger`.

## Shape

```text
App (web, Android, iOS, desktop)      Any MCP client (Claude, ChatGPT, ...)
        |  /app/api/* (owner door)              |  /mcp (model door)
        +-------------------+-------------------+
                            |
          https://tinyassets.io  (Cloudflare Worker -> tunnel)
                            |
     daemon container: python -m tinyassets.serve  (one cell, one droplet)
       MCP server + app API + agent loop + control plane + credential broker
                            |
          per-command-center state (SQLite, files, OKF soul bundle)
          provider children jailed to their owner's command center
```

`https://tinyassets.io/mcp` is the only public endpoint (`AGENTS.md` fact 11).
Production is one container image (`Dockerfile`) run by `deploy/compose.yml`
beside `cloudflared` and a log shipper.

## Codemap

### Entry points

| Path | What it is |
|---|---|
| `tinyassets/serve.py` | Container entry point. Keeps spawn children light, then starts the server. |
| `tinyassets/universe_server.py` | The remote MCP server: canonical handles, prompts, `/app` mounting. |
| `tinyassets/engine_mcp_server.py` | The same handles, served locally to the command-center agent's own turn. |
| `tinyassets/mcp_server.py`, `tinyassets/__main__.py` | Local file-interface server and CLI (`tinyassets-mcp`, `tinyassets-cli`). |
| `tinyassets/desktop/` | Desktop launcher and tray; `icon_gen.py` owns the mark geometry. |
| `tinyassets/control_plane/__main__.py` | The control plane's owner tick, runnable on its own. |

### Surfaces

| Path | Owns |
|---|---|
| `tinyassets/api/` | MCP actions, one module per cluster (runs, branches, wiki, status, connections, market, ...). |
| `tinyassets/onboarding/` | The app served at `/app`: sign-in, model connect, chat, requests, notifications. |
| `tinyassets/owner_door/` | The app's complete reads of its owner's data (ADR-011). |
| `WebSite/site-react/` | The public site (Next.js static export). |
| `mobile/`, `desktop-app/` | Capacitor and desktop shells around the app. |
| `packaging/` | MCPB, Claude plugin, registry metadata, OS installers. |

### The agent and its tools

| Path | Owns |
|---|---|
| `tinyassets/agent_loop/` | The thin loop: model turns in the platform, tool calls routed to the box. |
| `tinyassets/universe_tools.py` | The agent's four tools: `read`, `write`, `edit`, `bash`. |
| `tinyassets/ta_cli.py`, `tinyassets/ta_capabilities.py` | The `ta` client the agent runs inside its jail. |
| `tinyassets/persona.py`, `tinyassets/universe_soul.py`, `tinyassets/universe_bundle.py` | Persona, soul and the OKF soul bundle a new command center is seeded with. |
| `tinyassets/skills/`, `tinyassets/starter*` | Bundled skills and the starter package. |

### Graphs and runs

| Path | Owns |
|---|---|
| `tinyassets/graph_compiler.py` | Compiles a `BranchDefinition` into a LangGraph `StateGraph`. |
| `tinyassets/runs.py`, `tinyassets/run_*` | Run storage, events, run files and inputs. |
| `tinyassets/node_sandbox.py`, `tinyassets/sandbox/` | The sandboxed code node and sandbox detection. |
| `tinyassets/scheduler.py`, `tinyassets/automations.py` | Event subscriptions, cron, user-owned automations. |
| `tinyassets/branch_tasks.py`, `tinyassets/branch_tasks_v2.py` | The file-locked branch task queue and its transactional successor. |
| `tinyassets/workspace_*.py` | Workspace pool, provisioning, staging and git for workspace jobs. |
| `tinyassets/effectors/`, `tinyassets/delivery_runtime.py` | External effects and their delivery. |

### Providers and connections

| Path | Owns |
|---|---|
| `tinyassets/providers/router.py` | Serves one command center's owner-authorized provider; no platform fallback. |
| `tinyassets/providers/` | Owner binding, model selection, discovery, wire codecs, `provider_jail.py`. |
| `tinyassets/connection_oauth/` | Generic OAuth 2.0: discovery, PKCE, refresh. |
| `tinyassets/credential_vault.py`, `tinyassets/broker/` | Per-command-center credentials and the broker process that alone holds them. |

### Isolation and the target architecture

| Path | Owns |
|---|---|
| `tinyassets/boxes/` | `BoxProvider`, the only way to touch a command center's files or run its code. |
| `tinyassets/control_plane/` | Lease, triggers, scheduler tick, wake path. |
| `tinyassets/owner_lease.py`, `tinyassets/storage/owner_fence.py` | The execution owner's lease and fence. |

### State

| Path | Owns |
|---|---|
| `tinyassets/storage/` | Bounded-context SQLite stores with a shared `_connect()` and migrations. |
| `tinyassets/daemon_server.py` | Older multiplayer substrate still being split into `storage/`. |
| `tinyassets/memory/`, `tinyassets/retrieval/`, `tinyassets/knowledge/` | Memory, retrieval and the knowledge graph. |
| `tinyassets/wiki/`, `tinyassets/daemon_wiki.py` | Wiki pages and the curated OKF export. |
| `tinyassets/auth/`, `tinyassets/principals.py` | WorkOS AuthKit sessions and principals. |
| `tinyassets/payments/`, `tinyassets/bid/`, `tinyassets/treasury/` | Paid-market primitives, test currency only. |

### Domains

`domains/<name>/` registers through the `tinyassets.domains` entry-point group
in `pyproject.toml`; `tinyassets/discovery.py` finds them. Two exist:
`fantasy_daemon` (a LangGraph authoring domain) and `research_probe`.
`fantasy_daemon/` at the root is its runner. `tinyassets/constraints/` is an
ASP engine over `data/world_rules.lp`.

### Operations

| Path | Owns |
|---|---|
| `deploy/` | Compose bundle, systemd units, watchdog, backups, Cloudflare Worker. |
| `.github/workflows/` | Build, deploy, uptime canary, P0 triage, DR drill, release reconcile. |
| `scripts/` | Canaries, `deployed_sha.py`, invariants, OpenSpec flow, guards. |

## Invariants

These hold everywhere and are checked by tests or scripts. Each names its
decision.

- No platform LLM call; every model call carries one owner's authority
  (ADR-006, `tinyassets/providers/owner_binding.py`).
- No vendor names or code paths outside dev tooling (ADR-007).
- Every execution belongs to one command center and its owner (ADR-009).
- New command centers, nodes and branches are born `private` (ADR-010).
- The owner door imports no bounding module (ADR-011,
  `tests/test_owner_door_import_boundary.py`).
- The public MCP surface is the canonical handle set
  (`scripts/mcp_public_canary.py --assert-handles`; ADR-013).
- Accumulating graph state uses `Annotated[list, operator.add]`.
- Fail loudly: a missing dependency is an error, never a plausible fallback.

Known gaps against these: [`docs/concerns/`](concerns/).
