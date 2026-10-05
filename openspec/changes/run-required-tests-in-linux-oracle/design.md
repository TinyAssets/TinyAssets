# Design: one Linux venue for the proofs and the gate

## D1. Reuse the oracle; do not build a second venue

`linux-jail-proof` already loads `ta-jail-userns` and calls
`scripts/linux_oracle.py`, which builds the image, copies the tree, drops to
uid 1001, probes bubblewrap and only then runs the suite. Both new callers use
the same profile step (pinned equal to `linux-jail-proof`'s by test) and the
same script. There is no new profile, option or image.

## D2. `--required-runner` is a mode, not a command hook

The shards must run `scripts/ci_required_tests.py`, not raw pytest: selection,
sharding, the floors, the quarantine comparison and the manifest all live there.
The oracle therefore gets one flag that swaps the in-container command from
`python -m pytest ...` to `python scripts/ci_required_tests.py <args after -->`.

- The script path is a constant. There is no way to name another program.
- Arguments are passed with `shlex.join`, in order, unmodified. The one addition
  is `--pytest-arg=--basetemp=/tmp/b` when the caller gave no basetemp: the same
  short temp root outside the repo that the pytest mode adds.
- The mode refuses `--shell`, `--no-bwrap` and `--as-root`. A shard cannot be
  pointed at a venue where the jail tests skip.
- The mode requires `--out DIR` and exactly one `--junit /out/<name>.xml`. The
  runner writes its manifest beside the junit, so both land in `DIR`.

## D3. Output paths and digests do not move

| What | Before | After |
|---|---|---|
| Runner `--junit` | `shard-out/junit-shard-N.xml` | `/out/junit-shard-N.xml`, with `shard-out` bound at `/out` |
| Manifest | beside the junit | beside the junit (same directory on the host) |
| Empty shard | plan step writes both on the host | unchanged |
| Artifact | `junit-required-shard-N` from `shard-out/` | unchanged |
| Selection | `affected.txt` in the checkout | copied to `/work` with the tree |
| Selection digest | `--selection <digest>` from `select` | unchanged, passed through |
| Step summary | written by the runner | runner writes `/out/summary-shard-N.md`; a host step appends it |

`CI=true` and `GITHUB_ACTIONS=true` are passed into the container so tests and
the runner see the environment they saw on the bare runner.

## D4. Failure stays red

- Profile load fails: the step fails; no test runs; no manifest; the aggregate
  reports the shard missing.
- Image build fails: `linux_oracle.py` exits non-zero; same outcome.
- Jail probe fails: exit 3 before the runner starts; same outcome.
- Nothing retries, and no step carries `continue-on-error` or `|| true`.

## D5. What is not proven yet

Hosted runs must establish every item below. Items 1 and 3-5 gate the
implementation PR. Item 2 runs on the immediate documentation follow-up after
that merge: changing `tests.yml` makes the implementation diff select `ALL`,
so its own merge-group run cannot prove the selective path. The venue change
remains open until all five items are verified, the requirements are synced,
and the independent gate review is complete.

1. All six whole-surface shards green in the oracle, including the `shard`
   floor and the 10,700 aggregate floor. Tests that have only ever skipped on
   the bare runner will execute for the first time; some may fail.
2. A selective merge-group run green, with host planning and in-container
   sharding agreeing on file ownership. The container packs shards from a
   fresh `git init` of the copied tree rather than the checkout's index. A
   disagreement fails closed (the aggregate's coverage check names the file),
   but it has not been observed either way.
3. Shard wall clock inside the 30-minute timeout with an uncached image build.
4. Suite behaviour as uid 1001 without the package installed (the oracle
   imports it from the tree), and with `TINYASSETS_DATA_DIR=/tmp/ta-data`.
5. `real-browser-proof` green on an ordinary pull request with 0 skips.

The PR-time `affected-tests` job is not required and stays on the bare runner;
jail-backed tests still skip there.

## Rollback

Revert the commit. No state, protection setting or secret changes with it.
