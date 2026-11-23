# register-apply as built (2026-11-23)

`untangle/register-apply`, findings 1 and 7 of `stage-findings-2.md`: a full-grant client
(claude.ai) is refused `inbox_digest` and `mailbox_read` as no registered agent, files
`agent_register`, and waits for an apply nothing lets the owner make. The coordinator chose
option (iii): the filing answers an `approve_url`, the filing tells the owner with the same
link, and the app's view of the card applies or declines it. For a full grant
`agent_register` needs no place.

## Research (Fact 14)

- **The doors.** `registerPropose` (`agent_register`) files a card on the client's own call
  (`registrations.ts` `file`), `registerProposal` read one by id, and `registerAgent`'s owner
  form applies one by id and digest in session (`take`, then `Ledger.register`). The app had
  no route to either, and no screen shows a card.
- **The other approval flows** hold the same shape: a held card with an id and a digest, read
  by the owner, answered by id and digest. A held action call (`actions.ts`) has
  `actionRequest`, `actionApprove` (session) and `actionRefuse`, app routes
  `actions/request|approve|refuse`, and an `action_request` notice naming the card. A policy
  proposal's apply is `policyWrite` with `proposal`, and a key request's is `keyMint` with
  `proposal`. Neither has a decline: a proposal waits. None has an app screen (below).
- **Notices** are a type and a short text (`notices.ts`), shown as text on Home. The object
  already links the owner to its app by one rule's copy: `files.ts` `fileUrl` built
  `${APP_RESOURCE}#data/...` by hand.
- **The app's sections** (`doors/app/app.js` `SECTIONS`, `place`): `#<section>/<arg>`, a
  section's screen taking its argument (`#agents/<name>` is one agent). Connected apps
  (`apps`) is where the owner's main agents, registered outside agents, are listed.
- **The place a registration names** (`command_center_id`) is where a narrowed grant acts
  (`OwnerDO.#universe`: its one enabled agent's). A full grant holds everywhere, so its
  place is read by nothing but the row's key `(command_center_id, client, family)`, which
  keeps one identity's agent one row across filings. No tool answered a place.
- **What a filing was held to:** flat values and a size. `Ledger.register` checked the
  fields only at the apply, so a card asking an unknown delivery method or a blank name was
  shown and then refused (`ledger.test.ts` relied on such a card for a receipt).
- **The connector's guidance.** A registration is an optional mailbox identity most main
  agents never file (`registered-agent.md`), and the refusal and the mailbox tools'
  descriptions name `agent_register` at the moment of need (#5818). So `/workspace/index.md`
  and the connector's instructions stay as they are: leading every client to register would
  ask for mailboxes most never use.

## Decided

1. **`agent_register` answers `approve_url`**, the owner app's page of the card
   (`appPage("apps", card_id)`), and its description says to give it to the user.
2. **The filing tells the owner** (`agent_registration`), naming the same page; Home shows a
   page of this app a notice names as a link.
3. **The app's view.** Connected apps lists the pending cards ("Agents asking to join"), each
   with its display name, delivery, recipe, space, the asking app and what its grant lets it
   do, and Apply and Decline. `#apps/<card_id>` is one card's page, or says it no longer
   waits. Routes: `registrations` (`registerProposals`, the list, which replaces the one-card
   read), `registrations/apply` (`registerAgent`, in session), `registrations/decline`
   (`registerDecline`, new). Decline is a narrowing, so like `grants/revoke` it needs no
   session; it names the digest, as every answer to a card does.
4. **The place follows from the grant** (`centerOf`): a narrowed grant's agent registers in
   the space its scopes name, a full grant's in `main` (`HOME_CENTER`). `command_center_id`
   is optional: a narrowed grant naming several spaces names one of them, and a named space
   its scopes do not name is refused (its agent would hold nothing there).
5. **A filing is held to the rule its apply is** (`Ledger.registration`, which `register`
   calls), handed to the registrations doors as the object's other modules reach the ledger.
   The card holds the checked fields, so the owner sees what registers.
6. **One rule for the object's links into the app** (`contract/src/identity.ts` `appPage`):
   `fileUrl` and `approveUrl` both use it.

## Problems, under the fix that removes each

- The owner applies or declines a card in its app, at the page the client and the notice
  both name.
  - No route reached `registerProposal` or `registerAgent`'s card form, and no screen showed
    a card.
  - A filing told the owner nothing (only an eviction did).
  - A card waited until a grant change: the owner had no no.
- The place a registration names follows from the grant.
  - `command_center_id` was required, and no tool answered one (finding 7).
  - A narrowed grant could register where its scopes name nothing.
- A filing holds the registration's rule.
  - A card that could never apply was filed and shown (`ledger.test.ts`'s receipt case now
    uses a wrong digest).
- The object's app links are one rule.
  - `fileUrl` spelled `#data/` beside `APP_RESOURCE` by hand.

## Found and left

- `held_for_owner` tells an agent "the owner answers it in the TinyAssets app", but no
  screen shows a held action call (`actions/request|approve|refuse` are routes alone). The
  same view fits it. Owed below.
- `Registrations.file`'s flat-values and size checks are unreachable through the door now (it
  files the four checked fields). They stay as the store's own bound, which the schema's
  `CARD_REQUEST_MAX` check spells.
- `outside-agents` (an API key's registration from the owner's browser) still takes a
  `command_center_id`: no screen uses it since `app-key-grant.md`, which owes its redesign.
- A replaced or declined card's notice keeps its link; the page says it no longer waits.
- "Command center" as a name is `untangle/old-names`'.

## Owed

- [ ] `untangle/action-answers`: the app's view of a held action call (`actionRequest`, Apply
  as `actionApprove`, Decline as `actionRefuse`), where the `action_request` notice links,
  as this lane's view of a registration; researched first.
- [x] live: once the stage serves no doors version before this one, take out
  `registerProposal`, the one-card read kept `dropped` for the doors' version before (both
  tables' rows and its handler), as `deploy/handed.ts` holds a door the doors call no more:
  `untangle/drop-register-proposal` (`drop-register-proposal.md`).
- [ ] live: after the next deploy of the doors and the owner, on the stage, claude.ai's
  `agent_register` answers an `approve_url`, the owner applies it in the app, and the
  client's next `inbox_digest` and `mailbox_read` answer.
