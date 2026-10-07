---
severity: note
title: Automatic source-health follow-ups
filed: '2026-09-15'
summary: bounded advisory memory approved for MVP; post-live isolation, legacy classification and coverage observations remain
---

# Automatic source health — post-live follow-ups

Filed 2026-09-15. Fable5.1 review of d2b77b84a7a6654a1cf32925bff2a0f776720e26
approved the MVP with these non-blocking observations, not demonstrated outages:

- Success hint clearing sits inside the router's provider try; a future exception
  in this advisory operation could turn a successful response into a failure.
  Required authority fields currently exist. Isolate advisory failures when
  extending this mechanism; retain actual response/effect evidence.
- Legacy api/runs.py classification still reports provider_unavailable for this
  message. The old native authentication message also missed its auth tells;
  the served chat path now carries typed auth_invalid. Separate legacy follow-up.
- Process-local hints are intentionally advisory and disappear on
  restart; multiple workers do not share them. This is not durable availability
  tracking. Any persistence expansion needs a reviewed storage/authority design.
- Current tests cover adapter normalization and real router/plan composition in
  separate tests. A whole fake-stream-through-served-router regression would
  improve coverage; actual live Automatic recovery remains the shipping proof.

No credential renewal, saved default change, workflow edit or unsafe replay is
authorized by these findings. Canonical review: ../reviews/2026-09-15-automatic-source-recovery.md.

Post-deploy watch, September15 23:03UTC: release cee95ccbde4c passed protected
deployment/public-canary checks. An ordinary new Automatic message completed
through Codex after the prior turn's sign-in failure, without settings changes or
replay. Narrow live acceptance is recorded in
../../openspec/changes/archive/2026-10-06-select-agent-models/automatic-source-health-live.md.
Subsequent owner use contradicted lasting recovery: September15 failures at
16:10,16:19,16:26PDT were each followed by a repeated request answered by Codex.
PR3862 removes the timer expiry; deployed long-gap proof remains pending.
Watch returning user history for recovery/repeated failures; do not equate this advisory routing
repair with restored Claude authentication or durable multi-worker health.
