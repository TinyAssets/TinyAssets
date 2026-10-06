---
severity: correctness
title: Parent broker disconnect test lacks protected owner consent context
filed: '2026-10-05'
summary: The Linux broker disconnect test expects incarnation refusal but reaches the browser-consent gate first; its owner-session setup needs reconciliation.
---

Found during L11 verification on `feat/agent-box-git-credentials`, stacked on
`feat/per-role-uid-split`. `tests/test_broker_disconnect.py` is unchanged from
the parent. Neither it nor `tinyassets/api/pending_requests.py` was modified
by the L11 implementation.

`test_pending_remove_captures_broker_incarnation_and_rejects_replacement`
binds an ordinary `Identity`, then answers an owner approval directly. The
result is `interactive_approval_required`, rather than the expected
`connection_changed`. The current browser-only consent gate refuses the test
before its incarnation check. Do not bypass that gate or weaken the assertion;
repair the test's protected owner-session setup in the owning lane.

Linux oracle receipt: 224 passed, 1 failed, zero skips across git credentials,
broker server/scanner/upstream/disconnect/fence, egress, D72 git bridge/role,
and unchanged converse-turn budgets. The failure is the test above.
