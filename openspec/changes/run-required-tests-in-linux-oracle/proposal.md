# Run the browser proof and the required shards in the Linux oracle

Status: **local candidate, awaiting execution authorization.** Nothing here has
run in CI. The only authorized execution so far was the one-off trial #4398,
which explicitly excluded this permanent migration.

## Why

The custom-UI preview (#4316) renders a bundle in Chromium inside a real
bubblewrap jail. On a GitHub-hosted runner bubblewrap cannot make a jail, so
those tests skip:

- `real-browser-proof` run 37187940339 (at `0544b9bbb2`) skipped all 4 preview
  cases: `bwrap` is not installed on the bare runner.
- Installing a pinned bubblewrap on the host (trial #4388) got as far as the
  UID map and stopped on `Permission denied`: the hosted kernel restricts
  unprivileged user namespaces.
- The one-off trial #4398 (at `20cfcec59c`) ran the proofs in the existing
  Linux oracle container as uid 1001 and passed 32 browser cases plus all 31
  cases of `tests/test_ui_preview.py`, 0 skipped.

The same is true of the merge gate. `required-tests` runs its six shards on the
bare runner, so jail-backed tests skip there. The required browser no-skip
assertion rejects the skipped preview cases; ordinary jail cases count toward
the skip budget. The current venue cannot supply the required preview proof.

`linux-jail-proof` already runs in the oracle for exactly this reason. This
change moves the two remaining venues onto it.

## What Changes

- `docker/linux-oracle.Dockerfile` installs the `browser` extra and Chromium at
  `/opt/playwright`, readable by uid 1001. Chromium keeps its own sandbox.
- `real-browser-proof` runs the `real_browser`-marked cases in the oracle, then
  the whole of `tests/test_ui_preview.py`, and fails unless every case of both
  executed. Triggers are unchanged (ordinary pull requests plus dispatch).
- `required-tests-shard` runs the same `scripts/ci_required_tests.py` command
  in the oracle on both its whole-surface and its selective path. The host
  `select` job, the plan step, the six-shard matrix, the artifact names and the
  `required-tests` aggregate job are unchanged.
- `scripts/linux_oracle.py` gains one explicit mode, `--required-runner`, which
  runs that one script instead of pytest. The default pytest command is
  unchanged.

## Security boundary

The oracle container gets the per-container exceptions `linux-jail-proof`
already uses, and nothing more: `seccomp=unconfined`, `systempaths=unconfined`
and the named AppArmor profile `ta-jail-userns` (unconfined plus `userns,`).
These are **broad exceptions for one throwaway container, not a restrictive
allowlist**. What this change adds is two more jobs that use them, one of which
is the merge gate.

Not changed: no host sysctl, no privileged container, no added capability, no
secret, no self-hosted runner, no branch-protection edit, no skip-budget raise,
no gate demotion, no removed assertion, no weakened product or test sandbox,
and no fallback to a venue without a jail.

## Impact

- Files: `.github/workflows/tests.yml`, `.github/workflows/real-browser-proof.yml`,
  `docker/linux-oracle.Dockerfile`, `scripts/linux_oracle.py`, and their tests.
- The required gate will start executing tests that have only ever skipped
  there. Any that fail in the oracle will turn the gate red until fixed or
  quarantined through the existing ledger. That is the intended effect and the
  main rollout risk; see `design.md`.
- Each non-empty shard builds the oracle image (no layer cache yet), which adds
  wall clock to a 30-minute job.
