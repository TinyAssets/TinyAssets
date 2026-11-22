# start-transient as built (2026-11-21)

Owed by `owner-shape.md` (found by the review of #5733): a transient refusal of a run's start
(`object_overloaded`, or any code whose contract entry is `transient`) settled the run `failed`,
where `refusal.ts`'s header says a transient refusal is tried again. Main before #5733 settled it
`unknown` `start_lost`, also never tried again. The same file owed `untangle/signin-start-unknown`:
a sign-in's start with no answer ended the sign-in but did not stop a CLI that may have launched.

## Research (before the build)

1. **Which refusals a start can meet, and when** (`sandbox/src/control.ts`, `start`, `signin`,
   `#launch`). The sandbox Worker throws two refusals, `sandbox_runner_mismatch` and
   `sandbox_runner_protocol`. Both are terminal (`BAD_GATEWAY`), and both come before the
   runner's `launch` (or `login-launch`) command runs, or from the loader refusing that command,
   so the runner never ran. In `#launch` that command is the last outside call: after it come
   only the object's own SQL and its alarm (`#arm`). The runtime's overload of an object
   (`.overloaded`, which `refusalOf` reads as `object_overloaded`) is thrown before the call
   runs, whether it is the owner's call to `SandboxControl` or the control's call to `SLOTS`
   inside `#ensure` (before the launch, and after the sandbox's row is written, so a second
   send finds that sandbox running and creates none). So a transient refusal of a start means
   nothing started.
2. **Whether a second send can double-start** (`sandbox/runner/runner.ts` `launchJob`). It is
   not idempotent: a second `launch` of a living job's id spawns a second runner. So a resend is
   safe only because of (1), never because of the runner. A lost start (a plain throw) or an
   answer out of shape may have launched, so neither is ever sent again.
3. **A run's life** (`runs.ts` header; ADR-034: a run is never replayed). A run is admitted
   `queued` in one unit with its lease (opened or joined), its grant (the SHA-256 of its
   secret) and, for a lease's opener, its tokens and network. Only the launcher's memory holds
   the secret, the tokens and the sealed route; the rows keep their hashes. So the same start can
   be sent again only from that memory, inside the same `launch`. A later pass could not send it.
   It could only admit the run whole again (a new secret and lease, the source chosen again),
   which is what `sandbox_waiting` does. Sending again the start of a run that never started is
   not a replay: nothing ran.
4. **How the lane waits** (`launch.ts`, `runs.ts` `waitForSandbox`, `nextWaiting`,
   `schedule.ts`). `sandbox_waiting` puts the run back to `waiting` with `sandbox_wait = 1`, and
   the run is admitted whole again when the platform's line rings (an event, not a time). A
   transient refusal has only a time, its `Retry-After`. Waiting on the lane would need that
   time and a bound kept on the run's row, so that `nextWaiting` skips the run until then, `next`
   wakes the lane then, and the pass does not loop. `schedule.ts` puts such a back-off in the
   row. `runs` is an exported table (`export.ts`), and a column on it needs a `FORWARD` form
   (`schema.ts`). No such form exists yet, and building one is its own PR.
5. **The bound.** A run has no deadline (`runs.ts` header: its only time bound is the
   sandbox's). The recorded ceiling on one outside call a lane's row makes is `LANE_CALL_MS`
   (`schedule.ts`: what is left of the hung timeout once a pass stops taking rows). A start is
   that call when the lane starts a waiting, mailed or scheduled run. A wait within it keeps the
   run's lease alive (`LEASE_TTL_S` is 60 s, and the run's admission beat is its own) and keeps the
   pass inside its hung timeout.
6. **What can change while the start waits.** A cancel (`Runs.cancel`), a revocation
   (`endClient`, `endAll`), a settle or a lost runner each delete the run's grant. A send after
   one of them would launch a run whose every call is refused. So the start is sent again only
   while `Runs.grant(run)` stands. A sign-in's row can be lost (`Signins.lost`) or ended in the
   meantime; it is sent again only while its row stands (`Signins.owned`).
7. **The owner's limits** (Fact 15). The waiting run keeps the tier slot it was admitted to, and
   it spends nothing (no model call). The bound is a recorded technical ceiling, with its
   reason, and the diff adds no other limit.
8. **The sign-in's lost start** (`signins.ts` `signinStart`). It is the same `StartOutcome` read
   on the same path. A `started: null` answer ended the row and freed the lease. The sandbox
   Worker's `signinCancel` stops a launched sign-in CLI and answers `cancelled: false` for none,
   as the run's `cancel` does.

## As built

1. **A transient refusal is sent again, bounded** (`owner/src/sandbox.ts` `startOutcome(job,
   start, stands)`). On a transient refusal, `startOutcome` waits the refusal's own
   `retryAfter`. If the job still `stands` after the wait, it sends the same start again. It
   does so only while that wait ends within `LANE_CALL_MS` of the first send. Past the bound, or
   once the job no longer stands, the refusal stands by its own code. A terminal refusal, a lost
   start and an answer out of shape are read as before and never sent again.
2. **The start is built once** (`launch.ts`, `signins.ts`). Each call's request is one value,
   so a second send is the same start. Before, the closure sealed a new route on each call.
3. **What stands:** a run while it holds its grant (`Runs.grant`), and a sign-in while its row
   is live (`Signins.owned`).
4. **A sign-in start with no answer is stopped** (`signinStart`). After its row is ended and its
   lease freed, `SandboxService.signinCancel` is sent, as a run's lost start is cancelled.
5. **Not on the lane's next pass.** The coordinator's brief asked for that shape. Research (3)
   and (4) show that a later pass cannot send the same start, and that a re-admission needs a
   new column on an exported table, with no form to bring old generations forward. Sending the
   start again within the call's own bound gives the contract's retry with no new state.

## Found and left

- `launch.ts` `run`: a transient refusal before a start (an agent read, a mail's body) still
  fails the whole pass ("the run waits on"). Lifecycle reads that as a bug, so the lane backs
  off `LANE_RETRY_S`, where `schedule.ts` says a pass keeps an outside call's failure in its
  row. The mail and schedule loops do the same. This is separate from the start, so it is owed.
- `signinCancel` (the owner's door) forwards to the sandbox and leaves the row. A sign-in the
  owner stops while its start waits (or while its first send is out) is still started. The run's
  cancel deletes its grant, so the run's resend stops; the sign-in's equivalent is owed.
- `refusal.ts`'s header says Cloudflare's page advises against retrying an overload, while the
  contract marks `object_overloaded` transient with a one-second `Retry-After`. The resend
  waits that `Retry-After`, never at once, so this PR keeps to the contract and leaves its entry.

## Review (2026-11-21)

- **A refusal after the create** (research 1 was wrong here). In `#ensure`'s create path, the
  line's `took` ran after `e2b.create` and the row's write, but before `#install` and
  `#prepare`. An overload of that call crossed as `object_overloaded`. The resend then found
  the sandbox running, with its row naming this Worker's runner, so it ran neither the
  adoption nor `prepare`. The run launched with no pinned resolver and `/proc` not hidden.
  **Fixed:** `#took` logs a failure and goes on, as `#freed` does. The line's own back-off
  rings the owner, which leaves the line once none of its runs wait. A sandbox test holds it.
- **A send's own time is unbounded.** The bound covers the waits and every send but the
  last. A start is not wrapped in `inTime` as the lane's other outside calls are, so a pass
  that starts a run can outlast its hung timeout, as it could before this PR. The comment now
  says so.
- Checked and sound:
  - An awaited timer in a request handler holds no input gate, and `startOutcome` runs
    outside the write gate's units.
  - The control's `#serial` chain is free between sends.
  - `Retry-After` is checked against the bound before the wait.
  - A cancel deletes the grant, so `stands` stops the resend.
  - Keeping the secret only in memory is right: the rows keep only its hash (ADR-034).

## Owed

- [x] `launch.ts` `run`: a transient refusal met by a waiting, mailed or scheduled run's
  admission fails the whole pass (backed off `LANE_RETRY_S`), not its row. Research first
  whether each loop backs its row off instead (`untangle/lane-transient`).
- [x] `signins.ts` `signinCancel`: a stop asked while the sign-in's start is out, or waits to be
  sent again, does not stop it. Research first whether the stop ends the row
  (`untangle/signin-cancel-start`).
- [x] `sandbox/src/control.ts` `#ensure`, `#resume`: a fault (not a refusal) after a create or
  a resume, and before `prepare` ends, leaves a running sandbox. The next start finds it
  running and launches in it unprepared. Research first whether a row marks its sandbox
  prepared (`untangle/sandbox-unprepared-row`).
- [x] `owner/src/launch.ts` `launch`: a start's call is bounded by nothing, where every other
  outside call a lane's row makes is bounded by `LANE_CALL_MS` (`inTime`). Research first how a
  late start stays fenced (`untangle/lane-start-bound`).
