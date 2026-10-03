# Frontend blue-green behind a local switch (target-architecture S8.7/S8.8)

**Status:** proposed. Owner of the surrounding slice: turn-handover (S7/S8). This note specifies
only the **traffic switch** for frontends. It builds no turn handover and no execution owner.
**Closes, when shipped:** `docs/concerns/2026-10-01-deploys-are-not-zero-downtime.md`.

## Where we are (measured)

- Every daemon deploy is a stop-then-start on one port. On 2026-10-02 #4242's deploy measured a
  **27 s** public 502 window: a 20 s bounded drain plus about 6 s of boot
  (`docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md`).
- cloudflared runs with `network_mode: host`, and its dashboard ingress is `http://localhost:8001`.
  The daemon publishes `127.0.0.1:8001`. Nothing else can serve until the old container is gone.
- The old process cannot keep serving while it drains. uvicorn closes its listener on SIGTERM,
  and sse-starlette cancels open SSE streams as shutdown starts
  (`docs/audits/2026-09-26-pr4039-drain-repro.py`).

## Why not now: two daemons would be two writers

Today one process is both frontend and execution owner: the scheduler, the assigned-queue
consumer, the turn journal and reconcile. Running an old and a new daemon side by side, even for
seconds, runs two schedulers and two consumers over the same SQLite stores. target-architecture
S8 is explicit: the owner/frontend split "must land before any second writer exists".

**So frontend blue-green depends on C1a** (turn-handover, branch `spec/execution-owner-lease`, "C1
design addendum"). C1a puts the owner behind a Unix socket and ships a separate frontend image.
Until it lands, the honest state is the measured stop/start window. No interim blue-green is
proposed, because any overlap before the split is a double-writer bug.

**What a frontend is (C1 revision 3, 2026-10-02):** the daemon image run with a
`tinyassets.frontend` entrypoint. It is a plain reverse proxy that also renders the app shell
with the owner's own renderer, which keeps the CSP nonce and config injection. It does not
authenticate and holds no key. Everything else goes byte-for-byte to the owner over
`/run/tinyassets/owner.sock`. The owner keeps today's whole server: FastMCP, tools, sessions and
turns.

## The switch

A small reverse proxy takes over `127.0.0.1:8001`, the port cloudflared already targets, so the
dashboard ingress does not change. It forwards to whichever frontend colour is live:

```
cloudflared (host net) --> 127.0.0.1:8001  local switch (HAProxy, host net)
                                              |-- 127.0.0.1:8011  frontend-blue
                                              '-- 127.0.0.1:8012  frontend-green
                           frontends --unix socket, byte-for-byte--> owner (today's server)
```

**Choice: HAProxy**, over Caddy, Envoy and a SO_REUSEPORT trick.
- Its runtime API (`set server ... state drain|ready` over a local admin socket) moves new
  connections between colours **without a reload**. Established connections, including
  long-lived SSE streams, stay on the old colour until they end. That is exactly D11's "an old
  frontend keeps its open client connections until those streams end".
- It health-checks both colours and never routes to a colour that is not ready.
- A reload is needed only to change the topology, which is rare. Its config is validated
  (`haproxy -c`) before any reload, so a bad config cannot take the port.
- **SO_REUSEPORT was considered and rejected.** Both uvicorns would bind 8001 and the kernel would
  share new connections between them. But the old process still has to shut down to stop taking
  traffic, and shutdown cancels its SSE streams. The streams are the thing a frontend deploy must
  not break.
- Cost: one official `haproxy` image pinned by digest, about 10-20 MB RSS, no spend.

## Deploy protocol (frontend-only deploy)

1. **Start the idle colour**, say green on 8012, with the new image. Two different checks apply:
   - The **switch's continuous health check** is the frontend's own shallow `/healthz` (C1-8).
     If the owner restarts, the frontends answer with an honest "restarting" error and `/healthz`
     stays up, so HAProxy keeps the colour in. The client gets that error, never a 502, and the
     switch never marks both colours down because of the owner.
   - The **one-time deploy readiness gate** is a proxied `/mcp/pulse` round trip through the new
     colour to the owner socket, using the canary bearer. It proves that colour actually reaches
     the owner before it takes traffic.

   The colour owns nothing, and it stamps every response `X-TA-Frontend: <colour>/<sha>`.
2. **Health gate:** the switch's health check, plus a loopback canary through 8012 directly
   (`mcp_public_canary.py --url http://127.0.0.1:8012/mcp`, as the canary principal). It asserts
   `X-TA-Frontend: green/<new sha>`, because `/mcp/pulse` alone cannot tell the colours apart:
   both reach the same owner. On failure: stop green, leave blue alone. That is a failed deploy
   with zero user impact, and no rollback is needed.
3. **Switch:** `set server be/green state ready` then `set server be/blue state drain`. New
   requests go to green at once, and blue keeps its open streams.
4. **Drain blue, with no automatic cap:** blue is out of the pool for new connections, but stays
   alive until its last proxied stream ends. Only then is it stopped. An explicit operator stop is
   the only thing that may cut it short.

   The earlier draft capped this at 10 minutes. That was wrong. MCP response streams are not
   resumable: there is no event store, and sse-starlette cancels the response on disconnect.
   So a cut stream loses its reply, even though the tool keeps running in the owner (C1 shape
   review; the same reasoning as the lead's condition 4). The deploy reports
   "blue still draining: N streams" and does not block the next deploy, which drains blue and
   green alike.
5. **Verify through the public path:** `mcp_public_canary.py --url https://tinyassets.io/mcp
   --assert-handles` and `deployed_sha.py --assert-contains`.
6. **Rollback** at any point before blue is stopped is one API call (`blue ready`,
   `green drain`). After blue is stopped, rollback is the same protocol run in reverse with the
   previous image.

**Owner changes are not frontend deploys.** Owner deploys keep phase 1's whole-process wait (the
in-flight-turn wait in deploy-prod). Owner handover and frontend queueing are **deferred** (C1).
This switch is not involved in an owner deploy. Which path a change takes is decided from the
diff (C1-8): frontend-only diffs take this switch, owner diffs take phase 1's wait, and a mixed
diff runs the owner first, then the frontend switch.

## Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| The owner restarts | requests fail with an honest "restarting" error | frontends' `/healthz` stays up, so no colour is dropped and no 502 is served; the watchdogs' restart action targets the owner unit only (C1-8) |
| The switch process dies | port 8001 refuses, public 502 | `restart: unless-stopped`; the watchdog probes 8001 through it; the proxy restarts in well under a second; its state lives in the config plus a server-state file |
| The admin socket is reachable by a tenant | traffic hijack | a unix socket, root-owned 0600, on the host only; never in a jail bind |
| Both colours unhealthy | 502 | the same as today's failed boot; the deploy stops at step 2, before blue is touched |
| A colour/port mix-up | the new image never gets traffic | step 5 asserts the public `deployed_sha` matches the new image, not just green |
| Blue still has streams when the next deploy starts | two colours draining at once | allowed: drain is per colour; the deploy reports both counts and never cuts a stream automatically |

## Tasks: a separate change, landing in either order with C1a (deploy-incident lane)

Agreed with turn-handover 2026-10-02: one owner per change. C1a delivers the owner socket, the
frontend image and its compose services. The switch ships as its own change.

1. A `switch` service in `deploy/compose.yml` (haproxy, host net, binds 127.0.0.1:8001). Frontend
   services `frontend-blue`/`frontend-green` on 8011/8012. The daemon stops publishing 8001.
2. `deploy/haproxy.cfg`: one backend, two servers; the health check is each frontend's shallow
   `/healthz`; the admin socket; `load-server-state-from-file`; and **`retry-on none`**. Forwarded
   requests are never retried, because MCP ids are not idempotency keys.
3. `deploy_fail_safe.sh`: the colour protocol above for frontend-only deploys. The
   host-mutation lock covers the whole sequence.
4. Watchdogs probe through 8001 (unchanged) and stand down while the lock is held (already true
   since #4242).
5. A scripted deploy loop with a request-level error probe (S8.10): 0 failed requests.

## Founder decisions

None. No spend, and no user-visible change beyond fewer 502s.
