## Why

Saving an addressed room message does not invoke the native agent. Owners need a workflow they can install without creating a second agent runtime or an unbounded reply loop.

## What Changes

- Provide an editable skill example that builds a private owner workflow around an existing canonical room adapter and the existing native agent node.
- Persist bounded claims before generation, actual results before posting, and idempotent commit receipts; uncertain generations do not retry automatically.
- Keep activation disabled and separate from installing the code.

## Capabilities

### New Capabilities
- `native-room-replies`: owner-authored addressed native replies using existing automation and workflow infrastructure.

### Modified Capabilities
None.

## Impact

New skill scripts and fixture tests only. No global dispatcher, platform endpoint, default automation, credential, private room identifier, provider selection or live activation.
