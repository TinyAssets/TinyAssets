# Tasks: command-center-cutover

Prerequisite: C4a (#4234) is in production for at least one day.

**Hard prerequisite for anything that makes `config.yaml` agent-writable**
(lead, 2026-10-02). That includes the self-improving harness's config editing,
the sealed box, and a provider adapter that ships file tools under the
read-write default view. The authority split must land first: `allowed_providers`,
`engine_assignment_*` and `provider_authority_bindings` move to a platform-side
`assignment.json` through a resolver, readers read only from there, values
left in `config.yaml` are ignored and logged, and a test proves that writing
them there changes nothing. It ships as a standalone PR right after #4273,
outside the freeze window. Task 3 then only retargets its resolver to
`.platform/cc-<ulid>/`.

Today `config.yaml` is not agent-writable. The tool jail binds it read-only
(`universe_tools.py:118-137`), Claude provider turns have no file tools
(`providers/base.py:157`), Codex sees a read-only workspace
(`codex_provider.py:932`), and code nodes see no home.

- [ ] 1. `scripts/command_center_inventory.py` (read-only, E1), with fixture
      tests. It classifies every home entry by the E6 layout, and an entry it
      cannot classify fails the run. Attach a production-copy report.

      **R1 folded, 2026-10-02.** All five ADAPT must-fixes landed: classification
      by exact creator-backed names (unknown stays unknown and fails the run),
      scanning a private copy the tool makes itself, encoding coverage for CHECK
      bodies / generated columns / JSON values / LanceDB id rows, bounded
      traversal and value reads, and counts that say whether they count rows or
      cells. Two defects found reviewing that implementation were fixed with
      red-first tests: the worker-supervisor heartbeat family had lost its
      classification (which would have made the ~750 JSON files below
      unclassified), and requiring a database's `-shm` discarded every finding in
      it while a writer held the file. What the scan cannot see is reported as
      `deferred`, so `migration_ready` is false by construction until tasks 3/4
      cover derived identities. The remaining
      [acquisition/coverage contract](inventory-repair-contract.md) items --
      a producer-attested fence and checkpoint serde decoding -- are follow-up
      work; the observations below stay historical.

      **First result, 2026-10-02.** This was a names-and-schema pass
      (`--no-values`), read-only, against live production via
      `scripts/droplet.py`. The value scan runs on the dry-run copy (task 8).
      - Homes: 3. Unclassified entries: 3, all `.conversation_memory.db.bak-premigrate-*`
        database backups, now classified as platform state.
      - All 3 `config.yaml` files hold all four authority fields (E6 split).
      - SQLite: 22 files with findings, 27 `universe*` tables, 209 `universe*`
        columns, and 100 index / trigger / view / CHECK objects naming it.
      - JSON: 750 files with findings, mostly `.worker_supervisor.*.json`
        platform state.
      - LanceDB: scanned (schemas read).
- [ ] 2. First, commit old-layout fixtures built by the pre-cutover creators
      (26-character ids) with a creator-coverage manifest (E4). Then the codemod
      `scripts/rename_command_center.py`: identifiers, modules, env vars and the
      plugin id. Deterministic, with a non-mechanical report.
- [ ] 3. Migration phase 1 (names), including stored branch fields,
      custom-UI bundle bridge keys, and default-definition re-points. Each
      home entry moves to its target place (E6): user content in
      `cc-<ulid>/`, platform state in `.platform/cc-<ulid>/`, and an empty
      `.platform/accounts/`. One resolver builds every path, and mixed
      consumers (soul edit, self-model, config, dispatcher, vault lookup) get
      both roots explicitly. The authority fields move out of `config.yaml` into
      `.platform/cc-<ulid>/assignment.json`. Database families move journaled, and the run
      crash-resumes between every pair of moves (E6).
- [ ] 4. Migration phase 2 (ids, `u-` to `cc-`), including derived
      identities, folders and credential custody references (E4b).
- [ ] 5. Phase 3 verification: zero operational old names and ids, decoded
      round trips, digest integrity, independent deletion and export counts.
- [ ] 6. Phase 4 external: Stripe checkout drain, metadata and claim rewrite,
      late-event map, refusing an unknown home before storage. Recorded
      inverse.
- [ ] 7. Layout 2 in `tinyassets/storage_layout.py`, plus a
      `deploy_fail_safe.sh` that asks the image which layouts it knows.
- [ ] 8. Dry run on a consistent production copy. Attach the row-count and
      reader report, proof that every credential still resolves, and the
      measured duration that sets the cutover deploy's deadlines (E4).
- [ ] 9. Rollback runbook in `docs/ops/`, rehearsed on the dry-run copy.
- [ ] 10. Freeze window (E3.6). Evidence: canary `--assert-handles`,
      `deployed_sha.py`, and an inventory re-run on production showing zero.
- [ ] 11. Delete C1's edge translation, keeping the retired-name refusals.
      Land the ids-never-shown guard (E5, after notify-prompt's header fix).
- [ ] 12. Sync specs and archive both changes.
