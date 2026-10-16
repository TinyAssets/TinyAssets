# Wave 2 implementation and validation

Branch: `conv/c6-host-box-split`. Wave-one baseline: `37b227fffaad6086baab5dbf3544f9fd9a2d0243`. Continued the existing uncommitted work. No commits, pushes, git configuration changes, peer agents, deployments, or new worktrees were performed.

## Reader map

Old locations refer to HEAD. New calls use the shared adapter in `tinyassets/owner_box_reads.py` and never fall back to the host copy. An admitted session supplies the owner for internal soul/canon/effector operations; browser/package/automation/prompt callers pass their authenticated owner; API output/canon/config checks resolve the recorded center owner. Missing ownership or a missing provider is a refusal, not evidence that content is absent.

| Route | Old reader at HEAD | Replacement |
|---|---|---|
| 1 | `api/universe_file_reads.py:129,165,207` descriptor list/read and overlay stat | `box_list`, bounded `box_read`, `box_stat`; graph reads and app UI retain the same entry points and refusal envelopes; `owner_door/files.py:43` now calls `read_whole_file` |
| 2 | `automation_context.py:31` brain metadata and bytes | `box_stat` and `box_read`, with automation owner, existing size bound and link refusal |
| 3 | `command_center_packages.py:685,697,885,1578,1588,1604` publish walk/read and install conflicts | `box_stat`, `box_list`, `box_read`, `box_exists`; owner threaded from publish/package request actor |
| 4 | `soul_edit.py:91,258,433` realpath, governed-file lstat, snapshot glob | Lexical slot validation; box entry metadata including link counts; paged `box_list` for snapshot numbers |
| 5 | `api/universe.py:4708,4765`; `onboarding/model_setup.py:8` config existence/parse | `box_exists` and `box_read`; config loader accepts explicit owner for these calls |
| 6 | `effectors/wiki_write_back.py:112,255,311`; `api/wiki.py:2595` wiki destination/root checks | `box_stat` and `box_read`; owner wiki scaffold/scope checks use the box, shared non-center wiki retains its own host home |
| 7 | `api/universe.py:3835,3898,3940,3981,4062`; `ingestion/canon_io.py:70,203`; `work_targets.py:999` canon/source metadata/enumeration | Box listings/stats/reads; lexical canon paths, provider containment; manifest synthesis stat uses recorded owner |
| 8 | `api/universe.py:1099,1857,2014`; `api/status.py:1331` output metrics/tree/storage | `box_stat`, `box_list`, bounded `box_read`, `box_tree_size` |
| 9 | `universe_tools.py:275,315,420` skill and prompt inventory, workspace overlay lstat | `box_list`, `box_read`, `box_stat`; admitted founder owner threaded from `converse` |

`universe_soul.soul_present` is a thin `box_stat` call. Layout owns the only host/moved sets and supervisor/SQLite-companion predicates. `center_platform_files` imports its accessor names; `command_center_layout` derives its overlapping inventory classifications instead of copying them. `KNOWN_MIXED` remains the original single `log.md` entry.

## Classification audit

The requested sets are preserved. Three current writer routes conflict with the requested host classification:

- `dispatcher_config.yaml`: `tinyassets/api/universe.py` `_action_set_tier_config` builds the path and calls `write_data_path` (HEAD lines 3201/3220). The center-relative helper routes through the admitted box binding.
- `activity.log`: `domains/fantasy_daemon/phases/_activity.py:52-55` appends using `write_data_path`.
- `status.json`: the same module, lines 69-77, writes using `write_data_path`.

These are writer/home inconsistencies; this reader lane does not guess a reclassification or change those writers. The host set still contains all requested names. `.usage_ledger.db`, `.engine_mcp_config.json`, `.runtime_status.json`, and `rules.db` have only legacy/registry or reader references in the current Python tree; no active owner-side writer was found. `.effector_consents.db.premigration` is produced by platform migration (`storage/platform_state_move.py`, `os.replace`), although `storage_accounting.py:245` notes that the historical in-center backup is jail-writable. Several host entries now live at global or sidecar homes rather than inside every center; the classification does not move data.

Other checked writer locations: host pause/heartbeat/ledger accessors; `authoring/store.py`; `automations.py`; `conversation_store.py`; `conversation_attention.py`; `storage/effector_consents.py`; `storage/external_write_receipts.py`; `idempotency.py`; `credential_refresh.py`; `storage/pending_requests.py`; `workspace_fs.open_assignment_lock`; `provider_authority.py`; `runs.py`; `storage/subscription_state.py`; `storage/__init__.py`; wiki destination markers; `workspace_staging.py`; `providers/definition.py`; `agent_steering.py`; `engine_mcp_http.py`; `checkpointing/sqlite_saver.py`; and the platform runtime consumers.

## Validation

All nine route proofs use a real LocalBoxProvider with separate host and box trees. They are marked `role_split`, so the default test admission double cannot satisfy them. The adapter tests also opt out. The legacy unit-test box double now admits platform reads at the same explicit test seam as `universe_files`; it retains the real box core and validates recorded owners. Fixture helpers record ownership rather than weakening production owner checks. Five tests requiring host `resolve()` calls were deleted: they assert the implementation this change removes; existing real symlink tests and the new box-only tests retain containment coverage.

### Final touched-file results

- **Linux: 1,211 passed across all 33 touched test files**, using the latest per-file results. The combined 31-file run returned 1,104 passed / 2 failed; both failures were the explicit-provider tests still receiving the default test double. After adding their `role_split` marker, all six tests in that file passed. The two subsequently updated import/tier files passed 105 tests. This is a per-file aggregate, not a claim that one final invocation returned 1,211.
- **Windows: 748 passed, 261 failed, 101 errors, 101 skipped** across those same files. The portable adapter/home/guard subset returned 233 passed / 14 skipped. Windows does not supply the POSIX box fixture: failures include unavailable provider/admission, `open_assignment_lock` POSIX semantics, and missing `os.fchown`; downstream assertions also fail. Skips are not passes.
- The 14-file Linux regression run (download, canon, automation, engine, soul, package, visibility, telemetry) returned **642 passed**.
- All changed Python files pass `python -m ruff check`; all changed Python/Markdown files use LF; `git diff --check` passes; `openspec validate center-file-homes --strict` passes.

Representative commands:

```text
python -m pytest -q <touched test files> --tb=short --junitxml=<report>
python scripts/linux_oracle.py --out .pytest_cache/center-file-homes-touched-linux -- -q <31 touched test files> --tb=short --junitxml=/out/results.xml
python scripts/linux_oracle.py --out .pytest_cache/center-file-homes-host-confirm-linux -- -q tests/test_center_file_homes.py --tb=short --junitxml=/out/results.xml
python scripts/linux_oracle.py --out .pytest_cache/center-file-homes-import-tier-linux -- -q tests/test_owner_door_import_boundary.py tests/test_interlocutor_tier.py --tb=short --junitxml=/out/results.xml
```

The 31-file argument list is retained in `.pytest_cache/center-file-homes-touched-files.txt`; the broader 193-file reference selection is in `.pytest_cache/center-file-homes-tests.txt`. Reports and captured logs are under the matching `.pytest_cache/center-file-homes-*` names. No full-suite runner was used. One oracle launch failed during source copying because a concurrent Windows run changed bytecode-cache files; its sequential rerun completed. That failed launch is not a test pass.

### Broader scan and handoff

The initial reference-selected Linux run returned **4,153 passed, 182 failed, 1 skipped**. The initial Windows scan returned **2,628 passed, 818 failed, 737 errors, 150 skipped** (including an `fcntl` collection error). These ran earlier snapshots; the later per-file receipts below supersede the rerun files. The broad scan was not rerun wholesale.

**Overall validation remains non-green:** 23 broader failures in five untouched test files have no later passing receipt. They repeated in the narrowed rerun, so they are handed off under AGENTS loop rule 7, without another patch attempt or a host fallback. The explicit owner requirement is already present in HEAD's `_center_owner` and `read_self_model`; no clean-baseline execution is claimed.

- `test_a_universe_needs_an_owner.py` (15): fixtures/expectations treat ACL or founder-home rows as sufficient ownership but lack the `universe_owner` row required for provider binding. In particular, a write-only collaborator grant does not identify an owner to bind. This needs reconciliation of the ownership contract; no owner was guessed.
- `test_assigned_queue_consumer_live_worker.py` (1): host pause lookup encounters a missing `universe_alice` host directory.
- `test_branch_delete_is_first_class.py` (1): the soul-declared loop dependency is not reported by the deletion guard.
- `test_first_contact.py` (2): the permissive test provider materializes a directory while probing an unproven home; creation/admission expectations need reconciliation now that soul presence does not probe the host root first.
- `test_supervisor_liveness.py` (4): worker fixtures disagree with the configured data-root/heartbeat contract (three explicit outside-data-root failures and one missing live worker).

No requested reader route remains on a host content fallback. Internal readers require an admitted owner; external platform reads require a recorded owner. ACL/home-only legacy centers without a recorded owner therefore refuse rather than read the host. The unconfigured subprocess provider and wrong-founder tier fixture found by the scan were corrected and now pass on Linux.

### Per-file counts

Each cell is **passed / failed / errors / skipped** from that file's latest available receipt. Rows span the reference selection, including untouched files; receipts came from several snapshots and must not be treated as one clean full run. `*` marks a touched test file.

| Test file | Windows P/F/E/S | Linux oracle P/F/E/S |
|---|---:|---:|
| `tests/test_a_universe_needs_an_owner.py` | 33 / 19 / 0 / 0 | 37 / 15 / 0 / 0 |
| `tests/test_activity_fence.py` | 12 / 1 / 0 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_affected_tests.py` | 23 / 0 / 0 / 0 | 23 / 0 / 0 / 0 |
| `tests/test_agent_files.py` | 68 / 0 / 0 / 2 | 70 / 0 / 0 / 0 |
| `tests/test_agent_node.py` | 5 / 0 / 24 / 0 | 29 / 0 / 0 / 0 |
| `tests/test_agent_read_pages.py` | 25 / 0 / 0 / 0 | 25 / 0 / 0 / 0 |
| `tests/test_agent_sessions.py` | 6 / 3 / 0 / 3 | 12 / 0 / 0 / 0 |
| `tests/test_agent_wakes.py` | 18 / 1 / 0 / 0 | 19 / 0 / 0 / 0 |
| `tests/test_api_status.py` | 16 / 0 / 0 / 0 | 16 / 0 / 0 / 0 |
| `tests/test_api_universe.py` | 7 / 2 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_api_wiki.py` | 17 / 0 / 52 / 0 | 69 / 0 / 0 / 0 |
| `tests/test_app_live_pending_browser.py` | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 |
| `tests/test_app_profile.py` | 11 / 2 / 0 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_assigned_queue_consumer_live_worker.py` | 2 / 1 / 0 / 0 | 2 / 1 / 0 / 0 |
| `tests/test_assignment_projection_contract.py` | 28 / 0 / 0 / 0 | 28 / 0 / 0 / 0 |
| `tests/test_auto_ship_health_status.py` | 4 / 0 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_automation_context_scheduler.py` | 0 / 0 / 4 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_automation_live_context.py` * | 13 / 17 / 0 / 0 | 30 / 0 / 0 / 0 |
| `tests/test_background_wake_private_universe.py` | 9 / 4 / 0 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_box_admission.py` | not collected | 4 / 0 / 0 / 0 |
| `tests/test_brain_user_boundary.py` | 0 / 8 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_branch_delete_is_first_class.py` | 24 / 2 / 0 / 0 | 25 / 1 / 0 / 0 |
| `tests/test_branch_runner.py` | 17 / 5 / 23 / 0 | 45 / 0 / 0 / 0 |
| `tests/test_broker_catalog_ipc.py` | 0 / 0 / 0 / 10 | 10 / 0 / 0 / 0 |
| `tests/test_broker_definition_modes.py` | 0 / 0 / 0 / 2 | 2 / 0 / 0 / 0 |
| `tests/test_broker_discovery_http.py` | 0 / 0 / 0 / 8 | 8 / 0 / 0 / 0 |
| `tests/test_broker_liveness_creation.py` | 0 / 0 / 0 / 7 | 7 / 0 / 0 / 0 |
| `tests/test_broker_owner_metadata.py` | 0 / 0 / 0 / 7 | 7 / 0 / 0 / 0 |
| `tests/test_broker_usage_ipc.py` | 0 / 0 / 21 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_broker_vault_modes.py` | 0 / 0 / 0 / 7 | 7 / 0 / 0 / 0 |
| `tests/test_bug_investigation_wiring.py` | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 |
| `tests/test_canon_containment_sites.py` * | 2 / 6 / 0 / 14 | 22 / 0 / 0 / 0 |
| `tests/test_canon_io.py` * | 6 / 12 / 0 / 7 | 25 / 0 / 0 / 0 |
| `tests/test_capability_health.py` | 12 / 0 / 0 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_center_file_home_guard.py` * | 201 / 0 / 0 / 0 | 201 / 0 / 0 / 0 |
| `tests/test_center_file_homes.py` * | 5 / 0 / 0 / 1 | 6 / 0 / 0 / 0 |
| `tests/test_chat_file_download.py` | 8 / 4 / 0 / 1 | 13 / 0 / 0 / 0 |
| `tests/test_chat_turn_seat.py` | 0 / 0 / 4 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_command_center_copy.py` | 8 / 0 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_command_center_inventory.py` | 60 / 0 / 0 / 2 | 62 / 0 / 0 / 0 |
| `tests/test_command_center_packages.py` * | 72 / 1 / 42 / 8 | 123 / 0 / 0 / 0 |
| `tests/test_command_center_picker.py` | 16 / 0 / 13 / 0 | 29 / 0 / 0 / 0 |
| `tests/test_command_center_preview.py` | 1 / 0 / 14 / 0 | 15 / 0 / 0 / 0 |
| `tests/test_command_center_publish_intent.py` | 4 / 0 / 5 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_command_center_release_deletion.py` | 0 / 0 / 12 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_command_center_release_policy.py` | 1 / 0 / 25 / 0 | 26 / 0 / 0 / 0 |
| `tests/test_command_center_release_surface.py` | 0 / 0 / 13 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_command_center_system_browser.py` | 0 / 0 / 18 / 0 | 18 / 0 / 0 / 0 |
| `tests/test_command_center_system_copy.py` | 0 / 0 / 32 / 0 | 32 / 0 / 0 / 0 |
| `tests/test_command_center_update_adapter.py` | 1 / 0 / 33 / 0 | 34 / 0 / 0 / 0 |
| `tests/test_command_center_updates.py` | 44 / 1 / 0 / 0 | 45 / 0 / 0 / 0 |
| `tests/test_commons_bundle_versions.py` | 0 / 0 / 14 / 0 | 14 / 0 / 0 / 0 |
| `tests/test_config.py` | 21 / 0 / 0 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_connect_skill.py` * | 1 / 1 / 1 / 1 | 4 / 0 / 0 / 0 |
| `tests/test_connection_lifecycle.py` | 0 / 12 / 0 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_consumer_public_turn.py` | 9 / 1 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_conversation_failure_history.py` | 27 / 2 / 0 / 0 | 29 / 0 / 0 / 0 |
| `tests/test_conversation_failure_readers.py` | 7 / 3 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_conversation_memory.py` | 16 / 3 / 0 / 0 | 19 / 0 / 0 / 0 |
| `tests/test_conversation_pending.py` | 9 / 0 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_converse_addressed_agent.py` | 2 / 0 / 26 / 0 | 28 / 0 / 0 / 0 |
| `tests/test_converse_handle.py` | 12 / 10 / 0 / 0 | 22 / 0 / 0 / 0 |
| `tests/test_converse_server_owned.py` | 2 / 7 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_converse_turn_cost.py` * | 2 / 3 / 8 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_current_actor_auth_context.py` | 4 / 0 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_current_executor_liveness.py` | 11 / 1 / 0 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_custom_ui_assets.py` | 26 / 2 / 0 / 0 | 28 / 0 / 0 / 0 |
| `tests/test_daemon_brain_api.py` | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 |
| `tests/test_daemon_dashboard.py` | 9 / 8 / 0 / 0 | 17 / 0 / 0 / 0 |
| `tests/test_declare_universe_loop.py` | 1 / 7 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_dispatch_execution.py` | 14 / 0 / 0 / 0 | 14 / 0 / 0 / 0 |
| `tests/test_durable_conversation_ingress.py` | 13 / 6 / 0 / 0 | 19 / 0 / 0 / 0 |
| `tests/test_engine_mcp_server.py` * | 59 / 19 / 0 / 3 | 81 / 0 / 0 / 0 |
| `tests/test_execution_kind_generic.py` | 6 / 0 / 0 / 0 | 6 / 0 / 0 / 0 |
| `tests/test_file_bug_compact_response.py` | 0 / 4 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_first_contact.py` | 14 / 44 / 0 / 0 | 56 / 2 / 0 / 0 |
| `tests/test_founder_clock_in_context.py` | 0 / 3 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_free_model_reply_recovery.py` | 20 / 0 / 26 / 0 | 46 / 0 / 0 / 0 |
| `tests/test_get_recent_events.py` | 7 / 13 / 0 / 0 | 20 / 0 / 0 / 0 |
| `tests/test_get_status_primitive.py` | 8 / 32 / 0 / 0 | 40 / 0 / 0 / 0 |
| `tests/test_get_status_reports_working_turn.py` | 2 / 8 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_github_is_an_ordinary_connection.py` | 6 / 10 / 0 / 0 | 16 / 0 / 0 / 0 |
| `tests/test_idempotency.py` | 37 / 0 / 0 / 0 | 37 / 0 / 0 / 0 |
| `tests/test_identity_observability.py` | 11 / 1 / 0 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_in_platform_agent_systems.py` | 1 / 0 / 45 / 1 | 46 / 0 / 0 / 1 |
| `tests/test_in_turn_learning.py` | 2 / 1 / 25 / 0 | 28 / 0 / 0 / 0 |
| `tests/test_inspect_cross_surface_hint.py` * | 6 / 0 / 0 / 0 | 6 / 0 / 0 / 0 |
| `tests/test_interlocutor_tier.py` * | 20 / 78 / 0 / 0 | 98 / 0 / 0 / 0 |
| `tests/test_learning_never_locks_out.py` | 15 / 0 / 18 / 0 | 33 / 0 / 0 / 0 |
| `tests/test_link_free_leftovers.py` | 1 / 5 / 0 / 9 | 15 / 0 / 0 / 0 |
| `tests/test_loop_telemetry.py` | 9 / 1 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_manual_model_connect.py` | 38 / 6 / 0 / 0 | 44 / 0 / 0 / 0 |
| `tests/test_model_bootstrap.py` | 1 / 9 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_multi_tenant_isolation.py` | 0 / 5 / 0 / 0 | 5 / 0 / 0 / 0 |
| `tests/test_native_room_workflow.py` | 26 / 0 / 0 / 0 | 26 / 0 / 0 / 0 |
| `tests/test_no_anonymous_principal.py` | 27 / 0 / 0 / 0 | 27 / 0 / 0 / 0 |
| `tests/test_onboarding_note.py` * | 6 / 1 / 0 / 7 | 14 / 0 / 0 / 0 |
| `tests/test_onboarding_openai_device.py` | 39 / 1 / 0 / 0 | 40 / 0 / 0 / 0 |
| `tests/test_one_extension_unit.py` | 39 / 0 / 0 / 0 | 39 / 0 / 0 / 0 |
| `tests/test_open_provider_routing.py` | 1 / 3 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_owner_box_read_routes.py` * | 0 / 0 / 0 / 13 | 13 / 0 / 0 / 0 |
| `tests/test_owner_box_reads.py` * | 22 / 0 / 0 / 0 | 22 / 0 / 0 / 0 |
| `tests/test_owner_content_writers.py` * | 20 / 1 / 0 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_owner_door_import_boundary.py` * | 6 / 0 / 0 / 1 | 7 / 0 / 0 / 0 |
| `tests/test_owner_message_wakes.py` | 0 / 1 / 14 / 0 | 15 / 0 / 0 / 0 |
| `tests/test_owner_steering.py` | 47 / 2 / 0 / 0 | 49 / 0 / 0 / 0 |
| `tests/test_parent_turn_request_budget.py` | 26 / 0 / 38 / 0 | 64 / 0 / 0 / 0 |
| `tests/test_patch_request_answer_loop.py` | 6 / 1 / 0 / 0 | 7 / 0 / 0 / 0 |
| `tests/test_patch_request_incentives.py` | 6 / 1 / 0 / 0 | 7 / 0 / 0 / 0 |
| `tests/test_per_universe_engine_resolution.py` | 0 / 0 / 2 / 0 | 2 / 0 / 0 / 0 |
| `tests/test_permission_decisions.py` | 2 / 1 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_persona.py` | 19 / 1 / 0 / 0 | 20 / 0 / 0 / 0 |
| `tests/test_persona_custody.py` | 0 / 0 / 13 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_platform_reads_refuse_links.py` * | 1 / 5 / 0 / 20 | 26 / 0 / 0 / 0 |
| `tests/test_platform_state_outside_command_centers.py` | 16 / 0 / 0 / 1 | 17 / 0 / 0 / 0 |
| `tests/test_predeploy_auth_hardening.py` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_private_by_default.py` | 26 / 34 / 0 / 0 | 60 / 0 / 0 / 0 |
| `tests/test_provider_allowlist.py` | 0 / 3 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_provider_authority_record.py` | 16 / 0 / 0 / 0 | 16 / 0 / 0 / 0 |
| `tests/test_provider_model_refusal.py` | 3 / 0 / 15 / 0 | 18 / 0 / 0 / 0 |
| `tests/test_provider_sandbox.py` | 17 / 0 / 0 / 0 | 17 / 0 / 0 / 0 |
| `tests/test_provider_served_router.py` | 5 / 22 / 0 / 1 | 28 / 0 / 0 / 0 |
| `tests/test_provider_stream_and_classify.py` | 108 / 7 / 0 / 0 | 115 / 0 / 0 / 0 |
| `tests/test_publication_completion.py` | 1 / 0 / 14 / 0 | 15 / 0 / 0 / 0 |
| `tests/test_publish_is_discoverable.py` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_related_wiki_visibility.py` | 4 / 0 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_relay_ux_prompts.py` | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 |
| `tests/test_remote_mcp_connect.py` | 6 / 6 / 0 / 0 | 12 / 0 / 0 / 0 |
| `tests/test_request_allocation_concurrency.py` | 0 / 1 / 0 / 1 | 2 / 0 / 0 / 0 |
| `tests/test_request_answer_routing.py` | 37 / 24 / 0 / 0 | 61 / 0 / 0 / 0 |
| `tests/test_request_refusal.py` | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 |
| `tests/test_requester_owned_provider_execution.py` | 8 / 3 / 0 / 0 | 11 / 0 / 0 / 0 |
| `tests/test_resource_usage_status.py` | 23 / 0 / 0 / 2 | 25 / 0 / 0 / 0 |
| `tests/test_retrieval.py` | 35 / 0 / 0 / 0 | 35 / 0 / 0 / 0 |
| `tests/test_run_failure_model_advice.py` | 14 / 0 / 0 / 0 | 14 / 0 / 0 / 0 |
| `tests/test_s2_engine_assignment.py` | 5 / 3 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_s6_s8_engine_sources.py` | 4 / 2 / 0 / 0 | 6 / 0 / 0 / 0 |
| `tests/test_scene_attempt_counter.py` | 15 / 0 / 0 / 0 | 15 / 0 / 0 / 0 |
| `tests/test_scene_dispatch_advance.py` | 4 / 0 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_scheduler_owner.py` | 6 / 2 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_selected_model_authority.py` | 11 / 1 / 47 / 0 | 59 / 0 / 0 / 0 |
| `tests/test_served_cap_values.py` | 17 / 0 / 0 / 0 | 17 / 0 / 0 / 0 |
| `tests/test_served_model_preferences.py` | 0 / 0 / 50 / 0 | 50 / 0 / 0 / 0 |
| `tests/test_served_systems_guidance.py` * | 5 / 0 / 0 / 1 | 6 / 0 / 0 / 0 |
| `tests/test_served_tool_contract_small_models.py` | 34 / 1 / 0 / 0 | 35 / 0 / 0 / 0 |
| `tests/test_share_after_publish_skill.py` * | 0 / 1 / 1 / 1 | 3 / 0 / 0 / 0 |
| `tests/test_shared_background_self.py` | 11 / 3 / 0 / 0 | 14 / 0 / 0 / 0 |
| `tests/test_soul_edit.py` * | 0 / 2 / 22 / 0 | 24 / 0 / 0 / 0 |
| `tests/test_soul_effect_authority_failclosed.py` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `tests/test_starter_muse_package.py` * | 19 / 0 / 0 / 1 | 20 / 0 / 0 / 0 |
| `tests/test_starter_ta_tasks.py` | 0 / 0 / 4 / 2 | 6 / 0 / 0 / 0 |
| `tests/test_storage_gates.py` | 12 / 9 / 0 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_storage_registry.py` | 12 / 1 / 0 / 0 | 13 / 0 / 0 / 0 |
| `tests/test_storage_slice_gates.py` | 5 / 11 / 0 / 0 | 16 / 0 / 0 / 0 |
| `tests/test_storage_utilization_universe.py` | 3 / 5 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_submit_request_wiring.py` | 10 / 5 / 0 / 0 | 15 / 0 / 0 / 0 |
| `tests/test_supervisor_liveness.py` | 9 / 4 / 0 / 0 | 9 / 4 / 0 / 0 |
| `tests/test_supervisor_liveness_cache.py` | 9 / 0 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_tool_activity.py` | 30 / 0 / 0 / 0 | 30 / 0 / 0 / 0 |
| `tests/test_turn_continuity.py` * | 5 / 0 / 0 / 4 | 9 / 0 / 0 / 0 |
| `tests/test_turn_interrupt.py` | 10 / 6 / 5 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_turn_request_economy.py` * | 2 / 1 / 27 / 5 | 35 / 0 / 0 / 0 |
| `tests/test_turns_in_flight.py` | 33 / 1 / 0 / 0 | 34 / 0 / 0 / 0 |
| `tests/test_universe_asks_for_access.py` | 4 / 6 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_universe_bundle.py` | 20 / 0 / 0 / 0 | 20 / 0 / 0 / 0 |
| `tests/test_universe_file_reads_are_bounded.py` | 16 / 0 / 0 / 0 | 16 / 0 / 0 / 0 |
| `tests/test_universe_intelligence.py` | 7 / 26 / 0 / 0 | 33 / 0 / 0 / 0 |
| `tests/test_universe_list_observability.py` * | 5 / 3 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_universe_nodes.py` * | 132 / 16 / 0 / 9 | 157 / 0 / 0 / 0 |
| `tests/test_universe_owner.py` | 21 / 3 / 0 / 0 | 24 / 0 / 0 / 0 |
| `tests/test_universe_path_io_guard.py` * | 5 / 0 / 0 / 0 | 5 / 0 / 0 / 0 |
| `tests/test_universe_server_add_canon.py` | 1 / 9 / 0 / 0 | 10 / 0 / 0 / 0 |
| `tests/test_universe_server_isolation.py` * | 29 / 27 / 0 / 0 | 56 / 0 / 0 / 0 |
| `tests/test_universe_server_ledger.py` * | 6 / 14 / 0 / 0 | 20 / 0 / 0 / 0 |
| `tests/test_universe_server_premise_escaping.py` | 6 / 5 / 0 / 0 | 11 / 0 / 0 / 0 |
| `tests/test_universe_server_telemetry.py` * | 25 / 12 / 0 / 0 | 37 / 0 / 0 / 0 |
| `tests/test_universe_server_word_count.py` * | 0 / 8 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_universe_server_world_query.py` | 0 / 9 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_universe_soul.py` | 1 / 8 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_universe_tools.py` * | 17 / 6 / 0 / 5 | 28 / 0 / 0 / 0 |
| `tests/test_universe_visibility.py` * | 55 / 17 / 0 / 0 | 72 / 0 / 0 / 0 |
| `tests/test_universe_working_turn.py` | 9 / 10 / 0 / 0 | 19 / 0 / 0 / 0 |
| `tests/test_universe_write_boundary.py` | 9 / 12 / 0 / 0 | 21 / 0 / 0 / 0 |
| `tests/test_webhook_inbound_hardened.py` | 2 / 16 / 0 / 0 | 18 / 0 / 0 / 0 |
| `tests/test_wiki_alias_corner_cases.py` | 10 / 0 / 0 / 3 | 13 / 0 / 0 / 0 |
| `tests/test_wiki_file_bug.py` | 43 / 0 / 0 / 0 | 43 / 0 / 0 / 0 |
| `tests/test_wiki_file_bug_dedup.py` | 7 / 0 / 15 / 0 | 22 / 0 / 0 / 0 |
| `tests/test_wiki_scaffold.py` | 6 / 0 / 0 / 0 | 6 / 0 / 0 / 0 |
| `tests/test_wiki_tools.py` | 55 / 0 / 0 / 0 | 55 / 0 / 0 / 0 |
| `tests/test_wiki_write_back_effector.py` * | 1 / 10 / 0 / 0 | 11 / 0 / 0 / 0 |
| `tests/test_work_consumer_model_bridge.py` | 0 / 8 / 0 / 0 | 8 / 0 / 0 / 0 |
| `tests/test_work_target_advance_on_accept.py` | 9 / 0 / 0 / 0 | 9 / 0 / 0 / 0 |
| `tests/test_work_target_origin.py` | 5 / 0 / 0 / 0 | 5 / 0 / 0 / 0 |
| `tests/test_work_targets.py` | 11 / 0 / 0 / 0 | 11 / 0 / 0 / 0 |
| `tests/test_workflow_context_fit.py` | 0 / 4 / 0 / 0 | 4 / 0 / 0 / 0 |
| `tests/test_writer_tools.py` | 1 / 2 / 0 / 0 | 3 / 0 / 0 / 0 |

## Changed files

- `openspec/changes/center-file-homes/proposal.md`
- `openspec/changes/center-file-homes/specs/center-file-homes/spec.md`
- `openspec/changes/center-file-homes/tasks.md`
- `openspec/changes/center-file-homes/validation.md`
- `tests/conftest.py`
- `tests/support/box_double.py`
- `tests/support/owner_reads.py`
- `tests/test_automation_live_context.py`
- `tests/test_canon_containment_sites.py`
- `tests/test_canon_io.py`
- `tests/test_center_file_home_guard.py`
- `tests/test_center_file_homes.py`
- `tests/test_command_center_packages.py`
- `tests/test_connect_skill.py`
- `tests/test_converse_turn_cost.py`
- `tests/test_engine_mcp_server.py`
- `tests/test_inspect_cross_surface_hint.py`
- `tests/test_interlocutor_tier.py`
- `tests/test_onboarding_note.py`
- `tests/test_owner_box_read_routes.py`
- `tests/test_owner_box_reads.py`
- `tests/test_owner_content_writers.py`
- `tests/test_owner_door_import_boundary.py`
- `tests/test_platform_reads_refuse_links.py`
- `tests/test_served_systems_guidance.py`
- `tests/test_share_after_publish_skill.py`
- `tests/test_soul_edit.py`
- `tests/test_starter_muse_package.py`
- `tests/test_turn_continuity.py`
- `tests/test_turn_request_economy.py`
- `tests/test_universe_list_observability.py`
- `tests/test_universe_nodes.py`
- `tests/test_universe_path_io_guard.py`
- `tests/test_universe_server_isolation.py`
- `tests/test_universe_server_ledger.py`
- `tests/test_universe_server_telemetry.py`
- `tests/test_universe_server_word_count.py`
- `tests/test_universe_tools.py`
- `tests/test_universe_visibility.py`
- `tests/test_wiki_write_back_effector.py`
- `tinyassets/api/helpers.py`
- `tinyassets/api/package_requests.py`
- `tinyassets/api/publish_requests.py`
- `tinyassets/api/status.py`
- `tinyassets/api/universe.py`
- `tinyassets/api/universe_file_reads.py`
- `tinyassets/api/wiki.py`
- `tinyassets/automation_context.py`
- `tinyassets/boxes/layout.py`
- `tinyassets/center_platform_files.py`
- `tinyassets/command_center_layout.py`
- `tinyassets/command_center_packages.py`
- `tinyassets/config.py`
- `tinyassets/effectors/wiki_write_back.py`
- `tinyassets/ingestion/canon_io.py`
- `tinyassets/onboarding/model_setup.py`
- `tinyassets/owner_box_reads.py`
- `tinyassets/owner_door/files.py`
- `tinyassets/soul_edit.py`
- `tinyassets/universe_intelligence.py`
- `tinyassets/universe_soul.py`
- `tinyassets/universe_tools.py`
- `tinyassets/work_targets.py`
