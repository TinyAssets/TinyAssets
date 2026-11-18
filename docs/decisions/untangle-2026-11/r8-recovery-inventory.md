# R8 recovery inventory as built (2026-11-18)

Whole-account recovery and its drill use two inventories. A filtered listing finds erasure
marks first. A full listing supplies the objects and holds for the restoration plan. On
main, the full listing could show a mark for a namespace whose objects the plan still
copied. Files answers `erased` immediately on seeing that mark, so the later locate cannot
be relied on to remove those restored bytes.

1. Compare the filtered and full mark sets separately for R2 and for the selected copy.
   Either a newly visible mark or a previously visible mark missing from that store stops
   planning. A historical copy can legitimately have fewer marks than the current copy.
2. The files Worker writes a parent's mark before following its holds. A child can therefore
   still be unmarked when its parent's erasure has started. Follow holds observed in both
   the selected copy and current R2, through rootless intermediates and cycles. Use the
   selected copy's holds to choose which surviving owners to locate. Traversal indexes
   children by holder and visits each reachable holder once per walk.
3. The restore record and wildcard mark copy precede the first namespace byte. Just before
   writing those bytes, reread the current copy's marks and R2's marks. The former must
   match the original current copy. The latter must match the original union after transfer.
   On disagreement the already-written mark and restore record stand. Repeating recovery
   at the same historical time computes the plan again.
4. The stores have no joint snapshot or lock. An erasure or hold added after its last
   observation can still race a write. The check applies to namespace copying. A run that
   finds every object already in R2 can still reach the usual locate and begin. The
   single-owner lost-copy operation remains open under `untangle/r8-lost-copy`.
5. The regression fixtures cover a mark first visible in either full inventory, a mark
   arriving at either live store during the restore record, and an erased parent's R2 hold
   leading to a historical hold and then a surviving root. A local replay used the real
   rclone binary with three isolated directories, byte-checked an unaffected namespace,
   and exercised interrupted and idempotent retries. Off-account provider behavior and
   live authorization remain for the coordinated rehearsal.
