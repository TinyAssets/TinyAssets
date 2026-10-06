# Lean suite phase 2 candidates - 2026-10-06

Status: preparation only; no tests, skips, quarantine entries or required jobs removed.

Source snapshot: `838a22e087` (parent main `dd82fd3d4a`). Counts below are Python `test_` function definitions, not parametrized pytest cases. File groups overlap and must not be summed. This is a review census: a file reading app.html can also execute JavaScript, and a workflow file can contain a security guard. Whole-file deletion is not proposed.

**KEEP overrides every candidate label:** authority, authentication, credentials, cross-user isolation, consent, money, data loss, jail containment, release integrity and public-connector availability. Keep reviewed timer/storage classification and background execution boundaries. Keep the static prompt budgets in `tests/test_converse_turn_cost.py`.

## Evidence standard and decisions

No per-test historical JUnit archive was reconstructed, so **N = 0 historical runs examined**; this note makes no "never failed" claim. The supplied research note reports aggregate run outcomes, which cannot establish a particular test never failed. Evidence here is named behavior coverage, source comparisons, and the targeted baseline/mutation records below. A passing shared mutation does not prove that every distinct scenario is redundant; deletion remains conditional on checking the residual assertions.

## Counts

| Group | Files | Test definitions / entries | Disposition |
|---|---:|---:|---|
| app.html text/wording review | 73 | 1225 | Review individual assertions; KEEP executed behavior/security |
| workflow-YAML review | 75 | 1294 | Review formatting pins; KEEP executable gate invariants |
| duplicate inventory guards | 4 | 33 | Generate facts; KEEP classification and invariant tests |
| browser/packaging scheduling review | 44 | 529 | Consider nightly/post-deploy; KEEP affected authority/containment pre-merge |
| retired-design quarantine review | 1 | 2 candidate entries | Conditional; 61 other entries KEEP pending evidence |

## app.html wording/text review

Why: source spelling and exact copy can fail harmless refactors. These files are discovered by literal `app.html` or `render_app_html`; the census intentionally includes executed tests so they are not accidentally swept away. Evidence: `tests/test_onboarding_app.py::test_feedback_rides_along_and_clear_is_relayed_too` executes feedback delivery; `tests/test_app_working_indicator.py::test_a_reload_mid_turn_shows_the_indicator_history_cannot` executes the displayed activity; `tests/test_app_chat_cloud.py::test_keyboard_moves_and_resizes_within_the_stage` executes placement. The 20 probes below audit specific overlapping properties. Risk: losing a visual requirement, edge case, or security check hidden among wording pins. Keep any assertion without equivalent coverage.

| File | Definitions (review scope, not deletion count) |
|---|---:|
| `tests/test_account_deletion.py` | 47 |
| `tests/test_affected_tests.py` | 23 |
| `tests/test_agent_box_dev_workflow.py` | 7 |
| `tests/test_android_app_identity.py` | 15 |
| `tests/test_app_account_transition.py` | 12 |
| `tests/test_app_addressed_agent.py` | 11 |
| `tests/test_app_browser_notifications.py` | 10 |
| `tests/test_app_chat_cloud.py` | 22 |
| `tests/test_app_chat_cloud_browser.py` | 10 |
| `tests/test_app_connect_disclosure.py` | 11 |
| `tests/test_app_connection_controls.py` | 29 |
| `tests/test_app_file_upload_ui.py` | 52 |
| `tests/test_app_full_message_expansion.py` | 12 |
| `tests/test_app_header_name.py` | 5 |
| `tests/test_app_hosted_model_connect.py` | 27 |
| `tests/test_app_live_turn_recovery.py` | 14 |
| `tests/test_app_memory_panel.py` | 4 |
| `tests/test_app_model_choice.py` | 7 |
| `tests/test_app_model_picker.py` | 57 |
| `tests/test_app_modules.py` | 13 |
| `tests/test_app_native_push.py` | 20 |
| `tests/test_app_notify_auto_prompt.py` | 21 |
| `tests/test_app_owner_sign_in.py` | 7 |
| `tests/test_app_owner_unread.py` | 5 |
| `tests/test_app_plan_without_billing.py` | 5 |
| `tests/test_app_profile.py` | 9 |
| `tests/test_app_reads_use_owner_door.py` | 9 |
| `tests/test_app_recovery_browser.py` | 18 |
| `tests/test_app_request_rail_executes.py` | 4 |
| `tests/test_app_rules_panel.py` | 6 |
| `tests/test_app_send_not_delivered.py` | 12 |
| `tests/test_app_serving_heal_executes.py` | 5 |
| `tests/test_app_sign_in_again.py` | 13 |
| `tests/test_app_signout_clears_typed_credentials.py` | 2 |
| `tests/test_app_spoken_turn_account_fence.py` | 12 |
| `tests/test_app_stop_turn.py` | 9 |
| `tests/test_app_stream_liveness.py` | 9 |
| `tests/test_app_two_surfaces.py` | 13 |
| `tests/test_app_two_surfaces_browser.py` | 15 |
| `tests/test_app_ui_by_talking.py` | 12 |
| `tests/test_app_working_indicator.py` | 27 |
| `tests/test_billing_boundary.py` | 69 |
| `tests/test_brand_parity.py` | 4 |
| `tests/test_chat_renderer_browser.py` | 6 |
| `tests/test_command_center_copy.py` | 8 |
| `tests/test_command_center_packages.py` | 76 |
| `tests/test_command_center_system_browser.py` | 11 |
| `tests/test_connect_free_ai_real_browser.py` | 2 |
| `tests/test_connect_free_ai_screen.py` | 10 |
| `tests/test_connect_is_the_whole_gesture.py` | 4 |
| `tests/test_connected_model_row_is_optional.py` | 12 |
| `tests/test_connector_bounded_reads.py` | 16 |
| `tests/test_consumer_reason_actions.py` | 5 |
| `tests/test_custom_ui_isolation.py` | 9 |
| `tests/test_free_source_cards.py` | 8 |
| `tests/test_frontend_proxy.py` | 8 |
| `tests/test_generic_oauth_connections.py` | 28 |
| `tests/test_inline_approvals_real_browser.py` | 8 |
| `tests/test_learning_never_locks_out.py` | 25 |
| `tests/test_mobile_launch_background.py` | 1 |
| `tests/test_notification_is_the_setup.py` | 17 |
| `tests/test_onboarding_app.py` | 105 |
| `tests/test_onboarding_mcp_session_recovery.py` | 31 |
| `tests/test_onboarding_terminal_frames.py` | 1 |
| `tests/test_owner_steering.py` | 42 |
| `tests/test_owner_ui_prefs_browser.py` | 6 |
| `tests/test_request_card_layout_and_links.py` | 20 |
| `tests/test_request_rail_honest_asks.py` | 15 |
| `tests/test_shortlist_background_refresh.py` | 22 |
| `tests/test_soul_memory_real_browser.py` | 3 |
| `tests/test_status_says_what_is_true.py` | 8 |
| `tests/test_tool_activity.py` | 20 |
| `tests/test_universe_path_io_guard.py` | 4 |

## Workflow-YAML review

Why: formatting and obsolete step-name pins duplicate declarations; actionlint checks syntax, but cannot prove triggering, permissions, ordering or rollback. Evidence: `tests/test_deploy_terminal_receipt.py::test_rollback_step_exact_exit_matrix` exercises the exit policy; `tests/test_deployed_sha.py` exercises 0/1/2; `tests/test_real_browser_proof_workflow.py::test_every_marked_file_retriggers_the_proof` checks marker closure. **KEEP** trigger/concurrency, secrets, immutable image identity, rollback, auth and skip-refusal assertions. Risk: a workflow that never runs cannot be validated by its own green execution. Nightly evidence alone does not replace trigger guards.

| File | Definitions |
|---|---:|
| `tests/desktop_install/test_release_workflow.py` | 18 |
| `tests/desktop_install/test_two_windows_installers.py` | 5 |
| `tests/test_affected_tests.py` | 23 |
| `tests/test_android_app_identity.py` | 15 |
| `tests/test_android_push_build.py` | 17 |
| `tests/test_android_release_pipeline.py` | 17 |
| `tests/test_app_chat_cloud_browser.py` | 10 |
| `tests/test_app_two_surfaces_browser.py` | 15 |
| `tests/test_apply_daemon_env_voice_flags.py` | 2 |
| `tests/test_auto_enroll_merge_workflow.py` | 7 |
| `tests/test_backup_restore_drill_invariants.py` | 12 |
| `tests/test_build_image_workflow.py` | 16 |
| `tests/test_canary_scripts_import_smoke.py` | 18 |
| `tests/test_ci_concurrency_cancels.py` | 6 |
| `tests/test_ci_runner_budget.py` | 8 |
| `tests/test_cloud_only_preflight_boundaries.py` | 13 |
| `tests/test_cloud_prepush_oracle.py` | 44 |
| `tests/test_codex_cli_compat.py` | 9 |
| `tests/test_community_loop_typed_observation.py` | 10 |
| `tests/test_community_loop_watch.py` | 7 |
| `tests/test_community_loop_watch_workflow.py` | 5 |
| `tests/test_community_watch_result_boundary.py` | 3 |
| `tests/test_custom_ui_forms_browser.py` | 2 |
| `tests/test_deploy_digest_stream_drain.py` | 8 |
| `tests/test_deploy_drains_in_flight_turns.py` | 14 |
| `tests/test_deploy_prod_hmac_path.py` | 15 |
| `tests/test_deploy_prod_workflow.py` | 87 |
| `tests/test_deploy_worker_workflow.py` | 23 |
| `tests/test_deployed_sha_build_paths.py` | 12 |
| `tests/test_deployed_sha_post_deploy.py` | 2 |
| `tests/test_diagnose_prod_startup_workflow.py` | 2 |
| `tests/test_dns_canary_workflow.py` | 24 |
| `tests/test_docker_admission_fixture.py` | 5 |
| `tests/test_dockerfile_shape.py` | 36 |
| `tests/test_dr_drill_workflow.py` | 67 |
| `tests/test_drain_review_gate.py` | 53 |
| `tests/test_drop_first_exec_gate.py` | 12 |
| `tests/test_emergency_dns_flip.py` | 15 |
| `tests/test_env_unreadable_marker.py` | 13 |
| `tests/test_expected_instance_state_preparation.py` | 14 |
| `tests/test_forbidden_pr_paths.py` | 15 |
| `tests/test_grant_scope_guidance.py` | 7 |
| `tests/test_host_independence_runbook.py` | 6 |
| `tests/test_host_uptime_installers.py` | 53 |
| `tests/test_linux_jail_proof_workflow.py` | 32 |
| `tests/test_live_docs_reference_real_scripts.py` | 3 |
| `tests/test_main_red.py` | 17 |
| `tests/test_merge_queue_triggers.py` | 3 |
| `tests/test_mirror_parity_gate.py` | 16 |
| `tests/test_mobile_ios_release.py` | 18 |
| `tests/test_native_refresh_jail.py` | 2 |
| `tests/test_no_platform_github_push_credential.py` | 8 |
| `tests/test_no_platform_llm_credentials.py` | 10 |
| `tests/test_oauth_deploy_hardening.py` | 11 |
| `tests/test_owner_ui_prefs_browser.py` | 6 |
| `tests/test_p0_triage_workflow.py` | 27 |
| `tests/test_pre_commit_invariant_actionlint.py` | 14 |
| `tests/test_provider_jail_network.py` | 4 |
| `tests/test_provider_jail_root_masks.py` | 3 |
| `tests/test_provider_universe_jail.py` | 11 |
| `tests/test_prune_units.py` | 16 |
| `tests/test_public_model_lists.py` | 19 |
| `tests/test_queue_attempts.py` | 10 |
| `tests/test_queue_freshness.py` | 21 |
| `tests/test_real_browser_proof_workflow.py` | 13 |
| `tests/test_release_reconcile_workflow.py` | 32 |
| `tests/test_rerun_cancelled_required.py` | 5 |
| `tests/test_runtime_paths.py` | 54 |
| `tests/test_test_hygiene_gate.py` | 26 |
| `tests/test_tests_workflow.py` | 27 |
| `tests/test_turns_in_flight.py` | 31 |
| `tests/test_universe_tools.py` | 49 |
| `tests/test_universe_tools_jail.py` | 25 |
| `tests/test_uptime_canary_concurrency.py` | 4 |
| `tests/test_uptime_canary_workflow.py` | 12 |

## Duplicate inventories

Why: names discoverable from code should not require hand copying. Changes in this PR generate the browser proof block via pytest marker collection, derive 25 plugin callsites from 30 canonical authority records, and scaffold missing timer/storage keys with `UNCLASSIFIED`. The generator does not choose policy. Run `python scripts/generate_guard_inventories.py --write`, review every placeholder, then run the guards. The production-root listing is a dated external observation, not a code copy, and is retained.

Evidence: `test_scanner_detects_a_new_sensitive_execution_call`, `test_scanner_counts_duplicate_calls_in_one_function`, `test_activity_registration_refuses_an_extra_launch`, `test_the_scan_sees_loops_without_while_and_self_rescheduling_callbacks`, `test_every_on_disk_name_is_classified`, and `test_every_marked_file_retriggers_the_proof`. Linux oracle: 98 passed, zero skips across the affected slice. **All four guard modules KEEP.** Risk: generating the approved set directly from observed calls would rubber-stamp new authority. This PR generates only mirrors and scaffolds unknown classifications as failing placeholders.

| File | Definitions |
|---|---:|
| `tests/test_storage_registry_complete.py` | 3 |
| `tests/test_control_plane_inventory.py` | 5 |
| `tests/test_background_authority_inventory.py` | 12 |
| `tests/test_real_browser_proof_workflow.py` | 13 |

## Retired-design quarantine entries

Ledger census: 63 entries in two files: 46 in `tests/test_deploy_prod_workflow.py`, 17 in `tests/test_retire_cheat_loop_deploy_fence.py`. Do not equate quarantined with obsolete. All 17 fence entries and 44 remaining deploy entries are KEEP pending a behavior-specific review. The two narrow retirement-review candidates are:

| Candidate | Why / evidence | Risk |
|---|---|---|
| `tests/test_deploy_prod_workflow.py::test_rollback_step_present` | Pins the retired step name "Rollback on failure"; current workflow says "Roll back if the public canary is red". `test_deploy_terminal_receipt.py::test_rollback_step_exact_exit_matrix` covers rollback exit policy. | Policy does not prove workflow wiring: retain an always-run canary/rollback ordering guard before deleting this pin. |
| `tests/test_deploy_prod_workflow.py::test_deploy_failure_issue_has_truthful_bounded_wording` | Pins old issue sentences. `test_deploy_terminal_receipt.py::test_complete_rollback_issue_wording_matrix` and `test_terminal_receipt_issue_sentence_matrix` exercise the classifier wording. | Verify current issue wiring actually consumes the helper; do not discard truthful outcome reporting. |

No quarantine ledger edit is included. Neither proposed retirement is represented as fully proved safe; the residual integration gaps above must be closed in a deletion lane.

## Browser/packaging nightly or post-deploy review

Why: real browsers, platform packaging and process startup cost more than source guards. Consider moving redundant broad matrices to nightly/post-deploy while retaining affected-change execution and genuine platform proof. Evidence: `tests/test_app_chat_cloud_browser.py::test_it_starts_medium_and_can_be_dragged_resized_and_remembered` renders the interaction already exercised in `tests/test_app_chat_cloud.py`; `tests/test_packaging_build.py` imports the actual staged runtime. Existing `real-browser-proof.yml` refuses skipped cases and runs preview containment. No scheduling changes are made here. Risk: nightly detection is later, packaging can break installation, and browser-only bugs escape DOM shims. **KEEP real jail/UI isolation, credential handling and authority checks in affected pre-merge validation.**

| File | Definitions |
|---|---:|
| `tests/desktop_install/test_credentials.py` | 8 |
| `tests/desktop_install/test_onboarding.py` | 14 |
| `tests/desktop_install/test_packaged_runtime.py` | 11 |
| `tests/desktop_install/test_packaged_singleton.py` | 1 |
| `tests/desktop_install/test_packaging_definitions.py` | 10 |
| `tests/desktop_install/test_release_workflow.py` | 18 |
| `tests/desktop_install/test_two_windows_installers.py` | 5 |
| `tests/desktop_install/test_updater.py` | 14 |
| `tests/test_android_release_pipeline.py` | 17 |
| `tests/test_app_chat_cloud_browser.py` | 10 |
| `tests/test_app_chat_latest_browser.py` | 13 |
| `tests/test_app_first_run_connect_browser.py` | 11 |
| `tests/test_app_pending_requests_browser.py` | 2 |
| `tests/test_app_phone_conversation_browser.py` | 3 |
| `tests/test_app_recovery_browser.py` | 18 |
| `tests/test_app_send_resume_browser.py` | 19 |
| `tests/test_app_store_release.py` | 9 |
| `tests/test_app_two_surfaces_browser.py` | 15 |
| `tests/test_approval_sheet_real_browser.py` | 5 |
| `tests/test_chat_renderer_browser.py` | 6 |
| `tests/test_chatgpt_chat.py` | 7 |
| `tests/test_command_center_system_browser.py` | 11 |
| `tests/test_connect_free_ai_real_browser.py` | 2 |
| `tests/test_custom_ui_forms_browser.py` | 2 |
| `tests/test_custom_ui_isolation.py` | 9 |
| `tests/test_custom_ui_real_browser.py` | 2 |
| `tests/test_deploy_during_traffic.py` | 3 |
| `tests/test_dockerfile_shape.py` | 36 |
| `tests/test_inline_approvals_real_browser.py` | 8 |
| `tests/test_linux_jail_proof_workflow.py` | 32 |
| `tests/test_linux_oracle.py` | 21 |
| `tests/test_mobile_ios_release.py` | 18 |
| `tests/test_orphan_ready_browser.py` | 1 |
| `tests/test_owner_ui_prefs_browser.py` | 6 |
| `tests/test_packaging_build.py` | 34 |
| `tests/test_publication_completion.py` | 14 |
| `tests/test_real_browser_import_guard.py` | 4 |
| `tests/test_real_browser_proof_workflow.py` | 13 |
| `tests/test_soul_memory_real_browser.py` | 3 |
| `tests/test_starter_muse_browser.py` | 1 |
| `tests/test_tab_hygiene.py` | 10 |
| `tests/test_tests_workflow.py` | 27 |
| `tests/test_ui_preview.py` | 26 |
| `tests/test_uptime_canary_layer2.py` | 30 |

## Twenty targeted mutation probes

The sample covers 20 tentative consolidation candidates in the app-text review group. It includes executed tests with overlapping text/geometry assertions, rather than pretending every app.html reader is a static pin. Each row must have a green baseline, then a failure in both the candidate and a **different** surviving test under the same targeted product mutation. Authority/security tests are not deletion candidates. Each candidate retains distinct scenarios until a later consolidation proves those as well.

**Result: 20/20 shared-property probes demonstrated.** Baseline: 33 executed cases passed, zero skipped. Every mutant produced pytest exit 1 with assertion failures in both the named candidate and a different survivor; no collection errors or skips were accepted. The oracle research harness passed in 66.21 seconds. Product changes were restored byte-for-byte in a disposable tree; the lane never held mutant product code.

Exact inputs and failing case IDs: [mutation evidence](2026-10-06-lean-suite-phase2-mutations.json). Replay with `python scripts/linux_oracle.py --out <outside-repo-output> -- -q -s docs/design-notes/2026-10-06-lean-suite-phase2-probe.py --basetemp /tmp/b`. The probe refuses to mutate anything outside the oracle `/work` copy. It is a one-off research harness, not a CI suite addition.

**These are one-at-a-time consolidation candidates.** Keep each named survivor when considering its paired candidate. Some rows offer mutually exclusive consolidation directions: do not delete both sides. The mutations establish the stated shared property only; viewport extremes, stale-state cases, model variants and missing fields are distinct residual risks. None of these 20 tests is approved for deletion by this PR.

Initial counter-evidence is retained in the JSON. Row 5: disabling `renderWorking` did not break the local-turn candidate, because its status line uses another painter. The final mutation disables the shared `setStatusLine`. Row 20: blanking feedback left the source pin green while the executed test failed; moving feedback to the wrong field breaks both. This confirms the behavioral test catches a fault the spelling test misses.

| # | Candidate (individual removal only) | Surviving test KEEP | Product mutation | Shared property / residual risk |
|---:|---|---|---|---|
| 1 | `tests/test_app_working_indicator.py::test_a_turn_this_page_never_sent_still_shows_the_indicator` | `tests/test_app_working_indicator.py::test_a_reload_mid_turn_shows_the_indicator_history_cannot` | `renderWorking(): prepend return;` | Live activity paints the working indicator; retain unique scenarios until separately covered. |
| 2 | `tests/test_app_working_indicator.py::test_an_idle_server_and_a_stale_row_are_both_left_unpainted` | `tests/test_app_working_indicator.py::test_a_turn_this_page_never_sent_still_shows_the_indicator` | `renderWorking(): prepend return;` | Live activity paints the working indicator; retain unique scenarios until separately covered. |
| 3 | `tests/test_app_working_indicator.py::test_a_held_or_unreadable_row_is_not_activity` | `tests/test_app_working_indicator.py::test_a_reload_mid_turn_shows_the_indicator_history_cannot` | `renderWorking(): prepend return;` | Live activity paints the working indicator; retain unique scenarios until separately covered. |
| 4 | `tests/test_app_working_indicator.py::test_a_failed_poll_neither_clears_nor_outlives_the_claim` | `tests/test_app_working_indicator.py::test_a_turn_this_page_never_sent_still_shows_the_indicator` | `renderWorking(): prepend return;` | Live activity paints the working indicator; retain unique scenarios until separately covered. |
| 5 | `tests/test_app_working_indicator.py::test_this_pages_own_turn_paints_without_waiting_for_a_poll` | `tests/test_app_working_indicator.py::test_a_local_turn_and_a_server_turn_do_not_both_speak` | `setStatusLine(): prepend return;` | A local turn paints the shared status line; retain unique scenarios until separately covered. |
| 6 | `tests/test_app_working_indicator.py::test_this_pages_own_turn_says_which_step_and_model_it_waits_on` | `tests/test_app_working_indicator.py::test_a_native_agent_step_names_its_model_too` | `shortModelName(): prepend return "";` | The waiting line includes the displayed model name; retain unique scenarios until separately covered. |
| 7 | `tests/test_app_working_indicator.py::test_another_model_is_offered_only_after_a_long_wait` | `tests/test_app_working_indicator.py::test_this_pages_own_turn_says_which_step_and_model_it_waits_on` | `shortModelName(): prepend return "";` | The waiting line includes the displayed model name; retain unique scenarios until separately covered. |
| 8 | `tests/test_app_working_indicator.py::test_a_native_agent_step_names_its_model_too` | `tests/test_app_working_indicator.py::test_this_pages_own_turn_says_which_step_and_model_it_waits_on` | `shortModelName(): prepend return "";` | The waiting line includes the displayed model name; retain unique scenarios until separately covered. |
| 9 | `tests/test_app_working_indicator.py::test_a_model_is_named_as_a_person_would_say_it` | `tests/test_app_working_indicator.py::test_this_pages_own_turn_says_which_step_and_model_it_waits_on` | `shortModelName(): prepend return "";` | The waiting line includes the displayed model name; retain unique scenarios until separately covered. |
| 10 | `tests/test_app_working_indicator.py::test_a_live_turn_shows_one_inline_pending_update` | `tests/test_app_working_indicator.py::test_the_pending_update_line_clears` | `renderWorking(): prepend return;` | Pending deploy text is painted during a live turn; retain unique scenarios until separately covered. |
| 11 | `tests/test_app_chat_cloud.py::test_no_layout_starts_big` | `tests/test_app_chat_cloud.py::test_a_layout_starts_small_in_the_corner` | `cloudDefaultState(): prepend stage={w:1,h:1};` | Default cloud geometry respects the available viewport; retain unique scenarios until separately covered. |
| 12 | `tests/test_app_chat_cloud.py::test_no_layout_on_a_phone_starts_as_the_whole_stage` | `tests/test_app_chat_cloud.py::test_a_stage_smaller_than_the_minimum_gets_the_whole_stage` | `cloudDefaultState(): prepend stage={w:1,h:1};` | Default cloud geometry respects the available viewport; retain unique scenarios until separately covered. |
| 13 | `tests/test_app_chat_cloud.py::test_a_layout_starts_small_in_the_corner` | `tests/test_app_chat_cloud.py::test_no_layout_starts_big` | `cloudDefaultState(): prepend stage={w:1,h:1};` | Default cloud geometry respects the available viewport; retain unique scenarios until separately covered. |
| 14 | `tests/test_app_chat_cloud.py::test_a_stage_smaller_than_the_minimum_gets_the_whole_stage` | `tests/test_app_chat_cloud.py::test_no_layout_on_a_phone_starts_as_the_whole_stage` | `cloudDefaultState(): prepend stage={w:1,h:1};` | Default cloud geometry respects the available viewport; retain unique scenarios until separately covered. |
| 15 | `tests/test_app_chat_cloud.py::test_the_owners_last_state_wins_over_either_default` | `tests/test_app_chat_cloud.py::test_shrinking_is_remembered_and_restored_on_the_next_load` | `cloudResolveState(): prepend saved=null;` | An existing placement overrides the default; retain unique scenarios until separately covered. |
| 16 | `tests/test_app_chat_cloud.py::test_an_unreadable_saved_state_falls_back_to_the_default` | `tests/test_app_chat_cloud.py::test_a_layout_starts_small_in_the_corner` | `cloudDefaultState(): prepend hasLayout=false;` | A layout starts the unsaved cloud as a bubble; retain unique scenarios until separately covered. |
| 17 | `tests/test_app_chat_cloud.py::test_clamping_keeps_it_wholly_on_the_stage` | `tests/test_app_chat_cloud.py::test_keyboard_moves_and_resizes_within_the_stage` | `cloudClamp(): prepend return state;` | Movement remains clamped to the visible stage; retain unique scenarios until separately covered. |
| 18 | `tests/test_onboarding_app.py::test_answer_model_receipt_is_visible_on_typed_and_spoken_reply` | `tests/test_onboarding_app.py::test_history_restores_reply_receipt_and_latest_unknown` | `answerExecutionDetail(): prepend return "";` | A reply displays its provider/model receipt; retain unique scenarios until separately covered. |
| 19 | `tests/test_onboarding_app.py::test_history_restores_reply_receipt_and_latest_unknown` | `tests/test_onboarding_app.py::test_answer_model_receipt_is_visible_on_typed_and_spoken_reply` | `answerExecutionDetail(): prepend return "";` | A restored reply displays its provider/model receipt; retain unique scenarios until separately covered. |
| 20 | `tests/test_onboarding_app.py::test_the_rail_offers_feedback_and_dont_ask_again` | `tests/test_onboarding_app.py::test_feedback_rides_along_and_clear_is_relayed_too` | `if(text) payload.feedback = text; -> if(text) payload.note = text;` | The typed rail feedback reaches the answer; retain unique scenarios until separately covered. |

## Lane verification

- (A) Live anonymous HEAD `/app`: HTTP 200, `X-TinyAssets-Build: dd82fd3d4aca76e280acd4b2899bd3cff671c676`, `Cache-Control: no-store`; deployed_sha default JSON and `--assert-contains HEAD` passed before this lane commit. Receipt proof only; binary freshness is not inferred.
- (B) Final Linux oracle: 99 passed, zero skips (98 affected tests plus the checked-in mutation replay). Follow-up build-image verification: 22 passed, zero skips. Ruff clean. Hygiene: 0 removed / 0 tampering. actionlint unavailable on PATH; CI remains authoritative. No canonical tinyassets changes, so plugin rebuild not applicable.
- Release-critical files: deployed-SHA verifier, build-image receipt read, authority inventory checker, and browser workflow trigger list. No auth/public-route change.
- Draft PR #4530. Claude cross-family review through `.agents/skills/peer-agents/SKILL.md`: **VERDICT: ADAPT**. The reviewer confirmed failure semantics, identical 55-site authority sets and suitably limited mutation claims. AGREE: sync the canonical auth spec to describe the public default and protected CI modes; done. AGREE: include the census, JSON and replay artifacts; all are staged together in this lane. The build-image observation is addressed by explicitly keeping its authenticated `--url https://tinyassets.io/mcp` read and testing that choice. One review round; no second approval verdict is implied.
- Future frontend caveat from review: the dark stateless frontend supplies the app header from `TINYASSETS_FRONTEND_BUILD`, not `_load_release_state`. Revalidate the evidence interpretation when that frontend ships. The current live route projects the receipt as inspected above.
- Canonical spec synced: `openspec/specs/identity-auth-and-access-control/spec.md`. Final fetch/merge of origin/main and final-head hygiene are recorded in the PR body. No branch deployment or product UI change is claimed.

## Batch 1: workflow wording and redundant syntax checks

Founder-directed lean-suite change, based on `40061eaca9` (prep PR #4530).
**17 definitions deleted, 1,277 kept across the original 75 files; no files deleted.**
The census is a candidate search, not a deletion target. Most entries execute behavior or protect triggers, auth, secrets, isolation, data, release identity, rollback, required gates, or concurrency. Those stay, including all of `test_drain_review_gate.py` and its `SENSITIVE_RE` coverage, all signing checks, all prompt budgets, and every unchanged behavioral test. No product or workflow file changes belong to this batch.

Deleted groups: five standalone YAML parse checks (the all-workflow parser remains); five generic DR step-name checks (specific executable-step guards remain); the triage reprobe name check (repair ordering and reprobe identity remain); one duplicate DNS label substring check (the parsed environment assertion remains); three DR runbook wording checks and one log-description wording check (weekly scheduling, state-proof ordering and fail-closed cleanup remain); one hook comment-number check (the hook invocation guard remains). Exact removed nodeids and mutations are in `2026-10-06-lean-suite-batch1-removals.json`.

The DR prose checks are intentionally retired as copy requirements. The mutation probes establish continued enforcement of the associated schedule, probe ordering and cleanup, **not** that prose, headings or comments can never drift. No claim is made that an arbitrary change in human-facing copy fails a surviving test. The hook probe changes the referenced invocation; all four occurrences are changed to avoid a comment accidentally satisfying its source guard.

Evidence reuses the phase-2 subprocess/JUnit harness from `2026-10-06-lean-suite-phase2-probe.py`. The batch replay lives alongside it as `2026-10-06-lean-suite-batch1-probe.py`, and runs only in the disposable Linux oracle. Each probe first has a green surviving-test baseline, then corrupts one YAML file, removes one whole executable step without reformatting the YAML, or changes the exact schedule/label/hook wiring. Each expected failure must be a test failure, with no collection error or skip. Files are restored in `finally`. The original phase-2 JSON is unchanged; its frontend probes do not serve as evidence for this batch.

The deployment module was inspected but left byte-for-byte unchanged: it contains quarantined references to retired rollout steps, and existing Ruff findings. A red test cannot provide a green-to-red mutation proof. Those tests, including the legacy rollback/issue wording checks, remain for a separately evidenced retirement lane. This is why the cut is substantially smaller than 1,294 definitions; no security or behavioral assertion is sacrificed for a count.

### Per-file disposition

Counts are definitions, not parametrized cases. Zero means KEEP the entire file.

| File | Deleted | Kept |
|---|---:|---:|
| `tests/desktop_install/test_release_workflow.py` | 0 | 18 |
| `tests/desktop_install/test_two_windows_installers.py` | 0 | 5 |
| `tests/test_affected_tests.py` | 0 | 23 |
| `tests/test_android_app_identity.py` | 0 | 15 |
| `tests/test_android_push_build.py` | 0 | 17 |
| `tests/test_android_release_pipeline.py` | 0 | 17 |
| `tests/test_app_chat_cloud_browser.py` | 0 | 10 |
| `tests/test_app_two_surfaces_browser.py` | 0 | 15 |
| `tests/test_apply_daemon_env_voice_flags.py` | 0 | 2 |
| `tests/test_auto_enroll_merge_workflow.py` | 1 | 6 |
| `tests/test_backup_restore_drill_invariants.py` | 0 | 12 |
| `tests/test_build_image_workflow.py` | 0 | 16 |
| `tests/test_canary_scripts_import_smoke.py` | 0 | 18 |
| `tests/test_ci_concurrency_cancels.py` | 0 | 6 |
| `tests/test_ci_runner_budget.py` | 0 | 8 |
| `tests/test_cloud_only_preflight_boundaries.py` | 0 | 13 |
| `tests/test_cloud_prepush_oracle.py` | 0 | 44 |
| `tests/test_codex_cli_compat.py` | 0 | 9 |
| `tests/test_community_loop_typed_observation.py` | 0 | 10 |
| `tests/test_community_loop_watch.py` | 0 | 7 |
| `tests/test_community_loop_watch_workflow.py` | 0 | 5 |
| `tests/test_community_watch_result_boundary.py` | 0 | 3 |
| `tests/test_custom_ui_forms_browser.py` | 0 | 2 |
| `tests/test_deploy_digest_stream_drain.py` | 0 | 8 |
| `tests/test_deploy_drains_in_flight_turns.py` | 0 | 14 |
| `tests/test_deploy_prod_hmac_path.py` | 0 | 15 |
| `tests/test_deploy_prod_workflow.py` | 0 | 87 |
| `tests/test_deploy_worker_workflow.py` | 1 | 22 |
| `tests/test_deployed_sha_build_paths.py` | 0 | 12 |
| `tests/test_deployed_sha_post_deploy.py` | 0 | 2 |
| `tests/test_diagnose_prod_startup_workflow.py` | 0 | 2 |
| `tests/test_dns_canary_workflow.py` | 2 | 22 |
| `tests/test_docker_admission_fixture.py` | 0 | 5 |
| `tests/test_dockerfile_shape.py` | 0 | 36 |
| `tests/test_dr_drill_workflow.py` | 10 | 57 |
| `tests/test_drain_review_gate.py` | 0 | 53 |
| `tests/test_drop_first_exec_gate.py` | 0 | 12 |
| `tests/test_emergency_dns_flip.py` | 0 | 15 |
| `tests/test_env_unreadable_marker.py` | 0 | 13 |
| `tests/test_expected_instance_state_preparation.py` | 0 | 14 |
| `tests/test_forbidden_pr_paths.py` | 0 | 15 |
| `tests/test_grant_scope_guidance.py` | 0 | 7 |
| `tests/test_host_independence_runbook.py` | 0 | 6 |
| `tests/test_host_uptime_installers.py` | 0 | 53 |
| `tests/test_linux_jail_proof_workflow.py` | 0 | 32 |
| `tests/test_live_docs_reference_real_scripts.py` | 0 | 3 |
| `tests/test_main_red.py` | 0 | 17 |
| `tests/test_merge_queue_triggers.py` | 0 | 3 |
| `tests/test_mirror_parity_gate.py` | 0 | 16 |
| `tests/test_mobile_ios_release.py` | 0 | 18 |
| `tests/test_native_refresh_jail.py` | 0 | 2 |
| `tests/test_no_platform_github_push_credential.py` | 0 | 8 |
| `tests/test_no_platform_llm_credentials.py` | 0 | 10 |
| `tests/test_oauth_deploy_hardening.py` | 0 | 11 |
| `tests/test_owner_ui_prefs_browser.py` | 0 | 6 |
| `tests/test_p0_triage_workflow.py` | 2 | 25 |
| `tests/test_pre_commit_invariant_actionlint.py` | 1 | 13 |
| `tests/test_provider_jail_network.py` | 0 | 4 |
| `tests/test_provider_jail_root_masks.py` | 0 | 3 |
| `tests/test_provider_universe_jail.py` | 0 | 11 |
| `tests/test_prune_units.py` | 0 | 16 |
| `tests/test_public_model_lists.py` | 0 | 19 |
| `tests/test_queue_attempts.py` | 0 | 10 |
| `tests/test_queue_freshness.py` | 0 | 21 |
| `tests/test_real_browser_proof_workflow.py` | 0 | 13 |
| `tests/test_release_reconcile_workflow.py` | 0 | 32 |
| `tests/test_rerun_cancelled_required.py` | 0 | 5 |
| `tests/test_runtime_paths.py` | 0 | 54 |
| `tests/test_test_hygiene_gate.py` | 0 | 26 |
| `tests/test_tests_workflow.py` | 0 | 27 |
| `tests/test_turns_in_flight.py` | 0 | 31 |
| `tests/test_universe_tools.py` | 0 | 49 |
| `tests/test_universe_tools_jail.py` | 0 | 25 |
| `tests/test_uptime_canary_concurrency.py` | 0 | 4 |
| `tests/test_uptime_canary_workflow.py` | 0 | 12 |

### Batch verification and delivery

- Linux oracle affected files plus replay: **155 passed, zero skips** (Python 3.11.17, bubblewrap 0.12.0, uid 1001). The replay separately ran **10 green baseline cases and 14/14 caught mutation groups**; see `2026-10-06-lean-suite-batch1-mutations.json`. Ruff passes all six changed test files and the replay. The full census and peer result follow.
- #4530 is still open. The draft targets main as requested, but inherited prep changes must disappear through rebase after that dependency merges. This batch does not authorize merging the prep PR.
- The requested generic `hygiene 0 removed / 0 tampering` count conflicts with the explicitly requested removals. Record the actual removal findings and use the existing `Test-Removal: consolidated -- ...` declaration; never change the hygiene gate or pretend the count is zero.
- No production deployment or real-user product pass is claimed for this draft test-only lane. No canonical product behavior or spec changes are introduced by this batch.

- Full 75-file census, snapshot `2d725d2802`: **1,868 passed, 42 failed, 4 skipped**. All 42 failures are in the unchanged `tests/test_deploy_prod_workflow.py`, against the existing retired-step contracts. Two skips are Windows process-tree contracts; two need Git objects absent from the oracle's fresh snapshot (`e4c9218` and the historical #3936 merge). This broader run is **not** a zero-skip pass. No failures or skips were suppressed. The six affected modules remain 154 passed, plus the passing replay for 155 total.
- Full-census JUnit summary, exact failures and skip reasons: `2026-10-06-lean-suite-batch1-verification.json`. The affected run is on the same six test-file and workflow bytes as the reviewed slice. Merging origin/main subsequently changed unrelated request-consent UI files, not these files or the replay inputs.
- Hygiene against prep `40061eaca9`: exit 0 with the stated `Test-Removal: consolidated` reason; **0 added / 17 removed / 17 removal findings / 0 product lines added**. Every surviving module AST equals base minus the declared functions; no surviving assertion was weakened. The removal findings are accepted by the existing gate, not erased.
- Draft PR **#4533**. Claude review via `.agents/skills/peer-agents/SKILL.md`: **VERDICT: APPROVE**, exit 0. No floor/correctness findings. AGREE: preserve the #4530 dependency and serialize landing. The reviewer independently confirmed all 75 counts, unchanged surviving ASTs, retained authority/gate tests and the 14 mutation groups. It read the Linux mutation results and did not independently replay them. Minor unused helper/blank-line cleanup is left out of this deletion-only slice; its note-encoding observation is corrected here.
- `origin/main` at `fb22e770bd` was merged with no conflicts before the final push. Rebase onto the eventual #4530 merge remains conditional on that PR merging; current status and final head are recorded in the PR body.
- Final hygiene against current main: **exit 1**, 6 added / 17 removed / 17 removal findings / 172 product lines added. The 172 product lines and 6 new tests are inherited from unmerged #4530, not batch-owned edits. Its merge-queue Tests run is still in progress at handoff. Rebase after it merges, then rerun main-relative hygiene; do not weaken that gate or relabel this as product retirement to bypass it.

## Batch 2: app.html text assertions

Founder-directed lean-suite change, based on `3fa149e659`. Test code only. No product file changes.

**9 definitions deleted, 1,216 kept across the 73 files. 27 assert statements trimmed from 14 kept tests; 55 assert lines removed in all.** This is dozens, not the hundreds the census suggested, because the census counts every file mentioning app.html. It does not count only text pins. Most of the group executes code:

- **862 of 1,225** definitions run the page's code. They use the node harness (`_run_app`, `run_js`, `_run`, `_run_rail`), a real Chromium (Playwright), or a server handler. These are the survivors, not candidates.
- About **65** of the 363 non-executing definitions read app.html as text. The rest pin workflows, Python modules, packaging or other files, which are out of scope for this batch.
- Most of those 65 are security or compliance pins and stay. That covers CSP and nonce-only script, custom-UI sandbox and bridge revocation, owner-door reads and the post-switch login fence. It also covers the credential no-transmission shape, the secret-clearing DOM, the Play-billing native guards, voice-dark store shells, the in-app privacy link and the agent-link accent rule.

Discovery confirms the keeps. Ten candidate mutations targeted behaviour pinned only by kept text assertions. **Nine broke real behaviour while every executing test stayed green** (`kept_unguarded` in the removals JSON):

- history no longer loads on boot
- `include_conversation` turned off
- restored turns unsorted
- the history-failure notice dropped
- a secret field rendered as `input`
- paste-connect sent an empty secret
- a token refresh nulled the session id instead of bumping its generation
- the deposit endpoint lost its path
- `AppUI.turnSettled()` was never called

Deleting those pins would lose behaviour, so they stay until an executing test replaces them; none is cheap. Three first-round proposals also failed their proof and were kept: the `echoed:true` resend pin and two voice-teardown assertions.

### Evidence

`2026-10-06-lean-suite-batch2-removals.json` lists 31 groups. Each group is one app.html mutation, the removed tests or trimmed line ranges, and a survivor. `2026-10-06-lean-suite-batch2-probe.py` replays them in the Linux oracle and reuses the phase-2 subprocess/JUnit harness. It normalises CRLF, because a Windows checkout copies app.html with CRLF.

- `BATCH2_PHASE=pre` runs on the unedited tree. The mutation must fail every removed test, and for a trim the failure must point inside the trimmed lines. A named survivor must fail too. There must be no error and no skip.
- The default `post` phase runs on the final tree. There, the survivor alone must still fail.

Of the 31 groups, **21 are covered-behaviour groups** (reason a), each with an executing survivor. Survivors include:

- node harness tests: file upload transport, connection controls, stop/queue, live-turn recovery, rail/consent, voice adapter, held-message restore, resend, dont-ask-again
- real-browser tests: `test_orphan_ready_browser`, `test_approval_sheet_real_browser`, `test_app_pending_requests_browser`, `test_app_send_resume_browser`

**10 groups are retired copy or markup pins** (reason b). They have no survivor by design:

- a source comment
- absence pins for a removed banner, a full-page connect screen and a header button
- one literal-count pin
- a heading, help text and two button labels
- one static `open` attribute that the renderer re-derives

These are retired as requirements. The probe shows each pin did fail on its mutation. No claim is made that copy drift fails a surviving test.

### Per-file disposition

Counts are definitions, not parametrized cases. "Executing" is the classifier count of definitions that run code.

| File | Executing | Deleted | Kept | Assert statements trimmed |
|---|---:|---:|---:|---:|
| `tests/test_account_deletion.py` | 39 | 0 | 47 | 0 |
| `tests/test_affected_tests.py` | 0 | 0 | 23 | 0 |
| `tests/test_agent_box_dev_workflow.py` | 2 | 0 | 7 | 0 |
| `tests/test_android_app_identity.py` | 3 | 0 | 15 | 0 |
| `tests/test_app_account_transition.py` | 12 | 0 | 12 | 0 |
| `tests/test_app_addressed_agent.py` | 11 | 0 | 11 | 1 |
| `tests/test_app_browser_notifications.py` | 9 | 0 | 10 | 0 |
| `tests/test_app_chat_cloud.py` | 22 | 0 | 22 | 0 |
| `tests/test_app_chat_cloud_browser.py` | 10 | 0 | 10 | 0 |
| `tests/test_app_connect_disclosure.py` | 10 | 1 | 10 | 0 |
| `tests/test_app_connection_controls.py` | 28 | 1 | 28 | 0 |
| `tests/test_app_file_upload_ui.py` | 50 | 0 | 52 | 4 |
| `tests/test_app_full_message_expansion.py` | 12 | 0 | 12 | 0 |
| `tests/test_app_header_name.py` | 4 | 0 | 5 | 1 |
| `tests/test_app_hosted_model_connect.py` | 27 | 0 | 27 | 0 |
| `tests/test_app_live_turn_recovery.py` | 14 | 0 | 14 | 0 |
| `tests/test_app_memory_panel.py` | 4 | 0 | 4 | 1 |
| `tests/test_app_model_choice.py` | 7 | 0 | 7 | 0 |
| `tests/test_app_model_picker.py` | 57 | 0 | 57 | 0 |
| `tests/test_app_modules.py` | 7 | 0 | 13 | 0 |
| `tests/test_app_native_push.py` | 20 | 0 | 20 | 0 |
| `tests/test_app_notify_auto_prompt.py` | 20 | 0 | 21 | 0 |
| `tests/test_app_owner_sign_in.py` | 7 | 0 | 7 | 0 |
| `tests/test_app_owner_unread.py` | 5 | 0 | 5 | 0 |
| `tests/test_app_plan_without_billing.py` | 5 | 0 | 5 | 0 |
| `tests/test_app_profile.py` | 9 | 0 | 9 | 0 |
| `tests/test_app_reads_use_owner_door.py` | 3 | 0 | 9 | 0 |
| `tests/test_app_recovery_browser.py` | 18 | 0 | 18 | 0 |
| `tests/test_app_request_rail_executes.py` | 3 | 0 | 4 | 5 |
| `tests/test_app_rules_panel.py` | 6 | 0 | 6 | 0 |
| `tests/test_app_send_not_delivered.py` | 12 | 0 | 12 | 0 |
| `tests/test_app_serving_heal_executes.py` | 5 | 0 | 5 | 0 |
| `tests/test_app_sign_in_again.py` | 12 | 0 | 13 | 1 |
| `tests/test_app_signout_clears_typed_credentials.py` | 2 | 0 | 2 | 0 |
| `tests/test_app_spoken_turn_account_fence.py` | 12 | 0 | 12 | 0 |
| `tests/test_app_stop_turn.py` | 7 | 1 | 8 | 2 |
| `tests/test_app_stream_liveness.py` | 9 | 0 | 9 | 0 |
| `tests/test_app_two_surfaces.py` | 12 | 0 | 13 | 0 |
| `tests/test_app_two_surfaces_browser.py` | 15 | 0 | 15 | 0 |
| `tests/test_app_ui_by_talking.py` | 7 | 0 | 12 | 0 |
| `tests/test_app_working_indicator.py` | 27 | 0 | 27 | 2 |
| `tests/test_billing_boundary.py` | 12 | 0 | 69 | 0 |
| `tests/test_brand_parity.py` | 0 | 0 | 4 | 0 |
| `tests/test_chat_renderer_browser.py` | 6 | 0 | 6 | 0 |
| `tests/test_command_center_copy.py` | 3 | 0 | 8 | 2 |
| `tests/test_command_center_packages.py` | 10 | 0 | 76 | 0 |
| `tests/test_command_center_system_browser.py` | 11 | 0 | 11 | 0 |
| `tests/test_connect_free_ai_real_browser.py` | 2 | 0 | 2 | 0 |
| `tests/test_connect_free_ai_screen.py` | 8 | 0 | 10 | 0 |
| `tests/test_connect_is_the_whole_gesture.py` | 1 | 1 | 3 | 0 |
| `tests/test_connected_model_row_is_optional.py` | 12 | 0 | 12 | 0 |
| `tests/test_connector_bounded_reads.py` | 12 | 0 | 16 | 0 |
| `tests/test_consumer_reason_actions.py` | 0 | 0 | 5 | 0 |
| `tests/test_custom_ui_isolation.py` | 2 | 0 | 9 | 0 |
| `tests/test_free_source_cards.py` | 4 | 0 | 8 | 0 |
| `tests/test_frontend_proxy.py` | 6 | 0 | 8 | 0 |
| `tests/test_generic_oauth_connections.py` | 24 | 0 | 28 | 0 |
| `tests/test_inline_approvals_real_browser.py` | 8 | 0 | 8 | 0 |
| `tests/test_learning_never_locks_out.py` | 6 | 0 | 25 | 0 |
| `tests/test_mobile_launch_background.py` | 0 | 0 | 1 | 0 |
| `tests/test_notification_is_the_setup.py` | 14 | 1 | 16 | 0 |
| `tests/test_onboarding_app.py` | 79 | 3 | 102 | 7 |
| `tests/test_onboarding_mcp_session_recovery.py` | 29 | 0 | 31 | 1 |
| `tests/test_onboarding_terminal_frames.py` | 1 | 0 | 1 | 0 |
| `tests/test_owner_steering.py` | 24 | 0 | 42 | 0 |
| `tests/test_owner_ui_prefs_browser.py` | 6 | 0 | 6 | 0 |
| `tests/test_request_card_layout_and_links.py` | 13 | 0 | 20 | 0 |
| `tests/test_request_rail_honest_asks.py` | 0 | 1 | 14 | 0 |
| `tests/test_shortlist_background_refresh.py` | 0 | 0 | 22 | 0 |
| `tests/test_soul_memory_real_browser.py` | 3 | 0 | 3 | 0 |
| `tests/test_status_says_what_is_true.py` | 6 | 0 | 8 | 0 |
| `tests/test_tool_activity.py` | 12 | 0 | 20 | 0 |
| `tests/test_universe_path_io_guard.py` | 4 | 0 | 4 | 0 |
| **Total** | **862** | **9** | **1216** | **27** |

### Batch 2 verification

All runs are in the Linux oracle (Python 3.11.17, bubblewrap 0.12.0, uid 1001). Results are in `2026-10-06-lean-suite-batch2-mutations.json`.

- **Pre-deletion replay:** 31/31 groups proved on the unedited tree. The survivor baseline was 24 cases, all green, with 0 skips.
- **Post-deletion replay:** 21/21 survivor groups still fail on the final tree. The 10 reason-b groups have no survivor.
- **Final tree:** 110 files passed, covering the 73 census files, all real-browser suites (the `real_browser` marker plus `*browser*` files) and the `ci_structural_guards.py` guard files. The result was **2,426 passed, 1 skipped, 0 failed**. The skip is `test_agent_box_dev_workflow.py:206`, a declared manual public-network probe (`runs-in=manual ... TA_DEV_PUBLIC_PROBE=1`). It is not an app or browser test, and it was skipped identically on the pre-edit baseline (1,616 passed, 1 skipped).
- `python scripts/ci_structural_guards.py` passed locally: 574 passed. Ruff is clean on every changed file and on the replay.
