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
- [x] Source a7d7a7d4 passed all 21 ordinary owner import cases on Node
  22.23.5, including inline and chunked pre-epoch times and the public ISO
  date. The signed pending-owner test that dropped the bundle on main passed
  on this source. So did the full Node 24.22.3 platform typecheck. The
  standalone host fixture passed all 23 ordinary bundle tests. Independent
  Date comparisons covered 14,354 inputs on each Node runtime.
- [x] With the 23 staged changes documented at fa92c218, shared folder
  reader 72672304 and archive correction 8535a52d, all 26 ordinary bundle
  tests passed on Node 22. Preserve both the F1 tier and this timestamp
  when reconciling the chunk-upload test helper.
- [x] On that combined source, all 25 ordinary owner import and all 22
  signed Doors import cases passed on Node 22, including importCheck, along
  with the full Node 24 platform typecheck. The F1 tier fixture and the
  historical-time fixture both remained in the same upload helper.
- [x] The 47 owner-bound, starter and limits guards passed on Node 22 in a
  full checkout with only the host's UNDICI-EHPA warning suppressed. With
  the warning enabled, one starter stderr assertion failed and 46 passed.
  An earlier unfiltered owner invocation in a sparse tree was interrupted.
- [ ] Check historical stored values before applying any data repair. On
  f934871ad an offline owner accepted 8640000000000001 ms. Its fileStat
  returned that value and fileFetch raised Invalid time value. The exact
  upper boundary formatted correctly. The diagnostic assertions passed but
  the Worker also reported an unhandled rejection, so the run failed. This
  synthetic result establishes neither occurrence nor scope in existing
  accounts. a7d7a7d4 excludes new out-of-range imports. The file reader
  promises an ISO modification date, so any disposition for old values
  must preserve file access and avoid inventing a timestamp.
- [x] The same combined runtime passed 332 platform CI cases on Node
  24.22.3 with one excluded, all seven pre-commit invariants and the shape
  registry. Rerunning the six platform generators left the entire tracked
  diff against main byte-identical.
- [ ] Repeat after coordinator integration on main.

The separate archive writer preserves historical times in ignored or refused
trees at whole-second tar precision on 8535a52d. Ordinary file entries use
milliseconds and go through the owner directly.

- [x] In local Workerd, the original Doors queue consumer from f934871ad
  acknowledged the historical fixture and stopped with two collection rows,
  zero file entries and zero chunk writes. Resubmitting the identical signed
  message to the combined corrected consumer on the very same storage kept
  both rows, restored 28 entries with nine chunk puts and passed importCheck
  and public readback. Another delivery added zero puts and left the complete
  check identical. Both consumer versions used the same staged owner and
  Files Workers. An acknowledged delivery requires explicit resubmission.
- [x] On 08b27231 all 22 standalone Doors import cases passed on Node 22,
  including the committed-rows resend case. That same regression passed
  on the combined source. On Node 22 and 24, repeating the actual staging
  CLI against its offline fixture reproduced every URL, method and body
  across 35 requests. All three signed messages were sent again. This
  command reissues the batch, including owners whose first delivery succeeded.
- [x] The ordinary owner memory leg on the a7d7a7d4 runtime measured
  99 rows and all 66 verdicts passed. Local dry-build comparison at the same
  paths as f934871ad found exactly two changed ES modules, Doors and Owner,
  out of eight runtime modules across six Workers. The runner data and Wasm
  also matched. For seven affected doors, 14 official RPC recorder cases per
  source produced 686 byte-identical canonical records.
- [x] The same seven doors completed their 14 official reviewer cases on
  main and the staged runtime. All 4,592 canonical reviewer lines matched
  byte for byte. All 23 ordinary combined Doors import tests then passed.
- [x] Mixed-version acceptance requires both Workers. With corrected Doors
  and the original Owner, the delivery was acknowledged with two rows, 21
  entries and nine chunk puts, no completion receipt and a failing import
  check. All eight locally built runtime modules matched the intended mix.
  With the original Doors and corrected Owner, the same fixture stopped at
  two rows and zero entries. With both corrected it recovered all 28. The
  coordinator must verify both versions before resubmitting affected bundles.
- [ ] Finish the wire and tool-family comparison on this runtime. The full
  owner recordings and recovery of state produced by the older Owner during
  a mixed-version delivery remain open.

On main `f934871ad` and corrected `a7d7a7d4`, the ordinary Doors recorders each passed 47 wire cases and one discovery case. All 804 canonical wire records (110,359 bytes) and seven tool profiles (157,462 bytes) compare byte-for-byte. The owner comparison above covers the seven affected doors. Same-storage recovery from the mixed run with the previous owner is still being checked.

- [x] Recovered the later partial import across two local Workerd instances on the same persisted SQLite, KV and R2. The original Owner module matched main f934871ad and its replacement matched a7d7a7d4 by SHA-256. Corrected Doors and original Owner left 21 entries, two rows, nine readable chunks and no completion receipt. Before resending anything, the new process returned the identical partial inventory. Resending the original signed message completed all 28 entries with zero additional chunk puts and the same row digest. The job verification returned zero missing, extra or different. A third delivery made zero puts and left the complete inventory identical. After admission the ordinary reader returned the original 18 bytes, file mode and timestamp (-1250 ms) by content hash. An earlier harness attempt reopened empty temporary storage and failed the inventory check. This is offline upgrade evidence. Both corrected Workers, an explicit resend and per-owner verification are required before coordinator acceptance.

- [x] Check host eligibility before signing older and newly produced bundles. On the staged sources including f0443205, real Linux files at each Date endpoint and one millisecond beyond each produced complete synthetic bundles on Node 22 and 24. The staging reader accepted all four. The shared signed-consumer predicate accepted exactly the two endpoints. Ordinary files and archived databases use milliseconds, while private backup and ignored-tree archives have different preservation obligations. Keep independent verification and cleanup available for already staged runs. No real account inventory was examined.

Candidate applies the existing file-time range in the common owner fit for census and bundling. The staging step reads manifest pages from older, already-built bundles before any upload or queue request. Its parser is the signed consumer's existing reader moved to the import contract. Private backup stays available through the existing backup path, and verification, wipe and close retain their inventory reader. The two ordinary regressions failed on 0f561591 and passed on the first candidate. On 98e2c484, Node 22 passed all 169 ordinary importer tests, 22 Doors import Worker tests, 297 contract checks (18 excluded) and 64 isolation/kernel guards. Full platform typechecking passed on Node 24. Combined with the exact filesystem reader f0443205 and staged consumers, 82 ordinary bundle, staging and window tests and all 23 Doors import tests passed on Node 22, alongside a full Node 24 typecheck. Eight actual old-stack-shaped census and bundle checks on each of Node 22 and 24 covered both ISO endpoints, one millisecond beyond each, public eligibility and separate private backup. The two outside public owners were held before publication and each private bundle kept its exact original milliseconds. Compared with a7d7a7d4, seven of eight emitted Worker modules matched in full, including Owner. Only the Doors program differed (+183 bytes). Exact new Doors wire and tool comparisons and coordinator integration remain open.

- [x] Restore consumer. Joined host/Doors/Owner source `98e2c484` with the staged restore begin, page and finish changes and the archived-file precision source `f0443205`. One focused ordinary Workerd comparison passed (68 other cases outside the filter). A pending synthetic owner imported four executable files with distinct bytes at −8640000000000000, −1250, 0 and +8640000000000000 ms, then admitted and completed a real Files generation. After local database loss and eviction, the public restore completed as admitted. All four public reads returned the original bytes, content digests, executable bits and timestamps; file fetch rendered all four dates correctly. Earlier fixture attempts used a path outside the imported tree and expected the private digest on the public entry; the final comparison uses the supported path and hashes each file read. Historical already-invalid stored dates and representative acceptance remain open.

- [x] Official Owner RPC comparison. All 13,622 canonical answers across 139 doors on main `f934871ad` and staged timestamp source `a7d7a7d4` were byte-identical (sorted output SHA-256 `b766d1a639443ced372138b1fcc23ec312144ebbedaf970f762ca3a1e0e065f9`). Both trees passed the ordinary recorder in disjoint door groups. The later host admission change `98e2c484` emits the same Owner module. The Owner reviewer family and later Doors wire/discovery comparison are tracked separately.

- [x] The four-date database-loss regression now lives in the ordinary Owner restore suite. It failed against main Owner and Files source during the import of a negative date. On the standalone timestamp runtime it passed (one focused Workerd case, 62 outside the filter), as did the full workspace TypeScript check. The synthetic database reset is intentional. With the previously verified joined restore run this covers both the standalone and staged combined callers.
