# Idea Inbox

Quick capture surface for loose ideas, user nudges, possible features, and
half-formed experiments.

## Rules

- Capture first, refine later.
- Keep one idea per entry.
- If an idea becomes work, a design note, or a plan, add the destination in
  `Links` instead of deleting the capture history.
- Merge duplicates during triage in `ideas/PIPELINE.md`, not during capture.

## Inbox

- [2026-09-26] (source: host, owner: rulebook-recut, status: captured, priority: infrastructure, size: medium) **Scale disk hygiene past the dev box.**
  **Why it is captured, not built:** founder, 2026-09-26 — *"scaled with users as needed, but that's mostly later."* Kept here rather than in a reference doc, because an unbuilt plan in `docs/reference/` reads as procedure.
  **What already survives the move:** every machine-specific value in `scripts/dev_hygiene.py` is a constant or a flag (prefixes, classes, age floors, keep budget, per-pass cap, roots), and the extra temp root defaults to the repo's own drive rather than a literal `C:\`, so the rules apply unchanged to the production host and to per-universe scratch.
  **The three classes that would have to be added:** **per-universe quotas** (a reclaim decision needs a tier quota, not one global floor), **scratch-lease reclamation** (the proof becomes "the lease expired", not "the mtime is old"), and **host disk expansion** (escalation should be able to end in "grow the volume").
  **Unchanged either way:** production pressure stays `DISK_AUTOPRUNE_PCT` plus `scripts/daemon_image_retention.py`.
  **Links:** captured when `docs/reference/dev-disk-hygiene.md` was folded into `scripts/dev_hygiene.py`'s docstring and `--help`; `scripts/dev_hygiene.py`, `.claude/hooks/dev_hygiene_hook.py`.

- [2026-07-30] (source: host, owner: codex-gpt5-desktop, status: promoted, priority: product-capability, size: large) **Universe-scoped custom agents that converse through Slack/other apps and build, test, and improve automations.**
  **Problem statement / HMW:** How might a founder create specialized, persistent agents inside their universe, reach each one from the communication tools they already use, and safely delegate the creation and continuous improvement of workflow automations without turning Slack, an app vendor, or hidden chat history into the system of record?
  **Recommended direction:** "Custom agent" is the user-facing role; the canonical substrate remains a soul-bearing **daemon** registered in the universe's learned org chart. Each agent has an explicit role, authority scope, memory scope, model/resource bindings, and allowed goals. Slack/other apps are replaceable boundary adapters: inbound messages become typed, addressable inbox artifacts; outbound replies and actions pass through scoped user grants, action caps, and the exactly-once effect ledger. The agent authors automation candidates as ordinary graph branches, tests them with locked evaluators/acceptance scenarios, iterates in bounded optimization runs, and promotes a winner only through the universe's existing review/authority gates. The universe remains the durable authority and record; channels are transports.
  **Approved product envelope:** users may create an OpenClaw-like general operator, a Hermes-like assistant, a coding agent, a new specialist, a common reusable configuration, or their own blend of many other users' public definitions. These are examples of configurations over one substrate, not hardcoded platform categories.
  **Directions considered:** (1) one universe persona handles every channel and task — simplest, but loses specialist identity and delegation; (2) stateless channel bots implemented only as workflows — composable, but weak on durable memory, accountability, and long-horizon iteration; (3) a universe roster of scoped daemons with channel bindings — recommended because it reuses identity, graph, evaluation, boundary, and provenance primitives without creating a parallel agent platform.
  **Key assumptions to validate:** users want named specialists rather than only one universe intelligence; Slack threads can map unambiguously to `(universe, agent, conversation)`; current daemon identity can be scoped to a universe without contradicting public/forkable daemon definitions; evaluator isolation is strong enough that an automation-authoring agent cannot rewrite its own test; channel retries and reconnects preserve exactly-once effects and conversation continuity.
  **MVP scope:** one founder creates one specialist agent in one universe; binds it to one Slack app/channel or DM; asks it conversationally to create one bounded automation; sees the proposed graph, dry-run evidence, and evaluator result; lets the agent iterate; explicitly approves promotion; then observes one scheduled run and its effect-ledger receipt.
  **Not doing (yet):** no new MCP action until a composition audit proves a missing primitive; no unrestricted external effects; no autonomous evaluator edits; no cross-universe agent authority; no platform-built catalog of app integrations beyond the generic boundary/commons-adapter contract; no agent marketplace or multi-agent swarm in the first slice.
  **Decision (host-approved 2026-07-30):** reusable agent definitions and their complete remix lineage are public/forkable; universe bindings, roles, authority, resources, channels, credentials, conversations, private inputs, and learned memory remain private. A blend is a new definition with all parent attribution preserved; it never copies private state from its sources.
  **Decision (host-approved 2026-07-30): full component-level customization with no artificial power-user ceiling.** Users may inspect, select, replace, remove, compose, import, and export soul/policy, prompts/context, tools/custom code, adapters, workflows/topology, triggers, memory policy/schema, provider requirements, evaluators, budgets, and promotion rules. An agent may propose a blend, but the component choices and lineage remain inspectable. Safety and accounting invariants remain platform-owned and cannot be removed.
  **Differentiation:** advanced users should stay because TinyAssets gives them more leverage than a bespoke setup — the remix commons, preserved attribution/eval evidence, cloud and hostless uptime, plug-and-play setup, collaboration, and private binding to subscriptions they already have — never because TinyAssets prevents portability or withholds customization.
  **Current implementation constraint:** the end state includes arbitrary custom code, but TinyAssets-managed cloud execution of untrusted code is blocked by the existing P1 OS-sandbox gap. Until true isolation lands, declarative graphs and governed capabilities may run in cloud while arbitrary code runs only on a user-controlled host. This stages the promise; it does not lower the power-user ceiling.
  **Links:** promoted to `PLAN.md` Daemon Platform, Providers, Distribution & Discoverability, Evolution & Evaluation, and Design Decisions; tracked in `ideas/PIPELINE.md`; extends the 2026-04-20 "Agent-teams-on-TinyAssets" promotion; aligns with `openspec/specs/boundary-layer/spec.md`, `openspec/specs/universe-personification-and-relay/spec.md`, and full-platform architecture §28.

- [2026-07-30] (source: host, owner: codex-gpt5-desktop, status:
  promoted-active, priority: uptime-platform, size: large) **Fully
  customizable agents in each user's universe.** Users publish and remix
  public component-level agent definitions, bind them privately to their
  universe and subscriptions, talk to them through Slack or other apps, and
  let them create, test, evaluate, and iterate user-authored automations.
  OpenClaw-like operators, Hermes-like assistants, coding agents, common
  configurations, and multi-user blends are examples rather than fixed
  platform types. The composition contract must have no power-user ceiling;
  TinyAssets wins through commons discovery, lineage/eval evidence,
  host-independent operation, plug-and-play governed bindings, and
  collaboration. The active first slice builds definitions, component
  provenance, private bindings, interchange, and canonical graph-handle
  targets. Live Slack effects wait for the outbound boundary layer; arbitrary
  managed-cloud code waits for the Engine OS sandbox.
  Links: `PLAN.md` Custom-agent corollary; `openspec/changes/universe-custom-agents/`;
  `STATUS.md` custom-agent lane.

- [2026-07-24] (source: host full-product vision + Codex navigator, owner: unassigned, status: captured-review-blocked, priority: post-control-plane, size: large) **Organization-owned shared brains and work through existing company systems.** Define an organization/workforce authority successor before adding Slack/Teams adapters: canonical organization, membership and group binding, scoped roles, offboarding, audit/export, shared-universe ownership, and requester-scoped execution authority bundles with immutable audit receipts and live revocation checks. Slack/Teams remain conformance adapters over the same command/inbox/effect boundary, never a second policy engine. Required order: PostgreSQL tenant authority -> identity/universe visibility -> organization/RBAC lifecycle -> generic connector boundary -> Slack/Teams adapters -> rendered and concurrent acceptance. Opposite-provider review required before promotion.

- [2026-07-24] (source: host regulated-industry vision + Codex navigator, owner: unassigned, status: captured-research-review-blocked, priority: post-authority, size: large) **Regulation-neutral workflow assurance composition, with HIPAA as the first profile.** Candidate successor `compose-regulated-workflow-controls` / capability `regulated-workflow-assurance`: versioned community control profiles map cited requirements to frozen capability gates, scoped evidence references, independent assessments, expiring exceptions, and fail-closed routing. Never add a “HIPAA mode,” infer legal status, copy PHI/PII into public evidence, or claim certification from self-attestation. Extend the same data model to GDPR/SOX without regulation-specific runtime branches or a new MCP action. Requires opposite-provider primary-source review plus organization authority, Postgres tenancy, BYOC isolation, immutable artifacts/evidence, effect receipts, retention/deletion, incident evidence, and counsel/assessor-owned acceptance.

- [2026-05-05] (source: host, owner: unassigned, status: superseded-by-user-automation, priority: archived, size: large) **Loop self-stewardship: historical privileged-loop framing retired.** Host direction 2026-07-26 removes the platform-owned cheat/community-patch loop. The useful residue—manual-intervention ledgers, self-correction rates, review memory, and promotion rules—belongs in user-authored, copyable, remixable automation designs built from generic primitives, not in a privileged product loop.

- [2026-05-02] (source: host, owner: unassigned, status: capture, priority: uptime, size: small) **Review open-source Claude Code / agent-team implementations for persistence lessons.** User flagged `https://github.com/SafeRL-Lab/nano-claude-code` (redirects to `SafeRL-Lab/cheetahclaws`), `https://github.com/SafeRL-Lab/cheetahclaws`, and `https://github.com/instructkr/claw-code` (redirects to `ultraworkers/claw-code`) as possible inspiration. Evaluate against TinyAssets' target advantages: 24/7 persistence without humans online, truthful daemon identity, user-authored/remixable automation without a privileged platform loop, and multi-provider durable coordination.

- [2026-05-02] (source: host, owner: unassigned, status: promoted, priority: post-#18, size: medium) **Daemon mini OpenBrain per soul-bearing daemon.** Research Nate B. Jones / Open Brain and adapt the pattern so each daemon controls an atomic, searchable memory backend that works with its existing daemon wiki. Direction captured in `docs/design-notes/2026-05-02-daemon-mini-openbrain.md`: wiki stays the curated face; mini brain is daemon-scoped capture/search/review/promote storage; observable memory traces query/retrieve/inject/write/promote/compact; no one flat pool, no soul copying, no Supabase/OpenRouter dependency by default.
- [2026-05-01] (source: host, owner: unassigned, status: reframed-user-automation, priority: commons-pattern, size: medium) **Patch-request incentives + requester-directed daemon work.** The incentive/routing idea remains available as a user-authored marketplace/workflow design composed from generic primitives; it does not authorize a TinyAssets-owned patch loop, privileged merge path, or platform policy. Users may copy/remix it and direct their own daemons, while acceptance and release remain separately governed.
  Links: `PLAN.md` Section Community-build over platform-build; `openspec/changes/archive/2026-08-26-retire-cheat-loop/`.

- [2026-04-27] (source: navigator-userim-review, owner: none, status: retired-community-composition, priority: post-uptime, size: medium) **`extensions action=my_recent_runs` + `goals action=my_recent` — user-scoped recency primitives.** Priya Session 2 (2026-04-20) signal #1: chatbot needs one tool call to answer "show me what I built recently" instead of fishing through `list_branches` + `query_runs` with author filter. Workspace-memory continuity gap — distinct from chatbot_assumes_workflow first-chat principle (this is N-th chat continuity).
  Resolution 2026-05-01: retired as platform primitives after freshness check. Use existing query-run plus optional goal/branch lookup composition; do not dispatch `_action_my_recent_*` code work.
  Triaged 2026-04-27; refreshed 2026-05-01: PIPELINE row "Recency primitives" records supersession by composition.
  Promoted 2026-04-27: pre-implementation contract now serves as supersession record in `2026-04-27-recency-and-continue-branch-primitives.md (archived in git)`.
  Historical fixture pack + implementation cards remain at `2026-04-27-recency-continue-fixture-pack.md (archived in git)` and `2026-04-27-post-18-recency-continue-implementation-cards.md (archived in git)`; do not treat the recency portions as pending engine work.
  Links: navigator's 2026-04-27 chain-break review (chat record); persona memory `priya_ramaswamy/`; `ideas/PIPELINE.md` "Recency primitives" row; `2026-04-27-recency-and-continue-branch-primitives.md (archived in git)`; `2026-04-27-recency-continue-fixture-pack.md (archived in git)`; `2026-04-27-post-18-recency-continue-implementation-cards.md (archived in git)`.

- [2026-04-27] (source: navigator-userim-review, owner: dev post-#18, status: dev-ready-after-18, priority: post-uptime, size: medium) **`run_branch resume_from=<run_id>` — explicit "extend prior run" parameter.** Priya signal #6 + Devin Session 2 echoed. "Extend the sweep" / "continue branch" has no clean TinyAssets path — chatbot has to semantically infer "clone this branch, add nodes, re-run with extended inputs." Same root concern as INBOX 2026-04-24 entry but with concrete API shape proposal.
  Files (when scoped): `tinyassets/api/runs.py` (add `resume_from=<run_id>` to existing `run_branch`); tests.
  Depends: #18 lock clears.
  Verification: persona replay plus live MCP `run_branch` call with `resume_from` proves chatbot routes to the existing run surface instead of re-scaffolding.
  Triaged 2026-04-27; refreshed 2026-05-01: MERGED with 2026-04-24 "Extend run / continue branch" entry into PIPELINE row "Continue-run resume primitive". No standalone `continue_branch` action; active dev-ready row is in `STATUS.md`.
  Promoted 2026-04-27: semantics + v1 envelope landed in `2026-04-27-recency-and-continue-branch-primitives.md (archived in git)` (sibling-branch mode, carry-over contract, deterministic errors), then retargeted to `run_branch resume_from=<run_id>`.
  Promoted 2026-04-27 (execution-ready): fixture pack + implementation cards landed at `2026-04-27-recency-continue-fixture-pack.md (archived in git)` and `2026-04-27-post-18-recency-continue-implementation-cards.md (archived in git)`.
  Links: 2026-04-24 INBOX entry (merged into same PIPELINE row); navigator's 2026-04-27 chain-break review; `ideas/PIPELINE.md` "Continue-run resume primitive" row; `2026-04-27-recency-and-continue-branch-primitives.md (archived in git)`; `2026-04-27-recency-continue-fixture-pack.md (archived in git)`; `2026-04-27-post-18-recency-continue-implementation-cards.md (archived in git)`.

- [2026-04-27] (source: navigator-userim-review, owner: navigator, status: reframed-community-build, priority: domain-skill, size: medium) **Methods-prose evaluator class — publication-grade methods correctness.** Priya signal #2: when chatbot generates publication-grade methods paragraph (library versions + CV description + algorithm config), nothing checks correctness. Cross-layer chain-break (pitch-vs-product gap): platform pitches "Evaluator-driven workflows" but methods-section prose has no evaluator.
  Triaged 2026-04-27; refreshed 2026-04-28: host declined a new platform primitive. Next slice is a docs-only reframe of `docs/design-notes/2026-04-27-methods-prose-evaluator.md` to preserve the intent as chatbot + wiki composition guidance. No `EvaluatorKind` extension.
  Promoted 2026-04-27 (content-ready): wiki rubric starter content landed at `2026-04-27-methods-prose-rubric-starter-pack.md (archived in git)`.
  Links: navigator's 2026-04-27 chain-break review; `ideas/PIPELINE.md` "Methods-prose evaluator" row; `2026-04-27-methods-prose-rubric-starter-pack.md (archived in git)`.

- [2026-04-27] (source: navigator-userim-review, owner: unassigned, status: triaged, priority: knowledge-graph, size: large) **Cross-algorithm methodological-parity guidance — `node action=compatibility_with` or wiki concept page.** Priya signal #4: RF needs pseudo-absences, MaxEnt doesn't. Less-experienced users submit papers with flawed cross-algorithm comparisons because chatbot doesn't surface the differences. Lower urgency than recency / continue_branch primitives but real safety surface.
  Triaged 2026-04-27: PIPELINE row "Cross-algorithm methodological-parity guidance" — needed design-note first to choose surface (verb vs wiki).
  Promoted 2026-04-27: wiki-first template path selected in `docs/design-notes/2026-04-27-cross-algorithm-methodological-parity-guidance.md`; next step is a concrete wiki concept page + one user-sim retrieval pass.
  Promoted 2026-04-27 (content-ready): wiki publish template + RF-vs-MaxEnt seed landed at `2026-04-27-cross-algorithm-parity-wiki-template.md (archived in git)`.
  Promoted 2026-04-27 (publish-ready): publication checklist landed at `2026-04-27-cross-algorithm-parity-publication-checklist.md (archived in git)`.
  Links: navigator's 2026-04-27 chain-break review; `ideas/PIPELINE.md`; `docs/design-notes/2026-04-27-cross-algorithm-methodological-parity-guidance.md`; `2026-04-27-cross-algorithm-parity-wiki-template.md (archived in git)`; `2026-04-27-cross-algorithm-parity-publication-checklist.md (archived in git)`.

- [2026-04-27] (source: navigator-userim-review, owner: unassigned, status: triaged, priority: observability, size: small) **Trust-graduation observability — "% users skipping dry-inspect on session N" as retention proxy.** Priya signal #7: skipped dry-inspect on Session 2 after using it on Session 1. No surface tracks this today. Platform-instrumentation, not chain-break per se.
  Triaged 2026-04-27: PIPELINE row "Trust-graduation observability" — observability backlog.
  Promoted 2026-04-27: metric/event pre-spec landed at `docs/design-notes/2026-04-27-trust-graduation-observability-metric.md` (`pct_skip_dry_inspect_on_session_n` + event contract). Implementation remains observability-lane blocked.
  Promoted 2026-04-27 (query-ready): SQL/dashboard pack landed at `2026-04-27-trust-graduation-query-pack.md (archived in git)`.
  Links: navigator's 2026-04-27 chain-break review; `ideas/PIPELINE.md`; `docs/design-notes/2026-04-27-trust-graduation-observability-metric.md`; `2026-04-27-trust-graduation-query-pack.md (archived in git)`.

- [2026-04-25] (source: navigator-audit, owner: unassigned, status: triaged) CONTRIBUTORS.md authoring surface — a canonical file listing node/branch authors with GitHub handles for Co-Authored-By attribution, seeded from the designer-royalties model (`project_designer_royalties_and_bounties`). Chatbot can read it to credit contributors in commit messages and pull request bodies.
  Triaged 2026-04-27: PIPELINE row "CONTRIBUTORS.md authoring surface" — needed design-note (standalone-file vs. daemon_server.py table + MCP surface).
  Promoted 2026-04-27: file-first canonical decision landed in `docs/design-notes/2026-04-27-contributors-authoring-surface.md`; daemon/MCP API explicitly deferred behind volume/pain triggers.
  Promoted 2026-04-27 (ops-ready): maintenance/hygiene runbook landed at `2026-04-27-contributors-maintenance-runbook.md (archived in git)`.
  Links: `ideas/PIPELINE.md`; AGENTS.md Hard Rule #10; `docs/design-notes/2026-04-27-contributors-authoring-surface.md`; `2026-04-27-contributors-maintenance-runbook.md (archived in git)`.

- [2026-04-20] (source: host, owner: navigator-followup, status: triaged,
  priority: post-uptime, size: large)
  **Agent-teams-on-TinyAssets: open-source-Claude-Code-analog as a user
  project, backed by our primitives.**
  Host framing: the Claude Code system prompt leaked; a Python open-source
  analog is growing fast on GitHub and trending toward one of the fastest-
  growing repos. Opportunity — build "Claude Code agent teams" where each
  teammate is another TinyAssets node invoked via a daemon-request, and the
  teammates live in another branch of the workflow. **This is a USER
  project, not ours to build.** Our job is to make sure the primitives
  exist on the platform so the user's chatbot can compose this — daemon
  requests across branches, teammate roles as nodes, inter-teammate
  messaging via the paid-market / free-queue bid layer, per-teammate
  provenance + audit.
  Why this matters: validates the "daemon economy + convergent commons"
  thesis with a concrete, viral-shaped use case that isn't fantasy-
  authoring. If the OSS Claude-Code-analog audience can compose their
  agent teams on TinyAssets, the platform rides that wave.
  Required primitives (load-bearing, to check against existing roadmap):
  (a) node → daemon-request invocation with typed inputs/outputs,
  (b) cross-branch teammate spawning without per-teammate auth friction,
  (c) teammate identity + soul-file attachment per `project_terminology_daemon`,
  (d) per-teammate provenance in the activity log, (e) inter-teammate
  messaging — bid market OR free-queue fan-out, probably both,
  (f) graceful partial-failure when one teammate's daemon is down
  (handled by daemon-economy fallback paths).
  Open questions to answer at triage time (NOT now): does this need a
  dedicated MCP verb set for teammate orchestration, or does it compose
  from existing `submit_request` + branch primitives? Is the OSS Claude-
  Code-analog's data model mappable to our node-type taxonomy without
  forcing a schema change? How does convergent-commons apply — should
  popular teammate definitions be wiki-shared, forkable, autoresearch-
  optimizable?
  Dependencies: uptime-track (all 14 self-host rows) lands first;
  daemon-economy first-draft (Track A + bid + settlement) lands first;
  then this becomes a scoping exercise.
  Triaged 2026-04-27: PIPELINE row "Agent-teams-on-TinyAssets" — research-note
  landed at `2026-04-20-agent-teams-on-tinyassets-research.md (archived in git)`
  (11-seam gap analysis, foundation/UX/commons rankings, nano-claude-code
  as recommended Python reference base — ~40K LoC, ~85 files, architectural
  seams map cleanly onto our primitives — claw-code now Rust-primary, less
  ideal base). Blocked on uptime-track close + daemon-economy first-draft.
  Scoping exercise opens after both unblock.
  Promoted 2026-04-27: post-unblock execution checklist landed at `2026-04-27-agent-teams-post-uptime-scoping-checklist.md (archived in git)` (entry gates + phase checks + escalation criteria).
  Links:
  - **`2026-04-20-agent-teams-on-tinyassets-research.md (archived in git)` —
    navigator research + thinking note. 11-seam gap analysis, viral-
    moment considerations, foundation/UX/commons work item ranking.**
  - `2026-04-27-agent-teams-post-uptime-scoping-checklist.md (archived in git)`
  - `project_daemon_product_voice.md` — "summoning the daemon" brand fit.
  - `project_convergent_design_commons.md` — teammate-definition sharing.
  - `project_daemon_default_behavior.md` — multi-spawn policy needs
    re-read in light of "one user spawning many teammates."
  - `docs/design-notes/2026-04-18-full-platform-architecture.md` — check
    §10 track decomposition for whether this falls under Track N
    (vibe-coding authoring) or needs its own track.

- [2026-04-24] (source: user-sim/Priya-Session2, owner: navigator, status: triaged,
  priority: post-uptime, size: medium)
  **"Extend run" / "continue branch" as a first-class primitive.**
  User-sim signal: Priya Session 2 ask "add BIOCLIM + RF for comparison on the same 14 species"
  has no clean TinyAssets verb. "New branch" implies fresh scaffolding. "New run" implies same
  algo-set. Chatbot must semantically infer "clone this branch, add algorithm nodes, re-run
  same species set." No existing primitive surfaces this as an intent. Chain-break: Interface 1
  primitive gap — chatbot improvises where it should have a clear tool.
  Scoping questions: (a) clone-branch-and-add-nodes vs. re-run-with-additional-params vs.
  new sibling branch? (b) does this need a new MCP verb (`extend_branch`/`continue_run`) or
  is it composable from `submit_request` + `clone_branch`? (c) what state carries over from
  the original run (params, results, species set)?
  Dependencies: in-flight run recovery part 2 (#6) should land first (resume semantics
  inform extension semantics).
  Triaged 2026-04-27; refreshed 2026-05-01: MERGED with 2026-04-27
  `run_branch resume_from=<run_id>` entry into PIPELINE row "Continue-run resume
  primitive". Same root primitive gap; this entry's scoping questions carry
  forward as the design-note's open questions.
  Links:
  - `2026-04-24-competitor-trials-sweep.md (archived in git)` Signal 2
  - `ideas/PIPELINE.md` "Continue-run resume primitive" row

- [2026-04-24] (source: user-sim/Priya-W&B-trial, owner: navigator, status: triaged,
  priority: domain-skill, size: small)
  **`hyperparameter_importance` evaluator node for scientific/ML domain.**
  W&B Sweeps computes hyperparameter importance (which knobs matter most across a sweep)
  automatically. TinyAssets has no equivalent. User-sim rates it "cheap to add, high-value
  for scientific users." Domain-specific — belongs in the scientific-computing skill module,
  not the engine. CV-as-first-class-primitive is the structural moat; this is a parity win.
  Triaged 2026-04-27: PIPELINE row "hyperparameter_importance evaluator node" — waitlist
  until science-domain skill catalog exists. Cheap-to-add parity win.
  Promoted 2026-04-27: domain pre-spec landed at `2026-04-27-hyperparameter-importance-evaluator-node.md (archived in git)` (inputs/outputs/errors/tests frozen); implementation remains module-lane blocked.
  Promoted 2026-04-27 (execution-ready): fixture pack + implementation cards landed at `2026-04-27-hyperparameter-importance-fixture-pack.md (archived in git)` and `2026-04-27-hyperparameter-importance-implementation-cards.md (archived in git)`.
  Links:
  - `2026-04-24-competitor-trials-sweep.md (archived in git)` Signal 4
  - `ideas/PIPELINE.md` "hyperparameter_importance evaluator node" row
  - `2026-04-27-hyperparameter-importance-evaluator-node.md (archived in git)`
  - `2026-04-27-hyperparameter-importance-fixture-pack.md (archived in git)`
  - `2026-04-27-hyperparameter-importance-implementation-cards.md (archived in git)`
- [2026-06-24] (source: audit 2026-06-24 SDLC/vibe-coding + Claude-large-codebases best practices (G1), owner: navigator, status: captured) Eval + trajectory-evaluation discipline for daemon OUTPUT quality (rubric'd golden runs + output-eval + trajectory-eval gate). Whitepaper's central thesis: generation solved, verification is the remaining work. Deepest agentic-engineering gap; aligns with existing Evaluator frame.
  Next: idea-refine -> navigator design -> opposite-provider review gate before any build
  Links: 2026-06-24-sdlc-vibe-coding-claude-best-practices-adoption.md (archived in git), PLAN.md (Evaluator), .claude/agents/critic.md
- [2026-06-24] (source: audit 2026-06-24 best-practices (G2b), owner: host-decision, status: RESOLVED 2026-08-27 — the premise was wrong: `.claude/settings.json` is TRACKED, so the deny-list was folded straight into it and the `settings.shared.json` template plus its `setup_claude_settings.py` merger were deleted) Committed home for shared Claude Code config: .claude/settings.json is gitignored, so deny-lists/hook-registrations/permissions can't propagate across machines/providers (contradicts blog's version-controlled exclusions). Commit settings.shared.json or a seed-from-template setup step.
  Next: decide shared-config story; unblocks team-wide deny-list (G2) + reflection hook (G3)
  Links: 2026-06-24-sdlc-vibe-coding-claude-best-practices-adoption.md (archived in git), .gitignore:22, .claude/settings.json
- [2026-06-24] (source: audit 2026-06-24 best-practices (G3), owner: engineering, status: captured) Automated session-reflection Stop/SessionEnd hook: continuous-learning + REFLECTION.md norm is manual and lapses (project's own feedback_hooks_enforce_session_norms says automatic norms become hooks). When a session changed durable state, prompt one-line reflection + propose AGENTS.md/memory/skill update while context is fresh.
  Next: author .claude/hooks/session_reflection.py; depends on G2b for shared registration
  Links: 2026-06-24-sdlc-vibe-coding-claude-best-practices-adoption.md (archived in git), .claude/hooks/
- [2026-06-25] (source: user-chat, owner: claude-code, status: RESOLVED — Slice 3) BUG-029 chain-drain tests went red after FEAT-006. RESOLUTION: it was a STALE-TEST bug, NOT a production bug. The tests cooled *unregistered* provider names; effective_chain (correctly) strips absent providers, so the drain check saw a local-only chain. In production a provider can only be in cooldown after being registered+attempted, so cooled⟹registered⟹survives effective_chain⟹drain detected correctly. The captured hypothesis ("drain check should use the pre-effective_chain chain") was WRONG — that would let an unregistered, never-cooled provider in FALLBACK_CHAINS block the all()-cooled check and silently disable BUG-029 backoff on cloud hosts. Fix: register the cooled providers in the 3 stale fixtures + add a regression guard (test_drain_runs_on_effective_chain_unregistered_api_does_not_block). Router unchanged. Verified 65 bug029 tests green on origin/main 3530cedc.
  Next: none — resolved in branch claude/bug029-chain-drain-fix.
  Links: -
- [2026-06-25] (source: user-chat, owner: unassigned, status: captured) PRE-EXISTING (not mine): 11 tests in tests/test_dispatcher_queue.py fail on clean origin/main b4e3f31b — universe_loop_not_declared on submit + IndexError/empty-queue on cancel/list/clamp invariants. Likely a soul-loop-declaration fixture drift (queue ops now require a declared universe loop). Found during loop-lease-reclaim work; unrelated to lease reclaim. Triage separately.
  Next: triage and choose whether this belongs in STATUS.md, PLAN.md, a design note, or an exec plan
  Links: -
- [2026-06-27] (source: user-chat, owner: codex-gpt5-desktop, status: promoted) **Tiny/TinyAssets naming and Workflow retirement.**
  Host decision: Tiny is the personified intelligence users and developers interact with; TinyAssets is the website, platform, GitHub/repository, distribution, and app/listing brand; Workflow/workflow is only a migration compatibility name and retires after staged replacement.
  Promoted to: `PLAN.md` Canonical Naming Boundary; `docs/design-notes/2026-06-27-tinyassets-hard-rename.md`; `2026-06-27-tinyassets-hard-rename.md (archived in git)`; STATUS.md Work row "TinyAssets hard rename plan".
- [2026-07-29] (source: user-chat + automatic-drain live evidence, owner: unassigned, status: captured) **Separate OpenSpec backlog refinery from the delivery drain.** The repaired sequential drain can now deliver continuously, but the fresh inventory is 33 active changes / 833 unchecked tasks with only 4 claimable, 31 blocked, and 9 host-owned rows. A delivery worker cannot drain policy/dependency debt by coding harder. Explore a bounded, evidence-producing refinery that periodically identifies oversized changes, refreshes stale blocker labels, proposes dependency/slice corrections, and promotes only reviewed claimable slices; it must not silently rewrite PLAN authority, invent implementation permission, or bypass opposite-provider gates. Measure claimable-pressure gain before proposing parallel Codex+Claude workers or provider rotation.
  Next: `idea-refine` -> OpenSpec proposal -> opposite-provider review; define read-only report vs. mutation authority and §14 concurrency/load proof before any multi-worker mode.
  Links: `docs/ops/2026-07-28-openspec-drain-supervisor.md`; archived change `openspec/changes/archive/2026-07-29-harden-drain-progress-orchestration/`; live run `output/openspec-drain-auto-20260729-132507/`.
- [2026-07-29] (source: drain exact-head review attempts, owner: unassigned, status: captured) **Make read-only peer review a true single-provider budget boundary.** `scripts/peer_agent.py claude` claims a read-only review path, but project/user hooks spawned nested Claude/Codex process trees even when the brief explicitly forbade delegation, burning both subscriptions. Claude `--bare` prevented hooks but also disabled OAuth/keychain auth; `--safe-mode --tools Read,Glob,Grep` preserved auth and prevented nesting, but the account-wide spend limit then blocked review. Harden the wrapper with a tested safe-mode/read-tool contract, child-process audit/fail-closed behavior, and a clear subscription-limit result so drains never mistake recursive review work for bounded verification.
  Next: `auto-iterate` + `idea-refine` -> focused OpenSpec change; test CLI command construction, child-tree policy, authentication behavior, and provider-budget diagnostics without changing write-mode semantics.
  Links: `scripts/peer_agent.py`; draft PR #1880; `openspec/changes/enforce-durable-drain-blockers/tasks.md`.
- [2026-08-04] (source: claude-code session 2026-08-04 worktree teardown, owner: unclaimed, status: captured) wt.py done still refuses squash-merged lanes when the branch has merged main into itself — git_squash_merge.is_merged_into exists to fix exactly this pile-up, but claude/quarantine-wave2 (PR #2257 MERGED, 0 commits unpublished, rev-list main..branch = 10) was still refused, and only --force cleared it. Habituating to --force on a script that deletes work is the risk. UNVERIFIED hypothesis: the cherry-based detection compares the branch's cumulative tree, which for a branch that merged main several times also carries main's own changes, so the synthesized patch matches nothing. Re-test on a fresh branch that merges main twice before proving it.
  Next: reproduce on a throwaway branch that merges main twice, then fix git_squash_merge.is_merged_into or fall back to gh pr state==MERGED plus a zero-unpublished-commits check
  Links: scripts/git_squash_merge.py; scripts/wt.py:170; 2026-06-24-branch-lifecycle-automation.md (archived in git)

## Expose Trigger as a user primitive (2026-08-04)

Found while activating the cloud drain. `write_graph` targets are `goal, request,
branch, universe, automation, agent, agent_binding`; `read_graph` adds
`runs/run/automations/connections/agents/...`. **Neither exposes `trigger`.**
`CloudAutomationSliceTrigger` exists only inside the cloud-automation lane.

So `target=automation` is the pre-built complex thing, and the primitive
underneath it is not available. A user who wants a scheduled workflow must take
our opinionated automation lane whole, and when its scheduler wedges they have no
composable way out — they can run a branch on command (`run_graph`, verified
live) but cannot express "run this branch version on this cadence" themselves.

Proposal: expose Trigger as a first-class primitive (bind to a branch version,
schedule/fire/cancel, list). Then a scheduled automation is *composed* from
Branch + Trigger by the user, our automation lane becomes one shareable design
rather than the only road, and unwedging is something the owner can do.

Do NOT add a `run_once` verb to `target=automation` — that was drafted and
rejected 2026-08-04 as redundant with `run_graph` and as more pre-built
complexity. The gap is the missing primitive, not a missing convenience.

Needs promotion to an OpenSpec change before any build.
- [2026-08-29] (source: #2674 follow-up, owner: engineering, status: captured) Served absolute cap as a user-set budget: the per-universe `absolute_cap_s` / `idle_timeout_s` knobs exist (`_served_knob`); expose them as a user setting through the app/MCP instead of the 3600s constant, alongside a Stop control (`docs/concerns/2026-08-29-no-user-stop-for-a-running-turn.md`).
- [2026-08-29] (source: founder to their universe in the app, 2026-08-29 08:34-08:42Z, owner: engineering, status: captured) Requests rail as the away-channel: when the founder is in live conversation, a background self's needs and findings surface IN the conversation (same brain); when the founder has not been around, they go to the waiting-on-you rail and become phone notifications. The rail is 'a timely, actionable notice', not a generic permission form. Pairs with the proactive background self (standing access: web, X, own GitHub, brain) that acts -- posts, finishes/pushes/tests an update -- rather than only leaving notes.
- [2026-08-30] (source: Codex round 2 on #2698, owner: engineering, status: captured, decided-not-now) Multi-slot in-flight records for the app thread: the in-flight record is ONE localStorage slot, so when a failed turn (resend offered) is followed by a later turn that succeeds, the failed turn's record is cleared and a refresh loses its resend offer (the note stays on screen until then). Identical to a manual second send today; deliberately not built with the persisted send queue (2026-08-30). If it matters, keep a small list of failed records keyed by message + ts and restore each with its own resend offer.
- [2026-08-30] (source: live app, 03:58Z, owner: engineering, status: captured) The build check reloads the page when a new build is live and holds only for a turn in flight; a message typed but not yet sent is discarded by that reload (observed once: a founder line typed during the #2706 deploy vanished from the composer). Hold the reload while the composer is non-empty, or stash the draft (like the send queue) and restore it after the reload.
- [2026-08-30] (source: founder, 05:55Z, owner: engineering, status: captured, needs OpenSpec before build — money + limits) Meter and charge a universe by its STRAIN on shared capacity rather than by fixed rate caps: the binding resources on the 1 vCPU / 2 GB droplet are, in order, memory-seconds (each served turn is a sandboxed CLI subprocess with its own RSS), concurrency slot-seconds (4 run workers + simultaneous served turns — a heartbeat every 5 min holds slots at zero token cost), CPU (graph execution, effect dispatch, transforms), shared-SQLite lock time (admission p95 3 ms → 34 ms under four concurrent settlers), storage bytes (runs, evidence, settlements, branch specs), egress requests/bytes through the effector proxy, and process/FD slots (bwrap + CLI per turn). Droplets are the placement unit, not a meter. Shape: keep the rate caps as safety bounds (injection/spam) and charge on consumption — slot-seconds, memory-seconds, storage, DB writes, egress — all derivable today from runs.db, served-budget reservations, the admission ledger and docker stats.
- [2026-08-30] (source: founder, 06:00Z, owner: engineering, status: captured) Tiers already exist - free and a $20/month subscription - so strain is bounded per TIER, not metered per unit: each tier carries its own caps on slot-seconds, memory-seconds, storage bytes, DB writes and egress; the flat fee means over-cap behaviour is throttle/queue (back off the universe's runs, hold new served turns), never a surprise charge; the free tier is where the caps bite (e.g. one served turn at a time, a smaller run budget, a shorter absolute cap), the paid tier roughly the production numbers today. Same OpenSpec change as the strain meters above. UPDATE (founder, 06:05Z): not enough data yet to say what free vs paid means - everyone is on free for now, one cap profile, raised as the founder builds and learns where the free limit belongs; the tier split waits for that data.

- 2026-08-30: **an effect chain continues past a refused node.** Runs
  `6cb4d9f48a9949be`/`7a7a91c14b0d4b8b`: `write_readme` was refused before
  the wire (`invalid_body_transform`), `open_pr` still fired and got a 422
  ("No commits between main and auto/tiny-…"), leaving dangling `auto/tiny-*`
  branches on the repo. Consider: a node after a refused or far-side-failed
  WRITE in the same branch is skipped with `skipped_after_failure` (reads may
  still run). Public packet semantics - OpenSpec-first.

- 2026-08-30: **the universe narrated a value the artifact does not hold.** After
  merging #2728 it said `README: 92 lines.`; the file it wrote says 91 (correct).
  The served reply should quote the run's evidence (the node output / the
  delivered body) rather than recompute a number in prose. Served prompt -
  founder-owned.
- Anchor a refreshable credential's ISSUER outside the credential itself. The refresh endpoint is derived from `iss` in a stored identity token, so a JWKS signature check only proves self-consistency: an attacker-controlled `iss` points at their metadata and their keys. Provenance needs the issuer pinned where the token cannot reach -- recorded at deposit, or bound to the grant. Filed 2026-09-27 after removing a JWKS check that bought self-consistency at the cost of an unhardened key fetch.
