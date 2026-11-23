# agent-model-preview (2026-11-23): the agent's page shows which model its next run is given

`untangle/agent-model-preview`, owed by #5845 (`chosen-model-notice.md`, "Owed"): the app's
agent page shows, read-only, which model the agent's next run would be given, from `choose`
without its notices or refusals, for the owner's own attended and unattended runs. With it, the
small item from that review: `model_unranked` names a connection by its hex id alone, while the
app shows connections by name. Directions 3 and 4, Facts 8 and 15.

## Research (Fact 14), before the design

### Where `choose` has side effects, and its core

- **`Runs.choose` (`owner/src/runs.ts`) reads**: the served records (`servedRecord`,
  `bundledSources`), the owner's reachable keys (`RunHost.reachable`), the sign-ins of the
  run's sandbox (the owner's `engine_signins`, or a client's live lease's), and the sign-in
  switches (`#signinOn`). None writes.
- **It does two things past reading**:
  - it tells `model_unranked` (`picked`, through `noticeOnce`) for the candidate it picks when
    no entry ranks that model and the agent names none, from inside the loop that picks;
  - it throws six refusals: `no_source` (an engine no record serves; nothing takes the agent),
    `signin_off`, `signin_lapsed` (two sites), `unattended_refused`, and `no_source` with its
    reason (a model no key lists; keys that list only models an agent must name).
- **The side-effect-free core** is the pick itself: a source and model, or the refusal that
  says why there is none, and whether the candidate picked is unranked. Each of its exits
  becomes a return (a choice, or a `Refusal` value), and `choose` is that core, then the
  notice of an unranked pick, then the throw. The admission (`admit`, `#wait`, a mail's wait)
  calls `choose` as before, and the preview the core, so both read one pick: no copy.
- **What else admission refuses** is not the model's: the agent disabled, the tier's
  simultaneous runs, the owner's spending caps (`#limits`), a busy conversation. Those are the
  moment's, refused when a run is asked; the preview says only what `choose` would give.

### Which door the app reads it through

- **The agent page (`doors/app/app.js` `agent`) reads no agent fact today**: its Talk view
  reads runs (`agent/submit`, `agent/status`), its Files list `tools/file_list`. The agents
  list reads `tools/agents_list` (`outsideAgents`).
- **Adding the field to `agents_list` was weighed and rejected.** It is the ledger's door
  (`ledger.ts` `agentsListed`), synchronous, from the ledger's own rows, read by every outside
  client as an MCP tool: a field there would tie the ledger to the runs and to an async read of
  each agent's `agent.json` and the ranking file, for every agent on every list, and its answer
  would be the caller's authority's (an outside client's run picks differently), not the
  owner's. Not one fact in one place: a needless link.
- **Chosen: a new owner door, `agentModel`** (route `agent/model`), caller `owner` (as
  `signinList` and `noticeList`: only the owner, so no MCP tool is owed, `owner/guards/
  doors.test.ts`), the `runs` module's handler: the agent read as admission reads it
  (`Launcher.readAgent`: its `agent.json` and the ranking `auto` ranks by), then the core in a
  unit, for the owner's own run attended and unattended. A read: it writes nothing, so a
  preview never tells a notice the run would not.
- **Where a run runs, in words**: `model_chosen` names a key's connection `<display_name>
  (<id>)` (#5845's review), else `<engine>'s sign-in`, built inline in `admit`. The preview
  says the same place, and `model_unranked` is to say it too: one function (`#place`) for the
  three, so the app's line and the two notices name a place alike.

### What the deploy needs (expand, then contract)

- **The owner deploys before the doors, and a rollback takes the doors back first**
  (`door-answers.md`, `deploy/facts.ts` `WORKERS`). The new doors' app calls `agent/model`
  only once the new owner serves `agentModel`; the doors' version before never calls it. A
  new door is the expand step; there is nothing to contract.
- **The door table declares what the app reads** (#5843): `agentModel`'s row declares its
  answer, `attended` and `unattended`, each an object, so the plan's hold holds the owner to
  them from the next deploy on. `handed.json` lists no shape this changes; the new row's
  request is the table's (`args`), held by `deploy/guards/handed.test.ts` as any new door's.
- **Recorders**: a new owner door moves `rpc` (the owner's methods) and `rv` reads the
  notices, whose `model_unranked` detail now names the connection: `Recorders-Differ: rpc, rv`.

## Design

1. **`Runs.#pick`**: `choose`'s body, each throw a return, and the unranked candidate
   returned beside the choice. **`choose`** = `#pick`, then `model_unranked` for an unranked
   pick, then the refusal thrown. **`preview(file, ranking, now)`** = `#pick` for the owner's
   own run, attended and unattended, each the engine, model and place, or the refusal's words.
2. **`#place(engine, connection)`**: the one naming of where a run runs, for `model_chosen`,
   `model_unranked` (the fold-in: `<display_name> (<id>)`) and the preview.
3. **`agentModel`** door, route `agent/model`; its row's answer declared.
4. **The agent page** shows two lines under its name, read once as it opens: a run the owner
   starts there, and an unattended one (a schedule's, mail not from the owner).
5. **The owner spec's admission** says it.

## Problems found (untangle step 2)

- **The pick and its side effects were one function.** `choose` told `model_unranked` and threw
  from inside its loop, so nothing could ask what a run would be given without telling a
  notice or refusing: the reason the preview was owed. Fixed: `#pick`, which both read.
- **A run's place was named in one site, inline** (`admit`'s `model_chosen`), and
  `model_unranked` named a key by its id alone: two namings of one thing, one wrong. Fixed:
  `#place`.

## Left, with reasons

- **The preview reads the owner's own runs only.** An outside client's run picks by that
  client's sandbox's sign-ins; no surface asks it, and the owed line names the owner's.
- **The preview is read as the page opens**, not live: a connection or sign-in changed after
  shows on the next open. Each run's line shows the model it was given.
- **`connection_listing_unmatched` names a connection in `connections.ts`** from its own row,
  the same words: a different module's notice of a connection, not of a run's place.
