# ANSROUTE verification

Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4532

## Regression evidence

Ran the new `test_answerable_request_kinds_capture_the_actual_asking_agent` in an isolated archive of the pre-fix commit (`eec9f02037^`), outside the repo. All four kinds failed: stored `agent` was `main` instead of the actual sub-agent. The identical four tests pass on the fix.

New tests exercise authenticated owner HTTP answers, main/sub-agent routing, foreign owner/binding refusal before mutation, removed-agent fallback, item answers, notification replies, workflow/native provenance, retry isolation/backoff, live steering identity, and Chromium card/phone replies. The browser imports Playwright inside the test. Existing client assertions now forbid selected-chat relays and assert exact owner-answer payloads; no tests were removed, skipped or xfailed.

## Cross-family review

Claude review of `eec9f02037`, through `peer-agents`, completed successfully in 237 seconds. Verdict: **ADAPT**. Reviewer ran the new test file: 18 passed, zero skipped. One round, no delegated agents.

1. **AGREE**: ambiguous notification ownership must not revoke admitted workflow execution. The provenance wrapper now yields without attribution when notification ownership is ambiguous; answer routing remains fenced.
2. **AGREE**: failed answer computation needs bounded exponential backoff. Added persistent attempt count, matching the existing 60-second to 3600-second retry schedule.
3. **AGREE**: one failing delivery must not starve later generic or protected wakes. Both loops retain and log individual failures and continue. Added a two-answer failure-isolation regression.
4. **AGREE**: provenance must preserve live steering/approval turn identity. Foreground launches retain the live turn ID; launch records key on session plus turn. Requestless launches use a server-minted ID. Added a multi-node live-turn regression.

## Checks

- Initial Windows batches: 183, 181 and 50 tests passed; subsequent review-fix batches also passed.
- First full Linux oracle: 708 passed, zero skips, two failures. Fixed missing protected-preview detail without changing its contract; browser scroll proof now waits for the same exact position condition before asserting it.
- Changed-file ruff passed. Plugin mirror build/import and commit parity passed.
- Final hygiene after merge: 14 added, 0 removed, 0 tampering findings.
- Static prompt-budget tests passed without increasing any budget. Duplicated guidance moved to the on-demand handbook.
- Expanded Linux run: 917 passed, zero skips, one old exception-propagation assertion failed. Updated that test for the reviewed per-row isolation contract: assert the exact logged error, zero acknowledgments, retained unprocessed row, attempt count and exact retry deadline; preserve every existing backoff and fencing assertion. The focused Windows batch then passed all 57 tests. Final Linux results follow below.

Linux commands use `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <files> --basetemp /tmp/b`.
The full affected set is: `test_request_answer_routing`, `test_pending_requests`,
`test_agent_notifications`, `test_request_items_and_delivery`, `test_onboarding_app`,
`test_app_request_rail_executes`, `test_app_pending_requests_browser`,
`test_app_native_push`, `test_app_browser_notifications`,
`test_connection_sheet_continuations`, `test_background_work_agent`,
`test_work_agent_authority`, `test_work_agent_allowance`, `test_inline_approvals`,
`test_consent_owner_answers`, `test_owner_stores`, `test_converse_turn_cost`,
`test_branch_authoring_actions`, `test_owner_steering`, `test_storage_accounting`,
`test_converse_addressed_agent`, `test_authenticated_external_call_effector`,
`test_request_card_layout_and_links`, `test_approval_sheet_real_browser`,
`test_inline_approvals_real_browser`, and `test_mcp_instruction_surfaces`
(each under `tests/`, with `.py`). This includes the affected heavy files.

Merged `origin/main` at `fb22e770bd74333f54786233ba6046ccfb0b03c2`, including
request-history/reask UI changes. Rebuilt the mirror and launched a separate
post-merge Linux batch covering the six overlapping request/browser test files.

## Scope and limitations

No new on-disk store: delivery, workflow and launch tables share the existing accounted protected request/activity database. The new writer is registered in `owner_stores`.

Legacy requests retain their already-recorded agent; missing historical attribution cannot be safely guessed. A legacy row with no recorded asker is stamped only for the sole admin or the asking binding's creator, and only after the fence passes; in a multi-admin universe an unrecorded `main` ask therefore cannot be answered by anyone (an explained `unrecorded_asker_ambiguous` refusal) rather than guessed, and any admin may dismiss it without delivering anything. A late phone reply uses its request ID even after the card disappears, never the current composer. Recovery can retry computation after a worker crash, as the existing continuation system does; external effects remain under their existing consent/effector receipts.

The requested deliverable is a draft PR, not production deployment. No deployed-SHA or production real-user-pass claim is made.
