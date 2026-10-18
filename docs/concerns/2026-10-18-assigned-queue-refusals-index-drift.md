---
severity: P2
title: Assigned queue refusal index drift
filed: '2026-10-18'
summary: Two missing index entries in the reported cutover backup; the table is intact and the cause remains unconfirmed.
---

# Assigned queue refusal index drift

The reported fresh cutover backup's `.tinyassets.db` failed `integrity_check`:
two rows were missing from `idx_assigned_queue_refusals_universe_observed`.
The table was intact; REINDEX on a copy restored integrity. A write-path defect
or interrupted write/crash is suspected; the cause is not yet established.
Cutover may repair only named index-only errors after its paired backup, with
the daemon stopped, and must verify integrity again. No production values are
recorded here; development validation uses synthetic corruption only.
