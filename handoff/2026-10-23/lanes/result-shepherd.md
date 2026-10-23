**None of the seven PRs has merged yet.** #5226, #5228, #5229, and #5230 are in the merge queue. #5231, #5232, and #5233 have auto-merge enabled but need new lead Drain-Review receipts for their pushed heads. I did not write receipts or touch `reconcile`.

| PR | Current head | State |
|---|---|---|
| #5226 | `6cfc6f292dbca59b0b3bc5ebbef2594c736b6c13` | Queued; re-enqueued after a queue check failed |
| #5228 | `5bf91f41a1e69c2d60e27ec179b0b42449cf126a` | Queued |
| #5229 | `4ab6c7bf027d9e7fe99397f95430bd52a20470c9` | Queued |
| #5230 | `a043196c8f6c1e8dd24c9c64a007f81c397eb7fb` | Queued |
| #5231 | `12e87bbfd99ddd106ec9ea30fad49eeda89583e0` | Required tests pass; **needs lead receipt** |
| #5232 | `1fee54214e67b034d5240c80c57458022de5fd18` | Required tests pass; **needs lead receipt** |
| #5233 | `540cac20be340c0ef61dcd718a32c5bb8c9386b6` | Required tests pass; **needs lead receipt** |

**Pushes made:**

- #5231 `12e87bbfd99ddd106ec9ea30fad49eeda89583e0`: limited the native sign-in client lookup to native and desktop discovery. The Linux oracle browser test passed **4/4**; `node --check` passed.
- #5232 `1fee54214e67b034d5240c80c57458022de5fd18`: added the missing C3 `verifies` ID to the tests. Linux oracle **28/28** and Ruff passed.
- #5233 `540cac20be340c0ef61dcd718a32c5bb8c9386b6`: added the missing C10 `verifies` ID to the tests. Linux oracle **4/4** for the C10 test file and **7/7** for the public URL tests, plus Ruff, passed.

I corrected the Lane declarations in #5232 and #5233’s bodies. Their descriptions still end with the required footer. The temporary worktrees were removed.

**What still blocks merging:** the queue’s aggregate required check rejects four selected test files that report no cases; those same files have no test definitions on `main`. This affected the first queue group and needs a mainline CI/selection fix outside these reviewed PR scopes. Separately, #5233’s nonrequired runsc owner-wake job failed again on its one rerun because its test setup omits `UNIVERSE_SERVER_URL`; that requirement and omission are already on main.