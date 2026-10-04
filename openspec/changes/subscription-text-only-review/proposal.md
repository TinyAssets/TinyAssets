## Why

Ordinary users must be able to build and merge patches and create custom tools for their own connected software and platforms, including GitHub, GitLab, and other services, using agents, tasks and graphs. At baseline `811b4dd3b18ef369433b48ebeb7a08c7c6be157e` (2026-10-04), required review uses the working provider, but both subscription adapters refuse text-only calls. Subscription-only users are the default and must gain a working review path without failing open. The founder is one such user; his patch intake is an ordinary receiver node.

## What Changes

- Select an independently admitted reviewer from the command center's own authorized connections, with a verified model family different from the work's producing family. Preserve the conversation model and its defaults; record the actual reviewer provider, model, family, enforcement evidence, and verdict on the run.
- Accept either `enforced_text_only` or `contained` review, with per-launch proof and an honest tier in the run record. Claude qualifies as text-only when its complete tool/configuration/policy proof passes. The host-files-only argument is refuted by documented server-managed hooks; absence proof must cover remote policy too. Codex uses contained review because residual tools need not be absent if they cannot cause effects or persist.
- Specify a review profile in the existing provider jail: read-only review input, disposable workdir/home, no owner workspace, engine relay or general egress, bounded inference transport with isolated credentials, inherited process confinement and complete teardown. The existing jail's ordinary profile is insufficient. Use the contained profile for Claude too when tool-free policy proof is unavailable and containment can be proven.
- Admit either tier only with current version/configuration/authority evidence; otherwise hold the specific call with an actionable reason. An owner-authorized API-key HTTP reviewer is acceptable but not required. No platform credentials, silent paid fallback, or owner-approval bypass.
- **BREAKING:** consequential external writes require cross-family review, including where an older optional review-off setting would skip it. Same-family review, unknown family, and unproven confinement do not satisfy this gate. A valid review may still require the owner's separate consent.
- Make every receiver owner's intake durable and inspectable: one failed connection write does not discard later independent steps. Add owner-scoped `read_graph target="deliveries"` and operation-scoped retry. Assessment, internal notification and external filing have independent outcomes. The founder's inbox uses this same ordinary-user path.
- Defer semantic duplicate search/coverage assessment to a separately scoped follow-up; retain safe retry/idempotency requirements in this change.

## Capabilities

### New Capabilities

- `external-write-review`: provider-neutral text-only or contained cross-family review and actionable fail-closed outcomes for consequential external writes by agents, tasks, graphs and custom tools.
- `receiver-intake-recovery`: owner-scoped intake visibility and independent assessment, notification, and filing recovery for ordinary receiver workflows.

### Modified Capabilities

- `provider-routing`: required external-write reviews use only the eligible owner-bound review candidates, never generic role-chain fallback.

## Impact

Likely implementation seams: `tinyassets/agent_review.py`, `foreground_run_provider.py`, corresponding background/automation admission paths, provider work receipts and model discovery metadata, `providers/{base,router,api_key_http_provider,claude_provider,codex_provider,provider_jail,jail_seccomp,owned_process}.py`, the inference transport boundary, and every external-write enforcement boundary. Existing native adapters are migration debt; selection, capability metadata and containment are vendor-neutral contracts.

Intake seams: `patch_intake.send_patch_request`, `delivery_runtime`, `storage/deliveries`, `api/deliveries`, receiver graph execution, run projections, and existing `read_graph`/`write_graph` delivery surfaces. Proposed additive storage holds review provenance and per-operation delivery outcomes; all reads retain owner/receiver authorization. No new top-level MCP handle.

This lane revises proposal artifacts only from `ac40135f`. Owner: Codex; branch: `spec/subscription-text-only-review`; no PR is opened in this session. Implementation, floor review, deployment, app proof, and spec sync remain future work. The implementation must demonstrate successful subscription-only writes in both family directions; merely exposing holds or requiring an HTTP API key does not complete this change.
