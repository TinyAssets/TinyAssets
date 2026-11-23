# handed-fallback as built (2026-11-23)

`process/handed-fallback` takes the item `handed-deploy.md` owed (#5809): once the stage serves
a commit holding `contract/handed.json`, `sideAt`'s read of a commit's `bindings.json` goes.

## Research first (Fact 14)

- **What the stage serves.** Platform deploy run 45119864429 (main, d315aaa61, #5809's commit)
  succeeded: plan, pair and apply green. Its plan, made again in the apply job, read every
  Worker serving ecaccf90c721 (the sandbox's and owner's tags with their migration suffixes),
  held through `bindings.json`; the apply job ending green is the kit having deployed d315aaa61
  to each at 100% and read each back (it fails on any other). No platform deploy has run since. d315aaa61 holds `platform/contract/handed.json`;
  ecaccf90c721 does not. So every Worker's version before the next run is a commit with
  `handed.json`, and a rollback takes each back there, never to ecaccf90c.
- **Who reads `bindings.json`.** `deploy/handed.ts` `sideAt` alone (its constant, its branch,
  the reshape giving each shape `from: "doors"`, `to: "owner"`, and the cutover names read by
  that file's path), and the two tests of that branch (`deploy/guards/handed.test.ts`, the
  plan's case in `deploy/guards/deploy.test.ts`). The guard (`handed.test.ts`) reads the base
  branch, which holds `handed.json`. No workflow, manifest or doc outside the decision logs
  names the file.

## The problems, with their fixes

- **One file of shapes at every commit read** (`sideAt` reads `handed.json` or throws).
  - `sideAt` read a commit's `bindings.json` when it held no `handed.json`, its shapes made the
    doors' to the owner: the expand step of #5809's rename, the one special case of the hold.
  - The cutover's shape names were read from whichever of the two files was found.
  - Tests: the guard's "is its handed.json, or before it its bindings.json's shapes" and the
    plan's "a serving commit with only bindings.json passes" tested that branch alone.

## Behaviour changed on purpose

- A serving commit (or a base) with no `handed.json` stops the plan (and fails the guard),
  naming the file, whatever else it holds: what it hands and takes is unknown.

## Left, each with its reason

- **The `handed-deploy.md` design bullet on `bindings.json`** stays: a decision log records
  what that PR built; its owed line is ticked here.

## Owed

None.
