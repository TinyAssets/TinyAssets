# Proposal

## Why
Native metadata enumeration currently starts an agent executable outside the provider jail. Its owned credential snapshot does not restrict access to other command centers or platform files. This prerequisite repairs that existing boundary before any additional metadata adapter is enabled.

## What Changes
- Route registered metadata transport through the existing confined owned-process launcher with explicit owner and snapshot scope.
- Bind only this launch's snapshot, use private temporary runtime storage, and do not inherit an engine route.
- Preserve bounded protocol parsing, custody revalidation and cancellation-safe process-family cleanup.
- Refuse before launch when confinement is unavailable; prove cross-owner and platform-secret denial with synthetic executables.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `agent-model-selection`: native metadata enumeration requires the same OS confinement floor as inference.

## Impact
Native discovery transport, base adapter integration, necessary owned-process/jail integration, canonical runtime mirrors and dedicated tests. Existing Codex wrapper install-tree registration must be reused without widening mounts. No new adapter, grant, credential, host setting, live provider test or authority-store change. The recorded unjailed-discovery concern remains until required real-jail and deployment evidence exists.
