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
- [ ] 2.2 Implement the route and surrogate lifecycle with current-grant checks,
  revocation, pinned HTTPS, bounded secret scanning and unknown-push handling.
- [ ] 2.3 Prove synthetic authenticated clone/fetch/push, wrong-owner/host/repo,
  revoked grants, redirects, token non-exposure and binary pack integrity.
- [ ] 2.4 Integrate with an isolating `/cc` provider, pass a real-user flow,
  assert deployment SHA and sync the as-built spec before claiming completion.

## L11 handoff

Implementation is an unverified draft: authority and transport code plus a
synthetic IPC/jail proof have been written. Linux execution is blocked by the
Docker engine; see docs/concerns/2026-10-05-l11-git-credentials-linux-proof-blocked.md.
Tasks 2.2-2.4 remain unchecked until their evidence exists.
