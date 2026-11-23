# handed-fallback as built (2026-11-23)

`process/handed-fallback` takes the item `handed-deploy.md` owed (#5809): once no plan can meet
a commit without `contract/handed.json`, `sideAt`'s read of a commit's `bindings.json` goes.
The review (REQUEST_CHANGES at 258d02454) found that serving such a commit was not enough: a
rollback could still go back to one. The coordinator chose the general rule: the kit refuses a
rollback to a version it could not deploy forward from.

## Research first (Fact 14)

- **What the stage serves.** Platform deploy run 45119864429 (main, d315aaa61, #5809's commit)
  succeeded: plan, pair and apply green. Its plan, made again in the apply job, read every
  Worker serving ecaccf90c721 (the sandbox's and owner's tags with their migration suffixes),
  held through `bindings.json`. The apply job ending green means the kit deployed d315aaa61 to
  each Worker at 100% and read each back; it fails on anything else. No platform deploy has
  run since. d315aaa61 holds `platform/contract/handed.json`; ecaccf90c721 does not.
- **What a rollback can reach (the review's finding).** The run kept rollout record
  `deploy:rollout:45119864429`, whose version before is ecaccf90c721 for all six Workers ("a
  rollback takes it back there"). Rollout records are never deleted. Every Worker serves the
  run's commit, so that record passes rollback's drift check today. Mode `rollback`
  (`rollbackAsk`) never ran `servedHold`, so the rollback would succeed, and every later
  `plan`, `dark` or `routes` would then stop at "ecaccf90c721 holds no
  platform/contract/handed.json". Nothing could deploy forward until this PR was reverted. The
  first version of this file said a rollback never reaches ecaccf90c. That was false.
- **(a) How rollback picks its targets, and where the check goes.** `rollbackAsk` reads the
  record `--record` names. It walks the record's Workers in the reverse of the deploy order and
  holds each to Cloudflare, stopping with nothing changed on any difference:
  - every version it serves is its version before or one tagged with the run's commit (else
    drifted);
  - the record's tags and counts are Cloudflare's;
  - the version before is one the deployments listing shows serving alone just before a
    deployment of the commit (`servedBefore`).

  It stops taking Workers back at the first one the run made forward-only (`forwardOnly`). A
  Worker the run left at 0% is taken back anyway. The Workers it takes back (`back`), each to
  `before.version`, are the only versions the rollback changes. Every other Worker keeps what
  it serves, which a plan already reads today. After `back` is complete and the plan lines are
  said comes the last stop (`nothing can be rolled back`), then `if (!run.apply) return`, then
  the first change (`Cloudflare.deploy`). The only step before it, `zone`, only reads. So the
  check goes between that stop and the apply gate. There it stops the plan job (`--public`)
  and the apply job, which runs the same steps again with `--yes` before any change. The
  jobs check out full history without blobs (`fetch-depth: 0`, `filter: blob:none`) in every
  mode, so `fileAt` reads the target commits in a rollback job as in a deploy.
- **(b) Can a chain of rollbacks reach ecaccf90c or older after one more deploy?** Yes, before
  this change. Take a run R2 of a commit C2 over d315aaa61: its record's version before is d315
  for each Worker. Rolling back R2 leaves the Workers serving d315's versions. That is
  45119864429's "one tagged with the run's commit", so its drift check passes again, and
  `servedBefore` finds ecaccf90c alone at 100% followed by d315. Rolling back 45119864429 then
  reaches ecaccf90c. From there, 45110158056's record (ecaccf90c's run) is usable in the same
  way, and so on back. Each step needs only that nothing forward-only stops it. With this
  change, R2's rollback (to d315, which holds `handed.json`) passes. 45119864429's is refused
  before any change, and so is every older record whose target lacks the file. The handback
  record `deploy:routes:45059834265` moves routes, not versions, so it is not affected.
- **Who reads `bindings.json`.** Only `deploy/handed.ts` `sideAt`: its constant, its branch,
  the reshape giving each shape `from: "doors"` and `to: "owner"`, and the cutover names read
  by that file's path. Two tests covered that branch: `deploy/guards/handed.test.ts`, and the
  plan's case in `deploy/guards/deploy.test.ts`. The guard (`handed.test.ts`) reads the base
  branch, which holds `handed.json`. No workflow, manifest or doc outside the decision logs
  names the file.
- **What the check is, and what it is not.** It reads each target with the plan's own
  readers: `codeOf` for the version's code, then `sideAt` for its commit's side. A target
  either cannot read is one where every later plan would stop and no fix could go forward;
  only a revert or a hand deploy would get past it. The check does not run `handedProblems`
  against the tree. A later plan's refusal of a target's shapes can be fixed forward by
  changing the tree, and a rollback is the emergency path.

## The design

- **One reader, one stop, no fallback.** `sideAt` reads `handed.json` or throws. `servedHold`
  (plan) and `rollbackAsk` (rollback) both read a version through `codeOf` then `sideAt`. In a
  rollback, a target either cannot read stops the run before any change. The stop names the
  Worker, the version and its tag, and the reason. It names the ways out: roll forward, or
  close the gates. It also names the by-hand path (`wrangler versions deploy <version>@100%`,
  `checklist.md`), after which the kit plans nothing until that Worker serves a version this
  kit tagged of a commit holding `handed.json`. `UNKNOWN_CODE` is the one statement of an
  untagged version `codeOf` cannot trace, used by both stops.

## The problems, with their fixes

- **One file of shapes at every commit read** (`sideAt` reads `handed.json` or throws).
  - `sideAt` read a commit's `bindings.json` when it held no `handed.json`, making its shapes
    the doors' to the owner. That was the expand step of #5809's rename, and the one special
    case of the hold.
  - The cutover's shape names were read from whichever of the two files was found.
  - Tests: the guard's "is its handed.json, or before it its bindings.json's shapes" and the
    plan's "a serving commit with only bindings.json passes" tested that branch alone.
- **A rollback could leave the kit unable to plan** (`rollbackAsk` reads each target as a plan
  would).
  - Rollback never ran the plan's readers, so it could take a Worker back to a version every
    later plan stops at. ecaccf90c is one such version today.
- **A copy of the untagged-version wording**: both stops read `UNKNOWN_CODE`.

## Behaviour changed on purpose

- A serving commit (or a base) with no `handed.json` stops the plan (and fails the guard),
  naming the file, whatever else it holds: what it hands and takes is unknown.
- A rollback stops before any change, in its plan job and its apply job, when a version it
  would take a Worker back to has unknown code or a commit without `handed.json` (or one the
  checkout lacks). Today that refuses `deploy:rollout:45119864429` and every older rollout
  record.

## Left, each with its reason

- **A rollback by hand** (dashboard or wrangler) is not the kit's to stop. `checklist.md`'s
  rules for it now say: never to a commit without `contract/handed.json`.
- **The `handed-deploy.md` design bullet on `bindings.json`** stays: a decision log records
  what that PR built. Its owed line is ticked, with its condition corrected.

## Owed

None.
