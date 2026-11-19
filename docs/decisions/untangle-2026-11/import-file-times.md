# Historical file modification times during import

The survey reads the filesystem modification time and the builder writes its
whole milliseconds into each ordinary file entry. Opening that signed bundle
through Doors on main can drop the entire owner delivery. In a
synthetic old-stack backup on main f934871ad, setting the fixture's existing
executable to -1250 ms was enough. The unmodified build and stage produced a
bundle and the queue acknowledged the delivery after reporting that the file
entry was out of shape. The ordinary end-to-end test failed with zero of the
nine expected chunk keys.

- [x] The normal bundle guard preserves the exact timestamp, mode, contents
  and folder for that executable and still reports the owner bundled. This
  case passed on Node 22.23.5.
- [x] Reproduced the failed delivery on f934871ad with Node 22 and local
  Workerd by setting that same file time in the existing Doors import setup
  before buildBundles, then running the pending-owner readback case alone.
- [x] Reviewed the producer, stage, signed consumer, owner import, file writes,
  storage, export and readback. Doors and both owner validators reject a negative
  timestamp. The ordinary owner test specifically requires -1 to be invalid.
  SQLite stores the value as an integer, export includes it, the import check
  compares exact milliseconds, and the file reader renders it as an ISO date.
- [x] Merged [slice 5](https://github.com/TinyAssets/TinyAssets/pull/5477)
  settled the disposition after [slice 4](https://github.com/TinyAssets/TinyAssets/pull/5475)
  left the modification time outstanding. The import keeps each file's own
  milliseconds in its existing folder and compares them during importCheck.
  A test's blanket rejection of -1 was inconsistent with that requirement.
- [x] Doors, owner import and the optional timestamp on file writes now share
  one range check. Signed whole milliseconds within the ECMAScript Date
  range can be rendered by fileFetch. Fractional or out-of-range dates are
  refused. A missing optional time still uses the write's clock. No schema
  or bundle format change is needed.
- [ ] Verify the ordinary historical host, signed Doors delivery, owner inline
  and chunked readback, public ISO timestamp, importer checks and full
  platform typecheck. At the source checkpoint the host's 23 ordinary
  bundle tests had passed on Node 22. Other results are pending. Repeat the
  checks together with staged consumers and after coordinator integration.

The separate archive writer preserves historical times in ignored or refused
trees at whole-second tar precision on 8535a52d. Ordinary file entries use
milliseconds and go through the owner directly.
