---
severity: P2
title: Pending-request browser proof loses an older scroll position
filed: '2026-10-04'
summary: Two pending-request browser cases fail the scroll-preservation assertion in the full Linux required suite.
---

Found while repairing PR #4475 on base `37284a2479`. The six full required
shards ran through `scripts/linux_oracle.py --required-runner` on Python
3.11.16, with the unchanged CI heavy-file exclusions and `/tmp/b` basetemp.
Shard 3 failed these cases in `tests/test_app_pending_requests_browser.py`:

- `test_pending_requests_at_latest_and_new_arrival_answer[False-1280]`
- `test_pending_requests_at_latest_and_new_arrival_answer[True-390]`

At line 96, the test sets `#thread.scrollTop` to 200, renders a new request,
and observes 10542 or 10221 instead of 200. The required-browser-proof check
also rejects these failures. The test and app code are unchanged by the CI
repair; this observation alone does not establish whether they fail on main.

One isolated Linux oracle diagnostic run of the whole browser file plus the
served-router failing case passed all five cases in 10 seconds. That does not
erase the full-shard failures; the scroll failure is intermittent or sensitive
to the surrounding run. No retry was substituted into the required results.

Relevant mechanism: `followChatLatest` in `tinyassets/onboarding/app.html`
keeps a `following` flag and schedules animation-frame scrolling. A direct
test assignment to `scrollTop` does not deliver the wheel/pointer/key gesture
that clears that flag. Diagnose the interaction between the pending frame,
request rendering and the test's simulated scroll; retain the actual-user
scroll-preservation contract and do not quarantine or weaken the assertion.
