## Why

N3 needs live two-way voice and video without creating a second agent or model-call loop. The current browser Voice bridge and browser speech modes wrap `converse`; they do not provide a streaming `model_call` session and put turn orchestration beside S3.

## What Changes

- Add a streaming mode to owner-bound `model_call` and the existing session entry point for duplex audio, video and text over a capable connected source.
- Route authenticated realtime transport through the broker's single egress and credential custody, extending `broker-streaming-contract` beyond its response-only HTTP v1 where the source contract requires duplex transport.
- **BREAKING:** replace the auxiliary `realtime_voice.py` bridge, browser speech turn loop and `/voice/session` path with the canonical session path. Preserve explicit capture, accessibility and visible failure behavior.

## Capabilities

### New Capabilities

- `realtime-model-sessions`: Owner-bound duplex media sessions through one model-call and session path.

### Modified Capabilities

- `realtime-voice-conversation`, `provider-routing`, `provider-capability-negotiation`, `credential-vault`, `identity-auth-and-access-control`: Retire all requirements for the separate Voice bridge, capability, signaling and turn path.

## Impact

`model_call.py`, S3 session/loop, broker transport, owner-box media storage and the shared app Voice UI. Builds on N9's media-part storage rule; also depends on C3, C9 and `broker-streaming-contract`. This change specifies behavior only.
