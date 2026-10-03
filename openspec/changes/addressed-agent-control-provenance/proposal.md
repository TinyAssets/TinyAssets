# Addressed-agent control provenance

Status: design only; [Claude approved the fold](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542)
at 6bf7923597983ec9af61968b99001745a981f2a7. Owner: Codex handoff;
implementation owner assigned by the lead after review. Tier 2: authority/storage.

## Why

Carry the authenticated addressed agent through its work so each existing control
selects that agent's rules, requests, journal and Stop target.

#4287 and #4228 both landed and are on `main`; their work is re-verified against
`origin/main` `3e1587b3e500d81919c0c299b862d31b5360757c` (2026-10-03), which is
what every citation in `design.md` is now pinned to. Addressed conversation
threads and per-agent storage exist, but several callers still select `main`. A disposable
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

No new provider budget, model, shared-brain boundary, agent delegation feature or
broad UI redesign is introduced, and no existing permission is widened: every
control keeps the authority it has and merely selects the addressed agent instead
of always selecting `main`.

What this DOES add, stated plainly because an earlier draft claimed "no new
permission, policy behaviour or setting" and that was not accurate (design review
2026-10-03, finding 5):

- a per-launch transport credential with a server-side digest, which is a new
  authentication capability and a new stored secret shape (`design.md` §3, F2);
- a launch-binding table plus addressed-agent snapshot columns on runs, turns,
  the journal, pending requests and automations (new storage shape);
and nothing else. Both items are approved (founder, 2026-10-03; `design.md`
§10b) and both are internal.

CUT by the same answer, having been in an earlier draft of this list: the durable
`held` automation state with `held_reason`/`reconfirmation_required`, and the
public `write_graph` `confirm_agent_provenance` payload field with its read
projection fields. They existed only to carry EXISTING recurring definitions
across the change, and the founder is clearing those through their own surface,
so there is nothing to migrate (`design.md` §10a).

**So this change has NO public MCP surface delta.** Those four fields were the
whole of it: it needs no live-connector spec delta and no canary
`--assert-handles` run (Hard Rule 11). Storage shape and authority still change,
which is why they were specced before code.
Existing connection consent, authored-branch checks and owner/home authority
remain required.
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

The first Claude design ADAPT is folded in the design/spec: enforcement is scoped
to engine/effector admission with an explicit native termination residual; a
proposed per-launch authentication credential requires proved process isolation;
and existing recurring work visibly holds for explicit owner reconfirmation.
These are proposed contracts, not available runtime facilities. The D2 native-yield
hold and separate implementation/deployment acceptance remain unchanged.
