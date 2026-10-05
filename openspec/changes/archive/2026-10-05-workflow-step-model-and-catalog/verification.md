# Verification

Base: `aab7b2d1f7035a8cf5294757620c51aaab61c8c7` (`origin/main` at lane start).
Branch: `fix/workflow-step-model-and-catalog`.

Five new regressions failed before implementation: a policy-incapable bridge
silently answered, a pinned model's exhaustion selected the automatic default,
the unavailable-choice error omitted its requested model, a confirmed OpenRouter
source was absent from accepted membership, and a new cache instance lost a
completed Codex catalogue refresh. Supplementary integration tests prove both
families receive their exact model IDs and a rate-limited Codex review never
runs on Claude. The rate-limit integration already refused on the original
candidate function in this fixture; it is supporting coverage, not a claimed red test.

Initial Windows affected set: 203 passed. Cache safety additions: 10 passed in
their full file. Expanded graph checks: 72 passed. Initial Linux oracle:
Python 3.11.16, bubblewrap 0.12.0, uid 1001; 206 passed, no skips.

Review adaptations add legacy-error, prior-serving restoration and concurrent
disable preservation tests. Final consolidated results across 21 affected test
files (including affected heavy `test_llm_policy_override.py`):

- Windows Python 3.14: **355 passed**, no failures or skips (242.79 seconds).
- Linux oracle Python 3.11.16: **355 passed**, no failures or skips (80.83 seconds).
- Ruff: all changed Python source and tests passed.
- Plugin regenerated; mirror parity: all 597 canonical files matched.
- Test hygiene: **13 added, 0 removed, 0 tampering findings**.
- Strict OpenSpec validation and `git diff --check`: passed; as-built spec synced.

All pytest temporary roots were outside the repository. No real_browser tests or
app.html edits. Both founder concern files were removed after proof. Delivery is
the requested branch push only: no PR and no deployment claim.

## Round 2 repair (2026-10-05)

Started with a fast-forward pull and verified local HEAD and origin both at
`089a6ce9e857117d4c909a9336c7ddb2bde9cd74`. Source CI job 111621098967
and PR comment 5988421056 identify the provider-only allowance regression.
The existing `tests/test_work_agent_allowance.py` is unchanged.

Validation set: the full `test_work_agent_allowance.py`, all four PR-touched test
files (`test_llm_policy_override.py`, `test_native_model_options_api.py`,
`test_unify_connection_uses.py`, `test_workflow_step_model_catalog.py`), plus
affected `test_work_candidate_data.py`. The policy override file is the affected
heavy test. New parametrized coverage proves provider-only breadth, exact pins
via both model keys, and refusal to cross sources on exhaustion.

- Windows Python 3.14: **136 passed**, no failures or skips (71.31 seconds).
- Linux oracle Python 3.11.16, bubblewrap 0.12.0, uid 1001: **136 passed**,
  no failures or skips (27.79 seconds). An initial snapshot detected the archive
  move during copying and was stopped; this result is from a fresh stable-tree run.
- Ruff across all PR-touched canonical Python files and tests: passed.
- Regenerated plugin; mirror parity: **597 canonical files matched**.
- PR-wide hygiene against lane base: **14 added, 0 removed, 0 tampering**.
- Strict change and main-spec validation and diff whitespace checks: passed.

All four tasks were complete. Delta behavior is synced into the main model
selection spec, including provider-only breadth and legacy acceptance refusal.
Archived to `openspec/changes/archive/2026-10-05-workflow-step-model-and-catalog`;
no stale references to the former change-directory path were found. Fail-loudly
acceptance remains intentional; the design and user message now describe its
required recovery steps. Pytest temporary roots remain outside the repository.
