# C9 task 1.2 cannot meet its standalone cutover gates

The measured 1.2 implementation on `conv/c9-external-agents` is **+1,114 core
lines** against `origin/main` (1,184 added, 70 deleted, `tinyassets/` and
`deploy/`). `scripts/check_shapes.py` passes, but the S3 and S4 ceilings do not
fall. The old `provider-discovery` and `provider-exec` cells still need the
`/snapshot` mount and `AUTH_DIR`: the Codex and Claude job executors using them
are scheduled for tasks 1.4 and 1.5. Removing those paths in 1.2 would break
live jobs. The current branch therefore must not be opened as the requested
net-negative floor PR.

The prototype has a working package catalogue path: two production-shaped
`role_image_oracle.py` image runs with a synthetic owner and a mounted Codex
credential each returned 11 real models and a fresh derived status; the disposable
owner home had mode 0700, no `auth.json` and no job directories afterward.
One earlier run with the same credential exited nonzero; its cause remains
unclassified, so repeatability is not yet established. The actual founder
command-center copy has not passed this acceptance, and the production-copy
inventory lost its transient connection ledger during a concurrent oracle run;
task 1.1 still needs that ledger enumerated. A failed job initially
put credential writeback bytes in the mapper's stderr tail; writeback now uses
the private stdout pipe, and the failing path still needs a regression check.
The Linux affected suite passed 164 tests with one version-dependent CLI test
deselected. These facts prove the new path, not deletion of the old path or
the founder's production-copy acceptance.

The cache currently admits its durable attempt in the worker, so an admitted
refresh that never starts has no row to age into `failed: not_started`. Move
admission to the scheduler and pass its receipt id to the runner before
claiming task 1.2 complete.

Resolve by folding 1.2 into the executor cutovers after C3 authority work
(#4629 plus 2.4/2.5) merges, so the old snapshot cell and vendor executors
can be deleted in the same PR and the line and S3/S4 gates can fall. If 1.2
must remain a separate PR, its net-negative and snapshot-deletion requirements
need an explicit replacement design before implementation continues.
