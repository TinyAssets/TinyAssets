## Why

Ordinary users must be able to build patches and perform consequential writes on their own connected project platforms, including GitHub, GitLab, and other services. At baseline `811b4dd3b18ef369433b48ebeb7a08c7c6be157e` (2026-10-04), required automatic review uses the working provider, but both subscription adapters refuse enforced text-only calls; this blocks required-review writes and also strands the founder's ordinary receiver-based patch intake.

## What Changes

- Select an independently admitted reviewer from the command center's own authorized connections, with a verified model family different from the work's producing family. Preserve the conversation model and its defaults; record the actual reviewer provider, model, family, enforcement evidence, and verdict on the run.
- Preserve the enforced text-only requirement. Define `enforced_text_only`, `confined_tools`, and `unsupported` separately. Local Claude Code 2.1.289 documents built-in tools off, but managed policy/hooks prevent an unconditional subscription guarantee. Local Codex 0.160.0 documents confinement, not all tools off. Neither native adapter becomes eligible merely by setting a boolean or supplying a denylist.
- Admit native review only after version-bound evidence proves the complete execution contract, including ambient policy and account tools. Otherwise hold the write and name a concrete eligible connection/model or the missing family and text-only capability. HTTP adapters remain eligible only for their already-validated request shapes and owner-authorized models. No platform credentials, silent paid fallback, or owner-approval bypass.
- **BREAKING:** consequential external writes require cross-family review, including where an older optional review-off setting would skip it. Same-family review, unknown family, and weaker confinement do not satisfy this gate. A valid review may still require the owner's separate consent.
- Make receiver intake processing durable and inspectable: assessment, owner notification, and external filing have independent outcomes; filing failure never suppresses the other two. Add receiver-scoped listing and operation-scoped recovery to the existing delivery surfaces. The founder's patch-request inbox is a dogfood instance, with no repository, account, or destination special case.
- Defer semantic duplicate search/coverage assessment to a separately scoped follow-up; retain safe retry/idempotency requirements in this change.

## Capabilities

### New Capabilities

- `external-write-review`: provider-neutral enforced text-only, cross-family review and actionable fail-closed outcomes for consequential external writes by agents, tasks, and graphs.
- `receiver-intake-recovery`: owner-scoped intake visibility and independent assessment, notification, and filing recovery for ordinary receiver workflows.

### Modified Capabilities

- `provider-routing`: required external-write reviews use only the eligible owner-bound review candidates, never generic role-chain fallback.

## Impact

Likely implementation seams: `tinyassets/agent_review.py`, `foreground_run_provider.py`, corresponding background/automation admission paths, provider work receipts and model discovery metadata, `providers/{base,router,api_key_http_provider,claude_provider,codex_provider,provider_jail}.py`, and every external-write enforcement boundary. Existing native adapters are migration debt; the selection contract and new capability metadata are vendor-neutral, not new vendor-specific connection or destination code.

Intake seams: `patch_intake.send_patch_request`, `delivery_runtime`, `storage/deliveries`, `api/deliveries`, receiver graph execution, run projections, and existing `read_graph`/`write_graph` delivery surfaces. Proposed additive storage holds review provenance and per-operation delivery outcomes; all reads retain owner/receiver authorization. No new top-level MCP handle.

This lane writes proposal artifacts only. Owner: Codex; branch: `spec/subscription-text-only-review`; no PR is opened in this session. Implementation, floor review, deployment, app proof, and spec sync remain unchecked future work. This proposal explicitly does **not** claim to restore subscription-only writes today: the proven-native-reviewer gap remains visible until evidence closes it or the owner connects an eligible text-only source.
