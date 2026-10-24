Reading the new disconnect path's lock ordering against existing admission users. Next: the departure helper's member matching and the lease/capture changes.

Checking the disconnect's projection write against the projection validator (non-ready states with candidates).

Broker limits (finding 2) look resolved. Now checking how the owner channel surfaces transport failures, since the writeback retry only catches `OSError`.

The writeback retry looks incomplete: the relay turns a broker outage into EOF, which surfaces as `PermissionError`, not `OSError`. Checking the broker's error reply to confirm.

Finding 3 confirmed incomplete. Now checking a possible lock cycle: disconnect holds `_logins_lock` while waiting on assignment admission, and `login_operation` runs on the event loop.

Finding 1's departure path mostly holds, with one projection-write gap in non-ready states. Now checking finding 5's startup sweep ordering against `owner_runtime.start`.

Checking finding 8's broker-socket path against the real broker socket, then the merge conflicts.

I don't agree with this round yet. Finding 3 isn't fixed in the case it named, and the new disconnect path has one correctness gap. Findings 1, 2, 4, 5, 6 and 8 are resolved. I only read code and ran no tests: this is a Windows host, and the lease and custody tests need Linux tmpfs.

## Previous findings

| # | Status | Evidence |
|---|---|---|
| 1 | **Resolved** for ready assignments; see gap B | `provider_assignment_departure.py:76-124`, called from `subscription_lease.py:223`. Disconnect removes `subscription_cli:<def>` where `definition.ref == source_id`, under exclusive admission held across the custody delete. A concurrent bind can't re-add the member, because the marker is only deleted after the departure commits. |
| 2 | **Resolved** | `broker/subscription_custody.py:64-70`: at most 16 entries per owner, and the byte limit is checked before anything is written. Delete still works when an owner is over the limit. |
| 3 | **Not resolved** | See A. The partial-file half is fixed (`subscription_lease.py:98-105`). The retry half doesn't cover the broker-restart case. |
| 4 | **Resolved** | `subscription_lease.py:216-235`: custody is deleted by `source_id`, with no runtime built. The broker accepts a delete without a revision (`subscription_custody.py:56`). |
| 5 | **Resolved** | The sweep runs before the app and its lifespan are built (`owner_daemon.py:14-16`, `subscription_lease.py:32-40`). An expired login stays in `_logins` until its thread exits (`subscription_runtime.py:598-603`), and disconnect waits up to 5 seconds for it, then refuses (`:583-588`). |
| 6 | **Resolved** | `subscription_lease.py:238-251` accepts all three earlier record versions. `0ead9f6` only added `lock_sha256`, and `document()` returns a deep copy. The stale paragraph in `design.md` is gone. |
| 8 | **Resolved** | `cutover_assignment_repair.py:21-50` runs at `:60` before any read. The socket path matches `daemon_control.py:46`. An unreadable `/proc` entry refuses the run. |

## Defects in the new hunks

**A. MEDIUM: a refreshed token is still lost when the broker restarts** (finding 3's own scenario)
- **Where:** `subscription_lease.py:195-213` (`save_state`) retries only on `OSError`.
- **Cause:** when the broker is down, the box never sees an `OSError`. The relay accepts the box's connection, fails to reach the broker, and closes the connection (`boxes/relay.py:86-87, 112-115`). The box reads end-of-file, which `owner_channel.py:44-46` (or `:62-64` mid-operation) raises as `PermissionError`.
- **Result:** no retry, and the rotated refresh token is gone. The only cases that do retry are the relay socket being missing and the 30-second timeout.
- **Tests:** the new test injects a made-up `OSError("broker restarted")`. The suite's existing unavailable-broker test (`test_refused_refresh_writeback_still_wipes_tmpfs`) models the same outage as `PermissionError`.
- **Fix direction:** also retry on `PermissionError`. That's safe because the read-back at `:206-210` already tells a real generation conflict apart from an outage.

**B. LOW-MEDIUM: disconnect fails partway when the assignment isn't ready**
- **Where:** `provider_assignment_departure.py:115-121` always passes `updated.candidates or None` to the projection writer.
- **Cause:** `remove_members` keeps a `failed` or `pending` state when other members remain (`:19`). Failed assignments keep their candidates (`provider_serving_binding.py:189`, and candidates are stored whenever the manifest is set). The writer then raises "candidate projections require a ready assignment" (`config.py:332-333`).
- **Result:** the membership change is already committed, but custody, the connection marker and `~/.ta-cli/<id>` all remain, and the owner gets a 409. A retry finishes the job. The repair tool guards this case with `state == "ready"`; disconnect doesn't.

**C. LOW: a narrow chance the whole daemon hangs**
- **Where:** `disconnect_source` holds `_logins_lock` (`subscription_runtime.py:578`) while it waits for exclusive admission (`departure.py:94-95`).
- **Cycle:**
  1. A served request holds shared admission across `await off_loop(...)` (`provider_assignment.py:1317-1323`).
  2. Disconnect starts waiting for that admission while holding `_logins_lock`.
  3. A login request arrives. `handle` calls `login_operation` synchronously on the event loop (`subscription_connect.py:99, 108`), which blocks on `_logins_lock`.
  4. The served request can't resume to release its admission, because the loop is blocked. Nothing ever unwinds.
- **Window:** the admission is held only for those reads, so this is unlikely.
- **Fix direction:** call `login_operation` through `run_in_threadpool`.

**D. LOW: refreshes of some user-declared credential files are silently dropped**
- **Where:** `subscription_lease.py:98-105`.
- **Cause:** any changed state file that isn't `.toml` must parse as JSON, or the change is skipped without an error. `subscription_source.py:61` allows any relative path.
- **Impact:** none for the four shipped records, which are all JSON or TOML. A user-declared source with a YAML or plain-text token file would never save a refresh.

## Merges
Both merges are clean.
- Each merge's tree is identical to what `git merge-tree --write-tree` produces automatically.
- Neither merge has a file changed on both sides.
- The merged changes don't touch this branch's code: router 503 handling, `ensure_table` schema declarations, and the turn reply budget.

VERDICT: DISAGREE_EVIDENCE
