# Served Codex sandbox test fails in the workflow-model branch suite

Handoff from PR #4474's full required Linux 3.11 run. The nine requested policy
failures are repaired, but this additional failure is unresolved:

`tests/test_provider_served_router.py::test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`

The fake Codex launch is classified unavailable; the router raises
`AllProvidersExhaustedError` and correctly refuses authority widening. No sandbox
or routing assertion has been changed. It failed in the initial full shard 2,
a focused run after the four repaired files, and the corrected full shard 2.
AGENTS.md's same-error-three-times rule requires handing this off, not another
patch attempt in this lane.

Pinned comparison main is `e5de8d18b27280f2cdf2d4af3dd0417e765b601c`.
The case passes alone on that checkout, and main's full required shard 2 passes
with **3,761 passed / 50 skipped**, explicitly including this test (JUnit checked).
The corrected branch shard has **3,631 passed / 52 skipped / 1 failed**. Thus this
is not established as pre-existing on main. File allocation differs between the
two trees, so a suite-order interaction is still possible; no root cause is proven.
The initial failing shard already predates the additional cache-fixture repair,
so that repair is not an explanation for the initial failure.

Evidence under `C:/Users/Jonathan/Projects/`:

- `wt-4474-required-final/shard-2.log`: initial full-shard traceback.
- `wt-4474-linux-fixed.log`: all 74 repaired-file tests pass; sandbox case fails.
- `wt-4474-required-recheck-2.log`: corrected full-shard result.
- `wt-4474-main-shard2-failures.log`: main's isolated comparison, 15 passed.
- `wt-4474-main-required-2.log`: main's full-shard comparison.

Reproduce with `scripts/linux_oracle.py --required-runner`, CI/GITHUB_ACTIONS true,
`TINYASSETS_DATA_DIR=/tmp/ta-data`, and runner arguments
`--exclude-from .github/heavy-test-files.txt --shard 2/6 --profile shard` plus
`--junit /out/junit-shard-2.xml`. Use an external `--out` directory; basetemp is
the oracle's `/tmp/b`. Resolve by explaining and repairing the unavailable fake
launch with Linux evidence while retaining the complete sandbox assertions.
