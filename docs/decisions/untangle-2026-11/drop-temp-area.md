# drop-temp-area as built (2026-11-22)

`untangle/drop-temp-area`, owed by `fact15-defaults.md`: the owner's temporary area (`/.ta/tmp`,
`file_temps`, `tempDir`, `dropTemps`, `TEMP_TTL_S`) had no product caller, so it goes, its
retention with it.

## Research first (Fact 14), read on main b7a8e26f8

- **Writers.** Only `Files.tempDir` made a temporary directory (a `file_temps` row and its
  `/.ta/tmp/<prefix><16 hex>` entry), and only `Files.dropTemps` and housekeeping
  (`Files.run`, past `TEMP_TTL_S`) removed one. No door, tool, lane, Worker or app calls
  `tempDir` or `dropTemps`: their only callers were owner tests (`files-quota`, `files-share`,
  `files-tree`, `files-upload`). No sync op could write there (`syncCommit` takes workspace
  paths only), and the importer writes `/workspace/imported` alone.
- **Readers.** `files.ts`: `#record` skipped history under `/.ta/tmp`, `#temporary` refused
  any entry there outside a temporary directory (`#commit`, `#mkdir`, `rename`), and `next`
  read the expiry. The contract's `paths.ts` made `/.ta/tmp` the second root (`TEMP`,
  `ROOTS`), which `storedPath`, `isRoot` and `ancestors` read. `manifest.ts` charged a row
  (`TEMP_BYTES`, `MANIFEST_TABLES`, `MANIFEST_INDEXES`); the export (`export.ts`), the restore's
  load order (`restore.ts`), the frozen-bounds and owner-bound guards, and the tests' recount
  (`test/support.ts`) each named the table. Erasure, backups, the doors, the sandbox, the app,
  the recorders and the cutover manifest name none of it.
- **The agent's scratch** (`$TA_SCRATCH`, `sandbox/runner/scratch.ts`) is the sandbox's own
  disk, never the owner's files, and reads nothing here. Nothing depends on the area.

## What landed

1. **Migration 24 drops the area.** It deletes any entry under `/.ta/tmp` and every
   `file_temps` row first, so their triggers take back the manifest and share charges (a
   dropped table's rows fire no trigger), then drops the table with its index and triggers.
   No live object can hold such a row (no caller), but the SQL does not rely on that. Pinned
   (`node ci/kit/locks.ts pin owner HEAD`); the export's shape is pinned again at schema 24.
2. **No generation from before 24 is brought forward (`FORWARD`, `TEMPS_DROPPED`).**
   `file_temps` was exported, and the decided forward form (`r-restore-schema.md`: "drop a
   dropped table's line") is built only with its frozen fixture, by the first migration that
   needs it. Migration 23 already refused every older generation, and before the cutover no
   owner keeps one, so this strands nothing.
3. **`/workspace` is the one root.** `TEMP` and `ROOTS` go from `contract/src/paths.ts`:
   `isRoot` is the workspace, `storedPath` refuses anything outside it ("a path is under
   /workspace"), and `ancestors` stops at it. Two checks that tested for the second root are now
   plain: `syncCommit`'s and the starter package's `within(path, WORKSPACE)`, which every
   stored path passes.
4. **`files.ts`** loses `TEMP_TTL_S`, `tempDir`, `dropTemps`, `#dropTemp`, `#temporary` and its
   three calls, `#record`'s skip, and housekeeping's expiry and its due time.
5. **Docs.** `limits-audit.md` loses the `tempDir` row and the `TEMP_TTL_S` retention item, and
   its summary is recounted by its rule (214 rows; the summary said 210 of a count that was
   215 before this change). The owner-object spec drops the area's requirement text and
   scenario wording. `fact15-defaults.md`'s owed line is ticked.

## Tests

- Removed, as they only exercised the area: `files-tree` "a rename across the roots" (three
  tests) and "leaves the temporary area out of the feed". A rename's content kept through
  collection is `files-upload`'s garbage-collection test, which now moves files between
  workspace folders.
- Rewritten without it: `files-quota` "never wedges housekeeping" (its 600 temporary entries
  gone; the pass still expires an intent, prunes and collects at every bound), `files-share`'s
  recount and random-ops tests (the `temp` and `drop` ops gone), `files-upload`'s garbage
  collection; the refusal lists in `files.test`, `files-tree`, `files-lease` and `import.test`
  drop their `/.ta` cases.
- Added: `files-quota` "the temporary area's removal (owner migration 24)": an object at schema
  23 holding a temporary directory and an entry in it, migrated, holds neither, no `file_temps`
  object, and every counter equals a recount. The migration-8 test shares its helper.

## Untangle check

- **Fixed:** the dead area (surplus) and the special cases it carried: `#record`'s skip of
  history, `#temporary`'s refusal, housekeeping's second expiry, and the two-root checks.
- **Left:** the baseline's comments and SQL that name temporary directories stay, as the
  baseline is frozen text that still makes the table before migration 24 drops it.
- **Reintroduction:** a table brought back needs a new migration, which the owner-bound and
  limits-audit guards' "covers every table", the lock pin and the export shape already fail
  (mutation run). A constant like `TEMP_TTL_S` brought back with no caller has no natural guard
  here: holding every cap and retention in source to `limits-audit.md` is
  `untangle/limits-guard`'s, owed in `fact15-defaults.md`.
