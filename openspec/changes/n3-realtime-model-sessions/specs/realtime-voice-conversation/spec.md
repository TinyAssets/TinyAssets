## REMOVED Requirements

### Requirement: Voice mode is explicit, foreground-only, and accessible
**Reason**: Its bridge and browser speech selection are replaced by the canonical realtime session; capture and accessibility move to `realtime-model-sessions`.
**Migration**: Use the existing session entry in streaming mode.

### Requirement: Realtime transport uses WebRTC with a provider-neutral bridge contract
**Reason**: A direct browser media peer is outside the broker's one-egress contract.
**Migration**: Use the bound duplex broker stream declared by the connected source.

### Requirement: Voice turns relay through the canonical converse operation exactly once
**Reason**: Its Voice-side `converse` callback is a turn path beside S3.
**Migration**: Commit media input to the owner session and call `model_call` through S3.

### Requirement: Disconnect and expiry recovery never silently duplicate a turn
**Reason**: Recovery belongs to the canonical session and broker operation status.
**Migration**: Use the realtime session's committed history and per-call outcome.

### Requirement: Audio privacy and resource use are disclosed before capture
**Reason**: Disclosure moves with the unified capture surface.
**Migration**: Apply `realtime-model-sessions` disclosure and owner-box retention requirements.

### Requirement: Voice sessions are bounded and fail visibly
**Reason**: Session lifecycle moves to the canonical owner session.
**Migration**: Use bounded broker streams and typed session outcomes.

### Requirement: Voice transport status does not obscure conversation progress
**Reason**: The separate Voice status is retired.
**Migration**: Render the canonical session's progress and settled call results.

### Requirement: The shared app reports each turn's input method
**Reason**: The separate Voice turn record is retired.
**Migration**: Store input-part modality with the owner-box session turn.
