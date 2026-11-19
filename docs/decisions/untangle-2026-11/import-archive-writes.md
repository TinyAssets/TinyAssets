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
- [ ] Full ordinary importer and platform TypeScript checks are in progress.
  The first typecheck found two overload errors in the test stub. Its parameter
  now narrows from unknown to Buffer. Recheck the published change alongside
  the staged importer changes and again on main after coordination.
