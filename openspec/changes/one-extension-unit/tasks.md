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

- [ ] 3.1 Add on-demand guidance, run affected Linux tests/heavy files, Ruff, mirror and hygiene checks.
- [ ] 3.2 Open draft PR, obtain Claude implementation review, fold findings, merge main and push verified commits.
- [ ] 3.3 After deployment assert SHA, run real-user app acceptance and sync verified specs.

## Verified slice (2026-10-06)

Draft PR #4519. Linux oracle: 411 passed, zero skips, covering new unit/jail
tests plus extension boundary, ta, prompt costs, universe tools, package sharing,
installed directory and account deletion. New unit tests also pass on Windows
(31). Changed Python Ruff and plugin mirror/import probe pass. Existing prompt
budgets and test assertions are retained. No affected test file is heavy-listed.

2.4 currently supports explicit ta tools/commands/hook entries using read-only
per-bash snapshots; automatic turn-event hook launches are not wired. 2.5 is
metadata-only and reports UI runtime unavailable. 2.6 reuses canonical shareable
package envelopes and folds #4515's resolver/tests, but end-to-end active-revision
publication/recipient activation remains unproven. Connection requirement calls
report binding_required and create no grants. MCP declarations are validated and
discoverable but not attached. U1 admission remains unimplemented here.

No deployment, real-user pass or as-built spec-sync claim. The proposal spec is
the intended complete contract, not a claim that all requirements have shipped.
