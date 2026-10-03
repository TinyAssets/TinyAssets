---
severity: P2
title: Deploy restarts cost ~3.7 min/day of origin downtime that the 5-minute canary cannot see
filed: '2026-10-02'
summary: 81 daemon restarts in 4 days (p50 8 s, max 191 s, 14.8 min total, ~99.74%) while the canary showed 296/300 green; the uptime record overstates availability
---

# Deploy restarts cost ~3.7 min/day of origin downtime that the 5-minute canary cannot see

**Filed:** 2026-10-02
**Verified:** 2026-10-02 from production logs (read-only) and `gh run list`.
**Severity:** P2. This is an availability loss plus a measurement blind spot.
The loss of in-flight turns is tracked separately in
the resolved 2026-08-29 deploy-kills-turns concern.

## Source (verbatim)

From the staged-architecture review (2026-10-02):

> In 4 days there were 81 daemon restarts, with a median gap of 8 s, a worst
> case of 191 s, and 14.8 min of origin-down time in total. That is ~3.7
> min/day, ≈99.74% availability before any hardware fault. The 5-minute canary
> samples straight past these gaps (296/300 runs green).

## Evidence

- `journalctl CONTAINER_NAME=tinyassets-logs --since "4 days ago"`, pairing
  daemon `Shutting down` with the next `Application startup complete`: n=81,
  min 6 s, p50 8 s, p90 9 s, max 191 s, sum 14.8 min.
- The same window contains 379 cloudflared errors
  `dial tcp [::1]:8001: connect: connection refused` (2026-09-28).
- `deploy-prod.yml` runs per day: 09-28 7, 09-29 14, 09-30 45, 10-01 33.
- `uptime-canary.yml` (cron `*/5`): 296 success and 4 cancelled in the last
  300 runs.
- The shutdown-to-startup interval may include drain time, so 14.8 min is an
  upper bound on unavailability. Four busy days also do not establish a
  long-run rate (Codex refute, 2026-10-02).

## What would resolve it

- **Measure:** derive availability from the origin's own restart gaps, or from
  a sub-minute probe, rather than 5-minute samples.
- **Shrink:**
  - batch deploys;
  - cut startup time (most of the 8 s);
  - at the Worker, retry only requests that are safe to replay (reads, or
    requests with an idempotency key the daemon deduplicates). Return a typed
    "restarting" response for everything else. A Worker-side fetch failure does
    not prove the daemon never got the request, so blind replay is unsafe.
