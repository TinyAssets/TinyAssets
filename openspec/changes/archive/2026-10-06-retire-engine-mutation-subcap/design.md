## Context

The owner has authorized completing and deploying limits simplification. The
existing engine ledger atomically counts every admission against 900/hour and
run admissions against 300 writes/hour. Engine mutations also face a derived
600/hour ceiling to reserve capacity for the same owner's runs. No independent
physical-resource evidence supports that reservation; the total still bounds
recorded admissions. This is one retirement, not completion of the wider goal.

## Goals / Non-Goals

**Goal:** let the owner choose the mix within the existing total, removing the
engine-only refusal and its status/configuration representation together.

**Non-goals:** changing the total/write thresholds, provider spending authority,
workspace guards, byte/effect limits, admission failure modes, schema or historical
settlement meaning. No synthetic production workload or operator workflow edits.

## Decisions

- Delete the `engine_max` argument, derived helper and `REFUSED_BY_ENGINE` rather
  than accepting and ignoring a safety-looking argument. Repository caller search
  found no external argument users. Update generated mirror through the builder.
- Keep the transaction, total count, write count, row kinds and receipt lifecycle.
  Category observations remain valuable; a category is not necessarily a quota.
- Remove `activity.limits.engine_mutations` rather than report zero, null or the
  total under another name. No UI consumer was found; verify all callers and tests.
- Total refusals say admissions (runs and engine edits), not only runs. Ledger
  refusals remain distinct from quota refusals.

## Risks / Trade-offs

- An owner can exhaust their total allowance with edits, including failed
  validations: intentional removal of a same-owner strategy reservation, not
  permission to use other users' allocations. Test mixed kinds and separate users.
- Removing the category status key is a response-body change: declare it and
  preserve the current status ACL and both surviving numeric limits.
- Existing fail-open run callers remain fail-open on ordinary ledger errors;
  do not claim a stronger absolute cap than the unchanged contract. Fail-closed
  engine callers and tampered ledgers must continue refusing.
- This does not establish shared-host capacity or physical-cost fairness.

## Verification and migration plan

No migration: old rows retain their kinds, bindings and settlements. Prove engine
admission 601 succeeds below 900, admission 901 refuses, write 301 refuses, mixed
admissions cannot overrun the aggregate under contention, window expiry frees
capacity, and engine rows cannot be bound/refunded as reads. Exercise real engine
entry points, authority refusals and status through focused existing suites.
Compare Windows outcomes to a pinned base; require Linux CI evidence for affected
tests with skip census. Independent shape/basic-safety and exact-head approval
gate normal guarded merge. Deployment requires authenticated public canary and
deployed SHA containment. Owner-rendered acceptance is separately recorded; do
not claim it from unit tests or impose a production 900-call load for proof.

Rollback: revert the implementation commit and deploy via normal gates if total
or write enforcement, authority or settlement regresses. No data rollback is
needed; existing counts immediately apply under restored predicates.

## Open Questions

None blocks this retirement. Broader concurrency/activity/storage consolidation
and the later retained-space responsibility choice remain in the parent decision.
