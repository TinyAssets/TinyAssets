# Tasks: dynamic owner admission

These tasks are activation lane D in the per-role-uid-split landing plan. They
land after slices L2 (broker package) and L5 (`role_owner_launcher.py`), with
the switch OFF throughout. Task 1 comes first, because a failed kernel fact
stops DA3. The `deploy/` tasks need the `infra-change` label. Every proof runs
through `scripts/linux_oracle.py`, and a skip is not a pass.

- [ ] 1. Measure DA3's four kernel facts in the Linux oracle, on a Docker
  ext4 named volume and on tmpfs: setgid inheritance into a daemon `mkdir`
  under an owner-owned 02777 directory; the exact inherited access ACL;
  S_ISGID surviving removal of the default ACL; and rename and removal
  without capability. Record the results under DA3. Any failure is an
  acceptance stop.
- [ ] 2. Broker: the `center_admissions` table and the OWNER-channel
  `CENTER_ADMISSION` op (DA1), with tests for idempotent retry, every refusal,
  concurrent writers, and a missing map.
- [ ] 3. Broker and bootstrap: the mapper's inherited read-only channel with
  `OWNER_MACHINE` and `ADMISSION_ROW` (DA2), per-packet SCM_CREDENTIALS for
  the mapper PID and host 300000, and wiring in the D70 window. Test that an
  unknown op, an extra field, or a non-mapper sender refuses.
- [ ] 4. Mapper: the fixed `center-root` cell class, plus the daemon helper
  that shapes S, checks `g`, creates and checks the root, publishes it and
  cleans up (DA3). A root-oracle test asserts the full `_label` tuple and
  zero capabilities.
- [ ] 5. Mapper: the runtime `admit`/`retire` requests and the bootstrap
  `(bindings, generation)` (DA4, DA5). Refusal tests cover an absent or
  mismatched row, a stale generation, a rebound machine, a wrong root group,
  a non-1001 host UID, a numeric field, and retire without a fence or with a
  running cell.
- [ ] 6. Daemon: inventory every site that creates a canonical center root.
  Route each through DA4 when the bounded client is installed, and make a
  direct `mkdir` refuse there. Center creation in the app waits for the bind
  and fails loudly. The legacy path must be byte-for-byte unchanged (DA8).
- [ ] 7. Deletion: append `retire` and send the mapper `retire` in
  `role_owner_tree_deletion` before `finish` (DA6), with resume tests at each
  new step.
- [ ] 8. Restart contract (DA7), covering `role_volume_migration`,
  `role_volume_inventory` and the owner-phase journal:
  - store `generation` in `volume.json`;
  - reconcile the log delta;
  - adopt orphan roots;
  - seed rows when there is no journal or a stable reverse journal;
  - remove `.role-admission/` at startup.

  Coordinate the seeding with lane A's first-volume initialization.
- [ ] 9. Root-oracle matrix with real broker IPC and a real mapper:
  - signup, new center and deletion, each followed by a restart;
  - a crash at each DA4 boundary, followed by a restart;
  - reverse after runtime admission;
  - forward after the legacy image created centers;
  - an unexplained tree, and a changed owner, each refusing.
- [ ] 10. Implement F1 as the founder answers it (`docs/host-actions.md`).
  Until then the default (a) stands, and task 9 asserts it.
- [ ] 11. Production-image probe: runtime Alice and Bob admission through the
  application path, then real decoder and tool cells. The probe must show zero
  foreign bytes on the D59 relabel/copy matrix and read back zero capability
  sets in every process.
- [ ] 12. When per-role-uid-split's L0 lands, fold these requirements into
  `runtime-process-roles` and renumber DA1–DA8 as D-records there. Update the
  landing plan's lane D and D216/D218 to point here.
