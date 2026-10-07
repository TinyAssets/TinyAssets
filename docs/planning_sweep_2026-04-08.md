> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-08. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Planning Sweep: 2026-04-08

## Purpose

Take a clean planning pass over the project after the recent season of:

- workflow extraction
- Universe Server / MCP work
- multiplayer substrate work
- trust-recovery work on `STATUS.md`, `PLAN.md`, and `AGENTS.md`
- discovery of runtime pressure and large-document handling limits

This document is not a new source of truth. It is a planning synthesis that
pulls together what now appears stable, what this season taught us, and what
decision fronts remain before resetting priorities.

## Where The Project Actually Stands

### 1. The project now has a real split between infrastructure and domain

This season made the engine/domain split real enough to matter:

- `tinyassets/` exists as shared infrastructure
- `domains/fantasy_author/` exists as the first domain
- `domains/research_probe/` exists as a second-domain probe

That means the project is no longer only "a fantasy writer with ideas about
generalization." It is now visibly becoming a workflow engine with Fantasy
Author as the proving ground.

### 2. The public interface has shifted materially toward MCP

The Universe Server is not speculative anymore. It exists, it is wired, and
it is seeing live traffic. That changes how the project should think about its
front door:

- GPT is still important
- FastAPI is still important
- MCP/Universe Server is now co-equal or more central than old GPT-first
  framing

### 3. Operational reality is ahead of some of the docs

This season proved the repo can move faster than its shared memory surfaces.
The main example was not architecture confusion. It was confidence confusion:

- structurally large parts of the plan were right
- present-tense completion claims were often too strong
- runtime and environment evidence outran shared-state updates

### 4. The system is alive, but not yet settled

The current state is neither "prototype fantasy app" nor "finished extracted
engine."

It is best described as:

- structurally advanced
- operationally transitional
- runtime-active
- trust-recovering

## What Stabilized This Season

These look like durable gains rather than temporary experiments.

### A. Shared infrastructure plus per-domain graph ownership

This appears to be the right structural move. The repo now supports the claim
that reusable infrastructure and domain-owned graph topology can coexist
without forcing one universal workflow shape too early.

### B. Work targets and review gates

The move away from a flat queue and toward:

- foundation-priority review
- authorial-priority review
- durable work targets

looks like a real architectural gain, not just a refactor.

### C. Multiplayer substrate

Sessions, authors, branches, runtime instances, and the ledger are now
concrete enough that multiplayer is part of the actual project, not just an
aspirational note.

### D. The truth model repair

This season forced the repo to mature its own governance:

- `STATUS.md` now distinguishes current vs historical trust
- `AGENTS.md` now has explicit truth/freshness rules
- the truth hierarchy is now written down in ADR form

That is not side process. It is now part of the project’s architecture.

## What This Season Taught Us

### 1. Structural extraction is easier than operational extraction

We learned that moving packages and creating `tinyassets/` is not the same as
finishing extraction.

The hard part is not:

- folder creation
- import rewrites
- protocol definitions

The hard part is:

- removing bridge dependencies
- making runtime execution actually independent
- making API assembly actually domain-driven

This means future extraction work should be judged by operational
independence, not package shape.

### 2. Shared files prevent amnesia, not drift

The three-file model was not wrong. It was incomplete.

What was missing was:

- freshness semantics
- contradiction handling
- evidence strength
- a way to demote stale certainty immediately

The season taught us that filesystem memory is necessary, but not sufficient.
The repo needed explicit confidence rules on top of persistent files.

### 3. Tool-driven context is the right destination, but the bridge is still real

This season validated the direction:

- explicit writer tools are better than indiscriminate prompt stuffing
- shared search context is better than fragmented retrieval bundles

But it also showed that the system still carries compatibility context through
`retrieved_context`, `memory_context`, and other transitional state.

So the lesson is not "the tool-driven approach failed."
The lesson is "the bridge is still active, and runtime pressure exposes it."

### 4. Runtime artifacts are not secondary evidence

Recent work showed that logs, active DBs, and output artifacts can be more
trustworthy than polished documentation when asking "what is true right now?"

That means planning and debugging need to start from:

- current logs
- current runtime outputs
- current environment

not only from the project’s intended story about itself.

### 5. Harness quality is now a core bottleneck

This season surfaced a non-obvious but important lesson:

- large-doc truncation
- tool-output limits
- stale verification surfaces
- drift between live state and shared docs

are not just "AI workflow annoyances."

They are now direct bottlenecks on project correctness. The harness is part of
the cognition stack in a literal sense here.

### 6. The project’s center of gravity is widening

Fantasy writing is still the benchmark, but the project is now proving things
about:

- durable agent coordination
- truth recovery
- multi-surface control planes
- long-horizon memory and review loops
- community extensibility through node registration

That widening seems real and should be treated as part of the product thesis,
not as accidental scope creep.

## What Did Not Stabilize

These remain live uncertainty zones.

### A. Runtime/API independence of `workflow`

The extraction is not fully operationally complete yet. `workflow` still
bridges through `fantasy_author` for runtime execution and API assembly.

### B. Verification baseline

Current verification is not trustworthy enough:

- `compileall` passes
- `pytest` does not currently collect cleanly in the active `.venv`
- `ruff` is not currently clean

The project still lacks a fresh, trustworthy baseline for "what passes now."

### C. Retrieval/runtime pressure

Current daemon logs still show:

- provider exhaustion
- empty prose failures
- embedding failures
- retrieval instability
- context budget overruns

This means runtime quality and runtime resilience are still active engineering
problems, not only polish.

### D. Large-document access path

We learned this season that the repo already exceeds long-file handling limits
for some agent/file/tool paths. That means the project does not yet have a
safe, deterministic way for agents to inspect its own biggest text artifacts.

This is a harness and governance gap, not just a convenience gap.

## The Current Decision Fronts

The project is now sitting at five real decision fronts.

### 1. Trust/Harness front

Question:

- do we finish the large-doc query tool and related guidance now, so future
  sessions can reason over `PLAN.md`, `notes.json`, and other large artifacts
  safely?

Why it matters:

- without it, the project risks repeating documentation drift and agent-side
  misreads

### 2. Verification front

Question:

- do we restore a fresh "what passes now" baseline before deeper runtime or
  product work?

Why it matters:

- without a current baseline, every later change inherits uncertainty

### 3. Extraction front

Question:

- do we finish the runtime/API bridge extraction next, or accept the bridge
  for a while and prioritize runtime quality first?

Why it matters:

- this is the main remaining mismatch between the project’s architecture story
  and its actual execution path

### 4. Runtime quality front

Question:

- do we focus next on provider/retrieval/context pressure so the live system
  behaves better under actual use?

Why it matters:

- the project is already running live
- runtime failures now matter more than purely structural cleanliness

### 5. Productization front

Question:

- do we push outward on MCP registry, stable tunnel, more clients, and
  broader use now, or hold that until trust/harness/runtime are cleaner?

Why it matters:

- the public interface is becoming real enough that rough edges now scale

## Priority Frame

This is not the final priority decision. It is the frame I think we should use
when making that decision.

### Priority Tier 1: Preserve reasoning quality about the project itself

This tier exists so the repo can think clearly about itself.

Includes:

- large-doc query tool
- explicit large-artifact guidance
- fresh verification baseline

Why this tier is first:

- if agents cannot reliably read the project and cannot trust current
  verification, later prioritization gets noisier and more expensive

### Priority Tier 2: Remove the biggest architecture/reality mismatch

This tier is about the highest remaining structural contradiction.

Includes:

- `workflow` runtime/API bridge removal or explicit postponement decision

Why this tier is second:

- it is the largest remaining gap between the current architecture story and
  the current implementation

### Priority Tier 3: Improve live runtime behavior

This tier is about making the running system healthier.

Includes:

- provider exhaustion behavior
- retrieval instability
- context-budget overrun handling
- embedding/indexing reliability

Why this tier is third:

- the project is already live enough that runtime pain is real
- but runtime tuning on top of unclear harness/verification can waste cycles

### Priority Tier 4: Expand outward-facing surface area

This tier is about reach and distribution.

Includes:

- stable tunnel
- MCP registry listing
- broader client usage
- multiplayer live trials at larger scale

Why this tier is fourth:

- it multiplies the value of the system, but it also multiplies the cost of
  unresolved rough edges

## My Current Read

If we were resetting priorities from scratch right now, I would not start with
"new capability."

I would start with:

1. harness/trust readability
2. current verification baseline
3. bridge decision on `workflow`
4. live runtime pressure
5. outward expansion

Reason:

- this preserves the project’s ability to reason correctly about itself
- removes the biggest known structural ambiguity
- then improves the thing that is already running

That order matches what this season taught us.

## Suggested Next Priority Discussion

When we come back to priorities, I think we should answer only these questions:

1. Do we finish the large-doc query tool before anything else?
2. Do we restore a current verification baseline before bridge extraction?
3. Do we finish `workflow` runtime/API independence now, or explicitly defer
   it in favor of runtime quality?
4. Which live runtime pain is most urgent: provider fallback quality,
   retrieval instability, or context budget pressure?

If we answer those four questions cleanly, the next phase priorities should
become obvious.
