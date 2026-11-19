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
- [ ] Run the permanent disk and holder tests on the ordinary privileged
  runner, plus sandbox Workerd, guards and platform TypeScript. Verify with
  the staged sandbox renewal changes before integration.
