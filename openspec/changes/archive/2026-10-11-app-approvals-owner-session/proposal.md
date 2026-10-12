## Why

All Needs you owner sign-ins fail because the Cloudflare Worker strips the browser-binding Set-Cookie header. A live unauthenticated probe on 2026-10-09 returned 307 to AuthKit with no Set-Cookie. Native shells also have separate cookie stores, and the stateless frontend swallows server-owned owner-login callbacks.

## What Changes

- Open the existing request sheet in the system browser with an opaque, owner-bound handoff.
- Keep decisions behind browser owner proof; return only a completion reference and refresh the app list.
- Proxy owner callbacks through the stateless frontend.
- Preserve only named, host-only, Secure/HttpOnly app cookies at the edge; continue stripping tunnel Access and unknown cookies. Log secret-free callback refusal reasons.

## Capabilities

### Modified Capabilities
- `onboarding-web-app`: protected browser handoff for native request sheets.

## Impact

Owner sessions, app routing/UI, frontend proxy, Electron return handling, regression tests. This is the native approval bug slice of inline-connect-and-approve, not its provider-connection work. Owner: Codex; branch: fix/app-approvals-owner-session; one PR.
