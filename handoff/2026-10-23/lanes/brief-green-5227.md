Make PR #5227 (branch conv/source-records, worktree C:\Users\Jonathan\Projects\wf-source-records, head 9a6aa95c904)
mergeable to main. Production deploys are disabled; merging does not deploy. The branch is reviewed: do not change
product behavior beyond what a failing check proves is broken.

1. Merge origin/main into the branch (main now has #5205, #5212, #5222, #5217). Resolve conflicts keeping both sides'
   intent; report each conflict and how you resolved it.
2. Make the required checks green: structural-guards, affected-tests 1/4 (and any other shard), required-tests, Diff
   scope declared (fix the PR body's Lane line to list every changed file and any verifies ids it requires; read
   .github/workflows/pr-scope-guard.yml and scripts used by it). Reproduce each failing test with
   python scripts/linux_oracle.py -- <file> before fixing. Fix real breaks in the code; update a test only when the test
   asserts behavior this branch intentionally changed, and say so. The "reconcile" check is an external condition (two
   Shape reconciliation issues): leave it and report it.
3. Do NOT post a Drain-Review receipt; the lead posts it.
4. Read the deploy guard #5217 added (scripts/hotfix.py and its deploy hook) and report exactly how it decides that an
   active hotfix record is superseded by a deploy (record fields it reads, how it compares commits or file hashes), and
   what an operator must do so a deploy of this branch's image is not refused by records whose "commit" field is a
   label instead of a sha. Do not change the guard.
Report head SHA, check status, and the guard answer.
