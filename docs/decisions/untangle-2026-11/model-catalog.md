# model-catalog: pi-ai 1.6.0's catalog, and the gateway's interposer goes with 1.4.0 (2026-11-23)

`untangle/model-catalog`. The platform lists and prices models by pi-ai's bundled catalog,
pinned at 1.4.0 (2026-10-21). `served-models.md` (#5833) found 134 of the 477 models
OpenRouter serves missing from the connection's list, `anthropic/claude-opus-5.5` among them,
so they can be neither picked nor priced. Directions 1 and 4: match the leading agents, and
default to the user's strongest connected source, so the catalog must hold current models.

## Research (Fact 14), before the design

### What of pi-ai the platform uses

pi is no longer the engine the platform runs, but pi-ai is more than a catalog to it:

- **The gateway** (`gateway/src/catalog.ts`, `meter.ts`, `forward.ts`): `builtinProviders()`
  is every vendor's catalog (ids, limits, prices, thinking maps, and the per-model `compat`
  and headers its adapter shapes a request by); `createProvider` and the four lazy adapters
  serve a declared endpoint; `createModels().streamSimple` is the meter, which learns each
  API's call and reads each answer's usage through pi-ai's own adapter (no network), and
  `calculateCost` prices it. Sandbox calls themselves are forwarded raw.
- **The owner** (`owner/src/money.ts`): `calculateCost` over the records a connection keeps.
- **Tests and guards** in doors, owner and sources read `builtinProviders()`.
- Not pi-ai: the pi engine's own CLI pin (`sources/locks/pi`, `pi-coding-agent` 1.4.0, which
  brings its own pi-ai into the sandbox) and `pi-codemode` 1.4.0 (no pi-ai dependency). Each
  moves on its own record and is not this PR's.
- **`agents`** (the owner's dev dependency, used only for `agents/lifecycle` and
  `agents/websockets`) declares pi-ai as an optional peer, `~1.4.0` at 0.32.0, so npm refuses
  pi-ai 1.6.0 beside it. 0.34.0 (2026-11-20) peers `~1.6.0`; its changelog changes nothing the
  owner calls but `Lifecycle.fetch`'s failure body (a generic 500, or a 1011 close, instead of
  the error's stack) and fixes (a cancelled schedule no longer reruns; messages after a
  host's `close()` are dropped).

### Why 134 were missing (OpenRouter's public listing, read 2026-11-23)

| of the 134 | pi-ai 1.4.0 | pi-ai 1.6.0 |
| --- | --- | --- |
| Claude models (`anthropic/claude-opus-5.5` among them) | 15, in the catalog on `anthropic-messages` | 20, the same |
| not marked tool-capable by the listing (images, guards, older chat) | 61 | not in it |
| tool-capable chat models | 58 not in it | 55 added; `claude-fable-5.2` added on `anthropic-messages`; 2 listed after 1.6.0's cut |

- pi-ai's catalog is generated from models.dev (1.5.0) and holds OpenRouter's tool-capable
  chat models: of the 397 in 1.6.0 on `openai-completions`, one is not marked tool-capable by
  the listing. A model without tools cannot run an agent, so its absence is right.
- **The Claude models were never a catalog gap.** pi-ai has every OpenRouter Claude model on
  `anthropic-messages` (OpenRouter's `/api/v1/messages`). The OpenRouter source record speaks
  `openai-completions` alone, and `modelInfos` keeps only the models on the sealed API. No
  pin fixes that; it is owed below.
- So with 1.6.0, of 477 listed: 394 on the record's API, 20 Claude on the other, 63 not an
  agent's (55 without tools, 8 image models) but 2.

### What changed from 1.4.0 to 1.6.0 (pi-ai's `CHANGELOG.md`; the packages diffed)

- **Catalog:** OpenRouter 386 to 397 chat models on `openai-completions` (15 to 20 on
  `anthropic-messages`), Claude Fable 5.2, GPT-6.1 Terra, GPT-6.2 Astra and Sol, Kimi K3.1 and
  others. Every built-in catalog's largest record is still 607 bytes; the largest catalog is
  397 models (about 150 KB), so `MODELS_JSON_MAX` and `maxModels` hold unchanged.
- **Types:** pi-ai's model schema is built with pi-schema (1.5.0, `typebox` dropped), whose
  `Static` types each optional field `T | undefined`. `ModelRecord` keeps "pi-ai's fields as
  pi-ai types them", so it says the same; the JSON and its checker are unchanged.
- **Google's adapter takes `options.fetch`** (1.6.0, #12103): it hands it to `@google/genai`,
  which sends every unary and streamed request through it. It was the one adapter that refused
  one, and the only reason for the gateway's global-fetch interposer (`egress.ts`) and its
  three module-state exemptions, owed as `untangle/gateway-interposer` in `tasks.md`.
- **OpenRouter's billed cost** (1.5.0, #9980): its adapters put the answer's reported `cost`
  (with the upstream cost on a BYOK key) in `usage.cost.total` and marks it
  `totalFromProvider`, when the answer's usage carries `is_byok` and a finite, non-negative
  `cost`; otherwise the catalog's price, unmarked. The meter settles a marked total alone
  (review, below), so an OpenRouter call settles at what OpenRouter billed where the answer
  reports it, and at the catalog's price, by the router and served-model rules, where not.
- **A new router at zero:** 1.6.0 lists `openrouter/auto-code` at zero, OpenRouter's Auto
  Code (released 2026-11-05), "the same engine" as the Auto Router, "billed at the price of
  the model that serves it" (its model page and the Auto Router guide, which names it among
  the family's three members, read 2026-11-23). A zero-priced model the record names as no
  router and no free model has no price, so it joins the record's Auto Router entry: data.
- Behaviour on the wire the gateway forwards raw is the engines', not pi-ai's: nothing else
  the gateway calls changed shape. pi-ai's `personalizeOpenRouterCatalog` (the catalog
  filtered by `/models/user` and repriced by it) exists in both and is not wired to any
  provider.
- **Sizes:** the gateway bundles every catalog and adapter; its dry-run bundle grew from
  4,016 to 4,520 KiB (549 to 608 KiB gzipped; review), the owner's by 4 KiB. The memory leg
  passed; the sizes leg is CI's.
- 1.6.0 was published 2026-11-20 and `agents` 0.34.0 the same day; the repo has no rule on a
  release's age, and both move forward only (agents' upgrade note: no rollback past it once a
  Pi harness conversation runs on it; the owner runs none).

### A vendor's listing as the catalog instead

- OpenRouter's `/models` carries per model its prices (per token, with prompt-length
  overrides), context, output limit, modalities and supported parameters, and #5828/#5833
  already read it sealed. Anthropic's, OpenAI's and Google's own `/models` carry no prices,
  so a vendor listing is a catalog for aggregators only: two price sources, by vendor.
- A record made from a listing has no `compat`, headers, thinking-level map or API choice:
  pi-ai's adapters would shape its requests by defaults, the per-model fixes its changelog
  is mostly made of (strict tools, roles, effort, cache) lost, which looks right and fails
  later (Fact 8).
- pi-ai's own shape for this is the catalog first and the key's listing as a filter and a
  price over it (`personalizeOpenRouterCatalog`), which #5833 builds for the filter.
- The lag the pin leaves: after this bump, 2 tool-capable OpenRouter models, both listed after
  1.6.0's cut. pi-ai released four times in the month since 1.4.0.

## Design

1. **The catalog stays pi-ai's, current.** pi-ai 1.6.0 in the four workspaces that pin it,
   and `agents` 0.34.0, whose optional peer it is. No listing-made records. The owner's
   alarm guard pins agents by version and integrity after a read of its source; 0.34.0's
   `agents/lifecycle` and `agents/websockets` import closure was read from the published
   package (14 modules, as in 0.32.0): its module state is 0.32.0's plus the job queue's
   `#inFlight`, a map per queue instance, so none holds an owner's data across objects. npm
   now hoists it to the root (no peer conflict keeps it under the owner), so the guard holds
   the one copy wherever it sits, not npm's placement. `openrouter/auto-code` joins the
   record's Auto Router entry.
2. **`ModelRecord`'s optional fields as pi-ai 1.6.0 types them** (`| undefined`).
3. **The interposer goes, brought forward** (Direction 5): every API's adapter takes the
   `fetch` it is handed, so the meter hands it always. `takesFetch` (now true in every row),
   `egress.ts` (`within`, its scope, the captured fetch, `nativeFetch`), its test and its
   three module-state exemptions go; the gateway's guards call the runtime's `fetch`. What
   the interposer held is now a test: every API-key source's call is learned with the global
   `fetch` refusing, so an adapter that reached past the fetch it is handed fails it. The
   gateway spec says so.
4. **Numbers a test holds are not copied into comments**: the catalog's size and largest
   record are the connections test's scan, not prose to restate each bump.

## Problems found

- The pin was a month and four releases behind, so 55 tool-capable OpenRouter models were
  unpriced and unpickable. Fixed: 1.6.0.
- `api-rules.ts` `takesFetch`, a column with one `false` (Google), and the interposer behind it
  (`egress.ts`, `meter.ts` `within`, `rules.ts` three exemptions, `egress.test.ts`, two spec
  requirements), for an adapter that now takes `fetch`. Removed.
- `contract/src/limits.ts`, `owner/src/connections.ts`, `limits-audit.md` and the owner spec
  copied the catalog's counts (386, 607 bytes, 142 KB, "pi-ai 1.4.0"), which the connections
  test scans. Each now says what the test holds. `api-rules.ts` and `model-record.ts` named
  the version in prose; now they name pi-ai.
- `ModelRecord` claimed pi-ai's types; pi-ai's changed. Fixed.
- The alarm guard held agents at `owner/node_modules/agents` and asserted none at the root:
  npm's placement, which moves with peers. It now holds the one copy's version and integrity.
- OpenRouter's billed cost reaching the meter had no test; one now holds it (a non-BYOK and a
  BYOK answer).
- Review: the meter took the bill as the named model's price and kept its own rules over it.
  The served-model ratio (meant for a service tier) became bill over catalog, so a cheap name
  a dear fallback served settled at the dear one's catalog price times that ratio ($863 for a
  $0.42 bill, the test's models), and a router's call ignored the
  bill for the served model's catalog price: two rules for one price. A marked bill is now
  the call's price alone, whatever served it (`meteredUsage`), held for a fallback, a router
  and an unlisted route. The global-fetch test also learns a declared endpoint on each API
  one may speak (the lazy adapters, which the interposer also held).

## Left, with reasons

- `.github/workflows/store-probe.yml` and `scripts/probes/storeprobe/` import `pi-durable`
  and `chord`, which have not been in the lockfile since the engine moved, so the probe
  cannot bundle; its comment names pi-ai 1.4.0. Already broken before this PR and outside the
  catalog: its deletion is owed (`untangle/store-probe`).
- `engines-replan.md`'s file table says `egress.ts` is kept: a dated plan, not a description
  of the code; `tasks.md`'s owed clause is removed instead.

## Owed

- [x] untangle/openrouter-messages: OpenRouter's Claude models are on `anthropic-messages`
  (OpenRouter's `/api/v1/messages`) in pi-ai's catalog, and a sealed upstream speaks one API,
  so an OpenRouter connection lists none of them (20 in 1.6.0, `anthropic/claude-opus-5.5`
  among them). Its own research: one record on both APIs (each model's API from the catalog,
  the meter's and the egress scope's rules by the model's API) or a second record.
- [ ] untangle/listing-records: a connection keeps the catalog records of its connect, so a
  pin bump reaches only new connections. #5833's daily listing read (landed) can answer each
  listed model's record from the gateway's current catalog, so every connection moves with
  the pin without a reconnect.
- [ ] untangle/pi-ai-watch: a scheduled draft PR when pi-ai publishes (as `okf-watch` does
  for the starter's spec), so the catalog lags by a review, not by a month.
- [ ] untangle/store-probe: delete the store probe or rebuild it on what the owner runs.
