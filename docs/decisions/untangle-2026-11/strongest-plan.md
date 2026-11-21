# strongest-plan: a subscription ranks by the model its plan runs (2026-11-21)

`untangle/strongest-plan`. Direction 4: "Users connect any model source; default to their
strongest connected source." The starter's main agent runs on `engine: "auto"`.

## The bug, as found on main (`45d2b1bcf`)

- `owner/src/runs.ts` `choose()` ranks the owner's keys and signed-in subscriptions together
  (`auto.ts` `ranked()`), each plan by "the models the runner read it offers" (`SignIn.models`).
- The runner never reads them: `sandbox/runner/runner.ts` `reportSignins` posts
  `{ engine, signed_in }` only. So every plan's model is `""`, `rankOf` matches no entry, and
  the plan sorts after every ranked key at its fake output price of 0.
- A plan therefore wins only through the same-provider rule (a key of its provider is used as
  the plan). Claude Max plus an xAI key picks the xAI key, though `claude-opus-5*` ranks far
  above `grok-*`. No test put a plan against another provider's key.

## Research (Fact 14), before the design

### What decides which model a subscription's run gets

Each vendor's CLI, at the version its record pins, on the plan's sign-in: the run passes no
`--model` today (`model: null`), so the CLI's own default decides.

| Engine (pinned) | Default on a plan | Can it list or report its models non-interactively? |
| --- | --- | --- |
| Claude Code 2.1.309 | Opus 5.5 (`claude-opus-5-5`) on Pro, Max, Team and Enterprise alike. code.claude.com/docs/en/model-config (read 2026-11-21): "Pro, Max, Ultra, Team, Enterprise, and Anthropic API: defaults to Opus 5.5"; `opus` resolves to Opus 5.5 from v2.1.280; "No Fable model is the account-type default on any plan". | No listing command or flag. `--model` sets it; a `-p` run reports the model that ran afterwards (the result's `modelUsage`), not before. |
| Codex 0.166.0 | The account's catalog's first model. The pinned binary bundles a `models.json` (read from the `@openai/codex@0.166.0-linux-x64` binary's strings): `gpt-6.1-sol` is priority 1, then `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`. The live catalog is fetched per account and can differ (an OpenAI community report of a model callable but missing from `model/list`). | Only through `codex app-server`'s JSON-RPC `model/list` (account-aware, but it may serve a bundled or cached catalog). No plain command. |
| Gemini CLI 0.66.0 | `auto`, resolved per account at run time (`resolveModel` in the pinned bundle): `gemini-3.1-pro-preview` where the server's Gemini 3.1 flag is launched and the account has preview access, else `gemini-3-pro-preview`, else `gemini-2.5-pro`. | No. The account's access is read inside the CLI (`getHasAccessToPreviewModel`, `getGemini31Launched`); `/model` is interactive. |
| Kimi Code 2.4.1 | `kimi-for-coding` (the plan's one model; `kimi-for-coding-highspeed` needs a higher tier). After `/login` the CLI's managed provider aliases it as `kimi-code/kimi-for-coding`, and `-m, --model` takes "LLM model alias ... Defaults to default_model in config.toml" (the pinned `dist/main.mjs`). | Its `/login` reads the plan's models from the vendor API into its config; no command prints them. |
| pi 1.4.0 | No subscription: the `pi` record carries no sign-in (ADR-034 decision 3). | `pi --list-models`, but only keys reach pi. |

So reading the models from the CLI is not one mechanism: only Codex can, through a JSON-RPC
session, and only Kimi writes them to a file. A runner that read them would need a
per-engine reader for two engines and nothing for the other two.

### What "strongest" is ranked from today

- `platform/owner/src/model-ranking.json`: 20 hand-ordered entries (provider or `*`, a model
  glob), read by `auto.ts` `ranks()`. Written once by hand in G2+A1 (#5364, 2026-10-29) and
  never updated since; its `about` says "a package keeps it current", but no package does.
- A model no entry matches sorts after every matched one, by catalog output price. A plan's
  candidate has no catalog price (`planModel` gives 0), so an unmatched plan sorts last of all.
- The founder owns what the order means. No one maintains it today; this PR adds one entry
  (below) and names the gap as a founder question.

### The clean shape, checked against ADR-034 and the intent

Candidates weighed:

1. **The runner reads the models from the CLI** (the shape `SignIn.models` was built for).
   Rejected: two of the four sign-in CLIs cannot report them, so it needs either a special
   case per engine or a plan that ranks unknown for Claude Code and Gemini, which is the bug.
2. **The engine record declares, as data, the model its plan runs**, and the run passes it.
   Chosen. Engine differences are data (Direction 2; ADR-034 decision 4.3's "one declarative
   engine record"); the value sits beside the version it is true of, so a version bump and its
   model change in one place; every plan enters the ranking with a real id.

How it is held:

- `cli.login.model` is `{ id, citation }`: the model a run on the vendor's sign-in is given,
  the vendor's default for a plan on the pinned version, in the form `engine.model_argv`'s
  `{model}` takes. The record check requires it on every record that signs in, and requires
  such a record to carry `engine.model_argv`.
- The run passes it (`--model <id>`), unless the agent's file names a model. So the run is
  the model it was ranked as, and the receipt says which (`model`), where it said `null`.
  Where an account cannot run the declared model, Claude Code, Codex and Kimi fail the run
  (Fact 8). Gemini does not: the pinned bundle's `resolveModel` turns an explicit
  `gemini-3.1-pro-preview` into `gemini-2.5-pro` for an account without preview access, so
  that run's receipt names the model asked for, not the one that ran. One model per record
  stays: no runner report names the account's plan, so per-plan models would need a new
  research step and a plan signal from each CLI (founder question 2).
- An `auto` agent that names a model picks among the sources that offer it: a key whose
  connection lists it, or a plan whose record names it (a key of a plan's provider that
  lists it still takes the plan). One no source offers is `no_source`, never passed to
  another vendor's CLI (review). To run another model on a plan, the agent names the engine.
- An owner test holds every sign-in record's model to a ranking entry, so a plan can never
  rank unknown by construction: both files are bundled data, so no admission-time check is
  needed, and none is added.
- The runner-reported `models` goes: nothing ever sent it, and it is now a second, unread
  source. The report door stops taking it, and `SignIn` stops carrying it.

This is what "default to their strongest connected source" asks: the plan is ranked by what
it runs, by the same ranking as every key.

## Problems found (untangle step 2), with the fix above each

- **The plan's model is the engine record's data (`cli.login.model`), passed on the run.**
  - `runs.ts` `choose()` ranks a plan by `SignIn.models`, which nothing fills: the plan is
    `""`, unranked, last (the bug).
  - `runs.ts` `planModel("")` and `candidate.model.id !== ""`: a special case for the empty
    model the missing data made.
  - `runs.ts` builds a client's sign-ins with `models: []`, a value copied only so the type fits.
  - A plan run passes `model: null`, so the receipt cannot say which model ran, and a plan
    ranked as one model may run another.
- **The runner-reported models go (dead path).**
  - `reports.ts` `signinRequest` takes an optional `models` the runner never sends, checked
    against `SIGNIN_MODELS_CHARS`; `signins.ts` `SigninRequest.models`; `runs.ts`
    `reportSignin(…, models, …)` and `SignIn.models`; the spec's "the models the runner read
    it offers".
- **The ranking gains `kimi-for-coding`**, so the Kimi plan ranks (it matched no entry).
- Left, with reasons:
  - The frozen schema's `engine_signins.models` column stays, written `'[]'`. Dropping it is a
    new owner migration (the column is spelled in two change-log capture triggers, the
    export's table list and restore) with its `migrations.json` digest. **Owed below.**
  - The same-provider rule is unchanged, as asked: a key of a plan's provider takes the plan,
    at the key's rank. With a model now declared, that rank can be the key's stronger model
    (an Anthropic key's Fable) while the plan runs Opus. **Founder question 2.**
  - `auto`'s answer says which source it picked (the receipt's `engine`, `credential`, and
    now the plan's `model`), not why: no surface carries a reason today, and none is added.
    A key run's receipt does not name its connection either (the run row keeps none).

## Owed

- [ ] `untangle/drop-signin-models`: an owner migration drops `engine_signins.models` (its
  capture triggers remade, the export list and `SIGNIN_MODELS_CHARS` with it).
- [ ] `live:` a signed-in run of each sign-in engine with its record's `cli.login.model` passed
  runs that model (Claude Code `claude-opus-5-5`, Codex `gpt-6.1-sol`, Gemini
  `gemini-3.1-pro-preview`, Kimi `kimi-code/kimi-for-coding`), in the founder's sign-in tests.
  Gemini's and Kimi's values are read from their pinned bundles, not run.

## Founder questions

1. **What "strongest" means between vendors.** The ranking puts `gpt-6.1-*`, `gpt-6-*` and
   `gemini-4-*` above `claude-opus-5*`, so Claude Max plus an OpenAI key on `gpt-6.1-sol`
   picks the metered key over the paid plan. Should a plan the owner already pays for win
   over a key within some distance in the ranking? And who keeps `model-ranking.json`
   current (no package does)?
2. **A plan's top model or its vendor's default.** Each record declares the vendor's default
   (Claude: Opus 5.5, though Max may offer Fable). Should `auto` instead name a plan's top
   model, at the cost of its usage limits?
3. **Where to rank Kimi.** `kimi-for-coding` is placed beside `grok-*`, a guess.
