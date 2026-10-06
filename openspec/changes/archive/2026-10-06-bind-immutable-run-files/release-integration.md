# Combined release assembly — not yet the usable upload release

September20,2026 UTC. Root-owned isolated worktree wf-file-upload-release,
branch codex/file-upload-release. Integration commit
df2e641f118041e62ae4d52c1512c0626def3145 merges frozen upload backend
beee572ac3de22d3ca70a2d9db42604a75bcb2f7 with reviewed consumer successor
229094c4aadf0b77b50941739e8356f9dcabc20b. Neither experimental cloud
containment nor the unfinished UI candidate is included. No push or PR yet.

The only conflict was tests/test_delivery_account_deletion.py's maintenance
AST fixture. Resolution is byte-exact reviewed backend blob
3a86fa1b3fe955736465ad162ae000b9e2d860a7: it preserves both admission/file
cursors, independently failed maintenance cases and continued budget ticks.
No test was deleted, weakened or skipped. Canonical tinyassets and plugin
runtime are byte-identical to beee572a after this merge; git diff --quiet exit0.

## Executed integration evidence

- Windows Python3.14, `python -m pytest -q
  tests/test_delivery_account_deletion.py tests/test_run_recursion_limit.py
  tests/test_loop_telemetry.py`:34 passed,8.88s.
- Windows cohort: `python -m pytest -q tests/test_app_file_upload.py
  tests/test_delivery_account_deletion.py tests/test_onboarding_app.py
  tests/test_onboarding_model_preferences.py tests/test_run_input_origins.py`
  plus all17 `tests/test_run_file_*.py` files from rg inventory:
  **371 passed,1 POSIX-specific skip**,145.31s,exit0.
- Same22-file cohort through canonical Linux oracle, WSL Ubuntu Docker29.1.3,
  container Python3.11.16/git2.47.3/bwrap0.12.0:
  **372 passed,zero skips**,91.12s,exit0. The previously skipped file-stream
  symlink/FIFO test executed here. No new failure or ledger edit.
- Linux command: `wsl -d Ubuntu --exec python3
  /mnt/c/Users/Jonathan/.codex/worktrees/0a7f/TinyAssets/output/root-upload-integration-linux-oracle.py
  -- -q -rs <the same22 files>`; helper imports the integration tree's canonical
  linux_oracle and overrides only _repo_root to its explicit WSL path because
  the worktree gitdir is Windows syntax. No oracle behavior was changed.
- `python scripts/check_mirror_parity.py`:496 matched. `git diff --check`:pass.

## Remaining gates

Merge the frozen Opus UI successor only after real fetch cancellation, saved
queue ownership and same-label metadata-only reload checks are implemented.
Retain both test_app_file_upload.py and test_app_file_upload_ui.py, plus both
onboarding test hunks. Rebuild mirrors/brand receipt and run combined actual-JS,
route and custody regression checks. Backend reviewbeee does not approve the
future combined head. Obtain independent exact-head review, scoped canonical
spec sync, hosted CI, configured global custody capacity, verified deployment
and protected public gates. Ordinary app upload/downstream and cross-owner
use must succeed without operators editing private workflows. No private-user
test account reset/retry is authorized by this local integration evidence.
