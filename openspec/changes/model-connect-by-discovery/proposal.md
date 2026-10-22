## Why

Connecting an unfamiliar model source currently requires endpoint knowledge and model confirmation only recognizes installed presets. The default must accept a provider address and discover its capabilities without provider code.

## What Changes

- Add address-based model API and OAuth discovery, reusing PKCE and broker custody.
- Offer a discovered API-key fallback and refuse unknown model APIs.
- Compose discovered sources into the existing HTTP model shape.
- Add a small top entry in the connect dialog, compatible with lane U2.

## Capabilities

### New Capabilities
- `model-source-discovery`: address-based model connection and exact endpoint binding.

### Modified Capabilities
None.

## Impact

Router onboarding ingress and flow, broker discovery, box source composition, onboarding dialog, focused fake-provider tests. No production actions. Owner: Lane X; branch: feat/model-connect-by-discovery; draft PR targets conv/c6-forward-deploy.
