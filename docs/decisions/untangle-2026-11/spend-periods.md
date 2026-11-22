# spend-periods as built (2026-11-22)

Owed by `fact15-caps.md` (#5781): the owner's spend counters kept 40 days and 3 months of
periods (`USAGE_LIMITS.daysKept`, `monthsKept`; `SPEND_KEPT` 4,300, a hundred sources at 43
periods) with no reason behind either number. Fact 15: a ceiling is recorded with its reason.

## Research: every writer and reader of `model_spend` (read at `c21c166d0`; #5781 merged in at `b6895aaae` changed none)

Writers, all the schema's triggers, each into the row's own `d:<day>` and `m:<month>`, the
periods it was reserved in:

- `model_reservations_spent` and `_respent` (migration 1, part 6): the object's own model
  reservations (`usage.ts`). Nothing in `src` calls `Usage.reserve`, `dispatched`, `settle`
  or `abandon` since #5389 deleted the in-object pi loop; only `usage.test.ts` does. In
  production the table is written by no one, and read by `usageReceipts` and `recover`.
- `egress_reservations_charged` and `_recharged` (part 11): a sandbox call's charge, set held
  at its reserve and settled by its door's `egressSettle`. The door holds a stream at most
  `EGRESS_DEADLINE_S` (a day); `Egress.#recover`, run first by every settle, settles a row
  still open past that and a beat `lost`, its charge `unknown`, which holds the same estimate
  as `held` and so moves no counter. So the last counter move of a charge lands within
  `EGRESS_DEADLINE_S + EGRESS_BEAT_S` of its reserve.
- The prune (`Usage.spendPruned`), run by every reserve before it adds a counter: the
  object's in `#prune`, a sandbox call's in `Egress.reserve` through `pruned`.

Readers:

- The ceilings: `Usage.spent` for the current `d:` and `m:` only, from `usage.ts` `reserve`,
  `egress.ts` `#reserve`, and `runs.ts` `#limits` (through `launch.ts` `spent`). The owner's
  limits are `daily_cost`, `monthly_cost` and each source's two, UTC (`policy-document.ts`);
  there is no other period.
- No view: `usageReceipts` reads the reservation receipts, not the counters; no door, MCP
  tool, starter skill, plugin or site page reads `model_spend` (searched `platform/` and
  `WebSite/`).
- The export (`export.ts`, `period source cost`) and the restore (`restore.ts`, loaded after
  the rows that add to it, at the stream's value): the rows as they stand, whatever is kept.
- Erasure wipes the whole database; it names no table.

So no reader needs a past period. What needs one is the late settle: a charge reserved at
23:59 settles into yesterday's counter. If that counter were gone, the trigger's upsert would
make it anew holding only the settle's difference (often negative), a counter that is wrong,
counts toward `SPEND_KEPT` and lasts until the next prune.

## Decisions

1. **A period is kept until every charge reserved in it has settled, and no longer.**
   `money.ts` `SETTLED_WITHIN_S` (`EGRESS_DEADLINE_S + EGRESS_BEAT_S`) is the longest a charge
   stays open, now the one name `Egress.#recover`'s deadline cut reads too (it spelled the sum
   itself). `keptFrom(now)` is `periods(now - SETTLED_WITHIN_S)`, and `spendPruned` deletes
   every `d:` before its day and `m:` before its month. `daysKept` and `monthsKept` go.
2. **`SPEND_KEPT` is 500:** a window of a day and a minute spans at most three days and two
   months (a minute past midnight on the 1st holds the 1st, the last day of the month before,
   and the one before that; and both months), five periods at the row's hundred sources. The
   hundred is the row's existing share of the 10 GB, unchanged here. The worst case falls by
   3.2 MB (`limits-audit.md` restated by the guard).
3. **Owner migration 25** drops and remakes `model_spend_kept` at 500. It deletes no counter:
   SQL has no "now" the object's clock agrees with, and every reservation prunes before it
   adds one, so the first after the migration lets the old periods go (tested from a schema-24
   object holding 520 counters). `model_spend` is exported and a generation from before may
   hold more than 500, so `FORWARD` refuses it (`SPEND_PERIODS`), declared before the cutover
   as 23's and 24's are. The export's shape is unchanged; its pin moves to schema 25.
4. **The frozen-bounds guard reads a trigger as the schema stands** (`Live`): a statement a
   later `DROP TRIGGER` removes enforces nothing, so it is not held to the bound. Without it,
   no migration could ever change a trigger's bound: the baseline's frozen 4,300 would fail
   `unheld` for good. Tables keep counting every rebuild (`MADE`), as before; no `TRIGGER`
   place had a dropped statement, so no count changed.

## Found and left

- The object's own reservation path (`Usage.reserve`, `dispatched`, `settle`, `abandon`,
  `recover`, its triggers, and the `model_reservations` receipts `usageReceipts` answers) has
  had no caller since #5389. Its open reservations have no deadline, so `SETTLED_WITHIN_S`
  is the egress door's alone; and `receipts`' recovery could still settle a dead instance's
  old reservation into a pruned period (one counter made anew, gone at the next prune). Its
  removal is a door, an exported table and a migration of its own.
- `SPEND_KEPT`'s hundred sources a period: the row's share, its reason the 10 GB's
  (`fact15-caps.md`'s Share clause, now five periods, 0.42 MB).
- `specs/owner-object/spec.md` says the counters are "pruned past their window at each open",
  still true.

## Owed

- [ ] Delete the object's own model reservations, which nothing has called since #5389: the
  `Usage` reserve path, `model_reservations` and its triggers, `usageReceipts`, with an owner
  migration (`untangle/model-reservations`).
- [x] Once #5781 merges, tick its `fact15-caps.md` spend-periods line and move
  `limits-audit.md`'s `SPEND_KEPT` Share clause to five periods and this head's figure
  (`untangle/spend-periods`: merged with main at `b6895aaae`). The kept window is wider than
  that line's "current and previous": a day and a beat, so three days at a month's first
  minute.
