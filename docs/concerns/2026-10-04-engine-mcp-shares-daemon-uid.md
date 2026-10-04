---
severity: P1
title: Engine MCP children share the daemon UID and can recover its secrets
filed: '2026-10-04'
summary: Engine MCP children launch with the daemon's UID and PID namespace. Filtering their inherited environment does not prevent a compromised engine from recovering daemon credentials through procfs.
---

# Engine MCP children share the daemon UID

**Filed:** 2026-10-04.
**Verified:** 2026-10-04, source inspection at PR #4267 head `aaccf424`;
Linux credential recovery was not exercised by the independent review.
**Severity:** P1, pre-existing credential-recovery path.

## Source (verbatim)

> **DISAGREE_CONCERN — P1: filtering the child environment does not establish isolation from daemon secrets.** The engine launches directly without a UID or PID-namespace boundary (`aaccf424:tinyassets/engine_mcp_http.py:281`). The container runs as `tinyassets`, including `tini` (`aaccf424:Dockerfile:363`). Consequently, an engine compromise appears able to recover Stripe/WorkOS credentials through same-UID `/proc/1/environ` or the daemon’s environ. This is pre-existing; Linux access was not exercised here. Document this engine-specific residual explicitly and avoid claiming the split prevents credential recovery following engine compromise.

## Re-verification

2026-10-04: `_EngineServer.start()` still calls `subprocess.Popen` directly with
an explicit filtered environment, without changing UID or entering a separate
PID namespace. The Dockerfile runs the container as `tinyassets`, including
`tini`. Compose supplies the daemon's billing/account keys, signing keys and
other daemon credentials in `Config.Env`; PID 1 receives that environment too.
The normal same-UID procfs access rules therefore leave `/proc/1/environ` and
the daemon's exec-time environment available to an engine compromise. Popping
a variable in Python does not erase PID 1's copy or its exec-time environment.
This is a source-based threat assessment, not a production exploit measurement.

The PR removes unnecessary **inheritance** and reduces accidental child-env
disclosure. It does not prevent credential recovery after engine compromise.
The three deliberately retained engine credentials (identity fingerprints and
owner push) are listed in `docs/reference/environment-variables.md`.

## Resolution required

Establish an enforced process boundary that denies engine access to daemon and
PID 1 environments and other daemon secret stores. The existing
`openspec/changes/per-role-uid-split/` lane tracks the broader role separation;
its Linux proof must explicitly include the HTTP engine launch path. Verify
denial as the real engine UID/namespace with names-only fixtures, then verify
the deployed image and real-user engine path before deleting this concern.
No isolation code change or production remediation is claimed by PR #4267.
