## 1. Contract (this change)

- [x] 1.1 Proposal, design and spec deltas for step 1 of the 2026-10-11
      decision. Verify: `npx openspec validate one-model-call-path --strict`.

## 2. Build (each PR ships alone, net-negative, deletions included)

- [ ] 2.1 Measure: inventory every caller of `call_provider`,
      `call_with_policy(_sync)` and the allocation constructors, and count
      stored `llm_policy.difficulty_override` nodes on a production copy.
      Correct the design's deletion table with measured spans. Verify: the
      table matches `git grep` on the base commit.
- [ ] 2.2 Receipt (D5): extend `ExecutionReceipt`, return it from
      `ProviderRouter.call`, and delete `WriterExecutionReceipt`. Verify:
      tests assert that requested, resolved and reported models are distinct,
      and that `unknown` usage never reads as zero. Net below 0.
- [ ] 2.3 One call (D1, D7): add `model_call.call` and `call_sync` over
      `ProviderRouter.call`; move the 25 `call_provider` sites and the graph
      prompt path to it. Delete `call_provider`, `UniverseBoundProviderCall`,
      `_build_fallback_router`, `_call_router_with_retry`, `call_with_policy`,
      `call_with_policy_sync`, `_call_policy_router_with_retry` and both
      mocks. Verify: `git grep` finds no removed name, and a node with no model
      raises `CompilerError`. Net below 0.
- [ ] 2.4 Authority is the connected set (D2): add `connected_sources`; make
      `require_owner_bound_dispatch` check membership; collapse the
      default/manifest scopes in foreground, background and cloud continuation
      admission; delete the exactly-one-provider checks and `FALLBACK_CHAINS`.
      Floor: one cross-family `peer-agents` round. Verify: a two-owner test
      shows owner B's source refused for owner A, and a revoked grant is
      refused on the next attempt. Net below 0.
- [ ] 2.5 Explicit choice (D3): resolution order, declared fallbacks only,
      the first runtime caller of `SettingsSnapshot.model_choice()`, and M1
      (backup first, writers stopped). Verify: a node that pins a connected
      non-default model runs on it; a pinned model that is unavailable fails
      naming it; nothing substitutes without a declared fallback. Net below 0.
- [ ] 2.6 Allocation (D4): `call` opens or joins the durable broker
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
- [ ] 2.8 Supersede PR #4614: land its lost-message fix alone, then close the
      rest with a pointer to this change. Verify: #4614 is closed, and the
      lost-message fix is merged and deployed.

## 3. Ship

- [ ] 3.1 Live acceptance: `python scripts/deployed_sha.py --assert-contains
      <sha>`. Then, through the app agent as a naive user with no platform
      patch, an agent whose settings set `model:` to a connected GPT model runs
      a review step through `ta call model`. Its receipt shows
      `resolved_model` in the GPT family, `requested_model` equal to the
      setting, a `reported_model`, and a settled outcome on the run's
      allocation.
- [ ] 3.2 Sync the deltas into `openspec/specs/` and archive the change.
