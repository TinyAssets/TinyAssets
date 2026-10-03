---
severity: P2
title: Every daemon deploy still costs about 25s of public 502; zero-downtime needs a switchable origin
filed: '2026-10-01'
summary: 'A deploy stops the old daemon (bounded at 20s since the 2026-10-01 fix) and then boots the new one, and the public surface is down for the whole sequence. Nothing else can serve while that happens: cloudflared (network_mode host) routes to the single port localhost:8001. Zero-downtime needs blue-green: start the new container on a second port, wait until it is healthy, switch the origin, then drain the old one with no deadline pressure.'
---

# Every daemon deploy still costs about 25s of public 502

**Filed:** 2026-10-01
**Verified:** 2026-10-01. `deploy/compose.yml` publishes the daemon only on `127.0.0.1:8001`, and the
tunnel's dashboard ingress is `http://localhost:8001` (compose comments on the `cloudflared` service).
The repro in `docs/audits/2026-10-01-deploy-drain-repro/` measured a 21.4s dead window per recreate
during a long tool call.
**Severity:** P2. The P0 version, 3m16s of 502 per deploy during a turn, was fixed by #4242
(record: `docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md`). Measured after the fix on
2026-10-02: a 27s 502 window with a turn in flight. This file is what remains.

## What is true

- uvicorn closes its listener on SIGTERM, so the old process cannot serve while it drains.
- The new container cannot bind 8001 until the old one is gone.
- So downtime = drain bound (20s with a turn in flight, about 1s without) + boot (about 3 to 12s to the
  first answer).
- **A container swap can end an in-flight turn.** Observed 2026-10-02 at 01:30Z: the
  founder's village turn showed "reply was cut off in transit". Current deploys first run
  `deploy/wait_for_turns.sh` and wait while the live-work probe reports busy. That reduces
  interruptions, but the wait is bounded and yields to recovery; a turn can also begin
  after the final idle poll. A swap that reaches a running turn can still cut it off.
  `agent_turn_reconcile` settles the row truthfully at boot; it does not resume the lost work.
- **Guaranteed turn survival is not reachable by tuning the drain alone.** It needs the
  target architecture's single-execution-owner handover (#4263 S7/S8), so a turn can move
  to, or keep running beside, the new process. Any interrupted turn's user-visible notice
  must say it was interrupted by a deploy, never imply it completed.

## Shape of the fix

1. Bring the new daemon up on a second loopback port (blue/green), with the same data volume. This
   needs the SQLite writer barrier to allow a short overlap, or a read-only warm-up. That is the hard part.
2. Move the origin. Either repoint the tunnel ingress at a stable local proxy that both colours sit
   behind, or switch the dashboard ingress per deploy (dashboard-configured today, so not automatable
   without the API).
3. Drain the old colour with no deadline, so long turns finish. That also closes the remaining half of
   the resolved 2026-08-29 deploy-kills-turns concern (resolved 2026-10-02 by the deploy wait; uptime-and-alarms spec).

## How to resolve

Ship it, then show a production deploy with a probe running against `https://tinyassets.io/mcp` that
records zero non-401 answers. Delete this file then.

## Carried from the resolved 2026-08-29 concern: why a longer drain never saved the reply

That concern was resolved on 2026-10-02: deploys now wait for in-flight work (uptime-and-alarms "A Deploy Waits For In-Flight Work Before It Swaps The Daemon"). Its live proof was deploy run 36979226551 and turn c5264d0a, which completed. The measurement below still governs any drain-based design, so it lives here now.

### PR #4039 review: SIGTERM closes the MCP reply before the turn finishes

**Re-verified:** 2026-09-26, local Windows/Python 3.14 development test;
FastMCP 3.2.0, MCP 1.28.0, uvicorn 0.49.0, sse-starlette 3.4.5.
This is dependency-level evidence, not a production observation.

**Source (verbatim review finding):** A larger Docker stop grace can preserve
worker execution, but does not by itself preserve the served MCP reply.

`tinyassets/universe_server.py:4173` builds the default SSE-response HTTP app.
FastMCP awaits the synchronous tool in an AnyIO worker thread; the MCP session
runner belongs to the lifespan task group. The worker calls the synchronous
converse implementation (`universe_server.py:2866`), the writer
(`universe_intelligence.py:1359,1051`), and `asyncio.run(turn.run())`
(`providers/call.py:116`). The coordinator awaits inference and tools
(`agent_turn_coordinator.py:279,356`); it does not detach these operations.
However, sse-starlette patches uvicorn's exit handler and cancels SSE responses
on shutdown. MCP creates EventSourceResponse without a shutdown-grace override.

Commands, from the repository root:

```
python -u docs/audits/2026-09-26-pr4039-drain-repro.py
python -u docs/audits/2026-09-26-pr4039-drain-repro.py --disable-sse-exit
python -u docs/audits/2026-09-26-pr4039-drain-repro.py --short-timeout
```

The first run disconnected after 0.477s with an incomplete chunked response,
before a two-second tool finished, despite a five-second server grace. The
worker finished at 1.983s. Disabling automatic SSE termination preserved the
result at 1.993s. With a 0.25s uvicorn timeout, the worker/lifespan still ran until
1.981s: uvicorn's request timeout does not bound lifespan shutdown or kill a
synchronous worker. Thus the PR's 290 < 300 comparison is not proof of clean
process exit. The Docker grace remains useful, but the HTTP-lifetime claim is
false. Preserve the reply across SIGTERM and test this transport boundary before
claiming served turns drain successfully.

There is also a first-rollout caveat: Compose v5.1.3
`pkg/compose/convergence.go:621-622` stops the OLD container using the optional CLI
timeout; `cmd/compose/create.go:147-152` returns nil unless `--timeout` was set.
`pkg/compose/create.go:222` writes stop_grace_period into the NEW container's
StopTimeout. `deploy/deploy_fail_safe.sh:329` supplies no timeout override. An old
container created without the setting still gets its old/default stop timeout
during the first rollout. Later recreates use the stored 300s value.

Primary dependency sources:
[SSE shutdown](https://github.com/sysid/sse-starlette/blob/v3.4.5/sse_starlette/sse.py),
[Compose recreate](https://github.com/docker/compose/blob/v5.1.3/pkg/compose/convergence.go#L621),
[Compose creation](https://github.com/docker/compose/blob/v5.1.3/pkg/compose/create.go#L222).

