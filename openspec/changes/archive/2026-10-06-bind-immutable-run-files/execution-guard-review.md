I'll review the frozen checkpoint myself, starting with the primitive, its tests, the reused OS lock, and the design/verification sections.

Next I need the executor model and terminal-write ordering. Pulling `get_future`, the futures registry, `update_run_status`, the cancel path, and any asyncio hops in the invoke path.

I have what I need. One durable review pattern came out of this that I'll append to my existing cross-family review patterns memory, then deliver the verdict.

One last check before the verdict, per my own pattern 20: confirm the head has not moved during the review.

Verdict summary: the primitive is sound and may be integrated, but the lifecycle contract is missing one concrete invariant for unstarted rows. That gap lets a literal implementation both retire a healthy queued run and let a late worker overwrite that retirement and execute. Everything else I checked holds.

**Evidence base.** Reviewed at `f68a9db8` via `git show`; the head is unchanged at verdict time, though the worktree now carries uncommitted builder files outside this scope. Ran the eight tests on this host on 2026-09-19, Windows 11, Python 3.14.3:

```
python -m pytest -q -p no:cacheprovider --basetemp=C:/Users/Jonathan/AppData/Local/Temp/ta-pt-execlock tests/test_run_execution_lock.py
8 passed in 2.64s
```

## Basic-safety blocker on the contract

**DISAGREE_CONCERN: the queued-but-unowned handoff gap has no rule.** The runs row is inserted as `queued` in the request thread at `tinyassets/runs.py:4763` and the job is submitted at `tinyassets/runs.py:4812`. The guard can only be acquired when a pool thread dequeues the job, and the parent pool has four workers, so a run can sit queued and unguarded for a long time. `create_run` sets `started_at` at INSERT at `tinyassets/runs.py:1501`, so it is a creation time, not a start-of-execution discriminator. For ordinary runs the only start marker is the RUNNING write at `tinyassets/runs.py:3703`.

So "queued and the guard is acquirable" is ambiguous. It is either a healthy run waiting in a live peer's in-memory pool, or an orphan in a dead process. The contract forbids TTL and PID evidence on the guard, but says nothing about this state. A multi-worker recovery that acquires the guard and retires the row produces a spurious interruption. Worse, the transitions are unconditional. `update_run_status` writes `UPDATE runs SET ... WHERE run_id = ?` with no prior-status predicate at `tinyassets/runs.py:1621`, and the RUNNING write uses it. Sequence: peer recovery acquires the free guard, retires the queued row as `interrupted`, releases. The original pool thread then dequeues, acquires the now-free guard, overwrites `interrupted` with `running`, and executes. That is an unsafe retirement followed by a duplicate-execution path, reached by following the contract as written.

Two invariants close it and should be added to design.md section 8 before implementation:

1. Recovery never retires an unstarted row on guard availability alone. Under the guard it may re-dispatch it or leave it. Any retirement of a queued row needs a separate disclosed staleness rule that is not a guard takeover.
2. Start and every terminal write are conditional on the expected prior status and run only while the guard is held. A worker whose conditional start affects zero rows exits without executing. Delivery already does this through `start_attempt_in_transaction` claim tokens; ordinary runs need the same, either a status predicate or an `execution_started_at` equivalent on the runs row.

The primitive itself needs no change for this.

## Points I checked and agree with

- **Descriptor inheritance, AGREE.** The fd is set non-inheritable, so exec-based sandbox spawns drop it. The sandbox refuses any descriptor not in its explicit allowlist at `tinyassets/node_sandbox.py:1659`. No `os.fork` or fork-context multiprocessing exists under `tinyassets/`. A hypothetical fork-without-exec child would keep the flock alive after the parent exits, which is the safe-hold direction, not a steal.
- **Process and thread validity, AGREE.** The receipt is bound to PID, thread ident, and an active set, and the test proves a PID mismatch refuses. Thread ident reuse cannot matter while the receipt is in the active set. `contextvars.copy_context().run` keeps the worker on one thread, so wrapping `_worker` at `tinyassets/runs.py:4796` puts every terminal write inside the guard.
- **Scope identity, AGREE.** Sidecars key on sha256 of the run id under the resolved runs DB parent, and `require_held` checks the connection's main database. A path mismatch fails closed.
- **OS semantics, AGREE.** flock is per open file description, released by the kernel on death. `msvcrt.locking` treats EACCES as contention, and the CRT opens without delete sharing, so the sidecar cannot be unlinked while any handle is open, which matches the never-unlink rule. I/O errors raise rather than grant.
- **Stop, AGREE.** `request_cancel` at `tinyassets/runs.py:2890` inserts intent only and never writes terminal status, so Stop needs no guard. Because `try_run_execution_lock` has no blocking mode, "never wait for the guard while holding the fence" is true by construction.
- **Crash and contention, AGREE.** The spawn-process and crash tests cover cross-process exclusion and release without inode replacement.

## Nonblocking hardening

- State that the guard is host-local. flock and msvcrt.locking are not meaningful over NFS or SMB, the same assumption the SQLite runs DB already makes.
- Document that the lock is not re-entrant. A same-thread second acquisition returns None, which a caller could misread as foreign ownership.
- On filesystems reporting `st_ino` of zero, the dev/ino swap check is vacuous. Not unsafe, but worth noting.
- Audit every `update_run_status` caller for the predicate rule above. The provider-admission failure write at `tinyassets/runs.py:4780` runs in the request thread before any worker exists.

## Already-explicit activation gates

- The unconditional startup sweep at `tinyassets/runs.py:5446` and the read-path sweep at `tinyassets/runs.py:803` must be replaced, not wrapped. Both retire rows other live processes own.
- `_has_live_future` at `tinyassets/runs.py:155` must never remain as death evidence. Exact test: a run held by a spawned peer process shows no local future and must not be retired.
- Exact tests still owed: guard wraps the pool-thread worker end to end; Stop completes while a worker holds the guard; kernel-empty evidence refuses retirement while an orphan sandbox child lives; Linux oracle run of the integrated head.
- `FamilyFence` from cloud core `1b99c8c4` is not in this worktree, so fence ordering and its short-hold claim are unverified here.

VERDICT: ADAPT

The running dispatch is this review itself, and the vanished entries belong to other sessions. My brief forbids dispatching, editing, or committing, so there is no further lane I may take here. Restating the bounded result so the captured output stands alone:

**Reviewed** `f68a9db8` only. `tests/test_run_execution_lock.py`: 8 passed on Windows, Python 3.14.3, 2026-09-19.

**Primitive:** no basic-safety blocker. Descriptor inheritance, PID/thread/active-set receipt, DB scope binding, flock and msvcrt semantics, crash release without sidecar replacement, and non-blocking contention all hold. AGREE on each.

**Contract, one required addition (DISAGREE_CONCERN):** the queued-but-unowned handoff gap. The row is inserted `queued` in the request thread at `tinyassets/runs.py:4763` and the guard is acquired only when a pool thread dequeues at `tinyassets/runs.py:4812`. `started_at` is set at INSERT at `tinyassets/runs.py:1501`, so it is not a start discriminator. `update_run_status` is unconditional at `tinyassets/runs.py:1621`. A peer recovery can acquire the free guard, retire a healthy queued row, release, and the late worker then overwrites `interrupted` with `running` and executes. Add to design.md section 8: recovery never retires an unstarted row on guard availability alone, and start plus every terminal write is conditional on the expected prior status and executed only under the guard, with zero rows meaning lost ownership.

**Stop:** `request_cancel` at `tinyassets/runs.py:2890` writes intent only, and the guard has no blocking mode, so Stop never waits on it. AGREE.

**Nonblocking hardening:** state the guard is host-local; document non-reentrancy; audit all `update_run_status` callers for the predicate rule.

**Activation gates already explicit:** replace the unconditional sweeps at `tinyassets/runs.py:5446` and `tinyassets/runs.py:803`; retire `_has_live_future` at `tinyassets/runs.py:155` as death evidence; integrated Stop and peer-process recovery tests; Linux oracle on the integrated head. `FamilyFence` is not in this worktree, so fence ordering is unverified here.

VERDICT: ADAPT
