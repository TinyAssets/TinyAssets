---
severity: P1
title: Review follow-ups batch 2026-09-24
filed: '2026-09-24'
summary: 'docs-only merges restart prod and kill turns; plus non-floor follow-ups from #3954/3956/3958/3960/3963/3964/3967/3968'
---

# Review follow-ups batch (2026-09-24)

**Filed:** 2026-09-24. Non-floor findings from the Tier 1/2 reviews of that
day. Each shipped with its floor clear, and these were tracked rather than
fixed in-PR under the tiered review policy.
**Severity:** P2 unless marked.

- **P1: documentation-only merges restart production.** Deploy `b7a63737`
  (PLAN.md only, #3970) recreated the daemon. Every docs merge can kill
  in-flight turns (see the deploy-kills-in-flight-turns note). Deploy should skip,
  or the image be reused, when no runtime input changed.
- **#3954 agent access:** assert the specific refusal in the agent-surface
  cross-user test; `_section` returns `str(exc)` (use the exception type only);
  backfill `origin=platform` on pending bootstrap asks.
- **#3956 connectors:** the declared-list fallback read
  (discovery_snapshot.py:124) should also refuse cost-capped access.
- **#3958 jail:** non-Linux hosts (tray, local daemons, plugin runtime) now
  refuse universe provider launches; the stdio engine-MCP fallback can't
  start inside the jail; codex app-server discovery and the auth probe run
  outside the jail.
- **#3960 retention:** persist PREV_IMAGE or fill the receipt
  `rollback_target` (the manual-rollback window recovers only via re-pull);
  rotate the verify window past 8 unverifiable images and alert on repeated
  `retention_incomplete`.
- **#3963 codec:** literal `"null"` arguments still default to `{}`.
- **#3964 setup:** a permanently failing folded `bind_model_access` request
  hides the guided path, and "Finish connecting" loops with no decline.
- **#3967 GitHub:** the owner's direct connector `connect_http` can set
  `git_host` without a git scope (owner-authored; the floor holds).
- **#3968 OAuth:** the RFC 9207 `iss` check is an exact-string compare (a
  trailing-slash issuer is falsely refused); the agent-supplied public
  `client_id` isn't named in consent.

Delete a line when fixed; delete the file when empty.
