---
severity: P0
title: Public-site privacy, deps, and CI secret exposure
filed: '2026-07-27'
summary: a same-repo PR can request 19 secrets
---

# P0 - Public-site privacy, dependencies, and CI secret exposure

**Filed:** 2026-07-27 | **Verified:** 2026-07-27 | **Re-verified:** 2026-11-14 | **Severity:** P0

> Migrated verbatim from `STATUS.md` on 2026-08-25 when the board was retired.
> Source dates preserved. Premise re-verified against `origin/main` @ `8cbf9769`.

## Source (verbatim)

Public-site privacy/deps/CI: private/operator reads; React 1C/1H, Svelte 7H, design 2H; same-repo
PRs can request 19 secrets.

## Reading

Three distinct problems filed as one row:

1. **Privacy** - the public site performs private/operator reads.
2. **Dependencies** - React 1 critical / 1 high, Svelte 7 high, design 2 high.
3. **CI** - a same-repo pull request can request **19 secrets**. This is the sharpest of the three:
   any contributor able to open a same-repo PR reaches the secret set.

## Re-verification (2026-11-14)

1. **Privacy: fixed.** `WebSite/site-react/lib/live.ts` makes no request from the public
   browser, held by `scripts/public-boundary.test.mjs`.
2. **Dependencies: fixed.** The Svelte tree is gone. `site-react` production deps audit clean
   (`next` 15.5.28), and `.github/workflows/site-audit.yml` keeps them so
   (`docs/decisions/untangle-2026-11/site-deps.md`). The design system has none in production deps; its
   dev tools (`storybook`) still have some and ship nothing.
3. **CI: open.** This file stays for item 3.

## Note on scope

Item 3 overlaps the merge-attribution and `pr-scope-guard` work. `scripts/drain_review_gate.py`
(kept in the 2026-08-25 harness reset) enforces exact-head review receipts on sensitive-file PRs
including `.github/workflows/`, which narrows but does not close the secret-request surface.
