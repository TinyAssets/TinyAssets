## 1. Diagnosis and regression

- [x] 1.1 Verify production exports, transcript window and provider receipts read-only; record evidence and decisions.
- [x] 1.2 Reproduce missing workspace inventory and inaccessible older topic search before fixing them.

## 2. Implementation

- [x] 2.1 Fix persistent workspace inventory and verify fresh real-jail reads; commit and push slice.
- [x] 2.2 Add owner-bound conversation search and continuity instructions; verify and commit/push slice.

## 3. Delivery

- [ ] 3.1 Run affected Windows/Linux tests, ruff, plugin mirror and hygiene checks.
- [ ] 3.2 Open/update draft PR with evidence and decisions; perform one cross-family floor/correctness review.
- [x] 3.3 Sync capability spec; record deployment and real-user proof as remaining if not shipped.

Verification so far: Windows regressions 9 passed / 2 environment skips; Linux affected suite 184 passed / 1 new engine-fixture failure, fixed by peer using real bound authority. Focused Linux rerun pending. Existing Windows-only limitations: shared-background-self symlink creation lacks privilege; universe-tools provider mount test assumes POSIX paths. No existing tests changed or weakened. Deployment and real-user app pass are not claimed for this draft.
