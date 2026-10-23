## Why

N1 and frontier baseline #4624 require reaching the agent through connected channels with devices off. The existing webhook receiver bypasses the one `session.start` path and lacks a connector verification contract.

## What Changes

- Converge `/hooks/inbound/{binding}`: authenticate, positively map to one owner, wake and forward; durable acceptance and session start stay in that box.
- Preserve the 2026-10-23 founder decision: tap-to-install shared TinyAssets apps, no owner-created developer apps. N1 admits fixed Slack HMAC/installation extraction and WhatsApp only with proven per-account callback overrides; generic hooks and connected email use per-binding URLs. Teams is a follow-up pending identity and outbound-credential fixtures.
- Delete general selectors and shared key-refresh machinery. Platform installation authority alone owns shared profiles and install lifecycle; broker retains credentials. Authenticated unroutable shared deliveries are no-ops.
- **BREAKING:** replace direct branch/source dispatch, require generic event ID/timestamp headers, and make secret-dependent verification explicitly broker-dependent even for awake boxes. Reuse C2's pump.

## Capabilities

### New Capabilities
- `inbound-door`: verified delivery through existing session/wake machinery, with box-local processing and idempotency.

### Modified Capabilities
- None in main specs. Refine pending `automation-event-triggers/specs/inbound-webhook-triggers` delivery semantics at implementation/sync; preserve its management contract.

## Impact

Spec-only PR, N1 lane, branch `spec/n1-inbound-door`. Implementation depends on C7/C8 and `owner-daemon-in-box` T1-T10. Shared changes are authentication, identity metadata and credential verification only. No production access, deployment or main-spec sync here.
