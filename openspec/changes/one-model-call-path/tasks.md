## 1. Contract (this change)

- [x] 1.1 Proposal, design and spec deltas for step 1 of the 2026-10-11
      decision. Verify: `npx openspec validate one-model-call-path --strict`.

## 2. Build (each PR ships alone, net-negative, deletions included)

- [x] 2.1 Measure: inventory every caller of `call_provider`,
      `call_with_policy(_sync)` and the allocation constructors, and count
      stored `llm_policy.difficulty_override` nodes on a production copy.
      Correct the design's deletion table with measured spans. Verify: the
      table matches `git grep` on the base commit.
- [x] 2.2 Receipt (D5): extend `ExecutionReceipt`, return it from
      `ProviderRouter.call`, and delete `WriterExecutionReceipt`. Verify:
      tests assert that requested, resolved and reported models are distinct,
      and that `unknown` usage never reads as zero. Net below 0.
- [x] 2.3 One call (D1, D7): add `model_call.call` and `call_sync` over
      `ProviderRouter.call`; move the 25 `call_provider` sites and the graph
      prompt path to it. Delete `call_provider`, `UniverseBoundProviderCall`,
      `_build_fallback_router`, `_call_router_with_retry`, `call_with_policy`,
      `call_with_policy_sync`, `_call_policy_router_with_retry` and both
      mocks. Verify: `git grep` finds no removed name, and a node with no model
      raises `CompilerError`. Net below 0.
- [ ] 2.4 Authority is the connected set (D2, decided: admission snapshots
      the live set). Build: admission in foreground, background and cloud
      continuation reads `connected_sources` once and writes the sorted set
      into the receipt as `admitted_sources` (its digest in `manifest_digest`);
      reservation and launch allow a source only in both `admitted_sources`
      and the live set; receipt limits come from the run's allocation (D4).
      Delete: the default/provider scope and its child work-binding mint,
      `_admit_manifest`, `_current_work_manifest`, `_current_work_member`,
      `_issue_manifest_receipt`, the `manifest_bindings` plumbing,
      `_current_selected_member_authority`, the assignment and parent-binding
      fields checked in `_validate_receipt_parent`, the assignment rewrite on
      disconnect in `connection_lifecycle`, `_ensure_manifest_receipt_schema`,
      the exactly-one-provider serving checks and `FALLBACK_CHAINS`; delete
      `provider_assignment_manifest.py` if `git grep` finds no reader left.
      Migration M2 (window, writers stopped, after the backup): fence every
      active receipt and close its claim, log the count and run IDs; no table
      changes shape. Floor (cross-owner, revocation): one cross-family
      `peer-agents` round. Verify: a two-owner test shows owner B's source
      refused for owner A; a source connected after the owner's last
      admission runs on the next run without any assignment write; a running
      run never gains a source connected after its admission; a disconnected
      source is refused on the next attempt of a running run and in every new
      admission; an active receipt without `admitted_sources` is refused. Net
      below 0.
- [x] 2.5 Explicit choice (D3): resolution order, declared fallbacks only,
      the first runtime caller of `SettingsSnapshot.model_choice()`, and M1
      (backup first, writers stopped). Verify: a node that pins a connected
      non-default model runs on it; a pinned model that is unavailable fails
      naming it; nothing substitutes without a declared fallback. Net below 0.
- [x] 2.6 Allocation (D4): `call` opens or joins the durable broker
      allocation; delete the caller-built allocations,
      `request_budget_scope`, `current_request_budget` and the in-memory
      branch. Floor (money): cross-family round, plus a mutation table for
      double settlement and cross-owner allocation join. Verify: a retry and a
      child call settle on the parent exactly once, and a parent stop ends the
      child. Net below 0.
- [ ] 2.7 `ta` `model` capability and refusal (D6): add the capability,
      replace the refusal text, delete the `engine_mcp_server` prompt band-aid
      and the MCP-sampling reviewer. Verify: a raw POST to a model connection
      is refused with the fixed text before anything is sent, and `ta call
      model` returns text and a receipt. Net below 0.
- [x] 2.8 Supersede PR #4614: land its lost-message fix alone, then close the
      rest with a pointer to this change. Verify: #4614 is closed, and the
      lost-message fix is merged and deployed. Completed before this build;
      #4614 was already merged when C3 resumed.

## 3. Ship

- [ ] 3.1 Live acceptance: `python scripts/deployed_sha.py --assert-contains
      <sha>`. Then, through the app agent as a naive user with no platform
      patch, an agent whose settings set `model:` to a connected GPT model runs
      a review step through `ta call model`. Its receipt shows
      `resolved_model` in the GPT family, `requested_model` equal to the
      setting, a `reported_model`, and a settled outcome on the run's
      allocation.
- [ ] 3.2 Sync the deltas into `openspec/specs/` and archive the change.
