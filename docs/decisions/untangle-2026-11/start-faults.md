# start-faults as built (2026-11-22)

This PR is owed four items by `start-transient.md`: `untangle/lane-transient`,
`untangle/signin-cancel-start`, `untangle/sandbox-unprepared-row` and
`untangle/lane-start-bound`. All four are the same thing: a job's start, from the moment it is
sent until its answer is recorded. During that time the start is out. It is bounded where a lane
waits on it, a stop can be asked, whatever launches late is fenced, and the sandbox it lands in
must be prepared.

## Research (before the build)

1. **What a lane row can be refused for the moment** (`launch.ts` `run`, `readAgent`,
   `#mailPrompt`, `#deliver`, `Runs.admit`). These are the transient codes that reach the three
   loops:
   - `busy`: the isolate's pool (`slotted` in `#whole`), or the files Worker's own pool crossing
     back (`getChunks`, tested).
   - `object_overloaded`: a binding's object, read by `refusalOf`.
   - `storage_busy`, `unavailable`: a placed body's read.

   None of them is a fact about the row it met. Each is a fact about something every row of the
   pass shares: the isolate, the owner's storage, a binding. The next row would meet the same
   refusal. The mail loop's body read (`bodies.rereading`) sits outside any row's `try`, so
   before this PR it failed the pass as well.
2. **What a row back-off would need.** A waiting run would need a retry time on its `runs` row
   (an exported table, and a column needs a `FORWARD` form that does not exist yet;
   `start-transient.md` research 4). A mail would need one on its recipient row. A schedule could
   move its `next_at`, but that changes the schedule the owner sees. `schedule.ts` already has
   the lane's shape for "a reason outside the rows": `Held`, which the `wakes` lane (kill
   switch) and the archive lane (pool busy) answer. The contract says a transient refusal means
   "the same request succeeds after its `Retry-After`", which is exactly what a hold says.
3. **How a run's start can be stopped** (`Runs.cancel`, `endClient`, `endLease`, `lost`). A
   cancel of a `queued` run deletes its grant and leaves the row live (`cancel_requested`), so
   that its runner's end settles it. The sandbox's `cancel` goes onto `SandboxControl`'s
   `#serial` chain. A start enters that chain only after `made()` has read the control record,
   so a cancel sent while the start is out can reach the chain before the start's launch. It
   then finds no `launched` row, answers `cancelled: false`, and the launch that follows runs.
   On the owner's side, `started` then marked the run `running`. A signed-in run calls its
   vendor directly, so it could run to its end with only its MCP and egress calls refused.
   Likewise, a cancelled run whose start answered `sandbox_waiting` was put back to `waiting`
   (`waitForSandbox` reads only `state = 'queued'`). When the line rang, it was admitted whole
   again with a new grant, and it ran.
4. **How a sign-in's start can be stopped** (`signins.ts`). `signinCancel` forwarded to the
   sandbox and left the row. `startOutcome`'s `stands` is `Signins.owned`, so a start waiting to
   be sent again was sent again. A start whose first send was out raced the cancel on the chain,
   as in (3). `#unconfirmed` (`launch.ts`) already ends a sign-in on the trusted side: row
   deleted, relay settled, lease freed, then the sandbox's cancel. A runner whose post is
   refused (403 or 404) stops its job (`runner/frames.ts` `Poster`), so the ended row is itself
   a stop that needs neither the sandbox Worker nor E2B.
5. **The late start** (`launch.ts`, `clock.ts` `inTime`, `schedule.ts`, `Runs.lost`, `next`). An
   admitted run is `queued`, with `beat_at` set to its admission. No pass starts a `queued` run
   again: `nextWaiting` takes only `waiting` rows, a mail is `taken` in the admission unit, and
   a schedule is `claim`ed in it. The lane's `next` looks again at `beat_at + LEASE_TTL_S`, and
   `Runs.lost` settles a live run unheard that long as `unknown` and stops it. So the row is
   already fenced. A pass can stop waiting at `LANE_CALL_MS` and leave the start's own promise to
   record its answer. `inTime` never cancels it. A start that never answers is settled by `lost`
   one lease TTL after its admission. One that answers after `lost` has settled it is fenced by
   (3)'s rule. Doors are not lanes: a door's caller bounds its own wait (`settledWithin`), so the
   bound is the lane's alone.
6. **The unprepared sandbox** (`sandbox/src/control.ts`). Three paths mark a sandbox running
   before its `prepare` ends:
   - the create writes its row, then `#install` and `#prepare`;
   - `#resume` writes `paused = 0`, then the network, `#speaking` and `#prepare`;
   - an adoption commits the runner to its row, then runs `#prepare`.

   A fault in between leaves the row running on this Worker's digest. The next start's running
   path then ran `prepare` only when it had just adopted, so it launched with no pinned resolver
   and `/proc` not hidden. Running `prepare` on every start is not an option: it walks `/` and
   took about 7 s once a boot (`PREPARE_MS`). Not writing the row until `prepare` ends would
   leave a running sandbox no look sees. So the row has to say whether the sandbox is prepared.
7. **The owner's limits** (Fact 15). The lane's wait uses `LANE_CALL_MS`, a recorded ceiling.
   The audit lists lane passes and their timings among the internal ones, since they refuse
   nothing a user does: the start goes on either way. The hold is the refusal's own
   `Retry-After`. The diff adds no limit.

## As built

1. **A refusal for the moment holds the `runs` lane** (`Launcher.run`). The pass body is
   `#pass`. `run` reads whatever it throws with `refusalOf`. A transient refusal answers
   `Held` until `clock() + retryAfter`, and the row stays as it was: the run waits on, the mail
   is delivered again, the schedule stays due and unclaimed. Anything else still throws as a
   bug. The per-row catches rethrow a transient refusal, so a refusal met anywhere in the pass
   (a row's read or admission, the mail loop's body read) is held the same way. There is no row
   back-off and no new column. The per-row catches now read with `refusalOf` too, where they
   tested `instanceof Refusal`. A terminal refusal that crossed RPC (the files plane's, say) is
   now kept on its row. Before, it failed the pass, and the same row failed every pass after it
   at `LANE_RETRY_S`.
2. **A lane's wait on a start is bounded** (`#laneLaunch`). `launch` is split into `#admit`
   (the unit, and the start built once) and `#start` (the send and its record). A door's
   `launch` awaits both. The lane's three loops call `#laneLaunch`, which awaits the admission
   and then waits on `#start` with `inTime(..., LANE_CALL_MS)`. Late, the pass goes on, the row
   stays `queued` (5), and a fault of the start after that is logged, since no pass is left to
   fail. `launch` loses its two lane-only parameters.
3. **A start's answer is recorded against the run as it then stands** (`#start`). A run stands
   while it holds its grant. Only a run that stands gets a second chance: its start sent again
   (`startOutcome`'s `stands`, as before) or its wait for a sandbox (`SANDBOX_WAITING &&
   standing`). A run that no longer stands is settled by its start's own code (a cancelled one
   `cancelled` since `lane-followups.md`). Whatever may have launched and no longer stands is
   stopped: a start with no readable answer (as before), and now one that answered `started`
   after its run stopped standing. That run is marked running, so its runner's end settles it
   `failed` `cancelled`, and it is cancelled again in the sandbox, where the launch is now on
   the chain.
4. **A sign-in's stop ends its row at once** (`signinCancel`). In one unit the row is ended and
   the lease is freed once nothing holds it. Then the relay is settled and the sandbox's cancel
   is sent. The door still answers the sandbox's `cancelled`. `signinStart` reads `owned` in the
   unit that records its answer. A sign-in that started but no longer stands is ended there,
   stopped in the sandbox, and refused as `NO_SIGNIN` (now exported), the answer every door gives
   for a sign-in that is gone. No new code goes on the wire.
5. **A row marks its sandbox prepared** (`SandboxControl`, migration 7, pinned with
   `node ci/kit/locks.ts pin sandbox HEAD`). `prepared` is 0 when a sandbox is created, resumed
   (`#resume`) or adopted (`#adopt`). It becomes 1 only once `#prepare` has ended. `#ensure`'s
   running path prepares whenever the row says 0. That one rule replaces the `adopted` special
   case, and `#speaking` now returns only its box. The default for kept rows is 0, so each
   running sandbox is prepared once more after the deploy: unknown is re-checked, not assumed.

## Found and left

- Two of the lane's reads wait on the isolate's pool (`slotted`, up to `SLOT_WAIT_MS`), where
  `gate.ts` `unitsNow` says a lane never waits on the pool because the object's other lanes
  would wait with it: `readAgent`'s `#whole`, for an agent file past an inline one, and
  `Bodies.fetch`, for a placed mail body (the mail loop's `bodies.rereading` and
  `#mailPrompt`). Each wait is bounded and ends `busy`, which (1) now holds. Both are shared
  with the doors and are not a start, so they are owed (found in review).

## Owed

- [x] `launch.ts` `#whole` and `bodies.ts` `fetch`: a lane's read of an agent file or a placed
  mail body waits on the pool, where a lane takes units only now (`unitsNow`). Research first
  whether the lane's reads take them now and hold on `busy` (`untangle/lane-pool-wait`).

### An acknowledged create followed by a failed status read

The provider can return the new sandbox ID and then fail the first read of its status. On main `d662e63e`, the row was written after that read. Two local Worker tests evicted the control object and retried the original start. With one slot the retry waited on its own sandbox. With two slots it created another and left the first one running.

- [x] Keep the returned ID, its unprepared state and its start meter event together before awaiting another operation. Until a status read supplies the provider's actual deadline, the ordinary look is due immediately. The existing runner installation and preparation path can finish the start after eviction. Both regressions and the existing unprepared and queue-leave controls passed. The initial test counted a missing runner's first prepare request as a completed preparation; the final assertion checks preparation and launch after installation.
- [ ] Recheck the shared path with #5857. A lost create response supplies no ID. Template-copy promotion and retirement remain separate from remembering an acknowledged create.

On `12e05fc1`, all 165 sandbox Workerd tests and the full Node 22 platform typecheck passed. Rehearsed with #5857 at `aa40cc0a`, the move-retention tests at `c9ce0d59` and the list guard at `72bb0ecb`. The PR's template-aware create method must feed the immediate ID write. Full types and 173 Workerd tests passed together. The four previously recorded move cases still failed: two carry-out failures and two lost-delete-reply recovery checks. Both new eviction cases passed in both trees. All provider calls used the test fake.

Two further Worker cases cover a previously launched sandbox that disappeared and a caller that never retries. Main waits on the replacement in the first case and never observes the new sandbox idle in the second. On this branch, the new lease launches on the returned ID after eviction, with meter events start/kill/start. With no retry, the ordinary alarm observes the unlaunched sandbox idle, and after the simulated lease interval a cold control pauses the same box and clears the alarm. Reading the alarm during an in-flight delivery was timing-dependent; the final test waits for the durable idle observation. All 167 standalone tests and full types passed; the same join passed 175 tests and full types with the same four move failures.
