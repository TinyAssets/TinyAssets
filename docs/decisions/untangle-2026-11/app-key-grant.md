# app-key-grant as built (2026-11-23)

`untangle/app-key-grant`, finding 3 of `stage-findings-1.md` (#5814): the stage canary's
`TA_PROBE_KEY`, minted in the owner app's API keys screen, was refused `403 this outside
identity is not one enabled agent` at `file_write` and `file_read`. The coordinator chose
option A, and an honest screen.

## Research (Fact 14; #5814's finding 3, checked again here)

- **The mint.** `keysDoors.keyMint` (`owner/src/keys.ts`) always set the key's grant
  `scoped`. `Grants.set` already holds the one rule a grant's mode follows: "a scoped grant,
  and only a scoped one, names scopes". So a mint with nothing ticked was refused `invalid`,
  and a mint with ticks made a scoped grant.
- **A scoped grant's place.** `#universe` (`owner-do.ts`) reads where a scoped client acts
  from its one enabled registered agent's command center, never from the triples. The app
  never registers an agent for a key. So every app-minted key was refused at every door
  that holds a capability.
- **The MCP client's default** is `Grants.admit`'s first-call row, `full` with no scopes (SQL
  literals in the insert). It is not a value that a mint could call. What the two share is
  `Grants.set`'s rule, so the mint now follows it: no scopes named means full.
- **The screen's other two fields were wrong as well.**
  - Its "Space" became the triples' universe, which `#universe` never reads.
  - Its "Agent" became the triples' agent. The file doors hold the triples as the grant's
    own agent (`actingAgent`), `main` for every minted key, because `keyMint` names none. So
    any other agent's triples held nothing at the file doors.
- **The registration door.** `/app/api/outside-agents` (`doors/src/app.ts` `registering`)
  takes `command_center_id`, `display_name`, `delivery_method` and `recipe_id`, all
  required (`contract/doors.json`). It has no defaults: `pull` and a null recipe are what
  its test sends, not the door's.

## Decided

1. **A key minted naming no scopes is full**, as an MCP client's first call records it. The
   mode follows from the scopes by `Grants.set`'s own rule, with no branch for keys. A key
   minted naming scopes stays scoped: an outside agent, acting once registered
   (`outside-agents`), in its agent's command center. The spec now says so
   (`owner-object/spec.md`, grant admission).
2. **The API keys screen mints only full keys.** It asks for a name alone, says what a key
   may do, and lists each key as "everything" or by its scopes. Space, Agent and the
   capability ticks are gone.
   - **Not built: registering the key as an agent at mint through `outside-agents`.** It
     fits in lines, but not as a correct design.
     - Mint then register is two calls in two units. A registration that fails leaves a
       scoped key that acts nowhere, which is this lane's own failure, made again with a
       secret already shown.
     - The screen would have to keep the triples' universe equal to the registration's
       command center itself, a hidden link.
     - The triples' agent must also be the grant's agent. That means `keyMint` naming the
       grant's agent (and `liveNative` checking it), which no door does yet.
   - The right shape is the registration in the mint's own unit. That is its own research
     and lane (owed below). Until then, a scoped key is made by the two owner doors, as
     `api-keys.test.ts` now shows.
3. **The canary's checklist steps** (after `routes`, step 4; the stage, step 5) say how to mint
   the key: a name alone, a key that writes, reads and removes the owner's files and runs its
   main agent. A key the list shows with scopes acts nowhere: turn it off and mint another.

## Problems, under the fix that removes each

- A grant's mode follows from what it names, for a key's mint as for an MCP client's first
  call.
  - `keys.ts` `keyMint` hard-coded `mode: "scoped"`, against `Grants.set`'s rule, so no key
    minted with nothing ticked could exist.
  - `KeysDoorNeeds.grants.set` typed `mode` as the literal `"scoped"`.
  - `keys.ts` and `doors/src/api-keys.ts` described every mint as scoped.
- The screen offers only a key it can make usable.
  - `app.js` `keys()`: Space, read by nothing; Agent, held at no file door; the capability
    ticks, a scoped grant with no registration.
  - The key list printed "0 scopes" for a full key.
  - `app.e2e.mjs` ticked "Read your files" and named the screen's second answer
    (`capabilities`), which is gone.
  - `app-routes.test.ts`'s comment named "a key's" form among the capabilities' readers.
- Left, true now: `owner/test/ledger-support.ts` already said a minted key's grant is full.

## Tests

- `doors/test/api-keys.test.ts`, two new tests.
  - A key minted naming no scopes is full and writes and reads the owner's files through
    `/api`.
  - A key minted naming scopes is refused at `file_write` until registered as an agent in
    the scopes' command center, then writes.
- `doors/test/app.e2e.mjs`: the key the screen shows writes in the owner's workspace through
  `/api`.
- Mutations, each killed:
  - the mint always scoped, killed by the full-key test;
  - the mint always full, killed by the scoped-key test;
  - the screen minting the old scoped triple, killed by the browser test's `403`.

## Owed

- [ ] Scoped keys come with an outside agent's registration: the screen offers capabilities with an agent's display name, and the mint registers the key in the same unit as its grant, the triples' universe the registration's command center and their agent the grant's: `untangle/scoped-key-agent`.
- [ ] live: after the next deploy of the doors and owner, a key minted on the stage with a name alone passes the platform canary's keyed checks.
