Second fix round on the N5 spec, PR #5229 (worktree C:\Users\Jonathan\Projects\wf-n5n6-spec). Finding (HIGH):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-n5n6.md
The box can commit credential bindings (broker/server.py committed_via="box"; http_connect.py accepts it; extend/full
updates carry no committed_via), so an agent could deposit a binding for any destination and bypass allowlist/ask.
Fix the spec: only bindings committed or widened through the protected owner door (the N10 owner session) satisfy a
restrictive mode; box-committed bindings and box-widened scopes never do. State that extend/full updates record their
committer the same way. Add the spec scenario "a binding the box commits does not satisfy allowlist or ask" and name
the matching test in tests/test_owner_broker_egress.py. One writer of egress permission: the owner. Push to the same
branch. Keep it short.

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
