# model-reservations as built (2026-11-22)

`untangle/model-reservations`, owed by `spend-periods.md` (#5783): the owner object's own model
reservation path (`Usage.reserve`, `dispatched`, `settle`, `abandon`, `recover`, `receipts`,
the `model_reservations` table and its triggers, the `usageReceipts` door) has had no caller
since #5389 deleted the in-object pi loop. Every model charge is a sandbox call's, through
egress's two triggers.

## Research first (Fact 14), read on main 2aa7f61c2

- **Writers of `model_reservations`.** Only `usage.ts`: `reserve` (`#insert`), `dispatched`,
  `settle`, `abandon`, `recover` and `#prune`. No module of `src` in any Worker calls any of
  them (searched `platform/` for each name and for `.usage.`): the object built a `Usage` only
  to hand egress `spent` and `spendPruned` and the runs' limits `spent`, and to answer
  `usageReceipts`. Tests wrote it by hand (`egress.test` "share the source's day and month",
  `files-share` "keep at most CALLS_OPEN calls open", `restore.test`'s shaped owner).
- **Readers of the table or `usageReceipts`.** The door's handler alone (`usageDoors`, which
  ran `recover` first). No caller of the door: the doors Worker (`/api`, the app's routes, MCP
  tools), the app (`doors/app/app.js`), the starter skills, the plugins, the gateway, the
  sandbox and the site name neither; the MCP tool list (`tools` recorder) has no tool for it.
  The export (`export.ts`) and the restore's load order (`restore.ts`) carried the table as
  they carry every exported table; the change log captured it by its three `_logged_` triggers;
  erasure wipes the whole database and names no table; no recorder but `rpc` (every `OwnerDO`
  method) and `rv` (every row of `contract/doors.json`) reaches the door.
- **What outlives it.** `model_spend` (written only by `egress_reservations_charged` and
  `_recharged` now), `Usage.spent` (read by `egress.ts` `#reserve` and, through
  `launch.ts`, `runs.ts` `#limits`), `Usage.spendPruned` (run by egress's reserve), and
  `priced` (egress's price, handed in as `cost`, and its tests). `CALLS_OPEN` bounded only
  `model_reservations_open`; `USAGE_LIMITS` (`receiptsKept`, `receiptsListed`, `minOutput`,
  `minReasoningOutput`) only the deleted path; `ModelCaps` only its policy closure.
- **Specs and docs.** `specs/owner-object/spec.md` held four requirements of the object's
  reservations; `limits-audit.md` three rows (`USAGE_LIMITS.minOutput`, `receiptsKept`,
  `CALLS_OPEN`) and the term's 27.6 MB in its figures.

Nothing reads the table or the door, so the deletion goes ahead.

## Decisions

1. **The counters are egress's.** Every writer of `model_spend` is an egress trigger and its
   window (`keptFrom`, `SETTLED_WITHIN_S`) is the egress door's deadline, so `Egress` owns
   the read (`spent`, public: the runs' limits read it through the launcher) and the prune
   (`#spendPruned`, before each charge adds a counter). `EgressDeps` loses `spent`, `pruned`
   and `cost`, three hand-ins that existed only because another module held the counters and
   `priced` sat in a module the sandbox's spend may not reach. `priced` moves to `money.ts`
   (pure, pi-ai's `calculateCost` only), and `egressPrice` calls it. `usage.ts` goes whole.
2. **Owner migration 26 drops `model_reservations`**, its index and triggers with it (the
   open-calls bound, the spend triggers, the capture). It deletes no row first: deleting fires
   no spend trigger, a settled row's spend stays on its counters as it did each time the prune
   let one go, and no live object holds a row (no caller since #5389). Pinned; the export's
   shape is pinned again at schema 26.
3. **`FORWARD` 26 refuses older generations (`RESERVATIONS_DROPPED`)**: the table was exported,
   so a generation from before holds lines this schema has no table for. Migration 25 already
   refused every older one, and before the cutover no owner keeps a generation.
4. **The door goes from both halves of the door table** (`contract/doors.json`,
   `owner/doors.json`), the contract's `OwnerDoors`, and the generated `door-map.ts`.
5. **`CALLS_OPEN` goes from the contract.** Its frozen text stays in migration 1, so the
   frozen-bounds guard's `SHARED` counts its two 1000s (as frozen text of a dropped table) and
   its places count falls from 647 to 645. `egress.ts` `MIN_OUTPUT` takes the reason the
   deleted `minOutput` row carried.

## Tests

- Removed, as they only exercised the deleted path: all of `usage.test.ts` but what follows;
  `egress.test` "share the source's day and month with the model reservations"; the open-calls
  half of `files-share`'s bound test.
- Moved: "the models table of owner policy" to `policy.test.ts`; the bisection and the
  long-context tier price (now through `egressPrice`) and the µ$ rounding to `egress.test` "the
  spend caps".
- Rewritten onto egress (`egress.test` "the spend counters"): the kept window at its edges,
  each reserve pruning before it adds a counter, and a charge reserved before midnight whose
  settle after another call's prune moves its own counters. `restore.test`'s shaped owner holds
  a sandbox call charged, settled and pruned; `changelog.test` writes egress rows' lines.
- `usage.test` "past the bound migration 25 holds them" (a schema-24 object at 520 counters)
  goes: its claim, that every charge prunes before it adds a counter, is `egress.test` "prune
  the spend counters at each open" at the bound, and "are kept until every charge ... each
  reserve pruning before it adds one".

## Found and left

- The baseline's SQL and comments still make and describe `model_reservations` and its bound;
  the baseline is frozen text that migration 26 drops after it.
- Migration 25's comment names `usage.ts`, now deleted: a dated note of what that migration was
  for.
