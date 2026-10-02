---
severity: P2
title: 'Long `converse` responses: the origin pings every 15s; delivery through the tunnel is unproven'
filed: '2026-08-28'
summary: the "silent stream" premise was false (`tests/test_mcp_sse_keepalive.py`); whether long turns are still cut end to end, and by what, is open until a >3-minute live `converse` is captured
---

# A long `converse` response: the origin pings every 15s; delivery through the tunnel is unproven

**Filed:** 2026-08-28. **Premise corrected:** 2026-08-29.
**Verified:** the ORIGIN side is proven (`tests/test_mcp_sse_keepalive.py`); the
end-to-end side is not — that is the part that stays open.
**Severity:** P2 for the transport, but see 2026-08-31: the client can report "the reply did not arrive" for a turn that COMPLETED and already asked the user a question — the client survives a cut stream (PR #2640: it invalidates
the session, reports a sentence, offers **Send again**); this is "find out
whether long turns are still being cut, and by what".

## What was claimed, and what is actually true

The original filing said a `converse` tool call is answered over an SSE stream
that "sends nothing at all until the result frame", so an intermediary would
cut a minutes-long turn. That premise is **false**:

- fastmcp 3 hands an accepted tool call to the MCP SDK's
  `StreamableHTTPServerTransport`, which answers with
  `sse_starlette.EventSourceResponse(content, data_sender_callable, headers)`
  — `ping` unset — and sse-starlette's default is a `: ping - <timestamp>`
  comment every **15 seconds**, written by a task that runs alongside the
  SDK's SSE writer while it waits for the tool result. The synchronous
  `converse` runs in FastMCP's threadpool, so it does not block the ping.
- `tests/test_mcp_sse_keepalive.py` drives `create_streamable_http_app()`
  (the production construction, with its own pure-ASGI middleware stack) with a
  tool that outlives the scaled ping interval and reads two or more pings
  before the result frame. It proves the origin emits them, under the
  packages a local run resolves (`fastmcp>=3.0` floats; production on
  2026-08-29 ran mcp 1.29.1 / fastmcp 3.4.7 / sse-starlette 3.4.8) — it does
  NOT prove delivery through `uvicorn` → `cloudflared` → Cloudflare → the
  Worker → the client, because Starlette's `TestClient` buffers the body.
- Nothing in the repo buffers the response: both `/mcp` middlewares are
  pure-ASGI pass-throughs, and `deploy/cloudflare-worker/worker.js` returns the
  upstream `ReadableStream` directly. Cloudflare documents that Tunnel streams
  `text/event-stream`, Workers have no duration limit while the client stays
  connected, and 15s is well under the 125s proxy read timeout. A live
  `initialize` on 2026-08-29 answered `Content-Type: text/event-stream` with
  `Cache-Control: no-cache, no-transform`. (Codex review, 2026-08-29.)

## What is still open

1. **Whether long turns are still being cut end to end.** Nothing above is a
   wire capture of a >3-minute authenticated `converse` through
   `https://tinyassets.io/mcp`. Until one exists, the 2026-08-28 "200 whose
   body ends mid-frame" symptom has no established cause: a deploy recreating
   the container under the open response is one candidate
   (the resolved 2026-08-29 deploy-kills-turns concern (resolved 2026-10-02 by the deploy wait; uptime-and-alarms spec)),
   an intermediary is another; PR #2640 recorded three separate observations
   (a 503 mid-deploy, a cold-load failure, a cut stream) and said
   contemporaneous wire evidence was still required.
2. **`json_response` is settings-driven.** The app omits the argument, so
   FastMCP settings (`FASTMCP_JSON_RESPONSE`) decide the mode; a JSON response
   has no stream to ping. The test asserts the response's content type at
   runtime; keep that guard, not a source-string one.

## Observed again 2026-08-31: two cuts, and only ONE of them was a deploy

Both happened during the workspace acceptance test on the founder's universe,
minutes apart, and the container's `StartedAt` separates them — which is the
discriminator this file asked for.

**Cut 1, ~07:00Z — the deploy.** `deploy-prod` for `f47c01e2` ran
06:59:22Z–07:02:01Z and the daemon's `StartedAt` is **07:01:37Z**. The turn in
flight died with it; the client said *"That didn't get through — the reply was
cut off in transit."* This is
the resolved 2026-08-29 deploy-kills-turns concern, third recorded
occurrence, now with a container timestamp attached.

**Cut 2, ~07:04Z — NOT a deploy.** The next message was sent after the daemon
reported healthy. No deploy ran after 07:02:01Z and `StartedAt` stayed
07:01:37Z for the rest of the window, so the container never moved. The client
reported *"This message was never confirmed — the reply did not arrive."*

**The origin finished the work anyway.** That turn completed server-side: it
produced its reply (~270–360s after the send, per the turn watcher) and
created a request-rail item, `Compile-check path for README fix`, which was
present and answerable in the UI while the thread still showed the
never-confirmed notice. So for cut 2 the work was **not** lost — only its
delivery — which is the opposite of the deploy case and needs a different fix.

### What this excludes, and what it does not

* The deploy candidate is **excluded for cut 2**: the container did not restart.
* A turn of roughly 4–6 minutes is therefore being cut somewhere between the
  origin and the browser while the origin is still working.
* NOT established: whether `: ping` comments were arriving on that stream, and
  which hop dropped it. No raw response capture and no tunnel log for the
  request id were taken — the run was a live acceptance test and re-running it
  to capture bytes would have cost the test. That capture is still the missing
  evidence.

### What the user sees, which is its own defect

The thread showed "never confirmed / the reply did not arrive" for a turn that
had in fact completed and had already asked the user a question in the rail.
A client that says nothing arrived, next to a rail item proving something did,
teaches the user to distrust the surface. Whatever the transport cause, the
client's reconciliation on reconnect should prefer the stored turn over its own
optimistic verdict — see [[webapp-send-is-not-proof-of-delivery]] for the
mirror-image failure.

## 2026-09-30: the client's own silence bound EXCLUDES itself, by error class

Found while diagnosing a different turn
(`c7d6279d4af74d798375d3f13780140e`, PR #4108, after a Codex refute round).

The client does bound a long stream, which this file had not recorded: the body
reader is `await this._bound(reader.read(), ...silence())`
(`tinyassets/onboarding/app.html:1120`), a bound on the gap BETWEEN chunks
rather than on total duration, set to `SILENCE_MS: 120000`
(`app.html:1003`) — 120s against an origin that pings every 15s.

**But it is not what the recorded incidents saw, and the error class proves it.**
The silence bound raises `stream_silent` with the words *"your universe stopped
sending anything back; it may still be working on this"* (`app.html:1101-1102`).
Every cut recorded above reported `stream_truncated` — *"the reply was cut off
in transit"* — which is raised only where the body ENDED, or arrived corrupt,
without this request's answer (`app.html:1080-1085`, and the stream-died path at
`app.html:1319-1321`).

So this is a discriminator the earlier entries lacked:

* `stream_silent` → nothing arrived for 120s. Pings stopped or were stripped.
* `stream_truncated` → bytes STOPPED, mid-stream, before the answer. A closed
  connection, not a quiet one.

Every incident in this file is the second kind. That **rules the ping question
out as their cause** — a stream still pinging and then cut is cut by something
closing it, which keeps the deploy candidate and an intermediary in play and
removes "the origin went quiet" from the list. The origin-side ping proof
(`tests/test_mcp_sse_keepalive.py`) was therefore never the missing evidence for
these cuts.

Resolving evidence should now record WHICH of the two classes the client
reported, since they point at different causes.

## How to resolve this file

Delete it when one authenticated `converse` longer than 3 minutes completes
over the live connector without the client reporting `stream_truncated`, with
the evidence stamped: date, the turn's duration, the surface it ran on, and
(if captured) the raw response showing the `: ping` comments arriving. If such
a turn IS cut, replace this file with the cause — the tunnel log for that
request id and the container's `StartedAt` decide between the two candidates.
