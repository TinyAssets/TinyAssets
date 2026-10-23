## Why

N9 requires `model_call` to return images, audio and video as first-class results. Its current response is text and tool calls, so media cannot be attributed to a particular metered call.

## What Changes

- Extend `model_call` results with ordered, typed media parts and one execution receipt per call, including failed or interrupted calls.
- Keep media bytes in the owner's box and expose owner-scoped references where a caller needs to render or retrieve them.
- Decode media according to each connected source's declared or discovered contract, with no vendor-specific branch or alternate model path.

## Capabilities

### New Capabilities

- `model-media-results`: Typed model output parts, owner-box bytes and per-call receipts.

### Modified Capabilities

None.

## Impact

`model_call.py`, provider response normalization, owner-box media storage and consuming UI/API surfaces. Depends on C3; this change specifies behavior only.
