# CI venue acceptance evidence, 2026-10-05

Independent reviewer: Claude CLI, separate from the Codex closure author.
Result: APPROVE; all D5 requirements verified. The review's status corrections
are applied in this archive. Its summary-output observation is recorded in
`docs/concerns/2026-10-05-required-shard-summary-fixture-output.md`.

Reproduction: `gh run view <run> --repo TinyAssets/TinyAssets`,
`gh api repos/TinyAssets/TinyAssets/actions/jobs/<job>/logs`, and
`gh run download 37246536654 --repo TinyAssets/TinyAssets --dir <outside-repo>`.
The artifacts include `affected.txt`, all six shard manifests and JUnit files.
Compare each manifest's digest with the sorted unique selected paths and compare
JUnit `file` sets with that selection. Each selected file occurs in exactly one
shard. Hosted environment: Linux, Python 3.11.17, bubblewrap 0.12.0, uid 1001.

- [Whole-surface gate](https://github.com/TinyAssets/TinyAssets/actions/runs/37245286729)
- [Browser and full-preview proof](https://github.com/TinyAssets/TinyAssets/actions/runs/37245237518)
- [Selective merge-group gate](https://github.com/TinyAssets/TinyAssets/actions/runs/37246536654)
- [Spec sync and selective proof PR](https://github.com/TinyAssets/TinyAssets/pull/4459)

The review below is preserved as delivered. Its references to pending status
lines describe the reviewed pre-closure tree; those lines are now corrected.

---

# Gate review: `run-required-tests-in-linux-oracle` — CI venue acceptance

**VERDICT: APPROVE.** All five D5 items are proven by hosted runs, and the main `ci-linux-proof-venue` spec preserves the delta. Venue archive is warranted now: #4459 has already landed (see the first correction below), so no further wait applies.

Scope: the CI venue change only. `custom-ui-assets` stays OPEN with live founder acceptance owed, and nothing here is a production or deployment claim. I checked the three runs with `gh`, re-parsed the downloaded manifests, JUnit and logs myself, and ran no tests.

## Corrections to the evidence packet

- **#4459 is merged, not queued.** `gh pr view 4459` reports MERGED at 2026-10-05T00:23:27Z as `9e96ff95959499cd2ec6fda9e0761b9d498240dd`, alongside #4460. The squash message lists all three commits, including the `02f91d` status fix.
- **The selective proof ran on the commit that is now `main`.** Run 37246536654's merge-group head is `9e96ff…`, which is `origin/main`. `git diff origin/main HEAD` is empty, so the reviewed worktree tree equals `main`.
- **Three files are now stale and should be fixed in the closure commit:** the packet's status line, the `proposal.md` status ("selective merge-group proof … remain pending") and the last sentence of the `tasks.md` hosted-evidence paragraph.

## D5 acceptance items

| # | Item | Finding | Evidence |
|---|---|---|---|
| 1 | Six whole-surface shards green, with floors | AGREE | Run 37245286729: `merge_group` at `51db6894…`, all six shard jobs and aggregate 111563820969 succeeded. The aggregate log shows `--min-ran 10700`, ran 26297, 0 failing, 0 new failures, 95 skipped (budget 134), 2586 s (budget 3000). |
| 2 | Selective merge-group green, host and container agree on ownership | AGREE | Run 37246536654 succeeded. `affected.txt` has 172 files and is not `ALL` ("kept 172 of 180"). All six manifests show `pytest_exit 0`, `total 6`, shards 1–6, one digest `sha256:a28275d9…80ac`, slices 43/32/28/28/23/18. My re-parse of the JUnit `file` attributes gives 172 files, no overlap, exactly equal to `affected.txt`. |
| 3 | Wall clock inside 30 minutes, uncached build | AGREE | Job times recomputed from the API: 527, 591, 581, 632, 554 and 626 s against `timeout-minutes: 30`. All six full-run logs have zero `CACHED` steps and show the apt bubblewrap install. |
| 4 | uid 1001, package not installed, `TINYASSETS_DATA_DIR=/tmp/ta-data` | AGREE | All 12 shard logs (full and selective) carry the banner `python 3.11.17 \| git 2.47.3 \| bwrap 0.12.0 \| uid 1001`. Each invokes `--required-runner` with the data-dir env and `--basetemp=/tmp/b`, with no `--privileged`, `--cap-add`, `--no-bwrap` or `--as-root`. The Dockerfile installs requirements only, and no shard log has a `pip install -e`. |
| 5 | `real-browser-proof` green on an ordinary PR, 0 skips | AGREE | Run 37245237518: `pull_request` event at `87df68e62b`, the final head of #4316. Log shows `131 passed, 33 deselected` and `36 passed`, with 131 and 36 per-case PASS identity lines and no FAIL, SKIP or MISSING lines. |

On item 2, the JUnit holds 4985 cases with 77 skipped and no failures or errors, which matches the aggregate's 4908 ran. The four selected browser proofs each have a PASS line in the selective aggregate log.

## Spec preservation

AGREE. The requirement bodies of the delta and `openspec/specs/ci-linux-proof-venue/spec.md` are byte-identical from the first `### Requirement` onward: three requirements, six scenarios. The main spec adds only the title and Purpose, and that file is on `origin/main`.

## Missing acceptance evidence

None blocking. Two observations, neither a floor or correctness finding:

- **DISAGREE_CONCERN (cosmetic):** the selective shard 1 `summary-shard-1.md` is 209 lines of fixture output, such as "3 shards … ran 12" and "SHARD SET INCOMPLETE". The workflow passes `GITHUB_STEP_SUMMARY=/out/summary-shard-N.md` into the container (`tests.yml:356,377`), and shard 1 owns `test_ci_required_tests.py`, whose tests write to that variable. Line 390 then appends the file to the job summary, so a reader could mistake fixture text for a gate failure. The verdict is unaffected because it comes from the JUnit and manifests. Worth a `docs/concerns/` entry, not a blocker.
- **Not re-proven here:** the "jail cannot be created" and "runner mode is misused" scenarios rest on the unit tests from tasks 2–4 in #4316. No hosted run exercised a failing probe, and D5 does not require one.

## Closure

Tasks 5 and 6 can be checked. Archive `run-required-tests-in-linux-oracle` alone, after correcting the three stale status lines above. Do not archive `custom-ui-assets`.
