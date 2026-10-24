Draft PR: [#5244](https://github.com/TinyAssets/TinyAssets/pull/5244)

- Head: `d291f90479847597bfce4bc859733e902db98e21`
- Moved all 27 declarations into storage modules; S10 guard is zero.
- Added durable reconciliation and cutover row-preservation coverage.
- Targeted Linux oracle: **148 passed, zero skipped**. Ruff and commit hooks passed.
- Worktree clean. Production migration remains unrun, per your constraint.