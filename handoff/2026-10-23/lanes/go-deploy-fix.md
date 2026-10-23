LANE: make the production deploy safe for the owner-box bundle (floor). Worktree: create
C:\Users\Jonathan\Projects\wf-deploy-fix on a new branch fix/deploy-box-bundle from origin/main (you may create this one
worktree). Incident 2026-10-23 03:30-04:15 UTC (deploy run 39895153391 of eb582d82d08):
1. The public MCP canary got 503: the box host refused wakes with "boxhostd connection limit exceeded". The broker's
   lifecycle slots (boxes/host_service.py _broker_slots = volume_concurrency + 1) were all held by post-drain work:
   after image-at-wake drain every owner sleeps with a snapshot, and the next wake first reconciles that (rclone
   copyto/cat to Spaces), holding a slot for minutes. Excess lifecycle calls were refused instead of queued.
   Fix in the shape (T1/T7: the waker and router are thin; lifecycle stays in the box host): lifecycle calls from the
   broker wait (bounded) for a slot instead of being refused, scheduled wakes yield to request-driven wakes, and the
   deploy's canary waits for post-drain lifecycle work to finish (a bounded wait on a box host readiness signal) before
   judging, rather than failing on the first 503.
2. The rollback ("Roll back if the public canary is red" -> deploy_fail_safe.sh --restore-bundle) restored the
   containers and compose but NOT the box host: /opt/tinyassets/boxhostd/{tinyassets,deploy,scripts} and the
   harness-current symlink stayed on the new release, leaving a mixed bundle where the old broker could not reach the
   new box host's credential socket and no box could wake. The host-service bundle and harness-current must be part
   of the same snapshot/restore transaction as compose (deploy/DEPLOY.md "Nothing reports success over a mixed tree"),
   restored in the order box host -> broker -> daemon.
3. The deploy rewrote /etc/tinyassets/broker.env without TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED, so the broker
   could not fetch model catalogues. Make the env installer carry every flag the broker needs from the source env, with
   a test that the rendered broker.env contains it.
Tests that reproduce each failure first (a fake box host with a held slot; a restore that must revert the host bundle;
the env render). Update deploy/DEPLOY.md only where behavior changed. Open a draft PR to main. Do not deploy.

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
