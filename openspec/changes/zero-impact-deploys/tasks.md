## 1. Baseline before implementation

Implementation lane: bounded L7 component slices are recorded in `l7-evidence.md`
and `l7-ingress-slice.md`. Full tasks remain incomplete. Consume S4/S8a/S8b; no
competing lease implementation. No production ingress slice ships before task
1.1 exists and its candidate traffic gate passes.

- [ ] 1.1 FIRST build the required Linux deploy-during-traffic PR harness from design.md. Record a red baseline reproducing today's 520 and cut-off turn on wait/recreate, with digests, HTTP/turn/browser/effect evidence. Run the same oracle for every slice; enforce it before anything touching production ingress ships.
- [ ] 1.2 Implement the proposed delta specs consuming target-architecture D11/S8a/S8b and PLAN.md:875; inventory work origins and prerequisites. Integrate merged #4497 recovery and #4490 client_send_id without duplicating either contract.

## 2. Durable boundaries and per-key ownership

- [ ] 2.1 Implement principal-scoped acceptance and reply journal/cursors using client_send_id for app ingress and idempotent admission import. Verify ack/import loss, exact payloads, conflicting keys, revocation, retention and cross-user guards.
- [ ] 2.2 Consume S4/S8a idle fencing, per-key leases and acknowledged broker/boxhostd barriers before any second writer. Verify racing starts, reconciliation only after acquisition, and generation check in the same BEGIN IMMEDIATE transaction as sent; reject delayed stale dispatch after the barrier.
- [ ] 2.3 Introduce shared-journal stateless frontends and read-only candidate warming. Measure/enforce summed cgroup caps, gateway/tunnel/OS reserves, OOM priorities and one shared provider_admission cap. Prove bootstrap/replacement without a listener gap, capacity deferral and refusal of a third generation.
- [ ] 2.4 Implement persisted per-key handover and rollback: idle centers move immediately, busy keys remain admitted to old owners, stragglers finish there. Verify independent center progress, retained workspace/budgets, old retirement, lingering alarms and controller crash recovery without global holds or live checkpoint transfer.
- [ ] 2.5 Integrate schedules, webhooks and background work with S8b's platform lease and recoverable claims/outbox. Verify due/claimed/queued/running work survives transitions without duplicates or skipped occurrences.
- [ ] 2.6 Preserve MCP bindings/results in the shared journal and old frontend streams until completion. Measure supported Claude.ai/ChatGPT call and idle timeouts; prove reconnect, cancellation, uncertain mutations and handover latency within those limits.
- [ ] 2.7 Extend #4497 remount-then-reload recovery with its two-page ceiling and private drafts; reattach #4490 sends by client_send_id. Verify uncertain delivery never offers resend, plus sleeping/broken pages and account switches.

## 3. Release proof

- [ ] 3.1 Route compatible deploy/reconcile/rollback through the controller with durable receipts and resource preflight. Enforce D11/PLAN declared schema-maintenance exclusion across frontends, owners, boxhostd, Litestream and backups; never present maintenance/force as zero-impact.
- [ ] 3.2 Require red-to-green traffic evidence for deploy, rollback, ingress replacement, memory/admission pressure, independent key moves and straggler completion. Run affected/heavy tests, ruff and Linux oracle; complete required cross-family floor/correctness review. After authorized rollout assert deployed SHA and public handles, prove a rendered real-user app pass across deploy, sync specs and resolve concerns only with evidence.
