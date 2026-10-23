Fixed both new findings and pushed to the PR #5237 branch. Queued sends stay pending through drain or stop and resume exactly once on startup. Pre-cutover consumer runs can project their transcript once through the existing box-local writer. Both fixes had failing-first tests.

**Head:** `c8860ede6e1fb6436fe67f4409a8ba994205eea6`

**Checks:** 38 targeted Linux oracle tests passed; Ruff, `git diff --check`, and commit hooks passed. Nothing remains undone.