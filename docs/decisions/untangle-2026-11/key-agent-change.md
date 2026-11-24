# key-agent-change as built (2026-11-23)

`untangle/key-agent-change`, owed by `scoped-key-agent.md` (#5831) and `drop-key-request.md`
(#5845): a full key's mailbox, and a key's agent changing its delivery, were reached only
through the owner surface's `outside-agents`, which no screen calls; with the key request
gone, whether `keyMint` keeps `scopes`, and so whether `centerOf`'s "name one ... as
`command_center_id`" can reach a mint.

## Research (Fact 14)

- **What an owner does with any outside agent, and where.** Three acts, each on one surface
  today, for an MCP client's agent:
  - *Approve its registration*: the client files `agent_register` on its own verified call
    (`registerPropose`), the card captures its origin, source reference and grant stamp
    (`Registrations.file`), and the owner applies or declines it on Home's "Waiting for you"
    (`registrations`, `registrations/apply`, `registrations/decline`; `doors/app/app.js`
    `KINDS`), which names the asker by its grant row's name (`appOf`, from `grants`).
  - *Change its delivery*: the same identity filing again, applied, is a re-registration in
    the same place (`Ledger.register`'s update branch: one revision on, the old kind's
    subscriptions erased). There is no other delivery edit, and none is needed.
  - *See it, and hand it a doorbell*: Connected apps' "Your main agents" lists every enabled
    outside agent (`agents_list`' `agents`, whatever its source), each with the doorbell form
    of its kind (`agent/doorbell`, for the kinds whose record has the owner paste a token).
    Its mail is its rooms with the owner (`withRooms`).
- **What a key's agent had instead.** `agent_register` refused an API key (`own_card`: "an
  API key's agent registers from the owner's browser with the key"), so a key's registration,
  and so its delivery change, went only by `outside-agents`: the owner's browser posting the
  key id and the request, the doors reading the key's directory entry (`registeringEntry`)
  and binding the key's origin together with the owner's session (`bindKeyRegistration`), the
  one binding that pairs the two, which the object let through at one door alone
  (`registering: true`, `Serves.registering`, `MCP_REGISTERS_BY_CARD`). No screen has called it
  since #5817. A full key's program told by `NOT_AN_AGENT`, or by `inbox_digest`'s
  description, to "file one with agent_register" was refused.
- **Why a key filed no card.** `own_card`'s refusal predates keys reaching `/mcp`
  (`mcp-keys.md` 1, 2): it held the registrant to a source the owner's browser could vouch
  for. But a key's call is as verified as an MCP client's: `presentedKey` checks its secret,
  the binding's origin is `apiKeyOrigin(id)`, and the object re-derives it from the key id at
  every registration (`registrant`). A card filed by that call captures the same identity
  `outside-agents` named, with no owner-held key id in between.
- **What the main agents need from a key to receive events** (`contract/wake-kinds.json`,
  `contract/apps.json`):
  - Meta Muse is the one app whose connect steps use a key (a bearer header in its secure
    credential prompt). Its record has no push (`muse_recurring_task`, delivery `null`):
    its agent registers `pull` and its recurring task reads `inbox_digest` and
    `board_digest`. So all Muse by key needs is an agent at all: a mailbox, which a full key
    could not get. `mcp-keys.md` 5's `mcp_event` "for Muse by key" is any key client that
    subscribes over `/mcp` (`events/subscribe` finds the subscriber by origin, any source).
  - ChatGPT (`chatgpt_mcp_events`) connects by sign-in (CIMD) and subscribes over MCP Events
    on its own call; Claude's scheduled tasks (`claude_routine_fire`) and Grok routines
    (`grok_webhook_routine`) take the doorbell the owner pastes, for an agent of that
    delivery. A key's program using either needs only the same: its agent registered with
    that delivery, then the subscription or the pasted doorbell, both of which already take
    any agent.
  - So a key needs one thing the product lacked: to register, and re-register, its own agent.
- **Comparable products.** A token's holder asks; the account's admin approves where the
  product already lists approvals: GitHub fine-grained tokens targeting an organization wait
  "pending" until an organization owner approves them in the organization's settings
  (GitHub docs, "Managing your personal access tokens"; "Managing requests for personal
  access tokens in your organization"). Slack app installs requested by a member wait in the
  workspace's app-approval queue (Slack help, "Manage app approval for your workspace").
  Neither has a second, admin-typed registration form for the same token.
- **The key's mint and `scopes`.** The app mints full keys only (`{ name, scopes: [] }`,
  `scoped-key-agent.md` 5); the key request, the one product source of a scoped key, is gone
  (`drop-key-request.md`). A scoped key is minted only by the doors' tests, the wire recorder
  and the stage's old `TA_PROBE_KEY`. A key's grant is narrowed equally by `grants/set`, the
  rule every outside client's grant is narrowed by. The mint's one call of `centerOf` is the
  scoped branch's registration (`keyMint`), with `command_center_id` undefined, so a scoped mint
  naming several spaces reads `centerOf`'s words.

## Decided

1. **A key files its own agent's card**, as an MCP client does: `own_card` takes any outside
   client with a grant (`registrant` already holds an `api_key` source to the key's own
   origin). The card captures the key's origin and key id; `Registrations.take` hands the
   registration the source its client's grant was recorded from (`Grants.source`), so an
   applied key card registers an `api_key` agent, which goes with its key's revoke
   (`removeKeyAgents`). No new table, column or flag: the card table already captures
   `source_ref`, and a client's source is its grant row's, for life.
2. **One surface for each act, every outside agent alike**: the key's program files
   `agent_register` (a mailbox, or another delivery: filing again, applied, is the
   re-registration); the owner answers it on Home, where the card names the key by its name
   (its grant row's); Connected apps' "Your main agents" shows it and takes its doorbell.
   No key screen gains an agent form (Direction 3).
3. **`outside-agents` goes**, with `registering`, `registeringEntry`, `bindKeyRegistration`,
   the object's `registering` step and its session-with-origin exception
   (`MCP_REGISTERS_BY_CARD`): no outside origin ever comes with the owner's session.
   `registerAgent` is the owner applying a card, nothing else.
4. **`NOT_AN_AGENT` names the one remedy**: "file one with agent_register, for the owner to
   approve", for a key too. The stage's old scoped `TA_PROBE_KEY` can now follow it: its card
   registers in the space its scopes name (`centerOf`).
5. **`keyMint` keeps `scopes` in this change, and loses it in `untangle/key-mint-full`.** A
   scoped key has no product source, so its mint-time agent (`scoped-key-agent.md` 1) and the
   field are a second way to narrow a grant that only tests use; `grants/set` is the one.
   Taking the field out is its own expand-then-contract: the doors' version before always
   hands `scopes` and forwards a caller's, so `dropped` (taken and left out) would silently
   mint a full key for a caller who asked a narrowed one during the window; the owner must
   first refuse a non-empty one, and the doors' scoped-key tests and the wire recorder's key
   fixture move to `grants/set`. Until then the mint's `centerOf` call stays as #5831 built
   it: no screen mints a scoped key, so no mint reads its several-spaces words.
6. **Expand-then-contract.** `contract/doors.json`'s `registerAgent` keeps its `outside`
   form one deploy: the doors' version before still serves `outside-agents`, and the handed
   hold reads the door table, so the owner still parses it. The new owner refuses that call
   before its fields (`OUTSIDE_SESSION`); no screen sends it. The new doors send only the
   owner's form (`registrations/apply`). A new owner with the doors before: a key's card is
   filed and applied as below; `outside-agents` is refused. A rollback takes the doors back
   first, then the owner, which serves `outside-agents` again.

## Problems, under the fix that removes each

- Every outside agent registers by the card its own verified call files, and the owner
  answers every card on Home.
  - `own_card` refused an API key, so a full key had no mailbox and no key's agent could
    change its delivery, though `NOT_AN_AGENT` and `inbox_digest` told it to file one.
  - `Registrations.take` handed every card's source as `mcp`, a copy of which client may file.
  - The only key path, `outside-agents`, was a route no screen called, a binding of its own
    (`bindKeyRegistration`) and the one door where an outside origin came with the owner's
    session (`registering: true`, `Serves.registering`, `MCP_REGISTERS_BY_CARD`): a special
    case of the session rule, in the doors, the binder and the object.
  - `registerAgent` had two forms, its handler a branch per form.
- A client's run files no card (review). The removed source check was also what refused a
  client's run at `registerPropose` (a run's binding carries no source); without it the run
  reached the handler and threw a `TypeError` reading its source. `own_card` now refuses a
  run by its account (`account.run`), with the guard's own words.
- The refusal names the one remedy.
  - `NOT_AN_AGENT` sent a key to the owner to mint another key, because a key could file
    no card.
  - The card's notice said "An app you connected", which a key is not.
- Tests.
  - `doors/test/api-keys.test.ts` "the registering route" tested the removed route; it is
    replaced by a full key filing its card, the owner applying it on Home's route, the key
    reading its mailbox, filing again for another delivery (the same agent, one revision
    on), and the agent going with "Turn off".
  - `doors/test/binder.test.ts`' `bindKeyRegistration` cases go with it.
  - `owner/test/ledger.test.ts` "takes an API-key agent's identity from its key" registered
    through the session pairing; it now files the key's card and applies it, and the
    pairing is refused like any outside client's (`OUTSIDE_SESSION`).
  - `owner/test/registrations.test.ts` "is filed only by an MCP client" held the key's
    refusal; it is "only by an outside client", the key's card being the ledger test's above.
  - `owner/test/record/rv.record.ts` registered its scoped key's agent through the pairing;
    it files and applies the key's card (`Recorders-Differ: rv`).

## Found and left

- A key's card is named on Home by the key's name (its grant row's), under "an app"'s card
  words ("Applying gives this app its own mailbox"). The words are the registrations kind's
  for every asker; nothing shows a key differently, as nothing shows a vendor differently.
- A scoped key still has its agent made at its mint (`scoped-key-agent.md` 1), and a key
  card for it re-registers that same agent where its scopes act. Both go to one path with
  `untangle/key-mint-full` (decided 5).

## Owed

- [ ] `untangle/key-mint-full`: `keyMint` mints full keys only: the owner refuses a non-empty
  `scopes` for one deploy, the doors send none, the doors' scoped-key tests and the wire
  recorder's key narrow by `grants/set`, and the mint's scoped branch (its agent and its
  `centerOf` call) goes; then the field is dropped and taken out.
- [ ] live: once the stage serves no doors version before this one, take out
  `registerAgent`'s `outside` form (`contract/doors.json`) and its request type.
