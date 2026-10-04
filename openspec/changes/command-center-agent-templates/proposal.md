## Why
Published command centers copy screens and workflows but omit the explicitly configured agents their screens address. File-package `agents` entries are instruction-file inventory, not private runnable bindings.

## What Changes
- Export explicitly selected, stably keyed public agent definitions through owner-confirmed publication.
- Create fresh recipient-private configured bindings after copy consent, preserving all existing agents and settings.
- Remap declared UI agent references and refuse unsupported workflow dependencies before any copy.
- Describe absent agent templates honestly; never fill gaps from private publisher data.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `universe-custom-agents`: selected public instruction templates install into independent private bindings.
- `command-center-discovery`: declared agent references are copied and missing/unsupported dependencies refuse before effects.

## Impact
Publication/install APIs, UI reference validation, an additive agent-template module, and focused consent/isolation/replay tests. Frontend wiring follows in the integration owner's UI patch. No live design is changed or republished.
