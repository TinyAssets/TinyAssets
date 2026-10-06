## 1. Implementation
- [x] 1.1 Generalize cleared ask recovery and regression coverage.
- [x] 1.2 Fail closed on unclassified actions and test validator coverage.
- [x] 1.3 Test generic OAuth replay and foreign handles.
- [x] 1.4 Cancel owner connection flows and test isolation.
- [x] 1.5 Test install safety digest binding.
## 2. Verification and delivery
- [x] 2.1 Linux affected tests, ruff, mirror, prompt budgets and hygiene.
- [x] 2.2 Claude cross-family review, merge origin/main, draft PR and push.
- [ ] 2.3 After merge: assert deployed SHA, real-user app pass and spec sync.

Verification: Linux oracle 471 passed across 12 affected/adjacent files;
changed-file ruff and plugin mirror parity passed. Test hygiene: 8 added,
0 removed, 0 tampering. Draft PR #4504; first verified slice 6b622b0825.

Claude verdict ADAPT; AGREE and corrected system-created action classification
(review-response.md). Follow-up Linux oracle: 305 passed across pending requests,
proposals, consent owner answers, notifications, inline approvals and prompt budgets.
Local correction check: 94 passed. Ruff and regenerated mirror passed again.
Origin/main was current when merged before the final push. Deployment and live
owner proof are deliberately pending: this lane delivers a draft PR only.
