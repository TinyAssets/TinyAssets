---
severity: P2
title: Fake Codex served-router proof fails in the full Linux shard
filed: '2026-10-04'
summary: The sandbox command proof reports the fake Codex provider unavailable in shard 2 but passes one isolated Linux run.
---

Found while repairing PR #4475 on base `37284a2479`. The full required
Linux Python 3.11.16 shard 2 failed
`tests/test_provider_served_router.py::test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`.
The run used `scripts/linux_oracle.py --required-runner`, CI's heavy-file
exclusions, uid 1001, the successful bubblewrap probe and `/tmp/b` basetemp.

The fake provider exits as unavailable, and `ProviderRouter` raises
`AllProvidersExhaustedError: Served provider 'codex' exhausted; command center
authority forbids fallback widening`. The captured log reports a 120-second
unavailable cooldown. The original report does not expose the subprocess
failure excerpt, so the underlying cause is not established.

One isolated Linux oracle run of this exact case alongside
`tests/test_app_pending_requests_browser.py` passed all five cases in 10
seconds. Keep the original full-shard failure: diagnose test-order state or
environment dependence before changing production routing or the sandbox
guard. Neither this test nor provider code changed in the CI repair. No
quarantine, retry substitution or assertion weakening was applied.
