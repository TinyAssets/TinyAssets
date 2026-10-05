## Why

Founder directive, 2026-10-05: "that is a gap that our updates make things drop ... it should be wider to ensure deploys don't cause user issues is built into the platform."

A deploy at 06:01Z interrupted a turn and left an open app broken until a hard reload ([#4479](https://github.com/TinyAssets/TinyAssets/issues/4479)); [#4481](https://github.com/TinyAssets/TinyAssets/pull/4481) addresses page recovery. The founder also reports a send failing with HTTP 520 during the 07:31Z deploy of `29fa5b4686`. These incident observations are supplied evidence, not a new production reproduction. The existing deploy wait reduces interruption probability; it cannot guarantee continuity.

One intent: make deployment a transparent platform operation for accepted work, requests, replies, and open clients.

## What Changes

Define a release contract with durable ingress, stable request identities, fenced execution handoff, resumable replies, automatic page recovery, and a mandatory deploy-during-traffic gate. Use temporary blue/green runtime overlap behind stable local ingress on the existing host. Unsafe cutovers defer automatically; there is no timeout permission to kill user work.

This PR is DESIGN ONLY on `spec/zero-impact-deploys` in `wf-deploysafe`. It changes no runtime, pipeline, public API, or as-built spec. The three planning documents plus OpenSpec metadata are the deliverable; all implementation tasks remain unchecked. No claim of shipped behavior is made.

## Capabilities

### New Capabilities

- `deploy-continuity`: durable acceptance, delivery, and release transition contract across all platform surfaces (proposed).

### Modified Capabilities

- `uptime-and-alarms`: replace bounded best-effort turn waiting with proved safe handoff and continuous traffic verification.
- `live-mcp-connector-surface`: retry-safe request/result identity and transport continuity without changing the public endpoint.

Implementation must create the corresponding delta specs before apply and sync them after deployed proof. Delta files are deliberately deferred to honor this lane's requested proposal/design/tasks-only scope.

## Impact

Affected future surfaces: `deploy/`, `.github/workflows/deploy-prod.yml`, `.github/workflows/release-reconcile.yml`, request admission, execution ownership, turn journal, effect intents, schedules/background queues, MCP transport, and the app shell. Exact current behavior and proposed changes are in `design.md`.

Constraints: single host; no paid standby or load balancer until paying users; no founder desktop infrastructure; vendor-neutral execution; no platform LLM. The guarantee covers deployment-induced failures, including rollback and ingress updates. It cannot promise availability through destruction of the only host or independent upstream outages.

Related work: consume the continuity/workspace persistence contract from `fix/turn-continuity-and-workspace-persistence`, and page recovery from #4481; do not duplicate their implementation. The named continuity branch was not advertised by `git ls-remote --heads origin` during this review, so its implementation is not assumed. Existing `docs/concerns/2026-10-01-deploys-are-not-zero-downtime.md` already owns the verified outage and interrupted-turn defects; no duplicate concern is added.
