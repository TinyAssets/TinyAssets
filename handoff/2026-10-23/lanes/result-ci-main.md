Opened [PR #5246](https://github.com/TinyAssets/TinyAssets/pull/5246) to main. Head SHA: `2d66e023d3c82212463cd2645016acbe5a45c00e`.

The selector now excludes helper-only files from the merge queue’s required coverage list while still selecting tests that import them. The aggregate check remains strict for selected files that define tests. I also removed three obsolete test files and set `UNIVERSE_SERVER_URL` in the synthetic runsc host setup.

Verification: the selector regression failed before the fix and passed afterward; 48 targeted workflow tests passed through `linux_oracle.py`; Ruff and `git diff --check` passed. The PR’s CI, including the runsc proof, is still pending.