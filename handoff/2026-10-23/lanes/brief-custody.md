LANE A: subscription CLI credentials go back into broker custody, leased per run (shape correction).
Worktree C:\Users\Jonathan\Projects\wf-source-custody, branch conv/source-custody (from conv/source-records 9ab39b4b886).
A parallel lane (B/C) works in ..\wf-source-records on discovery placement and member pruning. Do not touch
broker/model_connect.py, connection_oauth/model_discovery.py, api/connection_uses.py, api/model_access_requests.py,
onboarding/source_connect.py or providers/served_model_plan.py.

READ FIRST: openspec/changes/owner-daemon-in-box/research.md and design.md "Target shape (binding)" T1-T10, especially T6:
provider credentials stay in proxy/broker custody, never "a raw secret into the box". ADR-022 P2b: CLI packages run in
the box with the token "leased for one run (ADR-012 D5)". docs/concerns/c6-cli-vault-box-copy.md describes the same
defect from the migration.

DRIFT: lane W (subscription_runtime.py, subscription_connect.py, the records' state_paths) keeps each CLI's long-lived
login (e.g. .claude/.credentials.json, .codex/auth.json) as persistent files in the owner's box home, where the agent's
read/bash tools can reach them.

TARGET:
1. Spec first: a delta in openspec/changes/box-subscription-sources (or the existing change that owns it) stating
   custody, lease, refresh writeback and disconnect; then build it.
2. Sign-in: the CLI login runs in the box as now; on success the declared state files are captured into broker custody
   (the existing credential vault / custody namespace the HTTP sources use, through the broker channel the box already
   has) and removed from the box home.
3. Each invoke leases the state into a private per-run tmpfs location (never the persistent home), points the CLI at it
   (HOME or the record's config env), runs, writes refreshed state back to custody under a generation check, and wipes
   the tmpfs. Concurrent runs of one source serialize on the lease or use generation compare-and-swap; never lose a
   refresh.
4. authenticated()/status checks use custody presence plus a leased status run, not persistent files.
5. Disconnect deletes the custody entry and the connection record; the box holds nothing persistent for the source
   (this also makes "disconnect leaves nothing" structural, and removes the race where a pending login writes after
   disconnect). Cancel any pending Login for that source first.
6. Migration of current live state: on first use, if legacy persistent state files exist in the home, move them into
   custody and delete them (one-shot, deleted later with the cutover code), so nobody has to sign in again.
7. Tests: the guest home holds no credential file after sign-in, between runs and after disconnect; a refresh during a
   run is written back; a second concurrent run does not clobber it; disconnect leaves nothing.
Report head SHA, files per running role (daemon container, broker container, box harness, page, box image).
HARD CONSTRAINTS: no sub-agents, no new worktree, no production access, no git config, never --no-verify, no full suite
(targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff). No model identifiers in commits. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Budget about 75 minutes. Before patching anything a test or reviewer flags, ask "can this exist in the target shape?";
if not, move the work, don't add a guard.
