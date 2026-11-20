# Recovering refused workspace writes, November 19

- [x] On main `f934871ad`, the ordinary sync-in, sync-out and SQLite mirror were exercised in sequence with a synthetic owner endpoint. Version 1 arrived, a local edit was refused for full storage, the mirror closed and reopened, and the next run synced in before retrying. With no remote change the next offer contained the local bytes and original base. An incoming version 2 replaced the local edit, and an incoming deletion removed it. In both cases the next sync-out sent zero retries. The three Node 22 cases had one pass and two failures (23 other cases outside the filter).
- [x] The standalone identity work at `aa341901` on Node 22 and the combined digest, ignore, identity and sandbox renewal work on Node 24 had the same two failures and one passing control. The local file and its mirror version are separate: keeping the old base alone cannot recover bytes after sync-in replaces them. The separate mirror-format research is on `codex/untangle-sync-file-identity`.
- [ ] Unfinished. ADR-032 keeps the API version and saves a reported conflict copy when both sides change a file. The doors spec requires a storage-refused file to survive for a later run. Reconcile those two requirements before changing incoming-file application or upgrading the mirror format. Check existing dirty files on old images, parent directory changes, interrupted delivery, reset and narrower grants, bounded disk use, conflict results and repeat recovery. Keep clean incoming updates and owner removals working. No runtime repair has been staged here.

- [x] A feed reset can lose the refused edit even when the owner never changed the original file. After the stored cursor was reset, the owner listed its original version and the ordinary sync-in refetched it over the local edit. A reset that listed a newer version or omitted the path also lost the edit. The corrected one-reset fixture on Node 22 produced five failed recovery cases, one passing quiet-feed control and 23 cases outside the filter. An initial fixture repeated its reset forever and was interrupted; it provides no recovery result.
- [x] The unchanged real Owner handled four lease conflict cases, including a newly deleted original. When the sandbox actually offered the retained bytes and original base, the original stayed deleted and the reported conflict copy held the original sandbox bytes and executable bit. Existing cases for concurrent replacement, changed parents and conflict naming also passed. This locates the loss before the Owner sees the retry.

- [x] Exact `b1573190` ordinary Node 24 sync test file, excluding sizes: 20 passed, five refused-edit cases failed, four size cases excluded. Full workspace types passed. The same six focused cases on the staged digest/ignore/session/renewal combination produced the identical five failures and one passing quiet-feed control. Four real Owner conflict cases passed. Next: retain the edit and original base through an incoming operation and restart; consuming a feed cursor cannot discard the obligation for that path.

- [x] Checked retry after an uncertain reply against the real Owner on main source. The API replaced the original file after the sandbox took its base. Two deliveries of the identical retained edit under the same lease and base returned two different conflict-copy paths. Both copies contained the correct bytes and the remote replacement stayed intact. One isolated Workerd case passed. An unconditional offer before every incoming update cannot by itself establish whether an earlier attempt already saved the local bytes. Keep this distinct from two genuinely different edits that both need saving.

- [x] Checked a mixed commit through the real lease-bound Owner and Files Workers on main `f934871ad`. One batch wrote an inline file and a 6,000-byte file using its uploaded chunk. The first reply reported both committed. Delivering the identical request a second time returned a conflict for the inline file and a refusal for the consumed upload intent. Readback of both original files and the extra inline conflict copy matched the submitted bytes. One focused Node 22 Workerd case passed, with 60 unrelated cases filtered. A retrying client that lost the first reply thus receives neither original outcome. Any recovery design must cover mixed batches and keep the original per-file outcomes recoverable across a lost reply. A genuinely new edit must stay distinguishable from a redelivery.

### A refused new child has no mirror row

- [x] A new ordinary runner test on dd5a1022 created a child under an existing
  remote directory. Full storage refused the first write. The runner offered
  the child's bytes with base null and its SQLite mirror had no row for it.
  After reopening the mirror, a quiet feed offered the same bytes and base
  again. An incoming deletion of the parent removed the only local bytes,
  and the second offer never occurred. The quiet control passed and the
  parent-deletion regression failed on Node 24 standalone and Node 22 in the
  ten-stage sandbox rehearsal. This is an intentionally failing regression.
- [ ] Reconcile eligible new children before recursively applying a parent
  deletion. A design consulting only mirror rows cannot find them. Check
  clean and ignored children, a changed parent type, cursor replay, scope
  narrowing and bounded retention. The separate question about a purged
  original file mode does not apply to a child that never had a version.

- [x] On the ten-stage rehearsal, three local Owner Workerd controls passed
  through the normal lease door. A write with a known deleted file version
  kept the original absent and saved a conflict copy. A write below a parent
  replaced with a file saved a conflict beside that parent. A new child sent
  with base null after an empty parent's removal committed at its original
  path and recreated the parent. These three outcomes are distinct. The
  last one describes current behavior, not acceptance of directory revival.
- [ ] Before implementing retry, determine how the runner retains enough
  information about the parent change to keep the API's deletion and recover
  the local edit as required by ADR-032. Reoffering the base-null child alone
  cannot express a previously deleted directory. Full platform types passed
  for the standalone regression; it remains red until retry is implemented.

### Directory history across a reset

- [x] On 8fc06bb0 a new ordinary runner regression syncs an empty owner
  directory, creates a separate local directory and reopens its SQLite
  mirror. An incremental deletion removes the owner directory. A reset with
  an empty listing leaves both directories on disk after the cursor advances.
  The local-only neighbor survives in each case. The incremental case passed
  and the reset case failed on both Node 22 and Node 24.
- [x] A separate Node 22 replay of a refused child kept its bytes through a
  reset and offered them again at the original path with base null. The
  earlier incremental-deletion case instead lost those bytes. On Node 24,
  two replays started the same child under an API-created directory and
  under a new local-only directory. At restart both mirrors had cursor 1
  and no row for the directory or child. After the same reset and empty
  listing both sent the same bytes with base null. The real Owner's
  previously verified commit would recreate the API-deleted parent.
- [x] The existing Node 22 Owner Workerd test now exercises a narrowing as
  well as a widening of the readable set. With two runs on one lease, the
  second agent's directory was listed. After that run ended, the first
  run's feed requested a reset and its list omitted the second directory.
  The owner's own list still included it. The next feed read cleared the
  reset. The selected test passed, with 33 unrelated cases filtered.
- [ ] Preserve the origin of directories across reopen and check current
  visibility before interpreting an omitted directory. The current file
  mirror cannot distinguish the two original histories retrospectively.
  Resolve how to recover a refused child whose parent was deleted while
  honoring the current grant and keeping the original parent absent. Test
  both forms of delivery, local-only directories and old mirrors. A retry
  count alone does not verify the resulting owner tree.
- [x] Two separate Node 22 Owner Worker replays used the real lease-bound
  commit on 5c6a3e60, whose Owner source matches main. Both sent a mkdir
  with the old directory version and a child write with base null in one
  batch. With the parent deleted, mkdir reported conflict and the child
  recreated the directory. With a different directory created at the same
  path, mkdir reported success despite the old version and the child landed.
  A separate mkdir operation cannot condition a child write. Full Node 22
  platform types passed with the two permanent directory tests on 0cbf54bb,
  after a concurrent Node 24 typecheck exited 137.

### Reporting the saved copy

- [x] The Owner already returns the path where it saved a conflicting write.
  The runner discarded that path and reported only the originally requested
  path. Sync-out now carries the returned copy through its own sync report.
  The existing conflict event names the original; a second event marks the
  actual saved copy, when present. Each frame carries one path so legal
  heavily escaped names remain within the shared frame bound. The runner
  uses the same executable for the sync subprocess and its report consumer.
- [x] On the candidate before combining with the directory regression, both
  new ordinary runner cases passed on Node 22 and 24. Four real Owner
  conflict cases and full platform types passed. The Node 24 sync suite had
  23 passes, six earlier refused-edit failures and four size cases filtered.
  Run events are a live report. Long names still exceed the app's preview.
- [ ] Verify the combined source here. A reported copy requires that a write
  reached the Owner. Recovery when the pending bytes never reached it remains
  open. Full privileged and main integration checks are still outstanding.

- [x] On cda05092 full platform types passed on Node 24. The ordinary
  Node 22 sync suite passed 24, failed the seven outstanding recovery
  cases, and excluded four size cases. The reporting regressions passed.
  The 64 isolation and kernel checks also passed on the matching candidate.
  Together with the eleven sandbox stages, runner file-cleanup fix and
  directory tests, the combined suite passed 26, failed the same seven
  plus two previously recorded identity cases, and excluded four sizes.
  Five real Owner Worker tests and the combined full typecheck passed.
  A text-rendering check exercised the 600-character preview; Chromium
  launch failed at its socket, so browser verification is outstanding.

### Lost commit replies and the ordinary Owner tests

- [x] Two earlier local diagnostics used copies of the Owner suites. Their
  distinct cases now live beside the real lease and upload tests. The first
  checks whether retry recovers the same conflict-copy path and preserves
  the newer API bytes. The second checks whether an inline and an uploaded
  write can recover their original outcomes after a lost reply. It reads
  both stored files before comparing the responses.
- [ ] Both tests assert the recovery that the runner needs. The earlier
  experiments recorded a second copy and a refusal for an already consumed
  upload. The current protocol has a lease and operations but no identity
  for an individual delivery. A genuine second edit is still allowed to
  produce another copy. Reconcile this with the runner before retrying
  uncertain batches. These are staged regressions, with verification open.

- [x] On 9d684773 the ordinary Node 22 Worker retry produced a second
  conflict filename. Both copies held the retained edit and the API's bytes
  survived. The mixed retry returned a conflict and a refusal although the
  two stored files matched. Both new recovery assertions failed; the
  existing distinct-edit and normal upload controls passed. The first
  two-file command timed out starting the upload Worker. Its individual
  rerun reached the actual response assertion. A type-only correction
  uses the existing commit request type for the mutable operation list.
  Full Node 24 platform TypeScript then passed on the corrected tree.

### The upload reservation before the lost commit

- [x] The runner already resends both upload-begin and commit requests.
  Independent loopback servers read the entire body before dropping the
  connection. On Node 22 and 24, each door sent identical bytes twice on
  that disconnect, a 429 or a 503. A successful response, a 401, invalid
  success JSON and an interruption after the success headers each sent
  the request once. An interrupted body rejected the call. These were
  28 transport checks. The Owner tests separately verify stored effects.
- [x] A local Worker processed two identical upload begins on one lease.
  After losing the first reply and saving the file with the second, the
  bytes matched but one unused reservation remained. A control used the
  same two begins for two genuine equal-content files. Both committed,
  both read back, and zero reservations remained. The lost-reply assertion
  failed on 5427abd7 with the new tests applied; the control passed.
- [ ] A begin request identifies the lease, size and chunks, but no file
  or logical upload attempt. Reusing the same intent unconditionally would
  tie two genuine files to a single consumable upload. The importer's
  open-upload lookup deliberately excludes leased uploads. Distinguish
  delivery identity from content matching for both begin and commit.
  Check lease release and the joined tree before closing this review.

- [x] The lease-release control passed on the standalone and joined local
  Workers. One reservation remained after the lost reply and successful
  file commit; the ordinary end of the last run released it. Two real
  files with identical chunks kept independent uploads and left zero
  reservations before release. Each four-case run had two recovery
  failures and two passing controls, with 59 unrelated cases filtered.
