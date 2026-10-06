---
severity: P1
title: Any signup or completed account deletion blocks the next role startup migration
filed: '2026-10-06'
summary: the volume coordinator and both phase journals refuse when the set of owner centers differs from the stable journal, so with the split ON every restart after a signup or deletion exits 78 and the reverse rollback refuses too
---

# Principal-set change blocks the startup migration (activation blocker)

**Found:** 2026-10-06, U2 D218 (`tests/test_role_owner_tree_deletion.py::
test_completed_deletion_fails_closed_at_next_migration`). **Severity:** P1 for
activation, none while the switch is OFF (production does not run the split).
**Owner:** U2 migration lane.

`deploy/role_volume_migration.py` refuses with `full migration authority
changed` when the discovered `{center: principal}` set differs from the stable
`volume.json`, and the owner and metadata phases refuse changed bindings
(D216 reconciles work names only, and says new principals stay a loud refusal
pending an admission-generation contract). Under `compose.role-split.yml`,
`ta-role-start.py start` runs that migration on every container start, so:

- a signup that creates a center, or a completed two-pass deletion that removes
  one, makes the next start exit 78 (fail closed, no data change);
- the startup reverse rollback refuses for the same reason, so rollback after
  any deletion needs a manual path.

The fix is an admission generation that adds a new center (allocate, label and
journal it) and retires a removed one, including its quarantine escrow rows,
without reopening D214/D215/D217 provenance. Do not turn the split ON until it
lands with root-oracle rows for add, remove, add-then-reverse and
remove-then-reverse.

(The PID1 zombie item formerly here is resolved by D220.)
