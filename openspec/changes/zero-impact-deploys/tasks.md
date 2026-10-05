## 1. Contract and durable boundaries

Design-only lane: these are future implementation tasks, not completed work. All belong to the single deploy-continuity intent; each slice stays disabled until its dependencies and release gate pass.

- [ ] 1.1 Write delta specs for deploy-continuity, uptime-and-alarms and live-mcp-connector-surface; inventory every work origin and integrate #4481/the continuity lane's contracts. Verify no entry point or conflicting lane is unaccounted for.
- [ ] 1.2 Add owner-scoped durable ingress identity, acceptance and reply outbox/cursors using existing admission storage. Verify commit/ack loss, exact payload preservation, duplicate/conflicting keys, revocation, retention and cross-user guards.
- [ ] 1.3 Implement turn checkpoint manifests and effect-intent resume rules with existing owner leases/run locks and durable workspace continuity. Verify one executor, exact admitted version, preserved budgets and no replay of unknown effects; non-resumable work drains.

## 2. Serving and handoff

- [ ] 2.1 Introduce stable local gateway and read-only blue/green warm-up on distinct ports; measure the single-host overlap budget. Prove bootstrap, ingress/tunnel replacement and capacity deferral without a listener gap or paid infrastructure.
- [ ] 2.2 Implement persisted release phases, admission fencing, drain/checkpoint proof and owner transfer before cutover. Verify late admissions, long native work, controller crash/cancellation and unsafe-transfer deferral without SIGTERM loss.
- [ ] 2.3 Connect schedules, webhook/event delivery and all background workers to durable work identities and fenced recovery. Verify due/claimed/queued/running work survives each transition without duplicates or skipped occurrences.
- [ ] 2.4 Preserve MCP sessions/results through gateway handoff with protocol-aware replay and authenticated operation identity. Verify legacy session drain, reconnect, cancellation and uncertain non-idempotent calls against supported transports.
- [ ] 2.5 Add app version/protocol handshake, automatic receipt reattachment and independent boot recovery with durable drafts/uploads/cursors. Verify open, sleeping and broken pages recover without manual reload/resend or cross-account restoration.

## 3. Release proof

- [ ] 3.1 Route deploy, rollback, reconciliation and host recovery through the same controller with schema compatibility, resource/queue preflight and durable receipts. Verify failed candidate and reverse handoff preserve newly accepted work.
- [ ] 3.2 Add the required Linux deploy-during-traffic PR gate described in design.md, including synthetic turn + concurrent send + open browser, scheduled/background/MCP traffic and injected rollback/ingress faults. Require zero deploy-induced failures and bounded recovery evidence before implementation merge.
- [ ] 3.3 Run affected tests including heavy files, ruff and Linux oracle; complete one cross-family floor/correctness review. Deploy compatible slices, assert deployed SHA and public handles, prove one real-user app pass across deploy, sync specs and resolve existing outage concerns only with recorded evidence.
