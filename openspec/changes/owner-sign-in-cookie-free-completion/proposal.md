## Why

Production Chrome returns from interactive AuthKit/Google sign-in without the flow cookie despite a working public-edge cookie round trip. Owner approval must complete without weakening interactive proof or login-CSRF protection.

## What Changes

- Log secret-free callback refusal context and explain expired links.
- Add same-origin bearer-bound completion of an unexpired, single-use server PKCE flow when the browser omits the flow cookie.
- Preserve cookie-based completion and web, Electron and native approval handoffs.

## Capabilities

### Modified Capabilities
- `onboarding-web-app`: cookie-independent interactive owner sign-in completion.

## Impact

Owner session storage, callback and completion API, app bootstrap and routing tests. Owner: Codex; branch: fix/owner-sign-in-cookie-free-completion; one non-draft PR. Fresh IdP identity must equal the independently authenticated bearer identity; bearer possession alone remains insufficient.
