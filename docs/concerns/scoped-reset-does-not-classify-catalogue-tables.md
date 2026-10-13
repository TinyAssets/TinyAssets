---
severity: P2
title: Scoped reset is blocked by the unclassified commons-catalogue tables in the main database
filed: '2026-10-13'
summary: MAIN_DB_TABLE_CLASSIFICATIONS lists no catalogue table, and the catalogue lives in .tinyassets.db, so inspect_reset_scope raises "unclassified tables block scoped reset" on any installation that has ever created a binding
---

# Scoped reset does not classify the commons catalogue

Found on C8 (#4744, floor round 1) while wiring the agent-file migration into
daemon startup. `tinyassets/commons_catalog.py` creates `agent_definitions`,
`agent_component_lineage`, `agent_bindings`, `commons_bundles`,
`commons_bundle_versions`, `commons_bundle_sources`, `universe_app_ui`,
`universe_app_ui_asset` and (C8) `agent_file_migration` in the root
`.tinyassets.db` (`storage.db_path`). `tinyassets/scoped_reset.py`
`MAIN_DB_TABLE_CLASSIFICATIONS` names none of them, and
`inspect_reset_scope` raises `ScopedResetSchemaError` for any table it does
not know. Every production installation has a serving binding, so the
catalogue exists there and a scoped test-identity reset is already blocked.

`tests/test_consumer_run_envelope.py::test_empty_common_envelope_schema_does_not_block_previously_supported_reset`
passes only because `consumer_runtime.initialize` does not create the
catalogue on a fresh store; the C8 startup migration therefore opens the
catalogue only when it already exists, which keeps that test honest but does
not close the gap.

Close by classifying each catalogue table (bindings and app UIs carry
`universe_id`, so `reset_home`; definitions, lineage, bundles, assets and the
migration ledger are shared or content-free, so `preserve`) and asserting the
inventory against a store that has created a binding.
