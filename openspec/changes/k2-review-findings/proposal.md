## Why

K2 review found that a read-only signed backend grant can mutate extension state,
the thin loop exposes extra handles, and non-granted Claude turns retain WebFetch.

## What Changes

- Enforce mutation authority once at the ta dispatcher, including extension lifecycle.
- Remove direct history/activity handles; keep reads through ta read_graph.
- Disable native tools for non-granted served turns on both providers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `universe-agent-harness`: signed mutation authority and provider-neutral model inventory.

## Impact

ta dispatch, thin-loop tool sessions, Claude launch configuration, regression tests,
and the generated Claude plugin. No storage migration or public endpoint change.
