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


## CI repair (2026-10-05)

Merged origin/main de1004e604b734bc9e5c2fec55cda5ded5908d7c in d48565743f,
retaining the existing #4489/#4490 app changes; the merge was conflict-free.
Read failed-job logs 111701869495, 111701869643, 111701869527, 111701869511.

Root causes and repairs:
- The hosted-model sliced-JS harness omitted the independent AppRecovery script.
  A shared loader now includes the real rendered source; the harness owns DOM
  and clock collaborators, and sends Node input via stdin to avoid Windows argv
  limits. No assertions were removed. This uncovered the unavailable-setup
  early return: first sign-in now opens chat while preserving an existing frame.
- The scope probe compiled the first script, which is now the recovery bootstrap.
  It now selects the app controller script and retains every duplicate-binding
  and no-shadowing assertion for the account/home globals.
- The new recovery asset read bypassed the universe path guard. It now uses
  read_data_path and raises FileNotFoundError if the packaged asset is missing.
- The marked recovery browser test did not retrigger real-browser-proof. Added
  its path plus the recovery script, renderer and module-loader dependencies.
- The fixed, high-z-index recovery nav intercepted native handoff and preview
  consent clicks. It now occupies a nonshrinking row in the app flex layout.
  Two new Chromium cases check desktop/phone geometry and composer clicks.

Linux oracle, Python 3.11.16, uid 1001, bubblewrap 0.12.0:
**428 passed, zero failures/skips** in 415.22s. Command prefix:
`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q`, suffix
`--basetemp /tmp/b`, with these files:

```
tests/test_app_hosted_model_connect.py
tests/test_app_connection_controls.py
tests/test_universe_path_io_guard.py
tests/test_real_browser_proof_workflow.py
tests/test_app_first_run_connect_browser.py
tests/test_command_center_system_browser.py
tests/test_app_recovery_browser.py
tests/test_app_account_transition.py
tests/test_app_modules.py
tests/test_onboarding_app.py
tests/test_custom_ui_bridge.py
tests/test_custom_ui_isolation.py
tests/test_custom_ui_real_browser.py
tests/test_custom_ui_asset_delivery.py
tests/test_app_two_surfaces_browser.py
tests/test_chat_renderer.py
tests/test_chat_renderer_browser.py
tests/test_app_phone_conversation_browser.py
tests/test_app_chat_cloud_browser.py
```

Affected heavy selection from scripts/affected_tests.py: **580 passed, 17 failed,
zero skips** in 111.40s, using the same Linux command with:

```
tests/test_handoff_concurrency.py
tests/test_host_uptime_installers.py
tests/test_interlocutor_tier.py
tests/test_mcp_instruction_surfaces.py
tests/test_retire_cheat_loop_deploy_fence.py
tests/test_scoped_identity_reset.py
tests/test_universe_visibility.py
```

All 17 failures exactly match existing entries in .github/known-failing-tests.txt
for test_retire_cheat_loop_deploy_fence.py; that test and its implementation are
unchanged from origin/main. No quarantine additions or exclusions were made.

Windows hosted-model harness: 51 passed. Final workflow-trigger check after the
peer's two additional paths: 13 passed. Ruff passed on all changed Python files.
Plugin builder import probe passed; parity matched all 607 canonical files.
One Claude repair review round: ADAPT, trigger-dependency finding AGREE and fixed;
full report and reconciliation are in review.md. Main recovery spec still matches
these implementation repairs. This repair is a PR push, not a deployment claim.

Final PR hygiene: **20 added, 0 removed, 0 tampering**; exit 0.
