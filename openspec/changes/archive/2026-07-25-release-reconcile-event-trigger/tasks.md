## 1. Initial Trigger Slice

- [x] 1.1 Add focused tests for the retained schedule/manual triggers,
  completed `Docker build smoke` runs on `main`, job gating, current-main
  checkout, and unchanged permissions; record the expected 2-failure RED.
- [x] 1.2 Add the minimal smoke `workflow_run` trigger and successful-main job
  guard; record 4 focused tests and 82 relevant workflow tests passing.

## 2. Opus 5 Blocker Reproductions

- [x] 2.1 Strengthen the trigger contract so deleting manual dispatch,
  changing conjunctions to disjunctions, or admitting fork pull requests fails;
  record the current-tree RED before fixing provenance.
- [x] 2.2 Add exact decision-script harness cases for current active release
  work, stale active work, active/deploy query failures, in-sync ancestry, and
  missing deploy; record the current-tree failures before adding the guard.
- [x] 2.3 Add build-image concurrency and exact converge-script harness cases
  proving manual recovery does not cancel active push work, a requested build
  is awaited, unchanged main deploys the immutable short-SHA tag, and advanced
  main does not deploy; record the current-tree failures.

## 3. Safe Convergent Implementation

- [x] 3.1 Restrict the privileged reconcile job to successful own-repository
  `push` or `workflow_dispatch` smoke runs on `main`.
- [x] 3.2 Make active/successful GitHub run-state queries fail closed and
  suppress duplicate dispatch only when an active main release SHA contains
  the relevant commit.
- [x] 3.3 Make manual image-build runs non-cancelling, then have reconciliation
  identify and await its same-SHA build and explicitly dispatch `deploy-prod`
  only while repository `main` still equals that SHA.
- [x] 3.4 Remove false 15-minute latency claims, preserve the live-receipt
  caveat, and run focused pytest, Ruff, actionlint, and strict OpenSpec checks.

## 4. Executable Load Proof and Review

- [x] 4.1 Execute the exact decision script under a 1,000-arrival
  one-running/one-replaceable-pending model and prove one corrective dispatch,
  no active-build cancellation, and no stale-run over-suppression.
- [x] 4.2 Record the command, environment, date, result, and scheduler-model
  limitation in `2026-07-25-release-reconcile-concurrency-proof.md (archived in git)`.
- [x] 4.3 Reproduce Opus 5's production-Python and repeated-deploy findings,
  make the exact scripts pass on Python 3.12, add a pinned CI contract, and cap
  failed same-SHA explicit retries before another build or deploy.
- [x] 4.4 Request a fresh Opus 5 review of the complete immutable diff, resolve
  every blocking finding, and rerun affected checks.

## 5. Verification and Foldback

- [x] 5.1 Update `REFLECTION.md`, sync the uptime-and-alarms delta into the main
  spec, archive the change, and rerun strict OpenSpec validation.
- [x] 5.2 Run the repository's required pre-merge gates, publish the PR, merge
  only after required checks are green, then remove the landed STATUS work row.
