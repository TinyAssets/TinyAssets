# stage-logs as built (2026-11-23)

`untangle/stage-logs` gives the deploy kit a read-only mode, `logs`, that prints the platform
Workers' error and warn lines of the last minutes from Workers Logs. The live stage failed
twice with causes only those logs hold: a sign-in refused `sandbox_start_failed` (the sandbox
Worker's `control.ts` `#launch` logs why), and a run settled `unknown` (the owner's
`sandbox.ts` logs "start did not answer"). Every Worker's `wrangler.jsonc` turns `observability`
on, but nothing in the kit could read it, and the coordinator holds no Cloudflare token: it can
only dispatch the workflow.

## Research (Fact 14)

- **The query.** `POST /accounts/{account_id}/workers/observability/telemetry/query`
  (Cloudflare API, `telemetry.query`:
  developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/;
  read here from Cloudflare's own OpenAPI schema, `cloudflare/api-schemas` `openapi.yaml`, and
  the `cloudflare` Python SDK 5.10.0's generated types, 2026-11-23: the docs host is not
  reachable from this session).
  - Body: `queryId` (any id for an ad-hoc query), `timeframe` `{from, to}` in Unix ms, `view`
    `events`, `limit` (default 50, at most 2000), `dry` (run without keeping the run), and
    `parameters`: `datasets`, `filters` (leaves `{key, operation, type, value}`, and groups
    `{kind: "group", filterCombination, filters}`, nesting at most 4), `filterCombination`,
    and `needle` (full-text, any field, case-insensitive by default).
  - Answer: the v4 envelope; `result.events.events[]`, each with `timestamp` (ms), `source`
    (the raw payload, string or object, as logged), `$metadata` (`service`: the script name;
    `level`; `message`; `error`: present on an error), `$workers` (`scriptName`, `outcome`,
    ...), and `result.events.count`, the whole match.
  - Cloudflare's own observability MCP server sends the same query with `dry: true` and
    filters a Worker by `$metadata.service` (`cloudflare/mcp-server-cloudflare`,
    `apps/workers-observability`).
  - Workers Logs keep 7 days on Workers Paid, 3 on Free
    (developers.cloudflare.com/workers/observability/logs/workers-logs/, "Limits"); the
    account is on Paid.
- **The token's permission: the founder must add one.** The schema's `x-api-token-group` for
  `telemetry.query` (and for `keys`, `values` and `live-tail`) lists one group: **Workers
  Observability Write** (the dashboard's Account, Workers Observability, Edit). Not Workers
  Scripts Write, not Account Analytics Read, and not Workers Observability Read, which only
  the newer `agents`, `issues` and `destinations` reads take. The deploy token holds Workers
  Scripts Write, Workers KV, R2, Queues, Routes, DNS, Zone WAF and Account Analytics Read, so
  it lacks it. Account Analytics Read reaches the GraphQL analytics (invocation counts and
  status), not log lines.
- **`wrangler tail` is no fallback.** Its `POST .../workers/scripts/{script}/tails` takes
  Workers Tail Read or Workers Scripts Write, which the token holds, but a tail streams only
  what is logged after it opens. The stage's failures are past, so a tail can't read them; it
  would also need a job held open while someone reproduces the failure.

## The problems found

- Nothing in the kit reads what the Workers log. The only way to read a failure's cause was
  the dashboard, which the coordinator can't reach and the founder must open by hand.
- The workflow's `apply` job names the one read-only mode (`inputs.mode != 'plan'`): a copy of
  the row's `applies: false`. The guard (`deploy.test.ts`, "applies each row that applies")
  holds it to the table, so a second read-only mode is one more name there, held the same way.

## The fix

1. **`deploy/logs.ts`** builds the query (the Workers by `$metadata.service`, and a line logged
   at `error` or `warn` or carrying `$metadata.error`, an uncaught exception; `needle` for
   `--filter`; `dry`), reads its answer, and prints each event oldest first: time, Worker (its
   folder), level and message, then its error, each `stack` in its source as lines, and the
   rest of the source as logged. An answer out of shape throws.
2. **One redactor, by shape** (`redact`), on every printed line before it is cut: the job's log
   of a public repository is public, and the plan job holds no value to redact by. It takes
   out bearer and basic credentials; the value of anything named authorization, cookie, an
   API key, or ending in token, secret, password, keyring or credential (header lines, JSON,
   escaped JSON, queries); JWTs; email addresses; and any run of 24 or more base64, base64url
   or hex characters (keys, keyrings' keys, sealed routes, tokens, long ids) unless it reads
   as words (lowercase words joined by `_` or `-`, paths of them, a constant's name).
3. **Bounds:** at most `EVENTS` events a query, each line cut at `LINE_MAX` after redaction (so
   a cut never shows part of a secret), and the printout stopped at `PRINT_MAX`, saying how
   many were left.
4. **`Cloudflare.telemetry`** sends the query. A 401 or 403 throws naming
   `TELEMETRY_PERMISSION`, the one group the query takes; any other failure throws as every
   call does.
5. **The row:** `logs` reads the Cloudflare token alone, `applies: false` (it takes no `--yes`,
   as `plan`), its own options `--minutes` (1 to 7 days; none, `MINUTES`), `--worker` (one
   of `WORKERS`) and `--filter` (text, no control character). The workflow offers it and its
   three inputs, and its `apply` job skips it as it skips `plan`.

## Left

- [ ] live: the founder adds Workers Observability Write (Account, Workers Observability, Edit)
  to the `CLOUDFLARE_API_TOKEN` token; then a dispatch of `logs` on `main` reads the stage's
  two failures, and the redactor is checked against what the Workers really log.
- The event's `source` shape for `console.error(message, error)` is not in the schema
  (`source` is "string or object"). The printer prints any `stack` it holds as lines and the
  rest as JSON, so it shows what is logged either way; the live dispatch above shows the shape.
