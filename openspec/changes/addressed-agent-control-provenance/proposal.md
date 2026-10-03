# Addressed-agent control provenance

Status: design only, awaiting cross-family design review. Owner: Codex handoff;
implementation owner assigned by the lead after review. Tier 2: authority/storage.

## Why

Carry the authenticated addressed agent through its work so each existing control
selects that agent's rules, requests, journal and Stop target.

The combined foundation at `6684a082d923d7db6288b4639a507719b94ab742`
(#4287 `8475c6d7f9371375082726fba01182ae1a76932e` plus #4228
`6951282aca0636bb389a4d16385b43e3cd2fb770`) has addressed conversation threads
and per-agent storage, but several callers still select `main`. A disposable
characterization set researcher `app.write=hand_off` and disabled main's review;
the actual effector rule door returned no refusal. No provider or outbound call
was made. This is an owner-level control mismatch, not a new cross-owner finding.

The [#4287 receipt](https://github.com/TinyAssets/TinyAssets/pull/4287#issuecomment-5964061132)
and [#4228 receipt](https://github.com/TinyAssets/TinyAssets/pull/4228#issuecomment-5964160024)
explicitly preserve this D8 residual. Foundation approval does not establish
complete custom-agent controls or grouped live acceptance.

## What changes

Capability: `addressed-agent-control-provenance`, implementing the control portion
of [universe-agent-harness design section 4.18](../universe-agent-harness/design.md).
Extend existing authoritative turn/run records and execution contexts with a
validated addressed-agent snapshot; carry it through direct, nested, queued and
resumed work. Use it at the existing rules/review, pending-request and journal
doors. Wire the existing owner Rules and Stop controls to the addressed agent.

No new permission, policy behaviour, provider budget, model, shared-brain boundary,
agent delegation feature, broad UI redesign or setting is introduced. Existing
connection consent, authored-branch checks and owner/home authority remain required.
Activities/manifest work reuse their existing execution subject and lease lineage;
this change does not replace that protocol. Visibility configuration and other D8
features remain in their existing slices.

## Delivery boundary

This PR is documentation only. The design must be reviewed before runtime or
schema implementation. Implementation lands as one coherent integration after
the foundation dependencies, with the proof matrix in `design.md`, mirrors,
focused CI and fresh implementation review. Deployment and live acceptance are
separate steps; no schema migration, credential change or rollout is authorized
by a design receipt.
