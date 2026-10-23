## 1. Converge the existing path

Implementation checklist; intentionally unchecked in this spec-only lane.

- [ ] 1.1 Confirm C7/C8's durable session-start idempotency handoff and map C2's folded webhook management to box-local connector configuration; no second pump.
- [ ] 1.2 Extend the existing owner binding relay and broker with declarative verification and revision revocation; prove signature/token failure and T5 admission races without owner-store access.
- [ ] 1.3 Wire the single ingress route to verification and existing wake/forward transport; prove sleeping-box timeout/retry and per-box router authentication without changing T7.
- [ ] 1.4 Replace receiver dispatch with an atomic in-box inbox/outbox and receipt-keyed `session.start`; prove concurrent replay and crashes at acceptance/start/ack boundaries.
- [ ] 1.5 Supply Slack, Teams, WhatsApp, connected-account email and generic webhook packages/profiles with protocol fixtures; prove parsing, challenges, grants, limits and proxy-only replies without raw credentials.
- [ ] 1.6 Require generic sender ID/timestamp headers, prune receipts by freshness, convert existing bindings/receipts and drain legacy pending deliveries; delete the direct branch/source bridge, payload-hash dedupe and duplicate authentication named in the design in the same cutover.

## 2. Prove and finish the implementation

- [ ] 2.1 Create `tests/test_inbound_door.py` with the exact route, real session-start and verification-outage assertions in the design; add the `inbound-door` route/dispatch guard and targeted router, webhook management and automation-event regressions; run `python scripts/linux_oracle.py -- tests/test_inbound_door.py tests/test_owner_router.py tests/test_automations.py tests/test_owner_waker.py tests/test_owner_next_wake.py` and ruff on changed Python.
- [ ] 2.2 In the implementation lane, complete the required floor-class review of shared mapping/verification against T1-T10 and resolve correctness findings by placement.
- [ ] 2.3 In the authorized deployment lane, verify deployed SHA and one naive-user channel message/reply with devices off; exercise disconnect and retry, run the public handles canary, and preserve fail-closed rollback.
- [ ] 2.4 Sync N1 and the refined C2 inbound delivery requirement together, preserving C2's unrelated wake/timezone obligations; mark registry N1 converged only after its guard and live proof pass.
