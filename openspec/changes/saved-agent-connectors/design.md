## Context

Split from connect-anything-ladder after round-1 shape review; preserves its custody and lifecycle contract.

## Goals / Non-Goals

Save, test and revoke reusable connectors authored by the owner's agent. No new card, request authority, provider-specific code or platform LLM.

## Decisions

Dependency order: `connect-anything-ladder` secret slots/egress and connection lifecycle first; existing extension activation and `inline-connect-and-approve` card next. Independent of browser custody; no D5 dependency.

Given API docs or OpenAPI, the owner's agent writes an ordinary extension with declared credential slots and a connection binding, runs a non-destructive self-test (or separately approves a test effect), and saves the exact tested revision with test receipt. A failed test is visibly failed, never labelled connected. List saved connectors beside other connections with source/version and revoke/disconnect controls; updates invalidate the old test receipt until re-tested. Reuse the current extension runtime and activation permissions. Sharing exports code, slot requirements and safe test metadata, never credentials, sample private responses or live grants. Revocation fences new dispatch before cleanup.

## Migration Plan

Preserve existing records and grants; version new metadata, fail visibly on unsupported schemas, and retain revocation/history/cleanup during rollback. Reuse incarnation fencing, coordinator idempotency and processed-ack continuation.

## Risks / Trade-offs

Remote challenges or failed self-tests remain visible. Never label a failed connection active. Cross-user isolation remains the fixed floor.
