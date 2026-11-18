# F1 index census research as built (2026-11-18)

The census owes a refusal for an owner whose import would exceed the file index
(#5548; clean-sheet tasks, step 5). Counting paths or the final file sizes cannot
tell us that. This branch records a tested design. The census refusal remains open.

## What the two runtimes agreed on

Using main f934871ad, a synthetic pending owner was run through the real Worker
import doors in workerd. An offline Node SQLite database ran the same 18 owner
migrations, installing 283 triggers. It ran the owner's Files, Bodies, Collections
and Starter implementations and the chunk and row import handlers. Node 22.23.5
(the import host's major) and 24.22.3 agreed with workerd at every comparison.

| Operation | File index bytes |
|---|---:|
| Fresh owner | 480 |
| Create /workspace/imported/é with parents | 1,892 |
| Write one byte at its child a | 2,945 |
| Create sibling folder eeeeeeeeeeee | 3,701 |
| Write identical content at its child a | 4,457 |
| Create another directory, a 756-byte write | 5,213 |
| Begin an 8 MiB file naming the same 4 MiB chunk twice | 5,834 |
| Record the chunk stored | 5,834 |
| Commit the file | 7,399 |
| Begin the same file under a second path | 7,399 |
| Commit the second file | 8,117 |
| Import a collection row with a 65,536-byte placed body | 9,329 |
| Write that same body as a file | 10,066 |
| Admit and reconcile the actual v7 starter, all 14 receipts | 61,988 |

A repeated begin, stored report, commit, and identical collection import had no
net effect. Creating the 756-byte directory exactly at the ceiling succeeded.
Starting one byte higher in the offline database raised file_index_full, rolled
back the change, and preserved the previous meter. At the ceiling the Worker
also refused the next directory without changing the meter. The per-unit figure
matters; a final-state comparison alone cannot prove that no earlier write
was refused. The numbers above are observations, not a second pricing formula.

The Node adapter must pass Worker ArrayBuffer bindings to node:sqlite as a
Uint8Array. Node 22 refused the first inline file before this change. Use
savepoints for nested synchronous storage transactions. A refused file write
rolls back its own work. The enclosing import call has a different boundary
(see the two-entry replay below). The smoke used a synthetic namespace key and
declared chunk digests offline. The Worker exercised its actual FILES binding.
No real customer data or infrastructure was used.

## File batches on disk

The real `migrate`, owner `WriteGate`, `Files` and `importFiles` ran against
a private on-disk SQLite database per synthetic owner, with DELETE journaling
and FULL synchronization. Each group of 32 entries starts with a directory.
Its 31 files have 32-byte bodies: odd entry numbers store the entry number as
a four-byte big-endian prefix, even numbers share an all-zero body. Paths are
`/workspace/imported/synthetic/d<group-start>/f<entry>`. The real 256-entry
import bound determines the batch. The last batch replay skipped all 256
entries in each run and left the charge unchanged.

| Entries | File index bytes | Database bytes | Largest sampled DB + journal bytes | Node 22 import time | Node 24 import time |
|---:|---:|---:|---:|---:|---:|
| 1,024 | 1,003,230 | 1,474,560 | 1,520,440 | 0.435 s | 0.395 s |
| 10,240 | 10,031,467 | 5,619,712 | 6,391,968 | 3.273 s | 3.618 s |
| 51,200 | 50,350,187 | 24,608,768 | 25,881,984 | 16.041 s | 19.109 s |

Both runtimes used SQLite 3.54.1. The table uses a per-database cache capped
at 32 prepared statements, enabled after the migrations. On Node 22 the 1,024
entries issued 12,745 data statements. Preparing them every time took 3.624 s,
versus eight preparations with reuse, with identical charge and file lengths.
Timing covers input generation, hashing, writes, commits and sampling. Import
time excludes schema setup and replay. Sizes are logical lengths
sampled before and after each batch commit. Production hardware, chunk I/O,
backup decoding, rows, starter reconciliation and the census window still need
a representative rehearsal.

A two-entry batch does **not** roll back its earlier successful file when a
later one is refused. The outer owner gate catches the error inside its SQLite
transaction, commits the earlier writes, then reports the error. A plain
transaction wrapper incorrectly rolled both entries back. An independent
Node replay measured the first file's 1,070-byte charge, set the disposable
meter so that file fit exactly at 536,870,912, and got `file_index_full` on
the second. Retrying the first alone skipped it. After making room, the pair
skipped one and wrote one, then skipped both. The real Worker RPC test in
owner/test/import.test.ts independently measured the space needed and confirmed
the retained and absent paths through the public importCheck door. A census
must stop on the actual
refusal and retain the correct per-file and per-batch semantics.

## Private SQLite scratch during interruption

The named database in the first experiment needs startup reclamation after
a hard stop. An alternative for rehearsal on the Linux import host is SQLite's temporary database.
`new DatabaseSync("")` creates a private temporary main database for each
connection. SQLite can spill it to disk. A successful open alone does not
establish where its files live. [SQLite temporary databases](https://www.sqlite.org/inmemorydb.html#temporary_databases) · [Temporary file placement](https://www.sqlite.org/tempfiles.html)

On Node 22.23.5 and 24.22.3 (SQLite 3.54.1), the same synthetic 51,200-entry
run through all 18 migrations, the owner's WriteGate and import door used
a private directory on a Linux overlay filesystem for `SQLITE_TMPDIR`.
Both runs wrote all entries and skipped all 256 on the final batch retry.
Both reported 50,350,187 charged bytes and 24,608,768 logical database bytes,
the same as the named control. The largest **sampled** combined length of
the temporary open files was 26,250,984 bytes. Sampling at batch boundaries
and inside the gate does not establish a maximum allocation. A separate
tmpfs run produced the same counts and lengths.

At 8,192 completed entries the child stopped before releasing a savepoint.
The parent examined the files and sent that child SIGKILL. On both runtimes
all three temporary file descriptors had mode 0600 and zero hard links, and
the target directory was empty both before and after the kill. In the named
control the 4,710,400-byte database and 21,032-byte journal remained.
A sibling-process check ran two connections in the same scratch directory
on each runtime. Killing the first left the other's inode alone. The survivor
verified 1,024 committed rows, added 256 and verified all 1,280. These
observations apply to the tested Linux filesystem and VFS. Access by the
host account and recovery of underlying storage are outside this check.

SQLite falls back from an unusable `SQLITE_TMPDIR` to other locations.
The window's existing free-space check measures its work volume. Place and
verify the writable scratch mount on that volume for the pinned job image
before using the measurements to size it. A tmpfs charges memory. In a
separate control, a child-local injected ENOSPC on an anonymous file write
caused SQLite error 13 on both runtimes. The previous transaction remained
intact and the next one committed once the fault cleared. An incomplete
census cannot authorize a cutover. The replay used synthetic data only.

## Boundaries for implementation

- Reuse the owner's real write paths and schema triggers. Do not sum separately
  computed charges for starter, files and row bodies; these may share content
  and chunks. Replay bundle rows, then manifest pages in the same order as the
  doors, then the starter when admission and tiers allow it. Confirm its version
  advanced because Starter records, and can absorb, a failed pass.
- Import entries are constrained to /workspace/imported. The current starter is
  outside that tree. Its registered main agent and backup bookkeeping do not
  add a charge to the nine tables in MANIFEST_TABLES. Keep this scoped to a
  fresh import. An arbitrary pre-existing owner can behave differently. A
  suspended account can defer its starter until it is active.
- Both census and bundle use measure. Its null-tier backup mode must still
  bypass fit. Preserve existing size and storage diagnostics and the distinction
  between aggregate public counts and the private local per-owner findings.
  Fail loudly on unreadable data, SQLite errors or an incomplete starter.
- The current importer cannot import arbitrary Worker source, start a child
  process, use a live owner, or add a network binding. Decide a narrow shared
  production boundary before wiring the offline owner. Retain only the inline
  content the real write requires and keep a per-owner temporary disk database
  private, bounded and cleaned up on success, refusal and interruption.
- Measure the full census on representative backup shapes and the planned host
  before claiming a cutover window. The batch timings above cover inline files.
