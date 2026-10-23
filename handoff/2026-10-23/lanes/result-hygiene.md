All four PRs **merged to main**. Production deploys are disabled; no deployment was run.

| PR | Head SHA | Result |
|---|---|---|
| [#5205](https://github.com/TinyAssets/TinyAssets/pull/5205) | `b5c4bb9a7cc19c3e8ea83752f01c18b4acc129e3` | Fixed the Lane declaration; required checks passed. |
| [#5212](https://github.com/TinyAssets/TinyAssets/pull/5212) | `3464c92eb552c9bc925233966a8daef7911dd247` | Fixed the Lane declaration and review receipt; required checks passed. |
| [#5222](https://github.com/TinyAssets/TinyAssets/pull/5222) | `ed39571b35c1ec2ec3baff776afe673edd9ea0de` | Removed the obsolete ratchet assertion. Targeted Linux oracle: **21 passed**; merge group passed on retry. |
| [#5217](https://github.com/TinyAssets/TinyAssets/pull/5217) | `1a52a0fdfb5ed46ee5fe2b304077ad47255daf54` | Dropped the AGENTS.md hunk, declared the hotfix test ID, and rebased after #5222. Tool and guard behavior are unchanged. Targeted Linux oracle: **70 passed**; Ruff, required CI, and runsc rehearsal passed. |

The separate **reconcile** check remains red because two “Shape reconciliation” issues exist; I left that external condition untouched. #5222’s first merge group also hit an intermittent `test_owner_volumes_real_mounts` failure; the unchanged retry passed. Temporary worktrees were removed.