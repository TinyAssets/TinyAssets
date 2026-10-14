# Architecture

What the system is today, as a map for finding code. Intent and principles live
in [README § Direction](../README.md#direction); why a choice stands lives in
[`docs/decisions/`](decisions/INDEX.md); as-built behaviour lives in
[`openspec/specs/`](../openspec/specs/). This file holds no rationale or
history. The one status it holds is the [Shape registry](#shape-registry). Update
it when a module boundary moves.

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
App (web, Android, iOS, desktop)   Any MCP client (Claude, ...)   Scripts, agents
        |  /app/api/* (owner door)          |  /mcp (model door)         |  /api (REST, API key)
        +-------------------+---------------+----------------------------+
                            |
          https://tinyassets.io  (Cloudflare Worker -> tunnel)
                            |
     daemon container: python -m tinyassets.serve  (one cell, one droplet)
       MCP server + app API + agent loop + control plane + credential broker
                            |
          per-command-center state (SQLite, files, OKF soul bundle)
          provider children jailed to their owner's command center
```

`https://tinyassets.io/mcp` (MCP) and `https://tinyassets.io/api` (REST,
API keys; `tinyassets/rest_api.py`, keys in `tinyassets/api_keys.py`) are the
public endpoints (`AGENTS.md` fact 11).
Production is one container image (`Dockerfile`) run by `deploy/compose.yml`
beside `cloudflared` and a log shipper.

## Codemap

### Entry points

| Path | What it is |
|---|---|
| `tinyassets/serve.py` | Container entry point. Keeps spawn children light, then starts the server. |
| `tinyassets/universe_server.py` | The remote MCP server: canonical handles, prompts, `/app` mounting. |
| `tinyassets/engine_mcp_server.py` | The same handles, served locally to the command-center agent's own turn. |
| `tinyassets/__main__.py` | The domain CLI (`tinyassets-cli`). |
| `tinyassets/desktop/` | The mark geometry, plus dashboard and tray helpers still imported by the shipped domain CLI. |
| `tinyassets/control_plane/__main__.py` | The control plane's owner tick, runnable on its own. |

### Surfaces

| Path | Owns |
|---|---|
| `tinyassets/api/` | MCP actions, one module per cluster (runs, branches, wiki, status, connections, market, ...). |
| `tinyassets/onboarding/` | The app served at `/app`: sign-in, model connect, chat, requests, notifications. |
| `tinyassets/owner_door/` | The app's complete reads of its owner's data (ADR-011). |
| `WebSite/site-react/` | The public site (Next.js static export). |
| `mobile/`, `desktop-app/` | Capacitor and desktop shells around the app. |
| `packaging/` | MCPB, Claude plugin and registry metadata. |

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
| `tinyassets/control_plane/tick.py`, `tinyassets/control_plane/events.py`, `tinyassets/automations.py` | One owner tick, event subscriptions, cron and user-owned automations. |
| `tinyassets/control_plane/agent_wakes.py`, `tinyassets/control_plane/continuations.py` | Saved agent follow-ups and protected request continuations on the owner tick. |
| `tinyassets/branch_tasks_v2.py` | The transactional v2 request queue adapter. |
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

`domains/<name>/` contains optional authoring packages discovered from files
by `tinyassets/discovery.py`. The packaged `fantasy_daemon` and
`research_probe` examples have no CLI entry points. `tinyassets/constraints/`
is an ASP engine over `data/world_rules.lp`.

### Operations

| Path | Owns |
|---|---|
| `deploy/` | Compose bundle, systemd units, watchdog, backups, Cloudflare Worker. |
| `.github/workflows/` | Build, deploy, uptime canary, P0 triage, DR drill, release reconcile. |
| `scripts/` | Canaries, `deployed_sha.py`, invariants, OpenSpec flow, guards. |

## Shape registry

One row per declared shape: the single path it names, what still runs beside
it, and the guard that fails CI when a second path appears or the old one
survives its cutover. Measured on `origin/main` `358e8c7c5ec`, 2026-10-11.
**converged** = one path; **drifted** = the old path is still live, often
behind a flag; **unbuilt** = declared, not built; **unverified** = not
measured. A named test is a guard that runs today; a *planned* id becomes a
structural guard in convergence item C1. A drifted row's guard is a ceiling
that only falls; at zero the row reads converged and the guard forbids
regrowth. **Removed by** names the item in the convergence plan
(`openspec/changes/shape-convergence/`) that deletes the second path; for an
N row (founder-approved primitive) it names where the build slots in. Item 10
of #4624 (broker credentials and browser sign-in) is S28.

| # | Shape (source) | The one path | Second path still live (lines) | Status | Guard | Removed by |
|---|---|---|---|---|---|---|
| B1 | The bubble agent is a product that tracks the frontier (Dir 1) | the starter package: files, skills and packages a user could also write; kept matching and beating Grok Bot, Muse, ChatGPT dots and Claude by the scheduled frontier review | bubble-agent behaviour built as platform code (wake, approval and domain features, provider special cases; see S9, S18) | drifted | starter-only change rule: frontier items edit the starter package, never platform code, unless a missing primitive is approved | frontier review loop; C5, C10 |
| B2 | The harness is pi.dev-modelled primitives (Dir 1-3) | four tools + `ta`, one model call (S1), one way to start an agent (S2), one loop (S3), one extension unit (S6), packages, a customizable interface | everything in the drifted rows below | drifted | the guards below | C1-C11 |
| S1 | One model call (Dir 2, ADR-006/007, D6) | `model_call.call(model_ref, context, parent)` -> broker; receipt per call | MCP-sampling reviewer and one-provider authority remain; caller budget roots and the in-memory allocation path are removed | drifted | `check_shapes.py` S1, `test_broker_usage_ipc` | C3 |
| S2 | One way to start an agent (Dir 2, ADR-014) | `control_plane/session.py` `start(agent_ref, input, parent)`: chat (and the wakes and owner answers that are chat), agent nodes, queued runs and `ta agent run` call it; it resolves the agent from its files once (C8) | the executors it drives are still two (`universe_intelligence._call_writer` for the served writer turn, `WorkflowAgentTurn` for run steps) with their own admission gates; the background served and cloud continuation authority paths remain live, and the shipped domain CLI (`fantasy_daemon` runner) still starts separately (C5 kept them for their live callers) | drifted | `test_spawn_entry` (spawn-entry = 1), `check_shapes.py` S2 | C3, C5, C7, C9 |
| S3 | One loop (D6, ADR-012) | `agent_loop/` thin loop (1.5k) | native CLI loop `claude_provider` 1335; thin loop dark behind `TINYASSETS_AGENT_LOOP` | drifted | S3 old-path and line ceilings in `check_shapes.py` | C9 |
| S4 | No provider-specific code (Dir 2, ADR-007) | connection primitives; vendors are data | 145 files name a vendor; three counted legacy cells remain: Claude provider, `FALLBACK_CHAINS`, `openai_device.py`; CLI installs in `Dockerfile` | drifted | `vendor-names` file ceiling in `check_shapes.py` | C3, C9 |
| S5 | One agent definition (Dir 2, ADR-014) | `agents/<name>/` files, read by `agent_definition.py` (instructions, settings, identity) | none: the SQL agent store, interchange, templates, platform review, `harness_settings.py` and the `agent_runtime_manifest` kind were deleted by C8; `commons_catalog.py` keeps the published-system catalogue and the binding rows (serving provider, conversation design), neither an agent | converged | `check_shapes.py` S5, `test_one_agent_definition`, `test_agent_files` | C8 (done) |
| S6 | One extension unit (Dir 2) | v2 activated revisions (`extension_manifest.py`) | v1 workspace executables (`ta_cli.py:54-105`); `api/extensions.py` 1108; 6 changes it superseded still open | drifted | `test_one_extension_unit` (move in) | C10 |
| S7 | Four tools + `ta` (Dir 2) | `universe_tools.py`, `ta_cli.py` | second handle server `engine_mcp_server.py` 5031 (15 tools) | converged for the model; drifted behind it | `test_served_tool_guidance`, `test_converse_turn_cost` | C10 |
| S8 | One box per owner (ADR-019, Dir 5) | `BoxProvider` (`boxes/`, 1.8k), attached once at admission | 33 `role_*` cells (7.9k), all added after ADR-012; `BoxProvider` has no production caller; `target-architecture` 0/12; 17 cell fixes, six (#4576-#4581) the day after the cutover | drifted | `test_universe_path_io_guard`; `role-cells` (planned) | C6 |
| S9 | Control plane is the only always-on layer (ADR-012) | `control_plane/` | One leased owner tick handles schedules, source events, automations, activities and protected wakes; the v1 scheduler, dispatcher, producers and continuation loops are removed | converged | `test_control_plane_inventory` | C7 |
| S10 | Platform state outside boxes (ADR-012) | `storage/` stores | `daemon_server.py` 5261, 27 `CREATE TABLE` | drifted | `test_storage_registry(_complete)`, `test_owner_stores` | C6 |
| S11 | Owner door complete, model door bounded (ADR-011) | `owner_door/`, `/app/api/*` | none | converged | `test_owner_door_import_boundary`, `test_app_reads_use_owner_door` | — |
| S12 | Canonical public handles (ADR-013) | 7 handles in `universe_server.py` | none | converged | `mcp_public_canary.py --assert-handles`, `test_mcp_instruction_surfaces`, `host-paths` | C5 |
| S13 | No platform LLM (ADR-006) | owner-bound calls only | platform helpers (`reflexion`, `raptor`, `agentic_search`, `extract_learning`) on `call_provider`; whether each carries owner authority is not checked | unverified | `test_platform_has_no_llm` (move in) | C3 |
| S14 | Identity follows the account; private by default (ADR-016, ADR-010) | `_current_actor` raises without a subject; every creation path writes `private` | none | converged | `test_private_by_default` (move in) | — |
| S15 | Cloud only, zero hosts (ADR-008, Dir 8) | the hosted cell | The root fantasy runner and its CLI entry are removed; the served owner process is the start surface | converged | `host-paths` | C5 |
| S16 | One app, thin shells (Dir 1, 7) | `onboarding/` at `/app`; `mobile/`, `desktop-app/` load it | none for chat; the tray host is S15 | converged | `test_onboarding_app` routes | — |
| S17 | Knowledge is files (ADR-017); export (ADR-018) | OKF bundle canonical, indexes derived; one export action | `memory/`, `retrieval/`, `knowledge/`, `daemon_brain.py` (10.7k) canonical; bundle is seed-only; `okf_export.py` unwired | unbuilt | none yet | C12 |
| S18 | Abilities are files, no pre-built features (Dir 3) | starter package, skills | fantasy and research_probe domains remain installed entry points; `api/market.py` goals/escrow, `bid/`, `treasury/` | drifted | `domain-entry-points` | C5, C10 |
| S19 | Built artifacts are not committed | Hosted Claude Code plugin uses the repository folder; `.mcpb` stages its own runtime at pack time | none | converged | `check_mirror_parity.py` rejects tracked plugin runtime files | C4 |
| S20 | One home per fact (ADR-005, Dir 6) | README Direction, AGENTS.md, this file, ADRs, `openspec/specs`, `openspec/changes`, `docs/concerns`, `docs/reference` | none; C1 removed retired document trees and root clutter, retaining cited incident repros | converged | `doc-homes`: no new file outside a home | C1 |
| S21 | One queue (AGENTS.md) | `openspec/changes/`, reconciled into one tracking issue | none; C2 archived completed changes and folded superseded lanes | converged | `superseded-open`; `shape-reconcile.yml` | C2 |
| S22 | Command center naming | `command_center` in code and storage | 10 `universe_*` modules outside the two target homes, ~13k mentions | drifted | S22 ceiling on `universe_*` modules + no new `*Universe*` definitions; mentions renamed as each slice touches a file | each slice |
| S23 | Accumulating state; fail loudly (Facts 5, 8) | `Annotated[list, operator.add]`; a missing dependency is an error | the S1 mocks | converged except S1 | `test_capability_health` | C3 |
| S24 | One durable send (commit before publish) | an owner message is stored once, then dispatched | send paths not measured; 11 lost or duplicate-send fixes (#4290 ... #4615, #4614 open) | unverified | `send-path` (planned) | C11 |
| S25 | Three public doors (Fact 11; founder 2026-10-11) | `https://tinyassets.io/mcp` for MCP clients, `/api` for keyed programmatic agents and `/hooks/inbound` for external events (N1), same handles and authority; `/app` for the app | duplicate REST PR #4589 closed; #4602 is the one REST PR; `/hooks/inbound` unbuilt | converged once #4602 lands; inbound door unbuilt | `public-routes`: `/mcp`, `/api`, `/hooks/inbound`, `/app`, root, and connector `/.well-known/*` discovery | — |
| S26 | Exactly two account limits (seats, storage) | `usage_policy.py` | five open limit changes; cleanup #4134 (1/3), #4136 (2/3), no 3/3 | drifted | `limit-names` (planned) | C2 fold, C7 |
| S27 | Zero hosts online, recoverable (Dir 8, ADR-012) | off-region backup, key escrow, standby | Litestream deferred (#4284); escrow and backup PRs #4279, #4283, #4332, #4333 closed | unbuilt | DR drill workflow | C6 |
| S28 | One credential custody (ADR-012 D12/S6, ADR-007) | broker-only key hierarchy: AWS KMS root behind a key seam -> account KEK -> command-center DEK -> sealed records and browser sessions; delegations only, never site passwords | cleartext `.credential-vault.json` (`credential_vault.py` 2575) read by the broker through the vault group and tarred into offsite backups; separate raw per-owner browser keys (`broker/browser_vault.py`) | drifted | `credential-custody` (planned) | `credential-vault-reshape` |
| N1 | One inbound door (Slack, Teams, WhatsApp, the owner's email via their connected account (no platform mail domain; founder 2026-10-11), webhooks) (founder 2026-10-11, frontier baseline #4624) | `/hooks/inbound` turns each verified external event into a `session.start` input under the owner's grants; channels are connector packages | inbound-webhook trigger changes (folded into wake-triggers by C2) | unbuilt | `inbound-door` (planned): one ingress route, every event lands as `session.start` | after C7, C8 (S2, S9, S25) |
| N2 | One push notification primitive (phone delivery) (founder 2026-10-11, frontier baseline #4624) | stored owner messages (S24) delivered to phones by one notification primitive the starter package and wakes call | none | unbuilt | `notify-path` (planned): one push sender | after C7, C11 (S9, S24) |
| N3 | Realtime two-way voice and video (founder 2026-10-11, frontier baseline #4624) | a streaming mode of `model_call` and sessions over a connected source, not a second loop | none | unbuilt | `call-sites` and `opt-in-flags` cover it: no realtime loop beside S3 | after C3, C9 (S1, S3, `broker-streaming-contract`) |
| N4 | Live takeover view of the box's browser and desktop (founder 2026-10-11, frontier baseline #4624) | a view of the owner's box exposed through the interface package, for passwords, 2FA and CAPTCHAs | none | unbuilt | `box-view` (planned): the view reads only its owner's box | after C6, C11 (S8, interface package) |
| N5 | Owner-set network egress gate (founder 2026-10-11, frontier baseline #4624) | owner policy (allowlist or ask-before-internet) enforced at the box boundary, not in the agent | none | unbuilt | `egress-gate` (planned): box egress passes the owner policy | after C6 (S8) |
| N6 | Undo by box snapshot and restore (founder 2026-10-11, frontier baseline #4624) | box filesystem snapshot before agent actions, restore on request; one snapshot path shared with S27 backup | none | unbuilt | `box-snapshot` (planned): one snapshot path | after C6 (S8, S27) |
| N7 | Money: agent purchases with spending caps (founder 2026-10-11, frontier baseline #4624) | spending through the broker (S28) under owner caps and approvals, with receipts; spec first (money, outward-facing) | `api/market.py`, `bid/`, `treasury/` (S18) | unbuilt | `spend-path` (planned): one spender, caps enforced | after C3, C10 and `credential-vault-reshape` (S1, S18, S28) |
| N8 | Optional link to the user's personal computer (Dir 8: optional) (founder 2026-10-11, frontier baseline #4624) | an optional external box connector the owner adds; every hosted surface works without it | tray host `desktop/` (S15) | unbuilt | `host-paths` stays green: nothing hosted requires the link | after C5, C6 (S8, S15) |
| N9 | `model_call` returns media (images, audio, video) (founder 2026-10-11, frontier baseline #4624) | `model_call` outputs typed media parts with a receipt per call | none | unbuilt | `call-sites` plus a media-receipt test (planned) | after C3 (S1) |
| N10 | Owner-only policy layer (ADR-019, frontier baseline v2 #4624) | one broker-private account policy; protected owner-session writes; read-only agent projection; every broker decision receipted | P5 box-boundary gate and P6 legacy source retirement remain | drifted | `policy-writers` (`test_policy_writers`): no box or agent path writes policy | C6 P1, P5, P6 |
| N11 | One user-built app (founder 2026-10-12) | `tinyassets/app_object.py`: the command center's id, manifest and default screen; the launch list is `GET /app/apps` inside the existing `/app` shell (S16) | none | converged | `test_app_object`; `app_manifest` outside that module is a second path | — |

Findings against a row go in [`docs/concerns/`](concerns/); a new shape is a
new row with its guard in the same PR.
