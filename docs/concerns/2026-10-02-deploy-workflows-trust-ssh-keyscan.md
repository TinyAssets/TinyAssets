---
severity: P3
title: Deploy and host workflows trust ssh-keyscan instead of a pinned droplet host key
filed: '2026-10-02'
summary: 'Every workflow that SSHes to the production droplet (deploy-prod, install-host-services, restart-daemon, p0-outage-triage, dr-drill, apply-daemon-env, cloud-only-preflight, diagnose-prod-startup; and the coming verify-escrowed-keys) adds the host key with a fresh `ssh-keyscan` on each run. That trusts whatever answers at DO_DROPLET_HOST at that moment, so an active intermediary could impersonate the host and receive the deploy key session, staged secrets or falsified verification results. Pin the droplet host key in a repo variable or secret and use it with StrictHostKeyChecking.'
---

# Deploy and host workflows trust ssh-keyscan instead of a pinned droplet host key

**Filed:** 2026-10-02. Raised by the Codex security refute of the automated host-key escrow, and
accepted there as a residual because it is a repo-wide pattern, not that change's.
**Verified:** `git grep -n "ssh-keyscan" .github/workflows/` on origin/main `118ef3e5f` finds it in
every droplet-facing workflow.

## Fix shape

1. Record the droplet's ed25519 host key once, as a repository variable
   `DO_DROPLET_HOST_KEY`. It is public, so it does not need to be a secret.
2. Add a small shared step, or a script under `scripts/`, that writes `known_hosts` from it. Every
   workflow then uses that step instead of `ssh-keyscan`, with
   `-o StrictHostKeyChecking=yes`.
3. When the droplet is rebuilt or rotated, the key changes. Update the variable in the same change
   that re-points `DO_DROPLET_HOST`. A mismatch then fails closed instead of trusting the new answer.

## How to resolve

Ship it and delete this file once no workflow under `.github/workflows/` calls `ssh-keyscan`.
