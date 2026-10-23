SPEC LANE: N3 (realtime two-way voice and video) and N9 (model_call returns media). Worktree
C:\Users\Jonathan\Projects\wf-n3-n9-spec, branch spec/n3-n9-streaming-media (from main eb582d82d08). Spec only: one
openspec change per primitive (proposal, design, spec delta, tasks); no product code. Registry rows N3 ("a streaming
mode of model_call and sessions over a connected source, not a second loop") and N9 ("model_call outputs typed media
parts with a receipt per call") in docs/architecture.md; read the merged C3 one-model-call work (model_call.py and PR
#5232 if not yet merged), the broker streaming contract change, and the existing browser voice code so the spec
replaces it rather than adding beside it. Place every piece by the target shape: provider credentials stay in the
broker, media bytes are owner data in the box, the realtime transport goes through the broker as the one egress, no
provider-specific code (capabilities come from the source's declared or discovered contract). Name the tests that prove
"no realtime loop beside S3" and "a media receipt per call". Keep it short: decisions, not essays. Open a PR.

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
