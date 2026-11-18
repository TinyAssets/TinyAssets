# When a scoped sandbox sync may finish (2026-11-18)

**Open.** On main `f934871ad` and the joined tree `b4ebe1371`, the runner can report
a successful sync-in before applying an already committed, readable change. The next sync
resumes at the correct cursor and applies the change. The immediate consequence is stale
input to projection and to the CLI about to start.

## Why a short page is ambiguous

The real `/sync/changes` door calls `fileChanges`. For a scoped client's live lease,
`OwnerDO.#syncer` constructs its readable set from the agent definitions and abilities
its run may use. `Files.changes` reads up to `MAX_LIST` raw history rows ordered by
version. On a full page the cursor is the last raw version. On a short page it is the
file space's current sequence. `fileChanges` then filters those rows while retaining
the cursor. The runner's `syncIn` saves that cursor and returns on fewer than
`MAX_LIST` **visible** changes. `syncCommand` proceeds directly to project the workspace.

On Node 22 and 24, against each tree, the owner's real 18 migrations in SQLite held
1,000 scoped-out directory changes followed by one readable directory. The owner's
real change method and filter returned zero visible rows at cursor 1,000. A real
runner sync and mirror ended there with the readable directory absent. Another sync
created it and persisted cursor 1,001. A read of the first page without filtering
returned all 1,000 rows. A stable cursor stopped after one request. A request ahead at
1,101 received 1,001 and the mirror rewound. A cursor below the retained history floor
caused a listing reset, followed by another changes request.

## Completion and bounded work

Reading again as long as the cursor advances repairs that finite example. In a
disposable runner experiment, it took three reads, including the final empty one.
With one new, scoped-out row committed before every read, it was still running after
six reads. The runner keeps heartbeating and waits for the sync child without a fixed
timeout.

A second consumer-only experiment used the present owner's cursor rule. The raw version
is a unique integer. A cursor advance smaller than the requested limit cannot contain
a full raw page, so that response has reached the owner's sequence as observed in that
read. At or beyond the limit, an additional read is safe even if there were holes.
This took two reads for the finite example and one with one new row per read. Both
experiments preserved adding a reachable directory, a stable cursor, an ahead cursor
and a history-floor reset. With 1,000 new filtered rows per read, neither candidate had
finished after six reads. Six was the experiment's stopping point.

The current owner's cursor suffices for a finite stable feed. Settle the completion
promise during continuous writes before changing the runner. A bounded snapshot needs
an observable end or an equivalent coordination rule that survives paging and reset.
Simply stopping after a page budget could once again start a CLI with missing input.
The existing working-set plan also needs the same guarantee when its selection changes.

## Paired release evidence

The old runner will continue to stop on visible count when given a new owner that only
adds completion metadata. A runner relying on version distances needs that cursor rule
from every owner it may encounter. Verify old and new images with every owner version
served during rollout and handback. Cover zero visible rows, late reachable rows,
scope resets, retention resets, a cursor ahead of the feed and concurrent writes.
The scoped-client route and grant-change reset are exercised in
`owner/test/runs-authority.test.ts`; the SQLite and runner replays above used synthetic
data and a supplied readability predicate. No deployed image or live owner was tested.
