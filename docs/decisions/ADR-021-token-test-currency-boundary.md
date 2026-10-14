# ADR-021: Test currency boundary

## Status

Accepted for the existing paid-market experiments; future product direction still governs whether those experiments remain.

## Date

2026-10-11

## Decision

The real currency reference is Destiny (tiny), symbol `tiny`. Existing Workflow bid, settlement, staking, fee and governance experiments use only `test tiny` on Base Sepolia. Mainnet contract addresses are reference data, not active payment or custody integrations. Public copy must not claim mainnet payment, staking, custody or governance. Real currency integration requires a separate decision and implementation.

The experimental runtime and its as-built paid-market specification were retired
by `openspec/changes/package-runtime-convergence/` (C10). Existing stored ledger
and settlement history is preserved; retirement does not authorize deletion or
value movement. The pre-retirement specification remains in git history.
