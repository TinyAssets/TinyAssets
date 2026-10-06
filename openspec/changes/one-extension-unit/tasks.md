## 1. Contract

- [x] 1.1 Read audit, reconcile overlapping lanes and define manifest, authority and U1 contract.
- [x] 1.2 Obtain Claude proposal review and record AGREE/DISAGREE_EVIDENCE dispositions.

## 2. Implementation

- [x] 2.1 Validate complete v2 manifests and package byte revisions with negative tests.
- [x] 2.2 Add owner-bound install/activate/revoke state with generation conflicts and isolation tests.
- [x] 2.3 Expose lifecycle, contribution discovery and connection requirement dispatch through ta.
- [ ] 2.4 Dispatch tools/commands/hooks in today's jail with exact bytes and revocation checks.
- [ ] 2.5 Project UI/cards and workflows through existing backends under the same lifecycle.
- [ ] 2.6 Integrate package sharing and authenticated installed-agent resolution without exporting grants.
- [ ] 2.7 Consume U1 package admission for scoped connections and MCP; retain explicit unavailable results until ready.

## 3. Verification and delivery

- [x] 3.1 Add on-demand guidance, run affected Linux tests/heavy files, Ruff, mirror and hygiene checks.
- [x] 3.2 Open draft PR, obtain Claude implementation review, fold findings, merge main and push verified commits.
- [ ] 3.3 After deployment assert SHA, run real-user app acceptance and sync verified specs.

## Verified slice (2026-10-06)

Draft PR #4519. Linux oracle: 411 passed, zero skips, covering new unit/jail
tests plus extension boundary, ta, prompt costs, universe tools, package sharing,
installed directory and account deletion. New unit tests also pass on Windows
(31). Changed Python Ruff and plugin mirror/import probe pass. Existing prompt
budgets and test assertions are retained. No affected test file is heavy-listed.

Claude implementation review: ADAPT, no floor break; findings and dispositions
are recorded in review-implementation.md and design.md. Recovery/concurrency
fixes: focused Linux oracle 127 passed, zero skips; Windows unit tests 36 passed.
Hygiene after the initial implementation commit: 0 removed / 0 tampering.
Ruff and rebuilt mirror/import probe pass after the review fixes.

Merged origin/main at 22bc0f728e without conflicts. Post-merge Linux oracle
(starter instructions, extension unit/jail and prompt costs): 57 passed, zero
skips. Final hygiene: 15 tests added, 0 removed, 0 tampering. Plugin mirror/import
probe remains clean after merge. PR stays draft; pending runtime tasks above are
not superseded by these verification results.

2.4 currently supports explicit ta tools/commands/hook entries using read-only
per-bash snapshots; automatic turn-event hook launches are not wired. 2.5 is
metadata-only and reports UI runtime unavailable. 2.6 reuses canonical shareable
package envelopes and folds #4515's resolver/tests, but end-to-end active-revision
publication/recipient activation remains unproven. Connection requirement calls
report binding_required and create no grants. MCP declarations are validated and
discoverable but not attached. U1 admission remains unimplemented here.

No deployment, real-user pass or as-built spec-sync claim. The proposal spec is
the intended complete contract, not a claim that all requirements have shipped.


## Remote MCP continuation (2026-10-06)

Ported #4496's streamable-HTTP protocol client onto today's governed connection
effector, retaining consent/rules/review, OAuth custody and endpoint restrictions.
No U1 broker metadata or stdio launcher dependency. Remote contribution dispatch
supports explicit discovery and catalog-hash-pinned calls, with no tool replay.
Replies are bounded by today's effector, including SSE framing; no persistent
server event subscription is claimed. Private activation bindings pin current
local grants and connection incarnations; rebind requires a new generation.

Linux oracle: 207 passed / zero skips (extension state/jail/remote, ta, existing
authenticated effector, account deletion and static prompt costs). Changed-file
Ruff and plugin mirror/import probe pass. Claude continuation review running;
verdict will be recorded before final push. Git/outside authority, automatic
hooks and UI projection remain in progress. No deployment or live proof claim.
