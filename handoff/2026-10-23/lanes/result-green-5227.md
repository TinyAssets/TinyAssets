Pushed **`5dc5d93bf37e8e329f1d5dbef696f880873b8b4a`** to [PR #5227](https://github.com/TinyAssets/TinyAssets/pull/5227). Worktree is clean.

- Merged `origin/main` through #5217. **No conflicts.** Git automatically combined `deploy/deploy_fail_safe.sh`, preserving both branches’ changes.
- Fixed structural inventories, deferred browser imports, declared browser proof paths, and added missing `verifies` markers.
- Removed the provider-specific discovery fallback caught by the ratchet. Updated its test for generic discovery and explicit custom paths.
- Adapted tests to the branch’s intentional schema-helper consolidation and deployment ownership changes, retaining assertions.
- PR `Lane:` lists all 180 changed files.

| Check | Result |
|---|---|
| structural-guards | Green — 769 passed |
| affected-tests 1–4 | All green — 2,111 passed, 25 accepted skips |
| required-tests | Green |
| lint / invariants | Green |
| Diff scope declared | Lane and hygiene pass; **lead receipt missing** |
| reconcile | Blocked by duplicate issues #4657 and #5224 |

Failing tests were reproduced through the Linux oracle before fixes. Targeted reruns included browser/discovery tests and deployment tests; the deploy transaction file passed all 34 cases in its required root venue. Ruff and `node --check` passed. No full suite, production access, deployment, or Drain-Review receipt. The PR remains draft.

The **unchanged deploy guard** works as follows:

1. Reads `id`, `status` (or legacy boolean `active`), `files[].path`, `files[].after_sha256`, container metadata, and optional `pr`. Legacy path→hash maps are normalized; IDs matching `hf-<number>-...` supply a default PR number.
2. Accepts either **exact candidate-file SHA256 matches**, or a PR that is **merged into main**, whose merge-tree files match every recorded `after_sha256`, and whose merge commit is an ancestor of the candidate revision.
3. Binds the proof to the entire normalized record and candidate OCI revision. Changed records, pending records, or a different image revision refuse deployment.
4. Requires recoverable applied hotfix bytes before mutation. Successful deployment subsequently archives records after revalidation; rollback restores those bytes.

**A label-valued `commit` needs no conversion:** the guard does not use it for ancestry or supersession, although it participates in the whole-record fingerprint. Operators must preserve valid hashes, container metadata, originals, and applied-byte recovery copies, then provide matching candidate bytes or the valid merged-PR proof. Merely changing `commit` to a SHA—or including a refactored equivalent in this branch—does not satisfy the guard. If neither proof route works, the hotfix must be properly reverted before deployment.