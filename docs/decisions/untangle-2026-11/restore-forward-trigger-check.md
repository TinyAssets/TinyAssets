# Detecting dropped triggers in restore compatibility checks

The Owner migration guard checks that an unchanged export format can pass
through each migration marked for identity forwarding. On main
`d662e63e`, dropping `object_owner_logged_insert` escaped the table-name
check: the statement names the trigger alone. In an independent SQLite
replay, inserting the owner still succeeded, but the export change log lost
the insertion. A drop can also affect another exported table through a
trigger that updates it.

The guard now applies the migrations in memory and uses SQLite's own trigger
catalog to identify which table a removed or replaced trigger observed. It
checks that table with the existing test for writes that reach exported
tables. It inspects individual statements because removing a trigger and
recreating it can leave an identical final catalog. Workerd accepts multiple
statements in one SQL string, including trigger bodies with semicolons.

Across all 35 migrations on main, Node 22 and 24 replayed 662 SQL entries.
The sole removal in the 20 migrations marked for identity forwarding was on
the unexported action_requests table in migration 18. The two dropped-trigger
regressions exercise a direct export and a trigger maintaining another
exported table. A separate case drops and restores the same capture trigger
in one SQL string. The previous lookup missed the direct change. The new
migration and frozen-bound guard files passed all 14 cases on Node 22.

This check strengthens the prerequisite for the live-run ceiling review on
`codex/sandbox-wait-answer`. Demonstrating that a proposed predicate change
admits existing generations still requires replaying their rows in the
actual restore order.

All 448 ordinary Owner guards and full platform types passed standalone on
Node 24 and with the staged sandbox changes on Node 22. The joined guard
entry initially reported 445 passes and three failures. The sparse rehearsal
lacked the tracked limits-audit file, and the starter's successful child
process wrote the host's experimental proxy warning to stderr. Using the
file from the joined base and suppressing that warning alone cleared all
three. The earlier joined typecheck exited 137 and passed on a bounded-heap
retry. The checked-in guard leaves the migration definitions unchanged.

The companion [restore comparison](https://github.com/TinyAssets/TinyAssets/commit/4e479fb513bb103d4730de53f9ba04acc0ab2c90) exercised the two predicates on writer schemas 31–35. The existing predicate distinguished two layouts with the same final run rows on every writer version. With the experimental predicates both layouts restored and reached the expected settled state on all five versions. This is a behavioral compatibility result for those fixtures. Deciding how the next migration brings an older generation forward remains open, and the guard still checks every changed trigger.

## Indexes on exported tables

Dropping `runs_by_request` removes the database constraint on repeated, non-null run requests. Dropping `collection_changes` prevents the collection feed from reading a page because its SQL names that index. SQLite accepted both drops on the five writer schemas from 31 through 35. On each schema, Node 22 and 24 verified that the second request row was refused before the drop and accepted afterward, and that the ordinary collection page query changed from an empty result to a missing-index error. Rolling back restored both behaviors. This tests the SQL constraint separately from any caller's deduplication logic.

The previous guard accepted both drops as an identity-forwarded migration. The [updated guard](https://github.com/TinyAssets/TinyAssets/commit/b84df8919c1aab4341da263073e632a716d36c16) attributes named indexes and triggers to their tables using the same catalog scan. The regression failed before the change with eight other guard cases passing. All 449 Owner guards and full platform types passed on the standalone Node 22 candidate and on Node 24 combined with the sandbox branch at `0e48b639`. No migration was added. The generation-forwarding decision remains open.

## Mixed-case names in SQLite

At [f32b1c6e](https://github.com/TinyAssets/TinyAssets/commit/f32b1c6efe6c76901084a42c91d327a68b3014ff), the review follows the trigger's table from the SQLite catalog and compares names without regard to case. A hypothetical read-only predicate on `"Object_Owner"` passed the previous guard and refused a formerly valid owner row on reload. An update to `"Log_Capture"` also changed that row through a trigger on the internal table. Dropping the trigger stopped the indirect write. Both Node 22 and Node 24 confirmed those results on separate databases for each of schemas 31 through 35.

The new regression failed before the correction, with nine migration guards passing. Afterward all 16 migration and frozen-bound cases passed. All 450 Owner guards passed standalone on Node 24 and in the combined restore, cursor and sandbox rehearsal on Node 22. Full platform types passed on standalone Node 22 and combined Node 24. The earlier combined typecheck exited 137 while Worker tests were running and passed serially. These checks change the migration review. The live-run predicates and the forwarding decision remain experimental.
