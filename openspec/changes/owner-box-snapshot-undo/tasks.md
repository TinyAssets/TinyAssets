## 1. Implementation prerequisites

- [ ] 1.1 Confirm C6 close-out, C1/C2/S27 recovery and N10 authority; verify sparse/full 5 GiB staging and transfer costs against the existing C10 fleet budget.

## 2. Implement after close-out

- [x] 2.1 Expose retained sleep points and explicit owner checkpoint via the existing lifecycle; prove no per-turn/session stops or shared turn pins.
- [x] 2.2 Preserve selected restore source with existing recovery references, publish pre-restore sleep first and retain it in the ordinary seven slots; prove upload failure, restart ordering, headroom refusal and deletion behavior.
- [x] 2.3 Add app Undo through the specified N10 route and broker session recheck to ordinary restore; prove forged box/stale owner refusal, drain/cancel, whole-volume restoration and unchanged platform authority.
- [x] 2.4 Settle waiting app/MCP calls before shutdown; validate restored DBs, invalidate approvals, pause queues/schedules and record the result in-box. Prove reconnect never replays old operations and explicit resumption uses fresh IDs.

## 3. Acceptance

- [ ] 3.1 Run targeted Linux tests including real mount proof and ruff, register `box-snapshot` evidence, complete required floor review and naive-owner app undo after deployment, then sync the delta. This spec PR claims no implementation results; a skip is not a pass.
