# registered-agent as built (2026-11-22)

`untangle/registered-agent`, owed by D3 (`agent-definition-door.md`): an outside client reading
the agent definition (`definitionRead`, the MCP door's resources, `agent` prompt and Skills
extension) always got `main`, though an owner may have several agents. The coordinator's call
(`next_builders.md`, "Agent definition"): "the agent its connection registered, else `main`".

## Research first (Fact 14), read on main 48eaf29be

- **The owner's agents** are the folders `/workspace/agents/<agent>/`, each a ledger identity
  while its `agent.json` exists (`native_agents`: the name, `ta_` and its digest, retired when
  the file goes; `Ledger.appear`, `retire`). An agent is named by its name (`AGENT_ID`), as a
  run (`account.run.agent`), `agentRun`'s `agent` and a scoped triple's `agent` name it.
- **A connection** is a grant row (`outside_clients`, `grants.ts`): every MCP client's first
  verified call records one `full`, a key's is written at its mint, and only the owner changes
  one (`grantSet`, in session, against its revision; receipted; a change of what it holds moves
  its generation, ending the client's stamped work and wakes). It already names the client's
  tool profile, the precedent here. The owner sees every one (`grantList`, `/api/grants`).
- **A registration** (`outside_agents`, `agent_register` then the owner's `registerAgent`) is
  an optional mailbox identity: a command center, a display name, a delivery method and a
  recipe. Most connections never file one (a Claude.ai connector reads the definition without
  ever registering), and an MCP client's can change only by its client filing a new card.
- **Which agent, before:** `definitionRead` read `account.run?.agent ?? MAIN_AGENT`. The scope
  step held every account door of a scoped grant as `MAIN_AGENT` (`#scope`'s default), and the
  doorbell and the board's ring each built `{ universe, agent: MAIN_AGENT }` by hand
  (`owner-do.ts`, `collections.ts`): three copies of "an outside client acts as `main`".

## What landed

1. **A grant names the owner's agent its client is (`outside_clients.agent`, migration 23).**
   `main` (`MAIN_AGENT`) until the owner names another in `grantSet` (`agent`, optional, kept
   when omitted, as `profile` is); a named agent must be one of the owner's live agents
   (`Ledger.liveNative`: `no_agent`, `agent_retired`), and a name outside `AGENT_ID` is
   `invalid`. The agent is authority, as the profile is: changing it moves the generation, and
   the receipt's digest names it. `grantList` shows it.
2. **One rule for the agent a call acts as (`authority.ts` `actingAgent`):** a run's own; an
   outside client's, its grant's; the owner's, `main`. The scope step's default agent and an
   account door's `Call.agent` are it, so a scoped grant's triples are held as the agent its
   grant names, and the grant read before a wake (`Grants.reads(client, universe,
   capability)`) uses the row's own agent: the doorbell and the board's ring no longer spell
   `main`.
3. **`definitionRead` serves the agent the call acts as, and refuses one that is not live**
   (`no_agent`, `agent_retired`, Fact 8): a grant naming an agent the owner since deleted gets
   the refusal as the MCP request's error, never `main`'s definition in its place. `main`
   deleted is refused the same way; it used to be served as empty files.
4. **Migration 23 and the export.** The column (an agent id's CHECK, as `native_agents`
   holds), the two capture triggers that write grant rows made again with it, `exported()`'s
   columns, the frozen-bounds guard holding `DEFAULT 'main'` to `MAIN_AGENT`, the owner-bound
   guard counting the 128 bytes, the lock pinned (`node ci/kit/locks.ts pin owner HEAD`).

## Departures

- **The grant, not the registration, names the agent.** The coordinator's call says "the agent
  its connection registered"; the connection's own record is its grant, which every connected
  client and key has, which the owner already edits in session, and whose triples already name
  agents. A registration is an optional mailbox identity most main agents never file, and the
  owner could not change its agent without the client filing again. With the agent on the
  registration, a scoped grant would have held its triples as one row's agent and read its
  definition as another's.
- **No generation from before migration 23 is brought forward (`FORWARD`, `GRANT_AGENT`).**
  The grant table is exported, and no forward form of a line exists yet (`schema.ts`: "built
  in its own PR"). Declared before the cutover, when no owner keeps a generation, as migration
  19 was; after it, that is the founder's call.
- **A key's mint names no agent**: it is `main` until the owner sets it on the grant, as any
  other grant's. The mint's request is unchanged.

## Owed

- [ ] live: at the next dark redeploy, a connector whose grant names a second agent gets that agent's `AGENTS.md` in Claude.ai's `agent` prompt, and one whose agent was deleted gets the refusal.
