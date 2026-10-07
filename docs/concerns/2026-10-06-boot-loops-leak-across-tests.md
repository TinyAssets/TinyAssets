---
severity: P2
title: Boot loops leak across tests and re-resolve the data dir each tick
filed: '2026-10-06'
summary: universe_server.main() daemon loops outlive their test; the served-budget loop still sweeps whatever TINYASSETS_DATA_DIR a later test sets
---

# Boot loops leak across tests

Tests that call `universe_server.main()` without stubbing `threading.Thread`
(`test_storage_layout`, `test_startup_db_order`,
`test_cloud_admission_serving_startup`, `test_platform_oauth_clients`) start
daemon loops that live for the rest of the pytest process.

`_resume_request_loop` re-resolved `data_dir()` every tick, so a leaked loop
swept later tests' homes under `owner_control.control()` and even consumed their
wakes. That was the intermittent `owner_control_unavailable` in PR CI
(#4519, #4531, #4537, #4544); it now binds its root at boot.

Still open: `_served_budget_lease_loop` calls `_sb_data_dir()` on every tick
(`tinyassets/universe_server.py`, the reconcile calls in that loop), so a leaked
copy still reconciles run files, deliveries and budget leases in whichever
test's data dir is current. Fix the same way (bind the root once at boot), or
give `main()` a stop handle the tests join.
