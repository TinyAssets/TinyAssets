## Context and decisions
The creator-revenue-share design requires stable author-owned bundle IDs. Package versions currently use author/name counters; recipient release chains separately authorize updates from explicit consent. Catalogue grouping must not manufacture that consent.

Use additive tables in the definitions database: public bundle ownership and append-only definition membership; private source keys keyed by owner_id. Mint an opaque bundle ID for an authenticated author's source home/screen and publication kind. Screenless legacy packages use their exact publication name as the source discriminator. New snapshots include the bundle ID in their approved definition. Ownership checking, version allocation and definition insertion share one transaction.

Backfill old definitions only when their platform idempotency request matches an activated publication pin and its published receipt names that definition. A recorded pin owner must match the definition author. Pre-owner-column pins use the authenticated author on that exact definition/receipt pair, never today's home owner. Keep definitions, blobs, fingerprints and installed copies unchanged. Unknown legacy provenance stays ungrouped. Names alone never establish screen lineage.

Catalogue filtering and pagination run after selecting current versions. Exact definition reads expose bundle ID, sequence, current definition and previous definition; walking previous IDs reaches immutable history. Importing a bundle creates a new author-owned bundle. Bundle history does not opt recipients into updates or broaden the existing presentation-only update executor.

Catalogue metadata is added only to public read/list projections (`include_catalogue=True` on exact reads), never to the internal immutable definition used by consent digests. Thus an in-flight install of an old definition remains valid when the catalogue head changes, and old activated install pins still validate after backfill.

All public definitions are globally discoverable unless an explicit author/query/tag filter narrows the read. The engine separates foreign rows into the untrusted envelope's `content` and the current account's rows into `own`. Second-account tests use actual publication and commons handlers for packages, agents and systems; only engine admission is fixture-bound. This proves the application path with isolated real stores, not the contents of the production catalogue. No owner-only discovery bug was found.

Package manifests and listing components carry the bundle ID. New package reservations use that ID as their internal counter namespace so a rename continues numbering and two same-named screens do not collide. Existing name-keyed rows remain untouched. Current catalogue rows report the bundle sequence; legacy blob manifest version fields are never rewritten. Imported package bytes retain their source manifest identity as provenance, while the new definition receives the importing author's own bundle identity.

## Verification
Supersede, author fence, rename, same-name distinct screens, exact history, legacy evidence, concurrent writers, rollback, installed-copy stability and cross-account reads. Windows and Linux 3.11 affected tests, ruff, hygiene, mirror parity and one cross-family floor review. Commit and push only; no PR or deployment in this lane.

## Cross-family review
Claude peer review, one round, returned ADAPT. Both findings are accepted:
- **AGREE**: mutable catalogue head metadata in `_definition_from_row` broke system-copy consent digests. Move it to explicit catalogue projections; preserve the immutable source and all existing digest checks. New in-flight-install regression plus the existing release policy/executor suites verify this.
- **AGREE**: `list_systems` still collapsed distinct screens by author/name. Remove this second deduplication after the storage catalogue has already selected current bundle versions; add distinct-screen coverage for both packages and systems.

No test names or assertions were removed or loosened. Existing recipient-update failures during development identified the first issue; they remain part of the final verification set.

## Final verification (2026-10-05 UTC)
- Windows Python 3.14: **627 passed, 6 skipped**. The existing skips require POSIX anchored-file semantics or symlink privileges unavailable on this host; all six ran on Linux.
- Linux oracle Python 3.11.16, real bubblewrap, uid 1001: **632 passed, 1 skipped**. The skip is the Windows directory-junction test, which ran on Windows. Skips are not counted as passes.
- Same 633-case suite on both: `test_commons_bundle_versions`, `test_command_center_packages`, `test_custom_agents`, `test_agent_interchange`, `test_in_platform_agent_systems`, `test_command_center_release_policy`, `test_command_center_release_surface`, `test_command_center_update_executor`, `test_command_center_release_deletion`, `test_engine_read_views`, `test_engine_mcp_server`, `test_command_center_publish_intent`, `test_discovery_catalogue_shapes`, `test_command_center_system_copy`, plus affected heavy files `test_canonical_dispatch` and `test_universe_server_isolation` (all under `tests/`, with `.py`).
- After the final migration ordering/transaction edits, reran `test_commons_bundle_versions`, `test_custom_agents`, and `test_agent_interchange`: **78 passed on Windows and 78 passed on Linux 3.11**, no skips. The new file contains 10 functions / 15 parametrized regression cases.
- Ruff passes for every changed canonical Python file and the new test. Whole-repo ruff has 55 findings in 23 files; a diff against `origin/main` confirms none of those files changed in this lane.
- Plugin rebuilt; all 597 canonical mirror files match. Commit hooks passed import-graph smoke, path-resolver lint, mojibake, skills and provider-drift checks.
- Hygiene against `origin/main`: **10 added, 0 removed, 0 tampering findings**. Delta and main as-built requirements match exactly and both validate strictly.
- Removed `docs/concerns/2026-10-04-commons-versions-do-not-supersede.md`. No `onboarding/app.html` edit and no new real-browser tests. Scope is branch commit/push, not deployment or a PR.
