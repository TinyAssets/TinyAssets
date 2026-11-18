# P18 canary research as built (2026-11-18)

The five alerts in §10 step 5 need three different observations. The following traces start at main `f934871ad`. They use repository source and the vendors' public contracts. Account access, production observations and alert delivery remain unverified.

| Signal | Producer | What the canary must count |
|---|---|---|
| `sandbox_waiting` | `owner/src/launch.ts`, after `waitForSandbox` succeeds | The owner's warning on the run entering the wait state. The same code in a sandbox reply can also describe waiting during sign-in. |
| `sandbox_renewal_refused` | `sandbox/src/control.ts` | Both error lines for a refused resume, including the path that keeps the sandbox marked for renewal. |
| `sandbox_runner_mismatch` | `sandbox/src/control.ts` | The loader's digest-mismatch error. An unsupported runner protocol has its own code. |
| `exceededMemory` | Workers invocation outcome | ADR-037 calls for an alert on any such outcome. A Worker can be stopped before it logs anything. |
| Peak concurrent sandboxes | E2B team metrics | Compare the team's maximum reported running count for the interval to the active control record's `sandbox_concurrency_ceiling`. The admission path reads the current team count. A snapshot at the end can miss the peak. |

## Cloudflare

The Worker configurations enable observability. Cloudflare maps the old Workers Observability Read role to Workers-scoped Metadata Read-Only. Its telemetry query reference lists Workers Observability Write as the accepted permission for the POST, even for an inline query. It requires a query ID and Unix-millisecond bounds. Whether the intended read-only credential can retrieve the required data is still open. Keep the deploy credential out of the canary. Verify a known console event and an invocation outcome for the right scripts on an authorized test stage before enabling the check. [Workers roles](https://developers.cloudflare.com/workers/authorization/workers/) · [Telemetry query](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/)

The Analytics GraphQL example groups Worker invocations by script and status. Its adaptive sampling can miss rare events, so a zero there alone cannot establish ADR-037's single-event coverage. The existing `scripts/probes/storeprobe/analytics.mjs` reads aggregate counts for specified probe objects. [Workers metrics query](https://developers.cloudflare.com/analytics/graphql-api/tutorials/querying-workers-metrics/) · [Sampling](https://developers.cloudflare.com/analytics/sampling/)

## E2B

The canary already reads the registered lifecycle webhook with `heldHook`. E2B's webhook list returns `WebhookDetail[]`, whose required `teamId` supplies the metrics path for precisely the selected hook. This avoids a second team setting or a separate team-list credential. The same API-key authorization is documented for the metrics call. Supply both bounds explicitly in Unix **seconds** and `metric=concurrent_sandboxes`. Omitting the bounds in the server selects the past seven days. The other supported metric is a rate. [Pinned OpenAPI](https://github.com/e2b-dev/E2B/blob/c4bab34e85a82cf55bbec5ce89d91d1abf299b1a/spec/openapi.yml) · [Max handler](https://github.com/e2b-dev/infra/blob/ec06ca431328dd1a713d6e40fbe29ef7e4c4931a/packages/api/internal/handlers/team_metrics_max.go)

E2B's exporter samples the running gauge every five seconds. The maximum is over those stored gauge values. A peak wholly between samples can therefore be invisible, even with a successful response. The owner's queue-transition warning remains a separate observation. [Team exporter](https://github.com/e2b-dev/infra/blob/ec06ca431328dd1a713d6e40fbe29ef7e4c4931a/packages/api/internal/metrics/team.go)

A successful maximum has numeric `value` and `timestampUnix` in seconds. The vendor's empty-team test expects a successful zero, and the database client's no-row branch can return the current time. A zero therefore means no positive peak reported, and cannot prove a sample existed at that time. A refused request, malformed body or unavailable ceiling leaves the comparison unknown. The checked-in initial control record omits sandbox numbers. Read the effective control record through an authorized read-only path before building the comparison. A copied plan limit would silently diverge when the operator raises it. [Database query](https://github.com/e2b-dev/infra/blob/ec06ca431328dd1a713d6e40fbe29ef7e4c4931a/packages/clickhouse/pkg/team.go) · [Vendor test, inspected only](https://github.com/e2b-dev/infra/blob/ec06ca431328dd1a713d6e40fbe29ef7e4c4931a/tests/integration/internal/tests/api/metrics/team_metrics_max_test.go)

## Integration still owed

Use the existing canary outcome and alarm on each doors-side run (`x7.md`). The existing alarm can close an incident on the next green. Preserve gaps in observation across late polls and refused reads, and surface aggregate counts without raw log messages or identifiers. Verify coverage across consecutive windows, read failure and recovery, and a single memory outcome before claiming the alerts.
