# Sync rescan cursor as reviewed (2026-11-19)

**Hold the owner-only change at `bfaaa617653e17d6d96e6f6dcbcb16e1f7ad77d9`.** Its direct owner test advances beyond a pruned deletion. That test does not run the recursive listing or the runner's next filtered feed read. The existing runner discards the changes in a reset response and resumes from its cursor after listing. The floor changes which records appear together on the first page after the scan.

| Replay with the existing runner | Main `f934871ad` | Candidate `bfaaa617` |
| --- | --- | --- |
| A readable directory arrives after the root listing, behind scoped-out history | The physical directory existed at completion, cursor 2,501. | The empty filtered page ended the sync at 2,499. The directory was absent. |
| A retained directory record precedes the regular file now at the same path | The listing installed the current bytes and sync completed at 2,803. | The following page tried to make a directory over that file. The retry failed at the same path and cursor 1,500. |

Both histories were replayed on Node 22.23.5 and 24.22.3 with fresh bundles of the owner reader and runner, the production SQLite migrations, a local mirror database and a physical workspace. These were offline comparisons. They reproduce the earlier [paired owner and runner review](https://github.com/TinyAssets/TinyAssets/blob/6095e45fcfcaf2c287d843cce5a75f964bed576f/docs/decisions/untangle-2026-11/sync-reset-boundary.md).

- [x] Compare the staged source with main through the runner that consumes it. Keep the candidate in branch history for comparison.
- [ ] Review a finite feed boundary, how the runner acknowledges a completed listing for its readable set, and how the mirror reconciles directory replacement while preserving pending local edits.
- [ ] Exercise interrupted listing, later pruning, and all four old/new owner and runner pairings before the runner image pin, working-set change or handback.
