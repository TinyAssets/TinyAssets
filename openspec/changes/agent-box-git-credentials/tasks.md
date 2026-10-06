## 1. Diagnosis and non-authority tools

- [x] 1.1 Inspect served/box paths and reproduce public clone, small pytest,
  large exact edit, branch/merge/conflict/rebase, and persistence in Linux.
- [x] 1.2 Install pytest on the production jail's Python, separately from the
  daemon venv; preserve existing isolation and resource limits.
- [x] 1.3 Complete synthetic authentication failure proof, Windows/Linux checks,
  ruff, hygiene and draft review; record evidence in the PR body.

## 2. L11 credential implementation (authorized 2026-10-05)

- [x] 2.1 Reconcile UID-split broker interfaces and specify binary streaming IPC
  with exact owner/agent/grant/host/repository/method bindings.
- [x] 2.2 Implement the route and surrogate lifecycle with current-grant checks,
  revocation, pinned HTTPS, bounded secret scanning and unknown-push handling.
- [x] 2.3 Prove synthetic authenticated clone/fetch/push, wrong-owner/host/repo,
  revoked grants, redirects, token non-exposure and binary pack integrity.
- [ ] 2.4 Integrate with an isolating `/cc` provider, pass a real-user flow,
  assert deployment SHA and sync the as-built spec before claiming completion.

## L11 handoff

Docker recovered. The synthetic IPC/jail proof and affected Linux suites pass:
322 tests, zero skips. The earlier broader run passed 224 tests and failed one
parent disconnect consent-context test, recorded in
docs/concerns/2026-10-05-broker-disconnect-consent-test.md. Cross-family review:
ADAPT, findings addressed (review.md). See delivery.md for exact commands.
Task 2.4 remains open: no production deployment or real-user app pass is claimed.
