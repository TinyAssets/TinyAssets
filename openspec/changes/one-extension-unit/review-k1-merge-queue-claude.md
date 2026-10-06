I found one failing test, caused by the `_run` split; now checking the broker test context and `Owner.read`.

Last check: finding the auth on the outside-clients route handler.

I found one real defect in the repair diff, and it fails a test. Everything else holds and no guard is weakened.

## Finding (correctness: a gate that fails today)

**`tinyassets/agent_turn_coordinator.py:501-511` (shipped copy is identical) breaks `tests/test_learned_catalog_grant_boundary.py:308-309`.**
Splitting `_run` into a `_run` wrapper plus `_run_turn` moved `self.owner = self._check_scope()` into `_run_turn`. The test `test_the_owner_threaded_into_the_catalog_is_the_same_identity_the_journal_uses` reads the source of `AgentTurnCoordinator._run` and asserts it contains that line. I ran that one file locally and it fails:
```
AssertionError: assert 'self.owner = self._check_scope()' in '    async def _run(self):\n        # Lifecycle hooks belong ...
1 failed, 1 passed, 6 deselected
```
The file is not in `.github/heavy-test-files.txt`, so required CI will go red. Either fix keeps the guard at full strength:
- **(a)** Make `self.owner = self._check_scope()` the first line of `_run`, before `await self._run_turn()`, and drop it from `_run_turn`.
- **(b)** Point the test at `AgentTurnCoordinator._run_turn`. The assertion stays exactly the same; it just reads the method that now contains the line.

Do not loosen the assertion. Your Linux "15 failed" baseline should include this test. If it doesn't, the required runner will add it.

The move itself is semantically sound:
- `turn_end` still fires inside `request_budget_scope`, only after a successful body.
- If the hook raises, it still reaches the `except BaseException` effects-evidence path.
- `run()` (`agent_turn_coordinator.py:462`) is the only caller of `_run`. `tests/_legacy_agent_turn_oracle.py:194` calls `self._run()` directly, so it now gets the hook too, which looks like the intent.

## Assessed, no finding

- **Guidance restored verbatim:** apart from the K1 middleware, the guidance diff against origin/main is exactly the new extension-UI paragraph. The `write_graph` base64 warning and the chapter list match origin/main, and so do the UI component and update-call text. The new paragraph (editing a working file doesn't update an activated revision; package pinning is in `ta extension:help`; direct authoring uses `write_graph`) agrees with the restored text.
- **Owner-door UI read (`app_ui.js:666`):** it now uses `Owner.read`, the same owner-session read every other app_ui and command-center read in the file uses (`:318`, `:349`, `:711`, …). The live-revision check is unchanged (an error, a missing row or a mismatched `ui_id` still throws).
- **Broker git refusal (`outbound_connections.py:1303-1309`):** only the message changed. Non-stream git verbs are still refused before the grant is resolved and before any credential or network use. `test_workspace_authority.py:518` (match on "outside the granted connection scope", `dispatched == []`) still holds. One cosmetic point: for an absent or revoked grant the text now says "scope" instead of a grant-resolution error. That's diagnostic only, not a floor issue.
- **Inventories:**
  - Background authority: `RemoteMcp._exchange` / `self.stream` is entered for both `tinyassets/mcp_remote.py` and the packaging mirror (the two files are byte-identical). The call at `mcp_remote.py:173` (`self._broker.stream(...)`) exists. The entry follows the existing `BoxExecutor._collect`/`self.stream` convention. It adds exact entries; it doesn't widen anything.
  - Timer: `broker/git_upload.py::Upload.read` is classified `CALL_SCOPED`. That's accurate: `_wake.wait(0.1)` loops with `_check()` and `check_authority()` re-run on every wake and after the loop.
  - Storage: `.outside-client-authority.sqlite3` matches `outside_authority.py:52` and is covered by account deletion (`account_deletion.py:711`). Calling it non-quota platform bookkeeping is correct.
  - Route: `/app/outside-clients` is POST-only (`onboarding/__init__.py:2572`). The handler calls `require(request)` and derives the owner from the session, never from the request body, and accepts exact field sets only. The test asserts `{"POST"}`.
- **Mirrors:** all five modified runtime files are byte-identical to their packaging copies.
- **Collisions:** none observed with other lanes.

VERDICT: ADAPT


## Agreed finding: correction verification

The finding is resolved: the fix is the first of the two I proposed, applied correctly in both copies of the file.

**What I checked:**
- **The move:** in `tinyassets/agent_turn_coordinator.py`, `self.owner = self._check_scope()` is now the first line of `_run` (line 502). It is gone from `_run_turn`. The plugin copy under `packaging/claude-plugin/.../runtime/tinyassets/` is byte-identical.
- **Nothing skips the scope check:** `_run_turn` has one caller, `_run` at line 505, and that call comes after the check. If the scope check raises, the turn body and the `turn_end` hook are both skipped. That is the same as before the split, when the check raised at the top of `_run_turn`. `pooled_budget` and the other `self.owner` readers still get the value before the body runs.
- **The guard test:** `tests/test_learned_catalog_grant_boundary.py` is unchanged and I re-ran it here: **9 passed**. The assertion was not loosened.
- **Scope of the change:** the code diff is just this one line moved, in both copies. The only other changes are the review records under `openspec/changes/one-extension-unit/`. None of my other earlier assessments are affected.
- **Collisions:** none observed.

**Two things this approval does not cover:**
1. The fix is still uncommitted in the working tree, so the PR head (79784d2c60) does not contain it yet. This approval applies to the head that includes this exact diff.
2. The full six-shard Linux CI run is still in progress. I am not vouching for it; the only test I ran was the one file above.

VERDICT: APPROVE
