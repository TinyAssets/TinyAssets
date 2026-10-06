# K1 merge-queue repair

PR #4519 remains draft. Final catch-up: main dd82fd3d4a merged as 442743f2b5; final-tree full verification is rerunning in `C:/Users/Jonathan/k1-final-verification`. The counts below currently describe the preceding b87bf5956c tree. `origin/main` was fetched and merged before repair (already contained). Original head: b3dfa3ca88. Initial Linux reproduction: 15 failed, 393 passed, no skips.

## Root fixes

- App reads / remaining MCP actions: the extension revocation read now uses `Owner.read`, preserving complete owner replies and account-switch checks.
- Background authority inventory: classify `RemoteMcp._exchange -> self.stream` and its plugin mirror as the governed remote-MCP connection transport, not a new graph execution root.
- Connect discovery, served systems guidance, guidance preservation / wrong first call, share-after-publish: restore the original resident chapter index, starter skill paths and byte-preserving base64 warning. No prompt budget changes.
- Custom UI discovery: restore the complete `tinyassets.app-ui.v1` component example and update semantics. Extension activation/pinning explanation stays in the on-demand interfaces chapter.
- Full channel access / git scope as verb: preserve the scoped-verb refusal contract on the non-stream HTTP path; binary git IPC remains separately validated and credential blind.
- Control-plane inventory: classify `git_upload.Upload.read` as CALL_SCOPED. Its bounded wait rechecks cancellation, deadline and grant authority and ends with the broker call.
- Onboarding route set: register K1's already-mounted `/app/outside-clients` in the exact route inventory and assert POST-only. It requires an interactive owner session and current owner authority.
- Orphaned answered turn: completion hooks run inside the initialized `_run` lifecycle; `run` retains its unconditional release wrapper over arbitrary completion/failure bodies.
- Storage registry: classify `.outside-client-authority.sqlite3` as platform authorization bookkeeping (grants, revocation generations and effect leases), never quota-gated.

## Verification before the final main catch-up

- Linux regression set: 421 passed, zero skips, including all 15 failures, prompt budgets and extension hooks.
- Gate selection: `python scripts/affected_tests.py --gate --base origin/main` returns ALL. Advisory selection also returns ALL because auth middleware is in conftest import closure.
- Full CI required selection, all six shards: **27,240 passed, 1 failed, 96 skipped**, 10 slow cases deselected across the shards. The sole failure is `test_provider_served_router::test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`, already tracked by `docs/concerns/2026-10-05-served-router-sandbox-suite-failure.md` and the 2026-10-04 fake-Codex concern. It is not quarantined. No full-green or zero-skip claim.
- Every reported guard suite passes in that full run: **408 passed, zero skips**, plus **10 prompt-budget tests and 9 learned-catalog authority tests**, all passed on the final code.
- Diagnostic comparison: the provider-router module passes in isolation on both K1 and an `origin/main` archive (**35 passed, 1 existing live-Codex skip** on each). The baseline sequence of 100 existing files through that module passes **2,572 tests with 48 existing skips**. This does not identify a root cause. The complete K1 shard-2 diagnostic replay passed **4,033 tests with 50 existing skips**, including the formerly failing case. This recurrence remains handed off to the existing concern, without changing routing, sandbox guards or quarantine.
- Replay union (the original other five shard reports plus the complete second shard-2 report; original failure retained): **27,241 passed, 0 failed, 96 skipped**, plus 70 passed subtests. The actual CI aggregator reports no new failures but **fails its summed-time guard: 5,421 seconds / 3,000 seconds**. Neither timing nor skip budgets were changed. This is not full CI green.
- The zero-skip requirement is **not satisfied** by the full selection: the oracle has existing Windows-only/inverse-platform proofs, missing Docker Compose (60 cases), shellcheck (3), scipy/sklearn (3), PostgreSQL integration configuration (7), history-dependent and opt-in live integration skips. No skip was added or converted to a pass.
- Linux CI slow selection: 10 passed, zero skips, 29,495 non-slow cases deselected by the unchanged CI marker.
- Affected heavy selection (all 43 listed files, raw pytest): 2,132 passed, 59 failed, zero skips. All 59 exact failing IDs are already in `.github/known-failing-tests.txt`: 42 in `test_deploy_prod_workflow.py`, 17 in `test_retire_cheat_loop_deploy_fence.py`; no unlisted failures. These are tracked by `docs/concerns/2026-08-27-full-tests-permanently-red.md`. No quarantine entry or test was changed.
- Plugin rebuild and import probe pass. Ruff checked across changed Python files.
- Claude cross-family review: ADAPT, one finding AGREE. The `_run` split moved owner initialization out of the method the learned-catalog authority guard inspects. Restored `self.owner = self._check_scope()` at the start of `_run`; no guard edits. Its nine tests pass locally; the full Linux run was restarted as the six CI shards with this correction. Reviewer verified the agreed one-line correction and returned APPROVE; no additional findings. Review and disposition confirmation: `review-k1-merge-queue-claude.md`.
- Supplement: every test file named inventory/guidance/storage, 552 passed, zero skips (6 deprecation warnings).
- Hygiene: 74 added test functions, 0 removed, 0 tampering.

This repair does not claim deployed SHA or real-user acceptance. No guard was removed, skipped, xfailed or weakened. The only guard edit extends the exact route inventory with a POST assertion.

## Exact full-selection venue

`python scripts/affected_tests.py --gate --base origin/main --out <outside-repo>/affected.txt` selected `ALL`; the advisory selector also selected ALL. For every `N` in 1..6:

```text
MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py --out <outside-repo> --required-runner -- --junit /out/required-N.xml --exclude-from .github/heavy-test-files.txt --affected /out/affected.txt --profile affected --shard N/6 --pytest-arg=--basetemp=/tmp/b
```

This runs the real CI test runner, Python 3.11.17, uid 1001, bubblewrap 0.12.0, with a successful jail probe. The selection file and raw reports are in `C:/Users/Jonathan/k1-verification`; logs are `C:/Users/Jonathan/k1-required-N.log`. Supplementary suite file names are recorded in `supplement-files.txt`. The heavy run uses all 43 entries of `.github/heavy-test-files.txt`, raw pytest `-q -m "not slow" --basetemp /tmp/b`; the slow run uses CI's `-m slow` and its two headless-import exclusions. No root-mode, no-bwrap or test-weakening workaround was used.


## Final main catch-up verification

Main dd82fd3d4a merged cleanly as 442743f2b5. Claude approved the resulting
coordinator/runner bookkeeping integration, with no finding; its focused
orphan-ready coordinator file passed 3 cases. Plugin mirror rebuild/import
probe and Ruff across all changed Python files pass.

The merged-tree expanded supplement (all inventory/guidance/storage files,
reported guard suites, prompt budget, hook and learned-catalog authority,
plus the new orphan-ready coordinator/turn guards) passed **936 tests, zero
skips**, with six deprecation warnings. Full required shards are rerunning
with CI=true, GITHUB_ACTIONS=true, TINYASSETS_DATA_DIR=/tmp/ta-data and the
unchanged CI ALL profile; at most two required shards run concurrently.
Final required, heavy and slow results are pending.
