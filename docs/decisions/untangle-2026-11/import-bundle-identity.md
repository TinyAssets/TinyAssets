# Import bundle identity as built (2026-11-18)

The backup survey derives a full SHA-256 from the original principal. Its first 16 hex digits name the bundle folder. A rehearsal uses the substituted principal for the emitted index and its folder. Excluded owners keep their original names in the private backup. The format-1 writer already produces these layouts.

The local stage, the doors' consumer and the all-runs close now read identity through the same check. The format, embedded key and hash of the index owner must agree with the folder. The consumer continues to check the signed digest and the message owner. Close could previously count a misplaced admitted owner's index twice and missed a different staged owner entirely.

The regression uses the builder's real indexes for original and substituted names. It checks an intact directory, one missing admission, a misplaced index, and an index whose key field was corrected without correcting the owner. Stage rejects inconsistent identity before uploading. The consumer's refusal is exercised through the import queue.

When the deletion comparison in #5688 and this change meet, its private original digest and emitted owner key use the same `bundleIdentity` helper. This change does not settle the separate discard-generation or late-producer questions.

### In-progress comparison with close grouping and wipe

- [x] On 727b718b the close reader expects owner folder keys. The grouping
  stage a799066e and wipe stage 2d527bca carry full object names. Both are
  strings. Rehearsing the published versions with prefix e0a847e3 and listing
  1bbfa667 showed that retaining the older grouping payload alongside the
  identity reader silently skipped a staged owner. The two ordinary identity
  tests failed for both original and rehearsal names. Each expected one
  missing admission and received zero.
- [x] Keep the listed object name and parsed folder key together through
  grouping, index lookup and validation. Verify both ordinary identities,
  the broader importer and the Doors consumer. Main integration remains open.
- [x] The close reader now carries the entire listed index through grouping.
  It reads its object by name and checks the embedded identity against its
  folder key. The two fixture variants also check all index GETs in order
  and the admitted owner count. All 21 ordinary window tests passed locally
  on Node 24.22.3. The full standalone importer passed all 169 tests on source 13453f71.

- [x] On the combined importer, retain the wipe branch's listing snapshots
  and parse their keys with bundleLocation. Group the returned index records
  and read each by name. The ten other staged inputs are F1 16161c75, policy
  4e102795, verbatim rows ba653c28, deletion comparison 3dd64974, contract
  digest b02a640e, host digest 2bd83dbb, prefix e0a847e3, listing 1bbfa667,
  wipe 2d527bca and grouping a799066e. Source 13453f71 with those stages
  passed all 199 importer tests and the platform typecheck on Node 24.22.3.
  All 22 Doors import Workerd tests passed on Node 22.23.5.
- [x] Independently read 1,013 genuine folder-keyed synthetic indexes in
  three runs across three listing pages. Sorted and shuffled listings both
  yielded exactly 1,013 index GETs in the same order and the same 93 missing
  admissions and ordinals. Each ordering passed on standalone and combined
  source on both Node 22 and 24.
- [x] The identity stage now adopts the shared bundleLocation reader from
  e0a847e3 at its original contract path. The signed message, staging scan,
  dead-letter reader and Doors index-key lookup use the same run and folder
  grammar. The independent prefix stage still owns its queue log change.
  This resolves the previously deferred Doors lookup while keeping both
  branches based on main.
- [x] Compared 17,343 distinct prefix inputs with 13453f71 on Node 22.23.5
  and 24.22.3. Signed-message parsing preserved all 4,386 accepted values
  and all 12,957 refusals on each runtime. The folder reader returned the
  original run and key for every accepted input.
- [x] Source 72672304 passed 169 ordinary importer tests on Node 24.22.3,
  21 Doors import Worker tests on Node 22.23.5 and the full platform typecheck.
  With the 23 staged changes identified in the archive verification at fa92c218,
  all 22 Doors import Worker cases passed on Node 22, as did the full Node 24
  platform typecheck. The combined contract preserved the same 4,386 accepted
  values and 12,957 refusals on each runtime. Only the Doors lookup and its
  test fixture changed relative to that rehearsal.
- [x] The repository's Node 22 Doors wire and discovery recorders passed
  on both f934871ad and 72672304 (47 wire cases and one discovery case per
  source). After the recorder's normal cursor normalization and sort, all
  804 wire lines and seven discovery profiles matched byte for byte.
- [ ] Compare the owner RPC and reviewer recordings for this source, then
  repeat consumer checks on main after coordinated integration.

- [x] Built all six configured Workers with the repository's pinned local
  build function, at the same filesystem paths on f934871ad and 72672304.
  The hashed list contained eight runtime modules on each source. Only the
  Doors ES module differed. The emitted Owner, Files, Gateway, sandbox
  Worker and runner data, and Codemode Worker and WebAssembly were
  byte-identical. The separate owner recorder families remain unrun here.
