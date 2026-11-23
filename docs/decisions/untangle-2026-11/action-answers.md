# action-answers as built (2026-11-23)

`untangle/action-answers`, owed by `register-apply.md` (#5821): an agent's call held for the
owner (`held_for_owner`) told the agent "the owner answers it in the TinyAssets app", but no
screen showed a held call, so the owner could not answer it. #5821 had given an app's agent
registration its view ("Agents asking to join", under Connected apps). The target: one
mechanism for both kinds of the owner's answer, not a second view beside it.

## Research (Fact 14)

- **Where a held call is stored and answered.** One row of `action_requests` per held call
  (`owner/src/actions.ts`): its card (what the owner is shown), the card's digest (what an
  answer names), the call's digest (what a yes admits once), the client whose share it takes,
  the policy generation, and for a run's call the run and its family (what a yes resumes).
  Three owner doors: `actionRequest` (caller `owner`, one card by id), `actionApprove` and
  `actionRefuse` (both caller `session`, rule `action_answer`, by id and digest). The app
  routes `actions/request|approve|refuse` reached them; nothing in the app called them.
- **What the agent and the owner are told.** The refusal's message names the card's id and
  "the TinyAssets app", no page. Over MCP a refusal reaches the client as its message alone
  (`doors/src/refused.ts` `asTool`), so the facts' `card_id` reaches no main agent's user.
  The `action_request` notice names the agent, the act, the door and the card's id: text on
  Home, with no link.
- **What #5821 built.** A registration is a card of its own store (`agent_cards`,
  `registrations.ts`) with an id and a digest. `registerProposals` lists the pending cards,
  `registerAgent` applies one in session, `registerDecline` declines one with no session ("a
  narrowing, as `grants/revoke`"). `agent_register` answers `approve_url`, the owner app's
  page of the card (`appPage("apps", card_id)`), and the filing's `agent_registration`
  notice names it; Home makes a page a notice names a link (`app.js` `linked`). Connected apps
  lists the cards (`asking`), and `#apps/<card_id>` is one card's page.
- **The two kinds side by side.** Both are a held card answered yes or no by id and the
  digest shown; a yes is a widening, in session, and a no deletes the card. They differ in
  what binds a card (a registration to its client's grant, a held call to the policy
  generation), in what a yes does (registers an agent; admits one call and resumes a run),
  and in who asks: a registration is always a connected app's, a held call is as often one of
  the owner's own agents' runs, which is no connected app.
- **The other held cards.** A policy proposal (`policyPropose`, applied by `policyWrite` with
  `proposal`, read by `policyProposal`) and a key request (`keyMint` with `proposal`) wait on
  the owner too. Neither has a list door, a decline or a screen (as #5821 found).
- **How the major agents present pending approvals.**
  - Claude, ChatGPT and Codex show an approval as a card in the conversation it came from,
    naming the app and the proposed action, with allow and deny (ChatGPT's app permissions
    help page); Codex's mobile supervision approves actions from the phone, and its users
    ask for a push when one waits (OpenAI community, 2026).
  - OpenAI's Agents SDK returns every pending approval of a run as one list,
    `interruptions`, whatever the tool, each answered `approve` or `reject` on the run's
    state, which resumes it (Agents SDK "Human in the loop").
  - LangChain's Agent Inbox is one inbox of every agent's interrupts, each declaring which
    answers it takes (accept, respond, edit, ignore).
  - MCP's URL-mode elicitation (2025-11-25 spec, SEP-1036) sends the user to a page of the
    server's for a step that must not pass through the client, the client showing the host
    and asking consent: what `approve_url` already is.
  - So: one list of everything waiting, each card naming who asks and what, answered in
    place, where the owner looks first; and a page the asking agent can hand its user.

## Decided

1. **One view of everything waiting on the owner: Home's "Waiting for you"** (`app.js`
   `waitingCards`), first on the screen the app opens on, oldest first, every kind together.
   A held call is often the owner's own agent's, no connected app's, so the view leaves
   Connected apps; #5821's "Agents asking to join" and its `#apps/<card_id>` page go.
2. **One page per card: `#home/<card_id>`** (`contract/src/identity.ts` `waitingPage`), for
   every kind: a registration's `approve_url` and notice, a held call's refusal and notice.
   The page says when its card waits no more.
3. **One card form in the app** (`asking`, by `KINDS`): each kind names its list route, what
   it shows, its yes and its no (their routes and words). Both answers post the card's id and
   the digest shown. A registration: Apply and Decline, as #5821's. A held call: "Allow once"
   (`actions/approve`) and "Deny" (`actions/refuse`), showing the act's words, the door, who
   asks (the agent, the app that started it), its conversation and the request as asked, and
   saying when a yes resumes a run.
4. **Held calls are listed as registrations are**: `actionRequests` (no request) answers the
   pending cards of the policy's generation, oldest first, each with its digest, the app's
   `actions` route. It replaces the one-card `actionRequest`, kept one deploy as a `dropped`
   row for the doors' version before (`deploy/handed.ts`), as `registerProposal` is.
5. **One rule for a no: it needs no session.** A no deletes a card and widens nothing, so
   `actionRefuse` is caller `owner`, as `registerDecline` and `grants/revoke` are. A yes stays
   in session (`actionApprove`, rule `action_answer`).
6. **The asking agent can hand its user the page.** `held_for_owner`'s message names the
   card's page ("your owner answers it at <page>: give them that link"), since a client sees
   a refusal's message alone; the `action_request` notice names it as `agent_registration`'s
   does, so Home links it, and so does a run's notice of a command of its definition held
   (`definition_command_dropped`).
7. **Each store keeps its own list door** rather than one door answering both: each kind's
   card, binding and answers are its store's, so a joint door would change whenever either
   does. The kinds meet in the view, the page and the card form.

## Problems, under the fix that removes each

- One view and one page of everything waiting on the owner.
  - No screen showed a held call; its app routes had no caller.
  - A registration's view sat under Connected apps, where a held call of the owner's own
    agent does not belong: a second, parallel view would have been needed.
- One card form, its kinds as data.
  - `asking` drew a registration alone; a held call needed the same card, Apply and Decline.
- Held calls listed as registrations are.
  - `actionRequest` read one card by id: the view would have had to know every id first.
- One rule for a no.
  - `actionRefuse` asked a fresh session where `registerDecline` did not, for the same no.
- The asking agent can hand its user the page.
  - The refusal and the notice named a card's id, no page; a main agent's user had no way in.

## Found and left

- A notice filed before this deploy names `#apps/<card_id>`, which now opens Connected apps:
  only the stage holds such notices (filed since #5821 today), and a card waiting is on Home.
- An approved call that has not yet been made is no pending card, so it is not listed: the
  agent's next try goes through.
- A held call's `request` is shown as its canonical JSON, cut at 8,000 code points as the card
  holds it (`ACTION_LIMITS.shownChars`), not in plain words: plain words per door is a
  feature editor the platform does not build (Direction 3), and the act's words lead the card.
- The resumed run's id is said, not linked: Past runs pages by time, with no page per run.
- The app loads the cards on every Home open, and the grants and apps list only when one
  waits; a push when one waits is the owner's notifications', which do not exist yet.

## Owed

- [ ] `untangle/policy-answers`: a policy proposal and a key request wait on the owner with
  no list, no decline and no screen; each joins `KINDS` with its list door and a no, as a held
  call does here, researched first.
- [ ] live: once the stage serves no doors version before this one, take out `actionRequest`,
  the one-card read kept `dropped` (both tables' rows and its handler), with
  `register-apply.md`'s `registerProposal`.
- [ ] live: after the next deploy of the doors and the owner, on the stage: with an `ask`
  rule on `workspace.files`, claude.ai's `file_mkdir` is held with the card's page in its
  message, the owner allows it once on Home, and claude.ai's same call then goes through.
