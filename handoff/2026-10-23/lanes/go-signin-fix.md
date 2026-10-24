Fix round on PR #5231 (worktree C:\Users\Jonathan\Projects\wf-signin-context, branch feat/signin-in-context). Review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-signin.md
Fix finding 1 (MEDIUM): on native and desktop, "Sign in to approve" (/app/owner-sign-in) must run the owner-proof flow
for the account already signed in, never a full app re-login that can switch accounts; if the owner-proof flow cannot
complete on that surface, do not show the link there and say what the owner does instead. Fix finding 2 (a web
sign-in the user abandons is cancelled server-side when it expires or the user starts another). Finding 4: if either
desktop flow can be made to finish with the existing scheme return, do it; otherwise record it in the PR body.
Finding 3 is accepted; leave it. One focused test per fix that fails first; push to the same branch.

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
