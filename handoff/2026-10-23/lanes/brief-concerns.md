LANE: resolve stale concerns (AGENTS.md: findings live in docs/concerns/, delete = resolve; Direction 6: delete stale
docs). Worktree C:\Users\Jonathan\Projects\wf-concerns, branch chore/resolve-concerns (from main eb582d82d08).
For every file in docs/concerns/ on main, decide from the code on main (and the merged PRs it names) whether the
concern is resolved, superseded by the C6 owner-box cutover (a path the cutover deleted), or still open. Delete each
resolved or superseded concern; for each deletion cite in the PR body the commit or PR that resolved it, or the
deleted path that made it moot. Keep every still-open concern, updating only a stale file/line reference. Do not
touch the untracked concern files in the primary checkout. Examples to check: docs/concerns/c6-cli-vault-box-copy.md
(subscription CLI credentials now in broker custody, leased per run, #5227). Open a PR (docs only).
