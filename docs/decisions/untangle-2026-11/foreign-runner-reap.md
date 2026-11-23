# foreign-runner-reap as built (2026-11-22)

`untangle/foreign-runner-reap`, found in #5775's review (`runner-protocol-contract.md`,
`sandbox-start-fault.md`). In `platform/sandbox/src/control.ts`, the look (the alarm that
reaps, renews and resumes an owner's sandboxes) asked each running sandbox's runner its
`state` and refused one speaking a protocol the Worker does not (`SPEAKS`). Such a sandbox was
never reaped: every look failed on it. The failure also stopped the look for the owner's other
sandboxes. So one such sandbox left them, and itself, running and billed to E2B's own timeout.

## Research first (Fact 14), read 2026-11-22 on main a0762eeee

1. **How the look goes.** `alarm` → `#look`: outside the serial chain, each running sandbox in
   its renewal window is sent `quiet-wait`, all at once in one `Promise.all`. Then, on the
   chain, each row goes through `#lookAt` in turn: gone (forgotten), paused (resumed when a
   lease runs in it or a renewal is pending, else left), releasing (`#reap`), before its window
   (the idle look: no live run for a lease's TTL pauses it), in its window with nothing live
   (paused), or renewed. A throw anywhere, from one row's E2B call or runner command, rejected
   the whole look. `alarm` then backed off (`known.failures`) and never reached the rows after
   it, in either pass.
2. **What the look needs from the runner.** Two answers: whether anything lives (`live`) and
   whether its CLIs are quiet (`quiet`). Both come in `state`, which every protocol answers
   alike (`state-format.test.ts` holds records 1 to 4 to the same command, answer, loader path
   and exit codes, #5775). Pausing an idle sandbox is E2B's alone. Only a renewal needs the
   runner beyond `state`: `quiet-wait`, `renew-wait`, then `prepare` and `renewed` (or
   `recover`) after the resume. So the look can reap an idle sandbox whatever its runner
   speaks. Only one a job lives on, on a runner the Worker does not speak, cannot be carried
   on: no renewal may send it those commands.
3. **What E2B lets the Worker do without the runner** (`sandbox/src/e2b.ts`,
   `sandbox/e2b/transport.ts`): `info` (state, `endAt`, `pauseMode`, metadata), `pause`
   (memory kept; 503 while a snapshot finishes), `connect` (resumes a paused sandbox, its
   timeout at least the runtime ceiling from now), `kill` (`DELETE`, 404 taken as done), `list`
   by metadata, `running`, and a network replace. Metadata and pause-on-timeout
   (`autoPause: true`, memory kept) are set at the create. The client changes neither after
   it, so the Worker cannot have E2B kill a sandbox at its timeout. E2B pauses it, and a
   resume restores the job.
4. **What a paused sandbox holding such a job becomes.** Every start resumes it (billed),
   asks `state`, finds the job live and refuses `sandbox_runner_protocol` (`#speaking`,
   #5775). The look's resume of a sandbox a lease runs in sent `prepare` and `renewed` to the
   runner without asking its protocol at all: `#resume` checked only for a start. That is the
   Fact 8 misread #5775 closed for starts, still open on the look's path. It is exactly where
   such a sandbox lands today: every look failed on it, so E2B's own timeout paused it.
5. **The adopt path** (#5775's `#speaking`/`#adopt`). This Worker's runner is installed by
   digest and adopted where nothing lives, at a start or at a start's resume. A runner kept
   (something lives, or its adoption failed) must speak `SPEAKS`. Adoption under a live job is
   refused by the runner itself (`adopt` answers "no"), so no adoption helps a sandbox a job
   lives on.
6. **The limits** (Fact 15). The owner's limits are off by default, and no platform deadline
   ends a run (`owner/src/runs.ts`: its lease lives while its heartbeat renews it). What bounds
   a sandbox's stretch is the vendor's: `runtime_ceiling_s`, E2B's plan ceiling, at whose end
   E2B's timeout pauses it (`contract/src/control.ts`). A renewal resets that window. Without
   one, the window's end is the most a job on such a runner can run.
7. **What the owner is told.** The sandbox Worker has no notice channel. It reaches the owner
   only through its meter (`meter`) and E2B's signed lifecycle events (`sandboxBilled`). A run
   whose runner stops reporting is settled `unknown` once its heartbeat lapses
   (`owner/src/runs.ts`). That happens when E2B pauses the sandbox at its window's end, before
   any kill.
8. **What a kill loses.** The sandbox's disk: scratch is a cache, never synced nor counted
   (`runner/scratch.ts`), and a frozen job's unsynced work, which no renewal could save.
9. **Live.** No owner sandbox exists (#5775's research), so nothing holds such a job today.

## The shape, checked against the intent

- **The look reads any runner by its `state`, and the reaper is one path.** The protocol check
  leaves the look's `state` read: idle is idle, whatever the runner speaks. An idle sandbox on
  a foreign runner is paused by the look or a release like any other, and the next start
  adopts this Worker's runner (#5775). No special path.
- **What follows from a runner's `state` is kept in one place** (`#heard`), on every path that
  reads it: the look's state read (`#runnerState`, the look and the release's reap) and
  `#speaking` (a start's, and now every resume's, check). On a runner it does not speak, no
  renewal carries the sandbox on: a pending one is dropped (`renewing = 0`), so no look
  retries a renewal resume it would refuse. A job living on it ends with the window it was
  found in: `kill_at`, kept from the first look that saw it, so a later resume does not move
  it.
- **Until `kill_at` it is busy, looked at once a lease's TTL** as before its window, and sent
  nothing but `state` (the quiet look asks `state` first of any runner not the Worker's own).
  Its job runs the whole window the vendor's ceiling allows. Should the job end first, the
  next `state` clears `kill_at`, and the idle reaper pauses the sandbox.
- **At the window's end, the look kills it by its E2B id** (`#kill`). E2B's own timeout has
  paused it by then, or the look finds it running past its end. Killing it only once its
  window is over keeps Fact 15: nothing is killed while its job may still run within the
  ceiling it was allowed. A pause is rejected: kept paused, it holds a job that every start
  resumes, pays for and refuses, forever. Loud: logged when first seen (`sandbox_runner_protocol: ...
  it is killed at its window's end`) and at the kill.
- **Every resume asks its runner first** (`#resume` loses its `start` flag). The look's resume
  of a paused sandbox a lease runs in now asks `state` before `prepare` and `renewed`. An
  unspoken runner is refused there as at a start. The refusal is that sandbox's failed look,
  logged, and `#heard` has kept its `kill_at` (the window it was paused at), so the next look
  kills it.
- **Each sandbox is looked at on its own** (`#look`'s `settle`). A sandbox whose look fails, in
  the quiet pass or on the chain, is logged with its id and left to the next look. The others
  are looked at still. A sandbox failed in the quiet pass is not looked at on the chain, so a
  failing `quiet-wait` never turns into a look at alarm speed. `alarm` backs off as before
  whenever any sandbox failed, sooner only for what the settled sandboxes need.
- **No owner notice is added.** The owner's record already says what happened: the run on such
  a sandbox is settled `unknown` when its heartbeat lapses at E2B's pause, before the kill. A
  notice would need a new channel from the sandbox Worker to the owner's object, a contract
  expand, for a case that only a deploy-order slip can produce and that nothing live holds.
  The founder's alert is the loud log.

## As built

1. `sandbox/src/schema.ts`: migration 5, `sandboxes.kill_at`, pinned
   (`migrations/SandboxControl.json`).
2. `sandbox/src/control.ts`:
   - `stateOf`, one reader of `state` for its three callers, replaces `spoken`.
   - `#heard` keeps `kill_at` and drops a pending renewal.
   - `#speaking` takes the `found` it was met at and refuses by name as before.
   - `#runnerState` reads any runner.
   - `#resume` always checks its runner, and owes a renewal's held slot back as soon as E2B
     has resumed the sandbox. Before, that came only at the end, so a refusal or fault after
     the connect kept the slot held.
   - `#arm` arms a paused `kill_at` row at once, and a running one by `kill_at` in place of
     its renewal window.
   - `#look` settles each sandbox on its own and returns how many failed. `alarm` backs off
     on any failure.
   - `#lookAt` kills a paused `kill_at` row, and a running one once its `kill_at` passes.
   - A start's create forgets the row of a sandbox E2B no longer has (`#forget`, as the look
     does) before it inserts the new one (review). Its upsert kept every column it did not
     name, `kill_at` among them: a gone sandbox's `kill_at` became the new one's, and a look
     that met the new one paused before reading its runner killed it, a live run on this
     Worker's own runner with it. The upsert also kept the gone one's `network_due`,
     `idle_since` and back-off, and a held slot it never owed back.
   - The header says all of it.
3. `sandbox/test/sandbox.test.ts`:
   - An idle sandbox on a protocol 3 or 9 runner is reaped by the look (`state` alone, no
     `quiet-wait`) and by a release.
   - A job on one runs to its window's end, looked at again a TTL on, never killed early. At
     E2B's pause and the owner's release, it is killed by its id, and nothing is armed after.
   - One past its end while still running is killed, the meter logging it.
   - A paused one no look had read is resumed, asked `state` alone, its slot given back, and
     killed at the next look.
   - Each sandbox is looked at on its own, failing before its window or in it.
   - A refused renewal's runner leaves no renewal pending.
   - A sandbox a start made in place of a gone one with a `kill_at`, paused before any look
     read it, is resumed, never killed (review).
   - A start's refused resume owes its give-back at once, so its look is armed at once and the
     runtime may run it: the test waits for the look to be armed again rather than reading the
     alarm in that moment (review; it failed about one run in ten).
4. `runner-protocol-contract.md`: its "Left" line corrected (the look kills such a sandbox at
   its window's end).

## Left, with why

- **No owner notice** (above): the run's `unknown` settle is the owner's record. A notice
  channel from the sandbox Worker would be a contract expand for a case nothing live holds.
- **A look refused at a resume counts as a failed look** for its alarm's back-off (one try at
  480 ms). It is loud, and the next look kills the sandbox. Catching that refusal by its code
  would be a special case for one path.
