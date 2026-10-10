## Why

After the per-owner cutover, every non-dot top-level entry of an owner center is
owner content, and `role_storage.scan` refuses a center holding one the owner
does not own. Three daemon writers still create such names directly as the
daemon uid: both `config.py` config writers (`mkstemp` + `os.replace`), the
branch-task queue lock `branch_tasks.json.lock`, and the auto-ship ledger lock
`auto_ship_attempts.jsonl.lock` (raw `os.open(O_CREAT)`, link-following). Any
config write after cutover, and the first status read of every new center,
leaves a daemon-owned visible file; every later owner write in that center fails.

## What Changes

- Both config writers publish `config.yaml` through `universe_files.write_universe_file`
  (`mode="replace"`), which routes owner-content names through `role_content.write`.
- The two coordination locks become platform-only dotted names,
  `.branch_tasks.json.lock` and `.auto_ship_attempts.jsonl.lock`, opened link-free
  through `universe_files.open_lock_file`, and are classified in `PLATFORM_LOCK_NAMES`.
- No ownership check is loosened; `role_storage` and `role_content` are unchanged.

## Cutover

The daemon is one compose service recreated per deploy, so old and new code never
hold the queue lock concurrently: the lock rename is a clean cutover at restart.
Existing visible `branch_tasks.json.lock` / `auto_ship_attempts.jsonl.lock` files
in migrated centers are empty, owner-owned, and coordinate nothing once the new
image runs. They are left in place (never blind-deleted, since they sit in the
owner's content tree); removing them is a later reviewable owner-tree cleanup.

## Impact

`tinyassets/config.py`, `tinyassets/branch_tasks.py`, `tinyassets/auto_ship_ledger.py`,
`tinyassets/command_center_layout.py`, their plugin runtime mirrors, and tests.
