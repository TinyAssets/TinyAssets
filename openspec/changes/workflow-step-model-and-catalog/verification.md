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
