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
in verification.md. Integrator disposition: APPROVE after addressing findings and passing final Windows/Linux tests (verification.md).

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

## CI repair review (2026-10-05)

Claude review via peer-agents, read-only, exit 0. Verdict: ADAPT.
AGREE: include the page renderer and module loader in real-browser-proof path
triggers, alongside the recovery script and marked test file. Added both paths.
No floor or app correctness findings; no test removals or weakened assertions.

### Retained peer report

Still reviewing: diff is read, now checking what happens after the `unavailable` setup call no longer returns early.

Checking one remaining risk: whether `read_data_path` could refuse the packaged script when the data dir contains the install path.

I'd ask for one small change before approval: two files that build the recovery page aren't in the trigger list of the `real-browser-proof` workflow. I found no floor findings, and no correctness findings in the app or test code. No tests were removed or loosened, and one new browser test was added. I didn't run any tests; this is from reading the diff and the code around it.

**Finding (ADAPT, low): the browser-proof trigger list is still incomplete**
- The diff adds `app_recovery.js` and `tests/test_app_recovery_browser.py` to the paths that retrigger the workflow (`.github/workflows/real-browser-proof.yml:61,75`).
- Two files that the recovery proofs depend on are still missing:
  - **`tinyassets/onboarding/__init__.py`**: `render_app_html` now inserts the recovery script with `read_data_path` (`__init__.py:234-240`).
  - **`tinyassets/onboarding/app_modules.py`**: it supplies `chat_renderer_source` and `module_url`, and this PR changes it (`git diff --name-only origin/main...HEAD`).
- **Failure case:** a later PR that touches only `__init__.py` could break how the recovery script gets into the page, and the browser proofs would never run. The workflow's header says a proof's dependencies must be in `paths:` for it to retrigger.
- It doesn't block this PR, because this PR also touches `app.html`. The fix is to add both paths next to `app.html`.

**Checked and correct:**
- **`read_data_path` in `__init__.py`:** the packaged `app_recovery.js` sits outside the data dir, so `_data_relative` returns `None` and the file is read plainly (`universe_files.py:444-447`). A missing file still fails loudly (`raise FileNotFoundError`). The only difference from `read_text` is that line endings aren't normalised, which is harmless for inline JS.
- **Recovery nav inside the flex layout:** `.app` is a full-height flex column (`app.html:19`), so the nav now sits below the views instead of being fixed over the content. No code rewrites `#app`, so the controls survive. The safe-area padding is correct.
- **Unavailable setup:** the floor is not affected.
  - Keeping a mounted AppUI is limited to the same identity: sign-out already resets AppUI (`app.html:6357`), a home change resets it through `setQueueScope`, and stale replies are dropped by `_loginEpoch` (`app.html:6029-6031`).
  - After `fail()`, boot now continues to `showView("chat")` and the "Could not confirm…" status. That is the same chat behaviour as `origin/main:app.html:6042-6054`; the only differences are that the frame is kept and recovery is scheduled.
  - `engineConnected` now keeps its last value instead of becoming `null`. On a first boot that is still `null`; on a later re-read it is the same account's last known state.
- **`tests/test_app_connection_controls.py`:** selecting the script that contains `const CFG =` is right, now that the recovery `<script>` comes first (`app.html:871-875`). The duplicate-declaration proof still compiles the real controller.
- **`tests/test_app_hosted_model_connect.py` and `tests/app_sheet_harness.py`:**
  - The recovery source is loaded verbatim from the page, not stubbed. Switching from `node -e` to stdin only changes how the program reaches node.
  - The stubbed `setTimeout`/`setInterval` never fire, so `not navigations` in the `"unavailable"` case doesn't cover the real reload. The real browser tests cover that path with actual timing (`test_degraded_session_reply_does_not_disable_ui`, `test_app_recovery_browser.py:291`).
- **New `test_healthy_recovery_controls_do_not_cover_content`:** it checks, at desktop and phone sizes, that the controls sit below `.chat-stage`, fit inside the viewport, and leave the composer focusable.

**Context:** PR #4497's last run shows `affected-tests` shards 1-4 failed and `real-browser-proof` skipped. That is the CI this repair targets; I didn't verify whether the repair turns it green.

No collision with another lane: the files reviewed are all within this PR's own scope.

VERDICT: ADAPT
