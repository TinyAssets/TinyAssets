LANE: build N1, the one inbound door. Worktree C:\Users\Jonathan\Projects\wf-n1-impl, branch feat/n1-inbound-door (main
eb582d82d08 plus the approved spec branch spec/n1-inbound-door, PR #5228). Implement the spec in
openspec/changes/<the N1 change>/ exactly; its tasks.md is your checklist. Scope for this PR: the one route, the
shared verifier and installation authority, generic per-binding webhooks, and the Slack shared app (fixed extraction
after HMAC, tap-to-install OAuth, per-installation breaker, no-op rows, proof-header stripping, wake reconciliation).
WhatsApp and Teams stay behind their fixture gates: build nothing channel-specific for them. Email is out unless its
gate is satisfied by a fixture you can write offline. Slack's live app registration and secrets are founder/host work:
leave a docs/host-actions.md row and read the platform signing secret from broker custody by name.
Write the tests the spec names first. Open a draft PR to main. Do not deploy.

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
