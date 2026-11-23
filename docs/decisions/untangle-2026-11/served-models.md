# served-models: a connection's models are what its vendor's listing serves its key (2026-11-23)

`untangle/served-models`, owed by `free-model-ranking.md`. A gateway connection's models were
pi-ai 1.4.0's catalog for its provider, kept whole at connect (`connections.models`). On the
stage's OpenRouter connection 9 of the catalog's 17 `:free` ids are no longer served, so
`auto` can land on a dropped one (when the vendor scores none of a key's free models, it falls
to the first by id), and an agent naming one gets the vendor's error, not a clear refusal.
Direction 2: a vendor's facts are its record's data, never code. Fact 8: fail loudly.

## Research (Fact 14), before the design

### How a connection's model list is built today

- At connect the gateway's setup check (`forward.ts` `verify`) `GET`s the record's
  `verify_url` and throws the body away; the models are `catalog.ts` `modelInfos`: pi-ai's
  catalog for the sealed provider and API, each a `ModelRecord` with its prices. The binder
  commits them; the owner keeps them on the row (`connections.models`, exported, within
  `MODELS_JSON_MAX`) and never changes them after.
- Every reader of a connection's models reads that column: `auto` (`usable`, `auto.ts`
  `ranked`, `runs.ts` `choose`, where a named model must be a candidate's or the run is
  refused `no_source`), the egress door's reserve (`egressOf`: a call's model must be one the
  connection lists, else `egress_unknown_model`; a router is priced by the connection's models
  it routes to, `routers.ts` `pricedBy`), and the owner's view (`ConnectionView.models`).
- #5828 added a record-driven read of the vendor's listing (`strength`: OpenRouter's
  `GET /api/v1/models/user`, sealed on every OpenRouter connection, the stage's included), read
  by the gateway's `sealedRead` and kept by the `connections` lane (`LaneRead`,
  `connection_strength`, migration 32, deployed) once active and daily. It keeps only the
  scored rows: the ids of the models the listing serves unscored are dropped at the gateway
  (`scoresBy`).

### What the vendors offer for listing the models a key is served (public docs, 2026-11-23)

- Every bundled gateway record's `verify_url` is already its vendor's list-models endpoint,
  and a `GET` among its sealed endpoints.
- **OpenRouter** `GET /api/v1/models/user`: the key's own listing, "filtered by user provider
  preferences, privacy settings, and guardrails" (the routes the vendor will refuse are not in
  it), `data[]`, `id`, one page. The public listing read that day: 477 models; 343 of pi-ai
  1.4.0's 386 OpenRouter ids are in it by exact id, 43 are not (the 9 free ids, `auto`, the
  retired `openai/o1`, `openai/o3-mini`, `openai/gpt-3.5-turbo`, several `:batch` variants),
  and 134 listed ids are not in the catalog (`anthropic/claude-opus-5.5` among them). Of the
  routers the record names, `openrouter/auto`, `openrouter/auto-beta`, `openrouter/pareto-code`
  and `openrouter/free` are listed; the bare `auto` is not.
- **Anthropic** `GET /v1/models` (platform.claude.com, "List Models"): `data[].id`, paginated
  (`limit` "Defaults to `20`. Ranges from `1` to `1000`", `has_more`, `after_id`), and lists
  `active` and `deprecated` models unless asked. pi-ai's Anthropic catalog holds both aliases
  and dated ids (`claude-haiku-4-5`, `claude-haiku-4-5-20251001`); which spelling the listing
  carries is not stated for every model, and no key was read.
- **OpenAI** `GET /v1/models`: "Lists the currently available models", `data[].id`, one page.
  pi-ai's catalog has aliases (`gpt-5-chat-latest`) whose listing spelling no key was read
  for. xAI, Groq, Mistral and Cerebras answer the same `data[].id` shape; Mistral's catalog is
  mostly `*-latest` aliases.
- **Google** `GET /v1beta/models`: `models[]`, each `name` "Format: `models/{model}`" (pi-ai's
  id has no `models/`) and a `baseModelId`; paginated ("If unspecified, 50 models will be
  returned per page", `nextPageToken`).
- So the listing read's record data (a URL, the rows, a row's id) fits OpenRouter today, whose
  ids were matched against the catalog; Anthropic and Google need pagination (and Google an id
  prefix) the read has no data for, and the others need their ids matched against pi-ai's with
  a key.

### Prices of a model the catalog lacks

- The owner reserves a call by the model's `ModelRecord` (`egress.ts` `reserve`, `pricedBy`),
  and the gateway meters it by pi-ai's catalog at call time (`forward.ts`
  `meteredRecord(catalog, ...)`, `unknown_model` for one it lacks): a model with no catalog
  record can be neither reserved nor metered, and a zero-priced model no record names a router
  or free is refused `egress_unknown_model` (#5827). OpenRouter's listing does carry prices
  (`pricing.prompt`, `pricing.completion`, per token as strings) and limits, but taking them
  would be a second price source beside pi-ai's, in both Workers. pi-ai has published 1.5.0 to
  1.6.0 since the 1.4.0 pin.

### What happens to a model that drops out between reads

- Today: the catalog never changes, so a dropped model is listed for good, `auto` may pick it,
  and the vendor refuses each call (loud, but never learned).
- A listing re-read daily drops it within a day; between reads the vendor's refusal is the
  run's error, as today.
- A listing read can also go wrong quietly: an empty list, a renamed rows or id path, or an id
  spelling change (a prefix) would make every model "not served". Each must be a failed read
  that keeps what was read before (Fact 8), never an empty connection.

## The design, chosen from the research

**A source record's `listing` (was `strength`) is where its vendor lists the models it serves
the key: a `GET` among its endpoints, the path to its rows, to a row's model id, and, where the
vendor gives one, to its score, higher stronger (`score`, now optional). The `connections`
lane reads it through the gateway's `listing` once a connection is active and again daily, as
#5828 read the scores, and the object keeps each of the connection's models the listing lists,
with its score or none (`connection_listing`, migration 33, replacing `connection_strength`).
A connection's models are then its catalog models the listing lists (`Connections` `modelsOf`),
in one place for every reader: what `auto` ranks and scores, what a named model must be (else
`no_source` at admission), what the egress door reserves and routes by (else
`egress_unknown_model`), and what the owner sees.** One read gives the list and the scores; no
second mechanism. The catalog stays the connection's record at connect, so a model the vendor
serves again comes back at the next read.

- Unknown is not empty: before the first read, and on a blob sealed before its record named the
  read (`endpoint_refused`), a connection's models are its catalog, as before.
- Failed reads keep the list as it was and are read again an hour later: an answer with no
  list at `rows`, a row with no id, no rows, a named `score` path with not one score (the
  gateway's `upstream_error`), and a listing that lists none of the connection's models (the
  owner's, logged, and the owner told once, `connection_listing_unmatched`: a key that lost
  every model reads the same, and only the owner can check it at the provider).
- A listed model the catalog lacks is not added: it has no price either Worker can reserve or
  meter by (left, below).
- OpenRouter's record names its listing (the same read and paths as #5828's strength). No
  other record does yet (left, below).

Expand-then-contract across Workers (the gateway deploys before the owner): the gateway gains
`listing` (shape `listing_read`; its answer's rows `listed_model`) and keeps `strength` (shape
`strength_read`) for the owner's version before, answered from the same read; the owner calls
only `listing`. Migration 33 drops `connection_strength` (its rows held only scored models, so
read as a served list they would drop every unscored one) and makes `connection_listing`; the
lane makes each active connection's row due at once, so none needs a reconnect.

## Problems found (untangle step 2), with the fix above each

- **One read of the vendor's listing for each connection, its record's `listing`, kept as the
  connection's listed models (`connection_listing`), and one function every reader reads a
  connection's models through (`connections.ts` `modelsOf`).**
  - A connection's models were the catalog at connect, never what the vendor serves: 9 free
    ids the vendor dropped stayed pickable, and a named one reached the vendor's error.
  - The strength read already fetched the served listing and threw its unscored ids away
    (`scoresBy`), so the list and the scores came from one answer read for half its facts.
  - The read was named for one of its two uses (`strength`, `StrengthRead`, `ModelStrength`,
    `connection_strength`, `strengthRecheckS`): it is the listing now, and the score one of
    its optional paths.
  - The gateway's strength answer's rows were checked inline (`keyed(one, ["id", "score"])`):
    the listing's rows are a handed shape (`listed_model`), read through `taken`.
- Kept, the system needs them:
  - A failed read keeps what was read before. A listing that names none of the connection's
    models is a failed read, never "the vendor serves nothing": an id spelling change would
    otherwise empty every connection in silence (Fact 8). A key that genuinely lost every
    model reads the same, so the owner is told once (`connection_listing_unmatched`; review).
  - A named model `auto` is given that no key lists is refused `no_source` naming it
    (`runs.ts` `choose`), not the generic engine text (review).
  - A listing whose record names a score path and that scores not one row is a failed read
    (#5828's rule against a renamed path). On a key whose listing scores none of what it
    serves, its models stay the catalog and the failure is logged hourly; OpenRouter's key
    listing lists the scored paid models too, so no such key is known.
  - `endpoint_refused` as "nothing to read on this blob" (`none`): its models are the catalog.
  - The gateway's `strength` and `strength_read`, for one release (owed below).
- Left, with reasons:
  - Between two reads (a day) a model the vendor dropped is still listed, and the vendor's
    refusal is the run's error. Re-reading on a refused call would need each vendor's
    "no such model" status as record data, and OpenRouter's 404 also means other things.
  - The 134 models OpenRouter serves that pi-ai 1.4.0 lacks are not offered: no price either
    Worker can reserve or meter by. A pi-ai bump lists them (1.6.0 is published); prices from
    the listing would be a second price source.
  - Only OpenRouter's record names a listing. Anthropic and Google paginate (Anthropic at 20
    by default) and Google prefixes its ids, which the read has no data for; OpenAI, xAI, Groq,
    Mistral and Cerebras answer `data[].id`, but their ids against pi-ai's aliases were not read
    with a key. Each takes the record field, data only, once its listing is read against its
    catalog; no such key is known to the platform today.
  - OpenRouter's rows carry an `expiration_date`: a model the vendor will drop on a date. Not
    read: the daily read drops it when it goes.
  - A connection's catalog records are pi-ai's at connect and are not refreshed when the pin
    changes; that is the pin bump's, not the served list's.

## Owed

- [ ] live: at the stage, the owner's OpenRouter connection's next lane pass reads `GET /api/v1/models/user` (`connection_listing`), its sources row lists none of the 9 free ids the vendor dropped (`cohere/north-mini-code:free` among them), and the next `auto` run is on a free model the listing lists.
- [x] untangle/served-models-contract: the gateway's `strength` and the `strength_read` shape go once the stage serves no owner version that calls them.
  #5840 found the hold reads the shapes data, not the calls, and marked `strength_read` `handed: false`; the removal is the next item.
- [x] untangle/strength-out: the gateway's `strength`, its entrypoint method and the `strength_read` shape go once the stage serves an owner of #5840 or later, whose shapes mark `strength_read` `handed: false` (`served-models-contract.md`).
  Removed once run 45251793237 served 19b79ef8f on all six Workers; the hold passes against it (`strength-out.md`).
