# ADR-005: Retire PLAN.md; One Home per Kind of Fact

## Status

Accepted. Supersedes ADR-001 and ADR-002.

## Date

2026-10-06

## Context

`PLAN.md` had grown to 1,015 lines (127 KB). It was billed as design truth,
carried a "consult before building" rule and a founder-approval gate, and led
with a thesis ("a global goals engine") the founder has since replaced. Agents
take docs literally, so a stale plan steered them toward the old product.
Research basis: the 2026-10-06 base-files study (a short always-loaded map,
on-demand specs and decisions, history in git).

ADR-001 named `PLAN.md` as design truth and `STATUS.md` as live state.
ADR-002 budgeted an always-loaded set that imported `STATUS.md`, which was
retired on 2026-08-25.

## Decision

`PLAN.md` is deleted. Each kind of fact has one home; everywhere else links:

| Fact | Home |
|---|---|
| Direction, thesis, principles | `README.md` § Direction (founder-owned) |
| Un-inferable constraints and the loop | `AGENTS.md` |
| What the system is, module by module | `docs/architecture.md` |
| Why a choice stands | `docs/decisions/ADR-*` |
| As-built behaviour | `openspec/specs/` |
| Queued work | `openspec/changes/` |
| Unresolved findings | `docs/concerns/` |

There is no "consult before building" ritual. Founder approval applies to
README § Direction and to accepting direction-level ADRs. An ADR is not edited
after acceptance except its Status line; a changed decision is a new ADR that
supersedes it. The always-loaded budget is enforced by
`scripts/check_context_budget.py`.

## Consequences

- Direction lives in one short block, so a re-steer is one replacement.
- Design text that was aspirational or stale is gone from the search path;
  git keeps it (`git show 922e36d504:PLAN.md`).
