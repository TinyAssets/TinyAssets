LANE: build N10 (owner-only policy layer) and N5 (owner network gate) on it. Worktree
C:\Users\Jonathan\Projects\wf-n5-n10, branch feat/n5-n10-owner-egress-policy (main eb582d82d08 plus the approved N5/N6
spec branch spec/n5-n6-egress-undo, PR #5229). Registry rows N10 ("one broker-private account policy; protected
owner-session writes; read-only agent projection; every broker decision receipted"; remaining: "P5 box-boundary gate
and P6 legacy source retirement") and N5 in docs/architecture.md. Implement the N5 spec in
openspec/changes/owner-network-egress-policy/ exactly (founder decisions 2026-10-23: open by default; allowlist and
ask; owner-connected destinations satisfy them only when committed or widened through the protected N10 owner session;
approving an ask writes a standing entry through the one policy writer). Finish N10's P5/P6 where N5 needs them; one
writer of policy (the owner session), the agent reads a projection only. The one egress check stays the one check
(broker/egress.py). The guard test named in the spec (box-committed binding does not satisfy allowlist/ask) must exist.
Open a draft PR. Merging waits for the C6 close-out; that is fine.

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
