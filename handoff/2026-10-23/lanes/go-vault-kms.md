LANE: S28 credential-vault reshape on post-C6 broker custody (tracker item 8.14; Shape registry S28). Worktree
C:\Users\Jonathan\Projects\wf-vault-kms, branch conv/s28-vault-kms (from main eb582d82d08).
Closed PR #4949 sealed credentials under a KMS key hierarchy but was built on the pre-C6 daemon vault
(credential_vault.py, role launchers). Read it (gh pr diff 4949) and openspec/changes/credential-vault-reshape if it
exists. SPEC FIRST (authority and custody): redo it on today's broker custody (broker/vault.py,
broker/subscription_custody.py, the HTTP credential store): every credential the broker holds is sealed under one
broker-only key hierarchy with a KMS root, envelope keys per owner, rotation, and backup/restore that never exposes
plaintext; nothing reaches a box. Write the spec delta and tasks, then implement only what needs no new cloud account
or spending (KMS account setup is a founder/host action: add a docs/host-actions.md row and keep a local root for
tests). Open a draft PR.

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
