# probe-model as built (2026-11-24)

`untangle/probe-model`, owed by `run-names-model.md` (#5842). Directions 2 and 4, Fact 8.

The route probe ran each keyed engine with no model, so the engine ran its own default and the
probe was given and answered in its record's first API: a record had to list first the API its
engines' default models speak, and the start kept a no-model branch for it (`engine.ts`). Since
#5842 every admitted run names its model. This PR makes the probe name one too, per sealed API,
and then makes the start require a model.

## Research (Fact 14), before the design

### Where the probe picks its API and model today

- **The plan** (`sandbox/runner/probes.ts` `plan`, run on the checks' host from the probes
  bundle, `template/checks.ts`) makes one route step a keyed engine and provider:
  `route-probe <engine> <provider> <resolver>...`. No API, no model.
- **The step** (`probeRoute`) finds the gateway record that seals the provider's key for the
  engine (`sealedScope`), takes `apis[0]` as its API for the base (`sealedBase`) and for the
  capture server's reply (`reply(api, ...)`), and starts the engine with `model: null`.
- So OpenRouter's second API (`anthropic-messages`, #5837) is never probed
  (`openrouter-messages.md`, "Left"), and the probe is the one start left with no model.

### Where the gateway's current catalog is, and how the probe can read it

- **The catalog is pi-ai's**, in the gateway's bundle: `gateway/src/catalog.ts`
  (`builtinCatalog`, and `modelInfos(catalog, upstream)`, "the models a sealed upstream serves
  on every API it is sealed for"). The gateway lists by it at connect (`verify`) and at each
  lane read (`models`, #5844). The owner keeps that answer; it bundles no catalog by design
  (`listing-records.md`).
- **The plan runs from the probes bundle on the checks' host**, never in a Worker, so bundling
  the catalog there costs no Worker's memory. The probes bundle is uploaded only to the
  throwaway check sandboxes; the Worker ships the runner alone (`wrangler.jsonc` `rules`).
- **One source, no copy:** the plan calls the gateway's own `modelInfos` with the record's
  upstream. The probes and the gateway are built from one tree and one lockfile, so the probe
  names models from the catalog the same tree's gateway lists. A generated list of ids (a
  file under `contract/`) was weighed and rejected: a second copy held equal by a guard.
- **The layer table** lets the probes import the runner, the contract and the records. One
  entry more: the gateway's `catalog.ts`, which itself imports only the contract and pi-ai.
- **What it moves:** the probes' digest, and so the pair's tag, now moves with a pi-ai bump
  (it already moved with every record and contract change it reads). A bump then needs a pair
  check before its deploy, which is the check the bump wants: the template's engines on the
  models runs will be given.

### Which model on each API

- A run that names no model is given the strongest the owner's keys list (`auto`, `choose`).
  The probe is to run "as a run runs it", so its model is one such a run is likely to be given:
  of the catalog's models on that API, those a run may be given on its own (`pickable`, the
  record's `not_interactive` out; the key's payment unknown), the one the platform's ranking
  places first (`place`), the catalog's order breaking a tie.
  - `auto`'s whole order (`owner/src/auto.ts` `ranked`: plans first, newer ids first within an
    entry, scores, prices, the connection's age) is the owner's and orders connections the
    probe has none of. The probe needs a model the API serves that a run may be given, not
    `auto`'s exact one, so it takes the ranking's `place` alone.
  - No model a run may be given on a sealed API fails the plan loudly.
- **At pi-ai 1.6.0 with the shipped ranking** (computed from the catalog and the ranking on
  2026-11-24):

  | engine | provider | API | model |
  | --- | --- | --- | --- |
  | claude-code, pi | anthropic | anthropic-messages | `claude-fable-5` |
  | codex, pi | openai | openai-responses | `gpt-6.1-sol` |
  | gemini, pi | google | google-generative-ai | `gemini-4-argon` |
  | pi | openrouter | openai-completions | `openai/gpt-6.1-sol` |
  | pi | openrouter | anthropic-messages | `~anthropic/claude-fable-latest` |
  | pi | xai | openai-responses | `grok-4.3` |
  | pi | groq, cerebras, mistral | their one API | `qwen/qwen3.8-27b`, `qwen-3.8-27b`, `mistral-large-4` |

### pi and a model its own catalog lacks (found here, owed below)

- The template's pi is 1.4.0 (`sources/locks/pi`), bundling pi-ai 1.4.0. Its catalog lists 15
  of OpenRouter's 20 Messages API models: not `anthropic/claude-fable-5.2` nor the four
  `~anthropic/*-latest` aliases (both tarballs read 2026-11-24).
- pi 1.4.0's `--model` on an id it lacks (`core/model-resolver.js` `resolveCliModel`, no exact
  or substring match) runs it as a custom id built on the provider's default model
  (`buildFallbackModel`: OpenRouter's `moonshotai/kimi-k2.6`, on `openai-completions`). So it
  calls `{base}/chat/completions` on the Messages API's base (`/api`), a path no endpoint
  seals: the door refuses it, and the probe's reply is 404.
- `auto` gives a pi run on an OpenRouter connection sealed with the Messages API
  `claude-fable-*` first (tier 0, entry 0). So such runs fail today, once a connection is
  sealed with that API (the stage's is not, until its owed reconnect). The probe's
  `anthropic-messages` step is that run, and fails the same way until pi's catalog has the
  model: pi 1.6.0 (published 2026-11-20) bundles pi-ai 1.6.0. This is the probe doing its
  job, so the probe does not pick around it (Fact 8). Its consequence: the pair check fails
  on that step until pi moves, and the deploy kit deploys nothing without a passed pair.

### Whether anything still serving or spoken starts without a model

- **Who builds a start:** only the owner (`launch.ts`, `model: admitted.model`), and
  `Admitted.model` is a `string` since #5842 (`choose`, `Chosen`). The sandbox Worker maps it
  into the runner's document unchanged (`control.ts` `startDocument`). The probe was the one
  other start with `model: null`, and now names one.
- **The stage** serves main 8c80b02c8 on every Worker (deploy run 45281806227), which includes
  #5842. The deploy order is gateway, sandbox, owner, doors, and a rollback takes the doors
  back first:
  - the sandbox of this release, deployed before the owner, takes a start from the owner
    before, which hands a string;
  - a rollback to 8c80b02c8 takes back an owner that hands a string.
- **The deploy hold** (`deploy/guards/handed.test.ts`, `contract/handed.json`) lists no start:
  `SandboxStart` crosses Workers as a typed RPC value no strict parser reads, so the hold has
  nothing to say for it (left, below). `handed.json` lists no field this PR changes.
- **The runner protocol:**
  - The Worker speaks protocol 5 alone (`SPEAKS`, `protocol.ts`; #5851's `speaks.json` is not
    on main yet). Protocol 5's record types the start's `model` `string|null`.
  - A record holds what the protocol's Workers may write, and is frozen. A string is in it,
    so no record moves and no protocol is bumped.
  - The protocol 5 Workers that wrote null were those before #5842. None serves. A live
    sandbox's older runner reads a string as it reads one now.
  - The runner of this release types the start's model a string. Only a Worker before #5842,
    which no rollback reaches, could hand it null.
  - The next protocol's record types it `string`.
- **A CLI record without `model_argv`** would still run its engine's default on a named model:
  the `?? []` in `engine.ts` is the no-model branch's other half. Every served record has one,
  and the checker required it only of a record that signs in. A run names a model on every
  credential now, so it is required of every CLI record.

## Design

1. **The plan names each sealed API's model** (`probes.ts` `plan`): for each keyed engine, each
   provider it keys and each API the gateway record sealing that key speaks, one route step,
   `route-probe <engine> <provider> <api> <model> <resolver>...`, its model from the gateway's
   catalog (`modelInfos`) as above.
2. **The step runs as a run of that model runs**: the start names the model, the base is its
   API's (`sealedBase(egress, api)`), the capture server answers in its API, and an API the
   record does not seal fails loudly. Its result, and the pair's record (`passed.json`
   `routes`), name the API and the model.
3. **The start requires a model:** `SandboxStart.model` and `StartDoc.model` are `string`;
   `engine.ts` always passes the record's model arguments; `model_argv` is required of every
   CLI record (`schema.ts`, `check.ts`).
4. **Docs follow:** the record schema's note on API order goes (a record's API order means
   nothing now); the probes' and the start's comments say what they do now.

## Problems found (untangle step 2)

- **A hidden link between a record's API order and the probe's engine default** (`sealedScope`'s
  "listed first for that reason", `schema.ts`'s note). Fixed: the probe names its API and model.
- **A sealed API no probe ran** (OpenRouter's Messages API). Fixed: one step per sealed API.
- **The start's no-model branch** (`engine.ts`), kept only for the probe and owners before
  #5842. Fixed: gone, with its other half, an optional `model_argv`.
- **The checker's special case**: `model_argv` required only of a record that signs in. Fixed:
  of every CLI record.

## Left, with reasons

- **The start is no handed shape.** `SandboxStart` crosses from the owner to the sandbox as an
  RPC value no strict parser reads, so the deploy hold cannot hold its fields to the versions
  the stage serves; this PR's step rests on those versions, read above. Making the start a
  handed shape means a strict parser of the whole start in the sandbox Worker, every field of
  which a release adds or drops: owed below, since every start field change rests on it.
- **Protocol 5's record keeps `string|null`** for the start's model: frozen, and a string is in
  it. The next protocol records `string`.
- **The probe does not hold a request to the named model.** An engine may call others in its
  turn (Claude Code's small model, Gemini CLI's router), so "every request names it" would fail
  honest engines, and each API carries the model elsewhere. An engine that swaps the model
  across APIs fails anyway (the 404 above).
- **The root tests' fake pi calls `/chat/completions` on every API**, as before: the fakes check
  the door's path, key and secret, not each API's path.

## Owed

- [ ] untangle/pi-catalog: pi runs a model its pinned catalog lacks on its provider default's
  API (pi 1.4.0 `buildFallbackModel`), so a pi run on an OpenRouter connection sealed with the
  Messages API, given `claude-fable-*` by `auto`, is refused at the door, and this PR's
  `anthropic-messages` route step fails the pair check the same way. Move pi to 1.6.0 (its
  catalog lists the gateway's 1.6.0 models), or hand pi the run's model record in its
  `models.json`, so the engine never decides a model's API.
- [ ] untangle/start-handed: the owner's start to the sandbox (`SandboxStart`) is a handed shape,
  read by a strict parser in the sandbox Worker and held by `handed.json`, so a start field's
  change is held to the versions the stage serves, as every other cross-Worker shape is.
- [ ] live: the next pair check (the deploy's pair job) passes every route step with its named
  model, and fails pi on OpenRouter's `anthropic-messages` until untangle/pi-catalog lands.
