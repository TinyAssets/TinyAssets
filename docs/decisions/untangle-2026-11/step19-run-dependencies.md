# Step 19 run dependencies (2026-11-18)

Reviewed main `f934871ad` and integrated candidate `533733c40` against the Step 19
tooling list. These four items remain open.

## Task operations and timed launches

The installed MCP SDK 2.3.1 has no typed task-method runtime. Its 2025 core task
vocabulary and the newer Tasks extension are different contracts. In an offline
2026-07-28 client/handler exchange, the real `createMcpHandler` routed both
`tasks/get` and `tasks/cancel` to explicitly registered extension handlers, with
the task ID in `Mcp-Name`. With neither handler registered the client received
`CLIENT_HTTP_NOT_IMPLEMENTED` for each. This establishes transport reachability only.
Check capability negotiation, creation and result envelopes, status, cancellation and
expiry for each supported revision before the doors advertise task support.

`agentStatus` and `agentCancel` accept exactly one of `run_id` and `schedule_id`.
The owner's `Runs.claim` removes a due one-shot and advances a cron. Each admitted
occurrence starts a separate run. The real schedule methods on the owner's 18 migrations
in SQLite confirmed that the one-shot's ID stops resolving after claim, a repeat claim
does nothing, and the cron ID remains until cancellation. Cancelling a schedule removes
future occurrences. An active run is stopped through the sandbox and settles through
the runner end or lost-run handling. The result, its retention, and who may read it
already belong to the owner's receipt. Which operation a task would track for a timed
request remains a design question. No second scheduler or task receipt has been built.

## Past runs

The app's `runs` calls the owner's `runList`. The owner filters by caller, orders by
descending sequence and gives the next cursor. It limits **rows**, selects the entire
row, then drops the result when building summaries. MCP's `resultText` enforces an
encoded byte ceiling. On both reviewed trees the real owner method over 130 synthetic
settled rows returned 55,554 bytes. The real MCP formatter refused that page at 50,000.
Ten rows passed and the cursor continued without a gap. An unrelated client saw zero.
Fit a page at the owner using its existing page-sizing machinery and keep the caller
filter, summary shape and continuation. Read only the fields needed for the page to
avoid materializing result bodies. The full result belongs to the status door.

## Paid server tools on key runs

`api-rules.ts` recognizes request shapes in data. The actual detector refused OpenAI
Responses web search, Anthropic's `mcp_servers` and OpenAI completions'
`web_search_options` in the six enabled/absent fixtures. `gateway/src/forward.ts`
refuses an enabled option before its reservation call. Today the reserve callback
carries model and tier, the owner bounds the charge with model token rates and the
gateway settles from the provider's model usage. A future price needs evidence for its
unit, a bounded quantity to reserve against the owner's caps, and actual billed usage
to settle. Unreadable usage must remain covered by the held charge. Keep these decisions
together with the rule that admits the request. No provider billing was exercised.

## Kimi region across a sign-in and its runs

`owner/src/signins.ts` records the chosen region on the live sign-in. The durable
owner and outside-client sign-in reports record an engine's status, not its chosen region.
`owner/src/launch.ts` asks `vendorHosts(engine, credential, null)` for a run.
Against each reviewed tree the served Kimi record produced zero hosts for a null region
and three hosts each for global and mainland China, with no shared host. A signed-in
run's host set is also used when the sandbox reapplies the live network.

Carry the confirmed choice for the same authority as its vendor credentials. Establish
how that choice is recovered or replaced for a successful sign-in, a failed replacement
and a sign-out, including existing stores which have no recorded region. Derive hosts
from the served record at the existing network boundary. A key run's vendor allowlist
remains empty. No Kimi account, paid work or live network was used.
