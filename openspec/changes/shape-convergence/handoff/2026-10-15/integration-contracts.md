# Convergence checkpoint for parent integration

Current priority is Roundtable. Its PR4861/4864 were not edited. No merge, deployment, live migration, credential creation or permission expansion occurred here.

## Published drafts

| Lane | Draft | Published content |
|---|---|---|
| C7/C9 core | https://github.com/TinyAssets/TinyAssets/pull/4939 | Session binding before owner-file load; inherited child/reconnect authority; explicit terminal cancellation; mandatory thin adapter; shared execution loop; task-aware provider admission; off-loop/fenced commits. Reuses PR4845 and unchanged PR4920. |
| C9 stream | https://github.com/TinyAssets/TinyAssets/pull/4940 | Async credential-blind broker stream, shared connection, scanned transient text events. Depends on the separate C6 seam below. |
| C10 activities | https://github.com/TinyAssets/TinyAssets/pull/4938 | Branchless activity admission/cancellation, v1 extension-loader retirement. Head410dad284bf1. |
| C10 engine | https://github.com/TinyAssets/TinyAssets/pull/4937 | Shared registration and paid-market retirement. Head6540bdcb4717. Published head requires excluded shared patches before it runs. |
| C11 UI | https://github.com/TinyAssets/TinyAssets/pull/4936 | Packaged JS modules; durable client send ID; unified typed/voice send lifecycle; transient progress-stream parsing. Head0742c1e94a48. Original shell remains active pending C12 composition. |

Root branch codex/c7-c9-session-convergence starts at C7 c1eb3d446909b5cdc534053d8d784701c0eaed70 and merges C6 bc418c32116eed6954435305b5526966fa33f29a unchanged, yielding anchor922b871970373a279d5c0b5b407881d5bb99295c. C9 prerequisite PR4642/4666/4752 were already merged. Root new core commitcdd668a5 is a cherry-pick of0109758c. Worker drafts are stacked on the root branch or the explicit C9 core branch; reconcile bases before merging.

Root final published head: b1a1e82a8ca2850a85688dea02e8ce10d3f77df5. Core repair f7144464 was cherry-picked as b1a1e82a; the root worktree is clean; the C10 engine worktree retains the two excluded shared-file edits (now also preserved in c10-uncommitted-worktree.patch). New platform lock helper is POSIX-only and explicitly refuses Windows.

## Shared-file composition contracts

1. **C6 broker seam:** c9-aclient-inference-usage.patch adds optional inference_usage to AsyncBrokerClient.stream and forwards it in OPEN. Stream draft excludes this C6-owned file. Tests reported for streaming ran on a locally composed tree; the published head is not independently runnable. No approval was received to edit the seam.
2. **C6 native launch:** native_launch.py already accepts explicit selected git connection grants and retains bound BoxLauncher authority. external_agents/runner.py and home.py still require host directory/executable paths; native_discovery still constructs CellLauncher. Coordinate those changes and old-cell deletion with C6, without inventing connections or credentials.
3. **C10 activity composition:** preserve root session.py box_binding fields and _binding-before-load ordering while composing C10's Activity(RunStep) parent and activity dispatch; retain provider._run_id as stable run identity. PR4938 tested its own branch (323 passes), not the final combined tree. Invoke/await parity and custom Activities instructions/queued-work migration remain undone.
4. **C10 market composition:** c10-extensions-retirement.patch removes retired escrow imports/dispatch/parameters from C12 api/extensions.py (based on C12 b098b1e0). c10-role-writer-retirement.patch removes stale bid-only probes from a shared C6 surface. Parent must reconcile owner_stores inventories and S7 guard to check one registration/construction boundary. PR4937's local passing evidence includes excluded patches; exact published head is not green.
5. **C11 shell composition:** c11-c12-shell-integration.patch composes app.html extraction and onboarding/__init__.py renderer with C12 b098b1e0 and updates the voice assertion. New modules alone do not activate the shell. After composition, source-reading tests must use app_modules.shell_source (onboarding recovery/terminal frames, UI by talking, Android identity, command-center copy, sign-in, custom UI isolation, mobile background, and C12 account-deletion/billing readers). Existing privileged operation guards remain; generic operation authority convergence is unfinished.
6. **C9/C11 progress bridge:** C9 model_stream.observe_model_stream(callback) emits model_start/model_text_delta/model_end dictionaries with operation_id, text and terminal status. In universe_server._register_structured_tool/_owned_tool, bind before copy_context/executor scheduling, enqueue events thread-safely, and emit report_progress(message=json.dumps({'tinyassets_model_stream':1, **event})). C11 sends tools/call _meta.progressToken=frame.id and accepts only matching notifications/progress. Final journaled tool result remains canonical. This server seam has not been implemented.

## Remaining C7/C9 implementation and production gates

- tick._paused still reads owner .pause; _publish_heartbeat still writes inside the owner directory. Move these with all platform readers/watchdog writers. Read-only audit ran70 scheduler/duty/wake tests successfully; no partial rename was made.
- tick.stop uses executor.shutdown(wait=False). Existing liveness lock retention is correct, but backup needs a coordinated writer drain barrier covering activities, foreground work, broker, receipts and owner stores. Do not infer a safe snapshot from current shutdown.
- Provider-assignment lock moves to platform .agent-sessions. Requires protected maintenance with no mixed old/new workers. Journal currently uses existing owner lease fencing; full platform journal-storage cutover is still pending.
- Complete C6 production owner-box/runtime composition and protected migration/restore acceptance before retiring old cells. Complete C10 invoke/await and migration parity and C11 generic operation authority; no checklist was marked fully accepted.

## Evidence and review

- /tmp/c7-c9-focused-final.log:128 passed at final head. /tmp/c7-definition.log:13 passed.
- /tmp/c7-heavy-final.log: final head, all35 selected heavy files,1351 passed21 failed. /tmp/c7-heavy-baseline.log reproduces the exact21 failures on unchanged anchor922b8719. No quarantine or bypass was added.
- /tmp/c7-c9-structural-final.log:829 passed at final head.
- /tmp/c7-openspec-validation.log: strict control-plane-agent-loop validation passes.
- /tmp/c7-hygiene.log and /tmp/c7-hygiene-stacked.log: own and actual C6-base test-hygiene checks pass with explicit inherited retirement declaration.
- reviews/c7-c9-brief.txt, reviews/c10-activity-brief.txt, reviews/c10-engine-brief.txt, reviews/c11-ui-brief.txt and reviews/c9-stream-brief.md are review inputs. Root CLI attempt exited127: claude executable not installed. Parent's working Claude MCP can review exact published heads. No review verdict exists yet.
- Linux oracle image fetch returned Forbidden for mirror.gcr.io/library/python:3.11-slim. Browser download also returned Forbidden in C11. No manual hosted dispatch or alternate bypass was attempted. Draft CI skips for Linux/browser/Docker proof are not passes. Use supported automatic PR CI after required integration/review. At final read, final-head Tests were pending and Linux/browser/Docker proofs skipped for the draft; no CI success is asserted.
