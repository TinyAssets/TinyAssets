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
