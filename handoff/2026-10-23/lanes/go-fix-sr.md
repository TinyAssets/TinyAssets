Fix round on branch conv/source-records (worktree C:\Users\Jonathan\Projects\wf-source-records, head c9ae0cac485).
Review (DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review3.md
Fix findings 1-6 and 8, each with one test that fails first; commit each separately. Finding 7 is pre-existing; leave it.
1 HIGH: disconnecting a subscription source must remove its member from the agent's model assignment in the same step,
  re-anchoring the root to a surviving member (an explicit departure through the owner's own action, in the box). Then
  later model-access changes work. Do NOT reintroduce runtime pruning on exceptions.
2 MEDIUM (broker): cap custody entries and total bytes per owner in broker/subscription_custody.py, enforced by the
  broker (it cannot trust the box): a small entry cap (e.g. 16 sources) and a total byte cap; refuse beyond them.
3 MEDIUM: lease writeback retries a bounded number of times with backoff before wiping; capture only state files that
  parse (or are unchanged), never partial bytes from a cancelled run.
4: disconnect deletes custody by source id without constructing a runtime, so a removed or broken record still
  disconnects.
5: sweep stale /tmp/ta-native-* leases at daemon start; disconnect joins (bounded) an expired Login thread before
  deleting.
6: upgrade_connection accepts markers digested from the record as it was before lock_sha256 was added; update the stale
  design.md text about the digest patch.
8: cutover_assignment_repair.py refuses to run while the owner volume is attached to a running box or the broker
  socket answers.
Report head SHA and files per running role.

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
