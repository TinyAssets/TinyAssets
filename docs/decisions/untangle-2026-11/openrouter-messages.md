# openrouter-messages: OpenRouter's Claude models as a record of their own API (2026-11-23)

`untangle/openrouter-messages`, owed by `model-catalog.md` (#5834). pi-ai's catalog holds
OpenRouter's Claude models (20 in 1.6.0, `anthropic/claude-opus-5.5` and Claude Fable 5.2
among them) only on `anthropic-messages`, OpenRouter's `/api/v1/messages`. The OpenRouter
record speaks `openai-completions`, and `modelInfos` keeps the models of the sealed API alone,
so an OpenRouter user can use none of the strongest Claude models. Directions 1 and 4.

## Research (Fact 14), before the design

### How a source declares its API, and whether one may speak two

- A gateway record names one `upstream` (`contract/src/egress-scope.ts` `Upstream`): one
  pi-ai provider, one `api`, one `base_url`, one `auth`, and the `endpoints` its secret may
  reach. The checker (`sources/src/check.ts`) holds it by the gateway's own `source`, and the
  gateway seals exactly those values with the secret (`credential.ts`).
- Everything downstream reads that one API: `modelInfos` and the meter's `apiModels` keep the
  catalog's models on `upstream.api`; a committed connection's `ModelScope` carries one `api`
  and one learned `meter`; the owner's admission (`owner/src/egress.ts`) and the gateway's
  `forward` hold every call to that API's rules (`api-rules.ts`).
- **An engine takes one base URL per connection.** A run's environment gives the engine the
  door, its run, its connection and the sealed base path (`contract/src/egress.ts`
  `egressBaseUrl`); pi's record writes it as the provider's `baseUrl` in `models.json`. pi
  applies a provider's `baseUrl` to every built-in model of the provider (its
  `models-configuration.md`: "Also applies to the provider's built-in models"; its
  `provider-composer.ts` `config.baseUrl ?? model.baseUrl`), and `modelOverrides` cannot set
  one.
- pi-ai's catalog puts OpenRouter's two APIs at two bases: `openai-completions` at
  `https://openrouter.ai/api/v1` (397 models), `anthropic-messages` at
  `https://openrouter.ai/api` (20), each adapter appending its own path (`/chat/completions`,
  `/v1/messages`). Probed with pi-ai's adapter and no network: on a base of `<door>/api/v1` a
  Claude model's call goes to `<door>/api/v1/v1/messages`. OpenRouter, asked with no key
  (2026-11-23): `/api/v1/messages` and `/api/v1/chat/completions` answer 401,
  `/api/v1/v1/messages` and `/api/chat/completions` 404.
- So **no one connection can serve both APIs to pi**: its one base reaches one of them. One
  record speaking both (per-call API dispatch, two meters in a scope) would build a dispatch no
  run can use; the unit an engine runs on, a key on one API at one base, is a connection as it
  is. A vendor serving models on two APIs at two bases is two records.

### OpenRouter's `/api/v1/messages` (its API reference and guides, read 2026-11-23)

- **Auth:** its OpenAPI declares a bearer key for every path; its endpoint reads `x-api-key` as
  a credential too (no key: "No cookie auth credentials found"; a made-up key, by either
  header: "User not found.", on `/api/v1/messages`, `/api/v1/key` and `/api/v1/benchmarks`).
  pi-ai's adapter sends `x-api-key` for these models, so the record seals it there (the
  gateway's meter test holds a record to the header pi-ai sends).
- **Usage:** the answer's `usage` carries `cost` and `is_byok` (its non-streaming example; the
  streamed `message_delta` schema lists neither). pi-ai 1.6.0's adapter, replayed an answer
  with them in `message_delta`, marks `usage.cost.total` `totalFromProvider`, so the meter
  settles at OpenRouter's bill (#5834's rule); without them, at the catalog's price. A stream
  that names Anthropic's own dated id (its `message_start` schema example,
  `claude-sonnet-4-5-20250929`) and reports no cost is `unknown`: the id is neither the
  catalog's nor its snapshot. Which one OpenRouter sends is the stage's first call's receipt
  (owed, `live:`).
- **Fields beyond Anthropic's:** `models` and `fallbacks` (fallback lists, "Handled by
  OpenRouter multi-model routing"; Anthropic's own API has a server-side refusal `fallbacks`
  too, pi's `allowedFallbackModels`), `plugins` (web search and others, billed beyond tokens),
  `deferred_tools` ("Opt-in versioned router-level deferred-tool protocol", whose usage counts
  "Upstream model calls the run made": several calls under one reservation's cap), and typed
  server tools (`tools[].type`, already refused by the API's tool kinds). OpenRouter's chat
  completions takes `deferred_tools` too, which the completions rules do not refuse.
- **Service tier:** OpenRouter: "Every request uses the standard tier unless you ask for
  another one" (but priority endpoints of open-weight models), and its values are `default`,
  `flex`, `priority`/`fast`, `ultrafast`; the Messages API reports `standard` in
  `usage.service_tier`. Anthropic: `"auto"` (default) "Uses the Priority Tier capacity if
  available", hence the rules' `standard_only` when absent. That default is Anthropic's
  account's, not the API's: sent to OpenRouter it is a value its page does not list.
- **Routers** (#5827): the catalog has none of OpenRouter's routers on `anthropic-messages`.
- **Listing** (#5833): `GET /api/v1/models/user` lists all 20 Claude ids, so a connection of
  the new record lists them all; the same read, credit check and OAuth as the record's.

### The stage's connection

Its blob was sealed with the record's endpoints: `POST /api/v1/chat/completions` and `GET`s of
`/api/v1/models/user`, `/api/v1/benchmarks`, `/api/v1/key`. It does not allow
`/api/v1/messages`, and a reseal (the keyring's rotation) keeps the plaintext as sealed, so no
existing connection can reach it. With this design the stage's OpenRouter connection is left
as it is; the Claude models come with a connect of the new record (a key of its own by OAuth,
or the same key pasted).

## Design

1. **`openrouter_messages`, a second OpenRouter record** (data only): `anthropic-messages` at
   `https://openrouter.ai/api`, its key in `x-api-key`, `POST /api/v1/messages` and the same
   three reads, `verify_url`, `credit`, `listing`, OAuth and help page as the record's, on pi.
   No routers (none on its API), no batch patterns (none on its API).
2. **Two records of one provider state the vendor's key reads once each, held equal** by a
   sources guard (`verify_url`, `credit`, `listing`, `oauth`, `help_url`): the copy is said.
3. **The guard "restate pi-ai's catalog" holds a record's base URL to its API's models' base**,
   not the provider's default, which is one API's.
4. **The Messages rules take OpenRouter's fields:** `plugins` and `deferred_tools` refused as
   billed beyond what is reserved, `models` and `fallbacks` read as fallback lists; the
   completions rules refuse `deferred_tools` too.
5. **A tier set when absent is keyed by the provider whose page says so**, as a free call is:
   `standard_only` for Anthropic's account default, with its page; OpenRouter's call is sent as
   asked. The gateway clamps by the sealed upstream (its provider and API).
6. `schema.ts` says why a vendor's second API is a second record; the checklist names its
   redirect (none).

## Problems found

- `api-rules.ts` `default_when_absent` set one provider's account default on every source of
  the API. Fixed: keyed by provider, with its page.
- `ANTHROPIC.fallbacks` was empty, though Anthropic's own API and OpenRouter's take a
  fallback list. Fixed.
- OpenRouter's `plugins` and `deferred_tools` on the Messages API, and `deferred_tools` on chat
  completions, passed the paid-tool check. Fixed.
- `sources.test.ts` held a record's base URL to the provider's default base, one API's: a
  second API's record could not restate the catalog. Fixed.

## Left, with reasons

- Two connects for one vendor: a user who connected OpenRouter gets the Claude models only by
  connecting the new record. One connect sealing a key for each of a vendor's records needs
  the key exchange to seal twice (its code is single-use), a gateway change of its own. Owed.
- The engine pin: pi 1.4.0 (`sources/locks/pi`) carries pi-ai 1.4.0's 15 Claude models, so 5
  of the 20 the gateway lists are unknown to the run's engine until pi moves; as for the 55
  chat models #5834 added. Its own record.
- A metered call's `anthropic-beta` is dropped (the API's rule: a beta can widen a call past
  its reservation), on Anthropic's own source as here; pi-ai sends two betas with the newest
  models' bodies. The rule is the API's, not this PR's; the stage's first call says whether
  OpenRouter needs them (owed with the receipt).

## Owed

- [ ] live: connect `openrouter_messages` on the stage and run one call on
  `anthropic/claude-opus-5.5`: its receipt says the settle (the bill, the catalog price or
  `unknown`), whether the dropped betas or the `x-api-key` read refuse it.
- [ ] untangle/connect-apis: one connect of OpenRouter seals its key for both records, so a
  user who connects OpenRouter once gets its strongest models (Direction 4).
