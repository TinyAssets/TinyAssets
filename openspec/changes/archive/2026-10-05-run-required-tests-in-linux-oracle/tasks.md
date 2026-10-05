# Tasks: run-required-tests-in-linux-oracle

Tasks 1-4 landed in PR #4316. The founder authorized the permanent venue on
2026-10-04 in PR #4316 comment5984775429, superseding the one-off #4398 scope.
Tasks 5-6 completed after #4459 landed. The full-first, selective-follow-up
sequence was agreed with the lead in comment5985466724.

- [x] 1. Oracle image: `browser` extra and Chromium at `/opt/playwright`, readable by uid 1001 (`docker/linux-oracle.Dockerfile`).
- [x] 2. `scripts/linux_oracle.py --required-runner`: fixed script, arguments unchanged, short basetemp, refuses weaker venues and unseen outputs; default pytest command unchanged. Tests assert the generated argv and output paths.
- [x] 3. `real-browser-proof`: marked cases and the full `tests/test_ui_preview.py` module in the oracle, with no-skip assertions and both junit artifacts; ordinary triggers kept.
- [x] 4. `required-tests-shard`: both paths through `--required-runner`; plan step, matrix, artifacts and aggregate unchanged. Workflow tests pin the venue, the argv and the absence of host privilege.
- [x] 5. Observe one `real-browser-proof` run and one whole-surface six-shard run on the implementation, then one selective merge-group run on the immediate documentation follow-up (the five items in `design.md` D5). Fix or quarantine through the existing ledger anything the gate executes for the first time and finds red; do not raise the skip budget.
- [x] 6. After the runs are green: independent cross-family review (gate file), then sync this spec and archive.

Hosted evidence (2026-10-05 UTC): PR #4316 merged as
`51db6894f30710ee9e00d816293fbbd76eff88cf`. `gh run view 37245286729`
reports the full required run successful: all six oracle shards passed,
26,297 tests ran, no new failures, 95 skips against 134, and 2,586 summed seconds
against the separately reviewed provisional 3,000-second ceiling. Browser run
`37245237518` at source `87df68e62b` also passed: 131 marked browser cases and
36 full-preview cases, no skips. Selective merge-group run `37246536654` passed
4,908 tests with no new failures, 77 skips and 605 summed seconds; its six
JUnit file sets cover exactly 172 selected files without overlap, with one
matching selection digest across all manifests. #4459 merged as
`9e96ff95959499cd2ec6fda9e0761b9d498240dd`, syncing the main spec. The independent
Claude acceptance review approved all five D5 items; see `gate.md`. This closes
only the CI venue change; `custom-ui-assets` retains its live founder acceptance.

## PR #4316 browser reload investigation (2026-10-04)

Merge-group run 37240184676 failed the visual-preview test after reload. This
was a test synchronization race: `AppUI.choose()` mounts the screen before
`remember()` finishes saving its selection. The heading proves rendering, not
persistence; reloading at that point can cancel the save and restore the default.

The unchanged focused test passed locally on Windows and in the newly built
oracle image `tinyassets-linux-oracle:724828e06295`. A temporary diagnostic that
delayed only the selection-save RPC by 500 ms reproduced the identical missing
heading after reload in both venues (Windows 9.97 s, Linux 9.89 s). Before reload,
both reported `busy: true`, client selection `null`, and database selection
`None`, despite the visible heading. The diagnostic delay is not retained.

The test now holds that RPC with an explicit promise, proves the screen renders
while the stored selection remains unchanged, releases the RPC, and requires the
existing success status plus the saved selection before reloading. All original
visibility/consent/isolation assertions remain; no timeout, skip or xfail changed.

The provenance exception is deliberately injected by this test's monkeypatch,
not an image or UID permission failure. `after_install()` catches it and returns
the unavailable-history receipt that the test asserts. The real registry uses
`_agent_connect(base)` and the same `.tinyassets.db` as the UI; the autouse fixture
pins `TINYASSETS_DATA_DIR` to the test user's temporary directory. Searches of
`record_install`, its failure injection, and browser reloads found the separate
history-failure test and adapter tests; neighboring selection/reload cases already
await the save status. No product/image change or plugin regeneration is needed.

Validation runs use `scripts/linux_oracle.py --apparmor ta-jail-userns --env
TINYASSETS_DATA_DIR=/tmp/ta-data`, with its UID 1001, real bubblewrap probe,
seccomp/systempaths options, `/opt/playwright` Chromium and `/tmp/b` basetemp.
Docker Desktop accepts the named AppArmor option but its kernel does not advertise
AppArmor enforcement; Ubuntu CI remains the proof of that kernel-specific policy.
Final validation: the complete `test_command_center_system_browser.py`,
`test_command_center_update_surface.py` and `test_command_center_update_adapter.py`
modules passed together: **62 passed, zero skipped** in the oracle (97.08 s,
Python 3.11.16) and **62 passed, zero skipped** on Windows (140.94 s, Python 3.14;
four existing LangGraph deprecation warnings). Ruff on the touched Python file
and `git diff --check` passed. This is focused local proof, not a claim that all
six CI shards, Ubuntu AppArmor enforcement, or deployment have completed.
