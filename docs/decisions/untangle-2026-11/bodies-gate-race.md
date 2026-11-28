# bodies-gate-race as built (2026-11-28): the sweep's delete is gated before it is made due

`fix/bodies-gate-race`. Found on #5856 (docs only): CI's owner shard 1/2 (run 45307714774,
job 138155635947) failed `owner/test/bodies.test.ts` "a placed write past a chunk the sweep
is deleting begins again once that delete lands" with `no deleteChunks of <ns> was held at
the gate`; a re-run on the same commit passed.

## Research (Fact 14), before the fix

- **The test made the sweep due, then armed the gate.** It set the garbage chunk's
  `gc_after` to 1 and wrote a row, whose `sync()` points the `files` lane's job at the chunk,
  due at once (`Files.next`: `max(gc_after + slack, now)`), and Lifecycle arms the object's
  alarm for now. workerd fires that alarm on its own (`support.ts` `drainDue` says so). Only
  then did the test read the namespace and arm `deleteChunks` at the FILES gate. When the
  runtime's alarm ran in that window, the delete crossed unheld, the chunk row was gone, the
  test's `runDurableObjectAlarm` found no alarm pending, and `held` waited out
  `HELD_WAIT_MS`.
- **Shown, not guessed.** With a 300 ms wait put between the write and the arming, the test
  failed every time with CI's error, and the chunk's row was already gone (`[]`) with the
  next alarm in the future. Under load with no probe, the test as it was failed 13 of 123
  runs (three processes of 41 runs, `repeats`, beside four CPU burners on four cores).
- **The owner code has no race here.** `Files.run` registers its delete in `#deleting`
  before the unit that marks the chunks `deleting`, and drops it only after the unit that
  removes their rows; `Bodies.placing` reads `deleteLanded` in the same unit as the begin's
  `chunk_deleting` refusal (both through `#write`). So a begin that meets a `deleting` chunk
  always finds the delete in flight, or a chunk a failed delete left (refused by design), and
  never one that landed since.
- **No other test has the order wrong.** Every other gated pass in the owner tests
  (`connections`, `doorbell`, `runs`, `archive-*`, `code-door`) arms before the act that
  makes its pass due, and nudges the alarm after (`connections.test.ts`: "by the runtime's
  alarm, or this one").

## Design

Arm the gate before the act that can send the call: the namespace is known from the first
write, so the arming moves above the `gc_after` change and the due write. Whichever alarm
runs the pass (the runtime's or the test's `runDurableObjectAlarm`), its delete is held.
No product change, no wait, no retry, no bound raised.

## Checked

- The fixed test, with the same 300 ms probe (the runtime's alarm always first): passes.
- The fixed test under the same load: 303 of 303 runs (three processes of 101).
- `bodies.test.ts` whole: 18 of 18. `npm run typecheck`: clean.
