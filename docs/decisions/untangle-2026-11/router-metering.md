# router-metering as built (2026-11-23): a router's call is priced by what it routes to

`fix/router-metering`, owed by `auto-credit.md`. A router is a model whose vendor picks the
model each call runs on and bills the call as that model, while its catalog lists the router at
a price of zero (OpenRouter's `openrouter/auto`). The platform metered it at that zero, so the
owner's limits (Fact 15) did not hold a router's call, and its usage could count nothing.

## Research (Fact 14)

- **How a call is metered today.** One path: sandbox egress.
  - **Reserve** (`owner/src/egress.ts` `#reserve`): the named model's catalog record, as the
    connection holds it (pi-ai 1.4.0's), its input held at its context window and its output
    at its cap, at its dearest rates (`egressPrice`). The charge moves the run's counters and
    the owner's and the source's day and month (`model_spend`), which every cap reads.
  - **Settle** (`gateway/src/meter.ts` `meteredUsage`): pi-ai reads the answer's usage, priced
    at the dearer of the named model and the model the answer says served it (pi-ai's
    `responseModel`). `null` settles `unknown`, keeping the whole reservation.
- **What that did for a router.**
  - Its reservation priced at zero, so it passed every cap, even one already spent, and held
    its whole output.
  - At settle, a served model the catalog lists was priced as itself. But `unknown` kept the
    reservation, which was zero. And an answer naming the router's own id, or a router the
    catalog also lists at zero (`auto` answered as `openrouter/auto`), settled at zero.
  - The gateway told a router from a free model by its zero price alone (`free`), and both are
    zero: a `:free` model answering with no model id settled `unknown` for no reason.
- **What the vendor returns for a routed call** (OpenRouter's docs, through web search: the
  host's network does not reach openrouter.ai).
  - Each chunk's `model` is the model that served it ("auto router", "model routing").
  - The routers (`openrouter/auto`, `openrouter/pareto-code`) bill each request at the price
    of the model that handles it, with no fee of their own ("auto router", "pareto router").
    The free router (`openrouter/free`) picks among free models.
  - `openrouter/fusion` runs a panel of models and a judge on each request, each billed, so
    one served model's price does not bound it (its page, and a third-party write-up).
  - Every answer reports `usage.cost` in its last chunk ("usage accounting": `usage.include`
    is deprecated and has no effect). For a key using its own upstream key (BYOK) that cost is
    OpenRouter's fee alone, the provider's own in `cost_details.upstream_inference_cost`.
  - `typesafe/jev-router`: no vendor page was found.
- **What pi-ai reads.** A probe through pi-ai's own adapter on an OpenRouter-shaped stream:
  pi-ai reads the served model (`responseModel`), and its `usage.cost` is its own catalog price.
  It drops the vendor's `usage.cost`.
- **The free-only rule (#5819).** `pickable` lets `auto` pick, on a key that pays for nothing,
  only models `credit.free` matches (`*:free`). No router matches, so `auto` never routes a
  free-only key. An agent may still name one; the vendor then refuses the call (`402`), which
  settles `failed` and marks the key free-only.

## The design

The served model's catalog price, the same price every call is settled at, and not the
vendor's reported cost: that would be a second price source beside pi-ai's, read from bytes
pi-ai drops, with BYOK reading another field. Which models are routers, and where each may
route, is the vendor's fact, so it is source-record data (Direction 2).

- **A source record names its routers** (`routers`: each router's model patterns, the patterns
  of the models it may route to, and the vendor's page). OpenRouter's: `openrouter/free` routes
  to `*:free`; `auto`, `openrouter/auto`, `openrouter/auto-beta` and `openrouter/pareto-code`
  route to `*`.
- **One rule says what a call is priced by** (`sources/src/routers.ts` `pricedBy`): a router's
  call by every model the connection lists that it may route to; any other model by itself. A
  record that names routers says its vendor's zero is no price of its own, so a model it lists
  at zero that is neither a router nor `credit.free` has no price known. That call is refused
  `egress_unknown_model` rather than counted at nothing (Fact 8): `openrouter/fusion`,
  `typesafe/jev-router`, and any zero-priced model a catalog update adds.
- **The reserve holds a router's call at the dearest model it may route to**, its input at the
  router's window and its output at the router's cap, so the owner's caps bound it. It tells
  the gateway the call is routed (`metering.routed`) when a route is priced above zero. A
  router to free models alone (`openrouter/free`) costs nothing whatever serves it, so it is
  settled as a model named alone: `succeeded` at zero with its real tokens (review: routed, its
  every answer settled `unknown`, counting its whole window to the run's tokens).
- **The gateway prices a routed call by the served model alone**, when the catalog prices it
  above zero. Anything else (no served id, the router's own id, another router, a free model,
  an unlisted model) is `null`, which keeps the reservation, the dearest route.
- **The gateway's zero-price guesses go.** A model named alone is priced as itself.

## Problems found (untangle step 2), with the fix above each

- **Routers become record data, read by one rule (`pricedBy`), which the reserve prices by and
  hands the gateway as `routed`.**
  - `#reserve` priced every call by `[model]`, the router's zero among them. `egressPrice` took
    a list of models, but only one ever reached it, and `#reserve` took `most` over that list
    of one (surplus). Now the list is what the call is priced by.
  - `meteredUsage` guessed a router from a zero price (`free`), twice: an unlisted served model
    and a missing served id. The guess could not tell a router from a free model, and it let a
    router's answer naming a zero-priced id settle at zero. Now the reservation says it.
  - Nothing priced a router's reservation, so an `unknown` settle kept zero.
- **The `routed` field goes in steps** (`handed.json` `always`). The gateway deploys first, so
  for one deploy its parser takes a metering without `routed` from the owner's version before,
  read as unrouted. That owner reserved no router above zero, so nothing is lost.
- **The gateway's tests gain an OpenRouter chat-completions fake** (`fake-upstream.ts`), whose
  answer names the model the word `served` names. It tests the reservation's `routed` reaching
  the meter.
- Left, with reasons:
  - `openrouter/fusion` and `typesafe/jev-router` are refused. A reservation bounding a panel of
    calls, or a router no page was read for, needs research of its own, and no one is known to
    use them.
  - A router's reservation is the dearest model it may route to, at the router's whole window.
    On OpenRouter that is large, so an owner with a tight cap may find a router's call refused
    where a named model passes. That is the honest bound: OpenRouter's router plugin
    (`plugins`, `allowed_models`), which could narrow it, is a paid-tool field the gateway
    refuses.
  - A routed answer naming a model the catalog does not list settles `unknown`, keeping the
    dearest bound. The vendor's `usage.cost` would price it exactly. It is left out, as above:
    a second price source.
  - On a key that pays for nothing, a router the agent names is still reserved at its paid
    routes, which the vendor then refuses. `pickable` keeps `auto` from picking one.
    `openrouter/free` is the free router and could be a free key's pick, but adding it to
    `credit.free` is ranking work (`untangle/free-model-ranking`), not metering.
  - `auto` could pick a model `pricedBy` refuses only when no ranked or scored model is
    pickable. Its call is then refused loudly at the reserve.
  - A paid router's answer naming a free model, or a dated id of a listed one, settles
    `unknown` at the dearest route: an over-count, never an under-count. The gateway cannot
    tell a free model from a router by price; the live line below shows what OpenRouter's
    routed answers name.

## Owed

- [ ] live: once the stage serves this owner, `metering.routed` moves from `always` to `required` in `handed.json`, and the gateway's parser stops reading an absent one as unrouted.
- [ ] live: at the stage, a run on the OpenRouter connection naming `openrouter/auto` is reserved at its dearest route (its receipt's `est_cost` above zero), and settled at the model its answer names (`served`, `cost` above zero).
