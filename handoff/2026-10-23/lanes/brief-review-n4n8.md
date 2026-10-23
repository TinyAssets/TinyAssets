Review the N4 (live takeover view of the box's browser/desktop) and N8 (optional link to the user's PC) specs in PR
#5239 (worktree C:\Users\Jonathan\Projects\wf-n4-n8-spec; `git diff origin/main...HEAD`). Read-only.
Yardstick: openspec/changes/owner-daemon-in-box/research.md and T1-T10 in its design.md; AGENTS.md Direction 8 (every
surface works with no host machine on); registry rows N4, N8 in docs/architecture.md; ADR-022 P4.
Check: the router only authenticates and forwards the view stream to the owner's own box (positive identity->box
mapping); no shared component reads the stream or input; what the owner types into the view (passwords, 2FA) never
leaves the box or reaches the agent's transcript unless the owner chooses; the agent cannot open, watch or drive the
view without the owner; the PC link is an owner-added connector with credentials in broker custody and nothing hosted
depends on it; nothing over-built; no decision left open. Report findings with file:line and severity, then exactly
one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE. No sub-agents, no peer_agent.py. Budget about 15 minutes.
