---
severity: Watch
title: Uptime/durability upgrades deferred by the slim-until-paying-users rule
filed: '2026-10-02'
summary: 'Founder, 2026-10-02: "keep things slim till we actually have at least some paying users". Four target-architecture S1 upgrades are deferred, each with a known gap accepted on purpose. Three are triggered by the first paying users: escrow wrapping key, R2 provider diversity, standby region + LB. Litestream waits until after command-center-cutover. A pinned host key is tracked separately.'
---

# Uptime/durability upgrades deferred by the slim rule

**Filed:** 2026-10-02. Founder: *"keep things slim till we actually have at least some paying
users"*. Lead decisions the same day applied it to target-architecture S1.

| Deferred upgrade | Trigger | The gap accepted today | Upgrade | Rough cost |
|---|---|---|---|---|
| **Escrow wrapping key** | first paying users | The host-key escrow (`scripts/host_key_escrow.py`) is plaintext in the private off-region bucket. Anyone who can read `escrow/` holds the keys themselves, not just sealed ciphertext: they can open sealed sessions and **forge billing-entitlement and app-ingress HMACs**. The readers are the droplet's per-bucket key (root, which already holds the keys), the DR drill's per-run read key, and DO account admins. | A founder-held wrapping key (age identity offline, recipient on the droplet), so the bucket holds only ciphertext. | $0; founder time |
| **Provider diversity (R2)** | first paying users | Both backup copies (sfo3 and nyc3) and the escrow are on DigitalOcean Spaces. Losing the DO account loses all of them. | A Cloudflare R2 bucket as the off-region store. | ~$1-3/mo |
| **Warm standby + LB** | first paying users | Recovery is restore-from-backup onto a fresh droplet: about 30-60 min, proven by the weekly drill. There is no automatic failover. | target-architecture S1b: a standby droplet in a second region, fenced promotion, and a Cloudflare LB detector. | $12-24/mo + ~$5/mo |
| **Continuous replication (Litestream)** | after command-center-cutover (#4262) | Platform-state RPO is about 1 h (hourly brain tier) and about 24 h for everything else (nightly). | Litestream for `.platform/` only. Its design constraints are in `docs/design-notes/2026-10-02-litestream-platform-state.md`. | ~$0-3/mo |

Related, tracked on its own: `docs/concerns/2026-10-02-deploy-workflows-trust-ssh-keyscan.md`.

## How to resolve

When a row's trigger fires, ship that upgrade or re-defer it with a recorded reason. Delete a row
when its upgrade ships. Delete the file when no rows remain.
