# Cross-family review

Reviewer: Claude through `scripts/peer_agent.py claude` (read-only), one round.
Peer verdict: ADAPT. No claim that the peer approved the subsequent edits.

## Reconciliation

1. **AGREE**: healthy-page build probes bypassed the existing live-turn/typing
   holds. The new probe now consults those holds on a healthy page; missing or
   failed frames bypass them. The deploy test now removes the frame during the
   in-flight turn. A separate test pins the healthy-turn hold.
2. **AGREE**: runtime errors were reported as boot failures. Frame error listeners
   now report only while the bundle is starting. Successful classic-script
   execution/module load ends that window; later runtime errors do not remount.
   A browser test raises a runtime error and asserts no recovery or remount.
3. **AGREE**: disconnected owners with a saved bubble layout were falsely treated
   as missing a frame. Expected-frame health now excludes a verified disconnected
   state. A browser test advances 40 seconds and checks no reload or alert and a
   working Open chat control.

The review found draft owner/home/agent fencing, retry bounds, independent controls,
module allowlisting and same-origin redirects sound. Final verification is recorded
in verification.md. Integrator disposition: findings addressed, pending final tests.

## Peer output

Checking whether bubble mode and `setup:"unavailable"` are reachable in normal states, since that decides whether the health check misfires.

Found a likely main issue: the new build probe reloads healthy pages mid-turn. Checking the tests to see whether that was intended.

I found three correctness issues, so the verdict is ADAPT. Your four focus areas mostly hold up; the problems are in triggers that fire on pages that aren't actually broken.

I worked from the diff and the cited files only. No tests were run, nothing was edited, and I didn't check the #4489/#4490 diffs for real hunk collisions, so I can't confirm or rule those out.

## Findings

**1. The build probe reloads healthy pages in the middle of a turn — `tinyassets/onboarding/app_recovery.js:88`**
`checkBuild()` runs every 60s and on every focus/online event. Whenever the live build differs from the page's build, it calls `retryFrame()` and then always `pageRetry()`. It never checks whether the page is actually broken (`failed`).
- **Failure sequence:** the owner is healthy, mid-turn (`btn-send` disabled), typing in the custom UI. A deploy lands. Within 60s, or on the next tab focus, the frame is remounted, losing its state, and the page reloads 2s later. Only the composer text is saved.
- **Why it matters:** the existing `checkForNewBuild` `held()` rule forbids exactly this, and the 2026-08-29 reload-under-a-turn incident is why that rule exists. The design says "Broken pages bypass live-turn holds", but the code bypasses them for every page.
- **The test pins the regression:** `test_deploy_mismatch_ignores_live_turn_and_preserves_draft` checks the reload with no fault injected.
- **Fix:** gate the mismatch branch on `failed` / not healthy, or apply `held()` when the page is healthy.

**2. Any error in a custom UI after it has started is treated as a startup failure — `tinyassets/onboarding/ui_frame.py:99-100`**
The new `error`/`unhandledrejection` listeners call `fault()` at any time, not only during startup. `fault()` now posts `boot_failed`, which sets `AppUI.bootFault=true`. That flag is only cleared by a remount, so `health()` stays unhealthy.
- **Failure sequence:** a user- or agent-authored bundle that is working fine leaves one bridge rejection uncaught, for example a refused action. That produces:
  1. the "This UI failed while starting." overlay;
  2. a frame remount;
  3. on the next error, two full page reloads at 2s and 8s;
  4. a permanent "Something went wrong" banner.

  The bundle is still selected after each reload, so the cycle repeats every load.
- **Fix:** only report faults that happen before the frame's `ready`, or keep runtime errors separate from boot failures.

**3. Not-connected owners with a saved bubble layout get reloaded for nothing — `tinyassets/onboarding/app.html:10367`**
When `engineConnected===false`, `enterSignedIn` calls `AppUI.reset()` (UI disabled, no frame). But `cloudResolveState` restores a saved `mode:"bubble"` no matter the connection state. `health()` then reports expected=true and healthy=false indefinitely.
- **Failure sequence:** an owner who shrank the chat to a bubble while connected later loses their model connection. On each load:
  1. the recovery script fails after 15s;
  2. `retryFrame` returns false;
  3. the page reloads twice;
  4. the permanent failure banner appears.

  This is a normal state that reloading can't change.
- **Fix:** don't count "not connected" as expected-to-have-a-frame, whatever the cloud mode.

## Checked and fine
- **Draft privacy:** a draft is saved only with a verified owner and home. It is restored only when owner, home and addressed agent all match, and only after `setQueueOwner`/`setQueueScope` run from the verified `/app/me` and pass the epoch check. Nothing is ever sent automatically.
- **Retry bounds:** at most 2 page retries per session (2s, then 8s), shared across reloads via `sessionStorage`. If storage can't be read or written, it falls back to the visible banner and stops. The budget resets only after 30s of continuous health. A manual Reload clears the budget.
- **Controls after parse/boot faults:** they live in a separate nonce script with a capture-phase click handler, outside the main script's failure path. Every unhandled case falls through to `navigate(true)`.
- **Module redirect:** it applies only to allowlisted names and stale hashes of exactly 16 lowercase hex characters. It returns a no-store 307 to `module_url(name)`, which is same-origin, so it is not an open redirect. Anything else still gets a 404, and the public path shape the auth exemption covers is unchanged.

VERDICT: ADAPT
