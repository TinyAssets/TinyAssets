Fix round on the C12 / S17 spec, PR #5241 (worktree C:\Users\Jonathan\Projects\wf-c12-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-c12.md
Resolve every finding by deciding: 1 make the S17 guard concrete (exact paths/globs/regexes the guard rejects, the test
name, the check_shapes.py ceiling); 2 add the T10 requirement (published knowledge is copied out of the box at publish
time via BOX_PUBLISH; no shared reader of private knowledge); 3 the conversion never silently drops data (counts per
source, refuses on loss, reports what it could not convert); 4 each slice names the exact modules/files it deletes;
5 decide every duplicate export door; 6 name the import format (the export manifest, with boxd.py import_bundle);
7 cut what the review called over-built and close what it called open. Push to the same branch. Keep it short.

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
