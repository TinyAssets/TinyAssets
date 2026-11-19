# Which reservation records an engine's model call?

ADR-034 decisions 7 and 13 put API-key calls through sandbox egress and
removed the in-object model caller. In the engine re-plan, the `usage.ts`
inventory row says to keep the reservation state machine and hold it to
the run at egress. Section 2.5 names egress as the enforcement point.
The checked G2 row and the owner-object requirements still describe the
earlier reserve, dispatch and settle path in the present tense.

## Follow a call

On main `f934871ad` and combined `151c2a83f`, the egress door opens and
reserves the call on the owner, forwards through the gateway and settles
on the owner. `owner/src/egress.ts` writes `egress_reservations`. Its SQL
triggers update the run and the source's `model_spend` day and month.
`owner-do.ts` supplies the shared price, spend reader and spend pruning
to egress. The launcher also reads spend.

`owner/src/usage.ts` still offers `reserve`, `dispatched`, `settle` and
`abandon` on a separate class backed by `model_reservations`. Its test
constructs the class directly with an independently supplied policy.
The owner door table exposes `usageReceipts` and the actual egress doors.
It provides no entry to this second call writer. An API-key call settled
through egress does not add a second direct-model receipt. Subscription
traffic has the vendor's own limits under ADR-034.

## What remains live

- Egress uses `priced`, `spent` and `spendPruned` from the same module.
  Both reservation tables can contribute to the same source counters.
- `usageReceipts` reads the historical table and resolves open rows
  left by another instance. `export.ts` and `restore.ts` admit both
  tables. Stored spend can outlive the rows that contributed to it.
- In synthetic histories loaded under all eighteen owner migrations,
  four historical rows contributed 42 to each source counter. Deleting
  one settled row retained its seven units. Reading the receipts
  recovered a reserved row as `not_sent` and a dispatched row as
  `unknown`, leaving 29 in each counter. A second read preserved the
  rows and totals. Main and combined agreed on Node 22 and 24.

## What each older requirement means at egress

| Owner-object requirement | Live engine path and historical obligation |
| --- | --- |
| Reserve against the owner's model policy | `egressOpen` admits the live lease uncharged. The gateway's request parser calls `egressReserve` for its model and tier. One unit reads run and source caps, chooses output and records the charge. The two-calls-at-once case in `owner/test/egress.test.ts` checks the run ceiling. |
| Estimate with pi-ai and settle at the gateway price | The owner estimates with the stored connection model, its window, dearest applicable input rate and tier. The gateway uses its catalog on the actual response. `owner/test/egress.test.ts` checks the estimate and `gateway/test/meter.test.ts` checks named versus served model pricing. Preserve both the long-context and changed-catalog scenarios when migrating direct-class tests. A partially reported usage record still needs a completeness check. |
| Caps read counters | The run holds its own requests, tokens and cost. The source's day and month are separate SQL counters updated by triggers. An ordinary egress charge and a direct-model historical charge contribute to the same source windows. Pruning a receipt must preserve each total. |
| Dispatch and settle once | `egressReserve` accepts an open, live, as-yet-uncharged call. Policy and lease changes end streams, the gateway observes their stop, and `egressSettle` accepts the first settlement. A known pre-send refusal, an interrupted stream, and a lost settlement answer have distinct handling in `doors/test/egress.test.ts`. The old direct dispatch state is specific to its row format. |
| Settle a dead instance's calls | An egress call has beats and a deadline. Loss before reserve charges nothing. Loss after reserve keeps the held estimate. Historical direct-model rows use their own on-read recovery, demonstrated above. A receipt read alone does not stand in for the egress beat/deadline tests. |

## Correct the plan before removing code

The original stage measurement came from G2's direct-call receipts
(tasks before #5507, `c33970800`). At stage a normal engine call uses
the run's egress receipt. Measure the actual call at that door and
observe its run and spend effects. Measuring rows on the old writer
alone would report zero on that path.

Map each retained promise from the five owner-object requirements
starting at “Every model call is reserved against the owner's model
policy” to the real egress path or to historical recovery. Preserve
the pricing, ceilings, lost-call and once-only settlement tests where
they apply. Report a gap if the actual caller fails any of them. Only
then retire the unused writer and the checks that assert it exists.
Preserve the deployed migration history, table access for export and
restore, and old receipt and counter semantics. The provider-side
completeness of a successful usage receipt is a separate question.
