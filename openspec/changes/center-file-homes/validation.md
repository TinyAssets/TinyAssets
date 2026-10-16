# Center file homes: round 4

All changes remain uncommitted on `conv/c6-host-box-split`. The incoming
`box_read_yaml` / `BoxConfigInvalid` queue-policy fix and its regression remain intact.

## Findings and proof

| Item | Change | Regression |
|---|---|---|
| 0 | Inventory classifies dispatcher configuration and status as user content; both original cases and all other assertions remain. | `test_command_center_inventory.py` classification and read-only inventory tests |
| 1 | `_universe_dir` validates the requested name lexically and refuses a linked final component. `_access` opens the stub without following links before either binding path; host writes retain the descriptor-based guard. | `test_owner_box_read_routes.py::test_linked_center_refused_before_any_box_bind_or_read` and `test_linked_center_control_daemon_never_writes_target` (ACL dispatch for pause/status/premise/inspect); helper traversal tests |
| 2 | Reads never scaffold. Scoped page lookup, contents, enumeration and metadata use the owner box without a session binding. Missing pages retain the page-not-found response. | `test_read_page_without_binding_reads_box_and_never_scaffolds`; original `test_wiki_missing_root_auto_scaffolds` now exercises write-triggered scaffolding and asserts reads write nothing |
| 3 | Notes use the box; a refused notes field becomes null with a field-specific unavailable reason. The AST heuristic catches literal MOVED names in host-wrapper arguments, including joined paths. The remaining activity-log wrapper in `api/runs.py` is box-routed. | `test_inspect_notes_use_box_and_isolate_unavailable_field`; `test_indirect_host_reads_are_rejected` |
| 4 | Recording streams bounded rows through a pinned, link-refusing descriptor instead of reading the whole ledger. Duplicate detection covers all historical IDs, not just the tail. | `test_record_streams_past_old_size_cap_and_rejects_old_and_new_duplicates` (18+ MiB ledger, old and newly appended IDs) |

The notes test separately proves an unbound notes read and inspect's field-level
refusal handling. Other legacy inspect reads still require a session binding
(`api/universe.py:1173`, `work_targets.json`); those names are outside the MOVED set.
The ledger scan bounds each row to 16 MiB; existing whole-ledger read/update bounds
are unchanged. Unbound engine hook middleware now skips hooks when there is no
center/actor destination, preserving the real server's retired-name refusal.

## Final validation

One final Linux invocation across **80 files: 2,662 passed, 0 failed, 0 errors,
0 skipped**, in 408.55 seconds. Seven warnings: six LanceDB deprecations and one
request-budget finalizer warning about an unavailable accounting broker.

The selection includes all 43 test files changed against `origin/main`, every
`read_page` match, and the affected helper, scheduler, hook and bounded-read tests:

```powershell
$tests = @(git diff origin/main --name-only -- 'tests/test_*.py')
$tests += @(rg -l 'read_page' tests --glob 'test_*.py' | ForEach-Object { $_.Replace('\', '/') })
$tests += @(
    'tests/test_control_plane_inventory.py', 'tests/test_command_center_inventory.py',
    'tests/test_api_helpers.py', 'tests/test_api_runs.py',
    'tests/test_private_by_default.py', 'tests/test_auto_ship_ledger.py',
    'tests/test_a_universe_needs_an_owner.py', 'tests/test_connector_bounded_reads.py',
    'tests/test_first_contact.py', 'tests/test_storage_utilization_universe.py',
    'tests/test_supervisor_liveness.py', 'tests/test_queue_policy_retired_market.py',
    'tests/test_remote_box_extensions.py', 'tests/test_universe_file_reads_are_bounded.py'
)
$tests = @($tests | Sort-Object -Unique)
python scripts/linux_oracle.py -- -q --no-header -p no:cacheprovider @tests
```

`python -m ruff check` on every changed Python file against `origin/main` passes.
`git diff --check` passes.

`test_hygiene_gate.py --base origin/main --head HEAD --body <saved PR body>` exits 0:
`tests added 42, removed 7, tampering findings 7, product lines added 1026`.
This checks committed history, not these working-tree edits. Round 4 retains every
existing test name and assertion and adds no skip/skipif; the two classification
expectations change as requested. Inventory assertion count stays 136; the wiki
test file increases from 114 to 117. No stash was used.
