> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-05. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# TinyAssets Engine Restructure Plan

**Date:** 2026-04-05
**Status:** Converged plan. D1–D4 resolved in PLAN.md Debate Surface.
Awaiting user approval before implementation begins.

Detailed phase specifications for the TinyAssets Extraction described in
PLAN.md. This is the implementation reference; PLAN.md holds the
architectural principles and Design Decisions.

---

## 0. Resolved Positions

Converged positions from Claude/Codex debate (D1–D4 in PLAN.md):

1. The thesis is right: the repo contains a reusable long-running workflow
   engine plus a fantasy-writing specialization.
2. Contract repair comes before package extraction (D2).
3. Memory is primarily a scoping and truth problem; the three-tier model is
   conceptual; the public interface is deferred and organized around query
   semantics, not tier names (D3).
4. Each domain owns its own graph topology. The engine is a shared
   infrastructure library with optional orchestration profiles, not a
   mandatory shared graph (D1).
5. The `workflow/` package split happens only after the seam is proven
   in-place.
6. A minimal second-domain probe pressures the interface before the
   extraction is declared correct.
7. Fantasy Author keeps its domain names (scene/chapter/book/universe). Only
   shared `workflow/` infrastructure uses domain-agnostic names. A mapping
   table documents the generalized intent (D4).

---

## 1. The Thesis

Fantasy Author already contains two things:

1. A general-purpose long-running agent workflow engine:
   LangGraph state graphs, multi-timescale hierarchy, provider routing,
   hybrid retrieval, evaluation, constraints, planning, checkpointing, API
   server, desktop control surfaces, notes, and work-target scheduling.
2. A fantasy-writing skill set:
   the orient/plan/draft/commit/worldbuild/reflect behavior, narrative state,
   story search tools, world-state queries, and editorial criteria.

The restructure should make that seam explicit. Fantasy Author remains the
first and primary skill set, but the underlying engine should eventually be
usable by other long-running agent workflows.

The key caution is timing: the repo should not extract unstable boundaries into
an engine package before those boundaries are actually trustworthy.

---

## 2. Key Shifts

### A. Scoped Memory With Temporal Truth

Current state:

- The repo already has core-like, episodic, and archival memory modules.
- The bigger weakness is not the absence of tiers.
- The real weakness is that scope, temporal truth, invalidation, and
  promotion semantics are not explicit enough for branch-first,
  multi-author, multi-user operation.

Target state:

- Keep a three-tier interface because it is useful.
- Treat the tier names as projections over a deeper contract.
- Skills should be able to ask memory for the right scope and truth mode,
  not just the right storage backend.

Important memory axes:

- Scope: universe, branch, author, user/session, artifact
- Truth mode: active, historical, superseded, disputed
- Time: when a fact became true, stopped being true, or was observed
- Promotion status: tentative, repeated, canonical, learned heuristic

Three useful tiers still remain:

| Tier | Role | Current shape |
|------|------|---------------|
| Core | Always-in-context active constraints and identity | Premise kernel, workflow instructions, active notes |
| Episodic | Searchable recent experience and event sequence | Episodic memory, recent prose, traces, reflections |
| Archival | Long-term semantic and graph memory | KG, vector store, RAPTOR, world state DB, style/craft artifacts |

Key additions:

- Scoped memory records
- Memory consolidation and deduplication
- Temporal tracking for canon evolution and branch divergence
- Agent-controlled promotion / archival / forgetting
- Clear invalidation and supersession semantics

### B. Domain-Owned Graphs Over Shared Infrastructure

Current state:

- The repo's 4-level graph (scene/chapter/book/universe) is validated for
  Fantasy Author but not for arbitrary domains.
- Research confirms different domains have genuinely different graph
  topologies — routing logic, stop conditions, persistence boundaries —
  not just different depths on the same ladder.

Target state (resolved from D1):

- Each domain owns its own LangGraph graph topology.
- The engine (`workflow/`) is a shared infrastructure library: providers,
  memory, retrieval, evaluation, checkpointing, notes, work targets, API
  scaffolding.
- The engine offers optional reusable orchestration profiles (e.g. the
  4-level multi-timescale hierarchy). Domains may use, extend, or ignore
  them.
- Skills remain valuable within a graph: swappable phase implementations,
  domain tools, eval criteria, and state extensions customize what happens
  inside a domain's graph, not the graph shape itself.

A domain skill set provides:

- Its own graph topology (LangGraph StateGraph)
- Phase implementations for its nodes
- Domain tools
- Evaluation criteria
- State extensions (TypedDict)
- Memory schemas
- Optional API routes

### C. Context Compaction As An Engine Capability

Current state:

- The repo has checkpointing and some bounded context behavior.
- Compaction is not yet formalized enough as an engine service.

Target state:

- Summarize and compress long histories at explicit handoff points
- Clear large tool results after durable extraction
- Write structured progress artifacts at compaction boundaries
- Use durable checkpoints and artifacts as handoff anchors
- Bound tool outputs through filtering, summarization, and pagination
- Choose budgets per phase/model rather than freezing one architectural cap

The principle is:

- bounded context is necessary
- one fixed token number is not architecture

---

## 3. Target End-State Package Shape

This is the target package map, not the first implementation step.

```text
workflow/                          # Shared infrastructure library
  engine.py                        # Registration, startup, config
  config.py
  state/                           # Base engine state (TypedDict)
    base.py
  memory/                          # Memory backends + query routing
    interface.py                   # Query-semantic surface (not tier addressing)
    core.py
    episodic.py
    archival.py
    consolidation.py
  context/
    compaction.py
    working_set.py
    limits.py
  providers/
  retrieval/
  knowledge/
  evaluation/
  constraints/
  planning/
  checkpointing/
  tools/
    registry.py
    guardrails.py
  notes/
  work_targets/
  api/
    server.py
    auth.py
  desktop/
  ingestion/
  judges/
  learning/
  testing/
  profiles/                        # Optional reusable orchestration profiles
    multi_timescale.py             # The 4-level hierarchy, available not mandatory

domains/fantasy_author/            # First domain — owns its graph
  skill.py                         # Registration, metadata, domain config
  graphs/                          # scene.py, chapter.py, book.py, universe.py
  phases/                          # orient, plan, draft, commit, learn, reflect, worldbuild
  tools/                           # story_search, canon tools, world-state queries
  eval/                            # Editorial criteria
  state/                           # Narrative state extensions (TypedDict)
  memory/                          # Domain-specific memory schemas
```

Important caveats:

- This layout is the destination, not the first implementation step.
- Graph topology lives in `domains/`, not in `workflow/` (resolved from D1).
- The `profiles/` directory offers reusable orchestration patterns that
  domains may import, extend, or ignore entirely.

---

## 4. Domain Registration Interface

The engine should expose a deliberately small first interface. Since each
domain owns its own graph (D1), the interface is primarily about registering
a domain's infrastructure needs, not about injecting phases into a shared
graph.

Minimal v1 expectations:

- domain name and description
- graph factory (returns the domain's LangGraph StateGraph)
- state extensions (TypedDict additions to base engine state)
- domain tools
- eval criteria
- optional API routes
- memory schema definitions

Initial build behavior:

1. Load a registered domain.
2. Call the domain's graph factory to get its topology.
3. Merge state extensions into the base engine state.
4. Register the domain's tools.
5. Register eval criteria.
6. Mount optional API routes.
7. Wire engine infrastructure (providers, memory, checkpointing) into the
   domain's graph nodes.

Converged position:

- Keep v1 minimal.
- Do not force plugin-style discovery, multi-skill composition, or mandatory
  orchestration profiles into the first interface.
- Domains that want the 4-level hierarchy can import it from
  `workflow.profiles.multi_timescale`.

---

## 5. Multi-Timescale Profile (Optional)

The current hierarchy is a reusable orchestration profile, not the engine
contract (resolved from D1). Domains whose workflow fits this shape can
import it; others define their own topology entirely.

| Fantasy Author | General Intent | Timescale |
|----------------|----------------|-----------|
| Scene | Task | Short-horizon atomic action |
| Chapter | Batch | Medium-horizon consolidation |
| Book | Project | Longer-horizon planning and recovery |
| Universe | Workspace | Global maintenance, synthesis, strategy |

Profile characteristics:

- Configurable depth (a domain may use 2, 3, or all 4 levels).
- Each level provides routing/entry/exit conditions, health monitoring,
  stuck detection, consolidation points, checkpoint boundaries, and
  compaction triggers.
- Fantasy Author uses all four levels with its own domain names.
- A future domain that fits a different topology (e.g. Search → Evaluate →
  Synthesize) would not use this profile at all — it would define its own
  graph and import only the infrastructure it needs from `workflow/`.

---

## 6. Migration Strategy

### Phase 0: Contract Repair And Convergence

Goal:

- Repair the live seams before extracting them into a platform package.

Work:

1. Restore `work_targets.py` as real source and re-establish the contract used
   by runtime, API, and tests.
2. Converge notes on one canonical contract, including `tags`, `metadata`, and
   active-direction access semantics.
3. Decide whether the multiplayer Author-server substrate is canonical now.
   If yes, wire the missing HTTP routes and GPT contract to it.
   If not, explicitly roll back the schema/tests that already assume it.
4. Reconcile the shared host-tray API so desktop source and tests describe the
   same interface.
5. Remove or quarantine steering-era runtime behavior still affecting startup.
6. Run the targeted tests for these seams before any package move.

Why this is first:

- extraction before convergence will formalize drift instead of fixing it

### Phase 1: In-Place Interface Proof

Goal:

- Define the engine/skill seam while code still lives in the current package.

Work:

1. Introduce thin engine-facing protocols for phases, tools, memory access,
   and API route registration inside the current package layout.
2. Separate shared engine state from fantasy-specific narrative state.
3. Wrap the current fantasy implementation behind those interfaces without
   changing external behavior.
4. Keep compatibility imports and scripts intact.
5. Run the full test suite with the seam proven in-place.

Why this comes before extraction:

- if the interface is wrong, it is cheaper to fix before moving half the repo

### Phase 2: Extract After The Seam Is Proven

Goal:

- Move code into `workflow/` and `skills/` only after Phase 1 proves the seam.

Work:

1. Create `workflow/` and `domains/`.
2. Move infrastructure modules behind already-proven interfaces.
3. Move fantasy-specific modules into `domains/fantasy_author/`.
4. Update imports, entry points, and wiring.
5. Use temporary compatibility shims where needed.
6. Remove shims only when they stop earning their keep.

### Phase 3: Context And Memory Upgrades

Goal:

- Add the memory and compaction capabilities the research actually supports.

Work:

1. Implement compaction services and durable handoff artifacts.
2. Add tool guardrails for filtering, summarization, and pagination.
3. Implement memory consolidation.
4. Add temporal truth tracking to archival memory.
5. Add explicit scope handling across universe/branch/author/user/session.
6. Wire agent-controlled promotion and forgetting through tools.

### Phase 4: Minimal Generality Probe

Goal:

- Pressure-test the interface with a tiny non-fantasy workload before
  declaring the engine shape correct.

Candidate probe:

- document research / summarization
- notes
- retrieval
- extracted facts
- durable artifact output

Success condition:

- the probe uses the shared engine without fantasy-specific state leaking
  everywhere

### Phase 5: Ship Fantasy Author On The Extracted Engine

Goal:

- Get Fantasy Author running end-to-end on the extracted engine with the
  repaired multiplayer/API/desktop surface.

Work:

1. Finish remaining runtime/API/desktop wiring on the extracted layout.
2. Run the first supervised daemon session.
3. Validate GPT + API + daemon + host dashboard end-to-end.
4. Only then treat the extracted engine as the new normal.

---

## 7. What "Usable" Means

After the shipping phase:

- the host can run the engine with the Fantasy Author skill loaded
- GPT/API interactions reflect the actual multiplayer surface
- notes, work targets, and review state use one coherent contract
- the daemon can work autonomously with inspectable artifacts
- host dashboard and shared GPT roles are separated correctly
- the engine seam is real enough that a second domain is plausible, not just
  aspirational

---

## 8. What We Are Not Doing

- Not adopting Mem0, Letta, or Zep wholesale
- Not rewriting the whole LangGraph system from scratch
- Not pretending the package move itself is architectural progress
- Not turning "no subgraphs" into ideology
- Not freezing one universal token cap into the architecture
- Not declaring the engine correct before a second-domain probe pressures it

---

## 9. Risks

| Risk | Mitigation |
|------|------------|
| Extracting unstable seams ossifies current drift | Do Phase 0 before any package extraction |
| Import churn breaks things | Move only after interfaces are proven; run full tests after each move |
| Skill interface is wrong because it is designed from one example | Keep v1 minimal and pressure it with a second-domain probe |
| Generalized names lose clarity | Keep Fantasy Author naming as comments / aliases while transitioning |
| Memory interface adds indirection without value | Start thin; add consolidation and temporal truth only where evals justify them |
| Graph topology assumed universal | Domains own graphs by default; shared profiles are optional (D1) |
| Active cross-provider work conflicts | Coordinate through STATUS.md and debate in this file before landing implementation |

---

## 10. Open Questions

1. **Canonical persistence contract:** Which surfaces become authoritative for
   notes, work targets, and review state: mirrored JSON, SQLite tables, or a
   thinner abstraction over both?
2. **Package name:** Is `workflow` the right engine package name?
3. **Monorepo vs separate packages:** Should `workflow/` and `domains/` stay in
   one repo or become separately installable later?
4. **Minimal proof of generality:** What is the smallest non-fantasy probe that
   meaningfully pressures the interface?
5. **Subgraph escape hatch:** What is the smallest clean extension point that
   allows skill-owned subgraphs without overcomplicating v1?
6. **Multi-skill composition:** Can two skills run in the same workspace, and
   if so, what shared state model would make that sane?

---

## Approval

D1–D4 resolved. Design Decisions and TinyAssets Extraction section already
written into PLAN.md. STATUS.md Work table restructured around these phases.

This plan requires user approval before Phase 0 implementation begins.

Remaining open questions (D5 in PLAN.md): package name, monorepo vs separate
packages, config-driven vs code-driven domain loading, multi-skill
composition. These are not blocking Phase 0.
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   