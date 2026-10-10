## Why

After the per-owner cutover, every non-dot top-level entry of an owner center is
owner content, and `role_storage.scan` refuses a center holding one the owner
does not own. Three daemon writers still create such names directly as the
daemon uid: both `config.py` config writers (`mkstemp` + `os.replace`) and four
coordination locks opened with a raw, link-following `os.open(O_CREAT)`:
`branch_tasks.json.lock`, `auto_ship_attempts.jsonl.lock`,
`subscriptions.json.lock` and `bid_execution_log.json.lock`. Any
config write after cutover, and the first status read of every new center,
leaves a daemon-owned visible file; every later owner write in that center fails.

## What Changes

- Both config writers publish `config.yaml` through `universe_files.write_universe_file`
  (`mode="replace"`), which routes owner-content names through `role_content.write`.
- The four coordination locks become platform-only dotted names (`.branch_tasks.json.lock`,
  `.auto_ship_attempts.jsonl.lock`, `.subscriptions.json.lock`,
  `.bid_execution_log.json.lock`), opened link-free
  through `universe_files.open_lock_file`, and are classified in `PLATFORM_LOCK_NAMES`.
- No ownership check is loosened; `role_storage` and `role_content` are unchanged.

## Cutover

The daemon is one compose service recreated per deploy, so old and new code never
hold a lock concurrently: the lock rename is a clean cutover at restart.

Centers the old image wrote to after the owner split already hold daemon-owned
(uid 1001) `config.yaml` and visible locks; the real synthetic image showed
`config.yaml`, `branch_tasks.json.lock` and `auto_ship_attempts.jsonl.lock`,
and every later owner scan of that center refuses. The new code does not touch
them, so they need repair. The existing backed-up migration already is that
repair; no new tool is added:

- `deploy/role_migrate.py` `target()` labels every visible center entry as the
  owner's (owner uid/gid, daemon named ACL), and `_scan` accepts uid 1001 as a
  source, so a daemon-owned visible entry is relabelled, never refused.
- A rerun on a split volume is allowed (`preconditions` accepts
  `"split": "owner-split"`, `step_marker` is then a no-op), and a converged entry
  is unchanged (`role_migrate_probe.py`).

So: deploy this image, then in a maintenance window run the runbook's
`migrate --check` / `migrate --snapshot <id> --snapshot-bytes <n>` /
`migrate --check` with writers stopped, after a full backup. The rerun also
resets the runbook's known post-start platform drift to its labels, which is the
migration's declared target. Old visible locks become inert owner-owned files;
deleting them is left to the owner.

## Impact

`tinyassets/config.py`, `tinyassets/branch_tasks.py`, `tinyassets/auto_ship_ledger.py`,
`tinyassets/subscriptions.py`, `tinyassets/bid/execution_log.py`,
`tinyassets/command_center_layout.py`, their plugin runtime mirrors, and tests.
