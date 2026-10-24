LANE: finish N1, the one inbound door. Worktree C:\Users\Jonathan\Projects\wf-n1-impl, branch feat/n1-inbound-door
(draft PR #5235), now merged with the approved one-durable-send branch conv/c11-durable-send (PR #5237). Your previous
round left these undone (see the PR body): the route still uses the old direct dispatcher; receipt-keyed durable
session.start, route cutover, per-installation breaker, wake reconciliation, migration and the remaining spec proofs.
Durable admission now exists (#5237): use it so an inbound event is acknowledged only after it is durably admitted as a
session.start input keyed by its receipt (no loss, no duplicate session on redelivery). Cut the route over to the one
door and delete the old direct dispatcher in this PR. Implement the breaker, the authenticated no-op rows, wake
reconciliation against the owner-filtered routing snapshot, and the spec's remaining tests, following
openspec/changes/<the N1 change>/tasks.md as the checklist. WhatsApp, Teams and email stay behind their fixture gates.
Mark tasks done only when their test passes. Push to the same branch.

RESUME NOTE: a previous run of this round was interrupted (system memory). Its partial edits are committed on the branch as "WIP: interrupted lane". Review that commit, keep what is right, finish the round, and replace the WIP commit message by squashing it into proper commits before pushing.

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
