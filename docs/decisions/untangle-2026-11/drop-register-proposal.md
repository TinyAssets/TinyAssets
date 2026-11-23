# drop-register-proposal as built (2026-11-23)

`untangle/drop-register-proposal`, the contract step #5821 (`register-apply.md`) owed: the
owner's one-card read `registerProposal`, kept as a dropped door (`doors.json` `dropped: true`)
for the doors' version before #5821, is taken out.

## Research (Fact 14)

- **How a door goes** (`deploy/handed.ts`, `binding-compat.md` "The design"): a door the doors
  call no more is first marked `dropped` (the owner takes it, `OwnerDoors` lacks it, so `tsc`
  refuses a doors call of it), and taken out once the stage serves no doors version that calls
  it. `handedProblems` lets a door go when the version before marks it dropped. This is the
  first dropped door taken out: `git log -S'"dropped": true' -- platform/contract/doors.json`
  names #5821 alone, which marked it.
- **What the serving versions hand.** Deploy run 45191373564 served main 9bb0b1b32 at 100% on
  all six Workers (codemode, files, gateway, sandbox, owner, doors, each read back); its
  rollout record's versions before are b65adb647 (#5821) on all six. Both commits' door tables
  mark `registerProposal` dropped, and no doors source calls it (`OwnerDoors` has no such
  method; the app's `registrations` route is `registerProposals`). So no version a deploy or a
  rollback reaches calls it.
- **The guard with it gone.** `handedProblems` of this tree against `sideAt(9bb0b1b32)` and
  `sideAt(b65adb647)` is `[]`; against `sideAt(b65adb647^)` (the doors before #5821, which
  called it) it is "the door registerProposal is gone, which the doors' version before calls".
  `deploy/guards/handed.test.ts` (against the base, main) passes, and the kit's plan holds the
  same against the serving versions at the next deploy.
- **Where it remained:** the contract row (`contract/doors.json`), the owner's row
  (`owner/doors.json`), its handler (`owner/src/registrations.ts`), and what `npm run generate`
  writes from them (`contract/src/requests.ts` `DoorRequests`, `contract/src/owner-doors.ts`
  `Dropped`, `owner/src/door-map.ts` `DoorRows` and `DoorMethods`). No test, spec or recording
  names it. Its `DoorTypes` entry went in #5821 already.

## Decided

1. Both rows and the handler go; the generated files are regenerated, so `Dropped` is `never`
   again.
2. The dropped-door rule stays: it is how every door leaves across the deploy order, and its
   guard tests (`contract/guards/doors.test.ts`, `deploy/guards/handed.test.ts`) hold it with
   rows of their own, not this one.
3. `register-apply.md`'s owed line is ticked. Its research and problem lines, which name the
   door as it was, are history and stay.

## Found and left

- The same deploy run's platform canary failed after all six Workers reached 100%: its key's
  `file_write` and `file_read` answered 403 (no registered agent). That is the app-minted key's
  grant (`stage-findings-1.md` section 3), already owed as `app-key-grant.md`'s live line
  (a key minted on the stage with a name alone passes the canary's keyed checks) and
  `untangle/scoped-key-agent`; nothing here touches it.
