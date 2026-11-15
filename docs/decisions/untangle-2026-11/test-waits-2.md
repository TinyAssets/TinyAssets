# test-waits-2 as built (2026-11-15)

What #5605 (`test-waits.md`) left on this branch: every remaining test that waited on the host's clock now waits on an event it caused or a record it reads, or runs on the fake clock, and a guard holds the rule over every platform test.

## The guard

`ci/timeouts.ts` `timerWaits`, held by `ci/guards/timeouts.test.ts` beside the hand-set timeouts it already refuses. A timer wait is an `await` of a timer's promise (`new Promise((r) => setTimeout(r, ms))`, `scheduler.wait(ms)`, or a helper of the file's own that is one) or an `Atomics.wait`, for any length but zero. It is refused unless it is inside a loop that polls (one that leaves by a `break` or a `return`, or whose condition reads more than its own counter and the clock), or on the fake clock (after a test's body installs it and before it turns it off, or in a suite whose `beforeEach` or `beforeAll` installs it). A timer's own callback, a promise handed on rather than awaited, and a zero-length hop are not waits. It reads every tracked `*.test.ts`, `*.test.mjs`, `*.e2e.mjs` and file under a `test/` folder in `platform/`, and owes nothing: every site it finds is fixed here.

It reads the syntax, not the run: a wait on the clock written another way (a child script's text, a fake's own timer the test awaits through a call) is a reviewer's to see, by the skill's rule.

## The runner: its own events, no bundle change

The runner is another process on the host's clock, so the fake clock cannot reach it without a hook only the tests would use. Each runner test now reads the runner's own events and records instead of the test's clock, and the runner's source is unchanged (its bundle digest too):

- A sync-out longer than a heartbeat: the fake doors hold the commit until the run's heartbeat (an event post with no frames) comes after it, so the run ends only if the runner heartbeats through the sync-out.
- The renewal told on a run's channel: two runs, the second listening only once the first heard its message, which `renew-wait` sends after its first try of both channels (both tried at once, synchronously). Each listener sends bytes past the socket's buffers before its answer: only a teller still waiting reads them.
- The pause: a control run, launched after the pause began, shows by its own heartbeat that a whole heartbeat passed on the runner's clock, while the paused run posted nothing.
- The deadline: the run's renewing is stamped by the runner's own clock at or after the deadline the test gave as data.

The heartbeat's 20 s still passes in the two large tests: it is the runner's own time, read from its own posts, never a guess of the test's.

## The rest

- Gateway: the wait-die cases, V-M2 and V-H2 run on the fake clock; V-M2's body is fed by the test, so its reads and the sender's timer are ordered.
- The browser: the CSP's one late attempt is read from its refusal (`securitypolicyviolation`); the payloads from a record of every element made with an inline handler or as inline script; each frame once it holds its own document; the CSRF post once its answer came; the two stale screens once the page parsed the answers they wait on and the code awaiting them ran (the tests' own instruments, beside `__replaced`).
- The memory guard's legs: each step follows the stand-in child's own event (its exit, its spawn).
- Three zero-cost hops lost their length (`gateway/test/egress.test.ts`, `owner/test/slots.test.ts`, `owner/test/wakes.test.ts`): a hop needs none.
- `until` has one home, `ci/workerd.ts`, beside `sqlIn`; it polls on workerd's own timer, so a test on the fake clock polls the same. The gateway's and the doors' copies are gone, each caller keeping its old bound, and the owner tests import it, and `sqlIn`, from there, not through `owner/test/support.ts`.
