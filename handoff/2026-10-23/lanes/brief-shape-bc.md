LANE B/C: shape corrections on branch conv/source-records (worktree C:\Users\Jonathan\Projects\wf-source-records,
head 9ab39b4b886). A parallel lane A works in ..\wf-source-custody on subscription credential custody: do not touch
providers/subscription_runtime.py, onboarding/subscription_connect.py or the subscription_sources records.

READ FIRST: openspec/changes/owner-daemon-in-box/research.md and design.md "Target shape (binding)" T1-T10. The shared
layer (router, broker/egress proxy, waker) holds no business logic; owner logic runs in the owner's box. Before
patching a finding, ask "can this class exist in the target shape?"; if not, move the work and delete the guard.

B. Address discovery runs in the shared broker (broker/model_connect.py calls model_discovery.discover/detect). That is
   business logic in a shared component, and it is why one owner's slow address could stall the broker for everyone
   (the 35s DETECT_BUDGET_S deadline was a guard on that). Move detection/choice into the owner's box (daemon code in
   the box); every probe goes out through the box's egress path, which the broker already checks (public address,
   admitted owner, redirects re-checked). The broker keeps only custody: the OAuth code exchange, the client
   registration and credential deposit. Delete the broker discovery entry point and any broker-side detect code and
   its budget guard (a per-probe timeout in the box is fine). Keep the same-site issuer rule and the
   free-models-only billing rule (now box-side).
C. Runtime "dead member" pruning (onboarding/source_connect.py live(), api/connection_uses.py alive(),
   api/model_access_requests.py still_resolves() and the relaxed "must preserve" rule) exists only because the cutover
   migration dropped pre-cutover connection grants but left them as members of accepted model assignments. Those are
   guards on a migration leftover, and they caused two review findings (a subscription member treated as dead; a
   transient failure dropping a source). Instead:
   - add a one-shot repair (a function plus a CLI entry run once by the lead, deleted with the cutover code) that
     removes assignment members whose grant or definition no longer exists, repoints a dead root to a surviving member,
     and reports what it changed (ids only);
   - restore the original strict behavior: no runtime pruning; preservation checks all accepted members;
   - keep the stale saved-default fallback only for a default whose model is absent from the catalogue or whose source
     is gone, never for one that is cooling down;
   - keep the bootstrap dedupe (newest pending duplicate) only if a duplicate can still be created in the target shape;
     otherwise fix the creation path and delete the dedupe.
D. Review finding 3 (box image build trusts the registry for transitive deps): commit a package-lock.json per
   subscription record (with its digest in the record), build with `npm ci` against that committed lock only, verify
   the lock digest first, and use a minimal build environment.
Each item: one focused test that fails before your change. Commit each separately. Report head SHA, files per running
role (daemon container, broker container, box harness, page, box image) and the exact one-shot repair command.
HARD CONSTRAINTS: no sub-agents, no new worktree, no production access, no git config, never --no-verify, no full suite
(targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff and node --check). No model identifiers in
commits. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Budget about 75 minutes.
