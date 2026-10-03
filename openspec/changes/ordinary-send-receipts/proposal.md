# Exact ordinary-send recovery

Design gate only. Mobile recovery owns this branch; main baseline is
`8a8ec27532902cd4b61b9886ef194919dcf93e2e`. Observational patch `cf5ff2a5` stays frozen.

## Why

A phone send can finish after losing its reply stream. History/text matching
cannot identify that request; reposting can duplicate providers or effects.
Recovery needs an exact, read-only receipt and at most one dispatch per receipt.

## Bounded change

Use server-issued, non-executable preparation receipts followed by one
prepared-to-started admission transition. Retain exact queued/steered input
custody across existing claim/take_carryover/enqueue/take/settle/open_turn paths.
Persist an exact terminal and idempotent history projection. Add owner-only
prepare/receipt endpoints and browser recovery by persisted receipt ID.
An ordinary identity is resolved before dynamic consumer selection. Missing,
erased or uncertain identities never authorize execution or consumer conversion.

The parent assigned custody and narrowly necessary lifecycle design to this lane.
Account deletion changes require independent security/privacy review AND parent
coordination before implementation. No runtime files or live schema are changed
in this design gate. The executable three-store model is synthetic evidence only.

## Baseline and boundaries

Use current main's existing author transaction, current-home/deletion checks,
maintenance barrier and journal identities. Preserve the single-writer,
no-handover operating restriction. This proposal does not depend on held #4308,
change that branch, add lease machinery, or claim cross-store fencing/atomicity.
BOOT identity adds a conservative stale-preparation restriction; it grants no
execution authority. Cross-process delivery observes the stored original issuer
using existing process_liveness, never the engine process's BOOT (design section 7). Foreground/effect-review plumbing and placement are excluded.
No provider calls, new credentials/settings/privileges, replay, or watchdog removal.
