# Account for the native metadata jail proof in the ordinary-shard budget

2026-10-04: merge-group run 37188645443 completed all six shards with zero test failures but failed the aggregate at 128 skipped tests against a budget of 127. Its tree combined #4404 and #4425. The last passing main-equivalent run 37187949417 contained 127 skips.

Set-comparing the two junit-required-tests artifacts identifies exactly one added skip: tests.test_native_metadata_jail::test_native_metadata_snapshot_cannot_read_foreign_or_platform_state. The ordinary shards do not provide bubblewrap; the new case is registered with the existing linux-jail-proof job and its no-skip assertion. All previous skip conditions remain unchanged.

This update changes only MAX_REQUIRED_SKIPPED from 127 to 128, with the measured reason beside the constant. Time, quarantine and execution floors are unchanged. No test is removed, weakened, newly marked skipped or removed from a selection. This is explicit reviewed accounting for the added proof, not a claim that the ordinary shards execute it. The separate current-head Linux proof must pass before landing this PR.

The isolated integration merges main de40b9b42c into the already-reviewed native metadata head d84fa86ab9 without conflicts or authored merge-resolution edits. It includes #4429's consent-sidecar fixture repair.

Validation, 2026-10-04:
- Windows/Python 3.14: python -m pytest -q -p no:randomly tests/test_ci_required_tests.py tests/test_linux_jail_proof_workflow.py: 116 passed. Ruff clean.
- Linux oracle/Python 3.11.16, uid 1001, bubblewrap 0.12.0: python scripts/linux_oracle.py --out <external-proof-dir> -- tests/test_native_metadata_jail.py tests/test_universe_tools_jail.py -q --junitxml=/out/junit.xml: 23 passed, zero skips, including the newly budgeted native isolation case and all tool-jail cases.
- The existing gate boundary test still exercises refusal above the skip and seconds budgets. Hosted CI for the final source head remains required. No deployment is claimed.
Independent Claude review approved the pure main merge and measured budget adjustment at `06e2ebb56b`: its own JUnit set comparison found exactly the native case added and no removed skips; six budget tests passed. Hosted source runs 37191691078 (Linux jail, zero-skip assertion) and 37191691029 (six affected shards) subsequently passed on that head. The final documentation-only commit syncs and archives the unchanged delta and preserves deployment/live-vendor acceptance in the existing concern; it changes no runtime, tests or CI.
