## Why

Connection actions that spend money need an exact-total approval and reconciled payment receipt, not a reusable broad connector grant.

## What Changes

- Add a spend request binding merchant, exact total including fees/tax, currency and quote expiry.
- Use provider-as-data payment/card issuance with broker-held credentials, single-use scope and owner budget caps.

## Capabilities

### New Capabilities

- `exact-total-spend-rail`: Connection actions that spend money need an exact-total approval and reconciled payment receipt, not a reusable broad connector grant.

### Modified Capabilities

None; consume the existing owning contracts below.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`. Proposal-only follow-up requested 2026-10-04; no product code, deployment or PR. Future implementation: one separately claimed worktree/branch/PR for this intent.

inline-connect-and-approve owns approval sheet/Needs you/continuation and effect intents; connect-anything-ladder owns credential custody; existing budget/accounting paths own balances. Creator revenue and payouts remain creator-revenue-share work.
