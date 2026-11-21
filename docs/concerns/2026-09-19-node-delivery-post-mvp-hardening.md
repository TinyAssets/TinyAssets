---
severity: note
title: In-node delivery post-MVP hardening
filed: '2026-09-19'
summary: exact-code review approves JSON RPC; refusal ordering, separate-ledger crash accounting, node-kind diagnostics and legacy owner fallback remain explicit follow-through
---

# In-node delivery: non-blocking post-MVP follow-through

**Filed:** 2026-09-19  
**Verified:** independent Claude Fable 5.1 source review of runtime head
`4ec8d2434002ff5bd0aee58fabf387a7c3439260`, terminal APPROVE after 269 seconds.  
**Severity:** note — reviewer found no release blocker; no live acceptance claimed.

Full substantive review and context:
[PR #3881 review receipt](https://github.com/Jonnyton/TinyAssets/pull/3881#issuecomment-5740090073).
Current candidate remains JSON/source-code RPC only. Full file/retention/retry and
two-user rendered proof remain in [the `connect-cross-user-nodes` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/connect-cross-user-nodes).

## Source (verbatim)

> **Later hardening, none blocking**
>
> - `deliveries.py:118` now validates the payload before the admin fence. A write-scoped non-admin gets a payload error instead of access denied. That reveals nothing about the universe, but restoring the old order keeps refusals uniform.
> - `settle_write` at `deliveries.py:183` runs after commit in a separate ledger. A crash between the two leaves a committed transfer whose admission can still be reclassified read. This is accounting only, not authority.
> - The alias table advertises `deliver_output` for every node kind, but only source-code nodes receive a delivery source. Other node kinds fail closed with `delivery_source_unavailable`, which is correct but will read as a confusing error to authors.
> - `create_run` retains the daemon-owner fallback when the argument is None and a daemon id is present. No path in this diff reaches it, but it is the remaining ambient owner source in the module.

## Disposition

Retain as explicit post-MVP work, without delaying the reviewed usable slice for
a zero-observation verdict. Do not advertise crash-atomic accounting: ordinary
accepted sends settle writes, but delivery and accounting are separate commits.
Any recovery follow-through must reuse durable delivery identity and existing
admission semantics rather than replaying user effects. Never delete or relabel
accepted transfers as accounting cleanup.

Missing trusted context must continue to refuse; clearer author-facing diagnostics
must not fill owner identity from an ambient request or make new node kinds appear
supported. Future owner-fallback removal needs regression coverage for legacy
daemon execution, not a blind change to non-delivery paths.

Resolve this concern when the four recorded observations are fixed or explicitly
retired with fresh evidence. Deployment, structured two-user acceptance and the
larger artifact capability have separate gates.
