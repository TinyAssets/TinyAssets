LANE: unblock main's merge queue. Worktree: create C:\Users\Jonathan\Projects\wf-ci-main on a new branch fix/ci-queue-empty-files
from origin/main (you may create this one worktree). Two mainline CI defects block every queued PR:
1. The merge queue's aggregate required check rejects test files that are selected but report no test cases (four
   selected files have no test definitions on main). Find the selector and the aggregate check (scripts/affected_tests.py,
   scripts/ci_required_tests.py, scripts/ci_assert_junit_case.py, the merge-group workflow), and fix the root cause:
   either such files are not selected, or a selected file with no test functions is not a failure; never weaken the
   check for files that do define tests. Also delete test files on main that define no tests and serve no purpose.
2. The runsc owner-wake job ("production boxhostd startup and owner wake through runsc") fails because its test setup
   omits UNIVERSE_SERVER_URL, which main now requires. Set it in the test setup.
Failing-first where possible. Open a PR to main with a Lane line listing every file; do not post a Drain-Review
receipt. Report the head SHA.

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
