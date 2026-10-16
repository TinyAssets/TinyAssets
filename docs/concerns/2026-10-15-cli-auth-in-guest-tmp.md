---
severity: P2
title: 'Owner-box CLI auth sits in the guest /tmp for the run'
filed: '2026-10-15'
summary: accepted ADR-012 D5 residual until P3 moves CLI auth to the broker's in-box endpoint
---

# Owner-box CLI auth sits in the guest /tmp for the run

`tinyassets/external_agents/cell.py` writes a leased external-agent CLI's auth
files under `/tmp/ta-agent-<nonce>` inside the owner's own box for the length
of the run. A prompt-injected CLI that can run commands can read them and send
them out through the box's outbound proxy. The exposure is the owner's own
credential, inside the owner's own box, for one run; no other owner is reached.

Accepted by the founder (2026-10-15, ADR-012 D5) for the C6 flip. Resolve by
P3: the broker serves CLI auth through its in-box endpoint, so no credential
bytes land in the guest. Delete this file when P3 lands.
