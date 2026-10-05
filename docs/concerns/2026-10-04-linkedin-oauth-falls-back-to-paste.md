---
severity: P2
title: Reconnecting LinkedIn falls back to pasting a token
filed: '2026-10-04'
summary: the agent asked for a 'Sign in with LinkedIn' connect request, but the platform turned it into a paste box, probably because LinkedIn has no registered OAuth client (unverified)
---

# Reconnecting LinkedIn falls back to pasting a token

LinkedIn returned REVOKED_ACCESS_TOKEN (run 8f24ac10595b43af). The agent tried to raise a one-tap Sign in with LinkedIn reconnect, but the platform turned it into a paste-a-token box. Hypothesis, not yet verified: generic OAuth can't reach LinkedIn without a registered client, so the platform falls back to pasting (see audit lane L5: OAuth providers as data). Confirm the cause before fixing.

**Source:** the founder's command-center agent, in the founder's app thread on 2026-10-04: its 3:06 PM PDT status report and its 5:27 PM PDT live re-test (plus its 5:58 PM and 6:25 PM replies where noted). Its run IDs are in its notes/command-center-playbook.md. Re-verify against current main before fixing.
