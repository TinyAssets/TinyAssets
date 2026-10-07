I'll review the frozen checkpoint `28b1a4a3` by reading the named files via `git show`, tracing the guarded-start contract, and comparing against the parent where relevant.

Analysis is complete. One last evidence item: confirm which commit the working tree is on for the evidence line.

One last lookup: exact line numbers in the frozen worker for the citations.

I have everything needed. Nothing further to request.

**Evidence base.** Reviewed `28b1a4a3` against parent `f68a9db8` via `git show` only. The worktree head has since moved to `9b8736fd`, but every reviewed path is byte-identical to the checkpoint. Focused run on this host, 2026-09-19, Windows 11, Python 3.14.3:

```
python -m pytest -q -p no:cacheprovider --basetemp=C:/Users/Jonathan/AppData/Local/Temp/ta-pt-rir94508 tests/test_run_input_runtime.py
13 passed in 1.02s
```

No edits, no dispatch, no memory writes.

## Release question, point by point

- **One reserved run, AGREE.** Guard is held from dequeue through settlement (`run_input_runtime.py:84` to the end of `_work`). Process-local dedup is a set keyed by resolved DB path plus run id, and re-dispatch after completion returns "terminal" without touching the row. Two processes contend on the OS lock and the loser returns without a status write.
- **Original inputs and target, AGREE.** The envelope is reloaded after the preparer returns (`run_input_runtime.py:112`), the snapshot is re-hashed against the stored digest, and inputs come only from the run row. The test that mutates the envelope in the callback proves the reload.
- **Current owner and source authority, AGREE.** Tombstone fence on the author transaction, owner and universe pinned on every read, identity `user_id` pinned to the admitted owner, actor pinned to the run row. Fresh empty context plus a set identity means no inherited request grants. I checked every `ContextVar` in the package: all seven default to None or False, so an empty context fails closed rather than open.
- **No replay, AGREE.** Start requires queued plus null marker plus null claim plus no cancel intent plus expected status, owner and universe, all in one `UPDATE` at `run_input_admissions.py:211`. Zero rows means return. A started marker with a queued row classifies as interrupted and the worker only logs. This is the queued-handoff CAS the prior review required, and it is present in this worker.
- **Cancellation and terminal ordering, AGREE.** Cancel intent is checked inside the same `BEGIN IMMEDIATE` transaction as the start marker, so a concurrent cancel either lands before the snapshot and blocks the start, or waits on the reserved lock and is then honored by the executor's pre-node check. Late workers after any of the five winners are proven by the parametrized test.
- **Callback contract executable, AGREE.** Lock order matches the delivery runtime (author store, then runs store). Read-only helpers on fresh connections proceed under WAL, and I probed that `CREATE TABLE IF NOT EXISTS` on an existing table does not take a write lock under a foreign reserved lock, so schema-ensuring reads inside the callback do not self-block. A callback that writes will hit the 30 second busy timeout and fail closed into the terminal seam, which is the correct outcome.
- **Branch-version hash, AGREE.** The version path re-encodes with `allow_nan=False` while the publisher hashes with `default=str`. For JSON-native snapshots both produce the same string. A NaN-bearing snapshot is refused as integrity failure, which is fail-closed.

## Required corrections, not basic-safety blockers

**DISAGREE_CONCERN: the activation gate fails after the irreversible writes, not before.** On any runtime lacking the cloud seams, the sequence is deterministic. Start commits at `run_input_runtime.py:123`. Events and lineage are written at line 138. The provider is bound at line 142. `invocation_dispatched` flips to True at line 146. The call at line 156 then raises `TypeError` because the frozen `_invoke_prepared_branch` has no `_execution_guard` parameter. The handler at line 160 sees the flag and does not terminalize. Result: a permanently interrupted row and a provider claim that never reaches `close_foreground_run_provider`. This never executes a graph, so it is not a replay or cross-user risk. But "no unsafe fallback" should mean refusing before the durable marker. Fix: verify both seams exist before `start_in_transaction`, either at dispatch time or at the top of `_work`. A signature check on `_invoke_prepared_branch` and a callable check on `terminalize_unstarted_run` is enough.

**DISAGREE_EVIDENCE: the local terminal set at `run_input_runtime.py:68` omits `interrupted` and `running`.** The storage module's set at `run_input_admissions.py:187` includes `interrupted`. Sequence: worker takes the guard; a legacy sweep marks the row interrupted; the author fence or preparer raises; the handler calls the seam with status failed on an interrupted row. Correctness then rests entirely on the seam's CAS. The local predicate should be `status == "queued"` only, which is the exact definition of unstarted.

**Contract pin needed at integration.** The worker relies on `terminalize_unstarted_run` for post-marker, pre-invocation failures, such as event initialization or provider binding raising. The seam must CAS on the runs row alone and be marker-agnostic. If cloud treats any start evidence as started, those failures become permanent debt for a case the worker knows exactly.

**Coverage gap.** No worker-level test drives the `run_cancels` intent branch into `_terminal_unstarted`. The only cancel test uses a terminal status. Add one test with `request_cancel` before dispatch and a traced seam asserting cancelled status and a null marker.

## Explicit missing integration gates, already disclosed

- Cloud's `_invoke_prepared_branch(..., _execution_guard=guard)` and `terminalize_unstarted_run` are absent at this checkpoint. Every test that reaches invocation monkeypatches it. These remain mocks, not runtime proof.
- The RUNNING write inside `_invoke_graph` at `runs.py:3703` is unconditional here. Cloud's CAS owns it.
- The read-path orphan sweep at `runs.py:174` retires queued rows after a default 3600 second grace with no local future. Same-process dispatch is protected because the future is tracked before the worker runs. A second process is not. Legacy fencing stays required.
- Legacy unguarded workers can still race an admitted run if an adapter admits a run created by the ordinary path. Fencing before migration stays required.

## Optional hardening

- Log exceptions raised by `_terminal_unstarted` inside the handler. Today a second failure is swallowed by the Future.
- Read `actor` before calling `prepare`, not after, at `run_input_runtime.py:103`.
- `dispatch_admitted_run` always uses the parent pool. Accept a depth if any adapter ever admits child runs.
- Document that adapters must create runs with `create_run`, never `_prepare_run`, or pending events are written twice.

VERDICT: ADAPT

The hook's lanes are outside my brief: it forbids dispatching, editing, committing, and re-dispatching. The one thing I may do is read the FINISHED item, so I'll check it against what I already reviewed.

Checked. The FINISHED item is the 94507 primitive review whose ADAPT I already folded in via the checkpoint's disposition and assessed inside this worker. The "running" item is this review's own capture. The vanished items and any re-dispatch, edit, or verdict action are forbidden by my brief, so there is nothing further I may advance here.

Restating the bounded result so the captured output stands alone:

**Reviewed** `28b1a4a3` against `f68a9db8` via `git show` only. Reviewed paths are byte-identical in the worktree. `tests/test_run_input_runtime.py`: 13 passed on Windows, Python 3.14.3, 2026-09-19.

**Release question:** AGREE on all five properties for the intended integrated head. One reserved run via guard plus dedup. Original inputs and target via post-callback reload and digest check. Owner and source authority via tombstone fence, pinned owner, universe, identity and actor, and a fresh context whose seven package contextvars all default closed. No replay via the single conditional start `UPDATE` at `run_input_admissions.py:211`, which is the queued-handoff CAS the prior review required. Cancellation via the intent check inside the same reserved transaction as the marker. The callback contract is executable: lock order matches the delivery runtime, and I probed that schema-ensuring reads do not self-block under the held reserved lock.

**Required corrections, not basic-safety blockers.** The activation gate fails after the marker, events and provider bind rather than before them, because `invocation_dispatched` flips at `run_input_runtime.py:146` and the seam-less call at line 156 raises; verify both seams before `start_in_transaction`. The local terminal set at `run_input_runtime.py:68` omits `interrupted` and `running`; restrict to `status == "queued"`. Pin at integration that `terminalize_unstarted_run` is marker-agnostic. Add one worker-level test for the `run_cancels` intent branch.

**Explicit gates, already disclosed:** cloud seams absent, unconditional RUNNING write at `runs.py:3703`, cross-process orphan sweep at `runs.py:174`, legacy worker fencing.

VERDICT: ADAPT
