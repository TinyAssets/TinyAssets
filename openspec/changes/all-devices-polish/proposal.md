## Why

App sign-in must finish on the device that started it, without embedded Google OAuth failures. The browser login view and native packages must be usable on phones and desktop.

## What Changes

- System-browser sign-in and PKCE-bound opaque returns for Android, iOS and Electron; web sign-in stays unchanged.
- Responsive private browser input, honest autofill/passkey support and assessed WebSocket egress.
- Matching launch backgrounds and Windows/macOS/Linux packages, plus mobile build validation.

## Capabilities

### New Capabilities
- `native-app-sign-in`: system authentication and one-use opaque app return.

### Modified Capabilities
- `browser-login-custody`: private input ergonomics and browser transport.

## Impact

Owner: Codex. Branch: feat/all-devices-polish. Browser-view work builds on PR #4585; approval return builds on merged #4567. App routes, native shells, desktop build workflow and relevant tests change. Store uploads remain founder-owned. No receipt artifact.
