## Context

The public MCP probe establishes transport and catalogue health, not owner execution. The existing production-copy oracle is valuable incident tooling but cannot be required PR CI: it consumes private backups and replaces the outbound service channel. The parallel regression lane owns that oracle and its repairs.

## Goals / Non-Goals

Detect loss of every founder-listed capability before merge and during production operation. Keep one capability catalogue and use actual persisted outcomes, streamed events and service effects as proof. Do not add a production test bypass, a platform model credential, or mock authority/cell/broker seams.

## Decisions

- Measure first: reverse each real production fix on an isolated scratch revision and compare existing test outcomes with the unchanged baseline. Report actual failing node IDs and zero-catch groups, keeping baseline failures and skips separate. Replacement acceptance must kill those same defects before seam-mocked capability tests are removed; preserve pure logic and unique correctness assertions. Both net test count and measured PR time must decrease.
- Encode tiers in affected-test selection: PRs run fast affected unit tests and one production-image check only for runtime/deploy input changes. Production checks run after deployment and on a hosted schedule. Heavy/slow suites remain outside the PR path.
- Required acceptance boots the production Dockerfile and normal entrypoint in compose security posture. A disposable volume contains only a synthetic owner, model source, HTTP service grant and command center. A local deterministic model endpoint returns tool requests; the real CLI, engine routes, cells and broker execute them. External service fixtures receive actual HTTP traffic. Structural checks reject patched execution seams and incomplete capability results.
- CI and live runners consume one catalogue and fail on missing, skipped or duplicate outcomes. Live checks use dedicated owner credentials and ordinary app/MCP entry points. Fixture provisioning never grants the operational canary principal owner authority.
- Hosted five-minute checks and post-deploy checks use GitHub issues and the existing Pushover emergency path. Failures name capability, stable error code and running revision; alert transport failures are errors. Post-deploy failure feeds the existing rollback condition.
- Runtime counters hold bounded, data-free minute buckets of successes and stable failure codes. Protected pulse diagnostics compare a recent window with a preceding baseline and also detect total/large absolute failure rates without a baseline. No exception text, owner IDs, credentials or prompt content enter aggregate diagnostics.
- Historical defects are exercised by isolated reintroduction runs or parent-image runs; a table identifies which defect was actually demonstrated and any unavailable evidence. A test of a fake failure report alone is not regression proof.

## Risks / Trade-offs

- External sign-in and model services cannot be exercised with production credentials in PR CI. Synthetic session material must use normal verification; live checks cover the dedicated account and real connected provider. Missing provisioning fails visibly and gets one founder host-action row.
- CI image acceptance is slower than unit tests. Reuse image layer caches, bound each probe, retain sanitized failure reports and require the aggregate job in merge admission.
- Live probes create artifacts and incur connected-model usage. Scope all effects to the dedicated canary center/service destination, overwrite two fixed scratch files and delete consumed wake definitions. Normal run, approval and delivery history remains in that dedicated account; never touch real users.
- A process restart resets in-memory rates. Expose the observation window and boot identity so absence of history is not a claimed healthy baseline.

## Migration Plan

Deploy additive diagnostics with ordinary runtime changes. Configure the dedicated owner once, then enable required image acceptance and hosted/deploy probes. Rollback uses the existing captured previous image. No user-data migration is required.
