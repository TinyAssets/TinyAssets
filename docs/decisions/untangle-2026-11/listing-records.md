# listing-records: a connection's models are the gateway's current catalog, read with its listing (2026-11-23)

`untangle/listing-records`, owed by `model-catalog.md` (#5834). A connection kept the catalog
records of its connect (`connections.models`), so pi-ai 1.6.0's 55 new tool-capable OpenRouter
models (#5834) reached only connections made after the bump, and each existing one kept 1.4.0's
prices for the owner's reserve while the gateway metered at 1.6.0's. Directions 1 and 4: every
connection offers the current catalog's models, the strongest among them, without a reconnect.
Direction 2: one catalog, the gateway's.

## Research (Fact 14), before the design

### What a connection stores at connect, and who reads it

- **The sealed blob** (`connection_secrets`): the secret and the upstream it was sealed with,
  which only the gateway opens: the provider, each sealed API (`apis`: its id and base URL),
  the auth template, the verify URL, the endpoints a call may reach, and, for a declared
  (`custom`) endpoint, its own model records.
- **The egress scope** (`connections.egress`, exported): the sealed APIs, each with the meter
  pi-ai's adapter was learned to call (method, path, model and output fields), and the
  endpoints, no secret. The egress door's admission reads it (`scopeApi`, `egressTarget`), and a
  run is given its model's API's base from it (`launch.ts` `modelApi`).
- **The models** (`connections.models`, exported): `modelInfos(catalog, upstream)` at the setup
  check (`gateway/src/forward.ts` `verify`), each a `ModelRecord` (id, API, name, limits,
  thinking map, prices), handed by the binder to `connectionCommit`, and never changed after.
  - Since #5833 the lane's listing read (`connection_listing`, migration 33) filtered them: it
    kept the ids of those models the vendor listed, so a listed model the commit lacked was
    dropped at the read (`listedJson(row.models, ...)`).
  - Every reader goes through `modelsOf`: `auto` (`usable`, `auto.ts` `ranked`), a run's named
    model (`runs.ts` `choose`), the run's thinking levels and its API's base (`launch.ts`), the
    egress door's reserve and router prices (`egressOf`, `egress.ts` `reserve`, `pricedBy`),
    and the owner's view (`list`).
- **The gateway already meters by its current catalog**: every forward opens the blob and
  builds `builtinCatalog()` (`forward.ts` `meteredRecord`), `unknown_model` for a model it lacks.
  So the stored records were a second, older copy of what the gateway holds, read only by the
  owner: its reserve priced by 1.4.0 while the settle was 1.6.0's.

### What can be recomputed, and what must stay as sealed

- **Recomputable, the platform's:** the catalog's records for the blob's provider on its
  sealed APIs. They are a function of the gateway's pi-ai pin and the blob, nothing the owner
  chose. A model new to the catalog on a sealed API needs no new consent: it is called at an
  endpoint already sealed (one `POST` per API, the model in the body or a path segment the
  endpoint's pattern already takes).
- **The vendor's:** the listing (what it serves the key, and scores), read daily since #5833.
- **The owner's consent, as sealed:** the APIs and endpoints in the blob. A record that later
  names another API (#5837's `anthropic-messages` at `POST /api/v1/messages`) must not reach a
  blob sealed without it: only a reconnect (a new seal) adds it. So the records are read
  through the blob, never through the current source record: `modelInfos` takes the APIs the
  blob was sealed for, and the scope and blob are never rewritten by a read.
- **A declared endpoint's records** are the owner's own, sealed in the blob, so they are what
  the blob says, not a catalog's. Read through the blob too, they come back unchanged.
- **The learned meter** (per API, in the scope) is a fact of pi-ai's adapter too, but it is
  part of the committed scope the admission holds a call to. 1.4.0 to 1.6.0 changed no API's
  call shape (`model-catalog.md`), so it is left (below).

### Where the owner can get the current catalog

- **The owner bundles pi-ai's catalog.** Then `modelsOf` could compute from the catalog with no
  read. But the owner holds no catalog by design (#5372, `model-record.ts`): the gateway's
  bundle carries every catalog and adapter (4,520 KiB at 1.6.0), the owner's object would load
  them in every isolate it shares (its memory baseline, `owner/memory/leg.mjs`), and pi-ai's
  `builtinProviders()` builds its providers per call (the gateway does so per request so no
  state crosses owners), which `usable()` and every egress reserve would pay. And it is a
  second copy of the gateway's catalog, held equal only by the lockfile, which during a rollout
  (gateway first) already differs.
- **The gateway hands it.** It opens the blob and lists `modelInfos(catalog, upstream)`, the
  setup check's own list, with no outside call. The lane already reads through the gateway
  once a connection is active and daily (`LaneRead`), and the listing read is one of those
  reads. So the owner keeps the gateway's answer, joined with the listing, as the connection's
  models: one copy, refreshed by the reads that refresh the listing.

## Design

1. **The gateway lists a blob's current models** (`GatewayService.models`, `forward.ts`
   `models`): `modelInfos(builtinCatalog(), upstream)` for the opened blob, each record naming
   its API, no call outside the gateway; a service's key has none (`unsupported_source`).
   `verify` keeps listing the same at connect.
2. **One lane read, `models`, replaces `listing`**, for every model connection whose source is
   a gateway source record (`upstream`): the gateway's `models`, then, where the record names a
   listing, the gateway's `listing` (unchanged). The owner keeps the join in
   `connection_models` (migration 34, replacing `connection_listing`): each current record the
   listing lists (every one where the record names none, or the blob reaches no such read,
   `endpoint_refused`), with its score or null. The read's records are held to the commit's
   rule (`checkedModels`: `maxModels`, each whole, none twice, `MODELS_JSON_MAX`).
3. **`modelsOf` is that read**, for every reader, and the commit's records only until the first
   read (or for a source with no record to read by, as a declared endpoint's). The lane makes
   every active connection's row due at once, the ones from before among them, so the first pass
   after this deploy moves every connection to pi-ai 1.6.0's records without a reconnect, and
   a later bump reaches each within `modelsRecheckS` (a day) of the gateway's deploy.
4. **What stays as sealed:** the blob, its scope (APIs, endpoints, meters) and `egress` are
   never rewritten by a read; the records come only from the APIs the blob was sealed for. So
   #5837's Messages API still needs a reconnect, and the stage's owed reconnect stands.
5. **Failed reads keep what was read**, as #5833's rule: an answer of another shape, a gateway
   refusal, a read that leaves no model (a listing that lists none of the current records, which
   the owner is told of once, `connection_listing_unmatched`; or an empty catalog), and one
   over `MODELS_JSON_MAX`, each read again `modelsRetryS` (an hour) later.
6. **Expand, then contract.** The gateway deploys first and gains `models`; the owner before
   never calls it, and the owner after calls `listing` as the one before does. A gateway rolled
   back past this release under this owner refuses `models` as no such method: a failed read,
   logged, the models kept as last read. The owner refuses to run under a schema newer than its
   code (`contract/src/migrate.ts`), so migration 34 has no rollback to serve. No handed shape
   changes: the records are held by `checkedRecord`, as at commit.

## Problems found (untangle step 2), with the fix above each

- **One catalog, the gateway's; the owner keeps its answer for each connection, read by the
  lane with the listing, through the one `modelsOf`.**
  - `connections.models` was the catalog at connect, a second copy of the gateway's that no bump
    reached: 55 OpenRouter models unpickable on the stage's connection, and the reserve priced
    by 1.4.0's records while the meter settled at 1.6.0's.
  - `listedJson(row.models, listing)` filtered the vendor's listing by the commit's records, so
    a listed model the catalog gained was dropped at every read.
  - Sources with no listing were never re-read: their models were the commit's for good.
  - The read and its limits were named for the listing alone (`connection_listing`,
    `listingRecheckS`, `listingRetryS`, `#listing`); the read is the connection's models now.
  - The lane's loop (`#reads`) turned `endpoint_refused` into "nothing to read" for every read,
    a meaning only a read of a vendor's endpoint has; the gateway's `models` reaches none. Each
    read that has it says so now (`reached`: credit's, and the listing inside the models read).
  - The commit's check of a model list and the read's would have been two copies; one
    (`checkedModels`) holds both.
  - A connection committed before #5837 kept records naming no API (`ModelRecord.api` absent),
    which `modelApi` reads as the scope's one API; the gateway's answer names it, so after one
    read every connection's records name their API (`apis-contract`'s "tagged its stored
    models" step, done by the read).
- Kept, the system needs them:
  - The commit's records (`connections.models`, exported): what `modelsOf` reads until the
    first read, seconds after commit, and for a connection whose source no record reads by.
  - `endpoint_refused` from the listing as "nothing to read on this blob": the models are then
    the catalog's on its sealed APIs, unfiltered, as a source without a listing.
  - A read that leaves a connection with no model is a failed read (Fact 8), never an empty
    connection: a vendor's renamed ids, or a gateway that lists nothing, would otherwise empty
    every connection in silence.

## Left, with reasons

- **The scope's learned meters** are pi-ai's adapter's call shape at connect. A bump that moved
  an API's method, path or model field would leave a connection's admission on the old shape
  while the gateway learns the new; none did from 1.4.0 to 1.6.0, and re-learning one rewrites
  the committed scope the admission holds every call to. The pi-ai watch's review (owed in
  `model-catalog.md`) reads each bump's adapters for it.
- **The owner is not told that a reconnect would add an API.** A record that now names an API
  the blob lacks (OpenRouter's Messages API, on the stage) offers its models only after a
  reconnect, and nothing says so but the decision files. Owed below.
- **A bump reaches a connection within a day**, not at the gateway's deploy: the owner cannot
  learn the gateway's pin without asking it, and the read that asks is the daily one. A bump
  is about monthly (`model-catalog.md`).

## Owed

- [ ] live: on the stage, the first lane pass after this deploy reads the OpenRouter connection's
  models (`connection_models`), and its sources row lists the 1.6.0 models its listing serves
  (`openrouter/auto-code`, new in 1.6.0, among them), with no reconnect, and still none
  on `anthropic-messages` until the owed reconnect.
- [ ] untangle/reconnect-offer: tell the owner once when a connection's source record names an
  API or endpoint its blob was not sealed for, with what a reconnect adds, so a record change
  like #5837's reaches the owner's connections by the owner's own consent.
