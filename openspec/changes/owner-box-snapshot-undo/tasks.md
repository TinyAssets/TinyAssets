## 1. Implementation prerequisites

- [ ] 1.1 Confirm C6 registry close-out and C1/C2/S27 single-owner recovery; measure sparse/full 5 GiB checkpoint latency and peak storage in the disposable Linux venue and establish budget fit before implementation.

## 2. Implement after close-out

- [ ] 2.1 Add the in-box queued-turn checkpoint handoff using the existing lifecycle intent/call; prove no early execution, lost turn, duplicate resume or repeated checkpoint loop across restart and concurrent wake.
- [ ] 2.2 Use existing catalog/pin operations for the latest pre-turn point and seven-snapshot retention; prove recovery pins, upload failure, exhaustion and deletion behavior without a second snapshot path.
- [ ] 2.3 Add in-box app Undo and agent-request/owner-confirm flow through ordinary restore; prove stale confirmation refusal, running-turn drain, whole-volume restoration and unchanged platform authority.
- [ ] 2.4 Handle restored generations inside the box: validate databases, pause restored queues/schedules, invalidate pending approvals and record the result; prove old operations refuse and explicit owner resume uses fresh IDs.

## 3. Acceptance

- [ ] 3.1 Run the design's targeted Linux tests including the real mount proof and ruff, register `box-snapshot` evidence, complete required floor review and naive-owner app undo after deployment, then sync the delta. A skip is not a pass; this spec PR claims none of those implementation results.
