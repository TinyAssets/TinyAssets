## 1. Converge the existing path

Implementation checklist; intentionally unchecked in this spec-only lane.

- [ ] 1.1 Confirm C7/C8 durable session-start idempotency and map C2 webhook management to box-local connector/agent configuration.
- [ ] 1.2 Extend binding metadata and broker verification with platform-only shared profile/install authority, server-verified identity, same-session OAuth state, conflict credential checks, owner-scoped retirement with provider revocation proof, owner-filtered snapshot views, erasure/revision retirement and wake reconciliation; router-only `INBOUND_VERIFY` resolves profiles broker-side.
- [ ] 1.3 Wire the single route to proof/admission and existing wake/forward; strip named proof headers, forward installation revisions, distinguish authenticated shared no-ops from durable acceptance, add the three-failures-in-60-seconds per-installation T1 breaker with owner-visible state and box reconciliation on wake, and keep app setup challenges outside delivery.
- [ ] 1.4 Replace dispatch with atomic box-local inbox/outbox and receipt-keyed `session.start`; prove replay and crashes at acceptance/start/ack boundaries.
- [ ] 1.5 Supply fixed Slack workspace/org extraction and unsupported/ambiguous no-op fixtures. Gate WhatsApp alone on fixtures proving Meta verifies per-account override URLs, the platform responder rejects foreign verify tokens, batches are account-isolated, and the box ignores entries with another account/phone ID; otherwise exclude it. Gate Slack alone on a fixture for org-wide/workspace overlap; if Slack permits both, authority rejects coverage conflicts. Gate connected email alone on a per-binding event source fixture. Keep Teams deferred until authenticated installation and scoped outbound-credential proof. Supply per-binding email/generic profiles and box-local parsing/replies/key renewal.
- [ ] 1.6 Require generic event ID/timestamp headers, prune by freshness, migrate bindings/receipts, drain pending deliveries and delete the old direct dispatcher, hash dedupe and duplicate authentication.

## 2. Prove and finish the implementation

- [ ] 2.1 Create `tests/test_inbound_door.py` with the design's route, authority, protocol, outage and replay assertions; run `python scripts/linux_oracle.py -- tests/test_inbound_door.py tests/test_owner_router.py tests/test_automations.py tests/test_owner_waker.py tests/test_owner_next_wake.py` and ruff on changed Python.
- [ ] 2.2 Complete implementation floor-class review against T1-T10; resolve findings by placement.
- [ ] 2.3 In the authorized deployment lane, prove deployed SHA, naive-user message/reply with devices off, disconnect/retry and public handles; preserve fail-closed rollback.
- [ ] 2.4 Sync N1 and C2 inbound delivery together, retaining unrelated C2 obligations; mark N1 converged only after guard/live proof.
