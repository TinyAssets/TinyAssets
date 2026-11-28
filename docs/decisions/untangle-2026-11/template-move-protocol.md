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
  `string|null`, frozen). Nothing a Worker of 5 sends, or reads back, changes.
- **What a Worker speaking `[5, 6]` with a protocol 5 runner does**: it adopts its own runner (5)
  where nothing lives, as today; it keeps and speaks to a runner of 5 or 6 a run lives on; it
  sends a runner of 6 only protocol 5's commands, which 6 takes alike. So the rollback of #5857's
  deploy lands on this Worker, which speaks a run living on #5857's runner 6.
- **The deploy hold** (`deploy/handed.ts` `runnerProblems`) reads a protocol the tree records
  from the tree's own frozen record. Its guard case "a serving sandbox speaking a protocol this
  owner answers not" spelled that protocol `6`, which this tree now records, so the case read the
  tree's record and no longer stopped: it now takes the protocol after the newest recorded.

## Design

This change records protocol 6 (`protocol.json`, its pin in `state-format.test.ts`) and has
the Worker speak 5 and 6 (`speaks.json`); the runner stays on 5 (`PROTOCOL`), and no carry
command is sent or taken. #5857 then moves the runner to 6 and adds the carry and the move; it
lands after this one has deployed.

## Problems found (untangle step 2), with the fix above each

- **A rollback across #5857 lands on a Worker that speaks its runner's protocol.**
  - #5857 carried protocol 6, its runner and the Worker's `SPEAKS` in one deploy, so a rollback
    met runner 6 with a Worker speaking 5 alone.
- **The hold's guard case names a protocol no tree records.**
  - `deploy/guards/deploy.test.ts` spelled the unrecorded protocol `6`.

## Left, with reasons

- **The carry's commands are recorded before any runner takes them**: that is the expand step.
  The record is frozen from here, so #5857's runner is held to it.

## As built

- `platform/sandbox/runner/protocol.json` 6, `speaks.json` `[5, 6]`,
  `runner/state-format.test.ts` its pin.
- `platform/deploy/guards/deploy.test.ts`: the rollback hold's case takes the protocol after the
  newest recorded.
