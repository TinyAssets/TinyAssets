# Quota ledger concurrency prerequisite

Current main: `16f0ca16de1db0e1a17e96c30604a3dafae0fd26`.
Independent preimplementation review `quota_split_design_review`: AGREE.

## Contract

1. Refresh stale store measurements before fitted admission. Read usage, fit the
   requested cap/credit, validate minimum and available quota, and insert the
   pending row inside the same `BEGIN IMMEDIATE` transaction. Concurrent callers
   receive the capacity left by earlier admissions instead of refusing a stale
   fit. Preserve account/store membership, credit and unattributed behavior.
2. Allocate a unique sequence before each measurement scan. A later successful
   scan must not be overwritten by an earlier scan, even when raw jail writes
   have not advanced the ledger's committed-write sequence. A failed newer scan
   publishes nothing and must not suppress an older successful scan.

No runtime exclusion, headroom policy, launch allowance, settlement, supervisor,
request-budget accounting, or deployment policy changes. Full-account recovery
and all existing grace tests remain unchanged. This does not complete #4403.

## Regressions and review

Use temporary data only. Reproduce concurrent fitted refusal and out-of-order
raw-write measurement on unchanged main before implementation. Cover same and
different owners, retained bytes, minimum refusal, replacement credit, over-full
accounts, commit-during-scan and failed-newer-scan ordering. Retain all existing
test definitions. Run accounting, jail/recovery and workspace quota tests;
check mirrors, lint, and independent exact-head review before handoff.

## Evidence

On unchanged main the new regressions produced two failures: concurrent fitted
requests both selected 60 KiB and one refused despite 40 KiB remaining; an older
raw-write scan replaced a newer 40 KiB result with zero. Independent-owner and
failed-newer-scan controls passed. The latter test was then strengthened with
10 KiB present only when the old scan starts, proving it actually publishes.

After implementation: 73 accounting, workspace slice-gate and jail-disk cases
passed. The strengthened ordering pair and both #4408 provider startup-recovery
cases passed with imports pinned to this worktree. Original tests are retained.
No call-site/runtime policy change is enabled. Current main does not yet contain
#4410; that separately reviewed lease-loss prerequisite remains independent.

Current integration contracts are recorded at
https://github.com/TinyAssets/TinyAssets/pull/4403#issuecomment-5974858398.
Code approval does not authorize deployment of a new confinement policy.
