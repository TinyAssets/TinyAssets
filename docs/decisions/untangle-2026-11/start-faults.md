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

### Open: creation with no returned ID

The same synthetic 502 on the create request was injected before allocation and after allocation. After evicting the control and retrying at a two-slot ceiling, the first history produced one sandbox. The second produced two. The new no-duplicate regression remains red. Account erasure found the unacknowledged sandbox by the owner's tag and preserved the neighboring owner's sandbox. The ordinary suite passed 169 with this one failure. In the first combined run 183 passed, with the new duplicate, the four known move failures and one status-read fixture failure. Both typechecks passed. The first local invocation had omitted the three new tests and is excluded from these counts.

E2B documents a team-scoped creation key. Its reply can replay an existing sandbox ID for a matching request. The request body participates in the match; an in-progress attempt, changed request, unknown result, and an expired key have different outcomes. The SDK introduced the option in 2.61.0. [Provider contract](https://github.com/e2b-dev/E2B/blob/main/spec/openapi.yml) and [SDK changelog](https://github.com/e2b-dev/E2B/blob/main/packages/js-sdk/CHANGELOG.md).

Both Owner launch and sign-in seal their lifecycle route anew. On Node 22 and 24, the real sealer and creation adapter produced different request bytes for the same authority on retry. Reusing the original sealed route reproduced the original body when other inputs were fixed. This held for the owner authority and for a client authority. No live sandbox was used.

- [ ] Resolve recovery on #5857 and the ordinary start path with durable attempt identity, matching original request content and an explicit answer for expired or uncertain attempts. The control keeps no credential. Reconstructing the old request with a newly sealed route, silently replacing a retained key, or adopting solely by owner tag does not establish that the sandbox belongs to this attempt. Verify the deployed provider's retention and replay behavior before depending on it. Preserve account erasure and historical rows.

At a one-slot ceiling, the sandbox whose create reply was lost fills the last slot. After eviction the retry joins the queue and never calls create again. Its new ordinary recovery regression fails alongside the two-slot duplicate: 169 passed, two failed, full types passed. The join with #5857, move retention, list progress and `1c3aed85` passed 184, failed those two and the four earlier move regressions, and passed full types.

A disposable local Worker experiment sent a fixed synthetic attempt key and retained the first successful provider reply even though the caller saw 502. At two slots, the identical request replayed the original ID and launched there; changing only its sealed route produced a mismatch with no launch. At one slot, both retries stopped at the capacity check before a second POST. All four observations passed. The test fake modeled the documented contract. No provider request was made outside the experiment. An implementation must distinguish recovery of an existing allocation from making a new allocation before relaxing the capacity check.

A second disposable Worker rehearsal separated three full-slot histories. With the existing count check, all three waited. Blindly sending the keyed POST past the check recovered a retained response, but exceeded the platform's one-slot ceiling in the other two histories: an unrelated allocation occupied the last slot after the first create failed before allocating, or the original allocation survived expiry of its replay key.

An alternative rehearsal stored only an opaque synthetic attempt marker before the POST and looked up that marker before checking capacity. It confirmed a single positive listing against the box's own info and owner tag. Both retained and expired-key histories reused the original box without another POST. With no allocation it held the attempt. Missing or ambiguous listing evidence, or a listed ID without confirming info, also held without launching or posting. Once the evidence returned, all three started on the original ID. The six Worker observations passed against a fake provider. This candidate is experimental. Before runtime adoption, resolve how an indeterminate empty result stops holding a legitimate later start, interrupted move roles, cleanup of pending attempts, and the provider's actual replay and lookup guarantees. A 503 on a keyed request may also report unavailable idempotency state; preserve that distinction when adding keyed calls.

The five-stage sandbox rehearsal was also applied to current main `d662e63e`. The six sandbox files from the tested candidate matched byte for byte. On Node 22.23.5 the full platform CI workspace passed 362 tests with one skipped, and the shape registry passed. Both used current main as the base. The initial run on the older PR snapshot had 360 passes and two failures: the host proxy warning on stderr and the snapshot missing main's 35th Owner migration pin. The PR did not edit the pin. A shallow checkout also lacked the historical deletion needed for the shape check. The verified rehearsal includes the 35th pin, has its history, and suppresses only the host's UNDICI-EHPA warning. This checks cross-area CI, not recovery when the provider allocates without returning an ID.
