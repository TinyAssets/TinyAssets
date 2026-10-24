PR HYGIENE (repo TinyAssets/TinyAssets, use gh). You may create a temporary git worktree per PR branch under
C:\Users\Jonathan\Projects\ (name wf-hyg-<pr>) and remove it when done; the base worktree is
C:\Users\Jonathan\Projects\wf-pr-hygiene. Goal: get these four PRs green and merged to main. Production deploys are
disabled, so merging does not deploy.

- #5212 (test fixture, synthetic cutover archive broker directory ownership) and #5205 (CI: empty affected shards
  pass): the only red check is "Diff scope declared". Read how that check parses the PR body (find it under
  .github/workflows and scripts), fix each PR body with `gh pr edit` (Lane line listing every changed file, verifies
  ids if required), re-run the check, then enable auto-merge (`gh pr merge N --auto --squash`).
- #5222 (deletes AGENTS.md "Live state" and "The loop" plus supervisor hooks): red because
  tests/test_rulebook_ratchet.py::test_the_agents_loop_says_a_new_rule_deletes_an_old_one asserts text that the PR
  deletes. Delete that test (or the whole ratchet if every assertion in it targets deleted text), push to the PR
  branch, fix Diff scope, get green, keep auto-merge on.
- #5217 (scripts/hotfix.py reversible hotfixes plus a deploy guard): drop its AGENTS.md hunk (Direction 6: no new
  process rules; #5222 deletes that section), keep the tool and guard, rebase onto main after #5222 lands, fix Diff
  scope and test ids, get green, enable auto-merge. Do not change the tool's behavior.
If a check is red for a reason outside these PRs (e.g. a flaky or pre-broken job on main), report it with the failing
test name instead of working around it.
