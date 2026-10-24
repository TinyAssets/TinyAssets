Fix round on branch conv/source-records (worktree C:\Users\Jonathan\Projects\wf-source-records, head 9ab39b4b886).
An independent review returned DISAGREE_EVIDENCE. The full text is in
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review2.md
Fix findings 1-5, each with one focused test that fails before your fix; commit each separately.

1. HIGH source_connect.py live(): returns False for subscription_cli members, so adding an API source to an owner with a
   subscription 409s, or silently drops the subscription. Make liveness provider-agnostic (resolve every member through
   the same resolver used for serving; never branch on access_method).
2. MEDIUM "dead member" pruning (connection_uses.py alive(), model_access_requests.py still_resolves(), source_connect
   live()): only EXPLICIT departure prunes a member: definition gone, grant revoked or not found, login removed
   (the connection marker is absent). Any other exception (timeout, CLI status failure, sqlite busy, network)
   propagates and the operation fails with a retryable error; it never drops a member or switches the root. Use one
   shared helper for all three call sites.
3. MEDIUM shared CLI image build: commit a package-lock.json per subscription source record (next to the record or
   referenced by it, with its own digest in the record) and make the image build use `npm ci` against that committed lock
   only, verifying the lock digest before install; build with a minimal environment. Generate the locks offline from
   the pinned versions where possible; if network is needed to produce them, produce them now and commit them.
4. LOW disconnect(): take runtime.lock() and cancel any pending Login for that owner and source before deleting, so
   nothing is written afterwards.
5. LOW drain: after cancelling pending logins, wait (bounded, a few seconds) for their CLI processes to exit before
   reporting idle; if a process does not exit, kill it.
Also note finding 6 (an explicit pin briefly unavailable falls back to automatic): limit the stale-default fallback to
a saved default whose model is absent from the catalogue or whose source is gone, not one that is cooling down.

Report the head SHA and the files per running role (daemon container, broker container, box harness, page, box image).
HARD CONSTRAINTS: no sub-agents, no new worktree, no production access, no git config, never --no-verify, no full suite
(targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff and node --check). No model identifiers in
commits. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Budget about 50 minutes.
