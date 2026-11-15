# r-restore-chunks as built (2026-11-15)

A restore leaves unnamed the chunks the owner put after E. They have no row in the restored object, so nothing collects them (`r7-sweep.md` item 10). This PR sweeps them. The design is item 1 of the lane's research (`r_next_research.md`, adopted), except for the staging table in item 2 below.

1. **Two restore steps while the restore holds the owner,** `names` then `sweep`, between `chunks` and `clear` (`owner/src/restore.ts`; `NEXT` is `chunks→names→sweep→clear`).
   - **Why under the hold.** `putChunks` on a key that already exists only verifies it. So a sweep outside the hold could delete an orphan just as a new upload of the same bytes makes a row for it. From the pin to `done` no door but the restore's is served, so no row can be made.
   - **The keep rule is the files plane's own:** every object in the bucket has a row first (`files.ts`). `names` takes every `file_chunks` row, in any state: an upload's held row, a garbage row in its grace that may revive, one a kept generation names. The sweep never reads which generations are kept, so a later change to `#retained` reaches it only through rows.
   - **The admission check comes before any delete.** `finish` used to throw on an admission at E that was not `admitted` or `suspended`, after the deletes. Each `names` piece now reads the admission from the record (`held.held`) and fails the restore first, naming it, with nothing deleted. `finish` no longer checks. The pin's own check uses the same list (`ADMITS`).
   - **`at` stays the chunk count.** `names` and `sweep` move `after` only. The answer's shape is unchanged; its step type gains `names` and `sweep`.
2. **A Bloom filter, not a staged table (the coordinator's decision, 2026-11-15).**
   - **Why not a staged table.** The research staged one row per name (`restore_names`). Counted by the owner-bound guard (1,211,898 chunk rows at most under `INDEX_BYTES`, each name 32 bytes plus `RECORD_FIXED`, at `WORST_FILL`), it came to 308.1 MB. The worst case went from 4,493,114,390 to 4,801,239,457 bytes, 96% of the half. The guard that keeps STORE's quarter of the room under half failed (`limits-audit.test.ts`: 362.7 MB left where 498.8 MB is held). The research had checked against the 10 GB, but `limits-audit.md` rules that the half is the ceiling.
   - **What the filter costs.** One row of 1,452,015 bytes, measured. The worst case is 4,494,570,004.75 bytes (44.9%), 1.46 MB over main. The room test and the shares are untouched.
   - **The accepted trade-off.** A Bloom filter has no false negative, so no chunk a row names is ever deleted. A false positive keeps an unnamed chunk until the owner's erasure. That is a bounded leak: at most about 1% of the unnamed chunks, only after a restore, and only at the index's worst case. Fewer rows fill the filter less, and then the share is lower.
3. **The filter** (`owner/src/names-filter.ts`).
   - **Sizing.** It is sized once for `CHUNK_ROWS_MAX`, the most chunk rows the file index holds: `INDEX_BYTES` over one row's charge (`CHUNK_ROW_CHARGE`, 443 bytes). The charge is computed from the same `CHUNK_RECORDS` that the schema's charge SQL (`CHUNK_ROW_BYTES`) is built from, so the two cannot drift. At `FILTER_FALSE` (1%) that gives `FILTER_BITS` (11,616,120), `FILTER_BYTES` (1,452,015) and `FILTER_HASHES` (7).
   - **The bit indices.** A name is an HMAC, so its bytes are uniformly random. Each of the 7 bit indices is one 32-bit slice of the name, taken modulo the bits, so no second hash is needed. Seven slices use 224 of the name's 256 bits.
   - **Where the sizes live.** They are functions, as `CHUNK_ROW_BYTES` is, because the module-state rule refuses a call at load. The guard term reads `FILTER_BYTES()`.
   - **`INDEX_BYTES` moved to `manifest.ts`**, the leaf module of the index's charge. That lets the filter derive from it with no import cycle, and lets the node guard import it. `files.ts`, which imports `restore.ts`, imports it from there.
   - **Storage.** The filter is one row, `restore_filter`, under SQLite's 2 MB value, so no split. A stored filter of any other size fails loudly: a short one would read named chunks as absent. The filter is kept out of the export (`NOT_EXPORTED`). The wipe drops and remakes it with every other table, and `clear` deletes it.
   - **Measured.** In `owner/guards/names-filter.test.ts`, the filter takes `CHUNK_ROWS_MAX` names and reads every one of them as held (a large test, run by the sizes leg: at 1.2 million names it takes about 5 s on the review host, past the owner guards' 9 s budget on CI's slower runners). Of 100,000 names it never took, 976 read as held (0.98%). Its size is within a byte of the derived least. In workerd, the stored row's length equals `FILTER_BYTES()`, and the schema's charge SQL equals `CHUNK_ROW_CHARGE()`.
4. **Pages and bounds** (the measured-limits rule).
   - **`names`:** `NAMES_ROWS` (10,000) rows a unit, by hash, after `after`.
     - Each unit makes ten `chunkNames` calls of `DELETE_BATCH` (1,000). These are HMACs only, with no R2 call.
     - One unit rewrites the filter row, so the 1.45 MB row is written at most 122 times for `CHUNK_ROWS_MAX`, against 1,212 times at 1,000 a unit.
     - A unit holds the filter, its rows' hashes and their names: under 4 MB of the isolate's 128 MB, and one 1.45 MB value against SQLite's 2 MB.
     - The first page begins the filter and each later page adds to the bits so far, in the same unit that moves `after`. A crash resumes from the cursor with the bits it had, and setting a bit twice changes nothing.
   - **`sweep`:** one page of `DELETE_BATCH` (1,000) keys a piece, R2's most for one list and for one batch delete.
     - Each piece makes one list (`startAfter` the last name, not an opaque cursor) and at most one delete, with one unit before the delete and one after.
     - A lost delete answer lists the same page again, finds those names gone, and moves on, so no name is asked to be deleted twice.
   - **What bounds a whole sweep's time.**
     - Its piece count: ⌈rows / 10,000⌉ for `names` (at most 122), plus ⌈listed / 1,000⌉ for `sweep`. Listed is the namespace's chunk objects: the rows' own, plus those put after E.
     - Each piece is a bounded unit inside one `restore.step` call's `RESTORE_PASS_MS`. The kit asks again until `done`, and the record resumes a run cut off anywhere.
     - The `chunks` step before it reads every chunk back, 64 a unit, and dominates the restore's time. No watchdog or budget comes near.
   - **`clear`** deletes the staged rows, `SLICE_ROWS` a unit, then the filter, then finishes. It reads the generation's root once, at the end, where it used to read it at every slice.
5. **The files Worker's RPCs** (`files/src/index.ts`, `contract/src/files.ts` `ChunkStore`).
   - `chunkNames(ns, key, sha256s)` answers the names of up to 1,000 hashes under the owner's key, with no R2 call.
   - `listChunks(ns, after)` lists one page of `chunkPrefix(ns)` after a name.
   - `deleteListed(ns, names)` deletes up to 1,000 of those names, under no key, as `deleteNamespace` takes none.
   - `deleteChunks`, `chunkNames` and `deleteListed` share one list check (`distinct`), and `deleteChunks` and `chunkNames` share one naming (`#names`), so no copy of either is made. The header's "only lists of the bucket" now counts three.
   - The owner object reaches `listChunks` and `deleteListed` through `FILES` directly, as the restore's manifest reads do: the restore holds the namespace in its record. Only `chunkNames` needs the key, so `Files.names` is the one pass-through, where the research had three (they would have been surplus).
6. **Migration 14** remakes `owner_restore` with `names` and `sweep` in its step check, keeping its row (proved: a record mid-`clear` of migration 13 is mapped unchanged), and makes `restore_filter`.
   - Repeating the record's two generation checks moves the frozen-bounds guard's count of 16 from 7 to 9.
   - Its pin is a PR of its own after the merge (`untangle/r-restore-chunks-pin14`).
7. **Proof** (`owner/test/restore.test.ts`, "a restore's sweep of the chunks no row names"; `files/test/chunks.test.ts`; `owner/guards/names-filter.test.ts`).
   - **A live owner pinned, wiped and restored at E.** At `done`, the chunk of a file written after E is gone. The chunks of these remain: a file live at E, an upload held at E whose chunk was put after it, and a file an open export reads, deleted and purged before E (a garbage row). Exactly one name was deleted, the filter is gone, and the deleted file revives through an upload that sends nothing.
   - The admission check fails the restore at `names`, with every files call so far a get or a check.
   - **`names` across a page boundary (10,500 rows), crashed between its pages,** resumes from its cursor and sets the bits a fresh run sets.
   - **A crash between `names` and `sweep`, and a lost delete answer mid-sweep, across a page boundary of 1,200 unnamed chunks,** end with each of them deleted once and every named chunk stored.
   - **The files Worker:**
     - `chunkNames` answers the names `putChunks` stored under, with no bucket call, and refuses a malformed list;
     - `listChunks` pages by name across more than 1,000 keys and never lists another namespace;
     - `deleteListed` deletes only the names it is given and refuses a bad list first.
   - **One mutation per declared change, each failing its test:**
     - the rows taken only when live;
     - the admission check dropped;
     - every `names` page begun from an empty filter;
     - the sweep's cursor not moved;
     - the filter not deleted at `clear`;
     - migration 14 dropping `fresh`;
     - `restore_filter` missing from `NOT_EXPORTED`;
     - `chunkNames` without its list check;
     - `listChunks` without `startAfter`;
     - the filter sized at half;
     - the guard term missing.
8. **Left.**
   - A doors upload already in flight at the pin can still land on `FilesUpload` after its name was listed. It then stays until erasure: a leak, never a loss (research §1.2), and it needs no fix of its own.
   - **A delete that lands late (review).** R2's delete takes no condition, and an issued `deleteListed` cannot be called back. Two `restoreStep` calls at once can both pass a `sweep` page's unit and both delete its names, and a delete whose answer was lost may still be landing when the next piece lists again. Under the hold that is harmless: every such name was unnamed. If one outlives the rest of the restore, it lands after `done`, and would take a chunk only if a writer made a row for those very bytes in between. Every session, API key and outside client is revoked at `done`, so that writer must first sign in again or be a run the lanes start afresh. It is left, with no fix owed. A wait at `clear` for this instance's sweep deletes still in flight would close the two-calls case; a lost answer cannot be closed under R2's unconditional delete.
