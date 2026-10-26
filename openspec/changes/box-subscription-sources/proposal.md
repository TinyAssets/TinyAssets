## Why
The owner-box cutover removed subscription sources, leaving subscription users without a model. Restore them through editable source data and one owner-box runtime.

## What Changes
- Pinned on-demand packages, native login URL/code handoff, durable owner login state, and generic non-interactive invocation.
- Subscription source registration, model selection, and definition-generated connect buttons.
- Linux fake-package proof; no production access or image rebuild.

## Capabilities
### New Capabilities
- `box-subscription-sources`: Owner-volume subscription CLI installation, login and inference.
### Modified Capabilities
None.

## Impact
Owner daemon and harness Python, broker source descriptors, connect-page JavaScript, and source JSON. One branch and draft PR into conv/c6-forward-deploy. No shared credentials or host CLI execution.
