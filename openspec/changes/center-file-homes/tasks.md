## Implementation
- [x] Define canonical file homes and derive inventory classification.
- [x] Route host pause, heartbeat and ledger readers/writers through the host adapter.
- [x] Route soul presence through admitted owner reads and preserve requested center names.
- [x] Port focused regressions, scoped guards and required fixture updates.

## Validation
- [x] Validate this OpenSpec change strictly.
- [x] Run CI affected selection on Linux and resolve or prove every failure on origin/main.
- [x] Run CI structural guards and control-plane inventory on Linux.
- [x] Run ruff on changed Python files and git diff --check.

Validation receipts (2026-10-16): all 1,230 CI-selected files ran on Linux:
24,658 passed, 22 failed, 102 existing environment skips initially. All 22
failures were fixed and passed targeted reruns; the final 28-file fixture-dependent
selection passed 886 tests with 3 existing skips. CI structural guards passed 824
with no skips (control-plane inventory: 4); the new PR-A tests passed 109 with no
skips. Affected heavy coverage passed after one explicit-owner fixture correction;
first-contact passed 58. Ruff (32 changed Python files), diff whitespace checks,
and strict OpenSpec validation passed. No failures were waived as pre-existing;
the clean temporary main comparison worktree was removed. Full file list, failure
node IDs and JUnit receipts: system temp `c6-center-file-homes-results/results.md`.
