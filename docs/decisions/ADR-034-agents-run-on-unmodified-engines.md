# ADR-034: Agents Run on Unmodified Engines; TinyAssets Is Everything Around Them

## Status

Accepted (founder, 2026-11-01).
- **It amends** ADR-032 decisions 1, 3, 4 and 5, its "Amended 2026-10-30" section, two lines of its "What stands", and two of its Consequences, each listed below. ADR-032 decision 2 stands with one addition (the egress host also serves the run-scoped MCP door and the runner's event socket), and decision 6 stands.
- **It also amends** the principle ADR-023 states and ADR-032 kept, that "the core loop runs outside every box".
- **It builds on** ADR-033 (merged in #5384).
- **Per ADR-005**, ADR-032's and ADR-023's bodies are not edited. Only their Status lines gain "amended by ADR-034".

## Date

2026-11-01

## Context

ADR-032 put pi-durable in each owner's Durable Object as the agent loop. The model sees four tools plus `ta`, and every model call goes through the gateway in pi-ai's pi-messages protocol. Slices A0–A5, S1–S5, F1 and H0+H1 built that, in workerd, and nothing is deployed.

On 2026-11-01 the founder ruled:
- Most users will power their agents with a subscription.
- Even API-key users will mostly want the provider's own harness, which already allows most of the customization TinyAssets was building into its own loop: instruction files, skills, hooks, subagents and MCP.
- So pi stops being the platform's engine.

ADR-033 (2026-11-01) had already made a subscription CLI "the agent's engine, never a backend", running the whole turn with its own loop.

Evidence read for this ADR:
- **Every major CLI fits one declarative engine record** (`openspec/changes/clean-sheet-platform/subscription-engines.md` §10, read 2026-11-01 from vendor docs and repositories). Claude Code, Codex, Gemini CLI, Grok Build, Kimi Code, Qwen Code, Copilot CLI, Mistral Vibe and Cursor's CLI each have:
  - a one-shot JSON or JSONL mode;
  - resume by session id;
  - an MCP client with streamable HTTP;
  - built-in tool filters;
  - an `AGENTS.md`-compatible instruction file;
  - `SKILL.md` folders.

  pi's own CLI has `--mode json`, `--session` and `--resume` (pi-coding-agent 1.4.0 `docs/cli.md`).
- **Claude Code headless** (https://code.claude.com/docs/en/headless, read 2026-11-01):
  - `stream-json` events start with `system/init`, which carries the session id;
  - `--resume <session_id>` continues a conversation;
  - `--permission-prompts none` is for runs "when nobody is available to answer permission prompts";
  - `--bare` "never reads OAuth credentials", so a subscription run must not use it, and Anthropic says bare mode "will become the default for `-p` in a future release".
- **E2B** (https://docs.e2b.dev/commands/background, `sandbox/persistence`, https://e2b.dev/pricing, read 2026-11-01):
  - a background command "will return immediately and the command will continue to run";
  - paused sandboxes are kept indefinitely and are not billed;
  - running time is $0.000014 per vCPU-second and $0.0000045 per GiB-second;
  - resume to first byte was measured at p50 247 ms and p95 706 ms (`openspec/changes/clean-sheet-platform/e2b-resume-measurement.md`, 2026-10-28).
- **The main agents** (Claude, ChatGPT dots, Grok Bot, Meta Muse) each reach a custom MCP server and run their own schedules. ChatGPT and Grok Bot also take a push (`openspec/changes/clean-sheet-platform/main-agents.md`, 2026-11-01).

## Decision

1. **Every agent runs on an unmodified engine.** There are three kinds, and none has special status:
   1. A provider's own CLI in the user's sandbox, on either of:
      - the user's subscription, signed in through the vendor's own flow where its terms allow (ADR-033);
      - an API key the sandbox never sees: the CLI's base URL points at the egress door with a placeholder key, and the gateway injects the real one.
   2. The user's own main agent on its vendor's platform, reaching TinyAssets over MCP. It is woken by its vendor's door or runs its own scheduled tasks (ADR-031 decision 4).
   3. pi's coding-agent CLI in the sandbox, on an egress-keyed API key only, never on a consumer subscription's sign-in (ADR-033 decision 1), for users who want a fully custom harness.

2. **An agent is files plus TinyAssets state.**
   - **Files:** under `/workspace/agents/<agent>/`, the owner's alone. They are `AGENTS.md`, `SKILL.md` folders, MCP config and hooks, in the formats these CLIs read, plus one small `agent.json` naming its engine, model and MCP scopes.
   - **State:** the mailbox, rooms, board, files, schedules and memory.
   - **Projection:** the sandbox projects the files into each engine's paths from its source record (for example `.claude/skills` for Claude Code). The platform builds no feature editor (Direction 3).

3. **TinyAssets is everything around the engine:**
   - the per-owner trusted object (identity, grants, policy, admission, erasure);
   - the mailbox, rooms, board and files;
   - schedules, which start a run in the sandbox or ring a main agent's doorbell;
   - the MCP door;
   - the sandbox and its file sync;
   - the egress door and the gateway's sealed keys.

4. **The unit of work is a run: one engine invocation.**
   1. **Admit.** The owner's object admits a run against the owner's admission, the agent, the run caps and the initiator's standing authority. It chooses the source (Direction 4's strongest connected source that has an engine), records the run, mints its grant, and opens the sandbox lease.
   2. **Start.** A stateless sandbox Worker, the only holder of the E2B key, resumes or creates the owner's sandbox with the owner's network document and starts our in-VM runner in the background.
   3. **Run.** The runner syncs the owner's files and the engine's session files in, projects the agent's files, and invokes the CLI from its source record. It streams the CLI's events to the owner's object, then syncs out.
   4. **Settle.** The object settles the run from the stream's terminal event. A run lost after it started is `unknown` and is never replayed.

5. **Engines are data.** A source record for an engine declares, beside ADR-033's citation and unattended-use flag:
   - its new-run and resume invocations;
   - its event matchers;
   - its instruction and skills paths;
   - its MCP configuration template;
   - its tool, approval and no-update flags;
   - its home directories;
   - its `session_paths`, which sync to the owner's files;
   - its `secret_paths`, which never leave the sandbox. The trusted side refuses any sync of them (ADR-031 decision 2).

   The platform holds no provider-specific code (Direction 2).

6. **Authority is a per-run scoped grant.**
   - **The connection.** The engine's TinyAssets MCP server is the egress host's run-scoped MCP door. The sandbox's identity is injected outside the VM, so the engine's MCP config carries no bearer.
   - **Scopes.**
     - An owner-started or scheduled run gets the agent's declared scopes.
     - A run an outside client started gets their intersection with that client's grant. Its stamp is rechecked on every call, and a grant change ends the run.
   - **Lifetime.** The grant ends with the run.

7. **The owner's limits hold where the traffic can be seen.**
   - **API-key runs:** every model request passes the egress door, which reserves and settles it against per-run and per-source caps.
   - **Subscription runs:** the vendor's own limits apply, plus ours: runs per agent per day, concurrent runs, a wall-time deadline per run, and the owner's sandbox-seconds. A vendor's rate-limit or billing error stops the agent's scheduled runs and notifies the owner.

8. **Continuity is the engine's own session resume.**
   - **Where sessions live.** The session store is the engine's own files: on the sandbox disk while it is warm, and in the owner's files as the durable copy.
   - **Resume.** The owner's object keeps each conversation's engine and session id.
   - **What a cold create loses.** It loses the engine's sign-in, which never leaves the sandbox. It never loses the conversation.

9. **Live output is the engine's own event stream**, relayed by the owner's object to the owner's app sockets. It is never committed: what is durable is the run, its receipt, its result and the engine's own transcript.

10. **Subagents and hooks are the engine's own.** Work between two TinyAssets agents goes through the mailbox and the board, which keep authorship.

11. **Code mode moves to the MCP door.**
    - **How it works.** A main agent or an engine may send one script that calls the door's tools, each held to the caller's grant. Its VM runs in the separately deployed Worker's `CodemodeSandbox`, never in the owner's object.
    - **Directory-listed profile.** Code mode is not offered there, since OpenAI's directory rejects a generic executor.
    - **User hooks.** These no longer run there.

12. **Deleted, each in one slice** (the re-plan's slice D):
    - the in-object pi loop (`OwnerAgent`, pi's `ExecutionEnv` over the manifest, `ta` as a pi tool);
    - `ThreadStore`, `RemoteStorage`, `ThreadHost` and the thread rows that name stores;
    - the gateway's pi-messages model-call path and the owner's `gateway` provider;
    - per-model-call turn identity on pi telemetry;
    - `ta.scheduled` as a pi task;
    - `agent:delegate`;
    - the skills prompt section, prompt templates and declarative pi packages;
    - the live tee;
    - the remote hook wrappers and the hooks Worker's `HookRunner`.

    The dependencies `@earendil-works/pi-durable`, `@earendil-works/chord` and `@earendil-works/pi-telemetry` go. pi-ai stays as a provider library in the gateway (connect checks and the egress meter), and pi-codemode stays for code mode.

## What changes in ADR-032, exactly

- **Decision 1:**
  - **Changed:**
    - "The trusted side and the agent loop run as one … Durable Object per owner" becomes "the trusted side runs as one Durable Object per owner"; the agent loop is the engine's.
    - "the agent's thread index" becomes the runs and the conversations' engine session ids.
    - The schedule's Lifecycle queue no longer holds "the thread host's wake jobs". It holds a `runs` lane.
    - The owner Worker's "one Durable Object namespace of its own `ThreadStore` class" is removed. Its Worker binds no Durable Object namespace.
  - **Bound Workers.** The allow-list adds the sandbox Worker, which holds the E2B key and reaches nothing but E2B. The hooks Worker is narrowed to code mode.
  - **Stand:** the binder rules, `disable_ctx_exports`, the `env` rule and the module-state rule.
- **Decision 2:** stands with one addition: the egress host also serves the run-scoped MCP door and the runner's event socket, both bound by the sandbox lease's token, which E2B injects outside the VM.
- **Decision 3:**
  - **Removed:** the model-call path from the loop ("opens one owner's sealed credential per call, meters usage" for pi's calls).
  - **Stays:** the gateway holds no owner state, seals and opens credentials, runs the connect, refresh and revoke flows, and serves egress, where it meters each call. "No credential sits in the loop or the sandbox" stands for API keys. A subscription's own sign-in sits in the sandbox under ADR-031 decision 2, as before.
- **Decision 4:** replaced whole. "pi-durable is the loop, hosted in the owner's object, on its own mechanisms" lapses, together with:
  - its hook names, Sessions, `ExecutionEnv`, replay policy, compaction, steering, subagents, Agent Skills section, `modelTools` and `pi.agent` bullets;
  - every "Kept difference";
  - the pi 1.4.x pin.

  What survives of it is the separately deployed Worker, now only for code mode's VM (decision 11 above), for the same measured reason: compute in the owner's object delays all its requests.
- **Decision 5:**
  - **Changed:**
    - "A Firecracker microVM sandbox … is woken only for `bash`" becomes "is woken for every engine run".
    - "pi's `ExecutionEnv` maps onto the manifest and the files Worker" is removed.
    - Sync adds two rules: it carries `session_paths`, and it never carries `secret_paths`.
  - **Stand:** bytes, manifest, versions and GC, the files Worker, and the lease and conflict rules.
- **Decision 6:** stands.
- **"Amended 2026-10-30: agent storage and hosting":** replaced whole. There are no thread stores and no thread host. Its erasure rule, that a store is named before it exists and wiped before its row is forgotten, lapses with the stores. Erasure now kills the sandbox and purges the session files before the owner is wiped.
- **"What stands":**
  - "ADR-023's principle: the core loop runs outside every box" becomes this: the **trusted side** runs outside every box. An engine's loop runs inside the owner's sandbox as untrusted code, or on its vendor's platform.
  - "The model sees four tools plus `ta`" lapses. An engine's tools are its vendor's, and TinyAssets adds one MCP server.
  - "Wakes are ID-only doorbells. Unknown work is never replayed. Status comes only from receipts." stands.
- **"Consequences":**
  - "The decision is re-evaluated if a thread store passes 1 GB … or if pi's Chord v2 `StoreStorage` gains a drop" lapses.
  - The other bullets stand. That includes the $0.25 trusted-side and E2B 1.5 s p95 triggers; the latter now gates every run, not only `bash`.

## What stands

- ADR-024 (the device runner runs only its owner's work). The same in-VM runner later runs on the user's device with their logged-in CLIs.
- ADR-025 (placement), ADR-026 (zero idle tail: the sandbox pauses when a run ends), ADR-027, ADR-028, ADR-029 and ADR-030's split rule.
- ADR-031 as amended by ADR-033, and ADR-033 itself.
- Directions 3, 4, 5 and 8.

## Consequences

- **Direction.** The founder replaced Directions 1 and 2 on 2026-11-01 with the text in README § Direction. "The model sees 4 tools plus `ta`" is no longer a platform rule; the default bubble agent may keep that surface as its own setting.
- **Build order.** `openspec/changes/clean-sheet-platform/tasks.md` is re-cut, in order:
  1. finish G4;
  2. delete in one slice;
  3. engine records (the `pi` record carries no subscription sign-in);
  4. runs in the owner object;
  5. the sandbox, live;
  6. the MCP door's code mode and digests;
  7. wakes;
  8. doors live;
  9. pre-data safety;
  10. the founder's live test;
  11. cutover.

  Branch `platform-a6` is closed after its socket ticket and binder upgrade are carried into the runs slice.
- **Cost.** Sandbox time now covers each engine run's whole wall time, model waits included, at $0.117 per running hour for 2 vCPU and 1 GiB. The trusted side gets cheaper: no awaited model stream and no pi partial commits. TinyAssets pays every sandbox second, so sandbox-seconds are metered per owner and capped before launch.
- **Latency.** A woken run spends about 1 s at p50 before the model's first token: E2B resume, sync-in and CLI start. A run kept warm while its conversation is open avoids it.
- **Direction 8.** When the sandbox vendor is down, runs stop. These keep working: files, rooms, the board, the mailbox, the MCP door, main agents over MCP, and code mode.
- **Owed.** The decision is checked against these live probes in the sandbox slice: resume after hours or days paused; E2B transforms on the MCP door and on a WebSocket; CLI memory for a 1 vCPU size; and #106037 (Claude scheduled tasks missing custom connectors) for main-agent Claude users.
- **The decision changes if** any of these happen:
  - a vendor withdraws hosted-CLI use for its subscribers (the per-source kill switch fails that source closed meanwhile);
  - E2B's p95 resume exceeds 1.5 s;
  - managed sandbox spend passes about $5k–10k a month (self-host Firecracker, as ADR-032 already says).

## Sources

- https://code.claude.com/docs/en/headless (read 2026-11-01)
- https://code.claude.com/docs/en/legal-and-compliance (via ADR-033)
- https://docs.e2b.dev/commands/background, https://docs.e2b.dev/sandbox/persistence, https://e2b.dev/pricing (read 2026-11-01)
- https://developers.cloudflare.com/durable-objects/platform/pricing (read 2026-11-01)
- pi-coding-agent 1.4.0 `docs/cli.md` (npm `@earendil-works/pi-coding-agent`)
- `openspec/changes/clean-sheet-platform/e2b-resume-measurement.md`
- `openspec/changes/clean-sheet-platform/subscription-engines.md`, `main-agents.md`, `engines-replan.md` (2026-11-01)
- ADR-023, ADR-031, ADR-032, ADR-033
