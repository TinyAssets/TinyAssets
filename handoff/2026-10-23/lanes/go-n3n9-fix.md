Fix round on the N3/N9 specs, PR #5240 (worktree C:\Users\Jonathan\Projects\wf-n3-n9-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n3n9.md
Resolve every finding by deciding:
1 HIGH: remove or modify every spec that still requires the old voice bridge, in this PR, so one voice path remains.
2 HIGH: spending for realtime and media: how an open-ended stream reserves against caps (e.g. a bounded time slice
  reserved and renewed), what happens when a cap is hit mid-stream (graceful stop with a receipt), media metered per
  call; free-only defaults unchanged.
3-4: one storage rule shared by N3 and N9 (state which: committed media stored before references are published;
  partial frames ephemeral), N3 declares it builds on N9; say exactly what is kept from speech input (transcript, audio,
  or both) and default to the least.
5: define one call: a duplex session is one parent allocation with one receipt per model turn inside it (or decide
  otherwise and say why); interruption cancels the current turn.
6: source selection follows Direction 4: use the strongest connected source that supports realtime; if the strongest
  source lacks it, say so visibly rather than silently dropping to a weaker source; no hidden fallback.
7: drop owner-elected raw capture saving. 9: mark the planned tests as new. Ignore 8 (dates are UTC, correct).
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
