Fix round on PR #5237 (worktree C:\Users\Jonathan\Projects\wf-durable-send, branch conv/c11-durable-send). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-send.md
Fix findings 1-10, each with a failing-first test. Priorities: 1 (a stored message is never run after a restart: the
box resumes pending stored sends on start, exactly once) and 2-3 (no caller of a deleted module: move what
mcp_events_http.py needs, update the background-authority inventory and stale templates). 4-6: stuck states resolve
(a proven no-effect send is retryable; the model sees an honest summary of non-completed rows instead of hiding them
forever; a retry conflict resolves). 7: report history_saved truthfully. 8: serialize dispatch per thread, not per
database, and never hold executor slots while waiting. 9: the send-path guard catches every writer of owner message
rows. 10: resume saved records for every agent on load. Push to the same branch.

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
