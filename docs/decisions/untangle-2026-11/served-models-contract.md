# served-models-contract: a request shape its sender hands no more is marked so before it goes (2026-11-23)

`untangle/served-models-contract`, owed by `served-models.md` (#5833): the gateway's `strength`
read and its `strength_read` shape were to go once the stage served no owner that calls them.
The stage serves main a5dc841df on all six Workers (deploy run 45232686791), and the rollback
target its record keeps is 1ae659350 (#5833 itself).

## Research (Fact 14), before the design

### Which owners call `strength`

- a5dc841df's owner calls only `listing` (`owner/src/connections.ts:450`,
  `this.#gateway.listing(...)`); its contract's `GatewayService` has no `strength`.
- 1ae659350's owner is the same: #5833 moved the owner to `listing` in the change that added it.
  No owner source at either commit names `strength` or `strength_read` (the `strength` left in
  `owner/src/schema.ts` is migration 32's and 33's history).
- The owners before #5833 (3d4a31119, #5828, and the deploys after it until 1ae659350) call
  `strength`.

### Whether a rollback can pair an owner that calls `strength` with a gateway without it

- The kit's rollback (`deploy.ts` `rollbackAsk`) takes one run's Workers back to the versions
  that run's record kept, in the reverse of the deploy order (the owner before the gateway),
  and refuses, changing nothing, when any of them serves anything but that run's version or
  the one before (a deploy since). So the run that would deploy a gateway without `strength`
  rolls back to the owner and gateway it replaced, and an older run's record (back to 1ae659350,
  or before #5833) is refused once a later run deployed.
- By code, then, no rollback pairs a `strength`-calling owner with a gateway without it: the
  only owners such a rollback can reach are a5dc841df's and 1ae659350's, and neither calls it.

### What the deploy plan's hold says (`deploy/handed.ts`, `deploy/guards/handed.test.ts`)

- The hold reads what each release hands from data at its commit, never from its code. At
  a5dc841df, 1ae659350 and main, `contract/handed.json` lists `strength_read` as a shape the
  owner hands the gateway: #5833 kept the entry for the gateway's parser (`taken`, which the
  contract guard requires where a parser runs), and the data has no way to say the owner
  stopped handing it.
- So the removal, held against each of the three (`GUARD_BASE=a5dc841df`, `=1ae659350`, the
  base), is refused: "the shape strength_read is gone, which the owner's version before
  hands". CI's base guard would fail, and the kit's plan (`servedHold`) would stop every deploy
  while the stage serves an owner whose shapes list it. A field has a step for this
  (`dropped`), and a door has one (a row marked `dropped: true`); a whole shape has none.

## The design, chosen from the research

**A shape its sender hands no more is marked `handed: false` (its parser still takes it), and
goes once the stage serves no sender version whose shapes hand it: the hold lets a request
shape go when the sender's version before marks it so, as it lets a door go that the doors'
version before marks `dropped`.** This PR marks `strength_read`; the gateway's `strength`
stays until the stage serves an owner of this commit or later. The removal is then the
change this lane was given (the gateway's `strength`, its entrypoint method, `strength_read`,
and their tests), and its hold passes against such an owner (tried on this branch: the
removal held against this commit passes, and against a5dc841df and 1ae659350 is refused).

A rollback after the removal deploys takes the owner and gateway back to this commit's
versions, which mark the shape and still parse it. A rollback of this PR's own run, before the
removal deploys, takes the owner back to a5dc841df, whose shapes hand it again, and the plan
refuses the removal until this commit (or a later one) is served again.

## Problems found (untangle step 2), with the fix above each

- **A shape's step out (`handed: false`), held by the deploy plan, the contract guard and the
  shape's type.**
  - `contract/handed.json` said the owner hands `strength_read` after #5833 stopped the
    owner sending it: the sender's side of a shape had no data for "hands it no more", so its
    parser's entry had to keep claiming it was handed.
  - `deploy/handed.ts`'s "is gone" refusal named no step, unlike every other refusal in it.
  - `gateway/src/forward.ts` and `index.ts` said `strength` is for "the owner's version
    before" and goes once no owner calls it: the serving owner never calls it, and what lets it
    go is the shapes data the hold reads, not the calls.
- Left, with reasons:
  - The gateway's `strength`, its entrypoint method and `strength_read`: they go in the
    removal, once the stage serves this commit's owner (`served-models.md`'s open item).
  - A shape's `from` is not checked against its sender's code: the contract guard holds that
    each parser reads its shape by name, and senders build their shapes inline. A sender guard
    would need every sender to build each shape by name; the step makes the data able to say
    the truth, and a builder who stops a call marks it, as a door's is marked. (A door's mark
    is held by `tsc`: `OwnerDoors` leaves a dropped door out. A shape has no generated sender
    type, so its fields and its mark are equally unchecked; for `strength_read`, `tsc` holds
    the owner anyway, since its `GatewayService` has no `strength`. Review, #5840.)
  - An answer shape gone (its sender deploys first) is not held, as before; `handed: false`
    acts on requests, the only shapes the hold refuses as gone.
