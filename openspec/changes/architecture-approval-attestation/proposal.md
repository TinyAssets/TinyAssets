## Why

Founder decision 2026-10-10 requires real evidence that the founder was briefed and agreed an over-cap PR advances the README Direction architecture. GitHub comments cannot prove this because automated actors use the founder's token.

## What Changes

- Add architecture approval to the existing protected pending-request flow, with exact PR scope and a required architectural briefing.
- Issue a broker-custodied Ed25519 attestation only after protected owner-session approval by the configured founder.
- Publish signed attestations read-only; above eight release-critical files require both the existing exact-count receipt and a matching, unexpired signature.
- Preserve all existing scope, label, review and hygiene rules.

## Capabilities

### New Capabilities
- `architecture-approval`: protected owner consent and independently verifiable PR architecture attestations.

### Modified Capabilities
None.

## Impact

Pending requests, broker RPC and secret custody, protected app approval, a public read endpoint, and the trusted CI scope guard. Existing cryptography dependency supplies Ed25519. No data migration; new broker-private state. Owner: Codex; branch: ci/receipt-declared-release-critical; PR: #4574.
