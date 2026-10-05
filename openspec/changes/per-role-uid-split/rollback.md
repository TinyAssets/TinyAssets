# Role-split rollback runbook

Status: specified, not yet executable or production-image proven. Task 2.4 must
record the actual env/flag invocation and task 2.8 must prove every step on a
disposable copy. Production execution is outside this build authorization.

1. Record split and old-image digests and take a recoverable volume copy under
   the layout-lock/backup protocol. Retain the split image until verification.
2. Quiesce admissions, stop the split container and all engine descendants, and
   ensure no other container writes the volume. Do not start the old image yet.
3. Start the split image in explicit opt-in **startup reverse-migration dry-run**
   mode. This uses the forward migration entry path before capability drop, with
   the exclusive layout lock and no daemon, broker or engine running. It reports
   intended ownership/mode/ACL changes without modifying data or layout markers.
   Inventory the trusted owner-to-tree mapping; no caller-selected global root
   exec. Retain no-follow traversal and hardlink alias validation, skip workspace
   symlinks without changing their targets, and refuse unresolved aliases.
4. Resolve refusals without deleting user data. Start the same image with the
   explicit startup rollback opt-in in apply mode. Durably record reverse
   migration in progress before mutation and completion afterward. Restore
   engine-created content, including 0600/0700 and later chmod, to uid 1001
   ownership and owner read/write/traverse access, retaining executable bits
   and bytes. D11 also reverse-migrates broker-owned outbound ledger/sidecars
   and proxy state to old-image ownership, and restores the old ledger location
   if forward migration relocates it. Prove an actual SQLite write with journal
   creation, not just file open. Preserve every retained row and proxy artifact.
   Keep credential and broker-state policies separate from ta-work.
5. The rollback startup exits before any role starts or forward migration can
   run. If interrupted, repeat that explicit opt-in startup to resume under the
   lock. A completed repeat makes no changes. Do not reopen admission against a
   partial transition. No service-time root helper or retained DAC/FOWNER/CHOWN
   capability participates in this procedure.
6. On the acceptance copy, start the **actual old image** as uid 1001 without
   supplementary work groups. Read/write the restrictive engine-created fixture
   files, then delete/reset disposable fixture trees through its actual APIs.
   Verify unrelated owners, outside symlink targets and retained bytes unchanged.
7. Only after successful reverse migration and copy verification, start the
   selected old image on the prepared volume, check health and normal owner
   operation, then reopen admissions. This step is not authorized for production
   in the present build.

Record image digests, exact commands, dry-run before/after metadata, repeat no-op,
crash/resume markers, alias/symlink refusal and old-image read/write/delete. All
acceptance results are currently **NOT RUN**. The previous capability-lifetime
conflict is resolved by the lead's startup-only reverse-migration decision.

## Implemented substep, not an operational rollback command

D12's egress move is available as `relocate(root, reverse=True, dry_run=True)`
in the immutable `ta-egress-migration.py` startup library. Apply sets dry_run=False.
It requires the layout lock and completed consent migration; it leaves the
whole layout migrating. Do not manually clear that marker or start an old image:
workspace/ACL reversal and the complete startup command are not implemented yet.
The production-image oracle exercises this substep on synthetic disposable copies,
including old-location writes as uid 1001 without work groups. That is not an
actual old-image or full rollback acceptance result.
