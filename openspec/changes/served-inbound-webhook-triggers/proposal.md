# Served inbound webhooks: the app agent can give its owner a trigger URL

Capability C16 (`2026-09-24-capability-gap-audit.md (archived in git)`, PR #3947):
scheduled and event-triggered runs fire as the owner with no host online.

## Why

Read-only production inspection on 2026-09-24 (`scripts/droplet.py ssh`,
`/data/.automations.db` and `/data/.runs.db`, scalars only) split C16 in two.

**The scheduled half already works hostless.** The founder universe's
automations completed 612 runs in the cloud daemon, all with actor
`universe:u-01kxm1vszd8hwp7em418asq8h9` and the owner's principal: 576 for the
5-minute heartbeat (2026-08-30 to 2026-09-01, provider `codex`) and 36 across
three automations on 2026-09-11. No host was involved.

**The "last scheduled run failed" had a known, fixed cause.** The heartbeat
(`d25a3568…`) failed three consecutive runs on 2026-09-01 at 03:23, 03:28 and
03:33 UTC (runs `763ec685d40442f6`, `c0fc403b25544b96`, `30c41bd729c74169`), all
with `provider invocation carrier is already consumed`. It then auto-paused
(`MAX_CONSECUTIVE_FAILURES = 3`) and was never resumed. The error came from a
Tenacity retry that re-entered the router with a single-use carrier that the
first attempt had already spent, and so masked that attempt's real error. PR
#2756 (`c5dd6414`, 2026-09-01 06:06 UTC) made carrier-armed calls non-retryable.
Since then there have been zero carrier failures in 3,657 recorded runs (the two
later rows matching the text are code nodes that printed AGENTS.md). The app's
09-08 answer "its last scheduled run failed" was accurate, but it described a
paused row whose last run was a week old.

**The inbound half had never been used.** Production has
`TINYASSETS_INBOUND_ENABLED=1`. `GET https://tinyassets.io/mcp/hooks/x` returns
405, which shows the POST route is mounted publicly. The receiver already runs
each delivery as `universe:<id>` for the hook's recorded owner. Yet
`webhook_hooks` holds zero rows and no run has ever been named `webhook` or
`event:source:*`. The only way to mint a hook was the claude.ai connector's
`run_graph webhook_op`. The served agent in the app (`engine_mcp_server`) could
schedule work but could not create, list or revoke a webhook, so C16's
acceptance ("an inbound webhook triggers a workflow run, through the real app")
was unreachable.

## What changes

1. Served `write_graph target="webhook"` supports `operation="create"` and
   `operation="revoke"`, and served `read_graph target="webhooks"` lists hooks.
   These delegate to the existing owner-scoped `mint_webhook`, `revoke_webhook`
   and `list_webhooks` handlers, with the universe and the owner taken from the
   server's pins.
2. Revoke accepts the non-secret `token_prefix` that the list shows, scoped to
   the pinned universe's plain hooks and to exactly one match. The raw token is
   shown only once, so before this change an owner who lost it could never
   revoke the hook, and could never delete its branch either, because the hook
   blocks that deletion.
3. Automation reads include `next_due_at`, computed with the pump's own
   trigger rules, so the app can show when a schedule fires next.

## Impact

- The served MCP surface and authority are affected. No new storage table or
  column is added. The connector's `run_graph` schema is unchanged.
- There is no production config change. The inbound flag is already on.
- Source nodes (event-bus triggers) stay connector-only. A direct hook covers
  "an external event runs my workflow" with one fewer moving part.
