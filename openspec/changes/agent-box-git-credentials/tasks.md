## 1. Diagnosis and non-authority tools

- [x] 1.1 Inspect served/box paths and reproduce public clone, small pytest,
  large exact edit, branch/merge/conflict/rebase, and persistence in Linux.
- [x] 1.2 Install pytest on the production jail's Python, separately from the
  daemon venv; preserve existing isolation and resource limits.
- [ ] 1.3 Complete synthetic authentication failure proof, Windows/Linux checks,
  ruff, hygiene and draft review; record evidence in the PR body.

## 2. Proposed credential route (not authorized for implementation in this lane)

- [ ] 2.1 Reconcile UID-split broker interfaces and specify binary streaming IPC
  with exact owner/agent/grant/host/repository/method bindings.
- [ ] 2.2 Implement the route and surrogate lifecycle with current-grant checks,
  revocation, pinned HTTPS, bounded secret scanning and unknown-push handling.
- [ ] 2.3 Prove synthetic authenticated clone/fetch/push, wrong-owner/host/repo,
  revoked grants, redirects, token non-exposure and binary pack integrity.
- [ ] 2.4 Integrate with an isolating `/cc` provider, pass a real-user flow,
  assert deployment SHA and sync the as-built spec before claiming completion.
