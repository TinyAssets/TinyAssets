# Tasks: per-owner isolation, one clean cutover

Already on main: the L0 spec (#4540), the admission spec (#4541, now folded in here), the L1
reader guards (#4546), the L2 broker (#4554), and L3 consumers (#4556, in the merge queue).
The founder combined PR1?3 into one cutover PR. Nothing below runs in production
until that PR merges inside the maintenance window.

## 1. PR1: migration and runbook (inert)

- [x] 1.1 Fold U2's five migration modules into a forward-only `deploy/role_migrate.py`:
      `--check`, `--snapshot`, preflight, identities, egress, labels, and the marker last
      (design § 3). Drop everything design § 2 marks DROP.
- [x] 1.2 Bring the kept parts of `deploy/role_admission_contract.py`, the inert deploy modules
      (`role_owner_launcher`, `role_decoder`, `role_git`, `broker_main`) and the broker
      admission tables from U1/U2.
- [x] 1.3 Root oracle: kill at every step, then rerun, gives a byte-identical manifest; a
      converged rerun is a no-op; each precondition refuses.
- [x] 1.4 Rehearse on a restored copy of the latest production backup: `--check` 0 diffs after
      the apply, 0 cross-owner inodes, wall time recorded in the runbook.
- [x] 1.5 `docs/ops/owner-split-cutover-runbook.md` (design § 4). Snapshot restore round-trip
      keeps numeric owners and ACLs.

## 2. PR2: cutover (merged only inside the window)

- [x] 2.1 Image and startup: Dockerfile, `compose.yml` with the PID1 bootstrap, `ta_op.c`
      `MASK` (SYS_ADMIN out, migration capabilities never present), `role_launcher.py` with
      `role_startup` folded in, `backup.sh`, the `docker-build.yml` chain check, and the
      marker refusal at startup.
- [ ] 2.2 Every switch and dual path in design § 1 deleted. Every U1 cell and consumer landed
      with its broker/cell branch only.
- [ ] 2.3 Providers through provider-exec: Codex and Claude adapters, the engine-MCP thin proxy,
      workspace provision/registry/worker, discovery egress, and the K1 consumer. A class not
      celled is retired, never left unconfined.
- [ ] 2.4 Center creation through runtime admission. Two-pass deletion everywhere, including
      the subtree cell for pool removal and `scoped_reset`.
- [ ] 2.5 `role_image_oracle` on a PR1-migrated restored clone: every class, zero foreign bytes,
      signup → admit → cell without a restart, two-pass delete, zero capabilities, `ta-op
      pulse`, and a real Codex and Claude turn.
- [ ] 2.6 Window run (runbook): announce, stop, snapshot, migrate, merge, then verify
      `deployed_sha`, the canary, a founder app turn and the cross-owner refusal probe.

## 3. PR3: deletions (same day)

- [ ] 3.1 The deletions and concerns in design § 5; the remaining dead legacy tests.
- [ ] 3.2 Re-run the cross-owner refusal probe; `deployed_sha`; sync the specs and archive this
      change.

Proof, 2026-10-09: `role_migrate_probe.py` passed all 356 interruption points,
all preflight refusals, converged no-op, and numeric-owner/mode/ACL snapshot restore.
The production-shaped migrated-volume image oracle passed serving, admission,
provider CLIs, workspace/provision cells and deletion at `6f838d1761`; root role
tests passed 45/45. The real engine HTTP-to-tool-cell write/read and read-only extension mount now
pass on the diagnostic image; the final production rebuild and full-suite comparison
remain pending. Nested prepared-workspace regression tests pass 8/8 on Linux.
The production-backup rehearsal for 1.4 is recorded in the runbook (2026-10-08).
