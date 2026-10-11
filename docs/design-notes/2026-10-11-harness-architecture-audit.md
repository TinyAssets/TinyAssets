# Harness architecture audit: why a user-buildable system keeps needing platform patches

Date 2026-10-11. Audited `origin/main` at `e603aafd618`, read-only. Research
and design only; nothing here changes code.

**Trigger.** The founder's app agent tried to add a cross-family review step
and failed three ways:
- Codex did not run inside the owner cells.
- Its workflow reached OpenRouter over a raw HTTP connection, and usage
  accounting refused the call.
- Its "Codex review" step ran on the default Claude model.

Open PR #4614 patches Codex-only and effector-only paths for this. The
founder's question: why does a pi.dev-style harness need a platform patch to
build an agent?

## 1. The answer in one paragraph

We designed the right shape and shipped none of it as the only path.
`target-architecture` D6 already says it:
- one thin, vendor-neutral loop speaking standard model protocols through the
  broker;
- CLIs only as command adapters inside the owner's box;
- credentials and metering at the broker.

On main, that loop is an opt-in environment switch (`TINYASSETS_AGENT_LOOP=thin`
in `agent_loop/served_chat.py:26`) that production does not set.

Every older path is still live, and **the authority model allows one provider
per owner**: `router.call_with_policy` begins with `del policy, difficulty`,
because "the owner's binding names exactly one provider"
(`providers/router.py:1895-1902`).

Three things follow:
1. "Call a different model" has no general path, so each caller grew its own.
2. The isolation cutover then had to harden every one of those paths
   separately, about ten kinds of cell, one at a time.
3. Most "bugs" are the seams between duplicate paths.

pi has one model-call function, one loop and one outer boundary, and builds
everything else (sub-agents, review, routing, UI) in user space. That is why
pi needs no patch to "review with GPT" and we do.

## 2. What pi actually is (sources accessed 2026-10-11)

The repo `github.com/badlogic/pi-mono` now redirects to
[earendil-works/pi](https://github.com/earendil-works/pi). Design rationale:
[Zechner, "What I learned building an opinionated and minimal coding agent", 2025-11-30](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/).

| Layer | Primitive | Source |
|---|---|---|
| `pi-ai` | `stream(model, context)` and `complete(...)` for every provider. `Model` is a plain object (`api`, `provider`, `baseUrl`, `cost`); a self-hosted model has the same type. `Context` is JSON, so you hand the same context to another model mid-session. Every result carries `usage` and `cost` at the one call site, even on error or abort. | [packages/ai/README.md](https://github.com/earendil-works/pi/blob/main/packages/ai/README.md) |
| `pi-agent-core` | One loop: call the model, run tools, repeat; no step cap; emits events. | [how-pi-works.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md) |
| coding agent | Four tools: read, write, edit, bash. AGENTS.md and SYSTEM.md files. Sessions are JSONL trees; a model switch is just a `model_change` entry. | same, `docs/session-format.md` |
| One extension unit | A TypeScript module: `registerTool`, `registerCommand`, `registerProvider`, `registerVirtualModel`, renderers; hooks on `before_agent_start`, `context`, `tool_call` (block or modify), `tool_result`, `agent_end`; UI calls `ctx.ui.*`. Skills, prompts and themes are files. Packages install with `pi install npm:/git:`, and project packages need a trust step. | [docs/extensions.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md), [docs/packages.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md) |
| Your own harness or UI | Interactive, print, JSONL, `--mode rpc` (stdin/stdout JSONL, with UI requests forwarded to the client), and an in-process SDK (`createAgentSession({model, tools, customTools, resourceLoader})`). Custom UIs drive the same session. | [docs/sdk.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md), [docs/rpc.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) |
| Another model | Three user-space routes, all on the same registry, auth and cost path chat uses: (1) the example `subagent` extension starts `pi --mode json -p --model <m> --tools <list>` from an agent markdown file (`reviewer.md`: `model:`, `tools:`); (2) an extension calls `ctx.modelRegistry.complete(otherModel, …)`; (3) "ask pi to run itself via bash". | [examples/extensions/subagent](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions), blog |
| Security | No boundary inside the app: the whole process is the unit. Run it in a container, VM or sandbox, and let a proxy substitute provider credentials at the network edge. | [docs/security.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md) |

**What pi deliberately leaves out**, each shipped instead as an example
extension: built-in sub-agents, plan mode, to-dos, permission popups,
background bash and, originally, MCP (MCP has since been added). Also outside
the core: multiple users, hosted 24/7 running (`pi-durable` and `pi-server`
are experimental), mobile, a web chat app, and any workflow or graph engine.

## 3. What we said the harness would be

- **README Direction 2-3:** four tools plus `ta`, one extension unit, one
  agent definition for every provider, no provider code, abilities as files.
- **ADR-006:** owner credentials only.
- **ADR-007:** an LLM is just another connection, and a "user-configured
  command adapter" is a connection primitive.
- **ADR-012 / `target-architecture`:** one sealed box per command center;
  credentials and metering in the broker; D6 thin loop with a CLI only as a
  command adapter in the box.
- **ADR-013:** ship the missing primitive, never the operator.
- **ADR-014:** an agent is a node running the same turn as `converse`.
- **`universe-agent-harness` §4.7:** exactly pi's four tools; breadth through
  `ta search/describe/call`.
- **2026-10-04 pi gap audit:** we have none of pi's four extension properties
  (loop hooks, tool and command registration, UI hooks, one-command install).
- **2026-10-06 Muse-fit note:** Muse-level abilities ship as the starter's
  package, not platform code.

Every one of these points the same way. The code does not.

## 4. What we actually have

**Total size.** `tinyassets/` is 318k lines of Python in 686 files. A
byte-for-byte mirror of about the same size sits at
`packaging/claude-plugin/.../runtime/`, kept in step by
`scripts/check_mirror_parity.py`.

| Area | Approx. lines |
|---|---|
| Model calls and provider authority (`providers/` is 19k on its own) | 44k |
| Platform-built features (market, wiki, bid, payments, knowledge, memory, ingestion) | 42k |
| Workflow and graph engine (`runs.py` 8.1k, `graph_compiler.py` 4.5k) | 40k |
| MCP servers | 21k |
| Connections, broker, vault (`storage/outbound_connections.py` 6.0k) | 19k |
| Isolation cells (33 `role_*` modules, 7.9k) and boxes | 19k |
| Agent definitions (`custom_agents.py` 2.6k, `agent_interchange.py` 1.8k) | 12k |
| Extensions, packages, skills, UI bridge and `ta` (the layer users build from) | 7k |

The layer the Direction calls the product is the smallest.

### Duplicate paths

| Job | Ways it is done today | Where |
|---|---|---|
| Call a model | (1) Claude or Codex CLI in a provider cell. (2) HTTP API-key provider through the broker. (3) Workflow prompt node through the router. (4) Agent node running a full turn. (5) MCP sampling for review. (6) `ta connection:<id>:POST` to a model endpoint, which is refused. | `providers/claude_provider.py` (1.3k), `codex_provider.py` + `codex_app_server.py` + `codex_launch_contract.py`, `api_key_http_provider.py`, `graph_compiler.py:1401-1800`, `workflow_agent.py`, `ta_capabilities.py:410` |
| Agent loop | Each CLI's own native loop, the HTTP served loop, and the thin loop (opt-in, off in production) | `interactive_http_agent.py`, `agent_loop/served_chat.py` |
| Run the four tools | Per-call bubblewrap jail, box tools (thin loop only), or the CLI's own native tools restricted per adapter | `universe_tools.py`, `agent_loop/box_tools.py`, `codex_provider.py:321` |
| Admit a workflow's model call | Four authorities (foreground, background, cloud continuation, assigned queue), each with a "default" and a "manifest" scope | `foreground_run_provider.py:540-570, 1040-1110`, `background_served_provider.py:640`, `cloud_automation_continuation.py:2316`, `runtime/assigned_queue_consumer.py` |
| Choose a model | Owner binding (one provider), fixed roles writer/judge/extract/embed, `FALLBACK_CHAINS` naming claude-code/codex/ollama, per-node `llm_policy` (discarded by the router), per-agent selection | `router.py:243, 1902`, `background_served_provider.py:56`, `providers/model_policy.py` |
| Meter a model call | Inside one adapter (`api_key_http_provider.py:454` attaches the usage envelope); every other route is refused or bespoke | `storage/agent_request_usage.py:155-191` |
| Define an agent | A SQLite definition, a 63-line "one agent definition", an interchange format, templates, and `/u/agents` files | `custom_agents.py`, `agent_definition.py`, `agent_interchange.py` |
| Run another agent | Agent node, `invoke_branch`, `invoke_branch_version`, `await_branch_run`, wakes, addressed agents, activities, control-plane triggers | `graph_compiler.py:3125-3864`, `addressed_agents.py`, `activity_runner.py`, `control_plane/` |
| Isolate owner work | About ten cell kinds (provider, tools, node, git, remote git, decoder, video, preview, packages, content), each with its own admission and diagnostics, plus the box | `tinyassets/role_*.py`, `deploy/role_*.py`, `boxes/` |
| Review an action | The platform's built-in auto-review (same model only), or a user-built step | `agent_review.py` |

### Provider- and feature-specific code (against Direction 2-3)

Hits for vendor names in `tinyassets/*.py`:

| Name | Files | Hits |
|---|---|---|
| codex | 125 | 1,063 |
| claude | 62 | 360 |
| openai | 25 | 127 |
| anthropic | 15 | 55 |
| openrouter | 16 | 21 |

Structural cases:
- `router.py:243`, the named fallback chains.
- `providers/call.py:239-258`, which registers adapters by name.
- `codex_launch_contract.py`, and the feature-flag patch at `codex_provider.py:351`.
- `onboarding/openai_device.py`, a one-vendor sign-in flow.
- `Dockerfile:125-147`, which installs both CLIs into the platform image.
- `graph_compiler.py:1741`: a prompt node with no provider returns
  `"[Mock response for <node>]"`, against Fact 8 (fail loudly).

### Why the review step failed, traced

1. **Codex is not a tool.** The agent's bash has no install tree, no network
   and a cleared environment (`universe_tools.py:42-46`). Codex exists only as
   the owner's chat provider in a provider cell (`role_provider_cell.py`), and
   it needed Codex-only fixes to run there at all: a private home and its own
   inner sandbox disabled (#4614).
2. **HTTP to a model source is refused.** `InferenceUsageRequired` fires for
   any POST to a connection marked as a model source unless the request carries
   an envelope that only `api_key_http_provider` attaches
   (`agent_request_usage.py:168-191`). The instruction text at
   `engine_mcp_server.py:1744-1760` then steers the agent to a prompt node
   instead, a band-aid written into the prompt.
3. **The node's model choice is discarded.** `call_with_policy` deletes the
   policy (`router.py:1902`). Only the foreground "manifest" scope reads a
   preferred provider, and the "default" scope refuses one
   (`foreground_run_provider.py:566-568, 1083-1110`). Unless several sources
   are bound and the node pins an exact model, the step runs on the owner's
   one bound source: Claude.

## 5. The band-aids, classified

Classes:
- (a) a real bug in a single correct path;
- (b) duplicate paths or special-casing;
- (c) the platform building something a user should;
- (d) fallout from the isolation cutover.

Secondary class in parentheses.

| PR | Merged | Class | Why |
|---|---|---|---|
| #4576 procfs ESRCH killed healthy cells | 10-10 | d (a) | The accounting reader treated "process already gone" as an accounting failure |
| #4577 premature cell revocation on stdin close | 10-10 | d | The cell's single shared socket turned a stdin close into end of output |
| #4578 provider-cell resets, relay half-close | 10-10 | d (b) | Discovery and execution validated argv differently; egress forwarder bug |
| #4579 owner-cell chat on production data | 10-10 | d (b) | Claude launched bare `claude`, while Codex resolved through `which` |
| #4580 launch the shipped claude path | 10-10 | b (d) | Same launch split; fixtures used absolute paths, production did not |
| #4581 tool cells, all four tools | 10-10 | d | The decoder counted 598 mounts against a 256 limit before filtering to 40 |
| #4582, #4583 | 10-10 | docs | Direction and research notes (#4582 leans toward c) |
| #4584 run-actor owner admission, tool-cell web | 10-10 | d (b) | The same admission fix applied across about ten cell kinds |
| #4596 isolated HTTP connections, image reads | 10-10 | b (d) | The HTTP path has its own "is this a model call?" check |
| #4597 daemon writer custody | 10-11 | d (c) | About twelve platform features wrote into owner space as the platform user |
| #4599 daemon-owned wiki folders | 10-10 | d (c) | A platform wiki feature broke every tool for that owner |
| #4613 disk exhaustion, grant refusal text (open) | — | a (b) | Ops bug; also touches the HTTP model-detection path again |
| #4614 owner provider workflows (open) | — | b (d, c) | Codex-only home and sandbox cases, plus a second accounting path in the HTTP effector. Its lost-message fix is (a) |
| #4615 Needs-you requests in native apps | 10-11 | a | One routing bug in `app.html` |
| Founder's review gap | — | b (c, d) | Six model routes, one-provider authority, accounting tied to one adapter |

**Pattern.** Only #4615 and the ops half of #4613 are plain single-path bugs.
Ten of the 13 code PRs are (d). But (d) has a cause: the cutover put a
boundary around each path and each platform feature, not around the owner. So
every duplicate became one more cell to harden.

Other recurring causes:
- **Separate launch code per provider:** #4578, #4579, #4580 and part of
  #4614.
- **Accounting per route rather than per call:** #4596, #4613 and #4614.
- **Platform features writing into owner space:** #4597 and #4599.

## 6. The target core

The shape below is what `target-architecture` D6 already decided, made the
only path. The new piece is replacing the one-provider owner binding with
"the owner's set of connected sources".

```text
 any surface: bubble chat | custom UI (bridge = ta) | MCP client | schedule/trigger
                                   |
                     control plane (always on, no LLM)
                 schedules, triggers, inbox, run journal, budgets
                                   |
                ONE loop: run(agent_file, input, model_ref)
                 model_ref = (source, model) from the owner's connected set;
                 default = strongest; an agent file or call may name another
                     |                                   |
        model call --+--> broker (THE edge)              +--> tool calls --> owner's box
        standard wire protocols,                              read/write/edit/bash + `ta`
        credential injection, METERING                        binaries, skills, extensions,
        from response usage, budget,                          command adapters (codex, claude
        ADR-006 owner check                                   CLIs) whose egress also
                                                              goes through the broker
```

Six primitives:

1. **One model call.** Every caller (chat, workflow step, sub-agent,
   extension, a raw `ta` connection POST) uses
   `call(model_ref, context) -> stream + usage`.
   - Sources are connections: API key, OAuth, local endpoint, or a command
     adapter.
   - The owner's authority is the set of connected sources, not one bound
     provider. A call names a source or takes the default.
2. **Accounting and isolation attach once.**
   - Isolation is the box: everything owner-side runs in it, including CLIs.
   - Credentials, ADR-006 checks, metering and budgets live at the broker.
     Usage is read from the standard wire protocol's `usage` field, so the
     parser depends on the protocol, never the vendor. If usage cannot be
     parsed, the call is still recorded and flagged; it is never refused.
   - This is pi's "the container is the boundary, the proxy swaps
     credentials", made multi-user.
3. **One loop.** The thin loop. A CLI is never a chat transport. It is a
   binary in the box that the agent runs through `bash` (`codex exec …`),
   exactly as pi's blog says.
4. **One agent definition: a file.**
   - The file is `agents/<name>.md` with frontmatter (`model`, `tools`,
     `skills`).
   - A sub-agent is `ta agent run <file> [--model]`.
   - An agent node (ADR-014) is the same call made from a graph.
   - "Codex review" becomes `agents/reviewer.md` with
     `model: <openrouter-or-openai source>/gpt-…`, or a skill that runs
     `codex exec` in the box. Either way, no platform patch.
5. **One extension unit.** `extension.json` grows pi's hooks (`tool_call`,
   `context`, `before_agent_start`, `agent_end`), command registration and UI
   contributions, running in the box with the launch's grant. The UI bridge
   exposes exactly `ta`.
6. **Durable runs stay.** The graph runtime, the control plane and the run
   journal are what pi lacks and Direction 8 requires (24/7 with zero hosts).
   - Node kinds shrink to four: `agent` (the loop; zero tools covers today's
     plain prompt), `code` (sandbox), `call` (a `ta` capability or
     connection) and `subgraph`.

## 7. Delete or merge

| Delete or merge | Into | Approx. lines out |
|---|---|---|
| `InferenceUsageRequired` refusal, the per-adapter usage envelope, and the prompt band-aid at `engine_mcp_server.py:1744` | Broker metering of every model-source request | -0.5k |
| One-provider owner binding, `del policy`, `FALLBACK_CHAINS`, fixed writer/judge/extract/embed roles, default/manifest scopes across four admission modules | One `model_ref` resolution against the owner's source set | -3k |
| `claude_provider.py`, `codex_provider.py`, `codex_app_server.py`, `codex_launch_contract.py`, `agent_chat_codec.py`, `native_jsonrpc_discovery.py`, provider cell and discovery roles, CLIs in the platform image | Thin loop plus command adapters in the box | -6k |
| Native-tool restriction per adapter; the per-call tool jail once boxes are default | Box tools | -2k |
| `custom_agents.py` SQLite definitions, most of `agent_interchange.py`, templates | Agent files (export is a copy) | -3.5k |
| `agent_review.py` platform auto-review; MCP-sampling review | A starter skill or agent file the user edits | -0.4k |
| About ten `role_*` cell kinds, each with its own admission and diagnostics | One box exec with one admission | -5k |
| Platform features writing owner space (wiki folders, soul history, goal_pool, `fantasy_daemon` phases, bid logs) | Starter or commons packages, or platform state outside the box (`owner-visible-platform-writers`) | varies |
| Mock prompt-node response (`graph_compiler.py:1741`) | A loud error | ~-5 |
| `onboarding/openai_device.py` | Generic OAuth device flow as data | -0.3k |
| `invoke_branch_version` and similar node variants | `subgraph` with a version pin | small |
| Mirrored plugin runtime in git | Built at package time | -318k tracked |

## 8. Migration order

Each step ships alone, is live-verified, and deletes more than it adds.
Foundation first (Direction 5).

1. **Meter at the broker; never refuse a model-source call.**
   - Broker metering from the response `usage` of every request to a
     model-source connection.
   - Delete the envelope requirement and the prompt band-aid.
   - This alone lets the founder's workflow call OpenRouter, with no
     special path.
   - **Replace #4614's effector-accounting and Codex-home parts with this
     step. Keep only its lost-message fix.**
2. **The owner's authority is a source set.** `model_ref` is honoured on every
   call, the four admission scopes collapse into one, and the roles and chains
   are deleted. A node's model choice then works.
3. **Agents are files; sub-agents use `ta agent run`.**
   - Migrate `custom_agents` rows to `agents/*.md`, and point agent nodes at
     the same call.
   - Move `agent_review` into the starter package.
   - Proof: the app agent builds a GPT reviewer itself, observed only (memory
     "agent ships alone").
4. **The thin loop is the only loop.**
   - Flip the default, then delete the CLI chat transports.
   - Codex and Claude CLIs become command adapters that run in the box, with
     egress through the broker (`target-architecture` D6 base-URL mode and the
     file-OAuth lease).
   - Depends on `broker-streaming-contract`.
5. **One box per owner.** Collapse the `role_*` cell kinds into box exec, and
   move the remaining platform writers out of owner space. This is the
   ADR-012 cutover finished, rather than ten cells hardened.
6. **Extension hooks and UI bridge = `ta`** (pi gap audit L11-L13). Only now
   does "users build any harness" become literally true. Before step 4 it
   would add a fourth loop to hook into.

Steps 1-3 are small and unblock the trigger case. Step 4 is the large
deletion. Steps 1-2 touch the floor (ADR-015: authority and money), so each
needs one cross-family review round.

## 9. Tradeoffs, and what not to copy from pi

- **Do not copy pi's security model.** "YOLO", in-process extensions with full
  rights. We are multi-user and hosted, so the box plus broker is the boundary
  and the protected approval sheet stays first-party. pi delegates this to
  "run it in a container"; we are that container.
- **pi has no durability or 24/7 operation.** Our control plane, run journal
  and schedules are genuine platform primitives (irreducible under ADR-013).
  Keep them, but make every model call inside them the one call.
- **Subscription credentials.** pi uses vendor OAuth tokens directly from its
  own loop. Vendor terms may bind a subscription to the vendor's CLI
  (`target-architecture` D6 keeps the Claude subscription CLI behind an
  owner-only setting). The command-adapter-in-box route respects that, at
  about 300 MB of box memory per CLI run against about 1 MB for a thin-loop
  turn.
- **Deleting the CLI transports costs native features** such as Codex
  `native_resume` and CLI-side compaction. The thin loop must own session
  resume and compaction (pi's JSONL tree is the model).
- **Mobile and the app shell** have no pi analogue. The UI bridge = `ta` rule
  keeps them a client of the same primitives.
- **Metering from response `usage`** trusts the provider's report. Budgets
  must reserve before the call and settle after.

## 10. Open questions

1. Can broker metering parse usage from streamed responses for every wire
   protocol we accept? `broker-streaming-contract` is still open, so step 1
   may need buffered settlement first.
2. Is the box (`target-architecture` S-slices) ready enough for step 5, or
   does step 5 wait while steps 1-4 proceed on today's cells?
3. Which `custom_agents` rows hold data with no file form (grants, bindings),
   and where does that data belong once agents are files?
4. Should `codex exec` from bash be allowed for file-OAuth sources before the
   lease protocol (D6) ships, or must API-key or OpenRouter routes serve
   cross-family review until then?
5. Should the 42k lines of platform-built features (market, wiki, bid) shrink
   or become packages, given Direction 3? That is out of scope here, but it is
   the same question one level up.
