# F1 pre-cutover file-index census research (2026-11-18)

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
savepoints for nested synchronous storage transactions, so a refusal rolls back
the same writes as the owner. The smoke used a synthetic namespace key and
declared chunk digests offline. The Worker exercised its actual FILES binding.
No real customer data or infrastructure was used.

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
- Exercise disk and time at census scale before any claim about the cutover
  window. One tiny synthetic owner establishes parity for the cases above.
