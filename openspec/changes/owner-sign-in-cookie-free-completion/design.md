## Context

Real Chrome omits the owner flow cookie on the cross-site return. The edge passes it correctly. The existing flow already owns an encrypted PKCE verifier, a ten-minute deadline and a single-use state.

## Goals / Non-Goals

Complete fresh owner sign-in using either the flow cookie or an independently authenticated same-origin bearer whose user matches the IdP exchange. Keep all approval operations behind the owner cookie. Do not transfer WebView credentials to a system browser.

## Decisions

- Preserve cookie completion. A missing cookie stages the callback code and state encrypted server-side in an expiring completion row, then redirects to the app with an opaque fragment handle. The app POSTs that handle to `/app/owner-sign-in/complete`; the code never enters app script. This resolves the tension between POSTing code+state and prohibiting codes in page script.
- Require exact public Origin, JSON and a resolved bearer identity for completion. Consume the flow atomically before exchange, require IdP user equality, and reuse owner-session/cookie issuance. A bearer alone, handle alone, replay or mismatched identity cannot establish proof.
- Refresh the browser's existing app session if necessary before completion. If none exists, fail visibly and require sign-in again; a native handoff never imports shell credentials.
- Retain expired login rows for ten additional minutes for secret-free refusal diagnostics. The authorization deadline remains unchanged. Log only cookie names, fetch metadata, Referer origin, flow age and enumerated refusal reason/status.
- Existing handoff/inline-return cookies become available to the same-origin POST and determine the return destination after owner checks.

## Risks / Trade-offs

- A bearer-less first sign-in with a lost cookie cannot safely complete under this binding; refuse rather than treat fresh tokens as their own CSRF proof.
- Concurrent or repeated callbacks must not replace a staged code or extend expiry. Consumption of one path invalidates all other paths.

## Migration Plan

Create one ephemeral completion table alongside existing flows; no existing identity data changes. Deploy server and app together. Old flow-cookie callbacks remain valid. Rollback leaves only expiring inert completion rows.
