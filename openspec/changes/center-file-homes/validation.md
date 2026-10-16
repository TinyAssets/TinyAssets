# Center file homes: round 3

## Reader map

| Readers | Authoritative home and identity |
|---|---|
| Pause, supervisor heartbeats, auto-ship ledger/lock | HOST via `center_platform_files`, anchored to the caller's path; missing center reads empty. |
| Soul/presence, branch loop dependencies, file browser, brain/package/prompt/canon/output/config/wiki readers | BOX via `owner_box_reads`: matching admitted session, otherwise broker `admitted_owner`; no actor or `universe_owner` threading. Missing admission/stub never binds or materializes a box. |
| Dispatcher configuration, activity log, status snapshot and their metadata | BOX; `.runtime_status.json` remains HOST. Single-center refusals propagate or return explicit error envelopes. |
| Universe listing | No box operations; box-derived fields are null with `unavailable_reason` directing the caller to inspect/status. |
| Branch dependency scan | Per-center refusal is explicit (`loop_branch_def_id: null`, `unavailable_reason`); scanning continues and deletion remains blocked. |

`boxes/layout.py` owns the disjoint file-home sets; the guard includes the shared box helpers. Existing authorization/visibility gates remain separate from admission. Story/knowledge database readers, migration, account deletion, OKF and deployment are outside this lane.

## Final validation

One final Linux invocation: **1,578 passed, 0 failed, 0 errors, 0 skipped** across 49 files (328.24 seconds). Three existing `record_property`/JUnit-format warnings in `test_converse_turn_cost.py`.

```text
python scripts/linux_oracle.py --out .pytest_cache/r3-final -- -q <files.txt entries> --tb=short --junitxml=/out/results.xml
```

The exact selection is `.pytest_cache/r3-final/files.txt`; JUnit is `.pytest_cache/r3-final/results.xml`. It includes all 40 test files touched relative to origin/main, the five reported failures, all center-file tests, owner-box routes, and tests referencing listing/liveness or queue policy. No full suite was run.

All changed Python files (relative to origin/main) pass `python -m ruff check`. `git diff --check` and `openspec validate center-file-homes --strict` pass. Changes remain uncommitted on `conv/c6-host-box-split`.

| Test file | Passed / failed |
|---|---:|
| `test_a_universe_needs_an_owner.py` | 52 / 0 |
| `test_api_status.py` | 16 / 0 |
| `test_api_universe.py` | 9 / 0 |
| `test_assigned_queue_consumer_live_worker.py` | 3 / 0 |
| `test_auto_ship_health_status.py` | 4 / 0 |
| `test_auto_ship_ledger.py` | 47 / 0 |
| `test_automation_live_context.py` | 30 / 0 |
| `test_branch_delete_is_first_class.py` | 27 / 0 |
| `test_canon_containment_sites.py` | 22 / 0 |
| `test_canon_io.py` | 25 / 0 |
| `test_center_file_home_guard.py` | 224 / 0 |
| `test_center_file_homes.py` | 7 / 0 |
| `test_command_center_packages.py` | 123 / 0 |
| `test_connect_skill.py` | 4 / 0 |
| `test_converse_turn_cost.py` | 13 / 0 |
| `test_daemon_dashboard.py` | 17 / 0 |
| `test_engine_mcp_server.py` | 81 / 0 |
| `test_first_contact.py` | 58 / 0 |
| `test_inspect_cross_surface_hint.py` | 6 / 0 |
| `test_interlocutor_tier.py` | 98 / 0 |
| `test_loop_telemetry.py` | 10 / 0 |
| `test_onboarding_note.py` | 14 / 0 |
| `test_owner_box_read_routes.py` | 17 / 0 |
| `test_owner_box_reads.py` | 25 / 0 |
| `test_owner_content_writers.py` | 21 / 0 |
| `test_owner_door_import_boundary.py` | 7 / 0 |
| `test_permission_decisions.py` | 3 / 0 |
| `test_platform_reads_refuse_links.py` | 26 / 0 |
| `test_private_by_default.py` | 60 / 0 |
| `test_queue_policy_retired_market.py` | 2 / 0 |
| `test_served_systems_guidance.py` | 6 / 0 |
| `test_share_after_publish_skill.py` | 3 / 0 |
| `test_soul_edit.py` | 24 / 0 |
| `test_starter_muse_package.py` | 20 / 0 |
| `test_storage_utilization_universe.py` | 8 / 0 |
| `test_supervisor_liveness.py` | 13 / 0 |
| `test_turn_continuity.py` | 9 / 0 |
| `test_turn_request_economy.py` | 35 / 0 |
| `test_universe_list_observability.py` | 8 / 0 |
| `test_universe_nodes.py` | 157 / 0 |
| `test_universe_path_io_guard.py` | 5 / 0 |
| `test_universe_self_model.py` | 7 / 0 |
| `test_universe_server_isolation.py` | 56 / 0 |
| `test_universe_server_ledger.py` | 20 / 0 |
| `test_universe_server_telemetry.py` | 37 / 0 |
| `test_universe_server_word_count.py` | 8 / 0 |
| `test_universe_tools.py` | 28 / 0 |
| `test_universe_visibility.py` | 72 / 0 |
| `test_wiki_write_back_effector.py` | 11 / 0 |
