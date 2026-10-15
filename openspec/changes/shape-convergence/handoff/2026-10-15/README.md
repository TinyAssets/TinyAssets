# Uncomposed C7–C12 handoff — draft material, not a runtime change

Jonathan requested publishing remaining C1–C12 work so other sessions can use it.
This bundle preserves the four excluded patches, the exact remaining C10 working-tree
diff, review briefs, and integration contracts. The patch files are inert: checking
out or merging this archive does not apply them. Do not merge this archive as the
convergence implementation. Use a separate coordinated integration worktree.

## Verified published implementation heads (2026-10-15)

| PR | Branch | Exact pushed head |
|---|---|---|
| [4936](https://github.com/TinyAssets/TinyAssets/pull/4936) | `codex/c11-durable-send-ui` | `0742c1e94a48167076b5984d619dda43c3f2163e` |
| [4937](https://github.com/TinyAssets/TinyAssets/pull/4937) | `codex/c10-engine-market` | `6540bdcb4717e9e3cf7fe7a7779a5d59c818adf8` |
| [4938](https://github.com/TinyAssets/TinyAssets/pull/4938) | `codex/c10-package-convergence` | `410dad284bf1ee80e7f75552e78f575052570492` |
| [4939](https://github.com/TinyAssets/TinyAssets/pull/4939) | `codex/c7-c9-session-convergence` | `b1a1e82a8ca2850a85688dea02e8ce10d3f77df5` |
| [4940](https://github.com/TinyAssets/TinyAssets/pull/4940) | `codex/c9-shared-loop` | `f7144464c984e17f58059328ea32ecb0ca637566` |

`codex/c9-shared-loop-core` is published at `0109758c15573cff3876b21c2999199a7aa9c159`.
The local C7 tick audit branch adds no commits: its head `922b871970373a279d5c0b5b407881d5bb99295c`
is already an ancestor of PR4939. No additional tick implementation is hidden locally.
The root core includes cherry-picks of C9 core and repair; avoid applying them twice.

## Patches and dependencies

| File | Original checked base | Integration dependency |
|---|---|---|
| `c9-aclient-inference-usage.patch` | C9 `f7144464` | C6-owned `broker/aclient.py`: forward usage authority on OPEN. Required by PR4940. |
| `c10-extensions-retirement.patch` | C10 engine `6540bdcb` | Coordinate C12 `api/extensions.py`; remove imports/dispatch for market code retired in PR4937. |
| `c10-role-writer-retirement.patch` | C10 engine `6540bdcb` | Coordinate C6 writer probes; remove only retired bid probes. |
| `c11-c12-shell-integration.patch` | C12 `b098b1e097f60bda5722639d145b1aab9f4a76ce` | Requires PR4936 modules and C12 shell composition; renderer and source-reading tests must move together. |
| `c10-uncommitted-worktree.patch` | C10 engine `6540bdcb` | Exact combined local diff for the two C10 patches above. **Alternative representation, not an additional patch to apply.** |

All four original patches pass `git apply --cached --check` against the named
bases in isolated temporary indexes. That is applicability evidence, not a test
pass or review approval. `sha256.json` records preserved file bytes. Original
source patches and the dirty C10 worktree remain intact locally.

C6 PR4920 advanced to `e22fafd81f056f07cb2138465368648799262a73`, and C12 PR4903
advanced to `1d40d748e66197e649f62604e0f44fe585f6583c`. These are observations at
publication, not immutable current heads. Fetch and inspect their newest changes
before composing patches; do not replace those branches with these older snapshots.
No shared-file patch was automatically composed into an owner's branch here.

## Next session

Read [integration-contracts.md](integration-contracts.md) and the relevant
[review briefs](reviews). Fetch the implementation branches and preserve the
latest C6/C12 work. Check each patch against the intended integration tree before
applying it. Re-run affected tests on the actual composed head and obtain the
required cross-family review. The prior test results describe the original heads
or explicitly identified locally composed trees; they do not certify this bundle.

Outstanding work includes C7 platform pause/heartbeat and a whole-writer drain;
C6 native launcher/selected git grants/old-cell composition; C9 streaming progress
attachment and storage migration; C10 invoke/await and legacy Activities migration;
C11 generic operation authority and C12 shell integration. Protected migration,
restore, Linux oracle and live acceptance gates remain. No merge, deployment,
credential or live-configuration change was performed to publish this material.

The only local uncommitted product edits are the two C10 shared-file changes,
both fully preserved here. Other residual `/tmp` files are diagnostic logs and
generated copies, not unpublished implementation. Existing PR descriptions retain
their historical validation results and limitations.
