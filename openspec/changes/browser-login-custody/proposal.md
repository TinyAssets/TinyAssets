## Why

The founder's 2026-10-09 decision (#4582) makes website sign-in the default connection for any platform, including unknown sites. No developer app, client ID or pasted credentials.

## What Changes

- One origin-labelled Sign in opens the site's actual login directly in protected owner takeover; verified success durably resumes the waiting task automatically.
- Reuse a verified remembered site/account session with zero connection taps; expose owner-only connection view and revoke/clear controls.
- Bind protected password filling to the displayed origin and live document. Device-native autofill/passkey forwarding requires further platform integration; do not pretend a streamed page supplies it.
- Chromium runs as the permanent owner UID/GID. The broker seals reusable state; `ta browser` offers structured actions without credential exports.
- Reconnect, challenge takeover and revoke use the same connection and protected view.

## Capabilities

### New Capabilities
- `browser-login-custody`: owner browser sign-in, encrypted custody and credential-blind actions.

### Modified Capabilities
None. Consume the interactive owner session and connect sheet from inline-connect-and-approve.

## Impact

Owner: Codex. Branch: `feat/browser-sign-in`, worktree: `wf-browser`, one non-draft PR. Extend the live owner launcher and broker; no migration of existing connections. Acceptance: real password/redirect login through the view, later `ta` action, revoke and foreign-owner refusal in the production Linux image.
