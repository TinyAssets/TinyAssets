# ADR-020: Test currency boundary

## Status

Accepted for the existing paid-market experiments; future product direction still governs whether those experiments remain.

## Date

2026-10-11

## Decision

The real currency reference is Destiny (tiny), symbol `tiny`. Existing Workflow bid, settlement, staking, fee and governance experiments use only `test tiny` on Base Sepolia. Mainnet contract addresses are reference data, not active payment or custody integrations. Public copy must not claim mainnet payment, staking, custody or governance. Real currency integration requires a separate decision and implementation.

The related as-built specification is `openspec/specs/paid-market-economy/`.
