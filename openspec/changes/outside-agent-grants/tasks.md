Docs-only proposal delivery; all boxes below describe future implementation and acceptance. No product code, deployment or live-proof completion is claimed.

## 1. Identity and authority foundation

- [ ] 1.1 Reconcile addressed-agent-control-provenance, agent-access-controls and inline-connect-and-approve integration points; verify AuthKit client claim/lookup, registered metadata, issuance/session generation and revocation capabilities with sanitized evidence, refusing unresolved identity.
- [ ] 1.2 Bind verified issuer/client id and name/logo/redirect metadata to every public request principal; test CIMD/DCR, two clients for one owner, spoofed/missing claims and independent owner identity.
- [ ] 1.3 Implement durable per-client grants, unique first-connect main-only read/message defaults, atomic revision edits, expiry and retained revocation tombstones; test concurrency, owner-scoped storage, first-converse home bootstrap and non-provisioning no-home status.

## 2. Enforcement and owner controls

- [ ] 2.1 Apply daemon-side grant checks to every canonical handle, operation/alias and page bridge; test read/message/control/costly, mixed-resource filtering, shared pages, clear missing-grant refusals and cross-user denial before data/effects.
- [ ] 2.2 Carry outside-client origin through existing addressed-agent turn/run/activity/nested/scheduled carriers; recheck current grants at downstream reads, tool/effect admission and resume, proving messages cannot borrow resident-agent authority.
- [ ] 2.3 Add Connected apps inventory/edit/revoke and last-used to the protected owner app; extend the existing access readback with scoped client grants and durable secret-free per-client action audit; prove bearer clients cannot self-elevate.
- [ ] 2.4 Implement immediate owner/client token invalidation across workers, open sessions, refresh, queued work and pending approvals; prove revoke/admission races and generation-bound reconnect never revive old credentials, including AuthKit revocation failure.
- [ ] 2.5 Integrate client provenance and grant revalidation with inline-connect-and-approve's protected sheet and standing-rule path; verify bearer approval/dispatching retries always refuse and effect approval cannot widen a grant.

## 3. Verification and live acceptance

- [ ] 3.1 Exercise the cutover/rollback plan, legacy missing-client and unattributed-work holds; run focused and affected heavy tests plus ruff, with cross-user mutation cases, and one cross-family floor/correctness review with AGREE/DISAGREE_EVIDENCE dispositions before implementation acceptance.
- [ ] 3.2 Run `npx --yes @fission-ai/openspec validate outside-agent-grants --strict`; assert the implementation SHA deployed with `python scripts/deployed_sha.py --assert-contains <sha>` and run `python scripts/mcp_public_canary.py --assert-handles` without adding handles.
- [ ] 3.3 Founder connects Muse to `https://tinyassets.io/mcp` in a rendered real-user pass: verify AuthKit accepts `https://agent.meta.ai/api/hatch/oauth/callback` via CIMD/DCR; prove default main-only read/message, owner widening to another agent/control, sensitive-action sheet, audit/last-used and immediate revoke. Record sanitized receipts or the exact callback/configuration blocker; no simulated pass.
- [ ] 3.4 After implementation and real-user proof, sync the deltas into the canonical specs and archive this change with verification receipts; leave live/outbox, A2A and outbound MCP attach in their separate later changes.
