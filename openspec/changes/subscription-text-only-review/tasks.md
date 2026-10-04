# Tasks: subscription-text-only-review

This is a proposal-only lane. All checkboxes below are future implementation or follow-up work; none was executed by preparing these artifacts. One intent: make required external-write review independently selectable and failures recoverable for ordinary user-connected platforms. Owner: Codex. Branch: `spec/subscription-text-only-review`. No PR is opened by this proposal session.

## 1. Prove and represent reviewer eligibility

- [ ] 1.1 Add version/configuration-bound enforcement descriptors and direct/router admission checks for `enforced_text_only`, `confined_tools`, and `unsupported`; verify HTTP retains source restrictions and native adapters remain ineligible without proof. Add focused cases to `tests/test_text_only_review_provider.py`, including tools beside verdicts and conflicting session inputs.
- [ ] 1.2 Complete a bounded native conformance investigation for the production Claude/Codex versions using isolated, authorized fixtures: Claude empty argv/MCP/settings plus managed hook/policy changes; Codex residual tools, credentials, and transport confinement. Record evidence or an explicit unsupported outcome. Promote neither adapter on help output, a denylist, or a skipped jail check; any jail/process changes require `python scripts/linux_oracle.py` with an actual pass. A negative result completes the investigation, not subscription support.
- [ ] 1.3 Add trusted model-family and producing-artifact provenance to discovery/run receipts; verify HTTP protocol labels, aliases, same-family gateways, multiple producers, unknown identity, and deterministic work cannot counterfeit diversity.

## 2. Route and expose required review

- [ ] 2.1 Implement review-only candidate selection and fresh owner-bound child admission across foreground/background/task/graph execution, with the reviewer's account seat, unchanged conversation assignment, privacy/spend limits, and two total attempts. Verify revocation, enforcement conflict, no-source holds, and eligible transient fallback.
- [ ] 2.2 Bind review to the complete immutable effect/artifact and authority generations at every consequential external-write boundary; persist actual reviewer and outcome on the run. Verify changed payloads, missing actual model evidence, persistence failures, old review-off settings, direct calls, and owner approval cannot bypass required review; retain consent semantics for completed `needs_approval` verdicts.
- [ ] 2.3 Add safe run diagnostics and deduplicated pending-request remediation through Connections; verify both Claude-to-GPT and GPT-to-Claude guidance names actual available sources or the exact missing family/capability, never suggests another unproven subscription as sufficient, and does not authorize new spending.

## 3. Preserve and recover receiver intake

- [ ] 3.1 Add receiver-scoped operation outcomes beside existing delivery acceptance/attempt records, including account-deletion/retention handling and `legacy_unknown` projections; expose independent assessment, internal notification, and filing composition in ordinary graph authoring. Verify accepted requests survive unavailable workers, filing refusal, and assessment failure using the delivery runtime/reservation tests.
- [ ] 3.2 Add the receiver inbox (`read_graph target="deliveries"`), extended individual detail, Received requests/Patch requests projection, and actionable rail holds. Verify pagination and state filters, normal receiver admin access, and refusal of sender/unrelated maintainer access in delivery public/API tests.
- [ ] 3.3 Add operation-scoped recovery with expected-version claims, current authority/review checks, and explicit unknown-outcome reconciliation. Verify concurrent retries, pre-send holds, post-send timeout/crash, completed assessment/notification preservation, and zero whole-branch replay. Prove an ordinary owner's app agent can adopt the independent intake composition without platform edits to user workflows.

## 4. Verify and deliver implementation

- [ ] 4.1 Run affected review/provider authority/receipt/delivery tests, affected heavy files, and ruff; use mutation tables only for cross-user and irreversible-effect authority guards. Obtain one cross-family floor/correctness review via `peer-agents` and record `AGREE`/`DISAGREE_EVIDENCE` dispositions. This is future work, not permission to dispatch agents or run tests in the proposal session.
- [ ] 4.2 Deploy the implemented change, assert `python scripts/deployed_sha.py --assert-contains <sha>`, run `python scripts/mcp_public_canary.py --assert-handles`, and complete real-user app proof: owner-connected GitHub and a non-GitHub platform, both producer-family directions using proven eligible reviewers, a truthful subscription-only hold, and filing failure with independent assessment/notification plus visible retry. Have the founder's own app agent adopt the same intake pattern, then sync delta specs and archive only delivered work.

## 5. Follow-up boundary

- [ ] 5.1 Scope a separate semantic duplicate-coverage change if prioritized: owner-authorized issue/request search, assessment-based coverage, links instead of duplicate filings, partial/unknown/search-failure handling, and concurrent filing policy. Do not implement duplicate search in this slice; idempotent recovery in 3.3 remains required regardless.

## Proposal verification record

The proposal uses baseline `811b4dd3b18ef369433b48ebeb7a08c7c6be157e`. Local evidence commands were `claude --version`, `claude --help`, `codex --version`, `codex exec --help`, and `codex --help`; only help/version processes ran, with no delegated agents or model calls. The design distinguishes local flag availability from upstream source evidence and unperformed runtime proof.

OpenSpec orientation and admission: `openspec list --json`, `openspec status --change subscription-text-only-review --json`, `python scripts/openspec_flow.py audit`, and `python scripts/openspec_flow.py check-change subscription-text-only-review --provider codex` (allowed). Existing global WIP and the unrelated incomplete `command-center-publish-repair` artifacts are diagnostic; no delivery implementation or additional worktree was claimed here.

Validation passed: `openspec validate subscription-text-only-review --strict` reported the change valid, and `openspec status --change subscription-text-only-review --json` reported all four artifact groups complete. Admission remained allowed with 12 future tasks, all unchecked. Product tests, native conformance launches, deployment, app mutation, and PR creation were not performed in this proposal session.
