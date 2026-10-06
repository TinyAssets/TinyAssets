---
severity: floor
title: L11 git credential route requires Linux proof before merge
filed: '2026-10-05'
summary: Docker Linux engine is unavailable; the new authority-bearing git route is an unverified draft and must not be deployed.
---

Lane: `feat/agent-box-git-credentials`, stacked on `feat/per-role-uid-split`.

The requested Linux oracle failed three times: unavailable
`dockerDesktopLinuxEngine` pipe, container wait `unexpected EOF`, then unavailable
pipe again. Both a hidden Docker Desktop launch and `docker desktop start`
were attempted. Per AGENTS.md loop item 7, hand off the engine failure; do not
treat Windows checks, collection, or skipped tests as the Linux proof.

Resume with a working Linux Docker engine:

```powershell
$env:MSYS_NO_PATHCONV='1'
python scripts/linux_oracle.py -- -q tests/test_agent_git_credentials.py tests/test_broker_server.py tests/test_universe_egress.py --basetemp /tmp/b
```

Then run affected outbound/broker, jail, role and heavy-file tests. The synthetic
fixture is implemented but has not executed. Verify clone/fetch/push and binary
pack integrity, read-only/wrong-owner/repo/host/incarnation refusals, active
revocation and backpressure, restart fencing, response secret scans, and box
files/environment/argv/output non-exposure before enabling or merging this route.
No real repository may be used as a credential probe. No deployment, real-user
pass or as-built spec completion is claimed. D72 local git_bridge remains intact.
