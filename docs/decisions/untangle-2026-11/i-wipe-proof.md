# Import window wipe evidence as built (2026-11-18)

1. The window can stage the same owner again after a wipe (`i-window-close.md`). A fresh owner object can use a different files namespace. Replacing the first discard receipt with the last one made a late object under the first namespace invisible to the proof. A failed host record read also started an empty record, even for a read error other than a missing file.
2. The private window record now retains all distinct namespaces each owner's confirmed discards named. Reading the original single-namespace format carries that value forward. An explicit null reports no additional namespace. An invalid namespace or an unreadable record stops the pass.
3. A host can stop after the doors put their discard answer in staging and before the host writes its record. On retry the host recovers prior successful final discard replies for this run before issuing any discard. It accepts an earlier valid bundle digest in the owner's private prefix because the run can be restaged. The direct answer must still match the bundle just asked about. The listing skips paged check results. The proof only lists the files prefixes it recovers.
4. Cleanup deletes the inspected staging snapshot and the direct replies it has recorded. An unexamined key arriving during the pass remains for the final listing. A nonempty listing or a late files object gives CLI exit 3. A retry can read the remaining receipt even after the staging index is gone.
5. On main the restaged namespace case produced a false empty proof. The unreadable host record allowed a discard to be sent. A separate interrupted-host control reproduced loss of the already-published namespace. The updated importer passed all 172 tests under both Node 22 and 24, plus platform typecheck. An independent entry-point replay exercised a changed bundle digest, damaged receipts, a failed staging read and a late reply. No real owner, store or queue was accessed.
6. This recovers evidence present in the host record or staged replies. A producer stopping before the first durable receipt, or replacing an earlier reply to the same ask, is a separate producer durability question. The dark rehearsal and the old deletion-writer handoff before real admission remain open.
7. A failed files listing used to leave no staged index. The independent close then accepted the run even though wipe had failed. The host now retires indexes only after checking the files prefixes and a second staging snapshot. Residual files, a failed listing or a newly published answer keep the indexes and private receipts available to a fresh host. Once proved, the host deletes the indexes before replies. An interruption after the first index deletion retains the receipt for that owner.
8. A late publication after the second snapshot remains possible. The final listing reports leftover staging, but a separate close reads indexes and can already pass. Closing that interval requires coordination with the producer. This change preserves the founder's close predicate. The host does not promise that later file writes are impossible.

### Report what the bounded listings measured

The final staging probe reads at most one key. The files probe reads at most one
key per discarded namespace. A run with multiple retained staging entries and
two files under one discarded namespace printed one object in each bucket on
4a53b14f. The CLI uses the zero/nonzero result to set its exit code.

- [x] Report staging presence and the number of nonempty discarded namespaces.
  The ordinary window suite passed all 25 cases on Node 22 with the revised
  reporting and the multiple-object regression. The original report failed
  the regression.
- [x] Retain the bounded probes when combining with the S3 listing and close
  grouping branches. The final observation still cannot fence a later writer.

On 839b971e the ordinary Node 22 window suite passed all 25 cases. Seven
importer suites passed 122 cases in a wider run. Host and runner failed
collection in that checkout because the workflow fixture was missing. After
restoring the fixture the full run exited 137. On a fresh standalone checkout
of 439fc49b the ordinary Node 24.22.3 importer command subsequently passed
all 173 cases across nine test files.

The local comparison combined 839b971e, e3293eb9 (S3 listing), and the
a799066e per-run grouping loop. All 42 window test bodies passed with their
original Vitest expectations and cleanup under direct Node 22 and 24 execution.
Ordinary Vitest on the combined tree exited 137, including a run filtered to
the retained-namespace case. That runner result and the late-writer fence
remain open.

A fresh checkout with the locked dependencies combined the importer source
and tests from this branch at 439fc49b, listing at c49f4ddf and grouping at
a799066e. For the adjacent close loop, use the wipe branch's listing call
and the grouping branch's per-run array update. On Node 24.22.3 the full
ordinary importer command passed all 190 cases in nine files. The separate
window command passed all 42, including both stopping boundaries for a
one-key listing. TypeScript passed with the platform compiler options and
all importer TypeScript roots. This resolves the ordinary combined-run gap.
The last listing still cannot prevent a subsequent producer write.
