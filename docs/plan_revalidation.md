> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-08. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# PLAN Revalidation

Date: 2026-04-08

## Purpose

Revalidate the highest-risk sections of `PLAN.md` against current
implementation and runtime evidence without editing `PLAN.md` directly.

This report does not assume `PLAN.md` is fully correct or fully stale. It
checks each high-risk section separately.

## Status Labels

- `retained`: still supported by current code/runtime evidence
- `bridged`: direction is still correct, but current implementation is
  transitional or only partially matches the stated target
- `contradicted`: current code/runtime materially disagrees with the section
- `unknown`: not enough current evidence to trust the section yet

## Highest-Risk Sections

### 1. `System Shape`

Status: `bridged`

Why:

- The overall control-plane shape still exists: API/MCP in front of a daemon
  with state, tools, evaluation, and providers underneath.
- But the diagram is stale in emphasis. The project is no longer best
  described primarily as `User <-> Custom GPT <-> FastAPI / MCP`.
- Current runtime evidence shows live MCP traffic and active TinyAssets MCP server
  usage.

Evidence:

- `fantasy_author/universe_server.py`
- `logs/mcp_server.log`
- `fantasy_author/api.py`

Assessment:

- The architecture is still roughly right.
- The public-interface center of gravity has shifted toward MCP/Universe
  Server more than the section currently reflects.

### 2. `Multiplayer Author Server`

Status: `retained`

Why:

- The core multiplayer substrate described here is present in code.
- Session, author, branch, runtime, and ledger surfaces exist.
- Shared host-tray/dashboard concepts also exist.

Evidence:

- `fantasy_author/api.py`
- `fantasy_author/author_server.py`
- `fantasy_author/desktop/host_tray.py`
- `output/.author_server.db`

Assessment:

- The section is directionally and structurally sound.
- It should still be read as “core implemented, scale limits remain” rather
  than “fully battle-proven.”

### 3. `Scene Loop`

Status: `retained`

Why:

- `orient -> plan -> draft -> commit` still exists as a real execution shape.
- The loop is still central to the runtime.

Evidence:

- `fantasy_author/nodes/orient.py`
- `fantasy_author/nodes/plan.py`
- `fantasy_author/nodes/draft.py`
- `fantasy_author/nodes/commit.py`

Assessment:

- The section still reflects real structure.
- It remains a valid design description.

### 4. `TinyAssets Hierarchy`

Status: `bridged`

Why:

- The scene/chapter/book/universe hierarchy is still present and meaningful.
- But transitional compatibility structures still exist, especially
  `task_queue`, which means the system has not fully moved to pure
  target/artifact-driven control.

Evidence:

- `fantasy_author/graphs/universe.py`
- `fantasy_author/state/universe_state.py`
- `fantasy_author/nodes/foundation_priority_review.py`
- `fantasy_author/nodes/authorial_priority_review.py`

Assessment:

- The hierarchy still exists for timescale separation.
- The section should be kept, but with clearer acknowledgment that queue-era
  compatibility remains active.

### 5. `Work Targets And Review Gates`

Status: `retained`

Why:

- The foundation-priority and authorial-priority review gates are real.
- The work-target model and guarded fields are implemented.
- The universe graph topology now routes through those review gates.

Evidence:

- `fantasy_author/graphs/universe.py`
- `fantasy_author/nodes/foundation_priority_review.py`
- `fantasy_author/nodes/authorial_priority_review.py`
- `fantasy_author/work_targets.py`

Assessment:

- This section still matches the code well.
- The main caveat is that compatibility queue fields still exist alongside the
  newer target-driven flow.

### 6. `Retrieval And Memory`

Status: `bridged`

Why:

- The tool-driven writer surface exists and is real.
- Shared search context and writer tools are present.
- But the runtime still carries pre-assembled `retrieved_context` and
  `memory_context` fields through orient/plan/draft.
- Current logs show retrieval instability and context-budget pressure.

Evidence:

- `fantasy_author/nodes/orient.py`
- `fantasy_author/nodes/plan.py`
- `fantasy_author/nodes/draft.py`
- `fantasy_author/nodes/writer_tools.py`
- `logs/daemon.log`

Assessment:

- The target architecture is still the right one.
- The section is overstated if read as “finished.” It is better read as
  “target direction with partial implementation and active runtime stress.”

### 7. `Providers`

Status: `retained`

Why:

- The provider router still expresses fallback chains and cooldown handling.
- Failure is logged loudly rather than silently masked.
- Current runtime logs show the system surfacing provider exhaustion and
  degraded fallback behavior explicitly.

Evidence:

- `fantasy_author/providers/router.py`
- `fantasy_author/providers/quota.py`
- `logs/daemon.log`

Assessment:

- The principle still holds.
- Runtime evidence shows resilience exists, but quality under sustained
  fallback pressure remains a live operational issue.

### 8. `API And GPT Interface`

Status: `bridged`

Why:

- The control-station principle still broadly holds: the daemon remains the
  author, not the GPT surface.
- But the section underweights the current TinyAssets MCP interface,
  which is now a live primary path.

Evidence:

- `fantasy_author/api.py`
- `fantasy_author/universe_server.py`
- `logs/mcp_server.log`

Assessment:

- Keep the principle.
- Update the section later so it reflects GPT plus MCP, not GPT as the main
  framing.

### 9. `Live State Shape`

Status: `bridged`

Why:

- Thin live-state ideas are implemented in part: selected target IDs,
  review artifacts, execution refs, work-target refs, timeline refs.
- But live state still carries counters and transitional queue state.

Evidence:

- `fantasy_author/state/universe_state.py`

Assessment:

- The section is still directionally right.
- It should be revised to explicitly acknowledge transitional fields that are
  still part of the current state contract.

### 10. `TinyAssets Extraction`

Status: `contradicted`

Why:

- The structural extraction absolutely happened.
- But the section’s completion language is too strong for current reality.
- `workflow` runtime and API are still bridge-mode instead of fully
  independent.

Evidence:

- `tinyassets/__main__.py`
- `tinyassets/api/__init__.py`
- `tinyassets/discovery.py`
- `tinyassets/registry.py`

Assessment:

- The extraction section should be split into:
  - structural extraction complete
  - runtime/API independence still incomplete
- The sentence “all complete as of 2026-04-06” is not safe as a present-tense
  statement.

## Lower-Risk Sections That Still Look Sound

These sections were not the main trust risk and still appear broadly valid:

- `Project Thesis`
- `Cross-Cutting Principles`
- `State And Artifacts`
- `Daemon-Driven`
- `Evaluation`
- `Harness And Coordination`

They should still be re-read cautiously, but the current repo shape supports
them more than it contradicts them.

## Recommended PLAN Edits

Do not apply automatically. These are proposed edits for approval later.

1. Update `System Shape` to show the TinyAssets MCP surface as a first-class public
   interface, not only Custom GPT.
2. Revise `Retrieval And Memory` to say the tool-driven surface is partially
   implemented and still coexists with pre-assembled compatibility context.
3. Revise `Live State Shape` to acknowledge transitional counters and
   compatibility queue fields explicitly.
4. Rewrite `TinyAssets Extraction` so it distinguishes:
   - package extraction complete
   - runtime/API independence still bridged
5. Add a note in `API And GPT Interface` that the GPT is one control surface
   among several, not the sole or primary framing anymore.

## Bottom Line

`PLAN.md` is not globally broken.

It still holds up well as:

- thesis
- direction
- architectural intent

It is least trustworthy where it:

- declares extraction or verification complete in a present-tense way
- under-describes the TinyAssets MCP shift
- describes target-state thinness/tool-driven context as if the transitional
  bridge is already gone

The safest read is:

- most of `PLAN.md` remains usable as design intent
- several high-risk sections need explicit “target vs current bridge” wording
  before the file should be treated as fully current again
