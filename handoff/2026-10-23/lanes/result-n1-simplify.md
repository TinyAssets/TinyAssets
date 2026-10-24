Pushed to `spec/n1-inbound-door` and updated [PR #5228](https://github.com/TinyAssets/TinyAssets/pull/5228).

Head: `9903b9851ebfa6b88aa9c7c4391e5ea4fed05a52`

- Removed general selectors; kept fixed Slack extraction, conditional per-account WhatsApp callbacks, and deferred Teams.
- Specified installation authority, OAuth/conflict handling, authenticated no-ops, proof stripping and wake reconciliation. Documents are 19% shorter.
- Passed: Linux oracle `tests/test_shape_registry.py` (**21 tests**), strict OpenSpec validation, ruff, diff checks and commit hooks. No JS changed.
- Protocol fixtures, implementation, spec sync and live proof remain unchecked because this is spec-only. No production access or sub-agents. Worktree clean.