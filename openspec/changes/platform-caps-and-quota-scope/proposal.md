## Why

Lane L2 fixes platform checks that obstruct owner-authorized work: short request-card URLs, cross-owner cooldowns, and opaque HTTP inference refusals.

## What Changes

- Accept request-card HTTPS URLs up to 8192 characters, preserving safety validation.
- Isolate cooldowns and their diagnostic details by owner and provider.
- Diagnose the inference accounting refusal and provide an actionable recovery without weakening authority.

## Capabilities

### New Capabilities
- `platform-check-scope`: Request links and owner-scoped provider checks.

### Modified Capabilities
None.

## Impact

Request validation, provider routing and cooldown callers, broker error transport, on-demand agent guidance, and regression tests. Owner: Codex, branch `fix/platform-caps-and-quota-scope`, one draft PR to main. This lane implements the narrow founder-requested slices of the broader non-usage-limit work; it does not claim that backlog.
