# free-model-ranking: a model the ranking does not place ranks by its vendor's strength score (2026-11-23)

`untangle/free-model-ranking`, owed by `auto-credit.md`. On a key that pays for nothing
(`connection_credit.pays = 0`), `auto` picks only the models its record's `credit.free`
matches (`*:free` on OpenRouter). The platform's ranking places none of them, so all are
unknown, all are priced 0, and `auto` took the first by id (`cohere/north-mini-code:free` in
pi-ai 1.4.0). Direction 4: "default to their strongest connected source". Direction 2: a
vendor's facts are its record's data, never code.

## Research (Fact 14), before the design

### How `auto` ranks today

- `owner/src/auto.ts` `ranked`: by tier, a plan before a key within a tier, then the entry,
  newer ids first; a model no entry matches (`place` null) after every tier, ordered by its
  catalog output price, highest first; then the older connection, the connection, the source,
  the id. `owner/src/runs.ts` `choose` walks that order and skips what `pickable` refuses
  (`not_interactive`; on a key that pays for nothing, what `credit.free` does not match).
- The ranking (`sources/src/model-ranking.json`, 4 tiers, 27 entries) ranks frontier and
  second-line paid models. Its matcher matches the id and the part after the last `/`, with a
  trailing `*` taking a `:free` suffix, so a free variant of a ranked model would rank; but no
  free model in pi-ai 1.4.0's OpenRouter catalog is a variant of a ranked one.

### What ranking data the platform holds

- A connection's models are pi-ai 1.4.0's catalog for its provider (`gateway/src/catalog.ts`
  `modelInfos`), kept whole on its row (`connections.models`, `ModelRecord`): id, name,
  inputs, limits, reasoning, thinking levels, cache, prices. No rank, score or date field;
  pi-ai's `Model` has none either (read through its API: `type, id, name, api, baseUrl,
  provider, reasoning, thinkingLevelMap, input, cost, contextWindow, maxTokens, compat,
  inputLimits`).
- pi-ai 1.4.0's OpenRouter catalog: 386 models on `openai-completions`, 24 priced at zero:
  17 `:free` variants and 7 routers (out of scope, `fix/router-metering`). Of the 17, 10 have
  their paid model in the catalog (by the id without `:free`), at $0.12 to $4.05 per million
  output tokens; 7 have none.
- The only stated proxy the code has for an unknown model's strength is that catalog price.
  For a free model it is 0 by definition. A sibling's price would cover 10 of 17, and price is
  a vendor's sales choice, not a measure: it put `thinkingmachines/inkling:free` first, a
  model the vendor no longer serves free (below).

### What the vendor's listing says (read 2026-11-23)

- `GET https://openrouter.ai/api/v1/models` (public) and `GET /api/v1/models/user` (the key's
  own listing: the same `ModelsListResponse`, "filtered by user provider preferences, privacy
  settings, and guardrails") carry, per model, `benchmarks.artificial_analysis.
  intelligence_index`: "Artificial Analysis Intelligence Index score, on a 0-100 scale ...
  Higher is better. Null when Artificial Analysis has not published a score for the model"
  (OpenRouter's SDK reference, `AABenchmarkEntry`, `ModelBenchmarks`, `Model`;
  `docs/client-sdks/typescript/models/aabenchmarkentry.md`). The REST page for `/models`
  documents `benchmarks` but only its Design Arena part; the SDK reference documents both.
- The public listing read that day: 477 models, 106 with an intelligence index (0 to 64.6;
  `anthropic/claude-fable-5.2` highest). A `:free` variant carries its paid model's score
  (`nvidia/nemotron-3-ultra-550b-a55b:free` 11.8, its paid id 11.8).
- 12 `:free` models listed. Only 8 of pi-ai 1.4.0's 17 are among them: the other 9
  (`cohere/north-mini-code:free`, today's pick, among them) are no longer served free. Of the
  8, 4 carry a score: `nvidia/nemotron-3.5-super-vl:free` 13.6,
  `nvidia/nemotron-3-ultra-550b-a55b:free` 11.8, `nvidia/nemotron-3.5-lightning:free` 5.5,
  `google/gemma-4-31b-it:free` 4.4.
- `GET /api/v1/benchmarks` (authenticated) is a second listing of the same Artificial Analysis
  scores, keyed by `model_permaslug` (a model's `canonical_slug`), which pi-ai's catalog does
  not carry: joining it needs the models listing anyway.
- `/api/v1/models/user` is the OpenRouter record's `verify_url` and among its sealed `GET`
  endpoints, so every OpenRouter connection ever sealed may read it, the stage's included
  (`auto-credit.md`: sealed with `chat/completions`, `models/user`, `benchmarks`).

### How comparable products rank free models

- OpenRouter's own `openrouter/free` picks "a free model at random" among those that support
  the request (its routers guide, `free-router.md`): no strength order.
- Kilo Code's free-models list ranks by a published benchmark index where there is one, and
  lists the unbenchmarked newest first; its docs warn that OpenRouter's free models "appear
  and disappear based on promotional periods".
- The platform's own ranking was set in review from the same Artificial Analysis index
  (`strongest-ranking.md`, "The ranking's data").

### Why not a hand list

- The free set churns faster than any reviewed change: 9 of pi-ai 1.4.0's 17 free ids were
  gone from the vendor within that pin's life, and the listing names 4 free models pi-ai does
  not have. A shipped entry per free model would be stale within weeks. The vendor publishes a
  measured score per model, current, through an endpoint every connection may already read:
  so there is another source, and no list is added.

## The design, chosen from the research

**A source record says where its vendor's listing gives each model's strength (`strength`: a
`GET` among its endpoints, the path to its rows, to a row's model id, and to its score, higher
stronger; the vendor's page). The `connections` lane reads it for each active connection
through the gateway (`strength`, held to the sealed endpoints, as `credit` is), once active
and again daily; the object keeps each of the connection's models' score
(`connection_strength`); `auto` orders the models no ranking entry places by that score,
highest first, a scored model before an unscored one, then by catalog output price as
before.** The ranking stays what places a model (the founder's call 4, an owner's file
replaces it); the score replaces only the price guess among the unknown, for every source
whose record names a listing, so a free-only key is no special case: on it every pickable
model is unknown. A model the vendor no longer lists has no score, so it ranks after every
scored one: the read also steers `auto` off free ids the vendor stopped serving.

OpenRouter's record reads `https://openrouter.ai/api/v1/models/user`, rows `data`, id `id`,
score `benchmarks.artificial_analysis.intelligence_index`. Read again daily (a listing scores
a model when it is benchmarked), an hour after a failure.

## Problems found (untangle step 2), with the fix above each

- **A source record names its vendor's strength listing (`strength`, `sources/src/schema.ts`,
  checked by `check.ts` `strengthSpec`); the gateway reads it (`forward.ts` `strength`); the
  lane keeps each connection's models' scores (`connection_strength`, owner migration 32);
  `auto.ts` `ranked` orders the unknown by score, then price.**
  - `ranked` ordered every unknown model by catalog output price alone, so on a key that pays
    only for free models (all 0) it fell to the id: the stage's `cohere/north-mini-code:free`,
    which the vendor no longer serves free.
- **One rule for a read the gateway makes with a sealed secret, whatever it reads.**
  - `egress-scope.ts` `creditRead` and `noCredit` held the read URL's and an answer path's
    checks inline: now `readUrl` and `answerPath`, which `strengthRead` uses too.
  - `forward.ts` `credit` held the sealed `GET` (open, service refused, endpoint check, fetch,
    status, bounded JSON) inline: now `sealedRead`, which `strength` uses too.
  - `check.ts` `creditSpec` held its endpoint check inline: now `sealedGet`, for both.
- **One loop for the lane's reads of a connection's source (`connections.ts` `LaneRead`,
  `#reads`, `#dueReads`, `#readOwed`)**, each read its table, its record field, its gateway
  call, its answer check and what an outcome keeps.
  - The credit read's due, owed, call, timeout and outcome code was written for credit alone;
    a second read would have been a copy. `#credited` no longer takes the version: the loop
    keeps an outcome only for the connection still active at the version read.
  - `creditMs` and `creditReadsPerPass` were one read's: now `readMs` and `readsPerPass`,
    for each kind.
  - `next()`'s one compound `SELECT` had five terms, the Durable Object SQLite's ceiling (a
    sixth failed: "too many terms in compound SELECT", found in the tests): each read's due
    is now its own query, so a read added adds no term.
- Kept, the system needs them:
  - `endpoint_refused` as "nothing to read on this blob" (`none`): a connection sealed before
    its record named a read. For strength it keeps no scores and reads no more; the stage's
    connection was sealed with `/api/v1/models/user`, so it is read.
  - An answer with rows but not one score is a failed read (logged, retried an hour later,
    the scores kept as they were), never "no model is strong": a renamed field would
    otherwise drop every score in silence (Fact 8).
- Left, with reasons:
  - A connection's models are pi-ai 1.4.0's catalog, not the vendor's listing: 9 of its 17
    free ids are no longer served free, and when the vendor scores none of a key's free
    models `auto` still falls to the first by id, which may be one of them. The strength read
    reads the very listing that says what is served; dropping what it does not list changes
    every key's model list, the app's and a named model's too, so it is its own lane (owed).
  - Only OpenRouter's record names a listing. The other bundled sources' models are ranked by
    the ranking's entries; whether their vendors publish a score was not researched, as no
    free-only key of theirs is known to the platform.
  - A scored free model is still unknown to the ranking, so an `auto` run on one still tells
    the owner `model_unranked` once: true, and the owner's fix (rank it in
    `/workspace/model-ranking.json`) still holds.
  - The scores are not in the owner's connection view: nothing reads them there.
  - A zero-priced router billed at zero is `fix/router-metering`'s.

## Owed

- [ ] live: at the stage, the owner's OpenRouter connection's next lane pass reads `GET /api/v1/models/user` and keeps scores for its `:free` models (`connection_strength`), and the next `auto` run is on the highest-scored one the listing scores (on 2026-11-23, `nvidia/nemotron-3.5-super-vl:free`).
- [x] untangle/served-models: a connection lists only the models its vendor's listing serves its key (the same `models/user` read), so a free id the vendor dropped is never picked; researched first. Done (`served-models.md`).
