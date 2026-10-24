Fix round on PR #5238 (worktree C:\Users\Jonathan\Projects\wf-deploy-fix, branch fix/deploy-box-bundle). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-deploy.md
Goal: "nothing reports success over a mixed tree" holds on EVERY path. Fix each finding with a test that fails first:
1 HIGH: every failure exit after install_bundle runs the restore (one trap or one exit function, not per-site calls).
2 HIGH: the host snapshot covers every path the host bundle installs, derived from the same install manifest
  (boxhostd_install_files.py / host_service_bundle.py), so a new installed directory can never be missed again;
  test that snapshot paths == install paths.
3: write the dirty marker BEFORE destructive restore steps, clear it only after a verified restore; never rm -rf a
  live tree before the copy-back is staged (stage then rename).
4: make the env restore match DEPLOY.md, or correct DEPLOY.md to what it does, and restore env with the bundle.
5-6: queue time must not consume the broker's request deadline; waiting for a slot must not hold the broker's shared
  thread pool (wait asynchronously, or bound concurrent waiters below the pool size with request wakes reserved).
7: the readiness signal reports ready only when no post-drain lifecycle work is pending or running (count pending
  work, not just active calls); bounded wait.
8: the box image selection (/var/lib/boxhostd/state/images.json) is part of the snapshot/restore.
9-10: fix tests that skip or mock away the behavior they claim to cover, and the small issues.
Push to the same branch.

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
