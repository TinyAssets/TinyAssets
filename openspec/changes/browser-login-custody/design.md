## Context

Split from connect-anything-ladder after round-1 shape review; preserves its custody and lifecycle contract.

## Goals / Non-Goals

Connect login-only sites through owner-bound credential custody. No new card, request authority, provider-specific code or platform LLM.

## Decisions

Dependency order: `connect-anything-ladder` connection metadata/lifecycle and secret entry, then completed universe-agent-harness D5 browser/live-view substrate; consume the card owned by `inline-connect-and-approve`. D5 is a release gate: no browser fallback until its real broker passes custody acceptance.

### Browser custody through D5

Reuse D5's owner/center/activity-bound browser context, live view and Take over/Return control. Add a daemon-owned login session bound to owner, connection draft/incarnation, expected origin, initiating owner session, task and expiry. The inline control launches a protected owner-only capture view; credentials, MFA codes, cookies and session storage flow to the daemon/browser broker and never through an agent message or extension payload. The agent receives only an opaque surrogate handle and public status. Surrogates authorize broker use under current owner permissions; they cannot be exchanged for raw vault material.

During login takeover, agent input, screenshots, DOM snapshots, network/body inspection and traces of the capture context are suspended. The broker suppresses password/OTP values and cookies from artifacts, error payloads and logging, not just from the chat renderer. Credentials may be injected only into the bound origin/context; redirects to a new credential-receiving origin require a new explicit binding in the protected capture view. Cross-origin pages cannot redeem capture handles. On successful return, discard login traces and expose only the ordinary authenticated page state with credential-bearing fields/headers excluded. Authenticated content is available according to owner permission; reusable credentials are not.

Credentialed browser contexts stay in the daemon broker's isolated process/profile, inaccessible to the agent's shell or filesystem. The agent interface offers structured navigation, click, nonsecret input and sanitized rendered-page observations; it offers no arbitrary JavaScript/evaluate, DevTools/CDP endpoint, cookie/storage export, request interception, profile download or raw network bodies/headers. Page scripts may use their own storage to function, but the agent cannot evaluate document.cookie or local/sessionStorage, read password/autofill fields, or retrieve authentication-bearing URL fragments. Scrub known captured/session credential values from allowed observations and never return the login page's retained input state. If the broker cannot safely expose a site's post-login surface, keep it owner-only and report that limitation instead of claiming credential-blind agent access. Browser implementation must demonstrate these controls before enabling the fallback; unrestricted evaluation plus output redaction alone does not satisfy this contract.

Where a passkey or challenge needs the owner, keep takeover open with truthful waiting state. A disconnected/expired login returns to the same card. Cancellation/Stop destroys staged session artifacts and cannot promote a late callback. Reuse vault/session cleanup and account-deletion lifecycle, including scoped backups, rather than retaining abandoned browser profiles. Logout from TinyAssets invalidates capture sessions; disconnecting a browser connection additionally revokes its reusable browser session. No claim is made to undo already-completed remote actions or delete the upstream account.


## Migration Plan

Preserve existing records and grants; version new metadata, fail visibly on unsupported schemas, and retain revocation/history/cleanup during rollback. Reuse incarnation fencing, coordinator idempotency and processed-ack continuation.

## Risks / Trade-offs

Remote challenges or failed self-tests remain visible. Never label a failed connection active. Cross-user isolation remains the fixed floor.
