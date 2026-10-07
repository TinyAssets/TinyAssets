---
severity: P2
title: Account change that skips sign-out keeps the previous thread
filed: '2026-09-20'
summary: the app's account boundary is fenced at `enterSignedOut` and around the history/send awaits; a verified account or home change reaching `enterSignedIn` without a sign-out would still keep the old thread and `historyLoaded`. No live path found
---

# P2 - an account change that skips `enterSignedOut` keeps the previous thread

**Filed:** 2026-09-20 | **Verified:** 2026-09-20 against `8ca62e49` | **Severity:** P2

## Premise

`8ca62e49` closed the account boundary at **sign-out**: `enterSignedOut` now calls
`clearAccountScopedState()`, which drops the rendered thread, the in-memory send
queue, the rendered-turn sets and the `historyLoaded` / `queueRestored` /
`inflightRestored` / `uploadsRestored` marks. The awaits in `loadHistory` and
`sendTurn` are fenced on login epoch **and** owner **and** home.

What is *not* covered is a change of verified account or home that reaches
`enterSignedIn` **without** passing through `enterSignedOut`.

`setQueueOwner` (`tinyassets/onboarding/app.html`, ~line 2654) already reacts to an
owner change - it aborts pending uploads and resets `uploadsRestored` - but it does
**not** clear the thread, the send queue or `historyLoaded`. So on such a path the
previous account's conversation would stay on screen and `loadHistory` would return
at its `historyLoaded` guard.

## Why it was not fixed in `8ca62e49`

Two reasons, both deliberate:

1. **No live path was found.** Every caller of `enterSignedIn` that changes identity
   (`btn-signout`, and each `err.authRequired` / `401` handler) goes through
   `enterSignedOut` first, which bumps `MCP._loginEpoch` via `endLogin()`. The
   remaining `enterSignedIn` calls (connect/callback flows, ~lines 3389/3400/3437 and
   boot ~5355) continue the *same* login.
2. **Fixing it is not a narrow change.** Clearing from inside `setQueueOwner` means
   clearing state while the owner is mid-assignment, and `setQueueOwner` is on the
   path of the held-message tests. That is a shape decision, not a boundary patch,
   and the brief for `8ca62e49` was explicitly narrow.

## What would resolve it

Either prove no such path can exist (and record that as the reason the fence lives
only at sign-out), or have `setQueueOwner` / `setQueueScope` call
`clearAccountScopedState()` on a *change* of an already-known pair - re-ordered so the
clear runs before the new pair is assigned, with the held-message cohort re-run.

Delete this file when one of those lands.

## Related

- `openspec/changes/archive/2026-10-06-bind-immutable-run-files/` - the accepted file design this sits under.
- `tests/test_app_account_transition.py` - covers the sign-out path that IS fenced.
