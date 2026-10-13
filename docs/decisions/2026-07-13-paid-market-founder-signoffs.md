# Paid-Market Founder Sign-offs — 2026-07-13

## Status

Accepted

## Date

2026-07-13

## Decision-maker

Jonathan (founder), in a Claude Code session.

## Context

Basis: `WORK-2026-07-11-DRIVING-SESSION.md (archived in git)` §5 and
`2026-07-08-production-mcp-sweep.md (archived in git)` P0. During
production hardening of the live `/mcp` service and while preparing the
paid-market Wave 2 transport, four cross-cutting questions needed a founder
ruling before they could gate implementation, migration, and OpenSpec adoption.
This ADR records those rulings so downstream specs, migrations, and the write
gate do not re-litigate them. The OpenSpec step-1.5 migration
(`PAID-MARKET-START-HERE-2026-07-08.md (archived in git)`) folds D4's serialization rule into
the capability specs as a HARD RULE.

## Decisions

### D1 — Anonymous-write gate

Mutating MCP handles require a resolved (non-anonymous) identity when the server
runs an OAuth-backed auth mode. Specifically: `write_graph` (all targets) and
`write_page` when `dry_run=false` require a resolved identity when
`UNIVERSE_SERVER_AUTH=optional` or `UNIVERSE_SERVER_AUTH=true`. Anonymous callers
get an actionable rejection explaining how to connect with OAuth. Reads stay
open. Dev mode (no auth configured) is unaffected.

**Rationale:** the live surface must not accept anonymous writes once an
OAuth-backed auth mode is configured, but reads and dev-mode ergonomics stay
frictionless. **Status:** implementation in flight on branch `claude/write-gate`;
needs merge + deploy.

### D2 — Ledger coexistence (APPROVED)

Freeze `public.ledger` as the v1 historical single-entry table. Adopt
`market.apply_tx` (migration 008, the double-entry / zero-sum / integer-micros
ledger) as the single money-movement RPC. Applying 008 to the live Supabase
remains a separate, deliberate step, gated on the migration renumber plus a
`schema_migrations` tracking table.

**Rationale:** the v1 shape must outlive the token-launch migration byte-for-byte
(preserved as historical record); the hardened double-entry ledger becomes the
one transport going forward. No shim, no dual write path — the old table is
frozen, not dual-maintained.

### D3 — Droplet concurrency target

Target roughly 100 concurrent cheap-read users after the threadpool raise.
Per-universe sharding is shelved until real load demands it.

**Rationale:** the current droplet, with a raised threadpool, covers the
near-term read-heavy load; sharding is complexity that only earns its place under
demonstrated load.

### D4 — Serialization HARD RULE + differential-testing discipline

Adopt the ledger-transport serialization rule verbatim into OpenSpec (see
`openspec/specs/paid-market-price-index-and-forwards/spec.md`, requirement "Money
movement goes through market.apply_tx() and nothing else (HARD RULE)"):

> Every money movement in the market goes through market.apply_tx() and NOTHING
> ELSE. Application code never computes a balance and writes it. The pure
> ledger.py stays the validation oracle and the executable spec; this RPC is its
> one transport.

Additionally, adopt **differential testing** as the permanent pattern for
hot-path rewrites: the original validated implementation is preserved verbatim in
the test suite as the executable spec, and the rewrite is differential-tested
against it with tie-heavy randomized trials plus a scale gate.

**Rationale:** a concurrency proof-of-need showed 8 unlocked threads against the
pure ledger created 278 units from nothing (lost updates), while single-writer
did 1M tx with zero drift — serialization is not optional. Reference
differential-test implementation: `tests/test_match_scale.py`.

## Consequences

- The eight paid-market OpenSpec capability specs migrated under `openspec/specs/`
  carry D4's serialization rule as a HARD RULE and were validated with
  `openspec validate --specs --strict`.
- Wave 2 transport work must route every money movement through
  `market.apply_tx()` and must land migrations 006–008 only after the renumber +
  `schema_migrations` table (D2).
- The write gate (D1) is tracked separately on `claude/write-gate`.
- AGENTS.md now carries the differential-testing convention under `## Testing`
  (D4).
- None of these sign-offs resolve the per-track open founder decisions listed in
  each spec's "## Open founder decisions" section (forward collateral/threshold/
  bucket defaults, training threshold, license registry contents, shuttle
  min-fill, carrier in-house-vs-bounty, token items + legal gates); those remain
  open.
