# Workflow — Design & Backend-Wiring Handoff

**For:** the app-building AI (Polsia) constructing a front-end / app on top of Workflow
**From:** Jonathan Farnsworth (project owner)
**Date:** 2026-06-23
**Purpose:** Give you (1) the full project design picture — what exists, what is half-built, and where the end-state is going — and (2) a precise spec for wiring an app to the **live Workflow MCP connector as its backend**, running live.

> Read this as structured prose, not a rigid machine spec. The wiring block in Part C is the part to treat as a hard contract; everything else is context so your app aligns with where the design is heading rather than just today's surface.

> **STATUS UPDATE (2026-06-23):** The 5-handle surface collapse described below is now being actively driven to completion on the **live** connector — filed as **PR-178** and specced as the checked-in OpenSpec change `collapse-live-mcp-surface-to-5-handles`. Target: the live `tinyassets.io/mcp` server (`universe_server.py`) will expose exactly `read.graph` / `write.graph` / `run.graph` / `read.page` / `write.page`, routed to today's action handlers, with the legacy tools dual-registered for one release. **Build the app against the 5 handles as the target shape**, but until the cutover ships and passes the public canary, call the current tools/actions in Part C. This doc will be revised the moment the handles are live.

---

## PART A — What Workflow Is (the design picture)

### A.1 One-sentence positioning
Workflow is a **goal-completion engine**: a user puts *any input* into a chat (text and/or files), their chatbot understands the goal and reaches for the Workflow connector, and the user gets *any desired output* back — even if the platform has to evolve in real time to make it possible. Time horizons range from instant (a recipe card) to **months-long, real-world coordinated efforts** (e.g. a research paper that actually gets published, with every interim deliverable and tracking metric along the way).

The chatbot is the **interpreter**; Workflow is the durable **substrate** it reaches for. Short-horizon work can be chatbot-mediated; long-horizon work cannot — a chatbot session can't be the durable layer for a goal that spans months. That forcing function drives the whole architecture.

It is **domain-agnostic**: live universes today include fantasy-novel authoring, an Earth-transition model (`earthos`), a research-paper publication goal, a grandma's bread recipe, a team standup tracker, and the platform's own self-development loop (`patch-loop-live`). Fantasy authoring was deliberately chosen as the *first benchmark domain* (hard to measure = good proving ground), never the trunk.

### A.2 The mental model: Goal · Branch · Daemon · Universe
- **Goal** — a durable, shared *intent* many workflows can serve. Each Goal carries a real-world **outcome-gate ladder** (e.g. draft → peer review → submission → acceptance → citations).
- **Branch** — a *workflow graph*. The unit users fork, iterate, run, publish, and bind to a Goal. This is the core thing your app will create/read/run.
- **Daemon** — a summonable, forkable, soul-bearing agent identity that runs a branch, often long-lived ("summoning").
- **Universe** — a self-contained workspace (a top-level Scope) for one multi-step workflow. Universes are isolated from each other by a hard memory-scope boundary. Each can have its own `soul.md` (intent), its own loop branch, its own voice, its own retrieval lens.

### A.3 The substrate: 6 primitives + 5 MCP handles (the stable contract)
This is the single most important architectural fact, **host-locked 2026-05-06**. The substrate vocabulary is finite and intended to be frozen:

**6 primitives**
1. **Node** — atomic unit of work (prompt template, sandboxed source-code, external call, or sub-scope invocation), with declared input/output ports.
2. **Edge** — directed reference carrying data (port→port) and/or control (a condition).
3. **State** — named, typed, scoped storage with a reducer (last-write-wins / append / sum / max / custom).
4. **Scope** — identity-bounding container (graph defs, runs, sub-graphs, universes, projects, orgs, sessions are all Scopes).
5. **Run** — an instantiated Scope at time T, with history.
6. **Trigger** — an explicit cause (input-port fill / schedule / event / parent-start / external call).

**5 MCP handles** (three verbs × two object types — the *entire* intended user-callable surface):
- `read.graph` / `write.graph` / `run.graph` (the compute dimension)
- `read.page` / `write.page` (the brain/knowledge dimension)

**The no-more-primitives rule:** there is never a 7th primitive. Identity, Capability, Permission, Effect, Channel, Schedule, compliance frameworks — everything else — is expressed either as composition of the 6 primitives or as a **user-authored "brain convention" ranked by adoption**. Mental model: lambda calculus (3 primitives) computes anything; this is the workflow-graph equivalent. "Substrate gets done; brain never does."

> **Critical wiring caveat:** the *live* MCP surface today still exposes ~7 coarse tools with ~50+ legacy actions (see Part C). The clean `read/write/run.graph + read/write.page` collapse is the **target** and is now being actively driven to completion on the live connector (tracked as **PR-178** + the `collapse-live-mcp-surface-to-5-handles` OpenSpec change), but it is **not yet the live wire format**. **Design your app against the 6+5 conceptual model, but call the actual tools/actions documented in Part C.**

### A.4 The "living organism" framing
The project models itself as an organism: the **loop is the body**, the **brain is the brain** (an LLM-maintained knowledge wiki — the same MCP), and **chatbots are organs** (any connected chatbot is a sensory/motor organ acting through the connector). The health metric is **cheat-rate → 0**: how rarely a human operator must hand-patch instead of the system evolving itself through user actions. Direct Python/YAML patches are treated as *interim cheats* that flag a missing primitive.

### A.5 The brain (knowledge substrate)
A 42-module brain (~12.3K LOC across 6 subsystems: Memory, Knowledge, Retrieval, Learning, Ingestion, Storage). It does 3-tier memory (Core → Episodic → Archival), 5-tier orthogonal scoping `(universe, goal, branch, user, node)`, and hybrid retrieval (HippoRAG + RAPTOR + Leiden communities + LanceDB vectors). The newest direction (June 2026) moves the brain onto the **Open Knowledge Format (OKF)** — markdown + YAML frontmatter, file path = identity, links = the graph — and gives **each universe its own brain (its own OKF bundle) nested inside one shared umbrella brain**. Your app should treat knowledge as `read.page`/`write.page` over the `wiki` tool; do not reach into brain internals.

### A.6 The economy (metabolism)
A treasury model self-regulates spend: `treasury_budget = sum(current monthly provider cost) + $10 buffer`, an append-only cost ledger, autonomous spend within budget, and propose-on-overflow via a host-approval gate. Schema exists; live cost tracking + autonomous spend are **not yet wired**. Currency is "Destiny (tiny)"; only `test tiny` on Base Sepolia is active — do not present mainnet settlement as live.

### A.7 How users are meant to build (fork-first)
1. **Find** a Goal matching intent. 2. **Fork** that Goal's canonical starter branch at a content-addressed version (fork over from-scratch — this is enforced "fork-first discipline"). 3. **Iterate** in the user's own universe (`patch_branch` / `update_node` edits in place). 4. **Run / compose / choose per-node LLM**. 5. **Publish** a new version bound to the Goal; eventually **attest** real-world gate events. The governing test for any change is *"Could a user have built this through MCP primitives alone?"* ("Mark" = the canonical user without repo access).

---

## PART B — What's Built vs Not (align to the trajectory, not the snapshot)

The design is **deliberately ahead of the implementation**, and the project tracks the gap openly. Build so the aspirational pieces can land *underneath* your app without forcing a rewrite.

| Dimension | End-state design | Reality today (2026-06-23) |
|---|---|---|
| MCP surface | 6 primitives, 5 handles | ~7 tools / ~175 legacy actions on the wire; collapse being driven to live under PR-178 + OpenSpec change `collapse-live-mcp-surface-to-5-handles` |
| Goal-completion | any input → any output, months-long | proven at recipe/day scale; multi-month probe not yet run |
| Real-world gates | world-event-attested ladders | gate tooling exists but flag-gated (`GATES_ENABLED`); ingestion pending |
| Treasury / economy | live ledger + autonomous spend | schema only; tracking + spend unbuilt |
| Per-universe souls | each universe = own soul + brain | **DELIVERED** (PR-139, merged 2026-05-28) — strongest foundation |
| OKF + nested brains | auto-conformant bundles, shared brain | direction only (June 2026); migration not shipped |
| Source-code node sandbox | microVM/gVisor isolation + identity | **frontier gap** (PR-144): source nodes run bare-metal `exec()`; approval handler partial; identity defaults to `anonymous` |
| Branch-as-first-class | branches readable/diffable/forkable as State via MCP | proposed (PR-104); largely unbuilt |
| External-write effects | user branches emit real-world side effects/PRs | partial; effects gated by `effect_authority` + consent |

**Two load-bearing not-yet-built items to know about:** **PR-144** (genuine new "Sandbox" primitive — until it lands, custom source-code nodes are unsafe/partly non-runnable) and **PR-104** (branch-as-first-class). If your app lets users author custom code nodes, assume that path is immature today; prefer prompt-template nodes and pre-approved branches.

**Delivered and dependable to build on:** per-universe souls + domain-neutral universe state, grant-based permissions (`.can(action, scope, context)` replacing a flat `is_host` boolean) with gradient OAuth scopes (read/write/costly/admin), the branch build/run/judge/publish/fork lifecycle, the wiki, the live daemon roster, and the change-loop read surface.

---

## PART C — Backend Wiring Spec (treat as a contract)

### C.1 Transport, endpoint, identity
- **Type:** remote **MCP server** (FastMCP, Streamable HTTP / SSE). Any MCP-capable client connects by **URL** and gets the same control-station surface. This maps cleanly onto Polsia's Claude-Code-subprocess agents, which natively consume remote MCP servers.
- **Public user-facing endpoint:** `https://tinyassets.io/mcp` — this is the **only** URL to use. (Do **not** use `mcp.tinyassets.io`; it is an Access-gated internal tunnel origin that returns 401/403 without Cloudflare Access headers.)
- **Server ID (as seen by an MCP host):** `e9d982fc-f6ec-46a0-ace5-b7c2249feba2`. Tools are namespaced `mcp__<server>__<tool>`.
- **Auth / identity:** GitHub OAuth is the single identity primitive at the MCP edge (OAuth 2.1 + PKCE), with per-user row-level security server-side. There is **no per-call bearer token in the tool arguments** — identity is resolved by the connector session. In single-operator dev, auth can be off (`UNIVERSE_SERVER_AUTH`). **Action item:** confirm with the owner how Polsia injects per-user identity, and plan for **OAuth Bearer / token-in-header at connect time**, plus **per-universe BYOK credentials** for any external effectors (X/Twitter etc. mandate the user's own keys — not a shared platform secret).
- **Versioning:** `get_status` returns `schema_version` (currently `1`). Pin to it; tolerate additive fields; breaking changes bump the version.

### C.2 Connect ritual (do this every session)
1. Call **`get_status`** first. Render its `caveats` and `evidence_caveats` **verbatim** to the user. Do **not** infer "secure", "local", or "idle" from `served_llm_type`, an `unknown` provider, or empty logs — those have explicit caveats saying they don't mean what they look like. Use `policy_hash` / `release_state.config_hash` for drift detection between polls.
2. Call **`universe action=inspect`** (and `action=list`) to orient — the connector's own instructions say to start here.
3. Respect `tier_routing_policy.tier_status_map`: only `host_request` / `user_request` / `owner_queued` are **live**; `goal_pool` / `opportunistic` are **stubbed**; `paid_bid` is **disabled**. Surface drift flags (e.g. `subs_but_pool_disabled`); don't silently retry.

### C.3 The 7 live tools
| Tool | Purpose | Dispatch |
|---|---|---|
| `get_status` | Identity + routing + health snapshot; load-bearing caveats | no `action`; optional `universe_id` |
| `universe` | Inspect/steer a universe: daemons, queue, premise/soul, canon, treasury, requests/directions | `action=` (44 actions) |
| `goals` | First-class shared intents + outcome-gate ladders + leaderboards | `action=` (17 actions) |
| `extensions` | The workflow-builder: design/edit/run/judge/publish/fork branches | `action=` (74 actions) |
| `gates` | Real-world impact claims per branch on a Goal's ladder | `action=` (15; needs `GATES_ENABLED=1`) |
| `wiki` | Prose knowledge wiki + bug/patch/feature/design intake | `action=` (17 actions) |
| `community_change_context` | Read the live GitHub change-loop queue (PRs/issues/runs) | `filter_text=` (no `action`) |

Tip: to discover any action-based tool's full catalog live, send a bogus `action` (e.g. `action="help"`) and read the returned `available_actions` list.

### C.4 The calls your app will use most

**Read / dashboard surface (safe to poll):**
- `get_status` → identity, `tier_routing_policy`, `supervisor_liveness.queue_state`, `release_state`, `storage_utilization`, `open_brain` cost ledgers.
- `universe action=daemon_overview` → **the one-call dashboard**: `{dispatcher, queue:{pending_count, top:[BranchTask...]}, subscriptions, bids, settlements, gates, activity_tail, run_state}`.
- `universe action=list` → universes `[{id, has_premise, has_soul, word_count, phase, phase_human, staleness, last_activity_at, accept_rate}]`.
- `universe action=inspect` → `{daemon:{phase,...}, soul:{purpose, domain_shape, loop_branch_def_id, effect_authority[]}, premise, pending_requests}`. (`effect_authority:[]` means dry-run only — no real side effects.)
- `universe action=daemon_list` → daemons + provisioned runtimes (provider/model/status).
- `extensions action=list_branches` → `[{branch_def_id, name, author, domain_id, goal_id, node_count, published, visibility, has_sandbox_nodes}]` (pass `scope=all|mine|published`).
- `extensions action=describe_branch` → **the one-call workflow view**: `{summary (enumerates Nodes, Edges, State schema), mermaid, valid, runnable, unapproved_source_code_nodes[], fork_from, fork_descendants[], related_wiki_pages[]}`.
- `goals action=list` → `[{goal_id, name, description, gate_ladder:[{rung_key,name,description}], canonical_branch_version_id, ...}]`.
- `wiki action=read|search|since|list` → prose knowledge. Wiki is large (~1,225 promoted pages + 245 drafts) — always paginate (`offset`/`limit`/`max_chars`) or use `search` / `since changed_since=<ISO>`.
- `community_change_context` (`filter_text=""|"queue"|"pr:N"|"issue:N"`) → open PRs / change requests / latest auto-fix runs.

**Write / mutate (gate behind explicit user intent):**
- Steer a universe: `universe submit_request` (queue a `scene_direction` or `branch_run`), `give_direction`, `set_premise`, `add_canon`, daemon lifecycle (`daemon_pause/resume/restart/summon/banish`, `control_daemon text=pause|resume|status`).
- Build/run workflows: `extensions build_branch | create_branch | add_node | connect_nodes | set_entry_point | add_state_field | validate_branch | patch_branch | update_node` (edits node source in place — don't rebuild whole branches), then `run_branch` (state via `inputs_json`) → `get_run | wait_for_run | stream_run | get_run_output` → `judge_run`. Publish/fork: `publish_version | fork_tree`. Recurring runs: `write_graph target=automation` (cron/interval); event triggers: `subscribe_branch`.
- Intent: `goals propose | update | bind | set_canonical | run_canonical`.
- Knowledge & intake: `wiki write | patch | file_bug (kind=bug|patch_request|feature|design) | cosign_bug` (`file_bug` does server-side dedup; no need to pre-search). Use `expected_sha256` on `patch`/`delete` for safe concurrent writes.

**Canonical build→run sequence for the app:**
`build_branch` (or `create_branch` + `add_node` + `connect_nodes` + `set_entry_point` + `add_state_field`) → `validate_branch` → `run_branch(inputs_json=...)` → `wait_for_run` / `stream_run` → `get_run_output` → optionally `judge_run` → `publish_version` + `goals bind`.

### C.5 Behavioral rules your app must honor
1. **Side effects are gated.** A universe with `effect_authority:[]` and `autonomous_spend_allowed:false` runs **dry-run**. Real external writes require `extensions grant_effector_consent` **and** a non-empty effect authority. `gates` actions need `GATES_ENABLED=1`; paid-market actions need `WORKFLOW_PAID_MARKET=on`.
2. **Large responses can blow token caps.** Always pass `limit` / `scope` / `max_chars` / `offset`; prefer `search` / `since` for the wiki and `describe_branch` over dumping whole graphs.
3. **The control station is for steering, not creating.** A chat surface that writes the *creative output itself* signals a missing daemon path — the daemon/branch does the work; the app inspects, steers, and runs.
4. **Don't add curated server features.** Anything a user could compose from the 6 primitives should be a branch or a brain convention, not a request for a new platform tool. Keep your app's "configuration" as user-authored branches/conventions, not hardcoded taxonomies.
5. **Assume minimal guardrails on the calling side.** Build idempotency, rate-limiting, and clear error surfaces into your integration; the connector enforces some gates but your app should be resilient to retried/over-eager agent calls.

### C.6 Polsia-specific fit notes
- Polsia agents are **Claude-Code CLI subprocesses that already use MCP integrations for live data**, so a remote MCP server is a native fit. The realistic wiring path is (a) register `https://tinyassets.io/mcp` as an MCP integration the agents call, or (b) have Polsia's codegen agent write a thin client against the remote MCP (Streamable HTTP/SSE) with OAuth/token auth.
- **Unverified:** whether Polsia's UI lets an end user paste an arbitrary third-party MCP URL today. "MCP integrations" is a confirmed platform capability; self-serve external registration is not confirmed. Support both paths.
- Polsia ingests **structured natural-language**, not a rigid schema file — which is exactly the form of this doc. Hand it this document plus the explicit wiring block (URL, transport, auth, tool/action list, example payloads) and clear **permissions / constraints / budget**, which Polsia treats as first-class inputs.
- Polsia's stack (Next.js + FastAPI + Postgres on Render/Neon) and its `SANDBOX_MODE` gating align well with Workflow's dry-run/effect-authority model — test wiring in no-effect mode first.

---

## PART D — Source Pointers (for deeper reading)

**Project repo:** `https://github.com/TinyAssets/TinyAssets` (MIT platform / CC0 catalog).
> Repo renamed Workflow -> TinyAssets after this handoff was written (2026-06-23);
> the rest of this document is a dated snapshot and its surface details have moved on.

**Canonical design docs (in the repo):**
- `PLAN.md` — design truth (thesis, scoping rules, ~30 design decisions, MCP interface contract). *Edits require host approval.*
- `AGENTS.md` — process truth, hard rules, full env-var reference, the "Forever Rule" (24/7 uptime).
- `STATUS.md` — live coordination board.
- `WebSite/04-deep-dive-v2-product-truth.md` — best single doc on what the product *is* ("real-world effect engine").
- `WebSite/08-design-direction-tiny-living-lens.md` — current site direction (platform personified as "Tiny"; live commons state on the page).
- `WebSite/DEPLOY.md`, `WebSite/PREVIEW.md` — site is a SvelteKit static build → GitHub Pages at `tinyassets.io`; dev preview at `http://localhost:5173/`.
- `BYOK_CREDENTIAL_VAULT_DESIGN.md` — per-universe encrypted BYOK credential vault (relevant to external effectors).
- `OUTREACH_CONTENT_ENGINE.md` — worked example of a capability composed as a forkable branch.
- `BRAIN_*.txt` (repo root) — the 4-part brain deep-dive (42 modules / 6 subsystems).

**Engine code (where the backend lives):** `workflow/universe_server.py` (the remote MCP), `workflow/api/*.py` (tool implementations), `workflow/branches.py` + `workflow/graph_compiler.py` + `workflow/runs.py` + `workflow/scheduler.py` (the loop/graph engine), `workflow/memory|knowledge|retrieval|learning|ingestion|storage/` (the brain), `workflow/api/wiki.py` (the wiki).

**Key wiki pages (read via `wiki action=read path=...`):**
- `pages/concepts/...-6-primitives-5-mcp-handles` — the locked substrate vocabulary.
- `pages/notes/...-triple-key-at-merge-2026-05-06` — the merge/governance gate.
- `pages/plans/substrate-cheat-migration-portfolio` — Mark's lane + the migration model.
- `pages/patch-requests/pr-104-branchasfirstclassprimitive-...` — branch-as-first-class.
- `pages/patch-requests/pr-144-make-source-code-genuinely-user-buildable-...` — the sandbox/identity frontier gap.
- `pages/patch-requests/pr-139-souled-universe-consolidation-program-...` — the delivered flagship.

---

### TL;DR for the app builder
Build a **control-station app** that connects to the remote MCP at `https://tinyassets.io/mcp` (OAuth/token auth, per-user/per-universe identity), polls `get_status` + `universe daemon_overview` for live state, lets users **find a Goal → fork its branch → iterate → run → publish**, and renders results — modeling every workflow as a **branch bound to a Goal, scoped to a universe**, accessed through the read/write/run-over-graph-and-page primitives. Treat the 6+5 substrate and goal-completion framing as the stable contract; assume gates, treasury, OKF, and the source-code sandbox land *underneath* you over time. Keep all "configuration" as user-forkable branches, not hardcoded features.
