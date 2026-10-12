## Why

The September 19 scheduled run reports a measured healthy current coordinator
but calls absent private REVERT evidence an outage. Its alarm threshold also
mistakes any prior workflow failure for measured Layer-1 red. Users need
truthful monitoring before the remaining hostless acceptance gaps can close.

## What Changes

- Classify existing scheduled results as observed red, observed green, or
  unknown. Missing required evidence remains visible and prevents green.
- Preserve real handshake, tool, current-executor and wiki reds; unknown in a
  different monitor cannot hide an observed outage.
- Count only measured Layer-1 red history toward the existing incident
  threshold, using Actions' existing run/attempt/step receipts, not the whole
  workflow conclusion.
- Report the scheduled Layer-2 runner's absent authorized rendered-browser
  capability as unknown before invoking a browser/LLM harness. Keep real
  rendered acceptance outstanding; do not provision a platform LLM actor.
- Keep current-engine sustained execution-quality coverage explicitly open:
  no invented private tail, new status field, or blanket user-failure alarm.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `uptime-and-alarms`: typed sub-probe aggregation, measured-red history and
  explicit unavailable scheduled rendered acceptance.

## Impact

Bounded to the existing uptime workflow, a testable result classifier if
needed, its tests, and probe documentation. No public MCP/API additions,
runtime database changes, new credentials, user content reads, or workflow
execution. No product authority changes. Existing Actions step metadata is
the receipt substrate; no new artifact or cross-run content store is required.

Owner: Codex, delegated lane `/root/engine_regressions` under the founder's
coordinated MVP push. Branch: `codex/hostless-revert-acceptance`. One PR for
this intent, not yet opened. Fable shape review gates implementation.
Evidence: `2026-09-19-hostless-monitor-contract-diagnosis.md (archived in git)`.
