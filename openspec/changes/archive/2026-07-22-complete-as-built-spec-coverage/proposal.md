## Why

The 2026-07-19 OpenSpec baseline intentionally excluded install, desktop, and
website surfaces, and later runtime work added more behavior without canonical
requirement coverage. `openspec/specs/` therefore cannot yet prove the project's
claim that canonical specs describe the full as-built platform.

## What Changes

- Add strict as-built capability specs for eight shipped surfaces that have no
  current canonical owner.
- Ground every requirement in current code/tests/workflows and state current
  limitations without promoting the full-platform target into shipped truth.
- Preserve active-change ownership: this change does not duplicate connector
  manifest reconciliation, distributed execution, universe work, or any other
  in-flight delta.
- Record the remaining enrichment and unbuilt-target gaps in a durable coverage
  audit so subsequent file-bounded changes can finish the program.

## Capabilities

### New Capabilities

- `domain-plugin-runtime`: Installed and editable-worktree domain discovery,
  registration, protocol, isolation, and reference-domain contracts.
- `daemon-identity-and-host-pool`: Daemon identity/control, host
  registration/heartbeat, and current bid-polling contracts.
- `evaluation-runtime-and-scenarios`: Generic evaluator/result interfaces,
  layered evaluators, outcome adapters, and acceptance-scenario execution.
- `desktop-host-runtime`: Current source-installed launcher, tray, dashboard,
  notifications, shortcuts, and host control behavior.
- `development-coordination-runtime`: Claim/worktree/context/drift tooling and
  Agent Village observation/dispatch contracts.
- `constraint-evaluation`: ASP rule loading, surface conversion, validation,
  synthesis, and current degraded-mode behavior.
- `oss-clone-and-install`: Fresh clone, supported install, import/smoke, and
  failure-escalation behavior exercised by the Tier-3 workflow.
- `public-website-surface`: Built website routes, data provenance, public
  proof/status presentation, and truthful host/install copy contract.

### Modified Capabilities

<!-- none in this collision-safe batch; enrichment of existing canonical specs
     is deliberately sequenced after active-delta collision checks -->

## Impact

- New delta specs under
  `openspec/changes/complete-as-built-spec-coverage/specs/`, later synced to
  matching new `openspec/specs/<capability>/spec.md` files.
- Durable evidence ledger:
  `2026-07-22-openspec-full-coverage-audit.md (archived in git)`.
- No runtime, API, storage, packaging, website, or deployment behavior changes.
- Existing tests and workflows are evidence sources only. Public-surface runtime
  acceptance is not re-claimed by this documentation-only change.
