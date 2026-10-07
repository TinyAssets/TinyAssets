---
name: openspec
description: Drives the OpenSpec CLI lifecycle — explore, propose, apply, sync, archive. Use when the user wants to think through a change, generate a reviewable change proposal (design + specs + tasks), implement its tasks, sync delta specs into main specs, or archive a completed change.
---

# OpenSpec

> Requires the `openspec` CLI (MIT, Fission-AI/OpenSpec; `npm i -g
> @fission-ai/openspec`). Specs live in the repo and persist across sessions.
> This is the CLI-managed, multi-session spec system. Where the CLI is
> unavailable, hand-write a change directory matching the OpenSpec layout
> (`proposal.md`, `design.md`, delta specs, `tasks.md`) so it can be adopted
> once the CLI is back — see `AGENTS.md` § *Spec-driven development*.

## Lifecycle

```
explore → propose → apply → sync-specs → archive
(think)   (artifacts) (build) (merge deltas) (finalize)
```

Each is an action on a change, not a rigid phase — invoke any of them anytime.
Always orient first with `openspec list --json` (active changes) and
`openspec status --change "<name>" --json` (schema, paths via `planningHome` /
`changeRoot` / `artifactPaths` / `actionContext`). Use those resolved paths —
never assume repo-local paths. If status reports
`actionContext.mode: "workspace-planning"` with empty `allowedEditRoots`, that
operation isn't supported in this slice — treat linked repos/folders as read-only
and STOP before editing.

At dispatch/triage time, also run `python scripts/openspec_flow.py audit` and
finish existing delivery WIP before admitting more. This is on-demand flow
control, not a mandatory session-start step.

## explore — a thinking partner

A stance, not a workflow: curious, visual (ASCII diagrams liberally), adaptive,
grounded in the actual codebase. **Never write code or implement features in
explore mode** — you may read/search/investigate and create OpenSpec artifacts
(that's capturing thinking), but if asked to implement, tell the user to exit
explore and create a proposal. Offer to capture crystallized insights into the
right artifact (requirement → specs, design decision → design.md, scope →
proposal.md, new work → tasks.md); the user decides — don't auto-capture.

## propose — generate all artifacts

Create a change and its artifacts in one pass. Derive a kebab-case name from the
user's description (don't proceed without understanding what they want):
`openspec new change "<name>"` → `openspec status --change "<name>" --json` for
the build order (`applyRequires`, `artifacts` with dependencies). Loop through
ready artifacts in dependency order: `openspec instructions <artifact-id>
--change "<name>" --json` gives `template` (structure to write),
`resolvedOutputPath`, and `context`/`rules` (constraints for YOU — never copy
these blocks into the file). Read completed dependencies for context, write each
artifact, re-check status, until every `applyRequires` artifact is `done`. Verify
each file exists before moving on.

A delivery change has one intent expressible in one sentence, one owner, one
branch, one PR, explicit acceptance/verification, and at most 12 total task
checkboxes. Reference full-product vision in ADRs/design notes/audits; never
bulk-convert it into active changes. Park incidental findings outside the
active queue. After scaffolding the change and before claiming or building it,
run `python scripts/openspec_flow.py check-change <name> --provider
<exact-session-provider>`. Every matching claimed row counts conservatively.
One active delivery change is allowed per exact STATUS identity; global WIP
stays visible and minting provider suffixes to evade the limit is a review
violation. Existing oversized changes remain diagnostic legacy state. The
12-task ceiling is a 2026-07-28 calibration to review on 2026-08-11.

## apply — implement the tasks

Select the change (announce "Using change: <name>" + how to override). `openspec
instructions apply --change "<name>" --json` returns `contextFiles` (read them
all — proposal/specs/design/tasks for spec-driven), progress, and the task list.
Implement pending tasks one at a time, keeping changes minimal and scoped, and
mark each `- [ ]` → `- [x]` immediately on completion. Pause and ask (don't
guess) on unclear tasks, design issues revealed mid-implementation, or blockers.

## sync-specs — merge deltas into main specs

Agent-driven intelligent merge (partial updates, not wholesale copy). Read each
delta spec (`artifactPaths.specs.existingOutputPaths`) and the main spec at
`openspec/specs/<capability>/spec.md`, then apply: **ADDED** (add, or update if it
exists), **MODIFIED** (apply just the changed scenarios, preserve the rest),
**REMOVED** (delete the block), **RENAMED** (FROM→TO). Create the main spec if the
capability is new. Idempotent — running twice gives the same result. Preserve
content not mentioned in the delta.

## archive — finalize a completed change

Always prompt for the change (don't auto-select). Check artifact completion
(`openspec status --json`) and task completion (count `- [ ]` vs `- [x]`); warn
and confirm if anything is incomplete (don't block — inform and confirm). If
delta specs exist, assess sync state and offer to sync first (recommended). Then
`mkdir -p "<changesDir>/archive"` and `mv "<changeRoot>"
"<changesDir>/archive/YYYY-MM-DD-<name>"` (fail if the target exists;
`.openspec.yaml` moves with the directory).

## Verification

- [ ] Oriented via `openspec list`/`status --json`; used resolved paths, not assumed ones
- [ ] No code written in explore mode; insights captured only with user consent
- [ ] All `applyRequires` artifacts `done` before apply; tasks checked off as completed
- [ ] Delivery admission checked; one intent/owner/branch/PR and no more than 12 total task checkboxes
- [ ] Spec sync preserved unmentioned content and is idempotent; archive confirmed on incomplete work
