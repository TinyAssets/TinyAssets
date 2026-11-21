# Durable Object regional availability research as built (2026-11-18)

ADR-032 gives each owner one SQLite-backed Durable Object. The doors resolve the same
owner name to the same object (`platform/doors/src/binder.ts`, `platform/owner/wrangler.jsonc`).
Requests arriving at different Cloudflare edges can still depend on that object's location.
Cloudflare documents placement hints as best effort at initial creation. Giving a later
request a different hint cannot fail an existing owner over. [Data location](https://developers.cloudflare.com/durable-objects/reference/data-location/)

## Incident evidence

At 10:00 UTC on November 18, Cloudflare's public feed held 50 recent incidents, the oldest
record created on November 3. An incident could have started before its record was created. Exactly three listed Durable Objects in their affected components. The
table uses the times Cloudflare posted. The two regional first notices were already in
monitoring, so the time until administrative closure cannot be counted as owner downtime.
[Status API](https://www.cloudflarestatus.com/api) · [Incident feed](https://www.cloudflarestatus.com/api/v2/incidents.json)

| Scope reported | First public update, UTC | Closure, UTC | What customers experienced |
|---|---|---|---|
| Western Europe | Nov 5, 13:23 | Nov 6, 09:05 | Error rates rose. The first notice said the issue was already resolved and under observation. [Incident](https://www.cloudflarestatus.com/incidents/oy8gxjb38yse) |
| North America | Nov 12, 22:39 | Nov 13, 03:44 | Traffic increased suddenly and errors rose. Impact had already subsided when monitoring began. [Incident](https://www.cloudflarestatus.com/incidents/9zxr2bkqoct7) |
| Region unspecified | Nov 17, 21:56 | Nov 18, 00:17 | Cloudflare investigated elevated timeouts and degraded performance. Stream and Containers were also listed. The notice supplies no regional breakdown or error percentage. [Incident](https://www.cloudflarestatus.com/incidents/ylj5po752g6h) |

A separate June 2025 postmortem quantifies a shared storage failure. Cloudflare reported
that the average error rate for SQLite-backed Durable Objects peaked at 22% and fell
to 2% during recovery. Legacy KV-backed objects were unaffected. The owner uses SQLite.
[June 12 postmortem](https://blog.cloudflare.com/cloudflare-service-outage-june-12-2025/)

## What the rebuild can conclude

Errors can be regional, and shared storage failures can also affect the backend this
project uses. These reports give no denominator of owner requests or per-region success
series. They cannot establish a regional availability percentage or rank ADR-032 against
a two-region alternative. The architecture comparison remains open pending comparable
measurements of successful owner operations. The existing canary exercises one configured
owner from the deployed system (`platform/deploy/canary.ts`), so its results describe that
owner's path. No vendor resource or test owner was provisioned for this review.

## Review (2026-11-21)

The table's dates, scopes, components and first-notice states match each incident's record,
and the June 2025 figures match the postmortem. Two later incidents, after the sample, were
posted while impact was live:

| Scope reported | First public update, UTC | Fix, closure, UTC | What customers experienced |
|---|---|---|---|
| A subset of customers, with D1 | Nov 19, 13:05 | —, 13:37 | Storage operations failed or were slow. [Incident](https://www.cloudflarestatus.com/incidents/5pe5mb9fefi8) |
| Eastern North America, with R2 | Nov 20, 02:52 | 04:02, 04:35 | Intermittent errors and failed requests. [Incident](https://www.cloudflarestatus.com/incidents/pfwuex7qix27) |

An object stays where it was created; only the first `get()` reads a hint, and a
jurisdiction only bounds where it runs. So an incident where an owner's object lives is that
owner's whole outage, from every edge: each door (`/mcp`, `/api`, `/hooks/inbound`) fails,
and the owner's wakes (the object's alarms) wait on the same location, with no failover.
Owners elsewhere see nothing. Direction 8 needs no host here, yet fails for that owner for
the incident: at least 70 minutes on November 20. A hint or a jurisdiction changes neither,
so the doors set none. The canary's one owner lives in one location, so an incident
elsewhere raises no alarm: that, and ADR-032's per-region measurement, are `tasks.md` step
2's "Per-region owner availability". [Data location](https://developers.cloudflare.com/durable-objects/reference/data-location/)
