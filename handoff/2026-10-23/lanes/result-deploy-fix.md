Draft PR [#5238](https://github.com/TinyAssets/TinyAssets/pull/5238) is open against `main`. Head SHA: `910c891e6bbb17cb3e8212da9251cbb4b3b50e83`. The worktree is clean.

The change queues lifecycle calls in boxhostd with request-wake priority, waits for post-drain readiness before canaries, restores the box-host bundle and `harness-current` with compose, and preserves the broker’s outbound HTTP flag.

Targeted Linux oracle runs passed: **147 tests**, **2 root-only cutover tests** run separately as root, and focused runs of **34** and **43** tests. Ruff, shell syntax, diff checks, and workflow YAML parsing passed. PR checks are still running; local `actionlint` was unavailable. No production deployment was run, as requested.