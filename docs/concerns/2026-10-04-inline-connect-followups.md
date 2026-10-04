---
severity: P2
title: Inline connect and approve follow-ups from the #4449 and #4452 reviews
filed: '2026-10-04'
summary: double sign-in on a fresh account's first connect, a stuck second tab after another tab connects, two transports for hosted model connect, and slice-1 lock/labelling/rollback items
---

# Inline connect and approve: review follow-ups

**Found:** 2026-10-04, Claude cross-family reviews of #4449 (slice 1, APPROVE) and
#4452 (first-run model connect, APPROVE). None of these is cross-user or a single-tab
data-loss hole. Fix them after the live user pass ("shape before hardening").

## First-run model connect (#4452)

1. **Double sign-in: fixed for fresh web sign-in; native remains.** Normal web
   sign-in now uses `/app/owner-sign-in?app=1`: the existing server-PKCE,
   browser-cookie-bound callback sets both `__Host-ta-owner` and the app refresh
   cookie. The first inline Connect goes directly to OpenRouter. Client-PKCE
   exchanges, refresh handles and app/MCP/CLI bearers still cannot mint owner
   proof; copied callbacks, cross-user launches and CSRF remain rejected.
   Existing signed-in sessions without owner proof still need protected sign-in,
   as do web sessions after the eight-hour owner cookie expires (app renewal can
   last seven days). A pending failed logout is completed before new web sign-in.
   Native app sign-in still opens the system browser, returns the code to the
   WebView and exchanges it with the WebView-held verifier. Its cookies do not
   establish owner proof in the system browser. With no live owner cookie there,
   first Connect still requires TinyAssets sign-in followed by OpenRouter; the
   completion page tells the user to return to chat manually. This applies to
   the separate browser context on Android/iOS; this patch adds no iOS handoff
   support (the existing app-login bounce emits an Android package intent).
   Later connects reuse a live owner cookie in that same browser. Eliminating
   the native detour needs a separately designed browser-bound login/app handoff,
   not a bearer or launch-URL upgrade. Record this remaining flow in App Store
   review notes. Implementation/test evidence: `tests/test_app_owner_sign_in.py`.
2. **The second tab looks stuck.** The tab that loses the atomic `take` keeps
   `needsConnection` in memory, polls `/app/me` every 1.5 s, never shows Connected,
   and queues every new message until a reload. `save()` matches only on message
   and timestamp, so a losing tab can write `needsConnection` back to disk. That
   leaves a narrow replay window if the winner's send then ends unconfirmed. Fix:
   clear the record when the saved message is already claimed, and have `save()`
   refuse when the record on disk lacks `needsConnection`.
3. **Account-switched browser loop.** If the browser's identity-provider session
   belongs to a different TinyAssets account, `return_path` sends the popup to
   `/app` and Retry loops.
4. **Native Retry:** after `begin`, Connect stays disabled until Cancel.
5. **Two transports for one fact.** `HostedModelConnect` keeps its verifier in tab
   storage and redirects, while the inline path keeps it server-side
   (`inline_model_flows` in owner-sessions.db). Fold them into one.
6. **History duplicate:** `record_failure` stores the refused attempt and the
   resumed turn stores the message again. Check reloaded history and model context
   in the live pass.

## Approval cards (#4449)

7. **DEPLOY PRECONDITION: no rollback past the protected-request storage
   cutover.** After the cutover, an old binary reads a stale legacy snapshot. An
   answered request looks pending again, and an old answer handler that runs an
   effect before its status write can duplicate that effect.
8. The `owner_control` lock is non-blocking. Overlap raises `ControlUnavailable`,
   and inside the effector's `capture()` that turns a clean ask_first refusal into
   a raised error. A short blocking wait fixes it.
9. Effector refusals before any send are recorded as `unknown` ("may have
   completed"), which permanently blocks re-approval.
10. Reads raise while the migration is paused. The design says reads should use
    the last authoritative store.
