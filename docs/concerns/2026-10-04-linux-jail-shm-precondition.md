# Linux tool-jail sidecar test fails its precondition on main

Observed while verifying quota write headroom against main
`d4a3a422730cd8f9e944f33b3c93debb25e1e2e7`.

`tests/test_universe_tools_jail.py::test_a_background_run_reads_and_writes_its_notes_while_a_database_closes`
fails at line 834: `.effector_consents.db-shm` does not exist immediately after
opening the database and selecting its rows. It fails before the intended
close-between-scan-and-launch race can be exercised.

Linux oracle: Python 3.11.16, git 2.47.3, bwrap 0.12.0, unprivileged uid 1001.
Focused storage + tool/provider jail run: 130 passed, 1 failed, zero skips.
Single-case baseline with `storage_accounting.py` and `jail_disk.py` from exact
main/HEAD substituted inside the same oracle image: 1 failed identically.
The test and consent implementation were unchanged from main in both runs.

Hand off to the tool-jail/SQLite test owner: establish why the expected WAL
sidecar is absent on this oracle image, then restore a meaningful race proof.
Do not count this test as passing or weaken its precondition in the headroom
lane. No fix attempted here. Full command/evidence context is in
`docs/audits/2026-10-04-quota-write-headroom.md`.
