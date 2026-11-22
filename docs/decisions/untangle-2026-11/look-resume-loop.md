# look-resume-loop as built (2026-11-22)

`untangle/look-resume-loop`, found in #5777's review (`foreign-runner-reap.md`,
`sandbox-start-fault.md`, `runner-protocol-contract.md`). When a run's lease is in a sandbox
and the owner's release never arrives (the owner's Worker faulting, a lost message, an
owner gone), the look in `platform/sandbox/src/control.ts` could carry the sandbox into new
windows for ever, billed each time. Also asked: whether the look's back-off, one counter for
each owner, should be one for each sandbox.

## Research first (Fact 14), read 2026-11-22 on main 6352b1e13

1. **What carries a sandbox into a new window.** Three paths. A renewal of a running
   sandbox in its window when its runner's `state` says a job is live. The resume of a
   paused sandbox mid-renewal (`renewing`). The resume of a paused sandbox a lease runs in
   (`launched` rows, which only the owner's release, the idle reaper or a forget clears).
   Nothing else. So each window is carried on by two beliefs: the runner's `live`, and the
   Worker's own `launched`.
2. **What bounds a run.** Nothing on the platform's side, by design: "a run's lease's tokens
   live while its heartbeat renews it" (`owner/src/runs.ts`). The owner's limits (off by
   default) are spend caps on key runs, not time. A run has no runtime limit to give the
   sandbox Worker at grant time. Adding one would be a new owner limit that is off by
   default, so it would leave the default case unbounded. The start's lease carries
   `{lease_id, epoch}`. Its expiry moves with every heartbeat, which the sandbox Worker
   never sees.
3. **How the owner settles a run whose heartbeat stops.** The `runs` lane settles each run
   `unknown` whose own report is `LEASE_TTL_S` old (`beat_at + LEASE_TTL_S <= now`). It
   asks the sandbox to stop it (`stop`, a `cancel`, logged and not retried when it does
   not reach). It releases the lease once no live run holds it (a release, logged and not
   retried). An event post needs a live lease (`LIVE_LEASE`, `lease_expires > now`, renewed
   only by posts), else the run is ended (`run_ended`, 403/404). So past a lease's TTL with
   no post taken, the run is over at the owner, and no post of it is ever taken again.
4. **What the runner does when the owner cannot take its posts** (`runner/runner.ts`
   `Poster`, `runner/sync.ts` `SyncClient`). It retries for ever: the network, 503 and 429
   alike. Only 401, 403 and 404 stop it. Its job's end (`finish`) loops until taken. The
   sync agent retries the same way. So with the owner's object unreachable, a CLI on the
   vendor's own sign-in (which needs no TinyAssets door) runs on, and a job whose CLI
   ended still waits on its end post or its sync-out. Either way `state` says `live`, and
   the look renews the sandbox for ever. When the owner is reachable, its `stop` or its
   refusal of the next post ends the job. The gap is only the unreachable owner.
5. **What the look knows of a paused sandbox.** Nothing from its runner. Once resumed, a
   runner whose `state` fails at every look (a fault of its own: a state file it cannot
   read) is never seen idle. So its `launched` rows stay. At each E2B timeout pause, the
   look resumes it again, billing one window per window while the release is lost.
6. **Where the bound can live without asking the owner.** Only the job sees its heartbeat.
   The rule the owner applies, no post taken for a lease's TTL, is the contract's
   (`LEASE_TTL_S`, `contract/src/limits.ts`). Applied by the job to itself, it never ends a
   run the owner still holds. The runner's last taken post is no earlier than the owner's
   `beat_at`, and its own start is after the run's admission. The guest clock after a
   memory resume can only be behind real time, which errs late, never early. `state` is the
   one answer every protocol keeps alike (`state-format.test.ts`), so carrying a heartbeat
   time in it would be a protocol bump. The job ending itself needs no protocol change.
7. **The back-off.** `known.failures` backs off the whole look after any sandbox's look
   failed. A failed sandbox is left out of `next`, so it is looked at at that back-off, at
   most a lease's TTL (`RETRY_MAX_MS`), never at alarm speed. Every settled sandbox's own
   next time, and every row's edge still ahead, still arm the alarm. Each sandbox's own
   pending E2B act (a resume or pause E2B refused) already backs off by itself (`tries`,
   `retry_at`).
8. **Live.** No owner sandbox exists (`runner-protocol-contract.md`), so nothing holds a
   job today.

## The shape, checked against the intent

- **A job ends when its heartbeat lapses** (`runner/frames.ts` `Poster`, moved beside the
  frames, heartbeat and flush cadence it is made of). A post not taken for `LEASE_TTL_S`,
  counted from the job's start or its last post taken, refuses the poster as the object's
  refusal does (`heartbeat_lapsed`, logged in the job's log). That holds whether the object
  cannot be reached, answers busy, or the posts are held by a renewal never ended. The job
  stops its CLI as on any refusal. Its sync agent, which could otherwise wait on an
  unreachable object for ever, is stopped whether it runs at the lapse or starts after it,
  and the job's end returns unposted. `state` then says nothing
  lives. The look's existing idle reaper pauses the sandbox, and drops its `launched` rows.
  One home (the contract's TTL, the owner's own rule). No new limit (Fact 15: the owner
  settled the run already). No special case: the lapse is one more refusal.
- **A paused sandbox is resumed for its lease only on its runner's word** (`heard`,
  migration 6). That word is a `state` answer or a job's launch since the sandbox last came
  running (created or resumed). Without it, the look leaves the sandbox paused, by name
  (`sandbox_runner_unheard`). It drops the lease's runs and any renewal, so no look comes for
  it again. A start resumes it like any paused sandbox. The resumes that are right are kept:
  a renewal or a timeout pause after the runner answered. The one that loops is gone:
  resuming a runner never heard from, one window after another. Fact 8: the cause is
  logged at every failed look and named at the stop.
- **No lease deadline at grant.** The research found none to carry (item 2). The owner→sandbox
  start and release are unchanged, so no expand or contract and no deploy order. The runner
  ships inside the Worker by digest. A runner kept under a live job across this deploy keeps
  the old posting until its job ends (adoption needs nothing living). Nothing live holds one
  (item 8).
- **The per-owner back-off stays.** A per-sandbox look counter would change only when a
  second failing sandbox's first retry comes (480 ms rather than up to a lease's TTL). No
  bound depends on that: the failed sandbox is still looked at within a TTL, and its edge
  still arms the alarm. Per-sandbox retries for E2B's refusals exist already.

## As built

1. `sandbox/runner/frames.ts`: `Poster` and `FLUSH_MS` moved here from `runner.ts`. The
   poster's lapse (`#over`, and `lapse`, a promise settled at it) and one refusal path
   (`#refuse`).
2. `sandbox/runner/runner.ts`: `syncAgent` takes the job and races its agent against the
   job's `lapse`. So a sync running at the lapse, or begun after it, is stopped and thrown,
   one path for both.
3. `sandbox/src/schema.ts`: migration 6, `sandboxes.heard`, pinned
   (`migrations/SandboxControl.json`).
4. `sandbox/src/control.ts`: `#heard` and a launch set `heard`. A resume clears it.
   `#lookAt` leaves an unheard paused sandbox. The header says both rules.
5. Tests:
   - `runner/frames.test.ts` (fake clock): a lapse when the object cannot be reached,
     answers busy, or the posts are held; none while heartbeats are taken; the object's own
     refusal is no lapse.
   - `runner/runner.test.ts` (`large`, about 61 s): a run whose posts are never taken ends
     a TTL on, its held sync-out stopped, and `state` says nothing lives.
   - `test/sandbox.test.ts`: a paused leased sandbox is resumed on the launch's word, and on
     a look's `state`; it is left paused, by name, once its runner was not heard since its
     last resume.

## Left, with why

- **The job's lapse is logged in the sandbox's job log only.** The owner's record already says
  it (`unknown` once the heartbeat lapsed), and the Worker sees the sandbox go idle. A
  notice from the sandbox would be a new channel for what the owner records already.
