# test-waits as built (2026-11-14)

The flake re-check (#5601, `flake-recheck.md`) drew the line between a test that waits on the host's clock and one that waits on an event. This PR makes the line the untangle skill's, and sweeps every test under `platform/` for the defect it would have caught.

## The rule

The untangle skill's special cases (step 2) name a test's sleep: a test waits on an event it caused or a record it reads, a time bound is a liveness bound only, never raised, "has not happened" is read from a record, never an elapsed window, and time that must pass is the fake clock's. The skills pin is set to the skills' new size, 34,467: the line's 250 bytes on main's 34,217 (a raise of 247, since the pin had 3 bytes of slack).

## The sweep's lines

- **Defect:** a fixed sleep that orders two events; "has not happened" read after a fixed window; a timer-driven behaviour asserted against the host's clock (a lower bound, or an upper bound tight to the regression's own time); a real wait for time to pass.
- **Not a defect:** a bounded poll of what the test caused or a record it reads; a bound far from the regression's form (the regression hangs, or waits the code's own seconds); a recorded time bracketed by the test's readings of the clock; a timer the test sets as the event itself (a lock let go while the waiter's first try is synchronous); a zero-cost hop across a timer whose length does not matter; a fake's own timers that hold a stream open.

## As built

1. Owner: a lease's release is awaited, not slept for (`signin`, `runs-sandbox`); a lease kept is read from its row; a stream left open is read from its row (`runs-authority`, as #5601 did in `egress`); an erasure attempt not begun is counted on `Erasure.erase` once the door answered; a write past a deleting chunk is let go once its refusal is seen (`Files.begin`); single-flight is held by a lane the test releases; the files lane's due is read after the door's own sync and after what is due ran; the seed's third door is read from the files plane's uploads, not 50 ms.
2. Gateway: the fake provider records each request with an `x-fake-id` as it arrives and holds a `held` answer until the test lets it go; the cut waits for the arrival; the stalled body's deadline runs on the fake clock, asserted on both sides.
3. Doors: the OAuth grant's slide runs a day on the fake `Date`; "no frame" and "still open" are read before a ping's pong, which the object sends on the socket's own line after anything it sent first.
4. Sandbox runner: the race's processes are all ready before the moment they start is set.
5. One `until` (owner `test/support.ts`) replaces the two copies in `gateway-support.ts` and `erasure-support.ts`.

## Left, with the branch that fixes it (`untangle/test-waits-2`)

- The runner's real-clock tests (`runner.test.ts` renewal and heartbeat windows): the runner is another process on the host's clock; its timing has to come in as data, a bundle change.
- The gateway's wait-die and V-M2 tests (`held.test.ts`): on the fake clock, the body must be fed by the test so its reads and the sender's timer are ordered.
- The app's browser windows (`app.e2e.mjs`): page-side events, run where a browser is.
- The memory guard's leg scripts (`owner/guards/memory-workerd.test.ts`): the child's own events.
- The other copies of `until` (`gateway/test/held.test.ts`, `gateway/test/entry.test.ts`, `doors/test/runs.test.ts`, `doors/test/egress.test.ts`): one home across workspaces is a shared test module (`ci/workerd.ts`, beside `sqlIn`), with owner's moved there too.
- The guard: a straight-line timer wait outside a poll loop, in a test, is precise, but until the above are fixed it would land with owed entries.
