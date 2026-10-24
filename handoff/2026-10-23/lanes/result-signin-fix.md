Pushed the fix round to PR #5231 at **`ccf201c2ba4a2173bf3ce90dc75e428e0531aeab`**. The worktree is clean.

- Native and desktop approval prompts no longer offer a link that runs app re-login. They tell the owner to open TinyAssets in a regular browser with the same account.
- Starting another inline connection cancels the previous flow; expired flows clear provider-side custody when the server next accesses them.
- Finding 3 remains unchanged. The two older desktop flows still lack a verifier handoff through the existing scheme return; that limitation is recorded in the PR body.

The focused tests failed before the fixes. The targeted oracle run passed **45 tests**; the revised focused run passed **14**. Ruff, `node --check`, `git diff --check`, and commit hooks passed. Physical native and installed desktop verification remains undone because this round had no production access or real sign-in.