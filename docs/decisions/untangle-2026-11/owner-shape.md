# owner-shape as built (2026-11-21)

Two owed items of `pass-owner.md` §6.7, found by the review of #5719: a start's answer out of
shape read as no answer (`untangle/start-answer`), and a placed write's guessed sleep past a
chunk the sweep is deleting, with `LOG_WIDE` read only by tests (`untangle/place-wait`). It
finishes a stopped builder's partial work, kept where it is right.

## Research (before the build)

1. **What a start's call can yield** (`SandboxService.start`, `signin`; `sandbox/src/control.ts`).
   - An answer in the contract's shape: `{ started: true }`, or `{ started: false, code }` with
     a `START_CODES` code.
   - A refusal thrown before anything launches: `sandbox_runner_mismatch` (the loader's 25 on
     any command, the launch's included, which then never runs) and `sandbox_runner_protocol`
     (`stateOf`, in `#ensure`). Both cross RPC as a `Refusal` (`refusalOf`; the sandbox suite
     holds the mismatch's code). The runtime's overload also reads as a refusal: the call was
     never run.
   - A plain throw: the answer lost on the way, or the sandbox Worker's own fault (its bad
     request check, the launch's non-zero exit). The object cannot tell them apart, and a lost
     answer may come after the job launched.
   - An answer out of shape: the sandbox Worker's bug, after whatever it did.
2. **What main did with them.** `launch` and `signinStart` each wrapped the call and
   `started()` in one `try`. `started()` threw on an answer out of shape, so the catch took it,
   and a thrown refusal, for a lost start: `unknown` `start_lost`, cancelled, one log line
   with no cause. The two catches were copies.
3. **The sweep's delete** (`files.ts` `run`). It marks its chunks `deleting` in one unit, sends
   `deleteChunks` bounded by the pass's budget (`until`, inside `LANE_CALL_MS`), then removes
   their rows in another unit, or leaves them `deleting`, retried after `GC_RETRY_S`. A begin
   that meets a `deleting` chunk is refused `chunk_deleting` (its bytes may be going). So a
   chunk is `deleting` for one of two reasons: a delete in flight on this instance, whose end
   is an event the object can wait on, or a delete that failed or was cut by an eviction, which
   no wait in this request ends.
4. **The gate runs a unit at once** (`gate.ts`: no queue). A delete's last unit can run between
   a refused unit and its caller's `catch`, so a check of the deletes in flight made outside
   the refused unit can miss one that ended meanwhile and refuse a write that would now land.
   The stopped builder's draft checked outside the unit.
5. **`LOG_WIDE`.** The capture triggers (`schema.ts`, frozen) write `{"t":"wide"}`; the product
   reads any line that is no `row` and no `gone` as wide (`changelog.ts` `segment`, `parsed`).
   Its readers were `changelog.test.ts` and the frozen-bounds guard.

## As built

1. **One read of a start** (`owner/src/sandbox.ts` `startOutcome(job, start)`), used by
   `launch` and `signinStart` in place of their two catches. It awaits the call inside its
   catch and reads the answer outside it, so an answer out of shape is never a lost start:
   - out of shape: `unknown` `start_malformed`, logged as the sandbox Worker's bug, cancelled
     in case it started;
   - a thrown refusal: `failed` with its code, logged, nothing to cancel;
   - a plain throw: `unknown` `start_lost`, logged with its cause, cancelled.
   A sign-in ends by the same code (`{ started: false, code }`), its lease freed.
2. **A placed write waits on the delete it met** (`bodies.ts` `placing`). In the unit that met
   the refusal, it asks the files plane for the end of the delete in flight that takes one of
   its chunks (`Files.deleteLanded`): with one, the unit returns it, and the write waits and
   begins again; with none, the refusal stands. `PLACE_TRIES` and its sleep go. The wait is
   bounded by the delete's own bound, the pass's budget.
3. **The deletes in flight are the files plane's own** (`Files.#deleting`): each pass's delete
   registers its chunks in the unit that marks them and resolves its end once its rows are
   removed or left (`run`, `#deleteDue`).
4. **`LOG_WIDE` goes.** The guard and the test spell the frozen text they pin;
   `contract/src/limits.ts` names the triggers.

## Found and left

- The stopped builder's draft is kept in `sandbox.ts`, `signins.ts`, `files.ts` and the tests,
  reshaped as above. Its refusal branch is in scope: the same catch took a refusal for a loss,
  the same conflation as an answer out of shape.
- `s7.md` says an answer out of shape settles `start_lost`; it records its own PR, and this file
  supersedes it.
- A wait for a delete held longer than a guess is shown on the fake clock (the review): the
  bodies test advances it by `LANE_CALL_MS` inside the object while the delete is held, and
  the write begins once, so a guessed sleep in place of the wait fails it.

## Owed

- [ ] `launch.ts` `beginReply`: a long reply that meets a chunk the sweep is deleting is
  dropped to a notice (`chunk_deleting` is terminal). It begins inside a runner batch's unit,
  which cannot wait; research first whether the batch should be posted again
  (`untangle/reply-place-wait`).
- [ ] `signins.ts` `signinStart`: a start with no answer the object can read ends the sign-in
  and frees its lease, but nothing stops a sign-in CLI that did launch, where a run's is
  cancelled. Research first (`untangle/signin-start-unknown`).
- [ ] `sandbox.ts` `startOutcome`: a transient refusal of a start (`object_overloaded`, the
  runtime's overload of the sandbox Worker's object, read by `refusalOf`) settles the run
  `failed`, where the contract leaves a transient one to be tried again (`refusal.ts`'s
  header); main settled it `unknown` `start_lost`, also never tried again. Research first
  whether it waits as `sandbox_waiting` does, for the lane's next pass
  (`untangle/start-transient`; found by the review of #5733).
