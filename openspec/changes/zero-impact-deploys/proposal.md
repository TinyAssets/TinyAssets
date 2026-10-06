## Why

Founder directive, 2026-10-05: "that is a gap that our updates make things drop ... it should be wider to ensure deploys don't cause user issues is built into the platform."

A deploy at 06:01Z interrupted a turn and left an open app broken until a hard reload ([#4479](https://github.com/TinyAssets/TinyAssets/issues/4479)); merged [#4497](https://github.com/TinyAssets/TinyAssets/pull/4497) defines page recovery in `openspec/specs/command-center-recovery`. The founder also reports a send failing with HTTP 520 during the 07:31Z deploy of `29fa5b4686`. These incident observations are supplied evidence, not a new production reproduction. The existing deploy wait reduces interruption probability; it cannot guarantee continuity.

One intent: make deployment a transparent platform operation for accepted work, requests, replies, and open clients.

## What Changes

Define a release contract with durable ingress, stable request identities, per-command-center idle-instant handoff under target-architecture D11/S8a, resumable replies, automatic page recovery, and a mandatory deploy-during-traffic gate. Use temporary blue/green runtime overlap behind stable local ingress on the existing host. Busy centers finish on old owners while idle centers move; memory/compatibility failures defer candidate startup; there is no timeout permission to kill user work.

This PR is DESIGN ONLY on `spec/zero-impact-deploys` in `wf-deploysafe`. It changes no runtime, pipeline, public API, or as-built spec. The proposal, design, tasks and proposed delta specs are the deliverable; all implementation tasks remain unchecked. No claim of shipped behavior is made.

## Capabilities

### New Capabilities

- `deploy-continuity`: durable acceptance, delivery, and release transition contract across all platform surfaces (proposed).

### Modified Capabilities

- `uptime-and-alarms`: replace bounded best-effort turn waiting with proved safe handoff and continuous traffic verification.
- `live-mcp-connector-surface`: retry-safe request/result identity and transport continuity without changing the public endpoint.

Proposed delta specs are included for strict validation; sync into as-built specs only after implementation and deployed proof.

## Impact

Affected future surfaces: `deploy/`, `.github/workflows/deploy-prod.yml`, `.github/workflows/release-reconcile.yml`, request admission, execution ownership, turn journal, effect intents, schedules/background queues, MCP transport, and the app shell. Exact current behavior and proposed changes are in `design.md`.

Constraints: single host; no paid standby or load balancer until paying users; no founder desktop infrastructure; vendor-neutral execution; no platform LLM. The guarantee covers deployment-induced failures, including rollback and ingress updates. It cannot promise availability through destruction of the only host or independent upstream outages.

Related work: folded into `target-architecture/design.md` D11/S8 (S8a `execution-owner-lease`, S8b `control-plane-scheduler`) and PLAN.md:875; exact section citations are in design D3. Consume merged #4497 recovery and #4490 `client_send_id` reconciliation with no resend offer while delivery is uncertain. Live checkpoints are not transferred; stragglers finish on their old owner. Schema-changing cutovers remain declared maintenance windows outside the compatible zero-impact guarantee. Existing `docs/concerns/2026-10-01-deploys-are-not-zero-downtime.md` already owns the verified outage and interrupted-turn defects; no duplicate concern is added. A separate source-proven scheduler loss defect is recorded in `docs/concerns/2026-10-05-scheduler-delivery-mark-precedes-run-admission.md`.
