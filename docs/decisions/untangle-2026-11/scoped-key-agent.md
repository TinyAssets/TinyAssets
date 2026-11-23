# scoped-key-agent as built (2026-11-23)

`untangle/scoped-key-agent`, owed by `app-key-grant.md` (#5817): an API key minted naming
scopes has no registered agent, so every call that needs one (`file_write`, a mailbox, its
rooms, a narrowed grant's work) is refused `403` `NOT_AN_AGENT`, which tells it to file
`agent_register`. The live stage canary shows it with the old scoped `TA_PROBE_KEY` (deploy
runs 45191373564 and 45200348628: `file_write answered 403: this connection has no
registered agent`). Brief: the owner minting a key in its own signed-in app is already the
owner's approval, so a scoped key gets its agent at mint, by the one path an agent exists by.

## Research (Fact 14)

- **The mint.** `keysDoors.keyMint` (`owner/src/keys.ts`) runs in the owner's session, in one
  unit: the live keys counted (`Keys.add`), the grant set (`Grants.set`, source `api_key`,
  scoped exactly when it names scopes), an agent's held key request applied
  (`applyKeyRequest`), the key's row. The doors' `mintKey` (`doors/src/api-keys.ts`) then
  writes the directory entry and shows the secret once. A refusal in the unit writes nothing.
- **Why a narrowed grant needs a registered agent.** A scoped grant holds
  `{universe, agent, capability}` triples (`authority.ts` `may`). Where its client acts, the
  universe, is read from its one enabled registered agent's `command_center_id`
  (`OwnerDO.#universe`), at every account door that holds a capability and at run doors. A
  full grant holds everywhere and reads no place. The mailbox and rooms need an agent for
  every grant (`Ledger.#self`). So for a scoped key the agent is its place, not only its
  mailbox.
- **How an agent exists.** One rule, `Ledger.register` (with `Ledger.registration`, the
  checks every registration holds): the verified origin and source (`registrant`: an
  `api_key` source's origin re-derived from the key id), the place, a display name, a
  delivery method, a recipe; one enabled agent per outside identity. Two callers, both the
  owner's act in session (`registerAgent`): applying an MCP client's card (#5821's
  `registrations/apply`, answered on Home since #5826), and `outside-agents`, the owner's
  browser registering an agent for a key it holds. `agent_register` refuses an API key
  (`own_card`: "an API key's agent registers from the owner's browser with the key"), so
  `NOT_AN_AGENT`'s remedy was one a key cannot follow. No screen calls `outside-agents`
  since #5817.
- **Where a registration's place comes from.** `centerOf` (#5821): a narrowed grant's agent
  registers in the one space its scopes name (a named one among several), a full grant's
  where named, else `HOME_CENTER`.
- **What a key's agent holds after its key.** A revoke (`grantRevoke`, from "Turn off",
  `keys/revoke`, `keys/delete`; a set to `revoked`; a restore's fence) deletes the key's
  directory entry, so the key never authenticates again. Its agent stayed enabled: a reserved
  mailbox (`maxAgents`), a registry row (`maxAgentRows`), and a count on its grant row that
  keeps the row from being forgotten (`#forget`, `evictRevoked`: "an agent ... still holds
  this client's grant"), so `keys/delete` of a key with an agent was refused after it had
  revoked the key. `MAX_KEYS` and `maxAgents` are both 1,000.
- **Comparable products bind a key to its identity and place at creation, in one act by the
  person entitled to:**
  - OpenAI project keys: a key is made inside one project, owned by a user or by a service
    account, a non-human identity of the project; its scopes narrow within the project role,
    and its secret is shown once (OpenAI "API keys and service accounts" guide; Admin API
    project API keys, `owner.type` `service_account` or `user`).
  - Anthropic workspace keys: a key made in a workspace only works in that workspace, and
    needs no workspace header (Claude Platform "Workspaces").
  - GitHub fine-grained PATs: the resource owner and repositories are chosen on the token's
    creation form, and the token reaches only that owner's resources; an organization may
    hold it pending its admin's approval (GitHub docs, "Managing your personal access
    tokens").
  - None makes the key's holder register its identity afterwards: the identity is the key's,
    from creation, gone with it.

## Decided

1. **A key minted naming scopes is registered as an agent in the mint's own unit**, by
   `Ledger.register`, the one path an agent exists by: the key's origin and `api_key`
   source, the place `centerOf` gives its scopes (the one space they name), the key's name
   as its display name, `pull` delivery, no recipe. Its triples' agent is the grant's
   (`main`, as `keyMint` names none) and their universe the registration's, by construction:
   no screen has to keep the two equal. A registration refused (several spaces, a name with no
   letter or digit, the enabled agents at their bound) refuses the mint, which writes no grant,
   row, entry or secret. No second mechanism: no card, no new table, no flag.
2. **A full key registers none**: its grant holds everywhere without a place, and a mailbox is
   an optional identity, as for an MCP client's full grant (`registered-agent.md`). Giving every
   key an agent would spend one of the owner's enabled agents (1,000, the same as `MAX_KEYS`)
   per key, a CI script's included.
3. **A key's agent goes with its key's revoke** (`Ledger.removeKeyAgents`, in the revoke's
   unit, wherever a grant is revoked: `grantRevoke`, `grantSet` to `revoked`, the restore's
   fence): every agent registered with an `api_key` source under that client, removed as the
   owner removes one, mail and rooms with it. A revoked key never acts again, so its agent
   would only hold a mailbox, a registry row and its grant row. An MCP client's agent is kept:
   its client may come back from a sign-in after the revoke. This also makes `keys/delete`
   forget a key that had an agent.
4. **Keys minted with scopes before this deploy keep the refusal, with a remedy they can
   follow.** No migration registers them: only the stage holds one (the old `TA_PROBE_KEY`,
   minted by the pre-#5817 screen), whose space and agent the owner typed into fields nothing
   read, so an agent made from them could still hold nothing (its triples' agent need not be
   the grant's). `NOT_AN_AGENT` now adds: "an API key has one only when minted with scopes, so
   ask the owner to mint a new key". The checklist's stage step 5 says the same.
5. **The screen keeps minting full keys** (#5817). The owed line asked it to offer
   capabilities again; not built. A scoped key's triples name a space and an agent, which the
   owner would pick for no reason of its own (a feature editor, Direction 3). The real source
   of a scoped key is an agent's key request (`api_key_request`), whose card names its scopes
   and is applied by `keyMint` with `proposal`: Home's answer to it is `untangle/policy-answers`'
   (that lane's scope). The mint registers that key's agent the same way.
6. **Expand-then-contract** (`deploy/handed.test.ts`): no door, field or binding changes, only
   the owner's behaviour behind `keyMint` and the revokes, and the refusal's text. The owner
   deploys before the doors; the doors' version before sends the same mint.

## Problems, under the fix that removes each

- A scoped key's agent is made with its key, by the one registration rule.
  - `keyMint` set a scoped grant whose place (`#universe`) only a registration gives, and made
    none: every scoped key was refused at every capability door.
  - The only way to give it one was a second call (`outside-agents`) from no screen, the
    universe kept equal to the triples by hand (`app-key-grant.md`'s hidden link).
- A key's agent lives as long as its key.
  - A revoked key's agent stayed enabled, holding a mailbox, a registry row and its grant row;
    `keys/delete` was refused once it had revoked the key.
- The refusal names a remedy the caller can follow.
  - `NOT_AN_AGENT` told an API key to file `agent_register`, which refuses API keys.
- Where a grant's agent registers is the grant's rule, so it lives with the grant's triples.
  - The mint first imported `centerOf` from `registrations.ts`, a module only the object may
    reach (the owner's `policy-boundary` guard, red on CI). `centerOf` and `HOME_CENTER` read
    only a grant, as `may` does, so they moved to `authority.ts`, which the keys and the
    cards both import (review).
- Tests.
  - `doors/test/api-keys.test.ts`'s scoped-key test registered through `outside-agents`; it
    now shows the agent made at mint, the key writing and reading its mailbox at once, and the
    agent gone with "Turn off". A new one: a scoped mint whose agent cannot register keeps
    nothing.
  - `owner/test/admit.test.ts` counted `MAX_KEYS` scoped keys; it counts keys, so it mints full
    ones (1,000 scoped keys would meet the enabled agents' bound first, decided 2).
  - Tests listing a scoped key's agents (`api-keys`, `profiles`, `mcp`) now see its own agent.
  - `mcp.test.ts` spelled `NOT_AN_AGENT` out; it reads the constant.

## Found and left

- A scoped key's agent counts among the owner's enabled agents with its own and its apps'
  (`LEDGER_LIMITS.maxAgents`, 1,000, its mailboxes reserved). A scoped mint past it is refused
  in its unit, "the owner's enabled agents are at their limit": the ledger's recorded ceiling
  (Fact 15), loud and recoverable by turning a key off.
- "Turn off" removes a key's agent with its mail and rooms: the owner's own messages with
  that key's program go with it. The key's program never reads them again either way.
- A key's name is its agent's display name as `displayName` keeps it (letters, digits,
  spaces, dots, dashes); a scoped key named with none of those is refused at mint.
- The owner may later name another agent on a key's grant (`grantSet` `agent`), which leaves
  triples naming `main`. Connected apps lists no key (`source !== "api_key"`), so no screen
  does it; it is any scoped grant's, as `registered-agent.md` left it.
- The restore's fence removes the agents of the keys it revokes (`Grants.revokeAll` returns
  only clients not already revoked). A generation captured before this deploy may hold the
  agent of a key revoked before it; such an agent is restored, and goes with that key's
  `keys/delete` (its `grantRevoke` runs the revoke's removal again). Stage only.
- A full key that calls a mailbox gets `NOT_AN_AGENT`, whose "mint a new key" gives it none
  while the screen mints full keys only: `untangle/key-agent-change` (owed below).
- `centerOf`'s refusal for scopes naming several spaces says "name one ... as
  `command_center_id`", a field the mint does not take. No screen mints such a key; owed
  below with the key request's answer.

## Owed

- [x] `untangle/policy-answers`: a key request applied on Home mints a scoped key whose agent registers where the card's scopes act; a card naming several spaces is refused at the mint by `centerOf`, whose text names `command_center_id`, which the mint does not take: that lane decides whether a key request names one space. Done (`untangle/drop-key-request`): policy-answers made no key request kind, and the half-flow is gone, so no card names any space.
- [x] `untangle/key-agent-change`: a full key's mailbox, and a key's agent changing its delivery (`mcp_event` for Muse by key, `mcp-keys.md` 5), are reached only by `outside-agents`, which no screen calls; research whether a key files its own `agent_register` card for the owner to answer on Home, so `outside-agents` and its session pairing go. Done (`key-agent-change.md`): a key files its own card, applied on Home; filing again changes its delivery; `outside-agents` and the pairing are gone.
- [ ] live: after the next deploy of the owner and the doors, on the stage: the old scoped `TA_PROBE_KEY` is refused with `NOT_AN_AGENT`'s new remedy, and a key minted with a name alone passes the platform canary's keyed checks.
