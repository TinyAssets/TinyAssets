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

## Reask lane completion of the history surface
- [x] 3.1 Reconcile merged #4504 recovery and #4500 consent spec sync; add one
  history revisit action for all kinds and all-action dedupe/context assertions.
- [x] 3.2 Add Chromium clear, history revisit, later failure and returning-card proof.
- [x] 3.3 Linux oracle, ruff, mirror, hygiene, Claude review and draft PR push.

The two requested concern paths are already absent from main. The consent spec
already names #4477's protected owner answers and Clear/Deny recovery. This lane
preserves it and records generic recovery in the as-built request-consent-recovery
spec. Draft PR #4527, implementation ed17394362. Final Linux oracle: 234 passed,
zero skips across pending requests, Chromium request UI, owner consent and
converse prompt budgets. Ruff, mirror/import probe and diff checks pass; hygiene
1 added, 0 removed, 0 tampering. Claude APPROVE, no floor/correctness findings;
reviewer independently passed the new browser test. origin/main merged before
final push (already current). Production deploy and real-user proof remain 2.3.

Optional Windows adjacent run: 227 passed, tool-description budget 32353 exceeds
30100 on the local environment; Linux passes the unchanged budget. No threshold
or always-sent prompt changed. The browser proof uses a controlled 401 response
and agent-to-API bridge, not a live external service or an LLM call.

## Reask round 2 browser contract verification

Retained the history tests and account-fence assertions. History permits exactly
Ask again, no approve/answer/grant controls or protected calls, and routes to the
original agent. The lifecycle test proves a fresh deduplicated pending card that
still requires normal consent. No product or prompt-budget change in this round.

Linux oracle (MSYS_NO_PATHCONV=1, --basetemp /tmp/b): final targeted run **28 passed,
zero skips** in test_approval_sheet_real_browser.py,
test_inline_approvals_real_browser.py and test_app_pending_requests_browser.py.
The broad run passed all **344 adjacent cases** in test_app_phone_conversation_browser.py,
test_app_send_resume_browser.py, test_command_center_system_browser.py,
test_app_browser_notifications.py, test_publication_completion.py,
test_pending_requests.py, test_consent_owner_answers.py and test_converse_turn_cost.py.
That first run was 370 passed / 2 failed: the two new sheet relay counts exposed
Playwright invoking the setup assignment's returned function. A void setup
callback fixes the fixture; the exact-one-relay assertion remains. Final targeted
execution covers every changed test file. No skipped or xfailed cases.

Ruff, plugin mirror/import probe and diff checks pass. Claude APPROVE, AGREE;
see review-response.md. PR #4527 is draft; origin/main merged. Production deploy
assertion and live real-user agent pass remain post-merge work.
