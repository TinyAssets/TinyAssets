---
severity: P1
title: Custom-agent effects still select main's controls
filed: '2026-10-03'
summary: Addressed-agent foundations preserve an owner-level control gap; effector rules/review and several control doors still default to main.
---

Re-verified on `origin/main` `3e1587b3e500d81919c0c299b862d31b5360757c`
(2026-10-03), after #4287 and #4228 landed. It was first filed against a combined
pre-merge foundation sha that never reached `main`; the finding survived the
re-check unchanged, but every line citation moved, so `design.md` now pins each
one to a symbol as well as a line. No production mutation or new cross-owner
bypass is claimed.

## Evidence and impact

Disposable local characterization used the real `agent_rules`/`agent_review`
stores and `effectors.authenticated_external_call._rule_refusal`: researcher had
`app.write=hand_off`; main's `app.write` review was disabled. Researcher's direct
rule decision refused, but `_rule_refusal(universe, 'demo', 'POST', '/')` returned
`None` because the door selects main. No provider or transport was called.
Standing destination consent and other existing authority checks remain required;
this result proves only that researcher's intended rule does not reach this door.

`engine_mcp_server.run_graph` carries no addressed identity into persisted runs;
`BranchExecutionContext`, initial/resumed `EffectChain`, and run reconstruction
have none to supply. A thread-local or model-supplied selector would not repair
durable/nested execution safely. Stop, rules UI, pending creation/withdrawal/answer
routing and journal callers have related omissions documented in the design.

## Accepted foundation residual and closure

The [#4287 exact-head review](https://github.com/TinyAssets/TinyAssets/pull/4287#issuecomment-5964061132)
and [#4228 exact-head review](https://github.com/TinyAssets/TinyAssets/pull/4228#issuecomment-5964160024)
retain the D8 integration dependency. Their foundation approval is not an assertion
that custom agents have independently enforced controls. This concern makes that
existing owner-level P1 visible; it does not reopen their capped reviews.

Resolution is tracked by [addressed-agent-control-provenance](../../openspec/changes/addressed-agent-control-provenance/design.md).
Close only after reviewed implementation proves the served two-agent effector,
queue/resume, request and owner-control flows; current owner/home revocation and
cross-owner negatives; safe legacy handling; and separately authorized grouped
live acceptance. The present PR contains a design, not a mitigation or fix.

First Claude design ADAPT on f302de7 is folded; [Claude approved the design](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542)
at 6bf7923597983ec9af61968b99001745a981f2a7. This concern remains open. The planned
engine/effector checks do not intercept native internal tools: launch termination
has an explicit pre-exit residual and D2 remains held. Per-launch transport
credentials/isolation and owner reconfirmation of legacy recurring definitions
are proposed, unimplemented requirements; they do not mitigate this concern yet.
