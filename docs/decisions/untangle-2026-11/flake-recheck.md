# flake-recheck as built (2026-11-14)

Four local failures earlier builders called flake or load (`permanence_audit.md` Part 4: F4, F6, F7, F8), rerun on an install `ci/deps.ts` holds to the lockfile (#5590).

## The line drawn

- **A defect:** a test that orders two events by a fixed sleep, or asserts a timer-driven behaviour against the host's clock. Load stretches the host, so the test fails, or stops testing its case, with no change in the code.
- **Not a defect:** a bounded poll for something another request does, which the test caused. workerd wakes no request by a promise another resolves (`ci/gate-worker.ts:19-22`), so such a poll is the only way to await it, and its bound is a liveness bound, not a timing assumption. The same holds for the runtime running an alarm armed for now: `drainDue` (`owner/test/support.ts:186`) relies on that across the owner suite.

## As built

1. `sandbox/test/sandbox.test.ts`, "looks on when a start adopted another runner under its quiet look", slept 100 ms to put a start inside a look whose quiet-wait the fake answered 400 ms late. The fake now holds that command until the test lets it go (`hold`, `/release`, replacing `slow`), and the test waits for the hold to be seen. One poll of the fake's state, `seenOnce`, replaces the three copies (`rung`, the pause wait, and this one).
2. `doors/test/follow.test.mjs` asserted the socket's reconnect backoff against the host's clock (`wait(1200)`, "two by 2.5 s"). It runs on node's mock clock (`t.mock.timers`, `setTimeout` and `Date`) and asserts each boundary: no socket before 1 s, two tickets by 2.5 s, three by 3.5 s.
3. F4 (`import`) and F6 (`owner/test/egress.test.ts`, the policy write's held re-apply) hold no wait of the first kind and are unchanged. The 2 s wall-clock wait F6 failed on in T1b4 (`env.SANDBOX_GATE.held(HELD_WAIT_MS)`) went with #5564.
