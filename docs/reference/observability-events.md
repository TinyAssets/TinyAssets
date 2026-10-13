# Session trail

Logs exist to reconstruct what happened to one person, and why. The unit is
the session. A request is one step inside it.

Every line is one JSON object on the `tinyassets.events` logger (stdout, message
unchanged). Vector already forwards that stdout into the host journal. Query
the journal with `journalctl CONTAINER_NAME=tinyassets-logs`. There is no second
pipeline. A Vector line may wrap the object in `log` or `message`; the reader
unwraps that.

## Event

`schema` is `tinyassets.event.v1`.

| Field | Meaning |
|---|---|
| `ts` | UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| `event` | `session.step`, `session.link`, `dependency.call`, `client.error`, `process.startup`, `process.crash` |
| `session_id` | Stable for one journey. Null on process events |
| `trace_id` | One HTTP request. Response header `X-Trace-Id` |
| `commit` | `TINYASSETS_BUILD_SHA` baked into the image, else the deploy receipt, else `unknown` |
| `context` | `client`, `account_id`, `signed_in`, `token_age_s`, `token_exp`, `token_iss` |
| `step` | Where the person is in the journey |
| `http` | `method`, `route`, `status`, `duration_ms`, `user_agent` |
| `dependency` | `kind` (`workos`, `stripe`, `db`, `other`), `target`, `status`, `duration_ms`, `error`, and `operation` for SQL |
| `error` | `type` and `stack`. A browser report keeps the error type and its top frames; the free-text message is dropped, not redacted |
| `links` | Other ids seen on this request |
| `tool` | MCP tool name, when it is a slug |
| `page` | Browser path, query stripped |

`client` is `claude`, `chatgpt`, `browser`, or `other`, from the user agent and
MCP `initialize` `clientInfo.name`.

`account_id` is the verified principal id when it is opaque. Emails, names, and
anything containing `@` are dropped. `signed_in` can still be true.

Token facts are `iss`, `iat`, and `exp` peeked from a bearer. The bearer itself,
`sub`, and any other claim are not recorded. A rejected token still records
age and issuer, so a 401 on `tools/list` is diagnosable.

## Session and links

The primary `session_id` is the `ta_session` cookie (`s_` plus 32 hex), else
`X-Session-Id` in that shape, else an `mcp-session-id` that is not a JWT, else
a new cookie. The cookie is not a credential. It is not HttpOnly, because the
site script has to send it. `SameSite=Lax`, 30 days, `Secure` only on https.

`links` joins what showed up on the same request:

| Link | Value |
|---|---|
| `cookie` | The browser cookie |
| `mcp_session` | The MCP session id |
| `oauth_state` | `o_` plus 16 hex of SHA-256. The raw `state` is a CSRF secret and is never stored |
| `checkout` | `c_` plus 16 hex of SHA-256 of the checkout attempt id |

The reader walks those links inside the requested window. Asking for a trace
id returns the whole session that contains that request, not the one line.

## Steps

Page loads, each OAuth hop (`oauth.protected_resource`,
`oauth.authorization_server`, `oauth.authorize`, `oauth.callback`,
`oauth.token`, `oauth.refresh`), MCP `initialize` / `tools/list` / tool call,
and checkout (`checkout.open`, `checkout.paid`, `checkout.cancel`,
`checkout.expired`, `checkout.lapsed`, `checkout.webhook`). `http.preflight`
is an OPTIONS request. Anything else on the daemon is `http`.

The marketing site is static, so the daemon never sees its HTML GET. The site
beacons `page.load` to `POST /app/client-error`, and the same endpoint accepts
`window.error`, `unhandledrejection`, and `react` reports. That path is public.
The body is at most 4 KiB, enforced while it streams. The session is the
caller's own `ta_session` cookie, or the one minted for that request; a
`session_id` in the body is ignored. The rate limit is 30 reports per client
address per minute, and the response is empty. It does not replace
`/app/trace`.

## Dependencies

WorkOS (JWKS and AuthKit metadata), Stripe, and SQLite. The target is the
host and path for HTTP, `stripe:<first segment>` for Stripe, and
`sqlite:<basename>` plus the SQL verb for the database. Statement text and
parameters are not logged. Database calls are recorded during a request, and
any failed statement is recorded even in the background.

## Redaction

Emails, `Authorization` values, bearer and basic credentials, JWTs, `Cookie`
and `Set-Cookie` values, cookie-shaped `name=value` tokens, Stripe keys and
object ids (`cus_`, `pi_`, `sub_`, ...), card-number-shaped digit runs,
80-character tokens, URL query strings, and
`code` / `token` / `state` / `client_secret` / `password` parameters are
removed before the line is written and again before a fetched line is printed.
A 40-hex commit is kept. Stack traces are capped at 8000 characters. Routes
replace the segment after `hooks` with `{token}` and any other non-slug
segment with `{id}`.

## Retention

`deploy/journald-tinyassets.conf` sets `MaxRetentionSec=30day` and
`SystemMaxUse=12G`. The age is a maximum, not a promise. Whichever bound runs
out first wins. At 100 KiB/s, 12 GiB is about 35 hours. Three days at that
rate is about 25 GiB. The production disk is 49 GiB and was 40% full on
2026-10-01, and `disk_watch` alarms at 80%, so 25 GiB does not fit. 12G keeps
a full journal near 65% in that reading.

## Reading

Workflow **Fetch production logs** (`workflow_dispatch` or
`repository_dispatch` type `fetch-prod-logs`). Inputs: `since`, `until`
(required, UTC, at most 7 days), and optional `session_id`, `account_id`,
`trace_id`, `grep`. `grep` is a literal substring applied after the trail is
joined. The run uploads `production-log-timeline` and prints
`TINYASSETS_SUMMARY` with error types counted by commit.

The workflow uses the existing deploy secret `DO_SSH_KEY`. It copies the
checked-in `deploy/read-prod-logs.py` into a fresh directory on the host and
runs `python3` on that file. The only remote executable is `python3`. Query
fields are checked against fixed patterns before they are sent, and each
argument is shell-quoted, so a field cannot change the command. The script
then runs `journalctl` against `CONTAINER_NAME=tinyassets-logs`. The fetch
does not write host config and does not restart anything. Its concurrency
group is `production-host-observation`.

Optional later hardening, not required to fetch: Install host services can
add a dedicated key whose `authorized_keys` line is
`command="/usr/local/libexec/tinyassets-read-logs"` with port forwarding, agent
forwarding, X11, pty, and user rc disabled. That line is written only when
variable `DO_LOG_READ_SSH_PUBKEY` is already set. With the variable unset the
installer copies the script and leaves `authorized_keys` alone. Under that
forced command the script reads `SSH_ORIGINAL_COMMAND` and does not execute
it. Fetch production logs does not use that key.

## Later counts and alerts

Funnel counts read these steps. They do not need a second event.

| Funnel | Step |
|---|---|
| visit | `page.load` |
| signup start | `oauth.authorize` |
| signup finish | `oauth.token`, or `signed_in` becoming true |
| checkout | `checkout.open` |
| paid | `checkout.paid` |
| cancel | `checkout.cancel` |

`checkout.expired` and `checkout.lapsed` are Stripe's outcomes. A new-error
alert groups `error.type` by `commit`.

## 2026-10-12 tool discovery

A fresh token can fail tool discovery before this process writes a line.

RFC 9728 inserts the resource path, so a resource of
`https://tinyassets.io/mcp` is looked up at
`https://tinyassets.io/.well-known/oauth-protected-resource/mcp`. The daemon
does not mount that path. It mounts the root and `/mcp`-prefixed metadata
documents only. The worker proxies `/mcp` and `/app`, so the inserted path
falls through to the static site, which answers 404 or 405.
`https://tinyassets.io/.well-known/oauth-authorization-server` is mounted on
the daemon and is not proxied, so the static site answers that one too.
`/mcp/.well-known/oauth-protected-resource` and
`/mcp/.well-known/oauth-authorization-server` are mounted and proxied. The
401 `resource_metadata` URL is the `/mcp` document when `WORKOS_MCP_RESOURCE`
is `https://tinyassets.io/mcp`. A client that ignores `resource_metadata` and
uses path insertion never sees the document.

The worker proxies OPTIONS and injects the Cloudflare Access service token.
The app does not add CORS headers. An edge 403 never reaches the origin, so
it will not appear in this trail. A preflight that does reach the origin can
still lack `Access-Control-Allow-Origin`. Either one fails a browser connector
immediately after sign-in while the token is valid.

Routing those discovery paths through the worker, mounting the path-inserted
document, and adding CORS on `/mcp` and the well-known routes are a separate
change. This trail does not do that work.

If the `tools/list` request reaches the app, this trail records the step, the
HTTP status, the token age, and the issuer. It does not change discovery.
Audience is checked against the canonical resource after PyJWT, so a trailing
slash or a capitalised host is not a false rejection by itself. A JWKS
failure, a bad issuer, or an expiry still rejects the token.
