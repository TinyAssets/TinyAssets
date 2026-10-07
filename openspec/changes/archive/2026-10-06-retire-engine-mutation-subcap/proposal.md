## Why

The engine-edit ceiling reserves part of a universe's existing total admission
allowance for that same owner's runs. It is a workflow-allocation rule, not an
independent resource boundary. The owner asked to finish and deploy the limits
simplification; this retires one actual predicate on the route recorded in
`../consolidate-platform-resource-policy/consolidation-decision.md`.

## What Changes

- Remove the separate engine-mutation ceiling; engine edits can consume the
  existing 900 total admissions per rolling hour.
- Preserve the 300 write-run ceiling, total ceiling, atomic accounting,
  settlement semantics, fail modes, resource guards and provider authority.
- **BREAKING**: remove the retired `activity.limits.engine_mutations` status key
  and internal `engine_max` argument/helper/refusal category. Keep engine usage
  observations; do not represent absence of a cap as zero or unlimited resources.
- No storage migration, ledger replacement, workflow edits, PLAN changes or
  public MCP tool-signature changes. Broader consolidation remains unfinished.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `engine-run-admissions`: engine mutations use the total allowance without a
  category reservation.
- `live-mcp-connector-surface`: resource status reports only enforced admission
  ceilings while retaining observed category usage.

## Impact

Owner: Codex (Patches). Branch: `codex/retire-engine-mutation-subcap`.
One implementation PR, followed by same-lane shipped evidence/spec sync.
Affected: engine admission/refusal code, admin resource status, focused tests
and generated plugin mirror. No provider/workspace/effect enforcement changes.
Independent shape and exact-head review, focused baseline/candidate tests,
Linux CI, authenticated deployed-revision and public canary evidence gate ship.
Rendered owner acceptance remains distinct from technical deployment readiness.
