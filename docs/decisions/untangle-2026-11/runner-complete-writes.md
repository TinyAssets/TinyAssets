# Sandbox runner disk and holder writes as built (2026-11-19)

- [x] Traced the platform's regular-file writes after reviewing short import
  archive writes. The sandbox sync writer already advances by the actual byte
  count. The runner's disk reserve advanced by the requested count, accepted
  any existing file as complete and returned success. The runner's lock linked
  its temporary file after a single holder-record write. A write error also
  left the temporary name behind.
- [x] The reserve now uses complete writes, checks the size of an existing
  reserve, and replaces a short one when the existing free-space rule permits
  it. With less room the short file stays available for release. The holder
  record uses a complete write before linking. The temporary name is removed
  on success and failure.
- [x] Independent local Linux checks used constant synthetic data. A transient
  file-size limit made the kernel return 4,096 for a 1,048,576-byte write.
  Main f934871ad reported success at 267,390,976 reserve bytes and later
  accepted an 8,192-byte file. With the fix both ended at the configured
  268,435,456 bytes. A zero file-size limit caused a real EFBIG on a new
  holder record. Main left one temporary name and the fix left zero. These
  outcomes matched on Node 22.23.5 and 24.22.3.
- [x] Standalone source a836ee72 passed the full Node 24 platform typecheck,
  140 ordinary sandbox Worker and guard tests, and six selected ordinary
  PID-lock tests on Node 22 including the disk-limit cleanup case.
- [x] Joined PR #5680 at 3200ee3e, reboot/session 6aea8136 and sign-in status
  26a281e with a836ee72. All source and tests applied cleanly. The combined
  tree passed 140 sandbox Worker/guard tests and 64 layer and kernel checks
  on Node 24.22.3, and full platform TypeScript on Node 22.23.5. The three
  independent Linux checks above also passed on both runtimes on the join.
- [ ] The ordinary privileged suite exited 2 at preflight on this host. Its
  tmpfs and UID cases require root with mount namespaces or passwordless sudo.
  Run the permanent cases on the privileged lane and recheck on main.

## November 19 reconciliation with current staged callers

- [x] Rebuilt the four-source combination above directly from GitHub heads.
  All eight changed source and test files matched the saved runner rehearsal
  byte for byte, including the combined runner and its test. No additional
  tracked source changes from that checkout need publication.
- [x] Added 08788f8a (login arguments), e0067de9 (paste), bb863130 (probe
  completeness), 44e44370 (sync identity), 534e6bcb (ignore) and dd5a1022
  (refused workspace edits) to the local four-stage rehearsal. All 26 changed
  source and test files were retained. The overlaps in runner.ts and
  sync.test.ts required the imports used by both sets of callers.
- [x] The 140 ordinary sandbox Worker/guard cases passed but the sources
  suite found the Sandbox Worker imported a source-record module for paste
  validation (354 passed, one failed). The repair on the ordinary paste
  branch, 9c4c50ac, moves that parser to the existing sandbox RPC contract.
  With the repair the joined sources suite passed all 355 tests, the sandbox
  suite passed all 140 and the six Doors sign-in tests passed.
- [ ] Five selected runner files on the ten-stage rehearsal had 44 passes,
  five skipped, and eight failures. Five confirm pending retry after a storage
  refusal; two confirm the outstanding workspace content/mode cases. The PID
  test stopped at unshare on this host. These suites do not certify either
  the pending workspace design or privileged acceptance. Recheck with the
  pinned sandbox and after integration on main.
