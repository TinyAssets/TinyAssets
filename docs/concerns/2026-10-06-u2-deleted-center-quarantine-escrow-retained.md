---
severity: P2
title: A deleted center's quarantined cross-tree alias stays in root-private escrow
filed: '2026-10-06'
summary: account deletion removes the tree and retires the center, but bytes the first forward migration quarantined for it are carried in the owner journal's escrow forever
---

# Deleted center's quarantine escrow is retained

**Found:** 2026-10-06, U2 D221 (`tests/test_role_admission_startup.py::
test_a_deleted_centers_quarantine_escrow_is_carried`). **Severity:** P2,
data retention after account deletion. There is no cross-user exposure: the
escrow is under `.role-owner-migration/quarantine`, which is root-private 0700.
**Owner:** U2 migration lane.

If a regular file has names in two owners' trees when the forward migration
first runs, the owner phase moves both names into escrow. This is rare: the
production alias scan found 0 such inodes. After DA7, when one of those
centers is deleted, D218 removes its tree and writes its `retire` row, and
startup accepts the smaller set. The journal still carries the deleted
center's escrow rows, however, and the inode stays in escrow under the other
owner's name. Nothing deletes the deleted owner's name or bytes.

Fix: when a center is retired, drop its escrow name, and drop the inode once
no surviving owner's name references it. This must happen in the owner phase,
under the journal, and must not touch D214/D215/D217 provenance.
