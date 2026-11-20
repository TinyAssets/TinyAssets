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

### Failed publication of a runner file

- [x] On 1da3cd6f the ordinary single-piece writer received ENAMETOOLONG
  at rename and left a temporary file in the destination directory. The new
  Linux filesystem regression failed on that branch and passed with cleanup
  in the shared stream writer. Its caller still receives the rename error.
  Both single-piece writes and streamed downloads use this implementation.
- [x] Source 5c6a3e60 passed five ordinary file-operation tests on Node 22,
  including the new failure case and the concurrent directory writer, and
  full platform TypeScript on Node 24. The same five tests, 140 sandbox
  Worker and guard tests and full types passed on the eleven-stage sandbox
  rehearsal with this fix. One ownership case was excluded from both focused
  runs. The normal privileged runner stopped at its root preflight (exit 2).
- [ ] Run the ownership case on the privileged runner and compare on main
  after coordinator integration. This change addresses failed writes. The
  separately staged refused-workspace investigation still owns unsent edits.

### Incoming engine session copies

- [x] Traced the downloaded session from sync-in through store import and
  home creation to writeback and upload. The copy into the engine store
  neither used the store lock nor bounded the source read. Home creation
  also allocated the entire transcript. The earlier synthetic replay on
  44e44370 observed an 8,388,639-byte allocation at each step.
- [x] The common regular-file copy reads one descriptor, writes through the
  existing atomic writer, and publishes only after the copy finishes. Import
  checks for an existing destination under the same per-engine lock as
  home creation and writeback. The ordinary Node 24 test verified a held
  lock, preservation of a newer stored value, a multi-chunk copy and its
  private mode. Independent Node 22 and 24 replays of the same synthetic
  three-chunk session preserved all bytes through upload. The largest source
  buffer observed at store import and home creation was 4,194,304 bytes each.
- [ ] Check joined callers, whole platform types, sandbox tests and the
  privileged runner. Home writeback and outgoing staging still read whole
  sessions. The path-count cost, historical snapshot, overlapping edits
  and retry ordering remain separate open findings on the identity branch.

- [x] On 5acdfb60 all 140 standalone sandbox Worker/guard tests and six
  focused file tests passed (one ownership case excluded). The first full
  typecheck rejected the allocation assertion's inferred unknown argument.
  The corrected assertion converts the recorded size to a number.
- [x] Rehearsed the corrected assertion and source with the saved sandbox
  stages, conflict-copy reporting and two-conversation regression. On Node 22
  all 18 session-import, session-cache, disk-format and file tests passed.
  The 64 layer/kernel checks, 140 sandbox tests with the ordinary runner and
  probe builds, and full platform TypeScript passed. The separate privileged
  entrypoint stopped at the root preflight, exit 2. Distinct runs can still
  overwrite newer transcripts during outgoing writeback or delayed upload.
- [x] Independently exercised 8e51fa94 on Node 22 and 24. With a native
  file-size limit of 4,200,000 bytes, copying an 8,388,639-byte file failed
  after the first full chunk. The previous destination stayed intact, no
  temporary name remained, and an ordinary retry copied all bytes with
  mode 0600. In two actual processes a live store holder wrote a newer file
  while the importer waited. The importer kept that file and copied the
  other 8,388,639-byte session exactly on both runtimes.
- [x] The joined source suite passed all 355 cases. All 152 test definitions
  checked across the four overlapping files survived the rehearsal with the
  intentional test edits. The full ordinary workspace sync suite finished
  with 26 passes, nine known failures and four size cases excluded. The
  nine failures matched the preceding combined checkout by test name.

### Sync replies over a pipe

- [x] On 7a5f26d1 the ordinary sync command produced all 936,901 expected
  bytes for 12,000 synthetic mirror paths when stdout was a regular file.
  Over a pipe to a Node parent it exited zero after 146,176 bytes. A
  two-path control delivered all 161 bytes both ways. Node 22 and 24 agreed.
  The parent already waits for process close. Its JSON parse failure treated
  the reply as absent, so a mirror lookup could act as though no paths were
  held and an outgoing sync could drop its stage without reporting outcomes.
- [x] Serialize the reply after releasing the mirror lock and await the
  stdout write callback before exiting. Reject an incomplete success reply
  in the caller. Preserve the existing nonzero failure and fence handling.
  The same two inputs produced all 161 and 936,901 bytes through both sinks
  on Node 22 and 24 with the producer correction. Added the large lookup
  to the existing ordinary sandbox integration suite.
- [ ] Finish the corrected caller/producer rehearsal with stage reporting,
  verify ordinary build and types, and run the privileged test on a capable
  worker. The path count and transcript ordering issues remain open.
- [x] Independent caller review found the same sync reader, producer and
  runner entry point on 156 of 162 reviewed branch heads. Preserve explicit
  runner termination because an exceptional run can still own a listener.
  The outgoing report is generated while holding the lock, then written
  after the lock releases. The parent waits for process close.
- [x] Reject an incomplete mirror map as well as truncated JSON. The
  diagnostic for a cut-off success reply omits its contents because the
  runner can forward it into a conversation. Three ordinary Node 24
  reader regressions and full platform TypeScript passed. The joined
  producer retained all 936,901 bytes on both Node versions. The privileged
  entrypoint for the permanent large lookup stopped at root preflight (2).
- [x] On e56edbc3 the parent and its separate child processed 4,096
  two-byte synthetic transcripts already recorded in the real on-disk
  mirror. Only the test directories and the child user switch were
  substituted. On 7a5f26d1 the lookup returned no usable map and
  stageSessions copied all 4,096 transcripts. On the fix all 4,096
  answers arrived and zero files were staged. Both Node 22 and 24
  produced the same outcomes. Six child controls on each version
  covered cut-off JSON, blank output, lease fence (4), ordinary failure
  with stderr (17), a valid answer and a well-formed missing answer.
- [x] With the prior sandbox stages and saved conflict-path work, all
  21 focused runner tests on Node 22, all 140 sandbox Worker/guard
  tests on Node 24 and full Node 24 platform TypeScript passed. The
  root integration case and pinned-image acceptance still need their
  respective environments. Other probe executables have their own exit
  paths. Their output completion has not been verified by these tests.

### Paired probe command output

- [x] Reviewed the four stdout replies in the paired probe executable. The
  plan, sign-in, route and matcher handlers all wrote JSON and could return
  their status before stdout completed. The local plan command needs no
  vendor or sandbox. On 43997671 it returned success with zero of its 3,120
  answer bytes when a real pipe was full until after the write returned.
  The ordinary asynchronous-output regression also failed. The template
  coordinator parses the plan before proceeding. A missing check reply
  fails its result check.
- [x] The runner and all four probe replies now share one stdout writer
  that resolves after the write callback. Their existing JSON framing and
  status codes are preserved. The ordinary offline plan delivered all
  3,120 bytes under the same pipe pressure on Node 22 and 24. Comparing
  the two complete plan documents found only the expected change in the
  paired runner digest. This is staged source and a regression. Actual
  pinned-image checks remain outstanding.
- [x] On d7d0c143 all eleven ordinary template guards and full Node 24
  platform types passed. With the staged runner and session changes, 141
  sandbox tests and full Node 24 platform types passed. The combined offline
  plan also delivered the same complete 3,120 bytes through an initially
  full pipe on each of Node 22 and 24. The overlap in probes.ts keeps the
  staged login argument owner and complete prompt check while returning its
  answer through the shared writer. Actual pinned-image acceptance is open.

### Template check reporting

- [x] The workflow and local pair checker use the same Node command. Its
  final log describes the pair already recorded. Its error log explains
  the nonzero status. On 5b7c2722, two ordinary workflow cases lost the
  final success report or refusal when those streams completed asynchronously.
  The caller separately checks the pair tag before proceeding.
- [x] The command now sets its exit status and lets Node drain both streams.
  All 27 template and workflow tests and full Node 24 platform types passed.
  Running the ordinary pair command with the existing fake service on Node
  22 and 24, and holding a native pipe full, previously received zero bytes
  on success and on refusal. The correction returned the entire success
  log (4,181 bytes here, matching the recorded pair) or the refusal reason
  (278 bytes), with exit 0 or 1 respectively. A refused run left the pair
  record empty. Pinned-image acceptance remains open.
