# Tasks: dynamic owner admission

These tasks are activation lane D in the per-role-uid-split landing plan. They
land after slices L2 (broker package) and L5 (`role_owner_launcher.py`), with
the switch OFF throughout. Task 1 comes first, because a failed kernel fact
stops DA3. The `deploy/` tasks need the `infra-change` label. Every proof runs
through `scripts/linux_oracle.py`, and a skip is not a pass.

- [ ] 1. Measure DA3's four kernel facts in the Linux oracle, on a Docker
  ext4 named volume and on tmpfs: setgid inheritance into a daemon `mkdir`
  under an owner-owned 02777 directory; the exact inherited access ACL; the
  final chmod clearing S_ISGID while keeping the named entries; and the
  cross-parent rename and removals without capability. Record the results
  under DA3. Any failure is an acceptance stop.
- [ ] 2. Broker: the `center_admissions` table and the OWNER-channel
  `CENTER_ADMISSION` op (DA1), with tests for idempotent retry, every refusal,
  concurrent writers, and a missing map.
- [ ] 3. Broker and bootstrap: the mapper's inherited read-only channel with
  `OWNER_MACHINE`, `CENTER_STATE` and `ADMISSION_ROW` (DA2). Create the pair
  before the broker fork, deliver the mapper PID with the proof hash, and
  check per-packet SCM_CREDENTIALS for that PID and host 300000. Test that an
  unknown op, an extra field, or a non-mapper sender refuses.
- [ ] 4. Mapper: the fixed `center-root` cell class, plus the daemon helper
  that shapes S, checks `g`, creates and checks the root, publishes it and
  cleans up (DA3). A root-oracle test asserts the full `_permissions` tuple,
  the explicit owners of daemon-created entries, and zero capabilities.
- [ ] 5. Mapper: the runtime `admit`/`retire` requests and the bootstrap
  `(bindings, generation)` (DA4, DA5). The bind uses the attached root
  descriptor. Refusal tests cover:
  - an absent or mismatched row, or a stale generation;
  - a rebound machine, or a retired center;
  - a wrong root group or inode, or a numeric field;
  - a bound retire without a fence or with a running cell.

  An unbound retire succeeds as a no-op.
- [ ] 6. Daemon: inventory every site that creates or removes a canonical
  center root, including the `api/universe.py` rollback and the
  `api/first_contact.py` retry. Route creation through DA4 when the bounded
  client is installed and make a direct `mkdir` refuse there. Turn
  post-publish failure into an in-place retry or a D218 deletion, done
  before any grant is revoked. Center creation in the app waits for the bind
  and fails loudly. The legacy path must be byte-for-byte unchanged (DA8).
- [ ] 7. Deletion: append `retire` and send the mapper `retire` in
  `role_owner_tree_deletion` before `finish`, on both the normal path and
  the tree-gone resume path (DA6). Add resume tests at each new step.
- [ ] 8. Restart contract (DA7), covering `role_volume_migration`,
  `role_volume_inventory`, `role_metadata_migration` and
  `role_owner_migration`:
  - remove `.role-admission/` before the inventory;
  - store `generation` and `missing` in `volume.json`;
  - reconcile the log delta in all three journals;
  - treat pending deletions as explained;
  - skip the phases for an empty set;
  - adopt orphan roots and seed rows (when there is no journal or a stable
    reverse journal) through a retired broker child.

  Coordinate the seeding with lane A's first-volume initialization.
- [ ] 9. Root-oracle matrix with real broker IPC and a real mapper:
  - signup, new center and deletion, each followed by a restart;
  - a crash at each DA4 boundary, and between the deletion's daemon pass
    and its `retire`, each followed by a restart;
  - a post-publish create failure, both retried and abandoned;
  - deleting the volume's only center;
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
