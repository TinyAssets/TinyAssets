---
name: shipping-and-launch
description: Sets up CI quality gates and ships to production safely. Use when configuring build/deploy pipelines or automated checks, or when preparing a launch — pre-launch checklist, feature flags, staged rollout, monitoring, and rollback.
---

# Shipping and Launch

## Overview

Automate the gates that keep broken code out of production, then deploy safely:
every launch reversible, observable, and incremental. **Shift left** (catch
problems as early as possible — a bug in lint costs minutes, in production
hours) and **faster is safer** (smaller, more frequent releases are easier to
debug than big-bang ones).

## CI quality gates

Every change passes these gates before merge, in order, **none skippable** (lint
fails → fix lint, don't disable the rule; test fails → fix code, don't skip the
test):

```
lint → type check → unit tests → build → integration → e2e (optional)
     → security audit (npm audit --audit-level=high) → bundle size
```

Run on every PR and push to main; failures block merge (branch protection, no
force-push to main). Review is NOT per-PR here — ADR-015
scopes it to floor-class changes and gate files. Configure as parallel jobs (lint/typecheck/test
separately) with dependency caching; if the pipeline exceeds ~10 min, optimize —
cache deps, parallelize, path-filter unchanged areas, shard tests — don't skip.

**Feed failures back to the agent:** paste the specific CI error and have the
agent fix and verify locally before pushing (lint → `--fix`; type error → fix at
the cited location; test → find the root cause, don't patch the symptom;
build → check
config/deps). A **Build Cop** keeps CI green: when it breaks, fix or revert
rather than letting breakage accumulate.

**Secrets/env:** `.env.example` committed (template); `.env` never committed; CI
uses separate (non-production) secrets from a manager, never hardcoded; production
secrets only in the deploy platform/vault. Automate dependency updates
(Dependabot/Renovate) and give every PR a preview deployment.

## Pre-launch checklist

- **Code:** all tests pass; build clean (no warnings); lint/types pass; no stray
  `console.log`/debug TODOs; error handling covers expected failures.
- **Security:** no secrets in code; `npm audit` clean of critical/high; input
  validation on user-facing endpoints; authn/authz in place; security headers
  (CSP/HSTS); rate limiting on auth; CORS scoped, not wildcard. (See
  the security checklist in this skill.)
- **Performance:** Core Web Vitals "Good"; no N+1 in critical paths; images
  optimized; bundle within budget; DB indexes; caching configured.
- **Accessibility:** keyboard nav; screen-reader structure; WCAG AA contrast
  (4.5:1); focus management for modals; descriptive field-associated errors; no
  axe/Lighthouse a11y warnings.
- **Infra:** prod env vars set; migrations applied/ready; DNS+SSL; CDN; logging
  and error reporting; health-check endpoint.
- **Docs:** setup/README, API docs, ADRs for decisions, changelog, user docs.

## Feature flags

Decouple deployment from release — deploy code with the flag OFF, enable when
ready. Lifecycle: deploy OFF → enable for team/beta → canary (5%→25%→50%→100%) →
remove flag and dead path. Rules: every flag has an owner and an expiration; clean
up within ~2 weeks of full rollout; don't nest flags; test both states in CI.

## Staged rollout

staging (full suite + smoke test) → production with flag OFF (verify health, no
new errors) → enable for team (24h) → canary 5% → gradual 25/50/100%, monitoring
at each step, able to roll back to a previous percentage → full, monitor a week,
clean up the flag.

**Advance/hold/roll-back thresholds** vs baseline: error rate within 10% (green) /
10–100% (hold) / >2× (roll back); P95 latency within 20% / 20–50% / >50%; new
client JS errors none / <0.1% sessions / >0.1%; business metrics neutral+ /
<5% decline / >5% decline. **Roll back immediately** on >2× error rate, >50%
latency, user-report spike, data-integrity issue, or security vulnerability.

## Monitoring & post-launch

Watch application (error rate total + per-endpoint, p50/p95/p99 latency, request
volume, key business metrics), infrastructure (CPU/memory, DB pool, disk, queue
depth), and client (Core Web Vitals, JS errors, client-side API error rates).
Wire error boundaries / server error middleware to a reporting service; never
expose internals to users. **First hour after launch:** health check 200, no new
error types, latency normal, critical flow works manually, logs flowing, rollback
verified ready.

## Rollback plan (write it before deploying)

Document trigger conditions, steps (disable flag <1 min, or
`git revert <commit> && push` <5 min, or DB migration rollback <15 min), database
considerations (does the migration have a down path? is new-feature data
preserved or cleaned?), and time-to-rollback per mechanism.

## Red Flags

No CI, or CI failures silenced / tests disabled to pass · deploying without a
rollback plan · no monitoring or error reporting · big-bang releases, no staging ·
flags with no owner/expiration · no one watching the first hour · prod config by
memory not code · secrets in code/CI config · "it's Friday, ship it."

## Verification

- [ ] All quality gates present and blocking merge; pipeline <10 min; secrets in a manager
- [ ] Pre-launch checklist green; feature flag configured (if applicable)
- [ ] Rollback plan documented; monitoring dashboards up; team notified
- [ ] After deploy: health 200, error rate + latency normal, critical flow works, logs flowing, rollback ready
