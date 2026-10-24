Final edits to the N1 inbound-door spec, PR #5228 (worktree C:\Users\Jonathan\Projects\wf-n1-spec). No further review
round: the lead checks your diff. Findings:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-n1c.md
- A (MEDIUM): a per-installation failure breaker. When one installation's deliveries keep failing past a set threshold
  (choose numbers well inside Slack's documented app-wide disable rule), the router moves that installation to the 200
  no-op row; only that owner loses events, the owner sees it in the app, and their box reconciles when it wakes.
  Amend spec.md:31 to allow exactly this. The breaker state is routing state (T1), not owner logic.
- E (LOW): the installation authority accepts a retirement request only for the calling owner's own installations and
  confirms the revocation with the provider; a box's routing-snapshot view is filtered to its owner's records.
- B, C, D: add as explicit fixture gates in tasks.md (each blocks its channel, not N1 as a whole): Meta verifies
  per-account override URLs and the platform responder rejects foreign verify tokens, plus the box ignores entries whose
  account id is not its own installation's; Slack org-wide plus workspace overlap is treated as a conflict if Slack
  allows both; connected email push uses a per-binding event source.
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
