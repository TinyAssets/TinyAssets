Fix round on PR #5243 (worktree C:\Users\Jonathan\Projects\wf-s2-start, branch conv/s2-one-way-to-start). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-s2.md
The zero ceiling was reached by replacing the probe while two executors with their own admission gates still exist;
the registry now records that duplication nowhere (S3 says "none"). Never lower a ceiling by changing what it measures.
Do the real convergence: merge the two agent executors into the one S3 loop behind session.start, with one admission
gate, deleting the other executor in this PR. Then the honest probe (the old one, or a stricter one that also catches a
genuinely new second path, finding 3) reads zero and structural-guards passes. Route the tools-enabled model calls from
`ta` (finding 4) through session.start or document precisely why they are not agent starts. Tighten finding 5. If the
executor merge is too large for this PR, instead restore the S2 row to drifted with the old probe and its true ceiling,
add a tracked S3 probe for the duplicate executor, and say so in the PR body: honest registry over a converged label.
Failing-first tests; push to the same branch.

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
