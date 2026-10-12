## 1. Diagnosis and regression

- [x] 1.1 Verify production exports, transcript window and provider receipts read-only; record evidence and decisions.
- [x] 1.2 Reproduce missing workspace inventory and inaccessible older topic search before fixing them.

## 2. Implementation

- [x] 2.1 Fix persistent workspace inventory and verify fresh real-jail reads; commit and push slice.
- [x] 2.2 Add owner-bound conversation search and continuity instructions; verify and commit/push slice.

## 3. Delivery

- [x] 3.1 Run affected Windows/Linux tests, ruff, plugin mirror and hygiene checks.
- [x] 3.2 Open/update draft PR with evidence and decisions; perform one cross-family floor/correctness review.
- [x] 3.3 Sync capability spec; record deployment and real-user proof as remaining if not shipped.

Final verification: affected Windows selection 81 passed / 4 symlink-privilege skips. Linux affected suite 184 passed / 1 new engine-fixture failure; Claude peer repaired that fixture using real serving authority. Final focused Linux oracle 12 passed / zero skips, including the real-jail persistence proof. The two unchanged Windows POSIX assumptions (shared-background-self symlink creation and provider mount paths) passed in Linux. Ruff, plugin import probe/mirror, strict spec validation and diff checks pass. Hygiene: 9 functions added, 0 removed, 0 tampering. Isolated session-predicate mutation returns foreign ID 2 and is detected by the regression's exact-ID assertion.

Cross-family review: Claude APPROVE, diagnosis AGREE, approach AGREE; no floor/correctness findings. Non-blocking inventory-error omission nit deferred: preserve existing fail-closed behavior and avoid hiding storage errors behind a partial inventory. Full review is in review.md.

Deployment and real-user app pass remain pending for this draft; no shipped claim. PR #4493.

CI repair (2026-10-05): merged origin/main while #4489 remained open. Moved detailed history retrieval/search/paging and missing-file inventory guidance to the served write_graph.systems handbook chapter, with one short head pointer. Head length is 1,136 characters (127 below the unchanged 1,263 ratchet); both systems-location phrases remain intact. Linux oracle: 27 passed, zero skips across test_converse_turn_cost.py, test_served_systems_guidance.py and test_turn_continuity.py. Ruff, plugin build/import probe and all 602 canonical mirror files pass. Continuity regressions follow the served handbook pointer and retain their content assertions.
