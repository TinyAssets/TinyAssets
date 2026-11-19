# Check inline file bytes before staging

- [x] Reviewed the bundle writer, host staging pass, Doors reader and Owner import on main at `f934871ad`. The writer uses the contract's inline content ID for the disk filename. The index hashes the metadata parts. Doors and Owner already independently check the inline bytes against that ID. Host staging checked chunk names but omitted this check for small files.
- [x] Exercised the ordinary staging entry with a locally truncated nonempty file from the synthetic author's bundle. On main, the fake service received all three messages and the host reported success. On this branch the host refuses the damaged file before its first request. All nine ordinary staging cases passed on Node 24, including valid bundles and dry runs.
- [x] The host now uses the existing inline size limit, content ID and part name from the shared contract. Produced bundles and their format stay compatible.
- [ ] Recheck on main after coordinator integration.

- [x] On staged source `45a6056d`, all 168 ordinary importer tests and the full platform TypeScript check passed on Node 24. On Node 22 and 24, an independent check built ordinary and rehearsal bundles with files of 0, 1, 4,095, 4,096 and 4,097 bytes. All 20 staged paths selected the expected inline or chunk name and kept the source bytes.
- [x] Rehearsed the change with bundle publication `86f4191f`, both digest branches `b02a640e` and `2bd83dbb`, and bundle identity `3af05df8`. All 172 ordinary importer tests passed on Node 22, and full platform TypeScript passed on Node 24. Preserve the shared chunk digest, shared index check and small-file check when combining the staging module. The two staging test imports from Node's filesystem module must be combined. In the 159-head snapshot, 153 heads had a staging module and their four distinct versions lacked the small-file check.
- [ ] Check whether an interrupted local copy can omit an entire file referenced by an intact manifest. The present check covers the identity of each inline file that is present. Reuse existing manifest definitions when reviewing full bundle coverage.

## Preserve historical rehearsal cleanup

- [x] Re-reviewed each caller of the shared bundle reader. Staging needs the bytes it will publish. The discard uses the local owner and bundle digest to identify a previously staged import. Its original bytes can still be correct in staging after local disk damage.
- [x] The preceding source `45a6056d` blocked a previously successful discard. On main, the original wipe with one matching staged owner finished despite a truncated local inline file. On the first fix it refused before altering staging. Restoring the original bytes allowed that wipe to finish, including with `8910c75f` (36 ordinary window and staging checks).
- [x] Keep the additional inline check in upload preparation, before dry-run output or any queue or upload request. Keep the original bundle reader for the existing cleanup checks. The regression that failed on the first fix passes with all 29 ordinary staging and window cases. The fake service receives only the matching owner's discard. Both dry-run and ordinary staging still refuse the damaged file before any request.
- [ ] Finish the full standalone suite and repeat the combined check on this corrected source. Verify completeness for missing manifest-referenced payloads separately.
