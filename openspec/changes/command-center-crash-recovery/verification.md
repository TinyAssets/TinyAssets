# Verification

## Root-cause evidence

The founder desktop CDP snapshot showed build 29fa5b4, zero iframes,
AppUI.enabled=false, AppUI.frame=false, and chat-cloud data-mode=bubble.
The app service worker has no fetch handler. At the incident baseline the main.js
entry was empty and not imported; stale module hashes were a separate latent risk
(now also relevant to the renderer merged from #4489).

An isolated Chromium replay loaded the actual enterSignedIn function from
3959a39903 (git show), with the local rendered-app harness and a saved bubble
layout. Both a /app/me 503 and the server's 200 setup=unavailable response produced:

```
enabled: false
frames: 0
mode: bubble
chatBrowseVisible: false
dialogOpen: false
```

fetchMe's null/degraded reply fell through to AppUI.reset(), removing the frame;
open/openBrowse could not open the disabled controller, and the cloud's Browse
control was inside display:none. No recovery monitored frame health, and the
build check could hold three hours for a turn. The exact production request that
entered this path was not retained, so this is a reproduced mechanism consistent
with the observation, not a claim to have recovered a production network log.

One desktop inspection accidentally called refreshChatCloud() once (returned ok).
No click, typing or reload was performed there; the mistake was disclosed to the
founder. All injected faults and recovery checks ran in isolated local browsers.

## Tests

Final Windows and Linux oracle selection: **46 passed on each, no skips**:

```
tests/test_app_recovery_browser.py
tests/test_app_account_transition.py
tests/test_custom_ui_bridge.py
tests/test_custom_ui_isolation.py
tests/test_custom_ui_real_browser.py
tests/test_custom_ui_asset_delivery.py
```

The final recovery file has 18 real-browser cases, including frame removal,
frame/bootstrap and bundle exceptions, boot 503, asset 503, old module 404,
main-script parse failure, broken-page version mismatch during a live turn,
healthy-turn holds, runtime-error exclusion, disconnected bubble state,
retry exhaustion/manual Reload, and private owner/agent draft restore. The
platform default remains recognized after automatic and manual remounts.

Merged-base regression selection passed **222 on Windows** (one existing symlink
capability skip) and **223 on Linux** (no skips): recovery/module routes,
onboarding, two-surface browser controls, chat renderer and renderer browser.
Earlier affected UI parsing/adoption tests also passed (217-test selection on
Windows; the same affected regression tests passed in the Linux run).
The Windows symlink skip is not counted as a pass; Linux exercised that boundary.

Linux invocations used MSYS_NO_PATHCONV=1 and:
`python scripts/linux_oracle.py -- -q <selection above> --basetemp /tmp/b`.

Ruff passed on all changed Python/test paths. Plugin builder import probe passed;
whole-tree mirror parity: 607 canonical files matched. OpenSpec strict validation
passed. Test hygiene after the code commit: 19 added, **0 removed / 0 tampering**.

## Review and delivery

One Claude cross-family round: ADAPT; all three findings AGREE and addressed.
See review.md for the peer's full report and the reconciliation. Bundle startup
acknowledgement and asset failure reporting close the remaining readiness gap;
the same frame-source fence protects the new messages. No sandbox or bridge
permission was broadened. The final subset above verifies these changes.

Draft PR #4497 supersedes #4481 and credits tiny. Main through d1070c363c was
merged, retaining #4489 renderer and #4490 delivery changes. Main spec is synced.
This is a draft handoff, not a production deployment: deployed-SHA assertion and
post-deploy real-user proof remain required before calling it shipped.
