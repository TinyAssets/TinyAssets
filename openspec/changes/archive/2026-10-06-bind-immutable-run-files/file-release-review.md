I'll review the isolated same-owner file-input release read-only against the stated head, starting with the change docs and the diff inventory. Per your hard constraints I will not dispatch Codex or any subagent, despite the hook prompt.

Reading the verification checkpoints and design boundary sections, then the core runtime modules.

Now reading the runtime modules and shared-file diffs in parallel.

Runtime hunks read. Next I need the new custody modules themselves plus two greps (patch persistence of `io_manifest`, served create sanitizer). Requesting all of them now.

**Review of isolated same-owner file-input release at head `db2da824ed99ea49173d150847fef78499ea5f1f`** (tree comparison against `d3fa5f91`, read-only, no subagents or Codex dispatched per your constraints; the hook's Codex prompt was deliberately not followed).

**Test scope I ran at this head (Windows, Python 3.14, fresh basetemp):**

| Command | Result |
|---|---|
| `python -m pytest -q tests/test_run_file_public_authoring.py` | 23 passed, 0 skipped, 17 s |
| Ruff E501 on `api/branches.py` head vs base | 3 vs 3 |
| Ruff E501 on `daemon_server.py` head vs base | 8 vs 8 |
| Ruff on all new custody modules | clean |
| Byte compare of every changed canonical module to its plugin mirror | all identical |

A first run of the same file errored at setup on a stale sandbox-locked basetemp directory. That was my harness path, not the code, and the rerun above is the evidence.

**Q1 Public usability: AGREE.** Ordinary create, remix, patch and publish carry the manifest through the shared staging in `tinyassets/api/branches.py:2579` and the remix inheritance at line 2663 applies only when the member is omitted. The patch op at line 3071 rejects a missing member, clears on null and replaces on object, and final-model validation runs on both build and patch so a bad batch saves nothing. Build idempotency treats the manifest as immutable content. Version snapshots include the manifest only when present, so legacy hashes are unchanged, and the first authoring test proves the old pin runs with its original contract after an edit. The served sanitizer allowlists only the one metadata op and still strips fork and publication fields. The agent guide text matches the served docstrings. One caveat, not a finding: the served fixture disables provider auth and engine admission, which is the pinned engine's real identity-binding shape but not proof of production admission. Rendered app proof stays the gate, as the docs already say.

**Q2 Authority and bytes: AGREE.** Capture resolves sources only from owned authoring handles, re-fences them before commit, and compares size and digest against the streamed body. Binding resolves the owned ready row first and compares supplied metadata afterwards, and it binds against the persisted envelope snapshot rather than the caller's branch object. Node reads require the execution-use context variable for that run, a running row matching owner, universe, actor and branch, no cancel row, and a reference frozen per invocation from the node's declared input keys. Zero-byte, multi-file and chunk-exact reads are handled correctly in the reader and stream code. No path, URL, workspace or cross-owner adapter exists.

**Q3 Lifecycle and capacity: DISAGREE_EVIDENCE on the rollback wording; the rest AGREE.** Byte-only reservations, ceiling checks against retained plus pending allocations, same-key replay conflicts, subset release with sibling readability, exact cleanup debt, one-hour unbound expiry, tombstone-gated erasure and the scoped-reset classification all check out. Scoped reset never deletes root run history and skips the custody directory, so accepted history survives. The evidence problem: the ceiling is read only at capture time in `tinyassets/run_file_capture.py:159`. The bind path at `tinyassets/storage/run_files.py:332` admits any owned file that is unexpired or already bound to any run, with no capacity check, and reads have none either. Unsetting the ceiling therefore stops only new byte intake. Already captured files stay bindable, executable and exportable, and once bound they never expire. Two sentences overstate this: design.md Migration Plan says rollback stops "captures/admissions", and release-inventory.md line 133 offers stopping intake as the response to an authorization failure. Smallest fix, docs only: state that unsetting the ceiling refuses new capture and reports capture unavailable, that binding, execution and export of existing files continue with no configuration fence, and that halting those requires a hot-fix redeploy, never the old binary.

**Q4 Integration and discovery: AGREE.** The compiler observer hooks, the byte-only pool extraction with scope-bound replay, serialization, the common prepared worker and the two independent maintenance cursors are intact. Moving invoker construction per invocation does not reset the shared enqueue budget, because compile creates it once. Scalar branches still take the legacy executor. No family enrollment, cgroup, bootstrap or private workflow code is in the diff. Read and write targets, the action alias, the guide and the mirrors agree.

**Q5 Evidence and scope: AGREE.** The authoring tests drive real canonical and served create, patch, publish, capture, run and read, and execute the sandbox to completion. Foreign-owner refusal, atomic refusal and immutable-version cases exist. The MagicMock correction returns a real scalar dictionary and weakens no runtime check. The healthy-run status projection returns nothing for ordinary queued runs, so agents are not told to seek host action. Nested, resume, producer capture, materialization, retry and cross-owner remain open, as documented.

**Tracked hardening, not gates:** the operation lock is exclusive and non-blocking, so two parallel nodes reading the same bundle, or a read racing the retention tick, get a busy refusal. A failed capture permanently burns its label.

**VERDICT: ADAPT**, docs only. Correct the two rollback sentences and this approves code landing at head `db2da824ed99ea49173d150847fef78499ea5f1f`. Required CI, the configured global capacity, deployed-SHA proof and rendered user acceptance remain.

Per your hard constraints I am not servicing the unrelated dispatch queue in the `0a7f` worktree. Restating the complete final findings.

**Review of isolated same-owner file-input release at head `db2da824ed99ea49173d150847fef78499ea5f1f`** (tree comparison against `d3fa5f91`, read-only, no subagents or Codex dispatched).

**Test scope I ran at this head (Windows, Python 3.14, fresh basetemp):**

| Command | Result |
|---|---|
| `python -m pytest -q tests/test_run_file_public_authoring.py` | 23 passed, 0 skipped, 17 s |
| Ruff E501 on `api/branches.py` head vs base | 3 vs 3 |
| Ruff E501 on `daemon_server.py` head vs base | 8 vs 8 |
| Ruff on all new custody modules | clean |
| Byte compare of every changed canonical module to its plugin mirror | all identical |

A first run errored at setup on a stale sandbox-locked basetemp directory. That was my harness path, not the code. The rerun above is the evidence.

**Q1 Public usability: AGREE.** Ordinary create, remix, patch and publish carry the manifest through the shared staging in `tinyassets/api/branches.py:2579`, and remix inheritance at line 2663 applies only when the member is omitted. The patch op at line 3071 rejects a missing member, clears on null and replaces on object. Final-model validation runs on both build and patch, so a bad batch saves nothing. Build idempotency treats the manifest as immutable content. Version snapshots include the manifest only when present, so legacy hashes are unchanged, and the first authoring test proves the old pin runs with its original contract after an edit. The served sanitizer allowlists only the one metadata op and still strips fork and publication fields. The agent guide matches the served docstrings. Caveat, not a finding: the served fixture disables provider auth and engine admission. That is the pinned engine's real identity-binding shape but not proof of production admission. Rendered app proof stays the gate.

**Q2 Authority and bytes: AGREE.** Capture resolves sources only from owned authoring handles, re-fences them before commit, and compares size and digest against the streamed body. Binding resolves the owned ready row first and compares supplied metadata afterwards, against the persisted envelope snapshot rather than the caller's branch object. Node reads require the execution-use context variable for that run, a running row matching owner, universe, actor and branch, no cancel row, and a reference frozen per invocation from the node's declared input keys. Zero-byte, multi-file and chunk-exact reads are correct. No path, URL, workspace or cross-owner adapter exists.

**Q3 Lifecycle and capacity: DISAGREE_EVIDENCE on rollback wording; the rest AGREE.** Byte-only reservations, ceiling checks against retained plus pending allocations, same-key replay conflicts, subset release with sibling readability, exact cleanup debt, one-hour unbound expiry, tombstone-gated erasure and scoped-reset classification all check out. Scoped reset never deletes root run history and skips the custody directory. The evidence problem: the ceiling is read only at capture time in `tinyassets/run_file_capture.py:159`. The bind path at `tinyassets/storage/run_files.py:332` admits any owned file that is unexpired or already bound to any run, with no capacity check, and reads have none either. Unsetting the ceiling therefore stops only new byte intake. Already captured files stay bindable, executable and exportable, and once bound they never expire. Two sentences overstate this: design.md Migration Plan says rollback stops "captures/admissions", and release-inventory.md line 133 offers stopping intake as the response to an authorization failure. Smallest fix, docs only: state that unsetting the ceiling refuses new capture and reports capture unavailable, that binding, execution and export of existing files continue with no configuration fence, and that halting those requires a hot-fix redeploy, never the old binary.

**Q4 Integration and discovery: AGREE.** Compiler observer hooks, the byte-only pool extraction with scope-bound replay, serialization, the common prepared worker and the two independent maintenance cursors are intact. Per-invocation invoker construction does not reset the shared enqueue budget, because compile creates it once. Scalar branches still take the legacy executor. No family enrollment, cgroup, bootstrap or private workflow code is in the diff. Read and write targets, the action alias, the guide and the mirrors agree.

**Q5 Evidence and scope: AGREE.** The authoring tests drive real canonical and served create, patch, publish, capture, run and read, and execute the sandbox to completion. Foreign-owner refusal, atomic refusal and immutable-version cases exist. The MagicMock correction returns a real scalar dictionary and weakens no runtime check. The healthy-run status projection returns nothing for ordinary queued runs. Nested, resume, producer capture, materialization, retry and cross-owner remain open, as documented.

**Tracked hardening, not gates:** the operation lock is exclusive and non-blocking, so two parallel nodes reading the same bundle, or a read racing the retention tick, get a busy refusal. A failed capture permanently burns its label.

**VERDICT: ADAPT**, docs only. Correct the two rollback sentences and this approves code landing at head `db2da824ed99ea49173d150847fef78499ea5f1f`. Required CI, configured global capacity, deployed-SHA proof and rendered user acceptance remain.
