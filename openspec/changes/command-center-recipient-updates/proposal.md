# Recipient update planning

## Why
Copied command centers have no trustworthy update relationship today. Recipients need to inspect release summaries and select changes without losing their edits or granting new authority.

## What Changes
- Define immutable author-bound release chains and per-component adoption baselines; register releases only from explicit publisher-approved lineage pins.
- Implement a pure three-way update planner with dependency, edit, deletion and permission guards.
- Persist explicit per-adoption presentation-update opt-in and report conservative eligibility within accepted authority; preserve automation runtime state. No scheduler or automatic executor is activated.
- Keep unknown legacy provenance unverified. Add a real owner-fenced registry and explicit-consent adapter for atomic existing-screen replacement, preserving all other copied components. Public routes and trusted consent UI are integrated by the main delivery lane.

## Capabilities
### New Capabilities
- `command-center-updates`: Safe recipient update provenance and planning.
### Modified Capabilities
None.

## Impact
New planner, adoption registry, publisher release-series, recipient policy and callable API modules with real SQLite tests. No existing shared installation, publication, provider or browser source files change in this lane. Registry tables and the manual UI transaction are new; route registration and trusted consent UI remain integration work.
