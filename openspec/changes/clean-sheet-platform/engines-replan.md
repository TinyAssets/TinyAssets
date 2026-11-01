# TinyAssets re-plan: agents run on unmodified engines (2026-11-01)

The founder approved this decision on 2026-11-01: **pi stops being the platform's engine.**
This document turns it into an inventory, a runtime, cost and latency checks, risks and a
slice order. The decision is recorded in ADR-034.

## How to read this

- **[E]** means evidence: a primary source or measurement I read or ran, with its source.
- **[I]** means my inference. It is never a fact.
- **[R]** means another research note read it: `main-agents.md` or `subscription-engines.md`
  in this directory, or a platform research note cited by name (the agent, files and gateway
  notes and the G4 review, which are not in the repo). I did not re-read the primary source
  myself, and the note's own label applies.

**What I read:**
- `AGENTS.md`/`CLAUDE.md`, `README.md` § Direction.
- ADRs: ADR-005, ADR-028, ADR-031, ADR-032, and ADR-033 (then on `origin/platform-adr031`; merged as #5384).
- `openspec/changes/clean-sheet-platform/`: `proposal.md`, `tasks.md`, the three specs and
  `e2b-resume-measurement.md`. There is no `design.md`.
- The research notes `main-agents.md` and `subscription-engines.md` (this directory), and the
  platform research notes on agents, files and the gateway.
- The code on `origin/main` (`40c336b4c`), `origin/platform-g4` (`b4ef07f5a`, plus an unpushed
  work-in-progress commit `6f55edb58`), and `origin/platform-a6` (`df0e2ac17`).

**The plan of record:** I read it with the Claude Docs `read` tool. It was rev 77, the
current revision, and I read all of it (137 top-level blocks plus the architecture
diagram's source).

**Web pages fetched from the research environment, 2026-11-01:**
- **Read:**
  - e2b.dev/pricing
  - docs.e2b.dev billing, `sandbox/persistence.md` and `commands/background.md`
  - code.claude.com/docs/en/headless (`.md`)
  - developers.cloudflare.com/durable-objects/platform/pricing (`index.md`)
- **Sources the research could not reach on 2026-11-01:**
  - **developers.openai.com:** it redirects to `learn.chatgpt.com`, where the proxy answered
    `CONNECT 403`.
  - **github.com/anthropics/claude-code/issues/106037:** the proxy refused GitHub access to
    that repository. Its facts below are [R] from
    `main-agents.md`, read 2026-11-01.

**CLI startup timings:** I measured these myself in the research environment, 2026-11-01 (4 vCPU, 16 GB). See
§3.4 and its caveat.

---

## 1. Inventory: every module under `platform/*/src`

Counts are lines on `origin/main` (`wc -l`). Every verdict assumes the new design in §2.

**Verdicts:**
- **Keep:** stays as built.
- **Change:** stays, but is edited.
- **Delete:** goes, in the deletion slice (§5, slice D).

### 1.1 `platform/owner/src` (22,521 lines in 41 `.ts` files, plus `model-ranking.json`)

| File | Lines | Verdict | One-line reason |
|---|---|---|---|
| `admission.ts` | 154 | Keep | Admission and door classes are engine-independent. Only the `ThreadHost.submit` mention in comments goes. |
| `agent.ts` | 1,608 | **Delete** | The in-object pi loop (`OwnerAgent`): pi Sessions, extensions, settings and `conversationCreated`. |
| `authority.ts` | 414 | Change | Keep the stamp and standing-grant core (about 120 lines) as a run's authority. Delete pi's `ta.authority` document, `MODEL_TOOLS`, the five-tool surface and the per-tool capabilities (about 290 lines). |
| `auto.ts` | 170 | Change | "Strongest connected source" (Direction 4) moves from per request to per run: rank sources that have an engine. Delete the transcript stickiness and the pi compaction thresholds. |
| `codemode.ts` | 87 | Change | Code mode leaves pi: one MCP-door executor over pi-codemode's `RemoteCodemodeSandbox`, whose tools are the door's own tools. Drop `createCodemodeExtension` (pi-durable). |
| `collections.ts` | 414 | Keep | Board and rooms on the one revision primitive. |
| `connections.ts` | 1,023 | Keep | Sealed credentials, connect flows and the model list (egress prices calls by it). |
| `cron.ts` | 188 | Keep | Recurrence for scheduled runs. |
| `delegate.ts` | 108 | **Delete** | `agent:delegate`. Subagents are the engine's own. |
| `erase.ts` | 177 | Change | Drop the thread-store destroy step. Add the sandbox kill and the session-file purge. |
| `erasure.ts` | 84 | Change | Same as `erase.ts`: the order's step 2 becomes "kill the sandbox, end every run". |
| `errors.ts` | 9 | Keep | |
| `files-env.ts` | 1,092 | **Delete** | pi's `ExecutionEnv` over the manifest. The engine's file tools act on the sandbox disk, and the doors use `files.ts` directly. |
| `files.ts` | 1,951 | Change (small) | Manifest, lease and sync stay. A sync-out now refuses any path matching a source record's `secret_paths`, and keeps `session_paths`. |
| `gate.ts` | 129 | Keep | The one write gate. |
| `gateway-provider.ts` | 509 | **Delete** | The gateway provider's model calls (pi-ai custom provider, envelope, reserve, dispatch). |
| `grants.ts` | 474 | Change (small) | Add the run-scoped grant kind (§2.4). Capabilities stay. |
| `hooks.ts` | 591 | **Delete** | The pi-specific remote hook wrappers on `pkg:<name>` extensions. User hooks become the engine's own, in the sandbox. |
| `index.ts` | 8 | Change | Stop exporting `ThreadStore`. |
| `ledger.ts` | 1,345 | Keep | Mailbox, receipts and the agent registry. A delivery method for "start this agent's run" may be added in slice R. |
| `live-tee.ts` | 118 | **Delete** | The pi 1.4 partials tee. Live output is now the CLI's own event stream (§2.2). |
| `manifest.ts` | 180 | Keep | |
| `model-ranking.json` | 26 | Keep | Ranking data for `auto`. |
| `notices.ts` | 71 | Change (small) | The source of notices changes from pi `onReport` to run failures (sign-in expired, vendor `rate_limit` or `billing_error`, sync refused). |
| `origin.ts` | 438 | **Delete** | Per-model-call turn identity on pi telemetry (`pi-telemetry`). A run is now the unit. |
| `owner-do.ts` | 2,369 | Change (large) | Doors stay. Delete the agent factory and the pi wiring, about 450 lines across the constructor and `agentSubmit` through `#agentConfig`. Re-point `agentSubmit`, `agentRun`, `agentStatus` and `agentCancel` to runs. Add the run-events socket and the `SANDBOX` binding. |
| `paths.ts` | 123 | Change (small) | `OWNER_TREES` keeps `/workspace/agents`. Add the per-source session tree. `/workspace/.agents` stays owner-only (it holds skills). |
| `policy.ts` | 943 | Change | Delete the pi `settings` and package model routing, and the `hooks` table's `timeout_ms` and `cpu_ms` (user-hook limits). Keep `hooks.codemode` as a code-mode switch. Add run caps (§2.5). |
| `registrations.ts` | 192 | Keep | |
| `remote-storage.ts` | 301 | **Delete** | pi's `Storage` over a `ThreadStore` by RPC. |
| `resources.ts` | 1,022 | **Delete** | The skills section (`formatSkillsForPrompt`), prompt templates, `/skill:` expansion and declarative pi packages. Every CLI discovers `SKILL.md` folders itself. |
| `schedule.ts` | 209 | Keep | Lifecycle lanes. Add a `runs` lane (scheduled and due runs) in slice R. |
| `scheduled.ts` | 227 | **Delete** | `ta.scheduled` as a pi sleeping task. Scheduled runs return as rows on a lane. |
| `schema.ts` | 1,480 | Change | Remove the pi tables (`agent_threads*`, `agent_live`, `agent_usage*`, `model_turns`, `model_turn_owners`, `scheduled_work` remnants) and add `runs`, `run_grants` and `conversations`. Nothing is deployed, so the migrations are rewritten in place (tasks 4: append-only "from the first deploy"). |
| `sessions.ts` | 160 | Keep | |
| `ta.ts` | 733 | **Delete** | `ta` as a pi tool: catalog, BM25, `executeTool`, `agent:run`, `model`. Its platform capabilities become MCP tools. |
| `text.ts` | 46 | Keep | |
| `thread-host.ts` | 1,265 | **Delete** | `ThreadHost`, the multi-Session pi host on Lifecycle. |
| `thread-store.ts` | 299 | **Delete** | `ThreadStore`, the pi store class (the `PISTORE` binding). |
| `threads.ts` | 1,157 | **Delete** | Thread rows that name pi stores, the drop path and the retention lane. A much smaller `runs.ts`/`conversations.ts` replaces it (slice R), and the idle-retention rule ports. |
| `usage.ts` | 523 | Change | Keep the reservation state machine (`reserved → dispatched → succeeded/failed/unknown`). Re-key it from pi turns to **runs**, and enforce per-run and per-source caps at the egress door (§2.5). |
| `wakes.ts` | 130 | Keep | The `wakes` lane. The `WakeSender` doorbells are still owed (tasks 7). |

### 1.2 `platform/doors/src` (2,965 lines in 19 files)

| File | Lines | Verdict | Reason |
|---|---|---|---|
| `api-keys.ts` | 186 | Keep | ADR-028 keys for main agents (Muse). |
| `api.ts` | 59 | Keep | |
| `app.ts` | 183 | Change | Add the owner app's run-event socket. Salvage A6's ticket and binder upgrade (§1.6). |
| `authorize.ts` | 277 | Keep | |
| `binder.ts` | 255 | Change (small) | A6's upgrade forward via `stub.fetch`. With G4: the egress host's run-scoped MCP and the runner socket. |
| `body.ts` | 59 | Keep | |
| `config.ts` | 157 | Keep | |
| `connect.ts` | 408 | Keep | |
| `control.ts` | 52 | Keep | Add a per-source kill switch (§4.2). |
| `directory.ts` | 126 | Keep | |
| `doors.ts` | 102 | Keep | |
| `index.ts` | 15 | Keep | |
| `mcp.ts` | 65 | Change (small) | Tool profiles: directory-listed, custom connector, and run-scoped (code mode only where allowed; §2.7). |
| `oauth.ts` | 80 | Keep | |
| `revoke.ts` | 92 | Keep | |
| `session.ts` | 134 | Keep | |
| `sources.ts` | 422 | Change | `CliSpec` gains an `engine` section (§2.1, the fields in `subscription-engines.md` §10). Add `secret_paths`, `session_paths`, the unattended-use citation (ADR-033), non-npm installs, and which API-key sources each engine can use. |
| `tools.ts` | 249 | Change | `converse` and `run_graph` start runs. Add a `code` tool (code mode) and compact digest tools for main agents (§2.7). |
| `upstream.ts` | 44 | Keep | |

### 1.3 `platform/gateway/src` (2,535 lines in 12 files)

| File | Lines | Verdict | Reason |
|---|---|---|---|
| `catalog.ts` | 110 | Keep | pi-ai is a provider library here, not an engine. `verify` and the egress meter use its adapters and catalog. |
| `credential.ts` | 413 | Keep | Sealed endpoints. G4 adds `egressTarget`. |
| `egress.ts` | 45 | Keep | The fetch interposer. `oauth.ts` and `forward.ts` use `nativeFetch` and `within`. |
| `encode.ts` | 97 | **Delete** | pi-messages wire encoding, used only by `messages.ts`. |
| `envelope.ts` | 128 | **Delete** | The loop's model-call envelope. |
| `failure.ts` | 135 | Change (small) | Drop the pi-only overflow mapping (`modelRefusalCode` and its `context_length_exceeded` rule) once `messages.ts` goes. Keep the fixed codes. |
| `forward.ts` | 260 | Keep | Raw egress passthrough with the credential swap. It is the API-key engine's only model path. |
| `index.ts` | 144 | Change | Drop `fetch` `POST /v1/messages`. Keep `seal`, `exchange`, `refresh`, `revoke`, `verify` and `forward`. |
| `keyring.ts` | 250 | Keep | |
| `messages.ts` | 388 | **Delete** | The loop's model calls in pi-messages. |
| `model-record.ts` | 197 | Keep | Model records price the egress reservation. |
| `oauth.ts` | 368 | Keep | Covers RFC 6749 sources (OpenRouter). The Sign in with ChatGPT `refresh_with: provider` path is parked with `chatgpt.json` (§1.5). |

### 1.4 `platform/hooks/src` and `platform/files/src`

| File | Lines | Verdict | Reason |
|---|---|---|---|
| `hooks/src/codemode.ts` | 25 | **Keep** | pi-codemode's `CodemodeSandboxDurableObject`: the VM for MCP-door code mode, still off the owner's object. |
| `hooks/src/index.ts` | 64 | Change | Keep `codemodeExecute`. Drop `run` (user hooks). The Worker could be renamed `tinyassets-codemode`. |
| `hooks/src/protocol.ts` | 165 | **Delete** | The hook request and reply protocol. |
| `hooks/src/runner.ts` | 421 | **Delete** | `HookRunner`: owners' hook bundles in the Worker Loader. Hooks are now engine files run in the sandbox. |
| `files/src/index.ts` | 178 | Keep | The byte pipe. |
| `files/src/keys.ts` | 44 | Keep | |

**What of hooks stays:**
- The separately deployed Worker, now as **code mode only**: its `CodemodeSandbox` class, its config guard and its isolation guard.
- pi-codemode's Cloudflare sandbox runs scripts in QuickJS compiled to Wasm (`quickjs-wasi/quickjs.wasm` in pi-codemode 1.4.0 `dist/cloudflare.js` [E]), not in a Dynamic Worker. So with `HookRunner` gone, the `worker_loaders` binding and the Dynamic Workers charge ($0.002 per owner per active day) go too [I].
- The owner side's code-mode executor (`codemode.ts`, changed).
- H2's live probe, narrowed to the code-mode VM's CPU budget and its blocking isolation.

**What goes:**
- The Worker Loader bundle path, the SES-style prelude, and memos keyed by hook point.
- The `hooks` policy's `timeout_ms` and `cpu_ms`.
- The per-package isolation question (founder's later call).

### 1.5 `platform/sources/*.json` (600 lines in 10 files)

**Engines (`connect: ["cli"]`):** `claude-code.json` (89), `codex.json` (107), `gemini.json` (82) and `kimi.json` (82). All four **change**:
- Today's `invoke` is a one-shot inference call with every tool and MCP server disabled (`--tools ""`, `features.*=false`, `mcp_servers={}`). An engine needs the opposite.
- Add `engine.argv` and `resume_argv`, event matchers, instruction and skills paths, MCP templates, unattended flags, `home_env`, `secret_paths`, `session_paths` and the ADR-033 citation.
- Claude needs `platform.claude.com` in `hosts.provider`. Per `subscription-engines.md` §1, refresh goes there [R].

**API-key sources:** `cerebras`, `google_ai_studio`, `groq` and `mistral` (36 each) and `openrouter_user_models_v1` (47) **keep**:
- Each gains `engines`: which engines can use it through the egress door's base URL. Examples: pi's CLI takes all of them; Codex takes OpenAI-compatible ones [I].

**`chatgpt.json` (49): park.** It is Sign in with ChatGPT for *our* loop, and that loop is gone. ADR-031 D6 already holds it behind OpenAI's approval. Codex signs in through its own flow in the sandbox.

**Missing:**
- There is **no `anthropic` or `openai` API-key source.** Claude Code and Codex on an API key need them (slice E).
- There is no record for pi's own CLI (`@earendil-works/pi-coding-agent`; its `--mode json`, `--session` and `--resume` are documented in its 1.4.0 `docs/cli.md` [E]).

### 1.6 Work in flight on branches

**`origin/platform-g4` (slice 8a, review REQUEST_CHANGES): keep and finish.**
- Files on the branch: `owner/src/egress.ts` (382), `owner/src/network.ts` (295), `doors/src/egress.ts` (221) and `doors/src/sandbox.ts` (99). The unpushed work-in-progress commit adds `gateway/src/meter.ts` (227) and grows `owner/src/egress.ts` to 487.
- This is the API-key engine's whole model path. It needs one conceptual change: its reservation keys on "the lease's turn", which becomes the lease's **run** (slice R).

**`origin/platform-a6` (in review, not merged): do not merge as is. Salvage, then close.**
- `sockets.ts` (751) and `socket-shape.ts` (64) attach sockets to pi's `Conversation.watch()`. The conversation doors (reset, compact, fork, withdraw and the task graph) are pi operations. The branch also grows `live-tee.ts` and `thread-host.ts`. All of this is deleted.
- Salvage:
  - the socket ticket bound to its grant, its expiry and sign-out (`doors/src/app.ts` +100, `binder.ts` +62, `socket.test.ts` 246);
  - the `stub.fetch` upgrade path, which A6 chose because RPC cannot carry a WebSocket [R: `agent-research.md` §9 item 7];
  - the per-owner and per-thread socket caps;
  - the memory-bound guard idea.

**`origin/platform-adr031` (ADR-033, PR #5384): merge first.** ADR-034 builds on it.

### 1.7 The deletes, grouped (source + tests, `origin/main`)

| Group | Source deleted | Tests and guards deleted |
|---|---|---|
| In-object pi loop | `agent.ts` 1,608 · `files-env.ts` 1,092 · `ta.ts` 733 · `authority.ts` pi part ≈290 · `owner-do.ts` pi wiring ≈450 | `agent.test` 747 · `files-env-conformance.test` 612 · `ta.test` 911 · `agent-support` 181 · guards `agent-authority` 403, `ta` 160, `pi-pin` 78 |
| `ThreadStore`/`ThreadHost` | `thread-store.ts` 299 · `remote-storage.ts` 301 · `thread-host.ts` 1,265 · `threads.ts` 1,157 · `PISTORE` + `ThreadStore` in `wrangler.jsonc` | `thread-store.test` 338 · `thread-host.test` 855 · `drops.test` 1,770 · guards `create-task` 117 + `creations.ts` 54 |
| Gateway provider's model calls | owner `gateway-provider.ts` 509 · gateway `messages.ts` 388, `encode.ts` 97, `envelope.ts` 128 | `gateway-provider.test` 836 · gateway `messages.test` 638 |
| Per-model-call usage tied to pi turns | `origin.ts` 438 · `usage.ts`'s `model_turns`/`model_turn_owners` part | `origin.test` 272 · `turns.test` 449 |
| `ta.scheduled` as a pi task | `scheduled.ts` 227 | `scheduled.test` 1,376 |
| `agent:delegate` | `delegate.ts` 108 | `delegate.test` 811 |
| The skills section | `resources.ts` 1,022 | `resources.test` 531 |
| `live-tee` | `live-tee.ts` 118 | (covered by `gateway-provider.test`) |
| A6 sockets on `watch()` | branch only: `sockets.ts` 751, `socket-shape.ts` 64, conversation doors ≈488 in `owner-do.ts` | branch only: `sockets.test` 881, `conversation-doors.test` 493, `sockets-support` 142 |
| pi-specific remote hook wrappers | owner `hooks.ts` 591 · hooks `runner.ts` 421, `protocol.ts` 165 | owner `hooks.test` 837 · hooks `runner.test` 403 |

**Totals on `origin/main`:**
- About **11,400 source lines**: 10,667 in whole files, plus about 740 inside `owner-do.ts` and `authority.ts`.
- About **12,400 test and guard lines** (12,379 counted).

**Dependencies:**
- **Removed:** `@earendil-works/pi-durable`, `@earendil-works/chord` and `@earendil-works/pi-telemetry`. After the deletes, `owner-do.ts`, `auto.ts` and `codemode.ts` are their only remaining importers, and each is changed.
- **Kept:**
  - `agents`, for Lifecycle and later its `WebSockets`;
  - `@earendil-works/pi-ai`, as a provider library in the gateway and in `connections.ts`/`usage.ts`;
  - `@earendil-works/pi-codemode`.

**Tests rewritten, not deleted:**
- `agent-doors.test` (1,296), which becomes run doors;
- `authority.test` (1,249);
- `usage.test` (862);
- `codemode.test` (388);
- `auto.test` (186);
- `gateway-support.ts` (181);
- `test/worker.ts` (831).

**Specs:** about 45 `owner-object` requirements fall with the deletes, from "Each agent thread is one pi Session" (line 582) through "The turn rows are bounded" (line 1340), plus "A scheduled launch is a pi task" (line 310). Three `gateway` requirements fall: "serves model calls in pi-messages", "a context too long reaches pi as an overflow", and "every other overflow". Slice D removes them.

---

## 2. The new runtime, concretely

**Engines:**
1. A vendor CLI in the user's sandbox, on a subscription or an API key.
2. The user's own main agent on its vendor's platform.
3. pi's CLI in the sandbox, on an egress-keyed API key only, never a subscription's sign-in (ADR-033 decision 1).

**An agent is:**
- `/workspace/agents/<agent>/`: `AGENTS.md`, `.agents/skills/*/SKILL.md`, `mcp.json` (the user's own servers, in one canonical shape) and hooks (Claude-style `hooks.json`; Codex's are "Claude-style" too [R]);
- a small `agent.json` naming the engine (`auto`, `claude-code`, `codex`, `pi`), the model and the agent's MCP scopes;
- TinyAssets state: mailbox, rooms, board, files, schedules and memory.

`OWNER_TREES` keeps all of these the owner's alone, as A5 built it.

### 2.1 How a turn runs: a **run**

A run is one engine invocation: one CLI process from start to exit. Its id is the unit that receipts, caps and the egress meter key on, replacing pi's turn.

1. **Trigger.** One of:
   - a schedule row due on the `runs` lane (cron, `cron.ts`, kept);
   - an input at a door: `agentSubmit` from the app, MCP `converse`, or `run_graph`;
   - mail to one of the owner's sandbox agents (ledger delivery).

   An empty-handed poll never starts a sandbox: the lane checks rows first (§3.2).
2. **Admit, in one gate unit** in the owner's object:
   - the owner is admitted (`requireWork`);
   - the agent is enabled;
   - the caps hold: concurrent runs, runs per agent per day, and the month's sandbox-seconds;
   - the initiator's stamp is standing: the owner, or a client's grant at its generation;
   - the source is chosen: `agent.json`'s, or `auto`'s strongest connected source that has an engine and, for unattended runs, a record citing a page that permits them (ADR-033 D3).

   It then writes the run row (`queued`, with a deadline), mints the **run grant** (§2.4), and opens the **sandbox lease** (G4's `SandboxLeases.open`, its "turn" now the run id), which mints the egress and sync tokens.
3. **Sandbox wake.**
   - A new stateless `tinyassets-sandbox` Worker, the only holder of the E2B API key, gets the call through a `SANDBOX` service binding. G4 found the key has the egress token's custody, since E2B's sandbox info returns static transform headers [R: `g4_g4entry.md`].
   - It calls `Sandbox.connect` (resume) on the owner's sandbox, or `create` from the versioned template, with the network document from G4's `network.ts`:
     - **API key:** deny all, the policy's allow-list, plus `egress.tinyassets.io`.
     - **Subscription:** additionally the source record's `hosts.login` and `hosts.provider`, since that traffic goes straight to the vendor and never through our proxy.
   - It starts our in-VM **runner** with `commands.run(..., {background: true})`. That call "will return immediately and the command will continue to run" [E: docs.e2b.dev/commands/background]. So nothing of ours awaits the run.
4. **File sync in.** The runner (the sync agent the plan already owes, tasks 6) acquires its lease epoch:
   1. It applies `fileChanges(after: synced_seq)`. A cold create gets a full materialization.
   2. It restores the source's `session_paths` from the owner's files.
   3. It projects the agent's files into the paths that engine reads, all from the source record as data:
      - Claude: `.claude/skills → .agents/skills`, and `AGENTS.md` read natively when no `CLAUDE.md` exists (v2.1.277+) [R];
      - Gemini: `context.fileName: ["AGENTS.md"]`;
      - Codex: its 32 KiB `AGENTS.md` cap is checked and reported.
   4. It writes the run's MCP config.
5. **CLI invocation from the source record.**
   - The runner execs `engine.argv` for a new conversation, or `engine.resume_argv` with the stored session id, in the agent's directory as cwd. The command carries:
     - the unattended flags (Claude: `--permission-mode … --permission-prompts none`);
     - the tool filters;
     - the MCP config (Claude: `--mcp-config` plus `--strict-mcp-config`, passed again on every resume because "`--mcp-config` and `--settings` are not restored on resume" [R]);
     - `home_env` (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, …), pointing at a sandbox-only directory outside `/workspace`;
     - for API-key runs: `<PROVIDER>_BASE_URL` on the egress door with placeholder keys (G4's `sandboxEnv`);
     - for subscription runs: none of that. Each vendor CLI then speaks to its vendor directly, with the credential its own sign-in wrote.
   - `--bare` is never used on a subscription: it "never reads OAuth credentials" [E: headless docs].
6. **Event stream.**
   - The runner reads the CLI's JSON stream: Claude `stream-json`, Codex `--json` JSONL, Gemini `stream-json`, pi `--mode json`.
   - It frames each event and sends it to the owner's object (§2.2).
   - It records the session id at the first event that carries one (Claude `system/init.session_id`, Codex `thread.started.thread_id`, Gemini `init`).
   - Vendor error categories in the stream (Claude `system/api_retry` with `authentication_failed`, `rate_limit`, `billing_error` or `account_on_hold` [E]) become run notices.
7. **Sync out.** On exit, the runner commits changed files as one fenced `fileSyncCommit`:
   - It includes `session_paths` (transcripts, auto memory).
   - It **never** includes `secret_paths`. The runner never offers them, and the trusted side refuses any op matching any record's `secret_paths` as a backstop, which keeps ADR-031 D2.
   - It keeps the conflict-copy rule.
8. **Result.** In one unit, the object:
   - settles the run: `succeeded` or `failed` from the stream's terminal event and the exit status, or `unknown` when the runner was lost after start. Unknown is never replayed;
   - keeps a receipt;
   - revokes the run grant and closes the lease, which ends egress streams by G4's end signals;
   - posts the reply where the trigger asked: the conversation, or the mail thread.

   The sandbox Worker then pauses the sandbox (pause measured at 185 ms p50 [E]), or lets E2B auto-pause on timeout (`lifecycle.onTimeout: 'pause'` [E: persistence docs]). ADR-026's zero idle tail holds.

**Cancel and deadline:** the object tells the runner over its socket. The runner sends SIGTERM to the CLI ("Stop a run with SIGTERM" is documented for Claude [E]), then kills it after a grace. A lost runner is detected by the lease's 20 s heartbeat and its 60 s TTL (G4/files).

### 2.2 How live output reaches the app

**The path:** CLI stream events → runner → the owner's object → the owner's app sockets.

- **Runner → object.** One outbound WebSocket from the runner to the doors Worker, which forwards the upgrade to the owner's stub with `stub.fetch`. A6 found this is the path an upgrade must take [R].
  - The preferred host is `egress.tinyassets.io`, so E2B's transform injects the lease's egress token outside the VM and no credential sits in the VM [I].
  - **Owed probe:** whether E2B's transforming proxy carries a WebSocket upgrade.
  - **Fallback:** the runner POSTs batched frames every 250 ms on the sync token, which G4 already lets the in-VM agent hold for the sync doors only. It would be widened to "this lease's run events".
- **Object → app.** The object relays each frame to the owner's app sockets subscribed to that conversation. These are hibernating sockets in the object, with A6's ticket bound to the grant and sign-out.
- **Frame shape.** `{v:1, run, seq, at, kind, event}`:
  - `kind` (`init`, `text_delta`, `tool_call`, `tool_result`, `result`, `error`) comes from the source record's `engine.events` matchers, as data;
  - `event` is the CLI's raw JSON;
  - Claude's token deltas need `--include-partial-messages` with `--verbose` [E].
- **What is stored.** Frames are not committed. The object keeps a bounded in-memory tail per live run for late joiners. What is durable is the run row, the result, the session id, and the CLI's own transcript file, synced at run end.
- **Cost.** Cloudflare bills incoming WebSocket messages at 20:1 as requests ($0.15 per million) and outgoing messages not at all [E: DO pricing]. The object stays awake, and is billed duration, while frames flow ($12.50 per million GB-s, about $1.6 per million seconds at 128 MB) [E]. Nothing else in a run holds the object.

### 2.3 How conversation continuity works

- **Rows.** A `conversations` row (agent, source, `session_id`, created and last run) replaces the thread row. The next input to that conversation runs `resume_argv` with the id:
  - Claude: `--resume <id>`;
  - Codex: `exec resume <id>`, with `--ephemeral` dropped from today's record;
  - Gemini and Grok: `-r <id>`;
  - pi: `--session <id>` [R/E].
- **Where the session store lives.** In the CLI's own files: Claude `~/.claude/projects/<project>/<id>.jsonl` under `CLAUDE_CONFIG_DIR`, Codex `$CODEX_HOME/sessions`, Gemini `~/.gemini/tmp/<hash>/chats` [R].
  - They live on the sandbox disk while it is warm, and in the owner's files as the durable copy: the record's `session_paths`, synced out after each run and restored on a cold create.
  - So a lost sandbox costs one cold create plus a sync, and the conversation still resumes. The credential does not survive (next bullet).
  - The cwd is stable per agent (`/workspace/agents/<agent>`). Claude also pins its project directory name (`CLAUDE_CODE_PROJECT_DIR_NAME`, v2.1.234+ [R]), so the resume key never moves.
- **Credentials.** They sit beside the sessions in the CLI's home, sandbox-only and never synced. A cold create therefore needs the user to sign in again, inside the sandbox, through the vendor's flow. This is ADR-031 D2's cost.
- **Kimi** does not print its session id in `-p` mode, and `--agent-file` cannot be combined with `--session` [R]. Its record uses a per-conversation cwd with `--continue`.
- **Switching engine mid-conversation** cannot resume across vendors. It starts a new session seeded with the last result's text, and the app says so [I].
- **Mid-run steering:** Claude takes more user messages on `--input-format stream-json` [E: headless docs mention it]. Other engines queue the message for the next run [I].
- **Retention:** Claude keeps transcripts 30 days by default (`cleanupPeriodDays` [R]). The owner's own file history and quota (S4–S5's rules) bound the synced copies.

### 2.4 How authority holds: the per-run scoped grant

- **What the CLI connects to.** The CLI's TinyAssets MCP server is `https://egress.tinyassets.io/mcp`: the same `tools.ts` table, under a **run-scoped profile**.
  - The CLI's MCP config holds no bearer. E2B's transform sets the lease's egress token on every HTTPS request to that host, outside the VM [R: G4].
  - The door routes by the token's owner part, then the object resolves the lease, the live run, and that run's grant.
- **The run grant.** A row `run_grants(run, lease, stamp, scopes, expires_at)`:
  - **scopes:**
    - for an owner-started or scheduled run, the agent's declared scopes (`agent.json`, owner-written);
    - for a client-started run, the intersection with that client's grant triples, so a client reaches nothing through an agent it could not reach directly. This is A2's invariant, kept;
  - **stamp:** the initiator. A client's stamp is rechecked with `Grants.standing` on each MCP call, and a grant change or revoke ends the run, as A2 did for turns;
  - **lifetime:** dies at settle, at the deadline, or when the lease closes;
  - **file writes:** the CLI's own file tools write the sandbox disk. The sync-out for a run under a client's stamp still never writes `OWNER_TREES`, which tasks 6 already requires.
- **Fallback, if transforms prove unusable for MCP:** a bearer in the MCP config. It would be a 256-bit run token whose hash the run grant holds, visible in the VM but scoped to one run, its deadline and its scopes. Prompt-injected code could replay it for the run's lifetime and only inside its scopes [I].

### 2.5 How the owner's limits hold

**API-key runs.** Every model request leaves through the egress door, which G4 already meters:
- The reservation is priced at the connection's dearest rate, bounded by the body length and the output cap.
- It is clamped and settled by pi-ai's own parser in `meter.ts`.
- It is re-keyed from the lease's turn to the **run**, with caps per run (requests, tokens, cost) and per source per day and per month (`usage.ts`'s counters).

The CLI's own budget flags are set from policy as a second line: Claude `--max-budget-usd` (which also stops background work [E]) and `--max-turns`; Vibe `--max-price` and `--time-limit` [R]. They are never the enforcement, because Claude calls its cost figures "client-side estimates" [E].

**Subscription runs.** TinyAssets cannot meter tokens it never sees.
- **Model limits:** the vendor's own. Claude Pro and Max have a 5-hour and a weekly limit, "shared across Claude and Claude Code"; Gemini allows 1,500 requests a day on AI Pro; Copilot gives monthly credits [R].
- **Our caps:**
  - runs per agent per day;
  - concurrent runs per owner;
  - a wall-time deadline per run (default 30 min [I]; Claude's own idle background cap is 10 min [E]);
  - sandbox-seconds per owner per month. This is TinyAssets' own E2B cost (§3).
- **Stop on vendor errors.** A `rate_limit`, `billing_error` or `account_on_hold` event stops the agent's scheduled runs until the next window and raises a notice, so we never hammer a plan. Anthropic's limits "assume ordinary, individual usage" [R].

### 2.6 How subagents work

They are the engine's own:
- Claude: `.claude/agents/` or `--agents`, and the `Task` tool;
- Codex: `[agents.<role>]` with `features.multi_agent`;
- Gemini: `.gemini/agents`; Grok: `.grok/agents`; Kimi: agent-file allowlists [R].

TinyAssets builds no delegation tool. Work between two TinyAssets agents, possibly on different engines, is mail or a board claim: agent A posts, which starts B's run, and B posts back. That is the ledger's authorship, which pi never had and ADR-032 kept as a difference.

### 2.7 How a main agent joins rooms and the board

**Connect.** Claude, ChatGPT, Grok and Gemini connect over OAuth. Muse connects with an ADR-028 key in its Secrets tab [R: `main-agents.md` §1]. The agent registers by card (`registrations.ts`) as an outside agent in the ledger.

**Read and act.** On its own schedule or on a doorbell, it reads with `mailbox_read`, `collection_list` (board, `room:<app>:<room>`) and `collection_get`. It posts as itself with `room_post` and `collection_write`. A board claim is a compare-and-swap write on the row's etag. All of this exists and is tested.

**Doorbells (tasks 7, unchanged):**
- ChatGPT: MCP Events;
- Claude: `/fire` on a scheduled task;
- Grok Bot: a webhook-triggered routine;
- Muse: none; it pulls [R].

**New primitives so a main agent needs few calls:**
- **`code`:** code mode at the MCP door. A script calls the door's tools as `tools.mailbox_read(...)`, each held to the caller's grant, in the hooks Worker's `CodemodeSandbox`.
- **Digests:** `inbox_digest` and `board_digest` give unread mail, room heads and open board rows in one read.

**OpenAI's directory "rejects a generic executor or discovery tool"** (plan doc, Doors § MCP). So `code` is in the custom-connector and run-scoped profiles and **not** in the directory-listed profile (§4.6).

### 2.8 What no longer exists

- pi in the owner's object, and the "model sees exactly four tools plus `ta`" rule. The engine's tools are the vendor's. TinyAssets adds one MCP server and filters built-ins only where the owner's `agent.json` says to.
- Per-model-call turns, pi telemetry, pi partial commits and their row cost.
- User hooks in a Worker Loader.

---

## 3. Cost and latency checks

### 3.1 Rates used

All [E], read 2026-11-01 unless marked.

**E2B** (e2b.dev/pricing):
- Billed "per second of a running sandbox".
- vCPU: $0.000014/s each. RAM: $0.0000045/GiB-s.
- Storage: 10 GiB free on Hobby, 20 GiB free on Pro.
- Plans:
  - Pro: $150/month for 100 concurrent sandboxes;
  - Pro+: 600 concurrent for +$500 a month;
  - Pro++: 1,100 concurrent for +$1,000 a month;
  - Enterprise "starts at a $2,000/mo commit, 500 concurrent". The 2026-10-28 measurement doc had $3,000: a change since.
- Paused sandboxes are not billed and are kept indefinitely. Pro sessions run up to 24 h, and the limit "is reset" by a pause and resume [E: persistence docs].

Sizes used below:

| Size | Per second | Per hour |
|---|---|---|
| S1: 1 vCPU / 1 GiB | $0.0000185 | $0.067 |
| S2: 2 vCPU / 1 GiB (the measured template) | $0.0000325 | $0.117 |
| S3: 2 vCPU / 2 GiB | $0.000037 | $0.133 |

**Cloudflare Durable Objects** (developers.cloudflare.com/durable-objects/platform/pricing):
- Requests: $0.15 per million.
- Incoming WebSocket messages: billed 20:1. Outgoing messages: free.
- Duration: $12.50 per million GB-s.
- Rows written: $1.00 per million after 50 million a month [R: `files-research.md` §7].

### 3.2 E2B cost per agent turn and per idle-scheduled agent

**Per-run overhead** [I, sum of measured and estimated parts]:
- resume to first byte: 247 ms p50 / 706 ms p95 [E];
- sync-in: about 0.3–1 s [I];
- CLI start to its first event: 344 ms p50 / 582 ms p95 [E local, §3.4];
- MCP connect: about 0.2–0.5 s [I];
- sync-out: about 0.3–1 s [I];
- pause: 185 ms p50 / 574 ms p95 [E].

That is about 2–4 s; I use **3 s**.

**The important change:** the sandbox now runs for the **whole turn**, model waits included. The old plan ran it only for `bash`, at 2 CPU-minutes per active hour.

| Run (wall time incl. 3 s overhead) | S1 | S2 | S3 |
|---|---|---|---|
| Chat turn, 20 s → 23 s | $0.00043 | **$0.00075** | $0.00085 |
| Agentic task, 2 min → 123 s | $0.0023 | **$0.0040** | $0.0046 |
| Long task, 10 min → 603 s | $0.011 | **$0.020** | $0.022 |

**Idle-scheduled agent per day** (S2):

| Schedule | Sandbox per day | Per month |
|---|---|---|
| Doorbell on change, nothing new (the lane reads rows; no sandbox) | $0 (24 alarms ≈ $0.0000036) | ≈ $0 |
| Daily cron, one 60 s run | 63 s → $0.0020 | $0.06 |
| Hourly run that must look outside TinyAssets (23 s each) | 552 s → $0.018 | $0.54 |
| Every 15 min, must run (23 s each) | 2,208 s → $0.072 | $2.15 |

[I] So the default for "check my inbox, rooms and board" is a doorbell on change, decided by the object from rows. A time poll that wakes a sandbox is only for agents that must look at the outside world.

### 3.3 Resume p50/p95 and the cold create

All [E] (`e2b-resume-measurement.md`, 2026-10-28, 2 vCPU / 1 GiB, 206 MB of files and a 150 MiB live process). Latencies include the client→E2B path.

| Measurement | p50 | p95 | Sample |
|---|---|---|---|
| Resume → first byte | 247 ms | 706 ms | n=40; p99 849 ms; max 895 ms |
| The resume call alone | 158 ms | 434 ms | n=40 |
| After a 5 min wait | 740 ms | — | n=4 |
| Cold create call | 456 ms | 635 ms | n=10 |
| Cold create → first byte | 569 ms | 766 ms | n=10 |

- **Unmeasured:** resume after hours or days paused (tasks 1 is still open). Owner sandboxes will spend most of their time in that state.
- **What a cold create adds in the new design:**
  - a full file materialization (size-dependent [I]);
  - the session-file restore;
  - a vendor **sign-in again** for subscription engines, a human step;
  - the CLIs themselves, which are baked into the versioned template, never installed at run time [I].

### 3.4 CLI startup time

Measured by me in the research environment, 2026-11-01 (Linux, 4 vCPU, 16 GB, Node from `/usr/local/bin`), with pinned versions in clean directories, an empty `HOME`, and `--strict-mcp-config` with no servers, n=10 each. [E local; not E2B]

| Command | First stdout line | Wall |
|---|---|---|
| `claude --version` (2.1.308) | — | p50 10 ms / p95 15 ms |
| `codex --version` (0.163.0) | — | p50 34 ms / p95 36 ms |
| `claude -p --output-format stream-json --verbose` | **`system/init` p50 344 ms / p95 582 ms** | p50 1.86 s / p95 3.0 s |
| `codex exec --json` (not signed in) | — | exits 1 in 33 ms |

- **Codex's first event and Claude with a remote MCP server** were not measured. Claude "waits for still-pending servers before running the first turn, up to … 30 seconds by default", unless a cached tool list lets it skip the wait [E: headless docs]. A slow MCP door therefore delays every Claude run.
- **Caveat: the Claude runs completed real model turns.** With no key or credential supplied by me, the 10 Claude runs returned `result: "ok"`, for example `duration_api_ms` 2,032. The research environment authenticated them; how was not inspected.
  - So the 1.86 s wall is a trivial whole turn, and about 11 trivial model calls were made: 10 in the timing loop and 1 to read the output shape.
  - I stopped there. The Codex runs made no model call.

**Time to the first event for a woken run** [I, sum]:
- p50: resume 0.25 s + sync-in about 0.3 s + CLI init 0.34 s + MCP about 0.2 s ≈ **1.1 s before the model's first token**;
- p95: ≈ **2.3 s**.

This is a regression against the pi-in-object plan, where a chat turn spent nothing before its gateway call.

**Mitigations:**
- keep the sandbox running for 60 s after a run while the owner's app is open on that conversation (S2: $0.002 per window);
- resume on compose (the app's typing signal);
- measure both in slice S.

### 3.5 Monthly estimate per active owner, against the current plan

**The current plan** (rev 77 § Cost):
- **Assumptions:** 36 active hours per owner per month and 2 CPU-minutes of sandbox per active hour.
- **At 1,000 owners:** $36–105 trusted + $555 E2B + $15 files = **$0.61–0.68 per owner**.
- **At 50,000 owners:** "roughly $23k–27k … about $0.50 per owner".
- **Unbudgeted:** pi partial-commit rows. The gateway research warned they cost "$1.0–1.6 in rows alone" for an owner whose model streams 9 h a month [R: `gateway-research.md` §3.3].

**The new design** [I, from the rates above]. "Plan-equivalent" keeps the plan's 36 active hours and assumes 10 min of runs per active hour plus two daily crons (25,380 sandbox-seconds a month):

| Owner profile | Sandbox (S2) | Trusted side | Files (1 GB) | Fixed E2B share at 1k owners | **Total** |
|---|---|---|---|---|---|
| Main agents only (engine 2) | $0 | ≈ $0.02–0.05 | $0.015 | $0 | **≈ $0.04–0.07** |
| Light: 10 runs a day × 30 s | $0.29 | ≈ $0.04 | $0.015 | $0.15 | **≈ $0.50** |
| Plan-equivalent: 36 active h × 10 min of runs + 2 daily crons | $0.82 | ≈ $0.10–0.15 | $0.015 | $0.15 | **≈ $1.1** |
| Heavy: 2 h of runs a day | $7.02 | ≈ $0.5 | $0.015 | $0.15 | **≈ $7.7** |

**Trusted-side basis:**
- DO duration while frames flow: 25,380 s × $1.6/M ≈ $0.04.
- Rows: about 110 per run (the run, grant, lease, about 20 metered egress calls at 4 rows each, the sync commit), × 600 runs ≈ 66k → $0.07.
- WebSocket messages at 20:1: negligible.

**Concurrency** (plan-equivalent) [I]:
- **1k owners:** about 10 average concurrent running sandboxes, with peaks perhaps 30–50, within Pro's 100.
- **10k owners:** about 100 average, which needs Pro+ (600).
- **50k owners:** about 480 average, which needs Enterprise or the self-hosted fleet.

**At 50k owners:** about $41k sandbox + $5–7k trusted + $750 files ≈ **$47–49k a month (≈ $0.95 per owner)**, against the plan's ≈ $0.50. With S1 sizing (1 vCPU, if the probe allows), the sandbox line falls about 43% (≈ $0.70 per owner). The plan's self-hosted Firecracker exit (about 5× cheaper at 50k [R: plan]) matters more now.

**Reading** [I]:
- Cost now scales with **engine wall time**, not with `bash` time. That is about 1.7–2× the plan for the plan's own activity profile, and far less for main-agent owners.
- The trusted side gets cheaper and stays under ADR-032's $0.25 threshold: no awaited model streams in the object and no pi partial commits.
- Model spend stays the user's own (their subscription or key).
- TinyAssets pays every sandbox second. Sandbox-seconds must be metered per owner and capped by plan, which is a pricing question for the founder.

---

## 4. Risks

### 4.1 Claude scheduled tasks do not load custom connectors (#106037)

**Fact** [R: `main-agents.md`, "V github.com/anthropics/claude-code/issues/106037"]:
- A custom remote MCP connector's tools are "missing for the whole run" of a scheduled task, in claude.ai web and Cowork. Built-in connectors load.
- The issue was filed 2026-10-29, is labelled `regression`, and was open on 2026-11-01.
- #102388, also open, reports the same for cloud routines.
- I could not re-read it: GitHub for that repository was among the sources the research could not reach on 2026-11-01.

**Impact** [I]:
- It breaks engine 2 for Claude, which is a Claude scheduled task pulling TinyAssets on a schedule or on `/fire`. That is TinyAssets' connector case.
- It does **not** touch engine 1: Claude Code in the sandbox reads its MCP servers from `--mcp-config`, which the run writes.

**Mitigation:**
- For Claude subscribers, unattended agent work defaults to Claude Code in the sandbox, which ADR-033 allows for Claude.
- `/fire` stays built (tasks 7) but behind a live re-test gate.
- The founder's Claude account re-tests on every Claude Code release note until the fix lands.

### 4.2 Vendor-terms drift

- **Anthropic** "reserves the right to take measures to enforce these restrictions and may do so without prior notice". Its limits "assume ordinary, individual usage" [R: `subscription-engines.md` §1]. Heavy unattended use may one day be judged not ordinary.
- **OpenAI and xAI** are unclear for a hosted CLI. Google forbids third-party use of Gemini CLI OAuth. Kimi enforces a client whitelist. Qwen, Z.ai and MiniMax forbid or discourage unattended use [R: §11].

**Mitigations:**
- Each engine record cites a dated vendor page (ADR-033). A record that cannot cite one is refused for unattended hosted runs.
- A **per-source kill switch** in the directory's `control` record makes a source fail closed to the API-key or main-agent path without a deploy [I: a new field].
- A terms re-read on every pinned-version bump and at least monthly.
- Never put a subscription's traffic through our proxy.
- The founder accepts Anthropic's Commercial Terms before hosting Claude Code (ADR-031 D3, still standing).

### 4.3 CLI version pinning

**Facts** [R]:
- Records pin npm version, integrity and lock digest.
- Today's pins lag npm: Claude Code 2.1.308 against 2.1.316, Codex 0.163.0 against 0.166.0.
- Auto-update is switched off by `DISABLE_AUTOUPDATER`, `COPILOT_AUTO_UPDATE=false`, `--no-auto-update` and Gemini's `disableAutoUpdate`.

**Risks:**
- **Bare-mode default.** Claude says "`--bare` is the recommended mode for scripted and SDK calls, and will become the default for `-p` in a future release" [E: headless docs]. Bare mode "never reads OAuth credentials", so a future pin would silently break every subscription run unless the record passes the opt-out. The template build must test for it.
- **Event-schema drift** in `stream-json` and JSONL. Mitigations:
  - a contract test per pinned version at template build: one run with a fixed prompt against a stub endpoint for the API-key path, and the founder's account for the subscription path;
  - feature detection from Claude's `system/init.capabilities` array [E].
- **Vendor-forced minimum versions:** a server may refuse old clients [I]. That needs a regular bump cadence: one template a week, canaried.
- **Non-npm installs** (Vibe in Python, Grok Build and cursor as curl binaries) need the record's `install.manager` widened [R].

### 4.4 A sandbox outage against Direction 8

**When E2B is down** (or the owner's sandbox is lost):
- **Stops:** engine-1 and engine-3 runs. A run already started goes `unknown` (never replayed). Scheduled runs wait on their lane and are refused, never replayed, past their window [I].
- **Still works, with no sandbox and no host of ours:**
  - files: the manifest is in the owner's object and the bytes in R2; no file path touches the sandbox [R: `files-research.md` §6];
  - rooms, the board, the mailbox and receipts;
  - the MCP door and `/api`;
  - doorbells;
  - the owner's app (reads, writes, uploads);
  - every main agent over MCP (engine 2, on its vendor's platform);
  - code mode, which runs in the hooks Worker, not the sandbox.

So Direction 8 holds for every surface, and agent *runs* depend on one vendor. A second sandbox vendor (Cloudflare Containers, the plan's runner-up) is a later slice; the file plane and source records keep it portable. A lost sandbox's unsynced work is at most the in-run window, about 15 s with checkpoints [R].

### 4.5 Sign-in longevity in a sandbox (new)

- Claude credentials warn 3 days before expiry, and "background sessions stop making progress once the credential expires" [R].
- Codex's ChatGPT session is "stale after roughly 8 days" without a refresh [R, search-index only].
- A cold create loses every subscription sign-in.

**Mitigation:** the runner runs the record's `status_argv` before each unattended run. A failed check raises a "sign in again" notice and refuses the run. It never falls back silently to another source (Fact 8).

### 4.6 Code mode against OpenAI's directory rules (new)

The plan records that OpenAI's directory "rejects a generic executor or discovery tool". The MCP door's tools are therefore profiled: `code` is off in the directory-listed profile (Direction 7) and on for custom connectors and runs.

### 4.7 Cost and concurrency (new)

- Sandbox time scales with engine wall time (§3.5).
- E2B Pro's 100 concurrent sandboxes are reached at about 10k owners.
- Sandbox-seconds caps per owner are required before launch.

---

## 5. Slice order from now to cutover

**What stays, already merged:**
- doors (2.1, minus the agent tools' targets);
- owner object 3.1–3.6 (revision primitive, ledger and mailbox, policy, grants, sessions, schedule);
- gateway G0–G3 (keyring, seal, connect, OAuth, verify, forward);
- files 6.1 and the byte quota;
- admission and the erasure order;
- the hooks Worker's code-mode sandbox.

**Founder-needed slices** are marked **[F: …]**: spending, live probes on vendor accounts, accounts, approvals.

| # | Slice | What | Where |
|---|---|---|---|
| **0** | **Decide and record** (done) | Done: ADR-033, merged as #5384; ADR-034, the Direction 1 and 2 replacements (README § Direction, synced by `sync_direction.py`) and the `tasks.md` re-cut pointer, in ADR-034's PR. Still open: the plan of record's rev 78, and closing A6's PR once slice R carries its notes. | docs |
| **G4** | Finish 8a | Land `platform-g4` with its review fixes (H1: drop the edge-expiry check on stale KV; M3: settlement leaks; M5: deviation-8 wording). Keep "turn" in its names until slice R re-keys it. | workerd |
| **D** | **Deletions, one slice** | Everything in §1.7: source, tests, guards and specs. Remove the `PISTORE`/`ThreadStore` binding and class (the config guard updated, the owner-bound guard recomputed with fewer tables). Rewrite the pi migrations in place (nothing deployed). Remove `pi-durable`, `chord` and `pi-telemetry`. Remove `run`, `protocol.ts` and `runner.ts` from the hooks Worker. Remove the gateway's `/v1/messages`. The four agent doors and the `converse`/`run_graph` tools leave the tables until slice R (an absent tool, never a stub: Fact 8). | workerd |
| **E** | Engine records | `CliSpec.engine` (§2.1), `secret_paths`, `session_paths`, the unattended citation, non-npm install, `engines` on API-key sources. Update the Claude Code, Codex, Gemini and Kimi records. New `anthropic` and `openai` API-key records and a `pi` CLI record that runs on an egress-keyed API key only, with no subscription sign-in (ADR-033 decision 1). Park `chatgpt.json`. The checker in `doors/src/sources.ts` and its guard. | data + workerd |
| **R** | Runs in the owner object | `runs`, `run_grants` and `conversations` (schema), the `runs` lane with doorbell-on-change, admission and caps (§2.5), run-level `auto`. The run-scoped MCP profile and grant checks. `usage.ts` and G4's reservation re-keyed to runs. The four agent doors and the `converse`/`run_graph` tools back, starting runs. `SANDBOX` binding behind an interface whose absence refuses loudly. The run-event relay to app sockets (A6's salvaged ticket and binder path). | workerd |
| **S** | **Sandbox (8b), live** | The `tinyassets-sandbox` Worker (E2B key; create, connect, pause, update the network, start the runner). The versioned template (pinned CLIs, the runner, `resolv.conf` on the pinned resolver). The **runner**: sync in and out with secret-path refusal, projection, invocation from the record, event framing, socket or POST, SIGTERM. Live probes: G4's owed list (private CIDRs, transform readability, `/etc/hosts`, IPv6, DNS); a transform over WebSocket and over MCP; resume after hours and days; CLI memory for S1 sizing; per-run overhead; keep-warm. | **[F: E2B Pro $150/mo; E2B workload-identity beta request; accept Anthropic Commercial Terms; founder's Claude, ChatGPT and API keys for the probes]** |
| **M** | MCP door for engines and main agents | `code` (code mode at the door, the hooks Worker narrowed to its sandbox, H2's probe cut to code mode), `inbox_digest`/`board_digest`, tool profiles (directory, custom, run). Live connector tests from Claude and ChatGPT (`ui-test`). | workerd + live **[F: live connector accounts. Dynamic Workers (open beta) are no longer needed once the loader goes]** |
| **W** | Wakes and outside agents (tasks 7) | `WakeSender`: ChatGPT MCP Events, Claude `/fire`, Grok webhook routines. Kill switch through `DOORBELL`. Live: a dot wakes on an event (per tier); a Grok routine calls our MCP; #106037 re-test. | **[F: ChatGPT Pro, Claude, SuperGrok/X Premium accounts; live probes]** |
| **L** | Doors live | The rest of tasks 2: the AuthKit upstream, DNS and routes, `/hooks/inbound`, the key store, rate limits; 0c, the dark deploy of owner, gateway, doors, sandbox and files; the handles canary. | **[F: DNS and route edits; Cloudflare plan check]** |
| **P** | Before real data | SSE-C on chunks (live probe), the append-only migration guard from first deploy, the nightly manifest and owner export, per-owner sandbox-seconds caps on. | live |
| **8** | Founder live test | The founder's account end to end: Claude Code on the subscription in the sandbox, Codex on an API key through egress, a ChatGPT or Claude main agent in a room, a scheduled run, a sandbox-down drill (§4.4). | **[F]** |
| **9** | Cutover window | Full backup; each owner's broker stores imported into their object and each volume into object storage; DNS switched; the droplet paths deleted (Direction 5). | **[F: maintenance window]** |
| **10** | Later slices | Rooms and status (S4), app manifest and agent players (S6), the **device runner** (S7: the same runner on the user's own machine with their logged-in CLIs, ADR-024 and ADR-031 D5), real-time tiers (S8). Claude and Grok wakes (S9) are mostly in W. Sign in with ChatGPT (S10) is parked: it served our loop. A second sandbox vendor. | later |

**Why this order** (Direction 5) [I]:
1. The record (0) comes first so no agent builds on pi again.
2. G4 is nearly done and is the API-key engine's only model path, so it lands before D rather than being torn up.
3. D removes about 24,000 lines before anything is built on them.
4. E is pure data that R and S read.
5. R is the foundation of every run and can be built and tested in workerd without E2B.
6. S is the first slice that spends.
7. M and W can run beside S.

---

## 6. Directions 1 and 2 (adopted 2026-11-01)

Only Directions 1 and 2 named pi. The founder adopted the replacements below on 2026-11-01 (README § Direction, synced by `sync_direction.py`). The block stays at 18 lines and well under its 4,096-byte cap.

**Direction 1, before:**
> 1. Product: a default bubble agent that continuously matches and beats the industry-leading main agents (Grok Bot, Muse, ChatGPT dots, Claude Duet), built as a starter package on a pi.dev-modelled harness; users customize the harness and interface to build anything.

**Direction 1, adopted:**
> 1. Product: a default bubble agent that continuously matches and beats the industry-leading main agents (Grok Bot, Muse, ChatGPT dots, Claude Duet), shipped as a starter package of agent files that runs on the user's own engine: their provider's unmodified CLI on their subscription or key, or their own main agent; users customize the agent's files and interface to build anything.

**Direction 2, before:**
> 2. Plumbing: the model sees 4 tools (read/write/edit/bash) plus `ta`; one extension unit; one agent definition for every model provider; no provider-specific code.

**Direction 2, adopted:**
> 2. Plumbing: an agent is files (`AGENTS.md`, skills, MCP config, hooks) plus TinyAssets state, run by an unmodified engine (a provider's CLI in the user's sandbox, the user's main agent over MCP, or pi's CLI); TinyAssets is everything around the engine and reaches every engine through one MCP door; one agent definition for every engine; no provider-specific code: engine differences are data.

The adopted text drops "the model sees 4 tools plus `ta`" as a platform rule. Each engine's tools are its vendor's. TinyAssets adds one MCP server, and an agent's `agent.json` may filter built-ins. The default bubble agent may keep the 4 tools plus `ta` as a line in the starter package's `agent.json`.
