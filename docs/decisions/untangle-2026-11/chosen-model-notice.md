# chosen-model-notice as built (2026-11-23)

`untangle/chosen-model-notice`, owed by #5842's review (`run-names-model.md`, "What changes
for a run"). Directions 4 and 3, Facts 8 and 15.

Since #5842 an agent that names an engine and a key but no model (every agent the app's form
makes) runs the strongest pickable model `choose` gives it, a scheduled or mail run
unattended too. The owner's spending caps are off by default (Fact 15), so its spend can rise,
and the only sign was the model on each run's line and receipt (`runs.model`). A run's model
chosen for it is behaviour the owner should be told of (Fact 8).

## Research (Fact 14), before the design

### The notices the owner already has

- **`Notices` (`owner/src/notices.ts`)** keeps typed notices in `owner_notices`, the newest
  `NOTICES_KEPT` (1000), each in its unit. `once(type, detail)` keeps one unless the same type
  and detail is kept already: "told once, not once a run". The app's Home lists every notice
  whatever its type (`doors/app/app.js`, `call("notices")`), so a new type needs nothing there.
- **Runs reach it through `RunHost.noticeOnce`** (`Launcher`), as `model_unranked` does.
  `connection_listing_unmatched` and `egress_overspent` are `once` notices keyed the same way:
  what they report is in their detail, so a change of it is told again.
- **A unit's writes before a refusal stand** (`gate.ts`): a notice told in `choose` stays when
  the admission refuses after it (`owner_limit`, the run's environment). So a notice of what a
  run runs belongs after the run is written, not in `choose`.

### Where a run is given a model it did not name

- `choose` gives every run a model (#5842): the agent's, else a plan's (`cli.login.model`),
  else the key's strongest pickable. A model the agent did not name is chosen in three ways:
  `auto` on a key or a sign-in, a named engine on its sign-in, and a named engine on a key.
- **Every run is admitted by `Runs.admit`**, which writes it `queued` once nothing refused. A
  waiting run is admitted whole again at its turn, choosing again; `#wait` only holds it. So
  one site, at the end of `admit`, sees every run as it starts with its model.

### Which runs tell

- **Only a named engine on a key** was weighed, since that is what #5842 changed. Rejected: a
  special case. Whether `auto`, a plan or a key chose it, the agent named no model and was given
  one, and the owner learns which only from each run's line. One rule: any run given a model
  its agent does not name.
- **A plan's model** costs the owner nothing new, but the rule needs no `if` for it, and it is
  one notice per agent.

### Once per what

- **Re-told only when the newest differs** ("told again only when it changes") was weighed and
  rejected. One agent's runs can alternate between two models in normal use: an attended run on
  the owner's plan and its scheduled run on a key when the record does not allow the plan
  unattended, or the owner's run and an outside client's on that client's sandbox. Telling each
  change would tell nearly every run.
- **Chosen: the existing `once`**, its detail naming the agent, the model and where it runs (the
  connection, or the engine's sign-in). So each agent is told once for each model and place its
  runs are given, again when that changes to one it was not told, and never once a run. A
  return to a model already told is not told again while that notice is kept: it is in the list
  already. The place is in the detail because the connection is what pays.
- One mechanism, no new table, no new method: `RunHost.noticeOnce`.

### The app's agent form

- The form (`doors/app/app.js` `agents`) writes no `model`. Showing which model an agent will
  run, from `choose`, needs a door of its own: `choose` reads the run's authority, its sign-ins,
  its keys and whether it runs unattended, tells `model_unranked`, and refuses. A read-only
  preview is a new owner door with its own row, contract shape and app route, and its answer
  differs by authority and by attended or not. Not small: owed below.
- **Small, and done:** the form says, under the engine, that each run is given the strongest
  model it can run on what the owner connected, that each run's line shows it, and how to pin
  one.

## Design

1. **`Runs.admit` tells the owner** of a run given a model its agent does not name, once the
   run is written: `model_chosen`, "`<agent>`: its runs are given `<model>` on `<connection's
   name> (<its id>)` (or `<engine>`'s sign-in), chosen for it because its agent.json names no
   model; name a model in agent.json to pin one", through `noticeOnce`. The connection is named
   as `connection_listing_unmatched` names it (review: an id alone names nothing the owner's
   app shows; its Model sources page lists names).
2. **The app's form** says the same in one line under the engine.
3. **The owner spec's admission** says it.

## Across Workers

- No shape crosses Workers: the notice is a row of an existing table read by the existing door.
  `handed.json` lists no field this PR changes; `deploy/guards/handed.test.ts` passes.
- The `rv` recorder admits runs of agents naming no model and reads `noticeList`, so its
  answers hold the new notice. `rpc` stays the same (CI's recorders shards on 0f88b85, review).

## Problems found

- **A run's model chosen for it was told to nobody** (Fact 8). Fixed: `model_chosen`.

## Left, with reasons

- **`model_unranked` is told in `choose`**, so a run refused after choosing (`owner_limit`)
  still tells it. It reports a state of the ranking (a key's model no entry ranks), true
  whether or not that run was admitted, so it stays.

## Owed

- [x] untangle/agent-model-preview: the app's agent page shows which model the agent's next
  run would be given, read-only, from `choose` without its notices or refusals, for the owner's
  own attended and unattended runs.
