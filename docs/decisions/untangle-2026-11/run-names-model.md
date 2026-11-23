# run-names-model as built (2026-11-23)

`untangle/run-names-model`, owed by `openrouter-messages.md` (#5837's review). Directions 2
and 4.

An agent that named an engine but no model ran an API-key run with `model: null`:
- `sandbox/runner/engine.ts` then left `--provider/--model` out, so pi picked its own default
  model (pinned pi 1.4.0's `defaultModelPerProvider.openrouter` is `moonshotai/kimi-k2.6`);
- the owner gave that run the first API of its connection's record as its base URL
  (`launch.ts` `#runApi` null, `contract/src/egress.ts` `sealedBase(egress, null)`).

So the record's API order was tied to an engine's default model by nothing. `auto` already
names a model on every run (`runs.ts` `choose`). This PR makes the owner name the model of
every run.

## Research (Fact 14), before the design

### Every path that admits a run

- **Every run is admitted by `Runs.admit`, which calls `choose` once,** and builds the run's
  environment (`prepare`, `launch.ts` `#runConfig`) in the same unit. That covers:
  - the doors' runs (`launch`);
  - the lane's runs (`#laneLaunch`): schedules, mail, a waiting run's turn, and a run that
    waited for a sandbox (`sandboxRung` admits it whole again);
  - code mode, whose `tools.*` calls reach those doors (`owner/src/codemode.ts`).
- **A waiting run chooses again** when its turn comes (`admit` with `waiting`). So no stored
  row's `model` reaches a run's environment: `runs.model` stays nullable only for receipts
  written before.
- **Subagents are not runs.** A run's engine may call other models inside its run (Claude
  Code's subagents, pi's model switch). Those are model calls on the egress door. Each is held
  to a model its connection lists on the API its path speaks (`scopeApi`, `modelApi`), whatever
  the run's own model is.
- **A run's model before this PR:**
  - the vendor's sign-in: the agent's, else `cli.login.model` (`strongest-plan.md`);
  - `auto`: always one;
  - an explicitly named engine on a key: `file.model`, null when the agent named none.

  Only that last case reached the engine's own default.
- **The route probe** (`sandbox/runner/probes.ts`) is the other run with no model. It is the
  template's check, not an admitted run, and it reads no catalog (pi-ai is the gateway's; the
  probe bundle has the source records alone). So it cannot name a model today.

### What model a run that names none should be

- **The rule `auto` uses** (Direction 4, "default to their strongest connected source"):
  - the ranking (`auto.ts` `ranked`: the owner's file, else the platform's);
  - never a model the key's source record marks `not_interactive`;
  - on a key that pays for nothing, only its source's free models (`sources/src/ranking.ts`
    `pickable`).
- **The explicit-engine path had a second chooser.** `keyFor` took the strongest candidate
  whose source lists the engine. It applied neither check, and it kept `file.model` (null)
  instead of the candidate's model.
- **The same filter serves both paths** once it is one function (`takes`): the named
  connection, the named model, else pickable. Only the engine filter differs:
  - `auto` takes any engine the source lists;
  - a named engine takes the sources that list it, or the connection the agent names.

### Refuse instead?

A refusal ("name a model in `agent.json`") was weighed for an explicitly named engine with no
model, and rejected:

- Direction 4 makes the strongest connected model the default, and an agent that names its
  engine is asking where to run, not to pin a model.
- A pinned model ages. When the vendor stops listing it, the run is refused (`no_source`,
  "no connected key lists …"), so pinning everywhere would turn a vendor's model change into
  refused runs.
- `auto` and a sign-in run already name a model. A third rule for one path would be a
  special case.

A refusal is kept where no model can be picked: keys that list only models a run is given
when its agent names one (batch variants, or no free model on a key that pays for nothing).
That refusal now says so, with the remedy, on `auto` and on a named engine alike.

### What changes for a run

- An explicitly named engine on a key with no model runs the connection's strongest pickable
  model instead of the engine's default. pi on OpenRouter ran `moonshotai/kimi-k2.6`; it now
  runs what `auto` would (for example `~anthropic/claude-fable-latest`, or the `:free` model on
  a key that pays for nothing). That is Direction 4, under the owner's own caps (Fact 15). An
  agent pins a cheaper model by naming it.
  - Review: the app's agent form (`doors/app/app.js`) writes no `model`, so every agent made
    there on an engine and a key changes this way, a scheduled one unattended too. The
    owner's caps (`MODEL_DEFAULTS`) are off by default, so only the vendor's limits bound the
    rise. The owner sees the model on each run's line and receipt (`runs.model`), and is told
    nothing else.
- An explicitly named engine with a named model now runs on a connection that lists that
  model. The model was not checked before, so its calls were refused at the door
  (`egress_unknown_model`). A model no key lists is now refused at admission, saying so, as on
  `auto`.
- The `model_unranked` notice is told for any run given an unranked model it did not name, not
  only an `auto` run. Its text no longer says "auto".
- A named connection whose models are all unpickable is now refused, with the remedy. Before,
  such a run was admitted with the engine's own default, which that connection did not list,
  so its calls were refused at the door.

### pi and the model it is named

- pi 1.4.0 (`npm view @earendil-works/pi-coding-agent@1.4.0`, its published docs, `cli.md`)
  selects `--model` "by exact ID or fuzzy ID/name match", restricted to `--provider`.
- Its bundled catalog (`@earendil-works/pi-ai@1.4.0`, `providers/data/openrouter.json`) holds
  `anthropic/claude-opus-5.5` and the Fable and Sonnet models.
- A model the owner's catalog has and pi's lacks is the engine's own record moving, as
  `openrouter-messages.md` and `model-catalog.md` record. `auto` already names such models.

### Across Workers (deploy order gateway, sandbox, owner, doors)

- **The start (owner to sandbox).** `SandboxStart.model` and `StartDoc.model` stay
  `string | null`. The owner of this release always hands a string, which the sandbox before
  takes. The sandbox of this release still takes null, which the owner before hands for an
  explicitly named engine during the window, and which the route probe sends.
- **`EngineRun`, `sealedBase` and `sandboxEnv`** are bundled into each Worker that calls them
  (the owner and the sandbox's probes). No shape crosses Workers.
- `handed.json` lists no field this PR changes. `deploy/guards/handed.test.ts` passes.

## Design

1. **`choose` names every run's model.**
   - One filter (`takes`) for `auto` and a named engine: the named connection, the named
     model, else `pickable`.
   - A named engine on a key takes the strongest candidate it passes, of a source that lists
     the engine or on the named connection, and is given that candidate's model. `keyFor`, the
     second chooser, goes.
   - `choose`, `Chosen` and `Admitted` type `model` as `string`.
   - The `model_unranked` notice applies to every run given a model it did not name.
2. **A run's base is its model's API's, always.**
   - `#runApi` returns the API of the model its connection lists, which admission chose
     from that list in the same unit. Null on the vendor's own sign-in and, only through a
     bug, for a model the connection does not list: `sandboxEnv` refuses an API-key run given
     none, the one refusal of that rule (review: a second refusal in `#runApi` was
     unreachable, so its mutation survived; it went).
   - `sealedBase(egress, api)` takes a string. Its first-API fallback goes.
   - `EngineRun.api` is null on the vendor's own sign-in alone, as `connection` is, and
     `sandboxEnv` refuses an API-key run given none.
3. **No-source refusals say why.**
   - A named model no key lists: "no connected key lists X", now on a named engine too.
   - Keys that list only models an agent must name: the new `UNPICKED` detail.
4. **The route probe names the API it probes** (`api`, its first), once, for its base and for
   the reply it answers in. It still names no model (left, below).
5. **Docs follow:**
   - `agent.json`'s `model` (`agent-definition.ts`);
   - the record schema's note on API order (`schema.ts`), now the probe's alone;
   - the owner spec's admission, base URL and ranking text, and its batch-variant scenario.

## Problems found

- **A hidden link between a record's API order and an engine's default model** (`#runApi`
  null, `sealedBase` null, the schema's "a record lists first the API its engine's default
  model speaks"). Fixed for every admitted run: each is given its model, and so its model's
  API's base. The probe keeps it (left).
- **Two choosers in `choose`.** `keyFor` repeated `auto`'s candidate walk without its checks,
  and dropped the candidate's model. Fixed: one filter, whose candidate's model the run is
  given.
- **A named engine's named model was never checked against its connection.** Fixed: the same
  filter.
- **`#runApi` mapped "a model its connection does not list" to the first API**, a quiet
  fallback (Fact 8). Fixed: an API-key run given no API is refused (`sandboxEnv`).
- **The unranked notice said "auto"** for what is now every run's choice. Fixed.
- **The probe spelled its API twice** (`apis[0]` for the reply, `null` for the base). Fixed:
  once.

## Left, with reasons

- **The route probe runs each engine with no model** (`probes.ts`), so the engine picks its
  default there. Its base and its reply are its record's first API, so a record still lists
  first the API its engines' default models speak. If one does not, the probe fails loudly,
  since its answer is in the wrong wire format.
  - To name a model, the probe needs one per sealed API, and its bundle has no catalog.
  - Until it does, `engine.ts` keeps its no-model branch, which the sandbox also needs for
    the owner before this release.
  - Both are owed below.
- **`runs.model` stays nullable**, for receipts written before this release. Nothing builds a
  run from it.

## Owed

- [ ] untangle/probe-model: the route probe names a model per sealed API. Its plan's step
  carries the model, for example from the gateway's catalog, so no engine runs its own default
  anywhere and a record's API order means nothing. Then, once the stage serves no owner before
  this release, the start requires a model: `SandboxStart.model` and `StartDoc.model` become
  `string`, and `engine.ts`'s no-model branch goes.
