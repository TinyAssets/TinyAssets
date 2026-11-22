# grant-agent-signal as built (2026-11-22)

`untangle/grant-agent-signal`, from #5776's review (`registered-agent.md`): the `agent_retired`
refusal's words spoke only of mail, though a grant naming a deleted agent and a definition
read as one get it too; and `grantList` did not show that a grant's agent had since been
deleted, so the owner had no sign of why that connection's definition is refused, nor a way
in the app to name another.

## Research first (Fact 14)

- **The words** live once, `ledger.ts` `AGENT_RETIRED`, beside `NO_AGENT`. The code
  (`agent_retired`, `contract/src/refusal.ts`, 403) is right for every path; the message is
  human text read by no code. Three paths throw it, all through the ledger's liveness check:
  mail to or from the agent (`#member`, and a run's post, `liveNative`), `grantSet` naming it,
  and `definitionRead` as it. So one refusal is correctly shared; only its words were the mail
  path's. `#member` spelled the check a second time by hand (by id, the two refusals copied).
- **`grantList`** is `Grants.list()`, the stored rows. The owner's live agents are the ledger's
  (`natives(false)`: `native_agents` with no `retired_at`), which the door already reaches
  (`GrantsDoorNeeds.ledger`). A stored column would copy what the ledger holds and need moving
  on every retire and return; a field computed at the read cannot drift.
- **The app** (`doors/app/app.js`, Connected apps) listed each grant's profile but neither its
  agent nor a way to set it, though `grantSet` takes `agent` since #5776: the owner could not
  name another agent for a connection at all. `tools/agents_list`, which the screen already
  reads, gives the owner's agents (`owner_agents`, the first page).

## What landed

1. **The words name the case neutrally:** "this agent's agent.json was removed, so nothing
   acts as it and no new mail goes to or from it; its mail is kept, and it is back when the
   file is". One home still; `#member` now uses the one check (`#live`, by id; `liveNative`
   by name), so the refusal is thrown from one place.
2. **`grantList` answers `agent_live`** per grant: whether its agent is one of the owner's live
   agents, from `natives(false)` at the read. No stored column, no migration.
3. **The app's Connected apps row shows the agent and lets the owner choose it:** a select of
   the owner's live agents, the grant's own always among them (marked "(deleted)" when not
   live, with a line saying its instructions cannot be read and how to fix it), so a save never
   moves the agent unasked even when the agents list is paged. Save sends `agent` with the
   profile. `grantSet` checks only an agent named anew (`Grants.set`'s `named`): keeping the
   grant's own, deleted or not, never blocks a save, a narrowing or revoke among them; naming
   a deleted one is refused (`agent_retired`), and its definition read stays refused, as Fact 8
   wants (review fix: the first build refused any save of such a grant).

## Departures

- None from the brief. The app's agent choice is in scope: without it the sign has no fix in
  the app besides restoring the file.
