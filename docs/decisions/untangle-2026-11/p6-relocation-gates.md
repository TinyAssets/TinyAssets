# p6-relocation-gates as built (2026-11-18)

The trusted base must recognize a moved test before it can review the move.
The two-file P6 rehearsal on f934871ad lost all 28 definitions from shape and
hygiene. This prerequisite gives tests that retain their OpenSpec claims a
home under scripts/tests/ci/. The required process job already collects it.
The other process tests keep their present scope. No legacy test moves here.

The git-blob and working-tree readers, inventory, hygiene and weekly prune job
now agree on that home. Weekly coverage excludes the test source itself from
production lines. The inventory includes its git history and treats imports
from relocated tests as test use. A structural test's execution and its prune
protection use the same path. Its original location takes precedence while
it exists. A missing registered file still reaches pytest even for an empty
selection, and the normal PR selector can load the guards by filename.

The existing required pytest command writes xunit1. On main pushes its optional
artifact supplies the missing process evidence to the weekly inventory. Only
case IDs from scripts/tests/ci/ are admitted from this file. Root-suite results
continue to come from their original jobs. A missing main artifact is named in
the report. A synthetic overlap preserved the shard's actual skip and excluded
an unrelated process failure. GitHub will first produce this artifact after the
change lands. The producer, reader and XML were exercised locally.

In the disposable move of the queue and rulebook modules, both sets of requirement
IDs survived, seven definitions plus 21. The trusted gate reported zero removals
and the 37 moved pytest cases appeared in the process XML. Removing one assertion
and introducing one skip were both reported. All 41 structural targets resolved
and the real ratchet command passed all 21 cases. With equal coverage, the prune
command kept the structural ratchet as a witness. Removing its file made even an
empty structural selection fail. The 25 unrelated untagged definitions still lie
outside the prune scope. Land this prerequisite first and then repeat the actual
move against the new trusted base.
