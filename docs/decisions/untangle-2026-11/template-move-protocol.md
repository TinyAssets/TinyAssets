# template-move-protocol as built (2026-11-28)

`untangle/template-move-protocol`: the expand step of runner protocol 6, split out of #5857
(`untangle/template-move`, its review's item 3). #5857 moves an owner's sandbox to a new template
pin through three commands only this Worker's own runner takes (`carry-out`, `carry-in`,
`carry-drop`): protocol 6. The protocol rule (`sandbox/runner/protocol.ts`,
`runner-protocol-contract.md`) ships a change in two deploys while a run can live across one, and
the stage owner has a sandbox, so it can.

## Research first (Fact 14), on main d662e63e1

- **What protocol 6 is**: protocol 5's record and three commands, the archive's path (`carry`,
  `/var/lib/ta/carry.tar`) and `carry-out`'s answer (`bytes`, `sha256`). Its start document's
  `model` is `string`, as every Worker since #5852 writes it (protocol 5's record keeps
  `string|null`, frozen). Nothing a Worker of 5 sends, or reads back, changes. It is #5857's
  record byte for byte (its pin `74bab57c…`), so #5857 merges onto it with no change to it.
- **What a Worker speaking `[5, 6]` with a protocol 5 runner does**: it adopts its own runner (5)
  where nothing lives, as today; it keeps and speaks to a runner of 5 or 6 a run lives on; it
  sends a runner of 6 only protocol 5's commands, which 6 takes alike. So the rollback of #5857's
  deploy lands on this Worker, which speaks a run living on #5857's runner 6.
- **The deploy hold** (`deploy/handed.ts` `runnerProblems`) reads a protocol this tree speaks
  from the tree's own frozen record. Its guard case "a serving sandbox speaking a protocol this
  owner answers not" spelled that protocol `6`, which this tree now speaks, so the case read the
  tree's record and no longer stopped.
- **Nothing held the rule's order** (the review's pre-existing 1): the hold checked the owner's
  answers against every spoken protocol, never that the tree's runner speaks a protocol the
  serving sandbox Worker speaks. `platform-deploy.yml` is dispatched by hand, so this change and
  #5857 merged before one deploy would ship runner 6 onto a Worker speaking 5, and pass. The
  runner's protocol was a constant in `protocol.ts`, so no serving commit's could be read.
- **A rollback of #5857 and its archive** (the review's pre-existing 3): a rollback lands on this
  Worker, which adopts its own runner (5) where nothing lives; a protocol 5 runner's `prepare`
  left `/var/lib/ta/carry.tar`, a copy of the sign-ins a move that went no further left. This
  tree's runner is still protocol 5, but its bytes are this tree's: its `prepare` can drop it.

## Design

1. This change records protocol 6 (`protocol.json`, its pin in `state-format.test.ts`) and has
   the Worker speak 5 and 6 (`speaks.json`); the runner stays on 5, and no carry command is sent
   or taken. #5857 then moves the runner to 6 and adds the carry and the move; it lands after this
   one has deployed.
2. **The runner's protocol is data** (`sandbox/runner/runner-speaks.json`), which `protocol.ts`
   `PROTOCOL` reads, and **the deploy hold holds the rule's two deploys**
   (`deploy/handed.ts` `ordered`): this tree's runner speaks a protocol the serving sandbox Worker
   speaks, and this tree's Worker speaks the serving runner's. A release that records none (no
   `runner-speaks.json`, every one before this) is not held to it, nor one where no sandbox
   serves. So #5857 cannot deploy before this has.
3. **A runner's `prepare` drops a carry left** (`CARRY_FILE`): a runner of 5 that a rollback
   adopts drops what a runner of 6 left.

## Problems found (untangle step 2), with the fix above each

- **A rollback across #5857 lands on a Worker that speaks its runner's protocol.**
  - #5857 carried protocol 6, its runner and the Worker's `SPEAKS` in one deploy, so a rollback
    met runner 6 with a Worker speaking 5 alone.
- **The rule's order is held by the deploy kit, not by care.**
  - Nothing checked a tree's runner protocol against the serving Worker's, and the runner's
    protocol was code no commit's data showed.
- **No archive outlives a rollback.**
  - A runner of 5 left a carry's archive at rest.
- **The hold's guard case names a protocol no tree speaks.**
  - `deploy/guards/deploy.test.ts` spelled the unrecorded protocol `6`.

## Left, with reasons

- **The carry's commands are recorded before any runner takes them**: that is the expand step.
  The record is frozen from here, so #5857's runner is held to it.

## As built

- `platform/sandbox/runner/protocol.json` 6, `speaks.json` `[5, 6]`, `runner-speaks.json` `5`
  (`protocol.ts` `PROTOCOL` reads it), `runner/state-format.test.ts` 6's pin.
- `platform/sandbox/runner/protocol.ts` `CARRY_FILE`; `runner.ts` `prepare` drops it;
  `runner.test.ts`'s prepare test holds that.
- `platform/deploy/handed.ts`: `Side.runner`, read by `sideAt`; `ordered` in `runnerProblems`.
  `ci/kit/layers.ts`: the deploy kit reads `runner-speaks.json`.
- Tests: `deploy/guards/handed.test.ts` (the two deploys held, either way; nothing serving or
  nothing recorded holds nothing; `sideAt` reads the runner's protocol),
  `deploy/guards/deploy.test.ts` (a run stops on a runner protocol the serving Worker does not
  speak; the rollback hold's case takes the protocol after the newest recorded, the serving
  Worker speaking this tree's too).

## Owed

- [ ] untangle/template-move (#5857): protocol 6's second deploy, the runner speaking 6 and taking
  the carry, merged only once this has deployed (the hold refuses it before).
- [ ] untangle/runner-protocol-6-contract: protocol 6's contract, the Worker speaking 6 alone,
  once no deployed runner speaks 5.
