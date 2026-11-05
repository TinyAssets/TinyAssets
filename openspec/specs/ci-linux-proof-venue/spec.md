# ci-linux-proof-venue Specification

## Purpose
Execute browser containment proofs, source-PR affected tests and required test shards in the reviewed Linux oracle while preserving the existing gate contracts.

## Requirements

### Requirement: Jail-backed tests execute in the Linux oracle, never skip
The browser-proof job, source-PR affected-test shards and both required-test shard paths SHALL run their tests
inside the Linux oracle container as the unprivileged
oracle user, behind the oracle's bubblewrap probe. A venue in which bubblewrap
cannot create a jail SHALL fail the job before any test runs.

#### Scenario: The jail cannot be created
- **WHEN** the oracle's probe cannot create a bubblewrap jail as the suite's user
- **THEN** the oracle exits non-zero before the suite starts, the job is red, and nothing retries or falls back to another venue

#### Scenario: A preview proof runs
- **WHEN** `real-browser-proof` runs on a non-draft pull request that touches its paths
- **THEN** every `real_browser`-marked case and every collected case of `tests/test_ui_preview.py` is present in JUnit with no skip, failure or error, or the job fails

### Requirement: The venue adds only the existing per-container exceptions
The oracle container SHALL receive only `seccomp=unconfined`,
`systempaths=unconfined` and the named AppArmor profile `ta-jail-userns`, the
same profile `linux-jail-proof` loads. No job SHALL change a host sysctl, run a
privileged container, add a capability, read a secret, or disable Chromium's
sandbox.

#### Scenario: A workflow widens the venue
- **WHEN** a shard or browser-proof step adds `--privileged`, `--cap-add`, a `sysctl` call, `--no-sandbox`, `--no-bwrap`, `--as-root`, or a profile step that differs from `linux-jail-proof`'s
- **THEN** the workflow's unit tests fail

### Requirement: The required shards run the unchanged gate runner
Both the whole-surface and the selective shard path SHALL invoke
`scripts/ci_required_tests.py` through the oracle's `--required-runner` mode
with the same selection, profile, shard and digest arguments as before. The
junit and manifest SHALL be written to the shard's artifact directory under
their existing names. The six-shard matrix, empty-shard handling, floors,
quarantine comparison and the `required-tests` aggregate job SHALL be unchanged.

#### Scenario: A shard runs
- **WHEN** a shard owns at least one test
- **THEN** the oracle execs `python scripts/ci_required_tests.py` with the workflow's arguments in order, plus only a short `--basetemp` outside the repo, and `junit-shard-N.xml` and `junit-shard-N.json` appear in `shard-out/`

#### Scenario: A shard owns nothing
- **WHEN** the plan step reports that a shard owns no selected file
- **THEN** the host writes the empty junit and manifest as before and no image is built

#### Scenario: The runner mode is misused
- **WHEN** `--required-runner` is combined with `--shell`, `--no-bwrap` or `--as-root`, or is given no `--out` or no single `--junit` under `/out`
- **THEN** the oracle refuses and runs nothing

### Requirement: A tree-only diff runs the Python tests that can read it
The merge-group selection SHALL run, for a diff confined to `platform/`, `docs/`,
`openspec/`, `ideas/` and the top-level `.md` files with no `.py` file, the tests
`affected_tests.tree_readers` maps to a touched tree, plus `select()`. It SHALL
run the whole surface for any other diff. Every runner run SHALL audit reads:
a test that reads a tree the map does not give it fails.

#### Scenario: A platform-only diff is queued
- **WHEN** a merge group's diff changes only files under `platform/`
- **THEN** the shards run only the tests that name `platform/` in path form, walk the checkout's root or run git, and a test of the old stack that does neither does not run

#### Scenario: A test reads a tree the map cannot see
- **WHEN** a test opens, lists, checks the existence of, or hands a child process a path in a tree its file is not mapped to, or runs git in the checkout with a subcommand that reads it
- **THEN** that test fails, naming the tree, the path and the file to fix

#### Scenario: A helper reads a tree once, at import
- **WHEN** a module's import reads a tree that some test importing it is not mapped to
- **THEN** the run fails naming the module and that test, whichever test loaded it first

#### Scenario: A diff touches Python
- **WHEN** any changed path is a `.py` file or lies outside the trees, counting a moved file's old path
- **THEN** the selection is the whole surface

### Requirement: A merge-queue removal counts against a head only for a test its diff selected

The queue-attempt cap SHALL count a `failed_checks` removal against the head only when the queue entry's own failed Tests run, created after the PR's latest enqueue, failed a test the entry's diff selected. A pytest test is selected when its file is in that run's selection, or the selection is ALL. A vitest test is selected when the diff touches `platform/`. The removal SHALL be recorded as counted before any evidence is read.

#### Scenario: A flake outside the diff's reach
- **WHEN** every test the removal's run failed lies outside the diff's selection
- **THEN** the ledger records the removal as not counted, and the head moves no closer to the cap

#### Scenario: The evidence is missing
- **WHEN** a GitHub read fails, no failed Tests run of this queue entry exists, or a failed job names no test
- **THEN** the removal counts

### Requirement: Source-PR feedback uses the same capable venue
The `affected-tests` job SHALL select its files from the pull-request merge
checkout against `HEAD^1` before entering the oracle, then pass that selection,
the existing heavy-file exclusions, the six-way shard and `affected` profile to
the unchanged gate runner through `--required-runner`. It SHALL publish the
runner summary and retain each shard's JUnit and manifest in an artifact, including
on failure. Source-PR feedback SHALL remain non-required; it SHALL NOT replace
or relax the authoritative merge-group `required-tests` gate.

#### Scenario: An affected test renders a protected preview
- **WHEN** the selected source-PR tests include a real preview render
- **THEN** they run as the oracle user behind the jail preflight with the same per-container exceptions as the merge queue, and an unavailable jail fails instead of bypassing the render

#### Scenario: Source-PR tests fail
- **WHEN** an affected shard fails its existing test/quarantine comparison
- **THEN** the shard reports failure and uploads its available JUnit, manifest and summary; it does not retry in a less restricted venue or report success through a fallback
