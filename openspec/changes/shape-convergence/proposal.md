## Why

The founder, 2026-10-11: "a dozen different ways an agent could be spawned
leading to endless patches when the real answer was a shape already clearly
defined but never fully set in place and drifted from." Earlier the same day:
"build towards the correct shape now ... do not patch a known bad shape."

The Shape registry (`docs/architecture.md`, PR #4619) measures it. Of 27
declared shapes, 15 have drifted: the new path exists but is off or unwired
while the old path keeps running. A few examples:

- The thin loop sits behind `TINYASSETS_AGENT_LOOP`, and that flag is unset
  in every deploy file.
- `BoxProvider` has no production caller, while 34 `role_*` cells (7.9k
  lines) were all added after ADR-012.
- The v1 and v2 extension loaders are both live.
- `branch_tasks.py` and `branch_tasks_v2.py` are both imported.

Agents start in 32 ways (24 live), through 4 distinct starters, 9 admission
gates and 5 budget roots. Since 2026-09-27 the patches have landed on those
seams: about 31 model-call fixes, 17 cell fixes, about 20 chat-delivery fixes
and 16 approval or request fixes.

The cause is a process with no executable check (design §2). Shapes are
declared in prose. Cutovers land additively and defer deletion to "a later
lane". Four fleets then patch whichever path is live in front of them.

## What Changes

- **Guards first (C1).** A structural guard, `scripts/check_shapes.py`, runs
  on every PR from any fleet. It fails when:
  - a second path appears for a registered shape;
  - a drifted shape's old-path ceiling rises;
  - a converged shape regrows;
  - a new document lands outside the registered homes;
  - an accepted ADR names no spec or change.

  The same PR deletes the document directories that ADR-005 already retired
  (about −100k lines).
- **A reconciler with no host (C2).** A scheduled GitHub Actions workflow
  compares open PRs, branches and `openspec/changes/` against the registry.
  It keeps ONE tracking issue that every fleet and the founder read. The same
  item folds the duplicate and superseded changes in the queue.
- **The convergence order (C3–C12).** One ordered list. The decided
  one-model-call path (#4616 step 1) stays first among the code items. It
  folds in #4616's six steps and #4609's prune order. Every item deletes the
  path it replaces in the same release, names its guard, and has a negative
  net line estimate.
- **Fewer documents.** Design notes stop being a home. PRs #4608 and #4609
  close, with their content folded here. The design note in #4616 folds into
  this change, and its spec stays.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-coordination-runtime`: declared shapes have one path and an
  executable guard; a superseding change deletes what it supersedes;
  reconciliation runs with no host online; documents live only in the
  registered homes.

## Impact

- **Gate files.** C1 changes `scripts/ci_structural_guards.py` and C2 adds a
  workflow. Each gets one cross-family review round (AGENTS loop 4).
- **No product behaviour changes in C1–C2.** The deleted documents are not
  read at runtime. About 40 comment citations are repointed in the same PR.
- **C3 onward** are product changes. Each one ships under its own change, or
  under the change named in `tasks.md`, with its own floor review where it
  touches authority or money.
- **Out of scope.** Renaming universe to command center is not its own
  item: each slice renames what it touches, under a falling ratchet.
