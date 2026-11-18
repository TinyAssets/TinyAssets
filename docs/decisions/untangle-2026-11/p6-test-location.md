# p6-test-location as built (2026-11-18)

P6 moves the surviving process scripts' tests alongside them. On main
f934871ad a disposable checkout moved the queue-attempt and rulebook-ratchet
modules from tests/ into scripts/tests/, corrected their root resolution,
removed the two explicit pytest arguments and retired their old-stack shard
durations. The required process job still discovers the two modules
recursively. All 37 cases in them passed, and the complete process command
passed all 227. Three other consumers lost evidence:

- The trusted base's test-hygiene command reported 28 removed definitions:
  seven queue and 21 ratchet. The head's ordinary pytest results cannot make
  the base gate recognize the new paths.
- The shape registry reader saw 2,216 definitions before and 2,188 afterward.
  Both modules still declare their original requirement IDs. The working-tree
  and git-revision readers and their inventory only recognize tests/.
- The required structural target list still named tests/test_rulebook_ratchet.py.
  This was the only missing target. The prune command separately reconstructs
  the same location when protecting structural checks.

The existing scripts/tests/ also contains six untagged modules with 25 test
definitions. Feeding that whole directory into today's orphan-prune rule
would nominate all 25 for removal. The required process job runs them today.

Land path recognition in the trusted base before actually relocating either
module. Scope it to the tests deliberately governed by shape and prune.
Exercise both revision and checkout readers, weekly inventory, coverage
collection and cleanup, and exclude the test source itself from measured
production lines. Resolve the structural runner and its prune protection
from the same target. Keep the process job on every event and the retiring
Python shards' selection separate. Rehearse the move again against that base:
all 28 definitions and their IDs must survive, the complete structural target
list must resolve, and deleting an assertion or adding a skip to a relocated case
must still be reported. The two modules remain in their original locations on this branch.
