## Why
The connect screen promotes one provider and stale setup hides alternatives.

## What Changes
- Remove setup.primary and list every available installed acquisition and source preset equally.
- Keep one-tap OAuth first, alternatives visible, and abandon stale flows silently on source choice.
- Center the default desktop chat and request dialog; preserve phone sizing.

## Capabilities
### Modified Capabilities
- `first-run-model-connect`: neutral source choice and interruptible recovery.

## Impact
Lane: U. Owner: Codex. Branch: fix/connect-any-source. One PR, no production changes.
Server setup payload and onboarding HTML/JS; existing connection authority and selection policy remain authoritative.
