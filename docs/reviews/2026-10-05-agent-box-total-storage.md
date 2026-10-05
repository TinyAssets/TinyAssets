# Agent box total-storage correction (PR #4485)

Founder finding: 2026-10-05 12:21 PDT, main account. A complete clone stopped
near 18 MB with a notice granting at most 16 MiB per call, despite available
storage. Scope: continue `fix/agent-box-dev-workflow`; no credential work or
production deployment is included in this correction.

## Source and replacement

At parent `3c0455c344eb612c64f20adb5c847b3133fc903c`:
- `tinyassets/jail_disk.py:57`: 1 GiB `LAUNCH_BYTES_CAP`.
- `tinyassets/jail_disk.py:63`: 16 MiB `GRACE_BYTES`.
- `tinyassets/jail_disk.py:263`: launch reserves fitted account headroom; a
  nested/concurrent launch falls to grace when active reservations consume it.
- `tinyassets/jail_disk.py:276,312`: per-call 16 MiB notice.
- `tinyassets/universe_tools.py:228,258`: 32 MiB `RLIMIT_FSIZE`.
- `tinyassets/providers/provider_jail.py:709`: 1 GiB `RLIMIT_FSIZE`.

The notice identifies the fallback path; without production ledger telemetry
we cannot distinguish reservation pressure from unavailable accounting for
that particular call. Both fixed fallback paths are removed.

Jails now observe total owner storage with ordinary gated-write reservations
still included. They do not reserve unused capacity. All owned jail-writable
stores are remeasured, other stores use the existing dirty/stale protocol,
and account scans are coalesced across nested/concurrent supervisors. Admission
and exit force a current measurement. Full accounts can delete files; their
initial total may not grow and ratchets down toward quota. Accounting failures
refuse admission or stop the running process, without a write allowance.

Shared-volume byte/inode floors remain unchanged. This remains polling-based,
not a filesystem project quota: an overshoot may remain on disk after stopping.
A completed over-quota command explicitly says writes landed. No production
filesystem quota or synchronous aggregate allocation guarantee is claimed.
The separate node-execution sandbox is not the served agent box path.

## Cross-family review (peer-agents, Claude)

One read-only round, exit 0, `VERDICT: ADAPT`.

1. **DISAGREE_EVIDENCE** with reintroducing a file-size limit derived from free
   volume bytes. A per-file ceiling is not a total-storage floor and can refuse
   an append to an existing large file despite available total storage. The
   pre-existing fixed limit also allowed multiple allocations to cross the
   floor before a poll. The new Linux probe applies the OLD 32 MiB limit to
   two 32 MiB allocations against 48 MiB available above a test floor; aggregate
   supervision still detects the breach. Preserve the total-volume guards and
   document their existing polling limitation instead of claiming an rlimit
   supplies synchronous total-volume enforcement.
2. **AGREE** that uncoalesced whole-account walks can multiply work. Added a
   bounded cache and per-account lock: nested supervisors share scans for
   0.5 seconds. Only jail-writable stores are always walked; other stores use
   their dirty/stale state. Keeping all owned writable homes in the scan
   preserves detection even when another supervisor is idle. A positive
   headroom crossing bypasses the cache so suspected overflow is checked at
   the existing supervisor poll; zero-headroom volume activity remains coalesced.
3. **AGREE** that an exit-time breach must not claim a kill. Tool/provider
   results now distinguish completed writes from a running process stopped.

## Test contract changes

No test identities are removed, skipped, or marked xfail. Existing identities
mentioning launch caps, reservation headroom, or grace are retained for hygiene,
but their assertions now encode shared total storage. Checked-renewal tests
still exercise the unchanged ledger reservation API directly, since jails no
longer own reservations. Fixed FSIZE assertions now expect its absence.
Former 24 MiB launch-cap jail probes now use a REAL 24 MiB account quota;
their overflow/stop and isolation assertions remain. Full-account grace
assertions become stricter: zero new growth, cleanup still allowed. Total quota,
shared-volume, runtime accounting and isolation checks are not weakened.

## Verification

- Final Linux oracle: **425 passed, zero skips**, 162.17 seconds, uid 1001,
  Python 3.11.16, git 2.47.3, bwrap 0.12.0. Includes real tool/provider jails,
  egress, storage accounting, the full
  clone, 1,200 MiB one-call success, 2,100 MiB quota refusal, and affected heavy
  `test_provider_retry.py` / `test_provider_work_authority.py`.
- Earlier broad oracle: 309 passed, no skips; final run includes added boundary
  proofs and scan coalescing. The public network clone is explicitly enabled.
- Windows storage suites: 44 passed, one POSIX-only skip (not Linux proof).
  An earlier broader Windows probe hit Windows-path rejection in the unchanged
  POSIX mount-view test; that test passes in the Linux oracle.
- Ruff clean; all 601 canonical files mirror-matched; storage-runtime-accounting
  spec strict validation passes. Staged diff has no whitespace errors.
- Final commit-to-base hygiene counts are recorded in the PR body. No gate or
  test exclusion files changed. No deployment or real-user app pass is claimed.

Oracle command:

```text
python scripts/linux_oracle.py --env TA_DEV_PUBLIC_PROBE=1 -- tests/test_agent_box_dev_workflow.py tests/test_jail_disk.py tests/test_jail_admission_headroom.py tests/test_universe_tools.py tests/test_universe_tools_jail.py tests/test_provider_universe_jail.py tests/test_provider_jail_policy.py tests/test_provider_jail_network.py tests/test_storage_accounting.py tests/test_storage_slice_gates.py tests/test_agent_loop_box_tools.py tests/test_universe_egress.py tests/test_provider_retry.py tests/test_provider_work_authority.py -q --basetemp /tmp/b
```
