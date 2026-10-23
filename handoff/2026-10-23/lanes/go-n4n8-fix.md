Fix round on the N4/N8 specs, PR #5239 (worktree C:\Users\Jonathan\Projects\wf-n4-n8-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n4n8.md
Resolve every finding by deciding, not listing options:
1 HIGH: decide the mechanism that keeps the agent (which runs in the same box with bash) out of the owner's view input
  and private frames: e.g. the view's input path and frame stream live in a separate process/user inside the box the
  agent cannot reach, and private input never enters agent observations; name the test.
2 HIGH: there must be one live view. Extend the existing "protected live owner view" in
  openspec/specs/browser-login-custody/spec.md into N4 (list it as a modified capability) instead of adding a second.
3: N8 decides who holds the pairing credential (broker custody), how the companion's outbound link reaches the box
  (through the router/broker, owner-bound), and how it is revoked.
4: fold or supersede the open companion-local-mcp change so one change owns the desktop companion (S21 one queue).
5: decide the fate of the existing tray host (tinyassets/desktop/tray.py, desktop-app/): becomes the N8 connector,
  or is deleted.
6: add tasks that build the box-view guard and update the N4/N8 registry rows; fix the created dates.
Push to the same branch. Keep it short.

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
