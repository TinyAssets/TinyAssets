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

## Round 3 full required-suite repair (2026-10-05)

Fast-forward pull verified HEAD == origin at `39d3cbae479571523d321eca213f2b5b3659ce80`.
All nine requested failures reproduced before edits (9 failed / 17 passed):

- `test_a_model_pin_admits_siblings_unless_the_node_declares_no_fallbacks` encoded
  removed silent substitution. Both exact-pin forms now exhaust without a sibling;
  additional assertions preserve provider-only same-source breadth.
- `test_policy_graph_preserves_context_and_holds_without_served_authority` used an
  obsolete raw callable. It now uses the production universe-bound policy bridge,
  retaining the authority error and both zero-dispatch spies.
- Seven receiver-projection cases used a policy-incapable fixture: internal entry,
  receiver default, parallel fan-in, conditional loop, shared definition, original
  entry, and disconnected component. The fixture now implements policy execution,
  asserts the selected policy/source, and refuses the plain bridge. All routing
  assertions remain unchanged.

The full run exposed three more obsolete fixtures in
`test_model_access_refresh_regression.py`: the `[False-verified-discovered]` and
`[False-last_catalogue-discovered]` refresh cases, and the hidden-model/no-snapshot
case. Advance the read clock instead of replacing a newer durable snapshot with
an older observation; clear the exact fixture's durable row as well as memory for
the no-snapshot case. All access and diagnostic assertions remain unchanged.
No production code was changed in this repair. The four complete repaired files
pass on Linux: **74 passed** (the additional sandbox probe in that run failed).

Full required selection ran through `scripts/linux_oracle.py --required-runner`
on Python 3.11.16 / bubblewrap 0.12.0 / uid 1001, with CI/GITHUB_ACTIONS true,
`TINYASSETS_DATA_DIR=/tmp/ta-data`, `not slow`, the committed heavy-file exclusions,
and all six `--shard N/6 --profile shard` selections. Basetemp: `/tmp/b`.
Shards 2 and 3 were rerun completely after fixture/documentation corrections;
the final union uses those results and the unchanged other four shards.

| Shard | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| 1 | 3684 | 0 | 5 |
| 2 | 3631 | 1 | 52 |
| 3 | 4195 | 1 | 6 |
| 4 | 3470 | 0 | 1 |
| 5 | 4000 | 0 | 21 |
| 6 | 7449 | 0 | 10 |
| Total | 26429 | 2 | 95 |

Comparison main was pinned to `e5de8d18b27280f2cdf2d4af3dd0417e765b601c`:

- `test_app_pending_requests_browser.py::test_pending_requests_at_latest_and_new_arrival_answer`:
  final `[False-390]` failure reproduces on main. Initial branch failures
  `[False-1280]` and `[True-1280]` also reproduce on main; those two passed the
  corrected branch rerun. All fail the old-scroll-position assertion.
- `test_provider_served_router.py::test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`:
  unresolved branch-suite failure. It passes alone AND in main's complete shard 2
  (3761 passed / 50 skipped; JUnit confirms this case ran). Branch reproduced three
  times; handoff per AGENTS.md, without changing guards or assertions:
  `docs/concerns/2026-10-05-served-router-sandbox-suite-failure.md`.
- The three original files pass on main (60 passed); the catalogue file and
  isolated sandbox case pass together on main (15 passed).
- Two initial rulebook-size failures were caused by putting this run's new report
  in `docs/reviews/`. Moved that new report outside the repo; all 19 rulebook tests
  pass locally and on main, and the corrected shard 3 clears both failures.

**The required gate is red**, not a full-suite pass. Its aggregate fails closed
on failed shard jobs. The union's own outcome/cost readers report 26,431 ran,
95 skips (budget 134), and 5,129.448 summed test seconds (budget 3,000). No budget,
quarantine, exclusion, skip, or test-removal edits were made. Main's full shard 2
took 1055s versus branch's 1050s; no whole-main timing comparison is claimed.

Ruff and mirror parity passed (597 files); repair and PR-wide hygiene both report
**0 removed / 0 tampering**. One cross-family Claude review of the original
three-file repair: APPROVE. AGREE with its context-docstring clarification;
wording corrected, plain-bridge forwarding coverage unchanged.

Evidence lives outside the repo under `C:/Users/Jonathan/Projects/`:
`wt-4474-required-current/` contains the final six JUnit/manifests and merged union;
`wt-4474-required-final/` and `wt-4474-required-recheck-{2,3}.log` hold full logs;
`wt-4474-main-required-2.log`, `wt-4474-main-shard{2,3}-failures.log`,
`wt-4474-main-targeted.log`, and `wt-4474-linux-fixed.log` hold comparisons.
The aborted initial Linux source copy is not counted as test evidence.
