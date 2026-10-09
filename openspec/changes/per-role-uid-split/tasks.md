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
- [x] 2.2 Every switch and dual path in design § 1 deleted. Every U1 cell and consumer landed
      with its broker/cell branch only.
- [x] 2.3 Providers through provider-exec: Codex and Claude adapters, the engine-MCP thin proxy,
      workspace provision/registry/worker, discovery egress, and the K1 consumer. A class not
      celled is retired, never left unconfined.
- [x] 2.4 Center creation through runtime admission. Two-pass deletion everywhere, including
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
The production-backup rehearsal for 1.4 is recorded in the runbook (2026-10-08).
The full production image built from `eb7604293a`,
`sha256:4102baa237574dfbb5db13276feb5368a3abb8e6a76a0ff67f21c5ebb370fddb`,
passed migration, actual serving, all eleven cell legs, and the shipped
Claude/Codex CLI execution and discovery probes. This includes engine HTTP tool
write/read, real dependency download/offline installation, workspace leases,
owner storage measurement, admission without restart, two-pass deletion, and
foreign-byte/capability refusals. Preview passed in this same production image:
a timed-out cell was reaped and its successor rendered the expected PNG.
Root role tests passed 45/45. The resumed full required-CI Linux selection passed
all six cutover shards: 27,064 passed, 96 skipped, zero failures; the aggregate gate
passed. The merge-base/main baseline `b5bc266a92` ran all six shards: 26,935 passed,
99 skipped, one failure in
`test_provider_served_router::test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`
(the fake Codex app-server exited). That test passed on the cutover. Comparing
test identities gives zero new failures and zero new skips. Slow tests passed
10/10 on both revisions. Heavy selection has identical failure messages for all
47 failures on both revisions, with 1,766 passes and zero skips each; both fail
the existing 2,000-test floor (1,813 selected). Neither floor nor quarantine was
relaxed. The previous structural proof passed 584/584; its final rerun is pending.

Task 2.5 remains incomplete: the current full image proof uses a freshly migrated
production-shaped fixture volume, not the restored production-backup clone, and
CLI startup/discovery does not prove a real credentialed model turn. Tasks 2.6
and 3.2 require the maintenance window, actual deployed SHA and founder app pass.
Production-Verify-conditioned concern deletions in 3.1 remain pending that window.

Resume audit, 2026-10-09: merged main through `b5bc266a92` in `46313de042`.
The credential broker and reader switches, legacy liveness, single-UID deletion,
and unconfined consumer fallbacks are absent. Design section 5's code deletions
and their legacy tests are already applied; its four concern deletions remain
conditioned on production Verify. The remaining `ENV_SWITCH` in served_chat is
the unrelated agent-loop selector. The resumed image run completed migration,
actual serving, all eleven cell legs and the provider probes with
`ROLE IMAGE ORACLE PASS`; it does not close the stricter task 2.5.
