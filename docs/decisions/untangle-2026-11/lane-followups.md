# lane-followups as built (2026-11-23)

`untangle/lane-followups` clears the three items #5804's review left (`start-faults.md`, its
Found and left, Owed and review receipt): `untangle/lane-pool-wait`, the code a run cancelled
while its start was out settles with, and the order of a stopped job's cancel and its lease's
release.

## Research (before the build)

1. **How a lane reads without waiting on the pool** (`gate.ts` `unitsNow`, `archive.ts`
   `MoveHold`, `backup.ts` `#shipOne`, `Launcher.run`). The object's other two lanes that hold
   the isolate's pool already take their units now: `unitsNow` hands the take a signal that
   aborts at the next turn of the event loop, so a call that does not fit at once leaves its
   queue `busy`, before it changes anything. The `runs` lane already holds on a transient
   refusal until its `Retry-After` (`Held`, #5804), and `busy` is one, so a read refused `busy`
   holds the lane with the row as it was. The other options:
   - `inTime` around `slotted` bounds the lane's wait but leaves the call in the pool's queue
     (`inTime` never cancels), so it still takes a waiter's place and its units when they free.
   - A lane-own slot is a second pool rule, for a read that already has a class: the agent
     file is a one-unit hold, which the reserve serves first.
   - A row back-off needs a column on the exported `runs` table (`start-transient.md`
     research 4).

   `Bodies.fetch` has one caller, the `runs` lane (the mail loop's `rereading` and
   `#mailPrompt`): it takes its units now outright. `readAgent` is shared: the `runs` doors call
   it (`runs.ts`), and a door's caller waits on the pool by the pool's own rule, up to
   `SLOT_WAIT_MS`. So the read is one body with two ways to hold the pool, as `launch` and
   `#laneLaunch` are.
2. **The code a cancelled run settles with** (`Runs.settle`, `events`, `cancel`, `#start`). A
   cancel marks the live run `cancel_requested` and takes its grant, then the run settles later
   by whatever comes next. `events` already reads the mark: a cancelled run settles
   `failed` `cancelled` whatever its runner's exit, error or vendor limit (the mark checked
   before every other code). `#start` did not: a late `sandbox_waiting` or refusal settled the
   run with that code. Nor did `endClient` (`revoked`) or `endLease` (`network_unconfirmed`)
   for a run already cancelled. None of these codes is read back to decide anything: the code
   is the owner's record of why the run ended, and the owner's cancel came first. The
   sign-in notice a `signin_required` answer leaves is kept, since it is about the sandbox, not
   the run. An `unknown` settle stays `unknown` (`lost`, a start with no readable answer): it
   says nothing ran to a known end, as `cancel` already documents for a cancelled run whose
   heartbeat lapses. So no failure code must win over the cancel.
3. **What a release does in the sandbox** (`sandbox/src/control.ts` `release`, `#reap`,
   `cancel`, `#launch`; `runner.ts` `cancelJob`, `state`). Every call goes onto the sandbox's
   serial chain in the order it arrives. A release drops its lease's `launched` rows, then
   pauses the sandbox only if its runner has no live job; one that has a live job dropped the
   ask (`releasing = 0`). A run's cancel finds its sandbox by its `launched` row, so a cancel
   that reaches the chain after the release finds nothing to stop. A sign-in counts as a live
   job (`state`'s `live`), and the runner's `cancel` and `login-cancel` send SIGTERM and answer
   at once: the job ends after its CLI's grace and its last post.
4. **Where the order went wrong** (`Launcher`, `signins.ts`). Each unit that gives a lease back
   queued its release in a microtask, and the stops of its jobs after it:
   - `#unconfirmed`: the release first, so it dropped the runs' `launched` rows, every run's
     cancel found nothing, and the pause was skipped while the jobs lived.
   - `signinCancel` and `signinEvents`' stop (a refused prompt, a passed deadline): the release
     first, while the sign-in still lived, so nothing was paused.
   - A late start: a release sent while the start was out could reach the chain first (a start
     enters it only after `made()` reads the control record), pause the sandbox, and the start
     then resumed it. Nothing released it again: the late stop sent only the cancel.

   In each case the sandbox ran idle until the look's idle reap (`LEASE_TTL_S` with no live
   job). Cancelling first and freeing the lease after (the first option) does not fix the late
   start: the start is out, so it can reach the chain after both. Only the start's own answer
   says nothing more will launch. And even in order, a release that lands while the stopped job
   is still ending dropped its ask, so the order alone pauses nothing.

## As built

1. **A lane takes the pool's units now** (`gate.ts` `slottedNow`, `slots.ts` `slotted`'s
   `signal`). `slottedNow` is `slotted` under `unitsNow`. `Bodies.fetch` holds its steps with
   it. `readAgent` (the doors') and `#laneAgent` (the lane's three loops) are one
   `#readAgent` with the hold it is handed (`PoolHold`): `slotted` for a door, `slottedNow`
   for the lane. A lane read the pool cannot serve now is refused `busy`, and the lane holds
   until its `Retry-After`, as #5804 made it.
2. **A cancelled run's failure is `cancelled`** (`Runs.settle`). The settle's unit writes
   `cancelled` for a `failed` settle of a run marked `cancel_requested`, whatever code it was
   handed. `events` loses its own check of the mark, so the rule is in one place.
3. **A stop reaches the sandbox before its lease's release** (`Launcher.stopAfter`,
   `#released`). Each job's stop asked in a unit (a run's cancel through `stop`, a sign-in's
   through `stopAfter`) is sent once the unit is over and kept in `#stopping` until it
   answers. Each release is sent once the unit is over and the stops then out have answered,
   once however often it is asked meanwhile. `signinCancel`, `signinEvents`' stop and
   `#unconfirmed` send their stops this way.
4. **A late start's stop releases its sandbox after it** (`Launcher.landed`). Whatever may
   have launched and no longer stands (`#start`; `signinStart`) is stopped, and if nothing holds
   its lease now, the lease is given back (if it still lives) and its sandbox released after
   the stop, however the lease went.
5. **A release keeps its pause asked while the lease's jobs end** (`#reap`). A release that
   finds a job still live leaves `releasing` set, so the look asks again, backing off
   (`#later`), and pauses once nothing lives. The owner gives a lease back only once no job of
   it stands, so a job live then is one of the lease ending. A job launched in the sandbox since
   ends the ask (`#launch` already clears `releasing`). That replaces "a live run means it is no
   longer released", which was a stand-in for "a run started since". The contract's `release`
   (`contract/src/sandbox.ts`) and the owner's list of sandbox calls say so (review).

## Found and left

- The release waits on every stop out when it is due, not only its own lease's. The stops are
  sandbox calls of one owner, each bounded by its RPC, so the wait is at most one more round
  trip. Keying them by lease would add a map for no case that differs.
- Nothing else found in scope.

## Owed

None.
