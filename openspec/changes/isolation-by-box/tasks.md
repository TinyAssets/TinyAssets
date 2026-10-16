# Tasks: isolation-by-box (C6 deploy gate)

Floor (cross-user, storage, migration): one adversarial review by a separate
reviewer. Validation before every push uses CI's own selection,
`scripts/affected_tests.py --base origin/main --pr`, run in chunks through
`scripts/linux_oracle.py`. A skip is not a pass.

- [ ] 1. Founder confirms this design. The co-admin question (D3) may stay
      open: co-admin reads fail `not_owner` until it's ruled.
- [ ] 2. One PR, the C6 gate:
  - [ ] 2.1 `boxes/layout.platform_center_path`; every platform store and file
        in D2 resolves through it; `admit_center` creates the sidecar.
  - [ ] 2.2 Owner-facing reads bind as the requester (D3); delete the
        listing word counts, the cross-owner branch scan and every host-path
        owner read.
  - [ ] 2.3 Pause, heartbeats and the ledger in the sidecar (D4).
  - [ ] 2.4 `box_layout_migration`: the preflight classification, a box copy
        that excludes platform names, the sidecar rename with integrity and
        digest checks, host-center removal, and the marker (D5).
  - [ ] 2.5 The guard `tests/test_no_host_center_io.py` (D6); delete the
        replaced checks.
  - [ ] 2.6 Tests:
    - a pause is honored by a scheduler tick with no box;
    - a non-owner bind is refused and gets no fallback;
    - first contact on a fresh center sees the box soul;
    - after migration, the subscription tier, pending requests and the
      ledger read back;
    - an unknown host name refuses the move.
- [ ] 3. Synthetic and `--archive` rehearsals prove D5 (the lead runs the
      archive on the private copy).
- [ ] 4. Close #5168 and #5167 with a pointer here. C12 rebases on this
      change, and its host-retire step is deleted.
- [ ] 5. Deploy C6 (lead), assert the deployed sha, and make one naive-user
      pass through the app agent.
