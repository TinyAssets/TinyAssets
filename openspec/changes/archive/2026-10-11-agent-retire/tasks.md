## 1. Implementation

- [x] 1.1 Add durable lifecycle and owner/revision-fenced graph and ta operations.
- [x] 1.2 Hide retired agents and stop interactive and automation execution with a reason.
- [x] 1.3 Add lifecycle, isolation, history, running-turn and real-browser switcher tests and handbook guidance.

## 2. Verification and delivery

- [x] 2.1 Run affected Linux tests with zero skips, prompt budgets, ruff, plugin mirror and hygiene.
- [x] 2.2 Sync spec, commit verified slices and open draft PR resolving #4521.
- [x] 2.3 Complete Claude review, record verdict and decisions, merge origin/main and push final evidence.

## Evidence

Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4528. Proposal/design
committed first in `358a487508`; initial verified implementation in `6b01c52085`.
Main spec synced at `openspec/specs/agent-retire/spec.md`.

Final combined Linux oracle: **391 passed, zero skips**, Python 3.11.17,
bubblewrap 0.12.0, uid 1001. Command prefix:
`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q`; suffix:
`--basetemp /tmp/b`. Test files:

- `tests/test_agent_retire.py`
- `tests/test_custom_agents.py`
- `tests/test_converse_addressed_agent.py`
- `tests/test_app_addressed_agent.py`
- `tests/test_agent_activities.py`
- `tests/test_activity_dispatch.py`
- `tests/test_activity_http_yield.py`
- `tests/test_turn_interrupt.py`
- `tests/test_converse_turn_cost.py`
- `tests/test_provider_serving_binding.py`
- `tests/test_engine_mcp_write_graph_patch.py`
- `tests/test_interactive_http_agent.py`
- `tests/test_account_deletion.py`
- `tests/test_ta_capabilities.py`
- `tests/test_ta_capabilities_jail.py`
- `tests/test_universe_server_five_handles.py`
- `tests/test_served_systems_guidance.py`
- `tests/test_onboarding_serving.py`
- `tests/test_model_bootstrap.py`

Affected heavy suites additionally passed **146 tests, zero skips**, with the
same Linux oracle prefix/suffix: `tests/test_mcp_instruction_surfaces.py`,
`tests/test_universe_server_isolation.py`, `tests/test_scoped_identity_reset.py`.
Total final verification: **537 passed, zero skips**. Hygiene: zero removed tests
and zero tampering findings. `origin/main` was merged before the final push
(already up to date at `a97c17c26ea2a7a25764c02e9e095b87589edc67`).

Ruff passed on all changed canonical Python files and tests. Plugin build and
import probe passed; whole-tree mirror parity: 625 canonical files matched.
Static prompt-budget tests were not modified. Chromium runs the shipped
switcher with real roster reads; Playwright is imported inside the test.

Claude review: **ADAPT**, all four findings accepted and addressed. Details:
`openspec/changes/agent-retire/review.md`. The draft has
no current-head Drain-Review approval receipt; that merge gate remains pending.
Production deployment, deployed-SHA assertion and live authenticated app-agent
proof are not claimed by this draft delivery.

## Round 2 CI regressions (2026-10-06)

Compared against detached `origin/main` at
`dd82fd3d4aca76e280acd4b2899bd3cff671c676` on the Linux oracle: the full
rulebook and live-view files plus the selected-proof case passed **22 tests,
zero skips**. The same selection on this lane reproduced all four failures
(18 passed). All are retire-lane interactions, not main-only failures:

- Both rulebook tests: the 3,420-byte retire review pushed `docs/reviews/*`
  from 538,389 to 541,809 bytes. Moved that change-specific record intact to
  `openspec/changes/agent-retire/review.md`; the existing pin is unchanged.
- Live-view owner-wait states: retirement admission correctly rejects the
  fabricated `agent_binding_1`. Publish and bind a real owner agent in the
  fixture, preserving every state, privacy and count assertion.
- Selected jail-proof helper: the new single-case retire browser file sorts
  first. Select an actually multi-case marked file; retain the full-pass and
  missing-sibling-fails assertions and the multi-case requirement.

Final Linux oracle: **152 passed, zero skips**, Python 3.11.17, bubblewrap
0.12.0, uid 1001. Prefix `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py
-- -q`, suffix `--basetemp /tmp/b`, files:
`test_rulebook_ratchet.py`, `test_live_view.py`,
`test_linux_jail_proof_workflow.py`, `test_agent_retire.py`,
`test_orphan_ready_turn.py`, `test_orphan_ready_coordinator.py`,
`test_orphan_ready_browser.py`, `test_orphaned_turn_reconcile.py`,
`test_turn_interrupt.py`, `test_interrupted_run_surface.py`, and
`test_converse_turn_cost.py` (all under `tests/`).

Ruff passed for all changed canonical Python/test files. Plugin build/import
probe passed (643 staged runtime files), producing no diff. No pins, static
prompt budgets, skips, xfails or production admission checks changed.

Claude round 2 via `peer-agents`, read-only, completed in 97 seconds:
**APPROVE**, no floor/correctness findings. **AGREE** with its only wording
note: remove the fixed "eight" sibling count from the proof-test docstring.
The review checked the rename, binding scope/ownership, all retained assertions,
and fail-loud behavior if no multi-case browser file exists.

Merged `origin/main` at `dd82fd3d4a` before verification; fetched again after
verification and it had not moved. Draft PR #4528 remains the delivery boundary.

## Round 3 fixture audit (2026-10-06)

Merged `origin/main` at `fb22e770bd74333f54786233ba6046ccfb0b03c2`.
`tests/test_app_live_route.py` now publishes and binds Mapper to `owner-secret`
in `u-alpha`, and asserts the live state using the returned binding ID. This
repairs all four setup errors without changing production addressing, access,
privacy or state assertions. PR #4528 was returned to draft as requested.

Whole-tree searches covered `agent-1`, `agent-a`, `agent_binding_1`, all
`agent_id` assignments, activity-store imports/calls and resolver imports.
Traced direct activity creation, the API and bound-request wrappers, and
retirement-aware turn paths. No other stale positive-path fixture remained:
activity callers use main or real bindings; custody/capability IDs are storage
keys; the background-provider `agent-a` is a serving-resolver stub; browser and
handler doubles bypass addressing by design. Negative tests retain nonexistent
IDs to prove refusal. The prior `test_live_view.py` repair remains intact.

`scripts/ci_structural_guards.py` is absent from merged main. Run the existing
structural guard suites directly on the Linux oracle instead of adding a runner.
Keep all static prompt budgets and admission checks unchanged. Retain the
existing PR for this same lane; do not create a duplicate PR.

Round 3 verification: **1,429 passed, zero skips**, Linux Python 3.11.17,
bubblewrap 0.12.0, uid 1001. Three invocations, each using
`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <files> --basetemp /tmp/b`.
All filenames below are under `tests/`:

- Affected fixtures and callers: **532 passed** in 156.71s:
  `test_app_live_route.py`, `test_live_view.py`, `test_agent_activities.py`,
  `test_activity_dispatch.py`, `test_activity_http_yield.py`,
  `test_activities_api.py`, `test_account_deletion.py`, `test_agent_retire.py`,
  `test_converse_addressed_agent.py`, `test_app_addressed_agent.py`,
  `test_custom_agents.py`, `test_command_center_agent_templates.py`,
  `test_inline_approvals.py`, `test_inline_request_storage.py`,
  `test_turn_interrupt.py`, `test_orphan_ready_turn.py`,
  `test_orphan_ready_coordinator.py`, `test_orphan_ready_browser.py`,
  `test_orphaned_turn_reconcile.py`, `test_interrupted_run_surface.py`,
  `test_background_served_provider.py`, `test_provider_serving_binding.py`,
  `test_universe_server_five_handles.py`, `test_conversation_custody.py`,
  `test_provider_request_capability.py`, `test_converse_turn_cost.py`.
- Structural guards: **476 passed** in 84.80s:
  `test_rulebook_ratchet.py`, `test_context_budget_regression.py`,
  `test_context_guardrails.py`, `test_control_plane_inventory.py`,
  `test_background_authority_inventory.py`, `test_command_center_inventory.py`,
  `test_engine_secret_inventory.py`, `test_manifest_connection_inventory.py`,
  `test_universe_ownership_inventory.py`, `test_real_browser_import_guard.py`,
  `test_source_guard_syntax.py`, `test_universe_path_io_guard.py`,
  `test_workspace_family_execution_guard.py`, `test_test_inventory.py`,
  `test_test_hygiene_gate.py`, `test_vocabulary_hygiene.py`,
  `test_channel_agnostic_ratchet.py`, `test_linux_jail_proof_workflow.py`,
  `test_ci_runner_budget.py`.
- Additional addressing and affected heavy suites: **421 passed** in 145.13s:
  `test_authenticated_external_call_effector.py`, `test_interactive_http_agent.py`,
  `test_owner_steering.py`, `test_agent_rules.py`,
  `test_engine_mcp_write_graph_patch.py`, `test_ta_capabilities.py`,
  `test_ta_capabilities_jail.py`, `test_served_systems_guidance.py`,
  `test_onboarding_serving.py`, `test_model_bootstrap.py`,
  `test_mcp_instruction_surfaces.py`, `test_universe_server_isolation.py`,
  `test_scoped_identity_reset.py`.

Ruff passed on every changed canonical Python/test file. Plugin rebuild staged
643 files and passed its import probe, with no generated diff. Context budgets
and diff whitespace passed. No tests weakened, skipped or xfailed.

Claude round 3: **APPROVE**, no floor/correctness findings; **AGREE** with the
independent fixture audit. See `review.md`. Fetched main again before final
push; it remained `fb22e770bd74333f54786233ba6046ccfb0b03c2` and merge reported
already up to date. Deployment and live authenticated app-agent proof remain
after merge, outside this requested draft-PR delivery.
