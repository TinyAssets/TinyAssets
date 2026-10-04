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

1. **Double sign-in on a fresh account.** The `__Host-ta-owner` cookie is set only
   by `/app/owner-sign-in`, so a brand-new account's first Connect detours through a
   TinyAssets sign-in before OpenRouter (in the popup on web, in the system browser
   on native). The App Store reviewer does two sign-ins in Safari and then switches
   back by hand. Either remove the detour (set the owner session at app sign-in) or
   say so in the review notes.
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
