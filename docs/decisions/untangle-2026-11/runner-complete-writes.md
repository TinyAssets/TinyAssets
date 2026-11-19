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
