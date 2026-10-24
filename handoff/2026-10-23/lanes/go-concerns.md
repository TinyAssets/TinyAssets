LANE: resolve stale concerns (AGENTS.md: findings live in docs/concerns/, delete = resolve; Direction 6: delete stale
docs). Worktree C:\Users\Jonathan\Projects\wf-concerns, branch chore/resolve-concerns (from main eb582d82d08).
For every file in docs/concerns/ on main, decide from the code on main (and the merged PRs it names) whether the
concern is resolved, superseded by the C6 owner-box cutover (a path the cutover deleted), or still open. Delete each
resolved or superseded concern; for each deletion cite in the PR body the commit or PR that resolved it, or the
deleted path that made it moot. Keep every still-open concern, updating only a stale file/line reference. Do not
touch the untracked concern files in the primary checkout. Examples to check: docs/concerns/c6-cli-vault-box-copy.md
(subscription CLI credentials now in broker custody, leased per run, #5227). Open a PR (docs only).

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

HARD CONSTRAINTS: no sub-agents (no scripts/peer_agent.py, no claude/codex subprocess). Work only in the worktree
named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.
