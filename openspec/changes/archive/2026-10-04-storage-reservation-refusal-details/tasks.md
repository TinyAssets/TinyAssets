## 1. Preserve the remaining diagnostics

- [x] 1.1 Independently review the bounded proposal and preserved #4428 diagnostic code before applying it.
- [x] 1.2 Hand-port only the four allowed diagnostic hunks documented in design.md (no cherry-pick or whole-file checkout), including diagnostic components and owner text, retain admission/recovery behavior, and regenerate the mirror.
- [x] 1.3 Verify real ledger transitions, owner-only projection and unchanged totals; complete independent code review.
- [x] 1.4 Sync the behavior specification and archive this change in the same delivery lane.
Validation so far (2026-10-04, Windows/Python 3.14): python -m pytest -q -p no:randomly tests/test_storage_accounting.py tests/test_storage_gates.py tests/test_storage_slice_gates.py tests/test_jail_disk.py tests/test_jail_admission_headroom.py => 113 passed, 1 platform skip. Ruff, plugin rebuild/import probe passed. Independent Claude code review APPROVE; reviewer independently ran the accounting module: 40 passed. Normal hosted CI and deployment are not claimed here; CI remains a merge gate.
