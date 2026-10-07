# Execution guard integration disposition

2026-09-19. Lead-dispatched Claude Fable 5.1 session 94507, terminal exit 0 after
461 seconds, exact primitive checkpoint `f68a9db8`: **ADAPT**. Full substantive
output is `execution-guard-review.md`. This is primitive/contract review, not
approval of later prepared-worker code or the final integrated release head.

- **Accepted required correction:** queued without a held guard can be healthy
  work waiting in a saturated live pool. No retirement or death inference from
  guard availability, local Future absence, PID or insertion timestamp.
- **Accepted required correction:** guarded start and terminal writes require
  expected-status/claim CAS; zero rows means no execution/no overwrite. Audit
  request-thread provider failure and late dequeued workers as well as normal
  execution. Cloud owns the ordinary lifecycle adaptation; this lane owns the
  admitted marker/worker adaptation using the same guard.
- **Agreed primitive:** current process/thread/context registration, canonical
  DB/run scoping, stable sidecars, nonblocking OS exclusion and crash release.
  Native Windows review evidence was eight passing tests; builder additionally
  proved eight Linux tests with no skips. Not evidence for integrations absent
  at the reviewed head.
- **Accepted documentation:** host-local filesystem/SQLite assumption and
  non-reentrancy. Pass the existing guard to nested invocation wrappers.
- **Remaining activation proof:** saturated-pool queued versus recovery,
  late worker after cancellation/retirement/resume, remote active worker with no
  local Future, Stop without waiting for the lifetime guard, exact owned
  namespace/kernel evidence before retirement, and complete Linux integration.
  A queued durable-started envelope retains unresolved cleanup debt without
  replay; it is not safely unstarted just because its row still says queued.

The lead authorized these adaptations without another broad shape review.
Exact integrated-head cross-family review, CI/deployment and ordinary app proof
remain open. No new queue, TTL takeover, production setting or user workflow.
