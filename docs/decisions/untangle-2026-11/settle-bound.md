# settle-bound: a call's settled cost is held to what the counters hold exactly (2026-11-23)

`fix/settle-bound`. Found by #5834's reviewer: since #5834 a call settles at the vendor's own
bill when pi-ai marks one (`usage.cost.totalFromProvider`), and a finite but absurd bill
(1e308, or simply far above the call's reservation) is taken as its cost.

## Research (Fact 14), before the design

### A call, reserved to settled (main at `916f22959`)

1. **Reserve** (`owner/src/egress.ts` `#reserve`): the named model's call at its context
   window of input and the output the caps leave, at the dearest rates of every model it is
   priced by, times its tier's multiplier, in µ$ (`money.ts` `priced`, `costMicros`). A model
   record's prices are at most $1,000,000 a million tokens and its window and output at most
   10^8 tokens each (`contract/src/model-record.ts`), the dearest tier multiplier 2.5
   (`api-rules.ts`), so a reservation is at most $5 × 10^8 (5 × 10^14 µ$): an exact integer in
   a JavaScript number, the reason `limits-audit.md` records for those record bounds. The
   row's `est_cost` is the hold; triggers add it to the run's `cost` (`runs`) and the source's
   day and month (`model_spend`).
2. **Meter** (`gateway/src/meter.ts` `meteredUsage`): pi-ai's read of the answer, the vendor's
   bill when marked, else the catalog's price; `null` (unknown) when not finite or negative.
   `forward.ts` then multiplies it by the tier's `absent` multiplier when the answer names no
   tier and writes the outcome line (`outcomeLine`, `JSON.stringify`). The gateway does not
   know the reservation (`Metering` carries the output cap, not `est_cost`).
3. **Door** (`doors/src/egress.ts`): reads the line (`outcomeOf`) and hands it to the owner's
   `egressSettle`; a refused settle is logged and the row is later settled `lost`, its charge
   `unknown`, keeping the whole hold.
4. **Settle** (`owner/src/egress.ts` `spendOutcome`, `settle`): a `succeeded` cost must be a
   finite number at least 0; it is stored as `costMicros(cost)` (`Math.ceil(dollars × 10^6)`,
   or 0 for a non-finite one), and the triggers move every counter by `cost − est_cost`.
5. **Limits** (Fact 15, the owner's own, off by default): `#reserve` reads `run.cost` and
   `spent(period, source)` (`SUM(cost)` over `model_spend`); `runs.ts` `#limits` refuses an
   API-key run once `spent` reaches a day or month cap.

### Is the settle bounded by the reservation?

No. A cost above `est_cost` is recorded in full: the receipt's `cost`, the run's `cost` and
both spend counters take the excess. That is the right half: the vendor bills what it bills,
so the counters must say what was spent, and the owner's caps then refuse what follows. The
receipt (`egressReceipts`) carries `est_cost` beside `cost`, so an overspend shows as one.

Nothing bounds it from above, and the store is where that breaks (measured in SQLite):

- **1e308 becomes Infinity.** It passes every check (finite, at least 0), and
  `costMicros(1e308)` is `Math.ceil(1e314)`, `Infinity`. SQLite keeps it as a REAL in the
  INTEGER column (`cost >= 0` holds), and the triggers make the run's cost and the day's and
  month's counters `Infinity`. Every capped reserve is then refused for the rest of the day
  and month (`cap − Infinity`), every capped API-key run too, and the export's JSON writes the
  counter as `null`. A counter ever moved by
  `Infinity − Infinity` is NULL, and its `NOT NULL` aborts the unit.
- **A bill past $9,007,199,254 (2^53 µ$)** is stored inexactly, and one past 9.2 × 10^18 µ$
  turns the column REAL.
- **`SUM` throws on overflow** (`integer overflow`), unlike `+`, which turns REAL: counters
  whose sum passes 2^63 µ$ make `spent` throw, and every capped reserve and run with it. With
  each settle bounded below 2^53 µ$, that still takes about a thousand such settles in one
  period, which up to 16,000 calls open at once, each reserved before the first overspend
  settled, reach.
- **A cost the tier multiplies past the largest double** (`forward.ts`, ×2.5) is `Infinity`,
  which `JSON.stringify` writes as `null`: the door reads a `succeeded` with no cost, the owner
  refuses it, and the call is settled lost much later instead of at once.
- `costMicros` answers 0 for a non-finite amount: a cost that should never get there would be
  charged nothing (Fact 8).

## Design

1. **One bound, by the reason the record bounds already have:** a call's settled cost is at
   most `METERED_COST_MAX`, $9 × 10^9 (`contract/src/limits.ts`), so its µ$ is an exact integer
   in a JavaScript number, as every reservation's is; it is eighteen times the dearest
   reservation, so no vendor's real bill comes near it. `meteredCost` (`contract/src/gateway.ts`)
   is the one check: a number, at least 0, at most the bound (NaN and Infinity fail it).
2. **Above the reservation, within the bound: recorded in full** (unchanged). A real overspend,
   which the owner sees on the receipt (`cost` over `est_cost`), on the run and in the counters,
   and which every cap then counts. Never clamped to the hold: that would hide it from the caps
   too.
3. **Past the bound: `unknown`.** No vendor bills that for one call, so it is no bill: the call
   is settled as any answer whose cost cannot be read, keeping its whole hold, its receipt
   `unknown`. The gateway builds every `succeeded` outcome through `succeeded`, which answers
   `unknown` for such a cost, after the tier's multiplier, so the line never carries one;
   `meteredUsage`'s own finite check, a partial copy, goes. The owner's `spendOutcome` refuses
   one (`invalid egress settle`), and `costMicros` throws on one instead of charging 0.
4. **The counters never throw:** `spent` reads `TOTAL(cost)`, which never overflows (`SUM`
   throws past 2^63); the triggers' `+` already turns REAL past it, never wrapping.
5. **Expand-then-contract:** no shape changes (`handed.json`), only the values a field takes.
   An owner refusing a bill past the bound beside a gateway before still settles it `unknown`
   (refused, then lost); a gateway answering `unknown` beside an owner before is a value that
   owner takes. Either order is safe, so it ships in one step.

## Problems found

- A settle's cost had no upper bound: 1e308 made the run and spend counters `Infinity`,
  refusing every capped call and run for the day and month and breaking the export. Fixed:
  `METERED_COST_MAX`, `meteredCost`, `succeeded`.
- The finite check was in two places with no bound (`meter.ts`, `egress.ts`
  `spendOutcome`), and missed the tier's multiplication after the first. Fixed: one check,
  applied where the outcome is made and where it is taken.
- `costMicros` charged 0 for a non-finite amount. Fixed: it throws.
- `spent` read `SUM`, which throws on overflow. Fixed: `TOTAL`.

## Left, with reasons

- An overspend raises no notice of its own: the receipt carries both numbers and every cap
  counts it; a notice for it would be a new feature, not this fix's.
- `outcomeOf` (the door's read of the line) tells success from failure and checks no cost:
  the owner's `spendOutcome` is the settle's one checker, and a third check would copy it.
- `model_reservations` (written by no code now) keeps its triggers: its SQL is a frozen
  migration's, and no settle reaches it.
