LANE: discovery finds a provider's API base generically. Worktree C:\Users\Jonathan\Projects\wf-api-base, branch
feat/discovery-api-base (from main eb582d82d08). Small.
Discovery (tinyassets/connection_oauth/model_discovery.py, runs in the owner's box) dropped the provider-named
"/openai/v1" probe (Direction 2: no provider-specific code), so typing a bare provider domain whose API lives under a
vendor-specific path no longer works. Make it generic: read what the provider publishes (an OpenAPI/servers document,
RFC 9728 protected-resource metadata, a well-known API catalog such as RFC 9727 /.well-known/api-catalog, or links on
the domain's root/docs page if machine-readable) to learn the model API base, then probe that. No vendor names or paths
in code. Keep the existing probe budget and egress path. Tests with offline fixtures for each discovery source. Open a
draft PR.

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
