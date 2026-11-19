# Historical file modification times during import

The survey reads the filesystem modification time and the builder writes its
whole milliseconds into each ordinary file entry. Open the signed bundle
through Doors and the same input can be lost for the entire owner. In a
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
- [ ] Resolve the supported timestamp range across these callers. Preserving
  historical files in their original folders requires signed milliseconds
  that the date renderer can represent. Keeping the existing rejection
  requires the census and builder to identify an affected owner before
  staging, with an agreed lossless disposition for that file. Until that
  decision, a successful host rehearsal alone cannot establish importability.
  Recheck both inline and chunked files, the check operation and the public
  file reader on the chosen contract. This investigation changes no runtime.

The separate archive writer preserves historical times in ignored or refused
trees at whole-second tar precision on 8535a52d. Ordinary file entries use
milliseconds and go through the owner directly.
