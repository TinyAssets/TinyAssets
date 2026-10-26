---
severity: P2
title: The outbound broker child failed to start, cause never found
filed: '2026-08-27'
summary: 'was P0; egress verified working 2026-08-31 (the universe opened #2691 through it). The intermittent was never diagnosed. (The `source_code`-nodes-unrunnable claim here was INVERTED 2026-09-01: they run; only the receipt says otherwise)'
---

# The outbound broker child fails to start, and nothing can say why

**Filed:** 2026-08-27
**Severity:** P2 — downgraded from P0 on re-verification (below). The egress path
works; what remains is an undiagnosed intermittent and two unrelated items.
**Re-verified:** 2026-08-31

## Re-verification 2026-08-31

The P0 wording describes a live outage that ended the day it was filed, and the
index row inherited it — so the table has been advertising a total egress
failure that this file itself retracts one section later.

Evidence the egress path works:

* PR **#2691** (`docs: add request rail note to README`, created
  2026-08-29T23:07Z, now MERGED) was opened by the founder's universe through
  `authenticated_external_call` against the GitHub API, uncoached. A PR cannot
  be opened without the outbound path completing.
* `docs/concerns/2026-08-28-a-403-effect-completes-the-run-silently.md` records
  GitHub *answering* an outbound call — a 403 is a reply, which means egress
  reached the far end.

Still open, and the reason this file is not deleted:

1. **The intermittent proxy-start failure was never diagnosed.** It stopped on
   its own. #2596 makes the next occurrence name its cause; nothing prevents it.
2. **`source_code` nodes remain permanently unrunnable.** Re-checked today:
   `mark_approved` (`tinyassets/branches.py:569`) still has **zero** callers
   outside `tests/`, so there is no handle that approves one.
3. Served onboarding copy is still guessed rather than read from the live app.

## UPDATE 2026-08-27, via the webapp as the founder

**The proxy-start failure is gone, and the real blocker is now outside our code.**

Driven through the live SPA at `tinyassets.io/mcp/app` as the signed-in founder
(not the MCP), one prompt: *"lets try the hello world post to twitter again"*.

- Run `948a32670485432a`, terminal status `completed`, `deliver_post: ran`.
- The request **reached X** at `POST https://api.x.com/2/tweets`.
- X returned **403 Forbidden**: the client app lacks OAuth 1.0a write permission.
- Response header: **`x-access-level: read`**.

So the deposited `x:posting` credential is **read-scoped**. The four keys were
minted while the X app's permission level was Read-only.

Credibility check: the SSRF-hardened driver returns `status`, `reason`, a
lowercased `headers` dict and `body` to the caller
(`tinyassets/storage/outbound_connections.py:2168`), so `x-access-level` is a
real response header the universe read, not a hallucination.

**This means the proxy-start failure was intermittent, not permanent** — the same
branch and grant now start the proxy fine. The diagnosability fix (PR #2596)
still matters: the next occurrence will name its cause instead of repeating three
days of "I cannot tell you why". But it is no longer the live blocker.

Next step is a host action. It was **already filed** in `docs/host-actions.md`
(*"X app is Read-only — flip it to Read and Write"*, run `c2b486ff315045c6`)
before this session started; my webapp run reproduced it independently and its
extra evidence is merged into that row. Reading only the top of that file is how
I missed it and briefly filed a duplicate.

---

## The finding

The founder has been trying to post `Hello World` to X since 2026-08-25. It has
never posted. Read from the shared cross-surface thread on universe
`u-01kxm1vszd8hwp7em418asq8h9` (30 turns, via the founder-only
`get_status include_conversation=true` peek).

The blocker has moved four times. Three of the four are fixed:

| Date | Failure | Status |
|---|---|---|
| 08-25 | `node_not_approved` — the static branch's `source_code` node | **open**, see below |
| 08-26 | `permission_denied:provider_not_bound` | fixed |
| 08-27 am | `provider_unavailable` — "provider invocation usage could not be settled" | fixed |
| 08-27 pm | `ProxyRequestError: outbound proxy failed to start` | **open — current blocker** |

The universe isolated the current one correctly and its evidence stands up:
under deploy `44c4e205` it ran `Webhook Channel Test Static v2`
(`4a18e60c0f04494b`) beside `X Hello World via Codex v2` (`a424177413074789`)
and **both** returned the same proxy-start failure. So this is not X-specific,
not the `x:posting` credential, and not the branch packet shape — it is the
shared `authenticated_external_call` egress. Earlier runs of that same webhook
branch had succeeded, so this is a regression, not a never-worked path.

## Why the cause is unknown

`tinyassets/storage/outbound_connections.py` made the failure structurally
undiagnosable, which is why three days produced no diagnosis:

- The spawned child wrapped `_load_dispatch_factory` in a bare
  `except Exception:` and replied with a hardcoded string. The real exception
  was discarded and never logged.
- The parent discarded even that reply, raising one fixed string whether the
  child had *timed out* or *failed*. `worker.exitcode` was never read, so a
  child killed outright (OOM, spawn failure) was indistinguishable from a slow
  one.

Hard Rule 8 (fail loudly, never silently) on the egress path.

## What landed

Branch `claude/outbound-proxy-start-diagnosable`. This does **not** fix the
outage — it makes the next run name its cause:

- The child prints its full traceback to stderr (the container log, host-side)
  and sends only the exception **class** across the wire. Startup runs before
  any credential resolution, so the class carries no credential material; the
  full detail — which includes host paths — stays server-side.
- The parent distinguishes three outcomes: handshake timeout, child-exited
  (with `exitcode`; a negative one is a signal, e.g. `-9` for the OOM killer),
  and child-reported startup failure (with the cause class).
- Handshake budget moved from a hardcoded `5.0` to
  `TINYASSETS_OUTBOUND_PROXY_STARTUP_TIMEOUT_S`, default `30`.

Verified end-to-end through a real spawned child: caller sees
`outbound proxy failed to start: FileExistsError`, traceback in the log.
`tests/test_outbound_proxy_startup_diagnosis.py`, 14 tests; the startup-failure
path previously had none.

## What is still open

1. **The actual cause.** The two candidates in the code are
   `runtime_root.mkdir()` and the `ConnectionLedger` sqlite open inside
   `_build_credential_broker_dispatch`. I could not confirm which — reading the
   droplet needs `~/.ssh/tinyassets_deploy_ed25519`, absent on this machine
   (filed in `docs/host-actions.md`).

   Two hypotheses I checked and **weakened**, recorded so the next session does
   not re-run them:

   - *Cold-import timeout.* The broker module is pure-stdlib; a fresh
     interpreter imports it in ~0.13s locally. The old 5s budget had real
     headroom. Raising it to 30s is insurance, not the diagnosis.
   - *`/data` ownership.* A bind-mounted `/data` would mask the Dockerfile's
     build-time `chown` and deny `mkdir` to uid 1001, which would fit "new grant
     ⇒ new `sha256(grant_id)` runtime dir ⇒ fails, while older grants work" —
     and the universe re-granted consent on every attempt. But
     `deploy/compose.yml:111` mounts the **named volume** `tinyassets-data`, not
     a bind, and a named volume initializes from the image *including*
     ownership. Weak unless the volume predates the current uid.

   **Next step: deploy this, re-run the post, read the named cause.**
2. **`source_code` nodes are permanently unrunnable.** `X Hello World Static`
   (`cc60c0c4f787`) is `runnable: false` on `node_not_approved`, and
   `mark_approved` has no callers — there is no approval handle to reach. The
   universe offered this as its fallback path in its last turn; it will fail
   again.
3. **Served onboarding copy is guessed.** The universe told the founder to tap
   "`Connect / add API connection` — or, if your build still has the older
   label, `Connect subscription`". It is hedging because it does not know the
   live label.
