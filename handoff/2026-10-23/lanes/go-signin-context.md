LANE: sign-in stays in the user's own context (tracker item 8.12 in openspec/changes/owner-daemon-in-box/tasks.md).
Worktree C:\Users\Jonathan\Projects\wf-signin-context, branch feat/signin-in-context (from main eb582d82d08).
Founder rules: a user is never sent somewhere they aren't. Web: the provider sign-in happens in the same tab and returns
to the app. Phones (mobile/, Capacitor): an in-app browser sheet, returning by the app's own scheme. Desktop
(desktop-app/): the system browser, in the browser profile that is already open for the matching account when one is,
then returns to the app via its scheme; never an embedded window. The fewest taps. Applies to every sign-in path
(app sign-in, model source OAuth, subscription CLI logins, connector OAuth), provider-agnostic.
Observed live: the OAuth tab said "you can close this" instead of returning; two browsers with different profiles
sent a sign-in to the wrong account; a pending approval link from another account produced a 409 loop (now dropped).
Audit every place that opens a sign-in URL, route them through one helper per surface, and test each surface's
return. Open a draft PR. Note anything that needs a new native app release.

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
