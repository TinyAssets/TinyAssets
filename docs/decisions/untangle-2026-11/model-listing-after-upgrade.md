# Last successful model listing across migration 35

The [migration decision](models-json-max.md) discards the previous derived
listing and expects a new read within seconds. The [listing decision](listing-records.md)
says an unsuccessful read keeps the last successful listing. Those rules
differ when a connection's setup catalog and its last served listing have
changed, and the first read after upgrade fails.

- [x] Replayed all 34 earlier migrations, then migration 35, with synthetic
  connections on Node 22 and 24. The old Owner exposed the served model
  with score 19.5 and price 4. The new Owner exposed the setup model with
  no score and price 1. An unavailable listing and a healthy listing that
  exceeded the total model limit both left the setup data in use and
  scheduled another read one hour later. Freeing space and rereading
  restored the served data. The same model ID with a changed score and
  price also reverted, without a visible name change.
- [x] On main d662e63e, replayed the shipped derived-table transition in
  Workerd and read the result through the Owner. The existing healthy
  upgrade case passed. The new case failed because a model previously
  removed by the vendor was offered again after an unsuccessful refresh.
  Its final healthy-read control recovered the served model.
- [ ] For owners still on 34, reconcile preserving a completed listing
  with the new per-record and owner-wide bounds and frozen migration.
  For owners already on 35, an active connection that never finished a
  listing and one whose listing was discarded can have the same stored
  state. Decide what the picker may offer until a fresh listing succeeds.
  No production owner history was read and there is no affected-owner count.

Two additional Workerd replays ran the original 3143e07d and d662e63e
Owner constructors on the same stored object across eviction. Its setup
catalog was A/B/C, and its last successful listing was B/C with scores
11 and 92. On 34 the picker and an actual submitted run chose C. After
upgrade both chose A, including after two unsuccessful refreshes. The
sandbox received A. A healthy read restored the scores and the next run
launched C. The tests exercised both a returned malformed listing and a
gateway failure using local services. Full Node 24 platform types passed
on the staged regression 8579c6a8.

Across the complete connections test file on 8579c6a8, all 74 existing
cases passed, including both size cases. The new failed-refresh upgrade
case was the only failure. The full invocation and the first subdivision
were killed. Smaller Node 22 and 24 Workerd runs completed all 75 distinct
test names. Both size cases ran separately on Node 22.

A supported pre-upgrade backup cannot generally recover the lost listing.
On 3143e07d, two synthetic owners with identical setup and no runs served
different models, prices and scores. On both Node 22 and 24 their listing
updates produced zero export change-log entries. The normal row exporter
traversed all 41 exported tables in three pages. With identical generation
metadata, the serialized header, four rows and end record matched byte for
byte. The setup catalog survived. A recorded run can still identify an
individual past choice. This comparison exercised the logical export and
change log with local SQLite, without reading any production backup.
