## ADDED Requirements

### Requirement: Jail-backed tests execute in the Linux oracle, never skip
The `real-browser-proof` job and both paths of the `required-tests-shard` job
SHALL run their tests inside the Linux oracle container as the unprivileged
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
