# TinyAssets — Plan

How the system should work and why. Architecture, principles, and the working theory of every module. PLAN.md is the reference everyone — humans, AI provider sessions, user chatbots, and user-authored automations — consults before building, so that the applicable module's shape is known before code is written.

For how to work on the project, and where live state lives, see AGENTS.md. **Changes here require user approval.**

---

## Project Thesis

**TinyAssets is a Muse-level agent in the chat bubble, on pi.dev-style plumbing, that users build from** (founder, 2026-10-06). Each user's command center is the agent's harness and workspace; what users build there — workflows, goals, apps, agents — is shared, remixed and evolved in a public commons. The value is that evolving ecology.

No domain is privileged. Every Goal — research breakthroughs, novels, prosecutions, cures, open datasets, restoring a legacy app — stands in equal standing: each inherits the engine, not a topology. A reader should not be able to tell from the architecture which domain the engine was first exercised against. (Where the current code still privileges one domain as the default/only runtime, that is residue to remove, not design intent — tracked in `docs/audits/2026-06-24-fantasy-architecture-residue-audit.md`.)

The system should maintain explicit state across many cycles; use tools instead of one giant prompt; separate generation from evaluation from environmental truth; learn through durable artifacts, not hidden chat; and evolve itself as models and community practice improve.

The system should get simpler as models improve. Every scaffold is temporary unless evals prove it still earns its keep.

---

## Operating Principles

Founder-stated principles, kept in the founder's own wording. They shaped the
architecture below, so they live here rather than in `AGENTS.md`, which holds only
the loop and the facts a model would otherwise get wrong.

- **24/7 uptime, zero hosts online.** Every surface works with no host online —
  chatbot users through the live connector, daemon hosts installing the tray in
  under 5 minutes, contributors cloning and running cleanly, plus discovery,
  remix, converge, the paid-market inbox, and moderation. Take the task
  unblocking the largest currently-broken surface; treat every outage as equal
  severity, because tiering is what starves the quiet surfaces.
- **The founder's desktop is never infrastructure** (2026-09-21). `DESKTOP-KCPMGP3`
  is the founder's home PC: never enroll it, route platform work to it, or use it
  as a fallback. Platform service dependencies are cloud-only, and an existing
  local registration, tunnel, provider login or heartbeat is not permission.
- **The platform has no LLM** (2026-09-24). Only a powered universe calls an LLM,
  with its owner's own connected credentials, for that universe alone. No platform
  model, no shared/host/maintainer credential, no fallback. The founder's
  subscription is the founder universe's only.
- **No vendor-specific compute or connection code** (2026-09-24). Any LLM or
  platform connects through vendor-neutral connectors the user's agent configures;
  a new vendor never needs a patch. Existing vendor paths are migration debt
  (§ *Module: Providers*). Dev tooling is exempt.
- **Shape → live MVP → user-test → then harden** (2026-08-20). One review for
  shape, approach and single-user safety holes; ship the MVP live; test as a real
  user; *then* harden what live use shows matters. Never gate a first draft behind
  a hardening gauntlet — only live users reveal whether the shape is right.
- **Private by default** (2026-09-26). *"nodes in users universes should be
  private unless they make them other user accessible or visible or interactable in
  some way"*, and *"universes and the nodes in them need to be default private and
  we need to make sure that is set correctly for new users also"*. The platform
  never declares an open level on an owner's behalf: every creation path writes
  `private` — first-contact home materialization included, which is the new-user
  path — the declaring migration writes `private`, and a Branch is born private
  too. Exposure is a separate, explicit owner action, and a level the platform does
  not enforce on every reader is not offered at all. As-built:
  `openspec/specs/universe-visibility/spec.md`.
- **The floor, and only the floor, blocks a deploy:** cross-user read or effect;
  auth or credential exposure; unrecoverable loss of user data; wrong money; an
  irreversible external act without consent; public connector down. Everything
  else is tracked and re-judged after live use.
- **If you know the next step, take it** (2026-09-17/24), approvals inside the
  agreed work included. Ask only about new spending, irreversible or
  outward-facing acts outside the agreed work, and PLAN.md changes. Gates default
  autonomously; a true host-only ask is a `docs/host-actions.md` row with the
  smallest possible scope, and it must not block unrelated work.
- **Test through the app agent as a user would** (2026-09-24). Ask the way a
  casual, naive user would; never feed it an answer it is supposed to work out,
  because that is a false "works" signal real users get no help behind. Never
  build or edit users' workflows yourself — enable the agent to do it. Scope is
  the basic capability set, not the current user's needs: a basic capability stays
  listed until cleared, even with nobody blocked on it.
- **User uploads are authoritative.** Preserved verbatim — never summarized,
  truncated, or reformatted.
- **A pasted client chat is a bug report.** Extract the issues and fix them.
- **Spec what is hard to reverse, build the rest.** Public MCP/API surface,
  storage shape, authority/permissions, migrations and money get a proposal and
  design before code; everything else is built, proven live, and specced from what
  shipped, which is more accurate than what was predicted.
- **The rulebook only shrinks.** A new rule must displace an old one; the ratchet
  (`scripts/check_context_budget.py`) enforces it. Keep a rule only if it encodes
  project knowledge unavailable from the repo — and put it where the agent needs
  it, not in an always-loaded file.

---

## Scoping Rules

These five rules govern what features, primitives, and architecture get built — and what does not. They run in scoping cadence: irreducibility test first, then composition test, then privacy specialization, then architectural placement, then runtime tier targeting. Any new feature, design note, or audit recommendation must clear all five before it is shippable as platform code. Cross-provider readers (Codex, Cursor, OSS contributors): read these before proposing a new tool, action, evaluator, or primitive. PLAN.md carries the rule + why + how-to-apply only.

### 1. Minimal primitives — fewest building blocks that compose to everything

**Rule:** The platform's tool surface is a small, fixed set of fundamental primitives. Every proposed new tool answers: "is this a primitive (irreducible building block) or a convenience (composable from existing primitives)?" Conveniences don't ship. Treat tool count as a budget that should shrink, not grow.

**Why:** Every new tool taxes user cognition, chatbot tool-list metadata (more confusion + hallucination surface), maintenance cost, documentation burden, and discovery friction. The natural reflex when a user wants X is "let's add X." This rule overrides that with: "what minimal primitive(s) does the user need to compose X themselves?" Per host directive 2026-04-26.

**How to apply:** Before adding a tool, verb, action, EvaluatorKind, or any primitive: (1) is this fundamentally NEW capability, or convenience over existing capability? (2) Could you build THIS from a smaller combination of existing primitives? If yes, don't ship it — document the composition pattern instead. (3) Two primitives that overlap are one too many. (4) The decision rule for "convenience that's so useful it should ship": would a competent chatbot reliably compose this from primitives in <5 reasoning steps? If yes → community-build, no platform ship. If no (composition is fragile, requires nondeterministic reasoning, or hits a structural gap) → THAT gap is the actual primitive worth shipping. See `composition-patterns` wiki page for a cataloged set of chatbot-built compositions over the canonical primitive surface.

**Irreducibility finding — the only door a new top-level primitive comes through (host-approved 2026-07-25).** The primitive set is minimal and irreducible, like a low-level coding language: small, orthogonal, and composed rather than extended. A new top-level primitive (a new MCP handle, a new substrate concept) ships ONLY on an explicit *irreducibility finding* — a recorded finding that the behavior has essentially **one working useful shape**, so there is nothing for the commons to disagree about. Everything else ships as actions and parameters under the existing canonical handles, or not at all. The corollary is the important half: a behavior with **many plausible custom shapes is user-buildable by definition** — including sandbox behaviors — and belongs to the commons, so the standard emerges from what users actually build and remix rather than from a shape the platform froze first. This is the rule that governs how architecture-note tool names become real: a design note naming a standalone tool is naming a *behavior target*, and that target lands as an action under a canonical handle unless someone records the irreducibility finding.

**The universe is the harness** (founder, 2026-09-24): a user's command center is their agent harness, project folder and workspace, in the pi style. It ships no pre-built features; isolation (every provider process OS-jailed to its owner's box) makes powerful primitives safe. Shape: § *Module: Agent Harness*.

### 2. Community-build over platform-build

**Rule:** When a feature is proposed, the FIRST question is "could the community evolve this?" — not "should we build this?" Platform-build is the fallback, not the default. Imagine the implementation; sketch how a chatbot would compose it from existing primitives + wiki rubrics + remix material; if that sketch works, don't ship platform code.

**Task-automation corollary (host-confirmed 2026-07-26; cloud placement clarified 2026-07-29):** Recurring task loops, schedulers, and similar automations are user-authored designs composed from platform primitives, published to the commons when their authors choose, and copied, remixed, or combined like any other workflow. A recurring automation that is expected to continue while the user's devices are off belongs in the user's cloud universe and runs through ordinary cloud execution using that user's explicitly bound compute/provider authority. A tray or other user device may bridge the period before cloud activation or run an explicitly host-only workflow, but migration uses a single-active cutover: the host executor is stopped before cloud acceptance and is not retained as a simultaneous fallback. TinyAssets does not ship a privileged product-specific automation loop. The historical cheat/community-patch loop is retired and must be absent from runtime, packaging, configuration, and shipped fallback paths; retained uptime canaries and deploy observability are infrastructure checks, not a user-task automation product.

**User-buildable-middle MVP boundary (host-confirmed 2026-08-01):** TinyAssets
owns the hard ends and invariants of a long-running process, not its preferred
internal strategy. The platform accepts a `Trigger` and typed input, durably
runs a versioned user-authored Node/Edge graph, enforces scope and provider/tool/
effect authority at every external boundary, and persists typed output,
artifacts, receipts, checkpoints, and lineage. Everything between those ends
that can have multiple useful shapes—task selection, prioritization, prompts,
evaluators, retry policy, branching, convergence, escalation, and loop shape—is
editable composition data. Power users build and publish those compositions;
other users discover, copy, combine, and remix them, then bind private inputs,
credentials, goals, and provider policy in their own universe. Chatbots must be
able to inspect and change the same definition from a phone while execution
remains in the cloud.

The reference setup journey is conversational and contains no maintainer-only
step: a user gives their chatbot a repository plus a spec or patch request;
the chatbot creates, imports, or remixes a suitable Branch definition; the user
privately binds repository and compute/provider authority; and the chatbot
validates and activates the cloud run. The same chatbot surface can inspect
progress, pause, resume, revise, roll back, or replace that composition while
the user's computers remain off. A rendered connector conversation is the
final acceptance proof for this journey once all underlying boundaries exist.

This is also the MVP scope test: platform code is justified only when it closes
a generic input, durable-execution, authority, output, evidence, discovery, or
remix gap that a user graph cannot safely compose. A first-party automation is
an acceptance fixture and optional commons template, never a privileged runtime
path. In particular, the OpenSpec drain proves the generic substrate by running
as one ordinary private Branch composition; drain-specific scheduling,
refinery, retry, evaluator, and prioritization policy do not become platform
services merely because the first fixture needs them.

**Custom agents are packages** (founder, 2026-10-06). A user's agent is the
starter package or any package from the commons, edited, blended and shared
like any other commons artifact, with lineage. There is no finite
platform-maintained catalog, privileged archetype or platform conversion
pipeline; private bindings, credentials, conversations and memory never travel
with a published package.

**Why:** TinyAssets' product soul is users + chatbots evolving the system through wiki + remix + autoresearch. Platform-shipped primitives are scarce, intentional, and expensive — they crowd out community evolution and lock users into our taste. Community-buildable features compound: every new primitive composition becomes a remixable artifact other users discover and extend. Platform-shipped features are frozen at ship date; community-evolved features iterate continuously across thousands of remixes.

**How to apply:** Imagine the implementation first. Then ask: could the user's chatbot easily compose this from existing primitives (workflow nodes, evaluators, branches, gates, autoresearch, wiki content)? If yes → don't ship as platform primitive; surface the community-build path in the design note + idea triage. If no (structural gap) → identify the gap precisely, ship the smallest primitive that closes it, not the policy. Platform-build is justified only when the gap is structurally impossible to compose around, OR the platform-shipped version unblocks 10x more community evolution than it crowds out.

### 3. Privacy + threat-model patterns are community-build

**Rule:** Privacy mode is a special case of rule 2. Do NOT ship privacy as platform primitives (sensitivity_tier flags, private_output/ trees, server-side response redactors, threat-model presets, pre-baked HIPAA/SOC2 modes). The chatbot composes privacy patterns per user request, using existing primitives + community-evolved best practices.

**Why:** Per host directive 2026-04-26: for well-known sensitive categories (invoices, medical, legal, financial, PII), the chatbot uses community-evolved best practices — wiki pages, remixable node compositions, soul-policy templates. For complex/novel sensitive workflows, the community is BETTER at evolving patterns than the platform — they meet the user in their own vocabulary, with their own judgment about what matters. Platform-built privacy features ship a frozen taxonomy; user threat models are open-ended.

**How to apply:** When a sensitive-workflow request comes in (privacy mode, redaction, threat-model preset), the FIRST response is "the chatbot composes this from existing primitives + community best practices." Design-note recommendation: a how-to-compose guide, plus a pointer to community-evolved templates. Platform action ONLY if a primitive is structurally missing — and then ship the smallest primitive, not the policy. The platform DOES still own primitive enforcement boundaries: local-LLM-only routing, file-path enforcement at write time, MCP approval surface. Those are primitives, not policies.

**Guidance is community-built; the platform owns enforcement boundaries only (host-approved 2026-07-25).** Privacy *guidance* — how to handle an invoice, what a HIPAA-shaped workflow should avoid, which redaction pattern fits a threat model — is commons content the community writes and remixes, not platform code and not a platform-authored policy surface. Architecture proposals for platform privacy-guidance tools or a platform-authored privacy taxonomy do not clear this rule. **A seeded, remixable wiki taxonomy is acceptable commons content** — seeding a starting vocabulary is not the same as freezing one, exactly as `_WIKI_CATEGORIES` seeds wiki categories while custom categories are sanitized and accepted. The test: can a user replace or extend it without asking us? If yes, seed it in the commons. If it is a boundary a user must not be able to move, it is enforcement, and it is platform code.

### 4. Commons-first architecture

**Rule:** Public data lives in the platform commons. Two settled parts: (a) Platform-stored data that is *in the commons* is open-source community data — public-by-definition. (b) Community designs published to the commons become the tool surface for next users via discovery + similarity + remix; the platform doesn't build features, the community evolves them. **Where a user's *private* data lives is a scoped open research question, not a settled rule** — see below.

**Why:** Per host directive 2026-04-27. Identity alignment — TinyAssets is open-source community first, the platform's data space is for the community. And the commons + remix engine is what makes minimal-primitives + community-build viable at scale: the platform ships discovery/similarity/ranking/attribution primitives; community ships features.

**Private-data custody is an OPEN RESEARCH QUESTION (host-approved 2026-07-25 — reopened, previously stated here as settled).** Custody is **per-situation and user-chosen**, not one architecture. It depends on the use case (a HIPAA-class workflow and a full-cloud personal brain are not the same problem) and on how much trust the user is willing to extend to us. The custody modes to research, none of them ruled in or out: **host machine** (data never leaves the user's hardware), **private universe brain** (the user's own brain bundle, wherever they choose to run it), **vault** (encrypted custody with the key held outside platform reach), and **platform-held** (we store it, under stated boundaries). The design constant is the customer: they hate lock-in and can build a ground-up alternative if we take their optionality away — so whatever custody a mode uses must stay exportable and replaceable, and the *user* picks the mode.

**How to apply:** Before adding ANY platform feature, ask: "Could a user compose this from existing primitives + community remix?" If yes, the answer is to make discovery / similarity / remix work well, not to ship the feature. Commons content is public-by-definition. For anything touching private data: **do not encode either custody answer as settled.** Do not ship a design that assumes the platform can never hold private content, and do not ship platform private storage or private catalog rows as though that question were already answered — name the custody mode your lane assumes, scope the lane to it, and record the assumption. The 2026-04-18 per-piece privacy design (private Supabase Storage, field-level platform records; recoverable from git history) is **research input to this question, neither canonical nor retracted** — cite it as one candidate custody mode, never as authority. Async availability remains acceptable for host-resident modes: content gated on a host being online yields a graceful "no host online" signal. Standing anti-patterns regardless of custody mode: discovery surfaces that bias toward platform-built content (commons content is equal first-class), and any custody design a user cannot export out of.

### 5. User capability axis — browser-only vs local-app, across providers

**Rule:** TinyAssets has two basic user shapes for product-design purposes: **browser-only** (phone or computer; chats through web client — Claude.ai web, ChatGPT web; no local file system or code execution) and **local-app** (computer with chat-client app + computer-use access — Claude Code, ChatGPT desktop with computer-use; local file system, local code execution, daemon hosting). Orthogonal axis: MCP host provider. Claude and ChatGPT are P0 launch/discoverability gates, not the market boundary. Any user-facing chatbot, IDE agent, local model shell, enterprise agent builder, or custom app that can connect to a TinyAssets MCP server is part of the customer model; non-P0 hosts get explicit matrix-scoped support and caveats instead of being treated as invisible long tail.

**Why:** "Use Claude.ai instead" or "use Claude Code instead" is an anti-pattern. A real user is on whatever client they chose, and the platform reaches them there. Bugs that work on one provider but not another are P1 product bugs, not "use the other one." Don't second-class browser-only users — compensate via cleverness (host the daemon for them, publish results to shareable URLs, stream long outputs, save state to universe, compose chains that produce tangible deliverables, use platform scalability advantages like parallelism + retries + evaluators that no single browser session could do alone).

**How to apply:** Every feature design names its target capability tier and host coverage. Local-app: daemon hosting, file system I/O, local program invocation, autoresearch overnight, multi-tenant tray, OSS-clone-and-extend. Browser-only: cloud-mediated equivalents for everything actionable. Launch parity: test on both Claude and ChatGPT before claiming a public chatbot feature ships. Matrix parity: for any other host, say exactly which host was verified and what caveat remains. A primitive earns its keep MORE if it works equivalently across both capability tiers and many MCP hosts; a primitive that only helps local-app users or one provider is a much higher bar to ship. Hopeful future: the gap collapses (Claude.ai gaining computer-use, ChatGPT gaining MCP local-file capabilities, browser sandboxing improving) — primitives should compose the same way regardless of capability tier; tier just determines leverage paths, not feature existence.

**Identity invariant (founder, 2026-09-08):** Identity follows the authenticated account, not the client. The same user signing in through ChatGPT, Claude, a local agent, or another MCP host resolves to the same principal and home universe everywhere.

---

## Canonical Vocabulary

**Status: canonical as of 2026-05-10; handle set restated 2026-07-25.** The platform's foundational substrate vocabulary is six work concepts plus seven permissioned MCP handles. Coding sessions should use this vocabulary when naming architecture, docs, tool metadata, and future design notes unless a narrower domain term is explicitly needed.

### Canonical Naming Boundary

**Status: canonical as of 2026-06-27.** `Tiny` is the personified intelligence users and developers interact with: the acting persona shaped as an extension of the founder's will. `TinyAssets` is the website, platform, distribution, GitHub/repository, package, and app/listing brand. `Workflow` / `workflow` was the engineering discovery name and is retired as a product, repository, connector, package, or durable namespace.

This boundary does not retire the generic English noun "workflow" when it literally describes a user's process, graph, or branch. It does retire `Workflow` as a product/repository/connector name and `workflow` as a durable namespace label. Current public copy, connector metadata, package names, env vars, data paths, and active docs use `TinyAssets` for the platform and `Tiny` for the acting persona. Any remaining old-name reference must be ordinary English or clearly historical.

The six base concepts describe durable work at the graph layer:

| Concept | Meaning |
|---|---|
| `Node` | A typed unit of work, judgment, transformation, or evidence capture. |
| `Edge` | A declared transition between nodes, including conditional routing and review paths. |
| `State` | The durable typed record a graph reads, writes, reduces, checkpoints, and resumes. |
| `Scope` | The authority and context boundary for a work item: user, branch, goal, daemon, host, commons, or other bounded surface. |
| `Run` | An execution attempt with inputs, outputs, provider traces, checkpoints, and evidence. |
| `Trigger` | The event or schedule that asks the platform to start, resume, replay, or route work. |

The seven MCP handles are the **external connector** surface: what an outside chatbot uses to inspect and act on those concepts. The in-app agent sees only its four tools plus `ta` (§ *Module: Agent Harness*).

| Handle | Authority |
|---|---|
| `read.graph` | Inspect graph structure, state summaries, lineage, runs, and public metadata. |
| `write.graph` | Propose or mutate graph definitions, state, scopes, edges, and work artifacts under the caller's authority. |
| `run.graph` | Start, resume, cancel, replay, or otherwise control graph execution within the caller's scope and confirmation policy. |
| `read.page` | Read wiki, commons, docs, request, and explanation pages that contextualize the graph. |
| `write.page` | Draft or update wiki, commons, docs, request, and explanation pages through the same reviewable artifact path. |
| `converse` | Relay a message to the universe intelligence and return its own first-person reply. The universe is the actor; the connecting chatbot is the relay, not the speaker. |
| `get_status` | Read-only platform, universe, host, and release-state evidence. Reading can create nothing and move nothing. |

The live surface asserts exactly this set: `CANONICAL_HANDLES` in `scripts/mcp_public_canary.py` (`--assert-handles`, Hard Rule #11), as-built in `openspec/specs/live-mcp-connector-surface/spec.md`. **New behavior arrives as actions and parameters under these handles by default** (see Scoping Rule 1 for when a genuinely new handle may ship). Architecture notes that name standalone RPC/MCP tools describe target *behaviors*, not an approved tool count.

Concrete tool names map back to one or more of these handles in docs, permission checks, and tool descriptions.

---

## Cross-Cutting Principles

These principles apply to every module. They do not own a module each; they constrain how modules behave.

**Agentic hybrid search is memory.** Durable memory is a policy over multiple stores (KG traversal, vector similarity, hierarchical summaries, notes, world-state, direct tool calls). No single *index* owns truth — truth lives in the brain's canonical store and every index over it is derived and rebuildable. For the commons, and as the default organization for a universe brain, that store is the OKF bundle (Brain Module); a founder may design their own brain organization (host-approved 2026-07-25, Design Decisions), and this source-vs-index split holds for whatever organization they choose. Routing across those indexes matters more than any one of them.

**Context is a managed working set.** Prompts are lossy projections over durable state. The goal is not "pack more context" but "give the model the smallest high-signal working set for the current step."

**Owner surfaces are complete; bounding is a model-door projection; the only
per-account input is account type** (founder, 2026-09-30). There are two doors.
The *owner door* (`tinyassets/owner_door/`, `/app/api/*`) serves the owner's app
on web, phone and desktop, and it always returns complete data: it has no size,
limit or truncation logic and cannot import one. The *model door* (the MCP
connector and the served-agent engine) bounds what enters a model's context, as a
projection applied only there. Shared domain reads return every row. Paging is an
explicit cursor the client drives, never a silent default. The only per-account
input that may change behaviour is `AccountType` (free | subscription), resolved
once, per account; connections and data volume never change what an account sees.
The rule is enforced by structure: import boundaries, not an exempt list. On
2026-09-30 the founder's request rail vanished because his 34 KB queue crossed a
24 KB model ceiling that the app shared with chatbots. That bug is the failure
mode this rule prevents. Change: `openspec/changes/archive/2026-09-30-owner-door-complete-reads/`.

**Platform state transitions are the core abstraction.** Orient, plan, draft, commit, learn, reflect, enrich, task selection. If the state model is wrong, the system feels smart locally and breaks over long runs.

**Every scaffold is a falsifiable hypothesis.** Counters, thresholds, phase gates, routing rules all encode a claim about model weakness. Prove the simpler approach fails before adding; prove removing hurts before defending. When a stronger model lands, re-test the harness. Trend toward less prescriptive control.

**Tools are the agent-computer interface.** Tool shape is architecture — names, parameters, return schemas, failure semantics. Prefer a smaller number of reliable composable tools over many overlapping ones. **Trust-critical tools include their own caveats:** structured evidence + structured caveats lets the chatbot compose trustworthy narratives without the system having to police its honesty.

**Generator, evaluator, and ground truth stay separate.** Self-evaluation bias is real. Keep them as separate channels, often separate model families. The evaluator needs a different failure profile, not a better creator.

**Learning is write-back compression.** Agents improve by promoting stable lessons into reusable artifacts (notes, style rules, facts, summaries, revised tools and prompts), not by hoarding transcripts.

**Evals grade process and outcome.** Final quality isn't enough. Inspect retrieval choices, tool usage, stopping behavior, handoff quality, grounding, artifacts. When a run fails, traces should explain why.

**Foundation builds to the end state; features may iterate** (host, refined 2026-04-19).
*Foundation* is infrastructure everything else depends on — multi-user support, storage schema,
auth, daemon dispatch core, naming commitments, module layout, MCP server core, basic paid-market
routing. Foundation always builds to the long-term best-known design **in the present**: no phased
rollouts, no compat-shim bandages, no "ship part 1 and iterate later." *Features* are the surfaces
the chatbot and user interact with — trust verbs, discovery polish, autoresearch refinements,
bid-UX, shared-account primitives — and they legitimately roll out in phases, because how the
chatbot wants to use a feature is not knowable in advance. The test before choosing a shape: **is
this load-bearing for other work?** Yes -> foundation -> end-state. No -> feature -> iterate; when
ambiguous, ask whether the next thing you want to build depends on this being its final shape.
Refactor foundation as better implementations are discovered — update PLAN.md first, then refactor
to match; each foundation ship is itself end-state-shaped. **Foundation does not carry debt;
features do, temporarily.** Carve-out: atomic-commit discipline stands — "end-state" means each
commit is atomic *and* takes the code to its final shape, not that related work is squashed
together. Cited from `tinyassets/storage/__init__.py` and `tinyassets/bid/__init__.py`.

**Code before agents: if an invariant can be enforced mechanically, build the check** (host,
2026-04-19). Every scheduled agent check-in for "is X still true?" is a place a script-that-never-
forgets does better — zero tokens, no memory decay, silent unless it had to act. The framework is
`scripts/invariants/`, run by `scripts/invariants_run.py` and gated by `scripts/git-hooks/pre-commit`.
When you notice an agent repeating a "recheck X, heal if drifted" pattern across sessions, promote
it to an invariant. Two corollaries earned the hard way: **an invariant that never blocks is
decoration** — `context-budget` sat registered, VIOLATED, and `pre_commit_scope=False` while the
always-loaded set grew from 17.6 KB to 62 KB — and **a check-adding commit must have that check's
own tests as its gate**, since the `#46 c880f94` regression shipped when the mojibake hook protected
downstream commits but not itself, stacking 4 commits on red main.

**Nothing runs unless it lives inside a user's universe, under that user's control** (founder,
2026-08-29). Every execution — a chat turn, a branch run, a background automation, a schedule
firing — belongs to exactly one universe and to the person who owns it, and that person can see
it, pause it, and delete it from their own surface. The platform never supplies an LLM and never
runs an actor of its own: no host-run worker fleet, no platform-level agent container, no
"host user" acting inside universes. Corollaries: (1) a registration that can never fire (a
schedule whose scheduler is dark, an automation whose executor cannot activate) must refuse
loudly at registration, never sit silently; (2) re-issuing execution authority under a new
identity goes through the user's surface, never a server-side mint on their behalf; (3)
infrastructure processes (canary, reconciler, log shipper) are not universe work — they may
run, but they never invoke an LLM or act as a universe. Stated while deciding the fate of the
retired cloud-worker fleet still declared in `deploy/compose.yml`; the fleet is out, the
user-owned background loop stays.

---

## Module Map

The codebase target shape, with each PLAN.md module mapped to its primary code package(s). Where the current state diverges from this target, the gap is in-flight work — not architectural disagreement. Anchored by the spaghetti audit at `docs/audits/2026-04-19-project-folder-spaghetti.md`.

`tinyassets/` is the engine package.

| PLAN.md Module | Primary code package(s) |
|---|---|
| Agent Harness | `tinyassets/agent_loop/`, `tinyassets/starter/` |
| Daemon Platform | `tinyassets/identity.py`, `tinyassets/discovery.py`, `tinyassets/branch_tasks.py`, `tinyassets/runtime/` |
| **Brain** | `tinyassets/memory/`, `tinyassets/retrieval/`, `tinyassets/knowledge/`, `tinyassets/storage/__init__.py` (memory_kinds), `tinyassets/learning/` |
| Providers | `tinyassets/providers/` |
| API & MCP Interface | `tinyassets/api/` (mounted submodules per cluster), `tinyassets/servers/` |
| Distribution & Discoverability | `packaging/`, `packaging/registry/`, `packaging/claude-plugin/`, maintained connector submission artifacts |
| Uptime & Alarms | `deploy/`, `.github/workflows/uptime-canary.yml`, `.github/workflows/p0-outage-triage.yml`, `scripts/uptime_canary.py` |

Engine subpackage target shape (the durable commitment — anything new must fit one of these or earn its root spot with a one-line explanation):

| Subpackage | Responsibility |
|---|---|
| `tinyassets/api/` | MCP tool surfaces. Mounted submodules per capability cluster (FastMCP `mount()`). **No god-modules.** |
| `tinyassets/storage/` | Schema + bounded-context storage layers. Shared `_connect()` + migrations in `__init__.py`. |
| `tinyassets/runtime/` | Run scheduling primitives — runs, work_targets, dispatcher, branch_tasks, subscriptions, producers, executors. |
| `tinyassets/bid/` | Per-node paid-market mechanics — node_bid, bid_execution_log, bid_ledger, settlements. |
| `tinyassets/servers/` | Entry-point shells. Routes to `api/` submodules. **Not the place action logic lives.** |

Existing subpackages already conforming: `auth/`, `catalog/`, `checkpointing/`, `constraints/`, `context/`, `evaluation/`, `ingestion/`, `knowledge/`, `learning/`, `memory/`, `planning/`, `providers/`, `retrieval/`, `desktop/`, `testing/`, `utils/`. Correctly-flat root modules (small typed surfaces with no clear sibling): `protocols.py`, `exceptions.py`, `notes.py`, `packets.py`, `config.py`, `identity.py`, `discovery.py`, `singleton_lock.py`, `domain_registry.py`, `registry.py`, `preferences.py`, `compat.py` (post-Phase-5).

**Migration policy.** When a flat module crosses ~500 LOC OR overlaps a sibling's responsibility, it gets a subpackage. New work goes into the target shape; legacy gets refactored opportunistically (the spaghetti audit ranks priority order).

---

## Module Shape

Every module section below follows the same shape so PLAN.md reads as reference:

- **Purpose.** One sentence — what the module exists for.
- **In scope.** What this module owns.
- **Out of scope.** What it does NOT own (pointing to siblings).
- **Principles.** The constraints that govern this module.
- **Substrate.** Concrete code paths and current shape.
- **Open evolution.** What is still being figured out.
- _Last audited: YYYY-MM-DD_

---

## Module: Agent Harness

**Purpose:** The product: a Muse-level agent in each command center's chat bubble, on pi.dev-style plumbing (founder, 2026-10-06). Users build from there.

**Principles:**
- *Four tools plus `ta`.* The model sees `read`, `write`, `edit` and `bash`, running inside the command center's sealed box, plus `ta` for platform primitives. Nothing else is model-visible.
- *One definition for every provider.* One agent definition renders identically on every model provider: same instructions, same tools, no provider-specific code, names or branches. Users connect any model source, and the agent defaults to their strongest connected one.
- *One extension unit.* Everything beyond the plumbing (skills, prompts, hooks, settings, app UI) arrives as one kind of unit: files in an editable package.
- *Abilities are editable files, not platform features.* The starter package is files and skills the agent and user edit; prompts and defaults are never Python strings. The platform builds no feature editors and no pre-built features: the agent edits its own files, and users build UI with `app_ui`.
- *Clean cutover.* When the shape changes, the old path, its tests and its docs are deleted in the same change; no aliases or compatibility paths.

**Substrate:** `tinyassets/agent_loop/` (`box_tools.py`, `box_ta.py`), `tinyassets/starter/`. Changes: `openspec/changes/universe-agent-harness`, `starter-agent-out-of-plumbing`, `starter-muse-package`.

_Last audited: 2026-10-06_

---

## Module: Daemon Platform

**Purpose:** A multi-tenant platform where every user's agent runs in its own command center, under that user's control, with no host online.

**In scope:** Agent definitions and private bindings, command-center export, the sealed box per command center, and host-independent user loops.

**Out of scope:** What the agent sees and how it is extended (Agent Harness); what it knows (Brain); model connections (Providers); the MCP surface (API & MCP Interface).

**Principles:**
- *Public definition, private binding.* An agent's reusable definition (its package: instructions, skills, settings, default workflows) is public and remixable with lineage. Its installation in a command center — role, authority, goals, model and channel bindings, credentials, conversations, private inputs and learned memory — is private and never travels with a fork or remix.
- *No power-user ceiling.* Every component is inspectable, replaceable, removable and exportable; the platform enforces authorization, secret isolation, sandboxing, attribution and spend caps, not agent categories or fixed topology.
- *Users can always take their command center with them* (founder, 2026-10-01). One action exports a whole command center to a folder on the user's computer: harness, roster, rules, workspace, wiki and brain, workflows and schedules, selected memory, and UI layouts. It uses the same bundle format as sharing and import. The folder runs standalone with a local model through a small pi-shaped runner, with no platform account, and it is publish-ready as a repository: README, license placeholder, secret-excluding `.gitignore` and `.env.example`, with credentials never exported. Publishing it anywhere stays user-built. The exportability principle is permanent; the module that implements it is refactored as the harness changes (`openspec/changes/universe-agent-harness` §4.17).
- *Multi-tenant from the first build.* Storage, authorization, queues, budgets, audits, agent bindings and runtime activations carry tenant/owner boundaries.
- *Every command center is a sealed box; the control plane is the only always-on layer* (founder-approved 2026-10-01: "approved, go with the sealed box design"; target shape now, capacity later, 2026-10-02). Each command center runs in exactly one box with its own kernel boundary (Firecracker microVM with snapshot/restore; gVisor behind the same `BoxProvider` interface where KVM is unusable), its own fixed-size disk allocated from the account's storage quota, and no network interface. Tool calls and any CLI run inside it. The daemon reaches box contents only through `BoxProvider` (never a host path) and treats them as untrusted. Platform state (vault, run/consent/usage/attention/conversation stores, rules, activity, sessions) lives outside every box. Boxes are awake only while acting and suspend after at most 60 s idle; the scheduler, triggers, inbox and notifications live in the control plane, and a box keeps no timers. Growth adds cells and box hosts behind fixed seams (`home_cell`, ownership generation, outbox); no code path checks the stage or the tier. Change: `openspec/changes/target-architecture`.
- *Zero daemons required for authoring.* Node/branch/goal creation, editing, forking, and collaboration work with no daemon running anywhere. Daemon hosting is opt-in for execution work. Load-bearing requirement — any architecture where authoring depends on a running daemon violates it.
- *Host-independent user loops live with their universe.* A recurring workflow's definition, schedule, checkpoints, receipts and health live in the user's cloud command center and keep running with every user device off.

**Substrate:** `tinyassets/custom_agents.py`, `tinyassets/agent_interchange.py`, `tinyassets/runtime/`, `tinyassets/branch_tasks.py`. Sealed box and control plane: `openspec/changes/target-architecture`.

**Open evolution:** N-of-M multi-actor approval as a generic primitive (founder vote, treasury multisig, publication co-signature) is unscoped.

_Last audited: 2026-10-06_

---

## Module: Brain

**Purpose:** The platform's memory + identity + authority substrate. The Brain holds what each universe / branch / daemon / contributor knows, what they've agreed to, and what they're eligible to do. Every other module consults the Brain before deciding.

**In scope:**
- Tiered memory across multiple stores (KG, vector, hierarchical summaries, world-state, notes, direct tool calls).
- The `memory_kinds` typed catalog — canon fact, attribution snapshot, soul fingerprint, gate-evidence, contributor weight, etc.
- Promotion state machine: candidate → accepted → promoted → rejected → superseded. No memory becomes load-bearing without earning promotion.
- Treasury status *read path* — bounded budget + spend visibility.
- Bounded autonomous spend guardrails — per-Goal / per-daemon / per-cycle caps.
- Authority-condition policy — Brain conditions every external-write authority decision on past-decision memory.
- Attribution graph snapshot at the moment a reward releases — authoritative for payout.

**Out of scope:** Treasury *write path* (future Treasury Module); goal/gate ladder definitions (Goals & Gates); provider routing (Providers); evaluation logic (Evolution & Evaluation); MCP surface (API & MCP Interface).

**Principles:**
- *Canonical source and retrieval routing are different questions.* The canonical store owns truth — the OKF bundle for the commons and for the default brain organization (below); no single *index* owns retrieval. A user-designed brain organization keeps the same source-vs-index split under its own canonical form. Routing across stores is the policy; routing matters more than any one index.
- *Memory interface is query semantics, not tier names.* The public interface feels like faceted search, not "core/episodic/archival" tier addressing.
- *Generator, evaluator, and Brain stay separate.* Self-evaluation bias is real; the Brain is read-only to its own evaluations.
- *Learning is write-back compression.* Stable lessons get promoted into the typed catalog; transcripts are not memory.
- *Brain conditions every authority decision.* Permissive by default — Brain logs and hints. Per-Goal opt-in to strict mode where Brain may refuse to authorize a contradicting write.

**Canonical store — host-approved 2026-07-25 (architecture; the write path is NOT built).**
- *Source of truth.* **For the commons — and as the default organization for a universe brain — the canonical knowledge representation is an OKF bundle** — markdown files with YAML frontmatter, one file per entry, cross-links forming the graph, reserved `index.md` + `log.md`, `okf_version` declared at the bundle root. The SQLite entry store, FTS index, and vector index are a **derived, fully rebuildable operational index** over it: disposable by design, one-command rebuild, and the bundle wins when the two disagree. Tiny's typed fields (`goal_id`, `universe_id`, `visibility`, `lifecycle`, `ttl_class`, `supersedes`, `evidence_refs`) ride as additional frontmatter keys — OKF requires only a non-empty `type` — so no profile mechanism is invented. This is a default and a commons contract, **not an all-brains mandate**: a founder may design their own brain organization (host-approved 2026-07-25, see Design Decisions) and gets the same substrate guarantees under their own canonical form. The substrate contract therefore stays organization-neutral — what the bundle reader/writer must abstract so a non-OKF organization is expressible without a second engine is an open seam owned by `openspec/changes/archive/2026-08-26-build-brain-canonical-store/` task 4.5, not settled here.
- *Durability boundary.* Writes are write-through under an **explicit commit protocol** — idempotency key, pending→durable entry states, atomic temp-file+rename projection, file locking, transaction/outbox ordering, crash recovery, rebuild reconciliation. An entry is durable only once it is in the bundle; the operational index alone is never durable storage, and a naive dual-write is not enough. `log.md` is generated human-readable history — the transactional journal/outbox is separate operational state, never one prose markdown file under concurrent writers.
- *Redaction ordering.* The operational index stops serving the entry **FIRST** (tombstone/block reads), *then* the bundle body is deleted at the source, *then* the index is rebuilt and rollups purged. Reversing that order keeps serving stale content from the index after the source is gone. A secrets-class tombstone omits any recoverable content hash.
- *Build boundary.* OKF **conformance validation is `[substrate]`** — a guarantee, not a forkable default. The **upstream-watch steward is `[composable]`**: a forkable branch that holds a vigil on the OKF spec, pins `okf_version`, and *proposes* migrations on backward-compatible minor bumps. A major bump is a deliberate reviewed migration, never automatic.
- *Backup.* The nightly git snapshot **is** the canonical durable store, not a backup of an authoritative database. Self-host and fork export emit the bundle wholesale as a portable OKF bundle consumable with no Tiny-specific tooling — this is what "format, not platform" buys, and it is the same no-lock-in guarantee Scoping Rule 4 owes the customer.
- *Status.* Architecture only. There is no `tinyassets/brain/` package, no bundle write path, and no commit protocol. What ships today is a one-way curated **export** (`tinyassets/wiki/okf_export.py`, as-built in `openspec/specs/knowledge-retrieval-and-memory/spec.md`), whose narrow local `conformant` flag does not claim canonical-store authority. Provenance for the decision: `openspec/changes/archive/2026-07-25-brain-okf-canonical-store/` and the Codex review at `docs/audits/2026-06-24-brain-okf-canonical-codex-review.md`. Earlier SQLite-canonical wording in the June legacy documents is superseded provenance, not authority.

**Substrate:** `tinyassets/memory/`, `tinyassets/retrieval/`, `tinyassets/knowledge/`, `tinyassets/learning/`, `tinyassets/storage/__init__.py` (memory_kinds + promotion state). Open-brain v2 slices landed 2026-05-19: A=memory_kinds registry, B=soul-guided dispatch read, C=treasury status read, D=bounded autonomous spend. Companion artifacts on main: #903 amendment-verdict carrier, #870 wiki-bug body inclusion, #866 dedup safety net.

**Open evolution:** Authority-condition strict-mode rollout. Brain's role in N-of-M multi-actor approval state. Brain ↔ Evolution feedback — which Brain-snapshotted attributions feed back into evaluator training signal? Cross-universe Brain federation (shared scientific corpus across Goals).

_Last audited: 2026-05-19_

---

## Module: Providers

**Purpose:** Pick the best provider per role and preserve role separation without hiding failure.

**In scope:** Vendor-neutral connection primitives, the user's model choice and fallback order, failure records.

**Out of scope:** What a provider is asked to do (the requesting module); evaluation of provider output (Evolution & Evaluation).

**Principles:**
- *The platform has no LLM* (founder hard rule, 2026-09-24; AGENTS Hard Rule 15). There is no concept of "the platform's LLM". Only a powered universe makes LLM calls, using the credentials its owner connected, for that universe alone. The platform never makes, needs or brokers an LLM call for its own operation: onboarding, selection, moderation, ranking, investigation, maintenance and monitoring all run without one. No platform, host, maintainer or shared credential ever serves a universe, including as a fallback. The founder's subscription belongs to the founder's own universe, like any user's.
- *The agent loop is thin, shared and vendor-neutral; credentials stay outside the box* (target architecture, 2026-10-02). Turns over standard HTTP model protocols run in the control plane's asynchronous loop, which forwards tool calls to the turn's bound box and never executes model output. Model and API calls go through the credential broker (bound to owner, connection and grant; the only holder of the vault key); API-key CLIs in a box reach it through an in-box endpoint, so neither the loop nor any box holds a credential. A CLI runs inside the owning command center's box only for a command adapter or a credential the CLI must hold itself (file OAuth); one CLI process never serves two accounts.
- *The platform is vendor-neutral: any compute source through standard connections, with no vendor code* (founder directive, 2026-09-24). Users connect ANY LLM or compute source to their universe, including ones that do not exist yet, with no patch from TinyAssets. The platform offers only standard, vendor-neutral connection primitives:
  - generic OAuth (with token refresh);
  - API key, bearer or custom-header auth;
  - standard HTTP model protocols, such as OpenAI-compatible chat;
  - local or self-hosted endpoints;
  - a user-configured command adapter.

  The user's app agent can set up any of these itself from what the user provides. No vendor-specific code paths, names or special cases exist on the platform. Today's common vendors are regional and transient, so they are configurations of these primitives, never code. A vendor CLI or API update must never require a platform patch. **One modular connector system serves compute and platforms alike.** An LLM is just another universe connection, like Twitter or any online software: the same vendor-neutral connectors plug a universe into any compute source or any platform the user wants it to use. **The connection request is the setup experience.** A universe that needs a connection (an unpowered universe needs an LLM) raises a request notification. From it, the user completes any shape (OAuth, API key, local endpoint, account sign-in, command adapter) in as few clicks as possible, and the app agent can raise and complete the same requests. **Prefer OAuth whenever the provider offers it for what the request needs** (founder, 2026-09-24): it means the fewest user actions and no key copying. Whether a provider supports OAuth, and with which scopes, comes from standard discovery (RFC 8414 authorization-server metadata, OpenID configuration, PKCE for public clients) or from connection data the user or agent supplies, never from per-provider code. Key paste is the fallback when OAuth is unavailable or does not cover the requested use. OAuth tokens refresh generically, with no reconnect. First power needs no LLM call: an unpowered universe is powered by OpenRouter (free, or the user's own OpenRouter account) through the sign-in OAuth flow below, and the user then adds any other sources.
- *Agent definitions are provider-portable; subscriptions are private bindings.* A public definition declares capabilities and optional model requirements, never credentials. At installation, the universe binds it to the compute sources the user has connected, under the resource ledger and `allowed_providers` policy. Provider choice may change without forking the reusable agent definition.
- *Model choice belongs to the user and their connection, not a compiled model list* (founder directive, revised 2026-09-09). Explicit user selections, saved defaults and fallback order take precedence. Without a user override, prefer the user's available subscription or locally running LLM sources over OpenRouter. For an OpenRouter-powered interactive agent, automatically select the best eligible available model and order fallbacks from most to least suitable, moving through them when limits are reached. Selection and ordering use current connection-scoped availability and required agent capabilities, not hardcoded model releases; the free OpenRouter onboarding path must not silently enable paid fallback. Where no more specific user-selected policy applies, retain the provider's own model default. Users can see the provider/model actually powering the interactive agent and click that indicator to switch, save a default, or edit fallback order. Distinguish actual execution (including fallback) from configured preference; do not label an unknown resolved model as known. Preserve explicit choices across provider updates and never borrow unbound authority. This revises the September 4 provider-default policy for OpenRouter and mixed-source selection. These are design requirements, not claims that current adapters, ranking, controls or failover already implement them.
- *A dead sign-in falls back within the turn, when nothing ran* (founder-stated,
  2026-09-26). An authentication failure on the first-choice source is not a reason
  for the turn to stop: it continues to the next model the user configured, in the
  same turn, and the source is marked for reconnect rather than cooled. Fallback is
  scoped to a failure with PROOF that nothing ran -- a quick exit, no protocol
  events, the process reaped -- because a turn that may already have acted must not
  be replayed on another model. Until then only capacity exhaustion advanced a
  turn, so an expired subscription ended it; that was the founder's complaint, not
  a design choice. This is required behaviour, not a claim that every launch path
  already implements it.
- *Error loudly when the remaining provider can't produce acceptable work.* Fake success is worse than failure. (Hard Rule #8.)
- *Failures are structured facts, not a catalogue of messages* (founder direction, 2026-09-24). Errors must stay diagnostically relevant without endless platform updates for new providers or failure modes. Every failed turn, node or connection step records the same small set of facts:
  - **Stage:** a fixed pipeline position (before send, connection/auth, model request, model reply, tool, platform).
  - **Effects:** none, some or unknown, read from the run/effects ledger, never guessed.
  - **Provider-reported detail:** the source's own error text and status, with secrets redacted and length bounded. It is passed through, because it is usually the best diagnosis and covers providers the platform has never seen.
  - **Class:** from a small closed set derived mechanically from transport facts (auth, limit/billing, unreachable, timeout, unreadable reply, refused by policy, platform fault).
  - **Reference:** the run/turn id plus the edge ray id.

  User-facing notices are composed from these fields; there is no per-error string table. "Actions may already have occurred" appears only when the effects field says so. The failure record is readable through the canonical tools, so the user's own agent can explain it and often remedy it (switch model, reconnect, retry). Diagnosis is a user-agent capability built on a primitive, not platform-authored help text.
- *First sign-in should power an unconnected universe* (founder directive,
  September14,2026 PDT). After TinyAssets authentication establishes the user's
  own universe, a universe with no connected LLM automatically continues to
  OpenRouter's hosted signup/sign-in and OAuth authorization flow. New users
  create their own OpenRouter account there; existing users authorize theirs.
  TinyAssets securely completes the owner/universe-bound connection and enables
  eligible free-only agent execution without requiring manual key creation or
  an already-running LLM. Provider consent remains the user's action, not implied
  by TinyAssets sign-in. Cancellation or failure leaves an honest resumable setup,
  never a redirect loop or a borrowed credential. Existing connections that are
  expired, unavailable or exhausted are recovery cases, not evidence of an empty
  setup. Once powered, a generic request to connect any additional supported LLM
  remains available; OpenRouter bootstrap does not restrict later provider choice
  or override saved choices. No paid fallback or account purchase is implied.
  This is required behavior, not a claim of current implementation.
- *User-owned compute precedes market compute.* A user MUST be able to bind and
  use their own compute/provider authority before TinyAssets offers that user
  market-supplied compute. Market compute is an optional later extension or
  fallback, never the prerequisite path, and maintainer quota is never an
  implicit substitute for either. **The compute market is not built yet and
  should emerge, not be designed ahead** (founder, 2026-09-24). Its shape:
  users let their universe's spare connected compute fulfil other users'
  workflow requests, for money or any other reason they choose. It becomes
  buildable once vendor-neutral connectors, cross-owner delivery and model
  selection work the way the founder wants. It must never encode any
  provider's terms-of-service policy: those vary across countless providers
  and change daily, and it is the lending user's responsibility. Locally run
  models fit it most naturally. Until then, market code makes no LLM calls
  and adds no behaviour.
- *Old bespoke request machinery gives way to user-built workflows* (founder,
  2026-09-24). The old bug-report and patch-request system is superseded by
  general cross-owner node inputs and outputs: a user, including the
  founder's own account, builds a bug-report workflow others submit to. Remove
  the old machinery rather than maintain it.
- *Universe authority is the capability boundary.* A universe may use only the
  provider capabilities its user has explicitly bound to it. Every Voice turn
  uses the universe's **current serving provider** through canonical
  `converse`, never a separately selected writer. Speech transport is distinct:
  the single composer control may use the user's browser/device speech service
  to turn speech into text and render the exact reply, or use an authorized
  `tinyassets.voice.v1` bridge when the current provider connection declares
  one. A missing realtime bridge MUST NOT send a user whose typed conversation
  already works through redundant provider setup. If the universe is
  unpowered, the control opens the existing provider setup; if the browser has
  no supported speech input and no bridge exists, it reports that device
  limitation. Voice MUST NOT introduce a second credential flow, silently
  switch writers, use platform or maintainer credentials, or aggregate usage
  across users.

**Substrate:** `tinyassets/providers/`.

**Open evolution:** Existing vendor-specific paths are migration debt, deleted as each one is replaced by a vendor-neutral connector.

_Last audited: 2026-10-06_

---

## Module: API & MCP Interface

**Purpose:** Let users steer through natural conversation and MCP tooling without letting any chat surface become the author.

**In scope:** MCP tool surfaces, FastMCP `mount()` topology, tool/prompt discoverability metadata, control-station prompt, server shells.

**Out of scope:** Action implementations behind the surface (each module owns its actions); the control plane wiring (Daemon Platform); discoverability outside MCP (Distribution & Discoverability).

**Principles:**
- *An outside MCP client is a relay, not the author.* The command center's own agent does the work and speaks in the first person through `converse`; the connecting chatbot relays.
- *The chatbot + connector path is the canonical first-class user experience.* Users who only talk through a real chatbot with the TinyAssets connector installed are complete product users, not a reduced tier. Core interaction design, uptime, and acceptance evidence optimize for that path first.
- *Tools publish explicit titles, tags, and behavior hints.* The daemon exposes a small number of coarse-grained tools; discoverability metadata is part of the interface contract.
- *Trust-critical tools are self-auditing.* Tools that touch privacy, cost, routing, scope, or moderation expose structured evidence + structured caveats; the chatbot composes the user-facing narrative on top. Caveats are part of the tool's contract.
- *Release state is a status contract.* `get_status.release_state` reads the deploy-published receipt that ties the live daemon to source SHA, image tag/digest, build/deploy runs, config hash, canary status, deployment time, rollback target, and actor metadata. Missing receipts surface as caveats, not probe failures.
- *Module shape rule.* API surfaces live in `tinyassets/api/` as mounted submodules per capability cluster. Server shells in `tinyassets/servers/` route to them. **No god-modules.**

**Substrate:** `tinyassets/api/` (helpers, wiki, status, runs, evaluation, runtime_ops, market, branches), `tinyassets/servers/` (workflow_server, daemon_server, mcp_server). Universe-server decomposition is in-flight per `docs/audits/2026-04-25-universe-server-decomposition.md` — universe_server.py is down from 14k peak to 972 LOC live in main.

**Open evolution:** Final cluster extraction completion. ChatGPT-host first-response UX caveat: large MCP responses render as "something went wrong"; a summary-by-default response shape is unscoped.

_Last audited: 2026-05-28_

---

## Module: Distribution & Discoverability

**Purpose:** Installable and discoverable across standard MCP surfaces, Anthropic packaging, and future packaging without changing the portable core.

**In scope:** MCPB packages, Claude Code / Cowork plugins, canonical remote MCP registration metadata, ChatGPT app submission, the per-host customer matrix, install-readiness invariants, software-surface authorization (declarative + multi-layer).

**Out of scope:** What the daemon does once installed (other modules); auth at the MCP edge (API & MCP Interface).

**Principles:**
- *Keep the core portable; add platform wrappers around it.* MCPB packages, Claude Code plugins, registry metadata, and future `.cnw.zip` packaging are distribution layers over the same daemon and tool surface, not replacement architectures.
- *Plug-and-play and power-user control share one path.* A common configuration should install into the cloud or a host against existing subscription grants in minutes; a fully custom definition uses the same manifest, runtime, and evidence path. Ease of setup is a default experience, not a separate restricted product tier.
- *One remote product identity.* Every maintained remote registration uses exact name `TinyAssets` and `https://tinyassets.io/mcp`. Retired route families are ordinary absent routes, never aliases, redirects, translation layers, or compatibility products.
- *One visual identity source.* `tinyassets/desktop/icon_gen.py` owns the mark geometry and palette. Website marks and cache-versioned browser icons, desktop/tray package icons, the shared iOS source, Android densities, and store exports are generated together by `WebSite/brand/render_marks.py`; a checked-in hash receipt is enforced by site build and deploy gates so no surface can retain a different mark silently.
- *MCP host coverage is matrix-driven.* Claude and ChatGPT are P0 launch gates, but every MCP-capable host is a possible customer surface; name the host verified and the caveat that remains.
- *Install-readiness is continuous.* Main is a downloadable release at all times. Every change preserves flawless first-install — packaging auto-builds via CI (import probe + plugin drift check), user-facing copy is branded and unambiguous, broken install is a production bug.
- *Software surface is declarative and multi-layer-authorized.* Nodes declare `required_capabilities`. Per-host capability registry resolves what's installed. Missing software auto-installs (host-policy gated). Daemons can invoke arbitrary local software via a dedicated `external_tool_node` type that bypasses the Python sandbox but layers security: bundled handler signatures, binary signature verification, universe-level allow-list, per-software host approval, subprocess isolation. Any single layer fails, the others hold. Cross-host software donation supported; cross-host node-execution hopping is not.

**Substrate:** `packaging/`, `packaging/registry/`, `packaging/claude-plugin/`, MCP Registry surface, maintained connector submission artifacts, and no-login deployment packs for Open WebUI / LibreChat.

**Open evolution:** First-user evidence after no-dev-mode acceptance proofs land. ChatGPT-mobile proof.

_Last audited: 2026-07-24_

---

## Module: Uptime & Alarms

**Purpose:** The complete system — MCP surface + node execution + collaboration surfaces + paid-market + moderation — stays up 24/7 with zero hosts online. The host machine being asleep for 24h must not extend any outage window past the pager's escalation ladder.

**In scope:** Self-heal layers, alarm ladder, DR drill, canonical canary, and provenance-labelled generic observation.

**Out of scope:** Application-level bugs (other modules); deploy mechanics (Distribution).

**Principles:**
- *Defense in depth, and the alarm path itself is host-independent.* Every self-heal layer assumes the layers below it will fail; the alarm ladder assumes every self-heal layer will fail. None run on a host machine.
- *Three self-heal layers, each catches a different class.* 1. Container restart (`systemd Restart=always` + `tinyassets-watchdog.timer`) — transient crashes, OOM recovery, hung-but-not-crashed. 2. GHA `p0-outage-triage.yml` auto-repair — six classes covered (OOM, disk-full, image-pull, watchdog-hot-loop, tunnel-token-manual, env-unreadable). 3. Deploy-side invariants — `deploy-prod.yml` asserts `/etc/tinyassets/env` is readable by the daemon user post-mutation and post-restart, then publishes `/data/release-state.json` for live status reconciliation.
- *Alarm ladder, host-phone-independent.* Pushover paging from GHA `alarm-sink` at threshold-cross (2 consecutive reds ≈ 10 min outage), `priority=2` + vibrate-tier initial, escalating re-page at 1h / 4h / 24h if `p0-outage` issue stays open with no human comment. Probe-without-paging is not an alarm path (2026-04-21 lesson).
- *DR validated end-to-end, on a schedule, from off-region copies.* A weekly scheduled drill provisions a fresh VM, bootstraps, restores platform state and a sample of command-center boxes from the off-region copies only into a fresh template environment with the pinned image (never the primary's secrets), starts the daemon, and asserts canary-green within SLA; a failed drill pages. Decoupled restore + start, exit-code propagation, SSH-tunnel probe; no host keystrokes bridge any step. As built (2026-10-02) the drill is dispatch-only, last ran 2026-07-24, and backups share the droplet's region; `target-architecture` S1 closes both.
- *Deploys cost no downtime, and a fenced standby covers the host and the region* (target architecture, 2026-10-02). Each command center has its own execution owner (agent loop, turn journal writer and reconciliation) under its own generation-fenced lease, and one platform lease runs the scheduler, triggers and outbox, behind blue-green frontends that queue only the affected command center's requests during its handover (amended 2026-10-02: a handover never makes one user wait on another's turn); the old owner drains before the new one reconciles, so no live turn is settled and no request fails. Schema-changing cutovers are declared maintenance windows. A warm standby in a second region restores continuously with its tunnel connector stopped, and is promoted only after every primary execution host is fenced (powered off through a CI-held credential); if fencing cannot be confirmed, promotion stops and pages. Recovery points: ~1 s for platform state (continuous SQLite replication), the backup interval for box files.
- *Uptime response uses ordinary primitives, never a privileged escape path.* Observation, alarms, diagnosis, approval, and remediation remain user-buildable and remixable workflow designs. Historical incidents may inform new designs but authorize no hidden task dispatch, repair, filing, merge, or deployment behavior.
- *Public-surface canary is required evidence, not final proof.* MCP/chatbot-facing changes also require live Claude.ai `ui-test` for final acceptance (Hard Rule #11).

**Substrate:** `deploy/`, `.github/workflows/uptime-canary.yml`, `.github/workflows/p0-outage-triage.yml`, `.github/workflows/deploy-prod.yml`, `.github/workflows/dr-drill.yml`, `scripts/uptime_canary.py`, `scripts/mcp_public_canary.py`. Acceptance probe catalog: `docs/ops/acceptance-probe-catalog.md`.

**Open evolution:** Validation of the testable assumption — if the host's phone is off for 24h, a secondary paging path (email / desktop / secondary device) still fires at the 4h escalation tick.

_Last audited: 2026-05-28_

---

## Reference: Target Architecture

**Target architecture (founder-approved 2026-10-01/02; `openspec/changes/target-architecture`):** per cell, an always-on control plane (MCP + app API, thin agent loop, scheduler/triggers/inbox/notifications, egress proxy, platform state in per-account SQLite under a platform root, continuously replicated off-region) in front of a box host running one sealed box per command center (Firecracker with snapshot/restore, gVisor fallback; fixed-size disk from the account quota; no NIC; awake only while acting). Postgres holds the cross-user transactional domains (catalog, ledger, inbox, market) behind an outbox. The edge routes each user to their home cell (one cell today). Box hosts and cells are capacity; the interfaces are fixed.

**Backend stack (target):** Postgres for catalog, ledger, inbox and market (decided 2026-07-25); the vendor is open (Supabase was the 2026-04-18 candidate: Realtime broadcast, Row-Level Security, S3-compatible Storage), and the choice is made when `target-architecture` S10 stands Postgres up. Identity is provided by WorkOS AuthKit. Postgres remains self-hostable without application rewrite.

**Auth + identity:** WorkOS AuthKit is the identity primitive across browser and local MCP clients. OAuth 2.1 + PKCE at the MCP edge (MCP spec 2025-11-25 mandate) maps every client session to the same stable WorkOS subject and therefore the same user universe. There is no anonymous principal and no unauthenticated access to platform data or actions: an unauthenticated request fails closed. Protocol discovery and sign-in bootstrap may be reachable before authentication only when they confer no principal, platform data, or platform action; operational probes use a named service principal. Session tokens are short-lived and scoped per user; refresh, logout, revocation, and concurrent sessions are explicit lifecycle operations. RLS enforces per-user visibility at the DB layer. Decision: founder directive 2026-09-03 (supersedes GitHub OAuth at launch).

**Real-time strategy — versioned rows + broadcast, NOT CRDT.** User collaboration is coarse-grained: users edit different nodes concurrently, or edit the same node with last-write-wins + update-since-you-viewed conflicts. Comments are append-only. Versioned Postgres rows + Supabase Realtime + presence channels covers this at a fraction of CRDT's complexity. CRDT is an escalation path for any specific artifact needing it later, not a baseline. Decision: 2026-04-18.

**Single canonical public entry point.** The daemon surface has exactly one public URL: `https://tinyassets.io/mcp`. Debug/diagnostic access is via Cloudflare Worker observability + tunnel logs, NOT a second public DNS record. The Worker requires a `mcp.tinyassets.io` hostname for internal tunnel-routing subrequests; this record is retained as Access-gated internal plumbing, not a second public surface. Host directive 2026-04-20; runbook: `docs/ops/dns-tunnel-single-entry-cutover.md`.

---

## Design Decisions

ADR-style index of decisions that don't fit cleanly inside one module.

- **Unified notes.** All feedback is timestamped, attributed notes on files. One system, one format, one durable store per universe.
- **Editorial feedback, not scoring.** Natural-language notes about what works, what's concerning, and whether a concern is provably wrong. No numeric rubric in the core loop.
- **TinyAssets MCP Server, not single-user daemon.** Control plane runs in the cloud (currently DO Droplet, formerly a host laptop); many named users connect through MCP clients.
- **Multi-tenant by design, single-tenant today as N=1.** Every daemon-related design must scale from `(user, daemon)` to `(N users, M daemons per user)` without rewrite. Any architecture that would require a migration to multi-user is rejected.
- **TinyAssets-first, domain-agnostic identity.** Fantasy authoring is an early benchmark domain, not the trunk.
- **TinyAssets competes on leverage, not lock-in or a lowered ceiling.** A power user can customize, compose, import, export, and run every agent component they would control in a bespoke setup. TinyAssets should remain the better choice because it adds the remix commons, preserved lineage and evaluation evidence, cloud/hostless uptime, plug-and-play installation, collaboration, and bindings to subscriptions users already pay for. If an advanced user must leave solely to express a legitimate agent architecture, the platform design is incomplete.
- **Branch-first collaboration.** Branches are first-class, long-lived, public-forkable. Reconciliation optional, no fixed mainline.
- **Canonical store is per-domain, not one store for everything (host-approved 2026-07-25).** The question "Postgres-canonical or file-canonical?" was miscast as global; it resolves by scoping each domain to the store that fits it. **Postgres is canonical for the platform's transactional domains** — catalog, ledger, inbox, and market. The 2026-04-18 one-way-door decision stands, now explicitly scoped to those four rather than to all state. **The OKF bundle is canonical for the commons** (see the Brain Module): knowledge is markdown + frontmatter files, and the SQLite/FTS/vector store over it is a rebuildable index. Neither store is canonical for the other's domain.
- **GitHub is an export sink for the transactional domains, not their canonical store.** GitHub receives a periodic flat-YAML export of public goals/branches/nodes; contributions via GitHub PR are accepted via a round-trip YAML → webhook → Postgres import path. (This says nothing about the commons bundle, whose *canonical* form is already files — for it, a git snapshot is the store, not an export of one.)
- **A user's brain organization is theirs to design (host-approved 2026-07-25).** The target experience is that a founder **designs their own custom MCP cloud brain organization** — modeled on Hermes, on OpenClaw, on an org-brain shape, or on something nobody has built yet. **OKF is the default organization when a user does not specify one, not a mandate.** Brain organizations are user-designable and remixable commons patterns: a good one is published, discovered, and remixed like any other commons artifact. This is Scoping Rule 1's corollary applied to the brain — "how should a brain be organized" has many plausible shapes, so it belongs to the commons, and the platform ships the substrate that makes any of them expressible.
- **Target architecture now, capacity later (founder, 2026-10-02: "i would like to move towards the architecture and dependencies we want later sooner rather than later. i want to do things correct the first time").** The final interfaces and data placement are built at small capacity: sealed command-center boxes behind `BoxProvider`, a thin loop and all timers in the control plane, platform state outside every box, Postgres for the four transactional domains, a user-to-cell seam with one cell, storage + seats with box-lifecycle metering and first-come host admission (wait, never refuse; a compute-hour budget stays a founder decision), one execution owner behind replaceable frontends, a fenced second-region standby and a scheduled off-region drill. Growth adds cells and box hosts; it never adds a code path. Firecracker on the DigitalOcean droplet or on a bare-metal box host is decided by a measured nested-KVM validation (slice S0). Change: `openspec/changes/target-architecture`.
- **User-controllable state architecture.** Users should eventually inspect, steer, and redesign tinyassets/state structure conversationally.
- **Capabilities are primitives the user's agent composes, not platform operators (founder-approved 2026-08-30).** The user's agent builds whatever workflow it wants from a small set of powerful primitives — ground-up design, build, test, redesign — remixes what others built in the commons, and can build a graph automation it was handed a link to. When a live failure suggests "add an operator / a special case", the question is which *primitive* is missing that would let the agent solve it itself; that primitive ships, the operator does not. Measured cause: 2026-08-29/30, four deploys of `$ta.*` body-transform operators to change one line of a fetched file, because nothing deterministic could run between a fetch and a write. The shape that follows: **effects fire at node time in graph order** (a node's declared channel calls run the moment it returns, a refused or failed write fails the node, later nodes can read earlier responses), and a **sandboxed code node** (deterministic Python with the node's data and every ancestor's response, no credentials, no network — authorship, not host approval, decides whose code runs; the OS sandbox bounds what it touches). The `$ta.*` vocabulary is frozen. **No structural cap on graph size** — nodes, effect nodes, edges — anywhere, served or connector; a big graph is bounded by usage (admissions, budget, consent, the sandbox's limits), never by its shape (founder, 2026-08-30; change `no-graph-size-caps`). OpenSpec change `sandboxed-code-node` (archived 2026-08-30, live proof #2728); next primitive: the `workspace` (change `workspace-node`).
- **An agent is a node (founder-approved 2026-09-27).** An agent's access is whatever context and tools its owner gives it, and the owner's own agents are "the same as itself" by default. A prompt node whose `tools_allowed` holds `agent` runs the same turn `converse` runs: the persona and brain, the shared agent loop, the engine tools pinned to the run's own universe and owner, and the owner's model preferences, with the node's `llm_policy` as a per-node override. It runs as one workflow step, foreground or background, until the turn finishes (the converse turn's own runaway backstop, not a node timeout), and writes its answer to graph state. A branch may hold any number of agent nodes beside ordinary steps. The run session resolves which node is calling from its admitted snapshot, and refuses a branch another user authored. The rest of `tools_allowed` is the owner's grant: the marker alone means everything the owner's chat has, and listed tool names narrow the node to exactly those on every provider surface. Each round is metered against the run's existing work receipt; no cap is added. A custom agent is a stored configuration of an agent node (instructions, grant, model, inputs and outputs), shared and remixed as a branch. That makes the `agent_runtime_*` second compiler, provider loop and grant model redundant; they are removed in a later lane. Change `agent-node-and-tool-grants`; code nodes reaching granted served tools through `invoke_mcp_action` is its second slice.
- **The system must evolve itself.** Stagnation is the worst failure mode.
- **Bad decisions are data.** When the daemon decides poorly, improve goals/tools/state/evals. Don't reflexively add rules.
- **Human control belongs at irreversible boundaries.** Bounded loops for autonomy; pause/stop/takeover/confirmation at the edge.
- **Currency naming + test rail.** Real currency reference is `Destiny (tiny)` with symbol `tiny`. Current paid-market tests use `test tiny` on Base Sepolia only. Mainnet Destiny/tiny settlement, staking, DAO voting, and treasury flows are deferred. See `docs/design-notes/2026-04-29-token-naming-and-test-currency.md`.

---

## Open Tensions

- **Structural scaffolding should shrink** as models improve — hard maxima and routing thresholds only survive if evals prove they help.
- **Hybrid memory must become one policy.** Retrieval and memory may be separate implementations but should behave like one coherent decision system from the daemon's perspective (Brain Module is the convergence point).
- **State contract mismatches are bugs.** TypedDicts, node outputs, and downstream consumers must agree.
- **Postgres-canonical vs GitHub-canonical — RESOLVED BY SCOPING (host-approved 2026-07-25).** It was never one decision. Postgres is canonical for catalog / ledger / inbox / market; the OKF bundle is canonical for the commons; GitHub is an export sink for the former and a snapshot of the latter. The two design shapes no longer compete — they own different domains. See Design Decisions and the Brain Module.
- **Private-data custody is an open research question, deliberately.** Custody is per-situation and user-chosen (host machine / private universe brain / vault / platform-held) and no lane may treat either the never-store or the platform-store position as settled. This tension stays open on purpose until the custody modes are researched against real use cases; see Scoping Rule 4.
