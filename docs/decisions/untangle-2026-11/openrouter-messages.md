# openrouter-messages: one source speaks every API its models speak (2026-11-23)

`untangle/openrouter-messages`, owed by `model-catalog.md` (#5834). pi-ai's catalog holds
OpenRouter's Claude models (20 in 1.6.0, `anthropic/claude-opus-5.5` and Claude Fable 5.2
among them) only on `anthropic-messages`, OpenRouter's `/api/v1/messages`. The OpenRouter
record spoke `openai-completions` alone, and `modelInfos` kept only the sealed API's models,
so an OpenRouter user could use none of the strongest Claude models. Directions 1 and 4.

The first build of this PR made a second OpenRouter record. Review (#5837, REQUEST_CHANGES)
showed that its premise was wrong and that the copy was a band-aid. This file records the
design built instead.

## Research (Fact 14), before the design

### What one vendor is, and what varies by API

- A gateway record named one `upstream`: one pi-ai provider, one `api`, one `base_url`, one
  `auth`, and the `endpoints` its secret may reach. The gateway seals those values with the
  secret.
- Downstream, everything read that one API:
  - `modelInfos` and the meter's `apiModels`;
  - the committed `ModelScope` (one `api`, one learned `meter`);
  - the owner's admission and reserve (`owner/src/egress.ts`), and the gateway's `forward`.
- The fields that change together are the vendor's key: `help_url`, `connect`, `verify_url`,
  `credit`, `listing`, OAuth with its key exchange, and the `GET` endpoints. Only four things
  vary by API: its id, its base URL, its learned meter, and the rules a call on it is held to.
- pi-ai models it the same way: one provider, and each model carries its own `api` and
  `baseUrl`. OpenRouter's two are `openai-completions` at `https://openrouter.ai/api/v1`
  (397 models) and `anthropic-messages` at `https://openrouter.ai/api` (20). Each adapter
  appends its own path: `/chat/completions` and `/v1/messages`.

### Why one connection can serve both to a run

- **A run runs one model.** pi's argv is `--provider {provider} --model {model}`
  (`sources/records/pi.json` `model_argv`; `sandbox/runner/engine.ts` `start.model`). The owner
  chooses the model where it builds the run's environment (`runs.ts` `choose`, then
  `launch.ts` `#runConfig`).
- pi applies its one provider `baseUrl` override to every built-in model of the provider
  (pi's `models-configuration.md`; `provider-composer.ts` `config.baseUrl ?? model.baseUrl`).
  So a run's base only has to fit its own model's API: `…/c/<conn>/api` for a Claude run,
  `…/c/<conn>/api/v1` for a chat run.
- A base of `/api/v1` sends a Claude call to `/api/v1/v1/messages`. OpenRouter answers that
  path 404 (probed with no key, 2026-11-23); `/api/v1/messages` answers 401.
- pi's `models.json` can also set a per-model `baseUrl` (a `models` entry replaces the
  built-in model whole). That would be the fallback if a run ever switched models partway,
  and none does.
- **The door already reaches every sealed path.** A run's path after `/c/<conn>` is the
  upstream's own path, held to the sealed `endpoints` (`egressTarget`), so
  `POST /api/v1/messages` is one more endpoint entry.
- A meter has a method and a path, so a call's method and path say which API's rules and
  meter apply (`scopeApi`).

### OpenRouter's `/api/v1/messages` (API reference and guides, read 2026-11-23)

- **Auth:** its OpenAPI declares one scheme, a bearer key in `Authorization`, for every path,
  `/messages` among them. Its Claude Code guide sends `ANTHROPIC_AUTH_TOKEN` as
  `Authorization: Bearer`. So the source keeps its one documented `auth`.
  - pi-ai's adapter sends `x-api-key` on this API. The gateway drops the engine's header and
    sets the sealed one either way.
  - The meter test holds a record's `auth` to the header pi-ai sends on its first API.
- **Usage:**
  - The non-streaming example's `usage` carries `cost` and `is_byok`.
  - Replayed an answer with them in `message_delta`, pi-ai 1.6.0 marks the total
    `totalFromProvider`, so the meter settles at OpenRouter's bill (#5834's rule).
  - With no bill, it settles at the catalog's price.
  - A stream that names Anthropic's own dated id (the schema's `message_start` example) and
    reports no cost is `unknown`.
- **Fields beyond Anthropic's:**
  - `models` and `fallbacks`: fallback lists. Anthropic's own API has a refusal `fallbacks`
    too.
  - `plugins`: billed beyond tokens.
  - `deferred_tools`: "router-level", and its usage counts "Upstream model calls the run
    made".
  - Typed server tools: already refused by the API's tool kinds.
  - Chat completions takes `deferred_tools` too.
- **Service tier:**
  - OpenRouter serves "the standard tier unless you ask for another one". Its values are
    `default`, `flex`, `priority`/`fast` and `ultrafast`, and the Messages API reports
    `standard` in `usage.service_tier`.
  - Anthropic's default `"auto"` "Uses the Priority Tier capacity if available", hence
    `standard_only` when absent. That is Anthropic's account default, not the API's.
- **Routers** (#5827): none of OpenRouter's routers are on `anthropic-messages`. A router call
  on chat completions may route to a Claude model, and it is reserved at the dearest of the
  connection's models, both APIs' included.
- **Listing** (#5833): `GET /api/v1/models/user` lists every model, all 20 Claude ids among
  them. A connection lists its catalog's models on every sealed API that the listing serves.

### What crosses Workers, by the deploy order (gateway, sandbox, owner, doors)

- **The sealed blob.** The gateway opens every blob sealed before (`api`, `base_url`). On a
  rollback past this release, the gateway before must open what this one sealed. The keyring
  guard holds both (`guards/keyring-main.test.ts`).
- **The seal input.** The doors hand it to the gateway (`source`, `keySource`,
  `keyExchangeSource`). The gateway deploys first, so it reads the doors' version before.
- **The setup check's answer** (`verify` → doors → `connectionCommit`). The gateway deploys
  before the owner, so while the stage serves the owner before, that owner parses the new
  gateway's answer with strict shapes (`egressScope`, `checkedRecord`).
- **The stored scope and models.** Each owner row holds the scope its connect committed. The
  owner reads rows written before.
- `handed.json` lists none of these inner shapes (their parsers check them in code), so
  `deploy/guards/handed.test.ts` does not hold them. This PR holds them as described above.

### The stage's connection

- Its blob was sealed with `POST /api/v1/chat/completions` and three `GET`s, so it cannot reach
  `/api/v1/messages`.
- A reseal (the keyring's rotation) keeps the sealed values. The stored models are the
  connect's.
- So the stage reconnects OpenRouter once, and that connection lists both APIs' models.

## Design

1. **An upstream names its APIs.** `apis: [{ api, base_url }]` sits beside the vendor's
   `provider`, `auth`, `verify_url` and models.
   - Every base is at one origin among the endpoints, since a door call names its path alone.
   - No API appears twice. A declared endpoint speaks one.
   - Every record moves to `apis`.
   - OpenRouter lists `openai-completions` at `/api/v1` first and `anthropic-messages` at
     `/api`, adds `POST /api/v1/messages`, and keeps its one documented Bearer `auth`.
   - The sources guard holds each API's base to that of its catalog models.
2. **One seal, one connection.**
   - `modelInfos` lists the catalog's models on every sealed API, each record naming its API
     (`ModelRecord.api`, pi-ai's own field).
   - The scope carries `apis: [{ api, base_url, meter }]` and the one `endpoints` list.
   - `Meters` learns per API.
3. **A call's API is the one whose meter its method and path are** (`scopeApi`).
   - Its rules (query, headers, body), its model's catalog record and the clamp are that
     API's.
   - Its model must be one the connection lists on that API (`modelApi`). The owner's reserve
     refuses another as `egress_unknown_model`, and the gateway refuses it as
     `unknown_model`.
   - A call no meter claims (a read, a free call) is held to the rules of every API the scope
     speaks, any of which may take a value.
4. **A run is given the base of its model's API** (`sealedBase`, `EngineRun.api` from the
   chosen model in `launch.ts`).
   - A run that names no model is given the connection's first API. A record lists first the
     API its engine's default model speaks.
   - An API the connection is not sealed for fails loudly.
5. **Expand, then contract.**
   - The gateway reads both spellings: blobs and seal inputs.
   - The owner reads both spellings: stored and handed scopes, and models naming no API on a
     source of one API.
   - The gateway still seals a source of one API in the old spelling (`sealedForm`), so the
     gateway before opens it.
   - The gateway still hands a source of one API in the old spelling (`handed`), so the owner
     before takes it.
   - A source of several APIs, which only this release's records make, is sealed and handed
     as it is.
   - The contract step is owed below.
6. **The API rules take OpenRouter's fields** (kept from the first build, which review
   confirmed):
   - `plugins` and `deferred_tools` are refused on the Messages API;
   - `deferred_tools` is refused on chat completions;
   - `models` and `fallbacks` are read as fallback lists;
   - `standard_only` when absent is keyed to provider `anthropic` with its page
     (`AbsentDefault`), and `clamped` takes the call's provider and API.

## Problems found

- **One API per upstream, scope and meter.** A vendor whose models speak two APIs could list
  only one API's models. The first build of this PR then made two records, and its guard held
  the copied reads equal. Fixed: one upstream names its APIs.
- **The run's base was the sealed base, one per connection.** Fixed: the base of its model's
  API.
- **`default_when_absent` set Anthropic's account default on every source of the API.** Fixed:
  keyed by provider.
- **`ANTHROPIC.fallbacks` was empty**, though both vendors take fallback lists. Fixed.
- **Unbilled fields passed the paid-tool check.** OpenRouter's `plugins` and `deferred_tools`
  (Messages), and `deferred_tools` (chat completions). Fixed.
- **The sources guard held a record's base to the provider's default base**, one API's. Fixed.
- **`owner/src/egress.ts` re-checked that a scope's API has rules**, which the scope's parse
  already holds. Removed.

## Left, with reasons

- **The route probe runs each engine with no model**, so it probes a source's first API alone.
  A probe per API needs a model per API in the probe's start; the live line covers the
  Messages API.
- **pi 1.4.0's catalog** (`sources/locks/pi`) has 15 of the 20 Claude models, so 5 are
  unknown to the run's engine until pi moves. That is the same as #5834's 55 chat models, and
  pi's own record moves it.
- **A metered call's `anthropic-beta` is dropped**, by the API's rule. That is so on Anthropic's
  own source too. The stage's call says whether OpenRouter needs it.
- **A pi run's own `GET {base}/models/user` is refused on a Claude run**, whose base is `/api`.
  pi then lists every OpenRouter model (its `providers.md`).
- **Claude Code on an OpenRouter key** is not in this PR. The Messages API makes it possible,
  but OpenRouter's `engines` stay `["pi"]`, and Claude Code's `api_key_env` names `anthropic`
  alone.

## Owed

- [ ] live: reconnect OpenRouter on the stage (its blob lacks `/api/v1/messages`), then run one
  call on `anthropic/claude-opus-5.5`. Its receipt says the settle (the bill, the catalog price
  or `unknown`) and whether the dropped betas refuse it.
- [ ] untangle/apis-contract: once the stage serves only this release's gateway and owner,
  seal and hand every source in the `apis` spelling (`sealedForm`, `handed` go). Once a
  keyring sweep has resealed every blob and the owner has rewritten its stored scopes and
  tagged its stored models, drop the old spelling's readers (`modelUpstream`, `egressScope`)
  and `ModelRecord.api`'s absence.
