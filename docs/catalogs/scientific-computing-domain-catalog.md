# Scientific-Computing Domain Catalog

**Status:** seed catalog.
**Created:** 2026-05-01.
**Purpose:** park scientific/ML domain work in a domain-owned catalog before
any engine or platform primitive is proposed.

This catalog is intentionally narrower than the cross-domain
`domain-pattern-catalog.md`. It answers: "what should the future
scientific-computing domain module own?" It does not authorize changes in
`tinyassets/`, tests, or MCP action tables by itself.

## Boundary Rules

- Domain rows land here before any code work starts.
- Engine work is out of scope unless a row proves an irreducible shared
  primitive gap and `PLAN.md` is updated with approval.
- Domain nodes should compose existing evaluator/run/artifact primitives when
  possible.
- Implementation files are future `domains/scientific_computing/` or an
  equivalent domain module, not shared engine code.

## Catalog

| Id | Domain node | Status | User signal | Existing artifacts | Implementation boundary | Acceptance before code |
|---|---|---|---|---|---|---|
| SCI-EVAL-001 | `hyperparameter_importance` evaluator node | scoped, lane-blocked | Priya W&B trial: users want to know which sweep knobs matter most. | `docs/design-notes/2026-04-25-hyperparameter-importance-node.md` (deleted 2026-10-06; in git history); `docs/specs/2026-04-27-hyperparameter-importance-evaluator-node.md`; `docs/specs/2026-04-27-hyperparameter-importance-fixture-pack.md`; `docs/exec-plans/active/2026-04-27-hyperparameter-importance-implementation-cards.md` | Future scientific-computing module only. No `EvaluatorKind` expansion, no engine dispatch change, no `tinyassets/` edits for v1. | Domain module location exists; fixture data is ready; v1 method remains observational and names that limitation in output. |

## SCI-EVAL-001 Notes

The node ranks hyperparameters by influence on a selected target metric across
an observed sweep table. v1 defaults to permutation-style importance over the
available rows, emits deterministic rankings, and produces warning entries for
constant or missing parameters.

This is a parity win against W&B Sweeps for scientific users, not a platform
capability gap. The platform already has evaluator/run/artifact concepts; the
missing piece is a domain-owned recipe and implementation.

## Open Next Rows

Future scientific-computing rows should use the same pattern:

1. Source user signal.
2. Existing artifact links.
3. Domain-owned implementation boundary.
4. Acceptance criteria before code.
5. Explicit statement of whether the row proves or does not prove a shared
   engine primitive gap.
