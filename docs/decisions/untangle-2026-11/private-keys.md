# private-keys (2026-11-23): each writer's private rows have ids of their own

`untangle/private-keys`, left by `row-stamps.md` ("Left", "Key existence"). After #5822 no
answer carries a count of changes, but one key space per collection held every writer's rows,
so a visitor in a shared room could probe an id it can guess: its write of an id that is the
host's private row was refused "a private row is its writer's own", which told it the row
exists, and when it is made or deleted (the host the same of a visitor's). The coordinator's
rule: nothing a reader gets may reveal the existence, count or timing of rows it can't see.
The coordinator's decision: each writer gets a private key space of its own; a public claim
still conflicts for everyone.

## Research (Fact 14), from the code and the spec

### Keys, privacy and conflicts today

- `collection_rows` is keyed `(collection, row_id)`; a row holds `private` and its `writer`
  (the caller: the owner's agents, outside clients and runs act as the owner's caller; a
  visiting owner as its own). `history` (`collection_history`) is `UNIQUE (collection, key,
  revision)`; a deletion (`collection_erasures`) keeps `private_writer`, the erased row's writer
  when it was private.
- A write reads the row at the key whoever wrote it. Another writer's private row there was
  refused `refused` ("a private row is its writer's own"), a privacy other than the row's
  `invalid` ("a row keeps the privacy it was created with"), and an etag other than the row's a
  conflict carrying the row. Only the first leaks; the second reached only rows the writer
  could see. Every read (`pull`, `changed`, `get`, `history`, `delete`'s match) filtered
  `private = 0 OR writer = caller`.
- The doors: a write names `private`; `collection_get`, `collection_history` and
  `collection_delete` name only the id, so a reader with no private row of its own could not
  tell the two apart, nor needed to.

### The spec

- `owner-object/spec.md`: "a private row its writer's own" and "a lost swap answered with the
  row as it stands". The board's claim (the skills `rooms-and-board`, `shared-brain`, the
  write tool's text): create `<kind>:<key>` with `expected_etag: ""` and `private: false`, "a
  conflict means it is taken". So the claim semantics are the public rows' alone: a private row
  is read by no other agent, so it can claim nothing.
- The digests: "A row keeps the privacy it was made with, so it turns private only by a delete,
  which is reported", and a public row's delete is kept while the key is made again private.
  Both exist only because one key held either privacy.

### A public write at an id another holds privately

Either the two coexist, or the write must answer as if the private row were not there. The
second is no shape: a commit would replace a row the writer may not touch, and any refusal,
conflict or slower answer tells of it. So they coexist: the private row's key includes its
writer, the public rows' key does not, and a public id is one for everyone.

### Migration, export and restore

- Every stored key is unique today, so `space = private ? writer : ''` maps each row lossless;
  each history entry takes its row's (every entry is a live row's: a delete or clear drops a
  row's entries, the prune only the oldest).
- SQLite changes no key in place: both tables are rebuilt, as migration 7 rebuilt the history
  (renamed with their taking-back triggers, made anew with their charge, placement and inline
  triggers, copied, the old deleted), so every counter and content reference goes up before it
  comes down. The charges gain the space's bytes in each record and its key's index entry.
- Both tables are exported, and a `gone` line names a row by its key alone, which says nothing
  of the space it was in; so no generation from before migration 31 is brought forward,
  declared before the cutover, when no owner keeps a generation (as 23 to 26 were).

## The shape

A row's key is `(collection, space, row_id)`: `space` is '' for the public rows and the writer
for its private ones, and `private` is whether it has one (the column goes). A caller names a
row by its id and `private`, so it names only rows it may see, its own or the public ones:

- `write` reads and upserts at the caller's key; the two refusals go.
- `get`, `history` and `delete` take `private` (the doors' optional field, false when left
  out); a history's cursor order names the space.
- `pull` and the digests read `space IN ('', caller)`; a digest's deletion says `private`, so a
  reader tells its own private row's deletion from the public row's of that id.
- The owner sees every row it saw before: every public row and its own private ones; its clear
  reaches every writer's, as before, and counts only the rows it could see (review).

The contract adds an optional field on three doors, which the owner, deploying first, takes
whatever the doors' release before hands: no expand-then-contract step (`handed.test.ts`).

## Problems found (untangle step 2), with the fix above each

- **A row's key is its space and its id, so a caller names only rows it may see.**
  - `write` refused another writer's private row, and a privacy other than the row's: two
    special cases for one key holding rows of different visibility.
  - `pull`, `changedSql`, `#visible` and `delete` each spelled the visibility test
    `private = 0 OR writer = ?`.
  - `import.ts` `rowThere` compared the held row's privacy with the import's, since one key
    could hold either.
  - `rowsPage` ordered by `(collection, row_id)`, no longer a total order.
  - (Review) The owner's clear answered `deleted` with every row it erased, so it told the host
    how many private rows a visitor held in a room or the board: `#erase` counts only the rows
    its caller could see (a delete's row is always the caller's), and the leak test's host
    clears both.
- **One fact, one column.** `private` beside the space it is derived from goes.
- **No test-only default.** `history`'s `before = null` was used only by tests; every caller
  passes it.
- **Comments and spec.** The module's head ("a row keeps the privacy it was made with"), the
  write tool's `private` ("only the owner may read the row": its writer), its text on claims
  (a public row), the spec's primitive, the digests' privacy sentence and its scenario.
- **Tests.** The private-row test held the refusal; it holds the spaces now. The leak test's
  host deleted its private rows with no `private`, which after this change would conflict
  unseen: it now asserts each delete. Tests reading the `private` column read the space.

## Left, with reasons

- **`collection_erasures` keeps `private_writer`** (NULL for a public row) where the rows and
  entries keep `space` ('' for one): two spellings of one fact in two tables, read in one
  place (`changedSql`). Renaming it is a third rebuild, of ten triggers, for no behaviour.
- **A private row's `writer` is its space** (a `CHECK` holds them equal): the writer is the
  column every row shows, the space the key.
- **The migration's time at fill.** It copies every row and entry in one transaction, as
  migration 7 did, unmeasured at `maxRowsCharge`; no owner holds more than a stage's rows
  before the cutover, which migrates every owner after a backup.
- **Bounds and resets**, as `row-stamps.md` left them: a write refused at the owner's bound
  counts everyone's rows, and a reset tells of a prune.

## As built

- `owner/src/schema.ts` migration 31: both tables rebuilt keyed by the space; pinned in
  `owner/migrations.json`; `FORWARD` 31 `PRIVATE_SPACES`.
- `owner/src/collections.ts`: `spaceOf`; `write`, `get`, `history`, `delete` at the caller's
  key; `pull`, `changedSql`, `touched` read the space; a deletion's `private`.
- `owner/src/import.ts`, `owner/src/export.ts`: the row's key with its space.
- `contract/doors.json`: `private` optional on `collectionGet`, `collectionHistory`,
  `collectionDelete`; the write's texts; generated files.
- The spec: the primitive's requirement, a scenario (a claim is one for everyone), the leak
  scenario at the reader's own ids, the digests' deletion.
- Tests: a visitor's answers by every door are the same whether or not the host holds and
  deletes private rows at the very ids it writes and claims; a public claim conflicts for
  everyone, the owner among them, beside a private row of its id; the owner reads every
  public row and its own; the migration keeps every row, entry, counter and content
  reference, and fails loudly on an entry with no row; a private row's charge at the widest
  names and (review) at the shortest, where every record is in place and the charge has no
  overflow page's slack, so a term it leaves out (its key's index entry without the space)
  fails it.

## Owed

- [ ] live: after the next owner deploy, the stage's owner object is at schema 31, its board
  reads as before from the start, and a private row is read by `collection_get` with
  `private: true`.
