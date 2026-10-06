# L7 implementation evidence

## First slice: RED component control

Verified on Linux oracle (Python 3.11.16, uid 1001, Chromium sandbox enabled).
`tests/test_deploy_during_traffic.py`: 1 passed, no skips. The test passes only
when the continuity invariant is RED: a send encounters connection refusal
after SIGTERM closes the origin listener, and an active real FastMCP response
ends with `RemoteProtocolError` without a terminal reply. One exclusive effect
file and the workspace update survive. The browser preserves its unsent draft.

The isolated edge explicitly maps upstream transport failure to 520. This is
not a measurement of Cloudflare's mapping. The page and deterministic MCP tool
are fixtures, not the production application/provider. Source digests, HTTP,
turn and effect evidence are in `l7-red-baseline.json`; screenshots and server
logs are emitted as CI artifacts. No production requests or changes occurred.

Command:

```powershell
$env:MSYS_NO_PATHCONV='1'
python scripts/linux_oracle.py --out C:/Users/Jonathan/AppData/Local/Temp/l7-evidence --env DEPLOY_TRAFFIC_EVIDENCE=/out -- -q tests/test_deploy_during_traffic.py --basetemp /tmp/b
```

The new draft-PR workflow runs this component oracle without skipping drafts.
It is not yet the complete release gate: task 1.1 remains unchecked pending
real Compose old/new image digests, full app and multi-surface traffic,
ownership/resource evidence, and enforcement before production rollout.
The existing production deploy workflow and ingress are unchanged.
