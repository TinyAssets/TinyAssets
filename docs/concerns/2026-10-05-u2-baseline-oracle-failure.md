---
severity: high
title: U2 baseline root oracle fails in the approved provenance regression
filed: '2026-10-05'
summary: The unchanged D214 stale-record metadata regression fails twice with KeyError ids; full migration acceptance and dependent deletion/startup work remain blocked.
---

At baseline d5cd06acf1, the required root Linux oracle returned 150 passes,
one failure and zero skips. After D216 work-name reconciliation, it returned
153 passes, the identical failure and zero skips. Both ran:

`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py --as-root -- -q tests/test_role_owner_migration.py tests/test_role_metadata_migration.py tests/test_role_volume_inventory.py tests/test_role_volume_migration.py --basetemp /tmp/b`

Failure: `test_d214_replaced_inode_reverses_to_legacy_after_any_cycle[.consumer_liveness/one-stale-record]`
in `tests/test_role_metadata_migration.py`, on the final reverse call.
`deploy/role_metadata_migration.py` raises `KeyError: 'ids'` while unpacking
`original["ids"]`. The test injects an original record with `uid`/`gid` keys,
whereas the current record shape uses `ids`. A generation mismatch should
avoid reusing that injected record, so the schema mismatch alone does not
establish the cause. No test or D214/D215 implementation was changed.

The founder explicitly said not to reopen D214/D215. AGENTS loop rule 7 also
requires handoff after the same finding twice. A separate owner must reconcile
this reproducible acceptance failure with the approved round-2 receipt before
full acceptance can be claimed. Do not skip, xfail, weaken, or retry-until-green.

D216's coordinator/inventory selection passes 31 tests with zero skips.
The installed owner-substep production-image probe passes; it does not prove
full coordinator migration, previous-production CMD boot, or startup admission.
Two-pass deletion remains gated on complete, verified migration. Startup stays
OFF. No production volume was mounted or modified.
