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
- First hygiene pass: 10 added, 0 removed, 0 tampering findings.
- Static prompt-budget tests passed without increasing any budget. Duplicated guidance moved to the on-demand handbook.
- Final Linux rerun and final hygiene results will be recorded before delivery.

## Scope and limitations

No new on-disk store: delivery, workflow and launch tables share the existing accounted protected request/activity database. The new writer is registered in `owner_stores`.

Legacy requests retain their already-recorded agent; missing historical attribution cannot be safely guessed. A late phone reply uses its request ID even after the card disappears, never the current composer. Recovery can retry computation after a worker crash, as the existing continuation system does; external effects remain under their existing consent/effector receipts.

The requested deliverable is a draft PR, not production deployment. No deployed-SHA or production real-user-pass claim is made.
