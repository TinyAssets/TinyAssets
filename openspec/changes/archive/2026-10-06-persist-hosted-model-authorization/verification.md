# Restart-safe hosted authorization candidate — September18, 2026 UTC

Base: dd053157, isolated codex/hosted-flow-durability. Production changes are
limited to hosted_model_auth.py and its generated plugin mirror. No browser
authorization replay, key/code/nonce inspection, credentials read, new grant,
provider HTTP call, merge or deployment was performed by this lane.

## Cause and red/green evidence

Lead observed begin before04:34:42, daemon replacement at04:34:47.24635097Z,
and consent return at04:36. The exact backend enum was not captured; the shown
banner grouped six enums, so do not claim a directly observed network response.
The unchanged implementation's synthetic begin in processA/take in freshprocessB
immediately failed unknown_model_connection. The durable implementation passes
the same test and a four-process race with exactly one successful take.

## Checks

`python -m pytest -q tests/test_hosted_model_auth.py tests/test_hosted_model_flow_persistence.py tests/test_onboarding_model_connect.py tests/test_app_hosted_model_connect.py tests/test_model_bootstrap.py tests/test_model_bootstrap_binding.py tests/test_model_bootstrap_candidate.py tests/test_onboarding_model_setup.py tests/test_account_deletion.py --tb=short`

- Windows Python3.14:156 passed20.96seconds; after WAL addition, persistence
  cohort10 passed2.44seconds. No credential/network mocks pretending to be live proof.
- Final tree snapshot through scripts/linux_oracle.py:156 passed, zero skips,
  22.42seconds; Python3.11.16/git2.47.3/bubblewrap0.12.0, existing native WSL
  Docker. The in-memory _repo_root override supplied the actual WSL worktree path
  because its Windows-linked git metadata cannot resolve inside Linux. No script,
  Docker configuration or host service was changed for this adaptation.
- Ruff changed Python files: passed. Strict OpenSpec: passed. Plugin regenerated,
  import probe passed; all457 canonical files mirror-matched. Diff check passed.

Existing owner/home/preset/verifier mismatches remain refusal-before-consumption.
Store only hashed handle, owner, bound home, preset+digest, public challenge/origin
and epoch timestamps. No verifier/code/key/rawhandle is persisted. Lock timeout
is explicit and preserves the row. SQLite connect timeout5seconds sets the busy
handler; WAL and explicit BEGIN IMMEDIATE precede row selection and consumption.

Account deletion tests cover former-home rows and the real API barrier where
scope succeeds, deletion finishes, then begin resumes: the author-store tombstone
and current-home transaction guard reject it before creating the satellite.
Author reservation is held through satellite commit, never provider exchange.

Future/expired/invalid-duration rows are pruned within the transaction. A failed
take rolls that pruning back; a successful begin sweeps them before capacity
checks. The max1-capacity clock-rollback regression verifies admission recovers.
All checks, including post-take exchange, now use epoch time. No expiry is extended.

## Release boundary

Fable shape ADAPT receipt is in shape-review.md. Exact-head release review must
explicitly verify the author→satellite deletion fence, then CI/deploy/ordinary
fresh browser authorization remain. The already-lost old flow cannot be restored
or replayed. User-approved persistent $0paid/free-only scope is unchanged and
does not imply any fresh authorization was executed by these tests.
