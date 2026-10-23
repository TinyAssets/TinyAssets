Checking whether the new runtime schema validation matches column order on existing (pre-PR) databases — early history suggests it doesn't.

The cutover is already live, so I'm checking whether this PR's startup validation rejects tables in production's historical column order. Running a quick local simulation.

Simulation confirmed the drift. Running the PR's own tests and the shape guard to finish.

I disagree with this PR. The code really did move out of `daemon_server.py`, but production boxes will fail on it, and S10 is marked converged on a relabel. The PR's own tests pass (37), but none of them covers a store that has already been cut over.

### Findings

**1. CRITICAL: every existing owner box will refuse to start its main database after deploy.** (`tinyassets/storage/schema.py:268-269`, `tinyassets/storage/catalog_schema.py:4-26` and `:27-42`)
- **What changed:** `_initialize_author_server_locked` now ends with `ensure_table(conn, ddl, kind="durable")` on all 27 tables. That check compares column order exactly (`sqlite_schema.py:47-55`). The old code never validated existing tables.
- **Why live stores fail:** the new declarations put columns in the order today's fresh install produces, not the order older databases accumulated through `ALTER TABLE ADD COLUMN`.
  - The `fork_from` ALTER existed before 2026-05-10. `default_llm_policy_json` and `concurrency_budget` arrived on 2026-09-23. So any `branch_definitions` older than September has `…, fork_from, default_llm_policy_json, concurrency_budget`. The declaration puts `fork_from` last.
  - `goals` has the same problem: `branch_protocol_json` (2026-05-27) landed after `auto_canonical_via_leaderboard` (05-20) and `selector_branch_version_id` (05-22). The declaration puts it second.
- **Why the cutover fix doesn't help:** the C6 cutover has already been committed live (#5204, #5227). It copied the DDL as-is into every box. The new `_current_table_ddl` normalization (`box_layout_migration.py:1921`) only affects a cutover that hasn't run yet, so no live box gets it.
- **Reproduced locally:** I built `branch_definitions` in the 2026-09-01 ALTER order and called the PR's `initialize_author_server`. It raised `SchemaDrift: durable table 'branch_definitions' has schema drift`.
- **Impact:** every caller goes through `initialize_author_server`, including `resolve_bearer_token`, the account functions and `write_tombstone`. So every migrated box fails closed.
- **Data:** no rows are lost, because durable tables refuse rather than rebuild. But it's an outage across the fleet, and fixing it means a stopped migration inside each box, which the PR doesn't include.
- **Missing test:** an in-box store that was cut over with the legacy column order.

**2. HIGH: S10 is labelled converged, but nothing actually moved out of the boxes.** (`tinyassets/storage/schema.py:1-5, 24-34, 65-71`; `docs/architecture.md:163`; `scripts/check_shapes.py:151`)
- **21 of the 27 tables are owner state.** `CENSUS_RULES` gives them placement `owner`. They were never platform state; they're in-box tables, and `schema.py`'s own docstring calls them "the in-box application stores".
- **The other 6 are shared, and are still created inside every box.** That's `user_accounts` and `user_sessions` (router), `author_definitions` and `author_forks` (published), and `deleted_principals` (broker). One combined `TABLES` dict creates them all in the owner's `.tinyassets.db`. That breaks D8 ("Shared tables are not split") and leaves each of them with two homes.
- **The guard's meaning changed without the code reaching the target.** The probe ("no CREATE TABLE in `daemon_server.py`") now passes because the code moved files. "Platform state outside boxes" is not achieved.
- **What would honestly earn "converged":** the shared declarations live with their router, broker or commons store and only that component creates them, while the owner declarations stay owner-box schema, outside the S10 count.

**3. MEDIUM: `deleted_principals` has two homes, two schemas and two writers.** (`tinyassets/storage/deletion_schema.py:3-8`, `tinyassets/account_deletion.py:663-686`)
- **Two schemas:** the broker copy is digest-only, `deleted_principals (digest TEXT PRIMARY KEY)`, created by the cutover (`box_layout_migration.py:1999`) in `.broker/credential-custody.db`. The new `deletion_schema` declares a different version, `founder_sub, deleted_at`, and creates it in every box.
- **Two writers:** `write_tombstone` still writes the box copy through `initialize_author_server(base_path)`.
- **Why it matters:** the erasure tombstone has no single writer, which T9 requires ("`erased` is terminal", one mutation path). A tombstone stored inside the box being erased doesn't outlive that box.
- **The PR makes it look settled:** `deletion_schema.py` is labelled "owned by the storage layer", and `_current_table_ddl` special-cases this table instead of resolving it.

**4. LOW: the unfenced-writer debt list grew.** (`tinyassets/owner_stores.py:107`) `storage/schema.py` was added to `FENCE_BEFORE_C2`, which is the debt list that is meant to shrink to empty. The writer is unchanged, so this is acceptable, but the new home is still not fenced.

**5. LOW: the canonical backfills now ignore fewer errors.** (`tinyassets/storage/schema.py:250-265`) They changed from `INSERT OR IGNORE` to `INSERT … ON CONFLICT DO NOTHING`. The new form only skips duplicate keys; a row that breaks another constraint now raises where it used to be skipped silently. That's consistent with "fail loudly" (Fact 8), but it's unannounced and has no test.

**6. LOW: the cutover helper sometimes keeps the old layout.** (`tinyassets/box_layout_migration.py:1942-1945`) When a required column is missing, `_current_table_ddl` returns the legacy DDL. Those stores would then hit finding 1's `SchemaDrift` at runtime anyway.

### Data loss
- **No rows are dropped.** Every one of the 27 tables is durable, so drift refuses instead of rebuilding, and the `existing - expected` check in the cutover refuses unknown columns.
- **The risk is availability, not loss** (finding 1), plus the `deleted_principals` split (finding 3), where a tombstone can end up only in a box that is being erased.

VERDICT: DISAGREE_EVIDENCE
