LANE: one durable send (shape-convergence C11, Shape registry S24 in docs/architecture.md; tracker item 8.11 in
openspec/changes/owner-daemon-in-box/tasks.md). Worktree C:\Users\Jonathan\Projects\wf-durable-send, branch
conv/c11-durable-send (from main eb582d82d08).
Target: an owner message is stored once (in the box, before anything is published or dispatched), then dispatched;
the page never drops a send and never shows duplicate "Still waiting to be sent when the page reloaded" notices.
Observed live: the page's self-reload (?_ta_recover=...) brought back old waiting notices, and a send typed while a
turn was running was dropped. Find every send path (onboarding/app/main.js send_journal.js durable_send.js, the box's
turn intake), measure them, converge on one, delete the others, and add the planned `send-path` structural guard that
fails CI if a second send path appears. Spec delta first if the stored-message contract changes. Open a draft PR.

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
