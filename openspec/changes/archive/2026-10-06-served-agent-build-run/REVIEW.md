# Cross-family review record — served-agent-build-run

Opposite-family (Codex) adversarial reviews of the served build/run/edit surface, per
AGENTS.md § Quality Gates. Each slice gated LANDING on an approve/adapt verdict.

## write_graph CREATE (2026-08-23)
Codex ADAPT ×2 rounds (6 → 2 → 0). Every known path to a persisted APPROVED source_code
node closed; `_sanitize_served_branch_spec` strips approval/author/fork, rejects
node_ref/nested-graph/invoke, forces private, size/node/type guards. Shipped + deployed.

## channel/consent slice — source_channel + authenticated_external_call effect (#2517, 2026-08-24)
Codex ADAPT → fixes → **APPROVE**. Fixed: duplicate-sink effect amplification (each node
`effects` must be `[]` or `[authenticated_external_call]`); non-string consent payload
crash. Deployed to prod (`5fe6a19b`) + live-tested via the connector.

## write_graph PATCH — edit an own branch (#2518, 2026-08-24)
Codex ADAPT → fixes → re-review → **APPROVE**. Fixed: update_node re-activation
(execution/data-authority field allowlist), cumulative effect-node bypass (no effect
nodes via patch), malformed-field crash/persist (per-op type validation incl. node +
state-field `description`), false idempotency docstring.

### Tracked residuals (accepted for the single-founder u-tiny deployment; NOT flip-on for multi-tenant)
- **Author-scoped, not universe-scoped (create + patch).** Branches carry no `universe_id`;
  authority is `actor == branch.author`. A founder cannot cross into another *actor's*
  branch, but has no per-universe isolation of their own. Closing this is the
  branch↔universe binding — the pre-multi-tenant harden gate for the whole surface.
- **No expected-version CAS in patch_branch (patch-specific).** `_ext_branch_patch` reads →
  stages → `INSERT OR REPLACE` with no optimistic-concurrency token, so concurrent served
  patches / retries can lost-update. Acceptable as post-live concurrency hardening for a
  single founder; the durable fix is an `expected_version_id` compare-and-swap returning a
  structured conflict for stale writers.
