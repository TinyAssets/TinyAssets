# renew-deadline as built (2026-11-17)

The finding from #5677's review (`pass-sandbox.md` §2): the Worker passes its own now as
`renew-wait`'s deadline (`control.ts` `#renew`), so no renewal waits for its runs' answers.
Research first (Fact 14), then the fix.

## 1. Research

**What the renewal is for.** E2B runs a sandbox `runtime_ceiling_s` at a stretch, and a pause
resets that window (`sandbox-probe.md`, "Renewal across a forced 45 s window"; E2B's lifetime
FAQ in `e2b-resume-measurement.md`). From `renewal_window_s` before E2B's `endAt`, the look
waits for a quiet moment (`quiet-wait`, outside the chain), and at one, or at the edge
(`RENEW_EDGE_MS`), renews: the runs are told to post `ta.renewing` and hold their posts, the
sandbox is paused with its memory and resumed at once, `prepare` runs, and `renewed` lets the
posts go. The lease is a different clock: the owner renews it on each of a run's posts, and
settles a run it has not heard from in `LEASE_TTL_S` (60 s). A renewal's hold is about 9 s
of it (`RENEWAL_MS`).

**What a renewal waits for from its runs.** Each run's answer to `renewing`: it holds its
posts at once, then waits out the post in flight and posts what waits, `ta.renewing` among it
(`runner.ts` `Job`, "nothing is in flight when the sandbox pauses"). The answer is the close
of its channel (`tell`). The design budgets that step as `RENEWING_MS`, the 252 ms that
`ta-runner renewing` took with a live run, its frame posted (`sandbox-probe.md`, template 1).
The edge is that step plus the longest pause, so the forced renewal's pause lands before
`endAt`, and the alarm's wall budget counts it twice a row (`RENEWAL_MS`). The spec says the
same order: the runner posts `ta.renewing` and holds its posts, then the pause
(`specs/doors/spec.md`, "Renewal at a quiet moment").

**What passing the Worker's now loses.** It has done so since slice S (#5397): `renew-wait`
then skipped its own wait for quiet and signalled with no wait for the ack, and A6's `tell`
(#5483) kept that. Two separate things follow:
- **No answer is lost.** The runner takes frames off its outbox only on a 200 and posts them
  again after the resume, and the owner reads only frames past the run's last `seq`
  (`runs.ts` `batch`). An end whose answer the pause cut is posted again, and is refused as
  the run already settled. So no frame goes missing or is relayed twice, and no run's end is
  lost.
- **What a post in flight does across a pause is unmeasured.** If E2B's network resets the
  connection, the post fails at once and is retried: one post late. If the connection hangs,
  the poster stays busy with no deadline of its own (Node's fetch waits up to undici's 300 s
  for headers), so no heartbeat goes, and the owner settles the run lost after 60 s. The
  slice S probe that renewed a live run (all 30 events, `exit_code 0`) ran the old
  `renewing`, which waited for its frame. So no run of today's path has been seen. The design's
  drain is what keeps the question from mattering, and today it never runs before the pause.

So it is a real gap, not harmless: whether it costs a run depends on a vendor behaviour no
probe has looked at.

**The intended deadline.** The edge's docs and slice S's `tasks.md` line ("forced at the
measured edge (`renewing` 252 ms + the longest pause 705 ms)") give the step a bound, the
measured `renewing`. Nothing names a reason for a zero wait.

## 2. Decided

1. **The Worker waits for its runs' answers to a bound: `renew-wait`'s deadline is
   `RENEWING_MS` past its now.** `RENEWING_MS` is now that bound, not a sample. The step is at
   most two posts: the one in flight, then the held frames. Each is within the 252 ms
   measured, which included Node's start and the envd call. Add the drain's look at the post
   in flight every 50 ms (`FLUSH_MS / 5`), and the step comes to 554 ms. The 20% rule makes
   the bound at least 693 ms, so it is **700 ms**, the step at 79% of it. The edge and the wall
   budget compose the same constant, so they move with it: the edge is 1,405 ms and a whole
   renewal 10 s. A run that has not answered by the bound is paused regardless, as every run
   is today, and loses no answer (§1).
2. **`renew-wait` tells at once; the Worker's look alone picks the moment.** Its own wait for
   quiet went. With a real deadline it would spend the bound waiting for a CLI that is not
   quiet, at the edge, and then tell with no time left for the answer: the same gap again,
   just where the CLI is busiest. The look already waited for quiet (`quiet-wait`), read
   `state` on the chain, and renews only at a quiet moment or the edge. So when to renew now
   lands in one place. `renew-wait <deadline_ms>` keeps its argument and its meaning (protocol
   1, `protocol.json` unchanged), so the runner of either deploy reads what the other sends:
   an older runner given a real deadline waits for quiet, then for the answer, both bounded;
   this runner given an older Worker's now waits for nothing, as today.
3. **Kept: the deadline is on the sandbox's clock,** as `quiet-wait`'s `until` is. A skew
   between the Worker and the sandbox larger than the bound changes the wait, and only for a
   run that does not answer. Ahead, it shrinks to none, today's path. Behind, a run that never
   answers is waited for longer, within the command's own `COMMAND_MS`. A relative wait would
   be protocol 2, two deploys, for a skew nobody has measured.
4. **`PAUSE_MAX_MS`'s doc says what was measured.** It said "the longest pause". It is the
   longest of 40 pauses with 150 MiB of memory in use. The scratch probe's pause, just after
   6 GB were written, took 7.0 s (`sandbox-probe.md`). Kept as it is: an edge renewal that
   slow lands on E2B's own memory pause at `endAt` (probed: the process alive), which E2B
   answers 409 or 503. `#pause` reads 409 as paused and 503 as busy, and either way the
   resume follows. That is the edge doc's own fallback. Raising it would also slow
   `#pause`'s busy back-off, which starts from it.
5. **The wait ends by `endAt` less the longest pause, when that is sooner** (#5680's review).
   The edge holds only for a look that renews at it. A look later than the edge (its own E2B
   calls, an alarm run late) waited the whole bound, so its pause could land past `endAt`,
   on E2B's own timeout, with the posts not yet held. Capped, a late renewal waits what is
   left and, past that, tells with no wait, as before this change.

## 3. Tests

- `test/sandbox.test.ts`, the quiet renewal: `renew-wait`'s deadline is `RENEWING_MS` past
  the renewal's clock read. Restoring the Worker's bare now fails it. The forced renewal,
  200 ms inside the edge: the deadline is `endAt` less the longest pause. Dropping the cap
  fails it.
- `runner/runner.test.ts`, "waits for a quiet moment, and renews at its deadline regardless"
  is replaced by "tells a run that is not quiet at once, and is back at its deadline when the
  run cannot answer". Its CLI is never quiet and its doors answer every post busy.
  `renew-wait` is back at its deadline with the run not yet acted. Once the doors take posts,
  the run acts, and its `ta.renewing` is stamped before the deadline. Restoring the quiet
  wait fails the stamp, and a `tell` with no wait fails the return.
- The large test "posts renewing when quiet, holds its posts" holds the answered case: its
  doors answer two posts busy, and `renew-wait` returns only once the frame was posted and
  the run's state says it acted.
- `RENEW_EDGE_MS` in the Worker's suite is the design's numbers written out (700 + 705), as
  before.

## 4. Found and left

- **The runner's posts have no deadline of their own** (`Poster.#post`, and the sync
  client's). A post in flight across a pause, or any blackholed connection, holds the poster
  for up to undici's 300 s, past the lease's 60 s. The renewal's wait keeps the runner's posts
  out of flight in the normal case. What a pause does to a connection in flight is the live
  test's: it is added to `sandbox-probe.md`'s list owed to the founder's live test, beside a
  model call the pause cuts (`live:` that test). If it hangs, the fix is a post deadline under
  the lease's TTL, and that needs its own research, since a slow owner can answer late.
- **`RunState.renewing` is written for the tests alone.** No code reads it, and a drain that
  outlives its deadline can set it after `renewed` cleared it. It is a field of state format
  1 (`state-format.json`), so it goes with the next format change, which ships in two deploys
  (`tasks.md`, the re-check loop's sub-line).

## 5. Owed at the next deploy

The runner's code changed, so the pair moved. Template `tinyassets-sandbox:s85528cdd2cd74efa`
is unchanged, so no paid build is needed. The runner bundle's digest goes from `36d72812188c8573`
to `e406bdcb6ca501eb`. The probes carry the runner's digest, so theirs moves too, from
`112a5b22659bc8ef` to `462b0e87a5c7a5d3`. At the next deploy, the kit's pair check finds no tag
`tinyassets-sandbox:s85528cdd2cd74efa-re406bdcb6ca501eb-p462b0e87a5c7a5d3` at E2B. It runs the
standing checks (`checks.ts pair`, throwaway sandboxes) and tags the pair before the apply, or
stops the deploy. Sandboxes running then adopt the new runner at their next start or resume
that finds nothing living (`#adopt`). Until then they run their old runner, which reads the new
Worker's deadline (item 2).
