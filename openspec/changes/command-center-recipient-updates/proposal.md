# Recipient update planning

## Why
Copied command centers have no trustworthy update relationship today. Recipients need to inspect release summaries and select changes without losing their edits or granting new authority.

## What Changes
- Define immutable author-bound release chains and per-component adoption baselines.
- Implement a pure three-way update planner with dependency, edit, deletion and permission guards.
- Admit automatic plans only after per-adoption opt-in and only within accepted authority; preserve automation runtime state.
- Keep unknown legacy provenance unverified. Add a real owner-fenced registry and explicit-consent adapter for atomic existing-screen replacement, preserving all other copied components. Public routes and trusted consent UI are integrated by the main delivery lane.

## Capabilities
### New Capabilities
- `command-center-updates`: Safe recipient update provenance and planning.
### Modified Capabilities
None.

## Impact
New planner, registry and callable API modules with real SQLite tests. No existing shared installation, publication, provider or browser source files change in this lane. Registry tables and the manual UI transaction are new; route registration and trusted consent UI remain integration work.
