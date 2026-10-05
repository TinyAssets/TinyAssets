---
severity: P2
title: Reconnecting LinkedIn falls back to pasting a token
filed: '2026-10-04'
summary: the agent asked for a 'Sign in with LinkedIn' connect request, but the platform turned it into a paste box, because LinkedIn has no registered OAuth client and no discovery/DCR
---

# Reconnecting LinkedIn falls back to pasting a token

LinkedIn returned REVOKED_ACCESS_TOKEN (run 8f24ac10595b43af). The agent tried to raise a one-tap Sign in with LinkedIn reconnect, but the platform turned it into a paste-a-token box. Generic OAuth can't reach LinkedIn without a registered client (audit lane L5: OAuth providers as data, plus a founder host action to register the app).

**Source:** the founder's command-center agent re-tested its known issues on 2026-10-05 at 00:27Z (5:27 PM PDT), with live run IDs in its notes/command-center-playbook.md. Main at that time was 51db6894f3 (#4458 not yet merged). Re-verify against current main before fixing.
