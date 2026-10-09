# K2 review fixes

Baseline: `5f231a5705a59110623d78208b08811cb4f62c5d` (main at lane creation).
Branch: `fix/k2-codex-review-findings`, worktree `wf-k2fix`.

The supplied temporary review file was absent. The three findings in the builder
brief were reproduced directly against the baseline source.

## Changes

1. `engine_dispatch` passes its verified backend grant into `Capabilities`.
   The shared dispatcher requires mutation authority before dispatching any
   known capability not explicitly classified read-only. This includes extension
   install/activate/revoke, with no per-lifecycle grant checks. Existing owner,
   platform allowlist, connection, consent and execution gates still apply.
   A missing mutation grant defaults to refusal.
2. The thin loop no longer appends or dispatches direct history/activity handles.
   Its fully granted inventory is exactly read/write/edit/bash. Tests exercise
   conversation and runs reads through the existing ta engine bridge.
3. Non-granted served Claude turns have empty allowed tools, explicit WebFetch
   denial, and an empty native tool inventory. The same configuration gives
   Codex no engine session and an empty dynamic-tool launch definition.

## Regression proof

The final regression tests were run with the five changed production modules
loaded from `git show <baseline>:<path>` in an isolated Python process. All other
production modules were unchanged from that baseline. All five cases failed:

- `test_status_only_signed_turn_cannot_mutate_extensions[install|activate|revoke]`:
  real signed route and real ta dispatcher allowed lifecycle effects.
- `test_thin_loop_model_inventory_is_exactly_four`: history/activity were present.
- `test_non_granted_served_provider_launches_have_no_tools`: Claude omitted the
  empty native inventory flag and allowed WebFetch.

Against the fix, the lifecycle tests additionally prove unchanged extension state
after refusal, usable status/help/list/events, and successful effects after a
signed mutation grant. The focused local run passed 14 tests.

## Validation

- Linux oracle, Python 3.11.17, bubblewrap 0.12.0, uid 1001: **222 passed**, no skips.
  Files: test_ta_capabilities, test_ta_capabilities_jail, test_one_extension_unit,
  test_extension_activation_boundary, test_extension_hooks, test_extension_ui,
  test_extension_remote, test_extension_unit_jail, test_remote_mcp_connect,
  test_agent_loop_tool_session, test_agent_loop_served_chat, test_provider_sandbox,
  test_universe_intelligence, test_persona_custody, test_relay_ux_prompts,
  test_one_agent_definition (all under tests/, with .py suffix).
- Second Linux oracle: **290 passed**, no skips. Files: test_provider_work_authority,
  test_provider_retry (both affected heavy suites), test_run_provider_session,
  test_agent_node, test_engine_tool_client, test_codex_app_server,
  test_interactive_http_agent.
- `python scripts/ci_structural_guards.py`: **582 passed**.
- Ruff over every touched Python file: **passed**.
- Broader `python -m ruff check tinyassets tests`: **27 pre-existing errors in 17
  untouched files**. Every reported file was compared with baseline git content
  and was unchanged. No unrelated lint edits were included.
- `python packaging/claude-plugin/build_plugin.py`: **passed**, 684 files staged,
  import probe `probe-ok`.
- `openspec validate k2-review-findings --strict`: **passed**; delta synced into
  `openspec/specs/universe-agent-harness/spec.md`.
- `git diff --check`: **passed**.
- `python scripts/test_hygiene_gate.py --base <baseline> --head HEAD --body <PR body>`:
  **passed**. One whole-test retirement is declared in the PR body; its covered
  direct history/activity implementation is removed in this change.
- Commit hooks: **passed**, including mirror parity and import-graph smoke.

An initial oracle attempt stopped during its source copy because the worktree
changed. It ran no tests and was not counted as a pass; both completed runs used
a stable source snapshot. Local test development failures were corrected before
the final oracle runs.

## Handoff

Non-draft PR: https://github.com/TinyAssets/TinyAssets/pull/4563.

Claude reviewed implementation commit `9de0a07a06749619aa76500cf3cd2db4eae1b9d6`
through the peer-agents skill and returned **APPROVE**, with no floor or
correctness findings. It independently ran `tests/test_ta_capabilities.py`:
**33 passed, 0 skipped**, and checked the five generated runtime mirrors.
Disposition: **AGREE**. The review confirmed verified grant propagation, one
mutation guard, preserved narrow permissions, ta reachability of owner reads,
and empty non-granted inventories on both providers. The subsequent commit only
records this result and the handoff.

Non-blocking pre-existing wording/dead-code notes are recorded in
`docs/concerns/2026-10-09-k2-tool-surface-cleanup.md`. The review's observation that
any mutation grant permits extension lifecycle is the explicitly specified rule,
not a permission expansion over the baseline.

At handoff the scope guard refused solely because no Drain-Review receipt was
posted. That receipt intentionally remains with the lead; this builder did not
post a GitHub review or receipt.

No merge, deployment, live-user claim, or Drain-Review receipt is part of this
builder submission. The lead owns the receipt and subsequent production proof.
