# Recipient update planning

## Why
Copied command centers have no trustworthy update relationship today. Recipients need to inspect release summaries and select changes without losing their edits or granting new authority.

## What Changes
- Define immutable author-bound release chains and per-component adoption baselines; register releases only from explicit publisher-approved lineage pins.
- Implement a pure three-way update planner with dependency, edit, deletion and permission guards.
- Persist explicit per-adoption presentation-update opt-in and apply eligible updates within accepted authority; preserve automation runtime state. The existing admitted service maintenance loop runs bounded executor and settlement sweeps every cycle (after its 300-second sleep), under the reset writer barrier.
- Separate erasable owner-keyed publisher evidence from immutable public release history, so publisher deletion removes private source identity hashes, home IDs and request IDs without changing release IDs or another owner's adoption history.
- Keep unknown legacy provenance unverified. Add a real owner-fenced registry and explicit-consent adapter for atomic existing-screen replacement, preserving all other copied components. Public routes and trusted consent UI are integrated.

## Capabilities
### New Capabilities
- `command-center-updates`: Safe recipient update provenance and planning.
### Modified Capabilities
None.

## Impact
New planner, adoption registry, publisher release-series, recipient policy and executor modules with real SQLite tests. Publication/install hooks, owner routes, trusted browser controls and the existing service maintenance loop integrate these modules. Saved opt-in defaults off; controls distinguish it from worker availability and pending accounting settlement.
