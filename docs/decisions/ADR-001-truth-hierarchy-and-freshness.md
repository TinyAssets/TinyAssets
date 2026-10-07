# ADR-001: Truth Hierarchy and Freshness Rules

## Status

Superseded by ADR-005 (2026-10-06): `PLAN.md` and `STATUS.md` are retired.

## Date

2026-04-08

## Context

The project uses shared files to preserve state across sessions and providers:

- `AGENTS.md`
- `PLAN.md`
- `STATUS.md`

That model is still correct, but trust decayed because the files were allowed
to accumulate stale claims:

- `STATUS.md` mixed historical completion with current verification
- runtime behavior moved ahead of the documented live state
- environment changes invalidated older test/lint claims
- `PLAN.md` remained useful as design intent, but could no longer be treated
  as automatically current in every section

The failure was not "we need a new permanent truth file." The failure was that
the project lacked explicit rules for freshness, contradiction handling, and
evidence strength.

## Decision

### 1. Truth is typed, not singular

The project does not have one undifferentiated source of truth. It has three
primary truth surfaces with different responsibilities:

- `AGENTS.md` owns process truth
- `PLAN.md` owns design truth
- `STATUS.md` owns live-state truth

### 2. Evidence strength is ordered

When reconstructing truth, use this order of confidence:

1. `runtime-proven`
2. `implemented`
3. `verified`
4. `doc-asserted`
5. `unknown`

Interpretation:

- runtime artifacts, live logs, active databases, and current outputs outrank
  older document claims about what is happening now
- current source code outranks stale descriptions of what was supposedly built
- verification is only valid in the environment where it was actually run

### 3. Reality audits are diagnostic, not a fourth living truth file

Reality-audit documents may be created when trust is damaged, but they are
temporary diagnostic artifacts. They do not replace the three living files.

Their job is to:

- reconstruct confidence from evidence
- identify contradictions
- recommend repairs

Once stable conclusions are reached, those conclusions must be pushed back into
`AGENTS.md`, `PLAN.md`, and `STATUS.md`.

### 4. `STATUS.md` `Status` means landed/dispatch state only

In the Work table:

- `pending`, `claimed:*`, `done`, `blocked`, and similar states describe task
  progress
- they do **not** automatically imply current verification or current trust

Current trust must be stated explicitly in the `Verify` or `Trust` text using
labels such as:

- `current:`
- `historical:`
- `contradicted:`
- `unknown:`

### 5. Verification claims must be freshness-stamped

If a claim depends on tests, lint, runtime behavior, or environment state, it
must include:

- date
- environment
- evidence or command

Examples:

- `current: 2026-04-08, .venv Python 3.14.3, compileall passed`
- `contradicted: 2026-04-08, .venv Python 3.14.3, pytest fails collection`

### 6. Contradictions must be downgraded immediately

If current code, runtime evidence, or verification output contradicts an older
claim:

- rewrite the stale claim in `STATUS.md`, or
- add a `Concern` immediately if the contradiction has not yet been resolved

Do not leave stale certainty in place just because the older statement once
used to be true.

### 7. `PLAN.md` must be revalidated section by section when trust is damaged

When confidence in the plan is damaged:

- do not assume the whole file is false
- do not assume the whole file is true
- revalidate section by section against implementation and runtime evidence

The plan remains the design surface, but its authority is conditional on
ongoing alignment with the real system.

## Alternatives Considered

### Treat `PLAN.md` as absolute truth

Rejected.

This fails whenever plan misuse or stale architectural text accumulates. It
turns design intent into folklore.

### Create a permanent fourth source-of-truth file

Rejected.

This would increase drift risk by creating another place where stale certainty
could accumulate. Diagnostic reports are useful, but they should not become a
shadow authority.

### Trust only the code and ignore docs

Rejected.

Code shows implementation, not process rules, design rationale, or current
project priorities. The project still needs explicit shared files for
cross-session coordination.

## Consequences

### Positive

- shared files regain clear responsibilities
- current trust becomes easier to judge quickly
- stale verification is easier to detect
- contradictions become first-class instead of implicit confusion

### Costs

- `STATUS.md` maintenance becomes stricter
- agents must do more explicit evidence labeling
- some older rows will remain historical until refreshed

## Implementation Notes

- `STATUS.md` now treats old Watch rows as historical unless freshly labeled
- key stale verification rows were downgraded from implied current truth to
  `historical:` or `contradicted:`
- `AGENTS.md` now includes explicit truth/freshness rules
- `docs/reality_audit.md` records the initial reconstruction pass that led to
  this ADR

## Follow-Up

1. Continue reclassifying misleading `STATUS.md` rows as they are encountered.
2. Rebuild high-risk `PLAN.md` sections from evidence instead of memory.
3. Use reality audits only when trust is damaged enough that normal shared-file
   maintenance is no longer sufficient.
