# Harness architecture audit: why a user-buildable system keeps needing platform patches

Date 2026-10-11. Audited `origin/main` at `e603aafd618`, read-only. Two
independent audits answered the same question: this one (Claude) and a Codex
audit (PR #4617, closed unmerged). Its findings are folded in below, so this
note is the single record. Step 1 is specified in
`openspec/changes/one-model-call-path/`.

## Decision (2026-10-11)

The founder, verbatim: "build towards the correct shape now, do not get
distracted by bad band-aids, if the correct clean shape is known build that,
then patch after, do not patch a known bad shape."

The steps, in order:

1. **One model-call path.** The owner's authority is the set of their
   connected sources. The broker meters every call. The caller's model choice
   is honoured. Every call returns a receipt.
2. **One box per owner.** Per-path cells are deleted as their paths go.
3. **Agents are files.** Child sessions run through `ta`.
4. **The thin loop is the only loop.** Native CLIs become explicitly named
   external-agent packages.
5. **Workflows and extensions are packages.**
6. **The UI is a package.**

These rules bind every step:

- **(a) Receipts.** Every model call returns a receipt that keeps the
  requested, resolved and provider-reported model as separate fields. Nothing
  infers the model from a title or from the answer.
- **(b) CLIs are not model calls.** A native Claude Code or Codex CLI cannot
  honestly be presented as one model response. Each ends as an explicitly
  named external-agent package over the generic job API.
- **(c) Net-negative.** Every implementation slice removes more executable
  core code than it adds, and its deletions ship in the same release. No
  parallel engines, and no per-account opt-in legacy paths.
- **(d) Settle once.** Retries and child calls link to the parent allocation,
  and each attempt settles exactly once.

Open band-aids for a known bad shape stop. For example, #4614's Codex-home and
effector-accounting parts are superseded by step 1; its lost-message fix is a
plain bug fix and lands as one.

## 1. The answer in one paragraph

We designed the right shape and shipped none of it as the only path.
`target-architecture` D6 already says it: one thin, vendor-neutral loop over
standard model protocols, with credentials and metering at the broker. On
main, that loop is an opt-in environment switch (`TINYASSETS_AGENT_LOOP=thin`,
`agent_loop/served_chat.py:26`) that production does not set. Every older path
is still live, and **the authority model allows one provider per owner**.
`router.call_with_policy` begins with `del policy, difficulty` because "the
owner's binding names exactly one provider" (`providers/router.py:1895-1902`).

So "call a different model" has no general path, and each caller grew its own.
The isolation cutover then hardened each of those paths separately, in about
ten kinds of cell. Most "bugs" are the seams between duplicate paths.

pi has one model-call function, one loop and one outer boundary, and builds
everything else (sub-agents, review, routing, UI) in user space. That is why pi
needs no patch to "review with GPT" and we do.

## 2. What pi actually is (sources accessed 2026-10-11)

`github.com/badlogic/pi-mono` redirects to
[earendil-works/pi](https://github.com/earendil-works/pi) (pinned at
`8a9c051f`, release v1.1.0 of 2026-10-07, MIT). Rationale:
[Zechner, 2025-11-30](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/);
parts of that essay are dated. For example, "no MCP" is obsolete.

| Layer | Primitive | Source |
|---|---|---|
| `pi-ai` | `stream`/`complete(model, context)` for every provider. `Model` is a plain object. `Context` is serializable, so the same context goes to another model mid-session. Every result carries `usage` and `cost`, even on error or abort. | [packages/ai/README.md](https://github.com/earendil-works/pi/blob/main/packages/ai/README.md) |
| `pi-agent-core` | One loop: call the model, run tools, repeat. Injectable `streamFn`, `transformContext`, events, steering and cancellation. | [packages/agent/README.md](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md) |
| Coding agent | Four tools (read, write, edit, bash). AGENTS.md and SYSTEM.md files. Sessions are JSONL trees; a model switch is a `model_change` entry. | `docs/session-format.md` |
| One extension unit | A TypeScript module that registers tools, commands, providers and renderers. `context` transforms the context; `tool_call` can block or modify a call. Skills, prompts and themes are files; packages install from npm or git. | [extensions](https://pi.dev/docs/latest/extensions), [packages](https://pi.dev/docs/latest/packages) |
| Your own UI | Print, JSON, `--mode rpc` and an in-process SDK; custom UIs drive the same session. | [sdk](https://pi.dev/docs/latest/sdk), [rpc](https://pi.dev/docs/latest/rpc) |
| Another model | All user space, on the same registry, auth and cost path: (1) the `subagent` example spawns `pi --mode json -p --model <m>` from a markdown agent file (`model:`, `tools:`); (2) an extension calls `ctx.modelRegistry.streamSimple(otherModel, …)` and reports the nested usage; (3) pi runs itself through bash. | [examples/extensions/subagent](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent) |
| Security | No boundary inside the app. Run it in a container and let a proxy swap credentials at the edge. Extensions have the process's rights. | [security](https://pi.dev/docs/latest/security) |

**What pi leaves out** ships as example extensions instead: sub-agents, plan
mode, to-dos and permission popups. It also has no multi-user custody,
enforced budgets, cloud uptime or cross-user isolation.

**Do not idealize pi.** Pi Durable (2026-10-01) is experimental and separate
from the coding agent. Adopt its commit-before-publish idea, but do not add it
as a third runtime. Pi's low-level imports can bypass provider auth; our
governed call must not expose such a bypass. pi also contains vendor adapters,
so the lesson is a stable protocol boundary with vendor code below it, not
zero vendor code.

## 3. What we said the harness would be

README Direction 2-4 (four tools plus `ta`, one extension unit, one agent
definition, no provider code, any model source); ADR-006 (owner credentials
only); ADR-007 (an LLM is a connection, explicit choice wins, default to the
strongest source); ADR-012 and `target-architecture` D6 (one sealed box,
credentials and metering in the broker, a thin loop); ADR-013 (ship the
primitive, not the operator); ADR-014 (an agent is a node running the
`converse` turn, with a per-node model override); the 2026-10-04 pi gap
audit; and the 2026-10-06 Muse-fit note. Every one points the same way. The
code does not.

## 4. What we actually have

`tinyassets/` is 318k lines of Python in 686 files. A byte-for-byte mirror of
about the same size sits under `packaging/claude-plugin/.../runtime/`.

| Area | Approx. lines |
|---|---|
| Model calls and provider authority (`providers/` alone is 19k) | 44k |
| Platform-built features (market, wiki, bid, payments, knowledge, memory) | 42k |
| Workflow and graph engine (`runs.py` 8.1k, `graph_compiler.py` 4.5k) | 40k |
| MCP servers | 21k |
| Connections, broker, vault | 19k |
| Isolation cells (33 `role_*` modules) and boxes | 19k |
| Agent definitions (`custom_agents.py`, `agent_interchange.py`) | 12k |
| Extensions, packages, skills, UI bridge and `ta` (what users build from) | 7k |

The layer the Direction calls the product is the smallest.

### Duplicate paths

| Job | Ways it is done today | Where |
|---|---|---|
| Call a model | (1) Claude or Codex CLI in a provider cell; (2) HTTP API-key source through the broker; (3) workflow prompt node through `call_with_policy`; (4) agent node running a full turn; (5) MCP sampling for action review; (6) `ta connection:<id>:POST` to a model endpoint, which is refused | `claude_provider.py`, `codex_provider.py`, `api_key_http_provider.py`, `graph_compiler.py:545-604, 1735-1800`, `workflow_agent.py`, `ta_capabilities.py:410` |
| Chat ingress | The installed `turn_consumer` path versus `universe_intelligence.converse` | `universe_server.py:3140-3185` |
| Agent loop | Each CLI's native loop, the HTTP served loop, and the thin loop (off in production) | `agent_turn_coordinator.py:569-600`, `agent_loop/served_chat.py` |
| Admit a model call | Foreground, background, cloud continuation and assigned queue, each with a "default" and a "manifest" scope | `foreground_run_provider.py:540-570, 721-813`, `background_served_provider.py:756-954`, `cloud_automation_continuation.py` |
| Choose a model | One-provider owner binding; fixed roles (writer, judge, extract, embed); `FALLBACK_CHAINS`; per-node `llm_policy`; `served_model_plan`; agent `settings.yaml`, whose `model_choice()` has no runtime caller | `router.py:243, 1902`, `harness_settings.py:103` |
| Open a parent allocation | Six callers construct `TurnRequestBudget` or `RunRequestAllocation` themselves; the router reserves per attempt | `agent_turn_coordinator.py:464`, `foreground_run_provider.py:288-295`, `background_served_provider.py:748`, `universe_intelligence.py:699, 1522`, `router.py:801`, `workflow_agent.py:112` |
| Meter an HTTP model call | Only `api_key_http_provider` attaches the usage envelope; any other POST to a model source raises `InferenceUsageRequired` | `storage/agent_request_usage.py:155-199` |
| Define an agent | SQLite rows, `AgentDefinition`, an interchange format, templates and `/u/agents` files | `custom_agents.py`, `agent_definition.py`, `agent_interchange.py` |
| Run another agent | Agent node, `invoke_branch`, `invoke_branch_version`, `await_branch_run`, addressed agents, activities | `graph_compiler.py:3958-4094`, `addressed_agents.py`, `agent_activities.py` |
| Load an extension | v1 workspace executables and v2 activated revisions; hooks observe and cannot transform the model input | `ta_cli.py:54-105`, `extension_hooks.py:114-142` |
| Isolate owner work | About ten cell kinds, each with its own admission, plus the box | `tinyassets/role_*.py`, `deploy/role_*.py`, `boxes/` |

Some differences are legitimate and stay: authentication versus authorization;
consent and revocation checks the OS cannot enforce; control-plane timers; and
the protected approval surface.

### Provider-specific code (against Direction 2)

Vendor names in `tinyassets/*.py`: codex 1,063 hits in 125 files; claude 360
in 62; openai 127 in 25. The structural cases are `FALLBACK_CHAINS`
(`router.py:243`), `_build_fallback_router` registering adapters by name
(`providers/call.py:217-267`), `_CLI_REFS`, `_CLI_PROTOCOLS` and
`_CLI_PROVIDER_CLASSES` (`api/compute_connection.py:42`,
`providers/definition.py:54`, `providers/provider_resolver.py:34`),
`codex_launch_contract.py`, `onboarding/openai_device.py`, and both CLIs
installed in the platform image. Provider names in preset JSON are data, not
a violation; Python that dispatches on them is.

**Fail-loudly breach:** a prompt node with no provider returns
`"[Mock response for <node>]"` (`graph_compiler.py:1741`), and `call_provider`
still returns a mock string under `_force_mock` (`providers/call.py:440`).

### Why the review step failed, traced

1. **Codex is not a tool.** The agent's bash has no install tree, no network
   and a cleared environment (`universe_tools.py:42-46`). Codex exists only as
   a chat provider in a provider cell, and it needed Codex-only fixes to run
   there (#4614).
2. **HTTP to a model source is refused.** `InferenceUsageRequired` fires for
   any POST to a model-source connection without the envelope only
   `api_key_http_provider` attaches. The text at `engine_mcp_server.py:1745-1763`
   then steers the agent to a prompt node: a band-aid written into the prompt.
3. **The node's model choice is mostly discarded.** `call_with_policy` deletes
   the policy. Only the foreground "manifest" scope reads a preferred provider,
   and the "default" scope refuses one. So the choice survives only when
   several sources are bound and the node pins an exact model. Codex rightly
   notes that we did not read this specific run's receipts. Which of these
   cases it hit is unproven, and a receipt per call (rule a) is what would
   prove it.

## 5. The band-aids, classified

Classes: (a) a real bug in a single correct path; (b) duplicate paths or
special-casing; (c) the platform building what a user should; (d) fallout from
the isolation cutover. Both audits classified these PRs independently and
agree.

| PR | Class | Why |
|---|---|---|
| #4576 procfs ESRCH killed healthy cells | d (a) | Process accounting treated "already gone" as failure |
| #4577 revocation on stdin close | d (a) | A shared socket turned stdin close into end of output |
| #4578 provider-cell resets, relay half-close | d (b) | Discovery and execution validated argv differently |
| #4579, #4580 launch the shipped CLI path | d, b | Claude launched bare `claude`, Codex resolved through `which`; fixtures used absolute paths |
| #4581 tool cells, all four tools | d (a) | The decoder counted 598 mounts against 256 before filtering to 40 |
| #4584 run-actor owner admission | d (b) | The same admission fix across about ten cell kinds |
| #4596 isolated HTTP, image reads | d (b) | The HTTP path has its own "is this a model call?" check |
| #4597, #4599 daemon writer custody, wiki folders | d (c) | About twelve platform features wrote into owner space |
| #4613 disk exhaustion (merged) | a | Ops bug |
| #4614 owner provider workflows (open) | b (d) | Codex-only home and sandbox cases, plus a third model-call entry in the HTTP effector. Its lost-message fix is (a) |
| #4615 Needs-you requests in native apps | a | One routing bug in `app.html` |
| Founder's review gap | b (c, d) | Six model routes, one-provider authority, accounting tied to one adapter |

**Pattern.** Only #4613 and #4615 are plain single-path bugs. Most of the
rest are (d), and (d) has a cause: the cutover put a boundary around each path
and each platform feature, not around the owner. So every duplicate became one
more cell to harden.

## 6. The target core

This is `target-architecture` D6 made the only path. The new piece is that the
owner's authority is their set of connected sources, not one bound provider.

```text
 any surface: bubble chat | custom UI (bridge = ta) | MCP client | schedule
                                   |
                     control plane (always on, no LLM)
                 schedules, triggers, inbox, run journal, budgets
                                   |
       session / durable job / child session  <--- agent files, packages
                                   |
         ONE model call: call(model_ref, context, parent) -> stream + receipt
           resolve model_ref against the owner's connected set; explicit
           choice wins, declared fallbacks only, default = strongest;
           reserve on the parent allocation; transport; settle once
                     |                                    |
        broker (THE edge): credentials,         tool calls --> owner's box
        ADR-006 owner check, metering           read/write/edit/bash + `ta`
                                                external-agent packages
                                                (native CLIs) via the job API
```

The six primitives, built in the decision's order:

1. **One model call.** Chat turns, workflow steps, child sessions, extensions
   and `ta` all use `call(model_ref, context, parent)`. Its receipt carries the
   requested, resolved and provider-reported model, usage with its provenance,
   and an outcome that may be `unknown`. Only an explicit fallback list permits
   a substitute. A raw HTTP POST to a known model API is refused with a message
   naming the model call. No caller reserves or settles.
2. **One box per owner.** Isolation attaches once, at execution admission;
   credentials and effects attach at the broker. Cross-owner checks the OS now
   enforces are deleted with their path. Do not reopen the finished UID
   migration.
3. **One agent definition: a file.** `agents/<name>/` holds instructions and
   settings (`model`, `tools`, `skills`). A child session is `ta agent run`;
   an agent node (ADR-014) is the same call. A reviewer is a file with
   `model: <connected GPT model>` and `fallbacks: []`.
4. **One loop.** The thin loop. A native CLI is never a hidden alternative
   implementation of the agent. It is an explicitly named external-agent
   package over the generic job API, with its own native-session evidence.
   Do not remove an owner's working subscription source until its replacement
   has parity in model, tools, session and usage.
5. **Workflows and extensions as packages.** v2 packages own tools, commands,
   typed context and tool-call transforms, and workflows; generic
   start/message/wait/cancel reuse the existing job records. The v1 loader,
   the Activities branch and the platform prompt/invoke/await node kinds
   convert once and are deleted. Durable runs stay platform primitives, but
   LangGraph is an internal checkpoint dependency, not a second model path.
6. **The UI as a package.** Isolated UIs get permission-bound `ta` calls and
   cursor-based events; the default layout is an ordinary package. Identity
   and consent surfaces stay first-party.

## 7. Delete or merge

| Delete or merge | Step | Approx. lines out |
|---|---|---|
| `call_with_policy`, `FALLBACK_CHAINS`, `call_provider`'s mock and fallback branches, `_build_fallback_router`, the graph policy retry, the default and manifest scope split, caller-side allocation, `InferenceUsageRequired` text, the prompt band-aid, the MCP-sampling reviewer, the mock prompt node | 1 | -1.5k net (see the step 1 spec) |
| About ten `role_*` cell kinds, each with its own admission; platform writers in owner space | 2 | -5k |
| `custom_agents.py` definitions, most of `agent_interchange.py`, templates, `agent_review.py` as a platform feature | 3 | -4k |
| `claude_provider.py`, `codex_provider.py`, `codex_app_server.py`, `codex_launch_contract.py`, `agent_chat_codec.py`, `native_jsonrpc_discovery.py`, provider and discovery cells, the CLIs in the platform image, the per-adapter native-tool restriction, the tool jail | 4 | -8k |
| v1 extension loader, Activities branch, `invoke_branch_version` and similar node kinds, `onboarding/openai_device.py` | 5 | -3k |
| Bespoke UI operation lists, persona and ordinary-chat heuristics, learning extractors, feature editors | 6 | varies |
| The mirrored plugin runtime in git (built at package time instead) | any | -318k tracked |

Package conversion preserves grants, provenance, branch IDs, run history and
in-flight jobs. Deleting old execution code never permits deleting user data.

## 8. Tradeoffs, and what not to copy from pi

- **pi's security model.** "YOLO" with in-process extensions. We are
  multi-user and hosted, so the box plus broker is the boundary, and untrusted
  hooks never run in a privileged process.
- **Durability.** pi has none in its coding agent. Our control plane, run
  journal and schedules stay (Direction 8), but every model call inside them
  is the one call.
- **Subscription credentials.** Vendor terms may bind a subscription to the
  vendor's CLI. That is why rule (b) keeps CLIs as external-agent packages
  rather than pretending they are model calls. A CLI run costs about 300 MB of
  box memory; a thin-loop turn costs about 1 MB.
- **Native features.** Removing CLI transports loses Codex `native_resume` and
  CLI compaction. The thin loop must own resume and compaction first.
- **Usage trust.** Metering from the response's `usage` trusts the provider's
  report. Budgets reserve before the call and settle after. An opaque
  harness's usage carries its provenance, and unknown never means zero.

## 9. Open questions

1. Can the broker parse usage from streamed responses for every protocol we
   accept? `broker-streaming-contract` is still open, so step 1 settles from
   buffered responses first.
2. Which subscription sources allow a single-response transport with tools and
   usage without exposing credentials to owner code? The answer decides which
   sources become external-agent packages in step 4.
3. Is the step 2 box the shipped owner-cell backend behind the `BoxProvider`
   seam, or Firecracker/gVisor? ADR-012 and the 2026-10-08 cutover decision
   must be reconciled before step 2's spec.
4. Which `custom_agents` rows hold data with no file form (grants, bindings),
   and where does it live once agents are files?
5. Should the 42k lines of platform-built features (market, wiki, bid) become
   packages under Direction 3? That is out of scope here, but it is the same
   question one level up.
