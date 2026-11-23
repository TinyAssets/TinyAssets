# strength-out as built (2026-11-23)

`untangle/strength-out`, owed by `served-models.md` and `served-models-contract.md` (#5840):
the gateway's `strength` read, its entrypoint method, the `strength_read` shape (marked
`handed: false` by #5840) and their tests go, now that the stage serves an owner whose shapes
mark it.

## Research (Fact 14), before the removal

### What the stage serves

- Deploy run 45251793237 (main 19b79ef8f, #5840) took all six Workers to 19b79ef8f at 100%,
  each read back; the run then stopped at its doors canary (the owner's files: `file_write`
  answered 403, the canary's connection has no registered agent), after the rollout, so it
  serves 19b79ef8f all the same.
- Its rollout record keeps 8e01c3dd2 (#5838) as every Worker's version before.

### Who calls `strength`

- 19b79ef8f's owner and 8e01c3dd2's call only `listing` (`owner/src/connections.ts:450`); no
  owner source at either names `strength` or `strength_read` (the `strength` in
  `owner/src/schema.ts` and `owner/guards/frozen-bounds.test.ts` is migrations 32 and 33's
  history). Only owners before #5833 called it, and 1ae659350 (#5833) is an ancestor of
  8e01c3dd2.

### The hold

- `deploy/guards/handed.test.ts`, with this removal: passes held against `GUARD_BASE=19b79ef8f`
  (what serves) and the default base (`origin/main`, the same commit); refused against
  8e01c3dd2 and a5dc841df, whose shapes hand `strength_read` unmarked ("the shape
  strength_read is gone, which the owner's version before hands").

### Why no rollback pairs an owner that calls `strength` with this gateway

- The kit's rollback (`deploy.ts` `rollbackAsk`) takes one run's Workers back to the versions
  its record kept, owner before gateway, and refuses, changing nothing, when any Worker serves
  anything but that run's version or the one before (a deploy since).
- The run that deploys this removal will record 19b79ef8f as each Worker's version before, so
  its rollback reaches 19b79ef8f's owner (calls only `listing`) and its gateway (still has
  `strength`). Every older record, 45251793237's included, is refused once that run deploys.
- Before this removal deploys, a rollback of 45251793237 takes the stage back to 8e01c3dd2,
  whose shapes hand `strength_read`; the kit's plan (`servedHold`) then refuses this removal
  until 19b79ef8f or later serves again. Even so, 8e01c3dd2's owner never calls `strength`.

## Problems found (untangle step 2), with the fix above each

- **The gateway serves only the one listing read the owner calls (`listing`).**
  - `forward.ts` `strength`, `index.ts`'s `Gateway.strength` and `handed.json`'s
    `strength_read` served no owner the stage can reach: surplus.
  - `forward.ts` `listed` existed to share the read between `listing` and `strength`; with
    one caller it was a pass-through, folded into `listing`.
  - `forward.test.ts`'s `read` helper took a `reader` only for `strength`: a one-caller
    parameter, gone.
  - `index.ts`'s notes named `strength` among the owner's methods.
  - `forward.test.ts`'s test of the scored-only answer and `entry.test.ts`'s `strength` in the
    owner entrypoint's surface tested the removed read.
- Kept, the system needs them:
  - `handed: false` in `contract/src/handed.ts`, `deploy/handed.ts` and its guard: the step
    every shape takes on its way out, held by the guard's own fixture, not by `strength_read`.
  - `owner/src/schema.ts`'s `connection_strength` (migrations 32 and 33) and its frozen bound:
    shipped migrations, which are never edited.
- No `platform.json` `forward` entry: the removal writes no new shape, and a gateway taken back
  to 19b79ef8f still parses all this tree's owner hands.
