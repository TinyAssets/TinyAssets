# Role-split rollback runbook

Status: specified by lead technical decision D10; **not executable or tested yet**.
The launcher, maintenance client and reverse migration do not exist in this
checkout. Task 2.4 must supply the actual commands and task 2.8 must prove this
runbook on a disposable copy with the production and old images before use.
Do not substitute an unrestricted recursive chown/chmod command.

1. Record the current and target old-image digests and take a recoverable volume
   copy under the existing layout-lock/backup protocol. Retain the split-capable
   image until rollback verification completes. Do not start the old image yet.
2. Quiesce owner work and refuse new admissions before reverse migration; wait
   for engine children and their descendants to stop writing. Keep the verified
   daemon and launcher available to mediate maintenance. Hold the exclusive
   layout lock during the transition. A stopped or failed transition must not
   reopen admission against partially restored permissions.
3. Inventory the trusted owner-to-tree mapping. Through the verified daemon,
   request `chown-back` **dry-run** separately for each admitted owner's work
   trees. No caller-selected global `/data` traversal or general root exec.
   Report planned ownership/mode/ACL changes and unresolved aliases; dry-run
   changes no file or layout-marker metadata. Refuse foreign-owner paths,
   symlink escapes and unresolved/cross-owner hardlink aliases. Do not follow
   venv/npm symlinks or mutate their outside targets.
4. Resolve reported refusals without deleting user data. Apply `chown-back`
   through that same scoped channel, durably recording reverse migration in
   progress before mutations and completion only afterward. Restore files and
   directories, including engine 0600/0700 creations and later chmod, to uid
   1001 ownership and sufficient owner read/write/traverse permissions while
   preserving executable bits and file bytes. Keep vault and broker policies
   separate; never apply ta-work ACLs to credentials or broker state.
5. If interrupted, keep admission closed and resume the idempotent reverse
   migration. A completed repeat must report no changes. Record audited
   per-owner outcomes without credentials, file contents or owner tokens.
6. On the disposable acceptance copy, start the **actual old image**, uid 1001
   without work-group membership. Read and write the seeded engine-created
   restrictive files, then delete/reset disposable fixture trees through its
   actual APIs. Verify unrelated owners, symlink targets and retained user bytes
   are unchanged. A generic uid switch alone does not prove old-image rollback.
7. Only after successful reverse migration and the tested old-image procedure,
   stop the split image and start the selected old image on the prepared volume.
   Verify its healthcheck and normal owner operation before reopening admission.
   Production execution remains outside this build's authorization.

Required recorded evidence: image digests, exact commands, dry-run before/after
metadata comparison, repeat no-op, crash/resume marker evidence, alias/symlink
refusals, and old-image read/write/delete outcomes. Every item is currently
**NOT RUN**. The capability-lifetime decision in `delivery.md` must be settled
before these commands can be implemented and tested.
