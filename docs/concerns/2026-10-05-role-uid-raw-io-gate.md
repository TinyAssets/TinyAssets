---
title: Role UID branch has four unregistered raw-I/O sites
status: open
date: 2026-10-05
---

The affected D73 Linux suite reports 243 passed / 1 failed / zero skips:
`tests/test_universe_path_io_guard.py::test_no_new_raw_file_io_in_universe_touching_modules`.
The same four extra AST inventory entries are present at incoming 891476fe23:

- broker/supervisor.py `_protect_daemon: .read_text()` (procfs status)
- credential_vault.py `_persist_role_vault: .unlink()` (failed temporary publication)
- credential_vault.py `_set_snapshot_directory_mode: os.open()` (directory descriptor)
- tool_images.py `_shown: .open()` (Pillow BytesIO parsing)

D73 introduces none. This is a gate failure, not proof these four operations
expose another owner's data. Route filesystem operations through the established
no-follow helpers and handle the in-memory parser accurately without broadening
the guard. Keep the guard and its shrink-only inventory assertions intact.
Must resolve before the per-role UID build PR; startup remains inactive.
