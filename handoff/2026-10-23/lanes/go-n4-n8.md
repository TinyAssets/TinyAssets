SPEC LANE: N4 (live takeover view of the box's browser and desktop, for passwords, 2FA and CAPTCHAs) and N8 (optional
link to the user's personal computer, Direction 8: every hosted surface works without it). Worktree
C:\Users\Jonathan\Projects\wf-n4-n8-spec, branch spec/n4-n8-view-and-pc-link (from main eb582d82d08). Spec only: one
openspec change per primitive (proposal, design, spec delta, tasks); no product code. Registry rows N4 ("a view of the
owner's box exposed through the interface package"; guard box-view: the view reads only its owner's box) and N8 (an
optional external box connector the owner adds; host-paths stays green) in docs/architecture.md; ADR-022 P4 lists
BoxProvider.view on the owner door only. Place every piece by the target shape (T1-T10): the router only authenticates
and forwards the view stream to the owner's own box; no shared component reads the stream; the user's credentials
typed into the view never leave the box; the PC link is an owner-added connector, not a platform host path. Keep it
short: decisions, not essays. Open a PR.

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
