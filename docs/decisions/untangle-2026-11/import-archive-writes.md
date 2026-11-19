# Import archive writes as built (2026-11-19)

The importer builds refused.tar and ignored.tar for the census, ordinary
owner bundles and the private backup. All use the same tar writer. On
f934871ad the writer counted the bytes requested from the filesystem even
when a write returned fewer. The builder then hashed the resulting damaged
archive and accepted it. If adding a member threw, the caller skipped the
method that closed the output descriptor.

- [x] The ordinary bundle regression interrupts a header write on the real
  fixture. Main returned a bundle, then GNU tar rejected its contents. The
  fixed writer uses the filesystem's complete-write operation for each part.
  The archive function owns the output from exclusive open to close in a
  finally block. The oversized-member regression verifies descriptor release
  after the refusal. The caller still cleans its temporary tree.
- [x] All 24 ordinary bundle tests passed on Node 24.22.3. Separately, Linux
  returned 73 bytes for an actual 512-byte regular-file write under a transient
  file-size limit. On both Node 22.23.5 and 24.22.3 the old writer returned a
  different archive. The fixed archive matched the unrestricted output byte
  for byte and GNU tar extracted the original member on both runtimes.
- [x] The Node 22.23.5 ordinary importer passed all 168 tests on afefb732.
  Two earlier typecheck attempts found overload errors in the test's short-write
  stub. Narrowing the buffer with instanceof Uint8Array resolved them. With
  this test correction, the full platform typecheck and all 24 bundle tests
  passed on Node 24.22.3.
- [x] On 991d9d0a plus the eleven importer stages documented in the
  [identity comparison](https://github.com/TinyAssets/TinyAssets/commit/5faf91961920c6a34d289bb87e603a6de4faa0f4),
  the complete ordinary importer passed all 200 cases on Node 22.23.5.
  The full platform typecheck passed on Node 24.22.3. The two source overlaps
  were imports. Keep tableRows alongside writeTar in the builder and retain
  truncateSync in the existing F1 fixture.
- [ ] Repeat the archive checks on main after coordinated integration.

- [x] Rehearsed the twelve importer stages above with the seven
  [owner restore stages](https://github.com/TinyAssets/TinyAssets/commit/5eefcdea52a781d6dd33641cfa73f25507b4cdf4)
  and four [sandbox runner stages](https://github.com/TinyAssets/TinyAssets/commit/895d11080c1f49adc6a6916625d32781aab98e86)
  on f934871ad. The three groups changed disjoint sets of 27, 7 and 8 source,
  test and workflow paths. In the full checkout 332 platform CI cases passed,
  one was excluded, and the platform typecheck, seven repository invariants,
  shape registry and clean-sheet OpenSpec validation passed. An earlier guard
  attempt on a sparse checkout omitted required repository inputs and failed
  before this full result. This rehearsal covers the 23 named stages only.

- [x] Both ignored and refused trees use the same archive header. It previously
  changed a file's pre-1970 timestamp to January 1, 1970. The ordinary build and
  GNU tar extraction reproduced zero milliseconds for an expected -2000 on
  fa92c218. They now preserve the original whole second using the same signed
  field as GNU tar. The focused build and extraction passed on Node 24.
  Python tarfile independently read all eight source timestamps and their file
  contents from Node 22 and 24 archives, including -2147483648, -1, epoch and
  10000000000 seconds. File, directory and symlink headers use this writer.
- [x] On 8535a52d the full standalone ordinary importer passed 168 tests
  on Node 22.23.5 and the full platform typecheck passed on Node 24.22.3.
  With the 23 stages above and shared import folder reader 72672304, all
  26 ordinary bundle cases passed on Node 22. Coordinator integration on
  main remains open.
