---
severity: P1
title: Hostless monitor schedule cadence gap
filed: '2026-09-19'
summary: five-minute requested cron has multi-hour observed gaps; existing external checks share GitHub scheduling, and host-local watchdogs cannot prove independent coverage
---

# Hostless monitoring has an unbounded observed schedule gap

**Filed / verified:** 2026-09-19 06:36 UTC. **Severity:** P1.
Read-only GitHub metadata and repository inventory; no dispatch, incident,
account, provider or production mutation.

## Evidence, not an inferred root cause

The Uptime canary workflow ID `263326518` is active and requests
`*/5 * * * *`. Commands:

```text
gh api repos/TinyAssets/TinyAssets/actions/workflows/263326518
gh run list --workflow uptime-canary.yml --event schedule --limit 8 --json databaseId,createdAt,event,status,conclusion,headSha,url
```

Latest five natural schedule records, using run `createdAt` (not proof of
runner start time):

| UTC createdAt | Run | Gap from preceding record |
|---|---|---|
| September 19 05:42:42 | [35424683040](https://github.com/Jonnyton/TinyAssets/actions/runs/35424683040) | 4h 39m 35s |
| September 19 01:03:07 | [35411401518](https://github.com/Jonnyton/TinyAssets/actions/runs/35411401518) | 2h 02m 19s |
| September 18 23:00:48 | [35403972718](https://github.com/Jonnyton/TinyAssets/actions/runs/35403972718) | 2h 02m 26s |
| September 18 20:58:22 | [35394309328](https://github.com/Jonnyton/TinyAssets/actions/runs/35394309328) | 2h 39m 46s |
| September 18 18:18:36 | [35379423384](https://github.com/Jonnyton/TinyAssets/actions/runs/35379423384) | — |

No later natural schedule was returned at this read. Post-classification
deploy-completion `workflow_run` receipts exist, but are not natural cron
acceptance. The latest schedule predates PR #3880. Source previously claimed
"Drift tolerance: ±1 min"; that comment was false, not an enforceable guarantee.

[GitHub's official schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
allows delayed and, under sufficient load, dropped scheduled jobs. It does not
promise a one-minute delay bound. This documents the service contract; it does
**not** establish GitHub load, cancellation, actor state or any other specific
cause of these observed gaps. No missing run is treated as measured green or
red. The API record establishes absent observations, not endpoint availability
through the gap.

## Existing primitives and independence boundaries

| Existing primitive | Actual boundary / limitation |
|---|---|
| `uptime-canary.yml` + issue/Pushover sink | External to daemon/developer hosts, but probe and paging execution share GitHub Actions. No run means no fresh observation or escalation decision. Exact prior-red receipts expire after 30 minutes; the observed gaps cannot count as consecutive timely reds. |
| `community-loop-watch.yml` + `community_loop_watch.py` | Requests 15-minute GitHub schedule plus completion events. Can flag an observation older than 90 minutes and dispatch a stale canary. Shares GitHub scheduler; latest natural record at this read is [35413249182](https://github.com/Jonnyton/TinyAssets/actions/runs/35413249182), 01:37:54 UTC. Its generic `workflow_stage` reads whole-run conclusion/freshness, not typed Layer-1 evidence: a fresh classifier-success/unknown run can be a green stage, not proof of green uptime. |
| `dns-canary.yml`, `llm-binding-canary.yml`, `release-reconcile.yml` | Existing external checks/backstop, all GitHub scheduled. Different measurements do not create an independent scheduler. Reconcile also has completion triggers; binding presence is not provider execution. |
| `p0-outage-triage.yml` | Issue-label-triggered GitHub repair; not an independent observer of absent canary ticks. |
| `tinyassets-watchdog.timer` / `daemon-watchdog.timer` | Production-VM systemd checks at 30 seconds / two minutes. Survive developer-host loss, but share the daemon VM failure domain; cannot attest that VM's availability when it is offline. |
| `install_canary_task.ps1` | Developer Windows Task Scheduler checks; explicitly host-fate-sharing, not zero-host proof. |
| `deploy/cloudflare-worker/{wrangler.toml,worker.js}` | Existing external request proxy. Tracked config has no cron trigger; code exposes fetch, not scheduled monitoring. No dashboard configuration was inspected or inferred. |
| Vector / Better Stack runbook | Optional remote log forwarding, not evidence of configured external polling or dead-man alerts. No live account inspected. |

Bounded searches across `deploy`, `scripts`, `.github`, `docs/ops` and
`docs/reference` found no configured independent dead-man/polling service.
That is repository inventory, not proof that no out-of-repository service
exists. No new scheduler, platform LLM actor or user workflow is proposed here.

## Open acceptance / smallest next observation

Retain the natural-cron task and the separate execution-quality/rendered
coverage gaps. Identify whether an already-owned independent monitoring source
has a real cadence receipt before selecting any new service. Any eventual
cadence repair must distinguish missing observations from measured failures,
keep unknown unable to close incidents, and demonstrate alarm delivery without
the developer host or failed target VM. Do not relax the existing 30-minute
red-receipt freshness rule to manufacture an outage threshold across hours of
missing data. Inventory the community-watch conclusion-only limitation before
claiming it supplies semantic uptime coverage.
