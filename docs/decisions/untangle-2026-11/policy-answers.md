# policy-answers as built (2026-11-23)

`untangle/policy-answers`, owed by `action-answers.md` (#5826): a policy proposal and a key
request wait on the owner with no list, no decline and no screen. The target: each a kind of
Home's "Waiting for you" on #5826's mechanism (`KINDS`, `#home/<card_id>`, an owner-only list
door, a yes in session and a no with none), with no view or case of its own.

## Research (Fact 14)

- **Where a proposal is stored and answered.** One row of `policy_proposals` per held card
  (`owner/src/policy.ts`): its `request_id` (the proposer's own id, 1 to 128 characters, the
  table's key), the change's digest, the change as filed, when, and the proposer (`owner` or
  the outside client). `policyPropose` (caller `account`, capability `propose_policy`, the
  MCP tool `policy_propose`) files it; `policyProposal` (caller `owner`) reads one card by id;
  `policyWrite` with `proposal` applied one, in session, only when the change is
  `{ type: "policy_proposal", expected_generation, policy }` naming exactly the document
  written at its generation. No decline existed, nothing in the app read or answered a card,
  no notice was filed, and the tool's answer named no card and no page.
- **A proposal naming no `request_id` was held nowhere**: receipted and dropped, though the
  tool tells the agent the owner approves it. Nothing reached the owner (Fact 8).
- **What a "key request" is.** Not a store or a door of its own: a policy proposal whose
  change is `{ type: "api_key_request", scopes }`, filed through the same `policy_propose`,
  applied only by `keyMint`'s optional `proposal` (`Policy.applyKeyRequest`, ported from the
  Python's `_apply_key_request`, origin/main 1fd430857). Nothing names the type to an agent
  (the tool's schema says only "the proposed change"), the app's key form never sends
  `proposal`, the Python stack on main has no such flow (`api_key_request_counts` is its rate
  limiter), and an applied request hands the asking agent nothing: the key's secret is shown
  once, to the owner. It is a ported half-flow, not a current one, so it gets no kind here.
  Its cards, if any are filed, are policy proposal cards and are listed and declined as such.
- **What an agent can propose that applies.** No agent can read the owner's policy
  (`policyRead` is caller `owner`; no tool reads it), but every `policy_propose` answer names
  the current `generation` and the default document is public, so an agent can file a whole
  document at the current generation, the only change an apply takes, without knowing what
  it replaces. An apply replaces the document whole: whatever the proposal leaves out (the
  owner's `ask` and `never` rules, its allowed hosts, its tools) goes (review, 2026-11-23).
- **#5826's mechanism** (`action-answers.md`): each store keeps its own list door; the app's
  `KINDS` names each kind's list route, what its card shows, and its yes and no, each posting
  the card's id and the digest shown; `waitingPage` is every card's page.
- **Who may decline, and with what proof** (as #5826's review of `actionRefuse`): caller
  `owner` is the owner on its own account, never an outside client (an API key included) nor
  a run (`door-steps.ts`). A decline deletes one pending card whose digest is the one listed
  and widens nothing; it answers the card's id alone, so it leaks no content; the worst a
  stolen first-party token does with it is dismiss a request its agent can file again, as
  with `registerDecline` and `actionRefuse`.
- **The deploy order** (`deploy/handed.ts`): the owner deploys before the doors, so a door the
  doors' version before names stays in the owner's table (`dropped`) one deploy, and a field
  it names stays taken (`dropped`: taken and left out).

## Decided

1. **A policy proposal is one more kind of `KINDS`**: list `policy/proposals`
   (`policyProposals`, caller `owner`: the pending cards, oldest first, each its id, digest,
   filing time, client, null for the owner's own agents, and change), the change shown as its
   JSON beside the owner's settings now, which an apply replaces whole, "Apply"
   (`policy/apply`) and "Decline" (`policy/decline`).
2. **The yes is its own door, in session**: `policyApply` (caller `session`, rule
   `proposal_apply`, gate `units` as `policyWrite`) takes the card's id and digest and writes
   the card's own document as the owner's write, the card marked applied in the same
   transaction. A change that is no whole policy, or one made before a later change, is
   refused in words that say so, and waits to be declined. `policyWrite` no longer applies a
   card: its `proposal` is `dropped` (taken and left out) for the doors' version before, so
   one apply path remains and the write's own card check went.
3. **The no needs no session**: `policyDecline` (caller `owner`, class `reduce`) deletes the
   pending card shown; its proposer may file again.
4. **Every proposal is a card**: one naming no `request_id` is held under a minted
   `pol_<32 hex>`. The answer names the card and its page (`approve_url`, as
   `agent_register`'s), the tool's description says to hand it to the user, and a new card
   files the `policy_proposal` notice naming the page, which Home links.
5. **`policyProposal`, the one-card read, is `dropped`** one deploy, as #5826 kept
   `actionRequest`: the doors' version before lists it.
6. **No key request kind**, for the reasons above; its removal is owed (below).
7. **`actionRequest` goes now.** The stage serves 5d7073f70 on all six Workers (deploy run
   45217169623; 45215267847 put the other five there and its rollback 45216810978 left the
   doors on 1685c187d, #5826 included, the doors' version before that run's). The kit's plan holds the tree against the versions the
   stage serves (`deploy.ts`, `handedHold` on each Worker's version before): against
   5d7073f70, 1685c187d and a1f0e0b19 it is `[]`, against b8424315e it is "the door
   actionRequest is gone, which the doors' version before calls". So: this PR's deploy pairs
   the new owner with doors 5d7073f70, which call `actionRequests`; a rollback of this PR's
   run takes the doors back to 5d7073f70 and the owner to a version that still takes it; run 45210631747's own
   rollback (doors to b8424315e) is refused by `rollbackAsk` once a deploy since serves
   (drift); and were it rolled back before this deploys, the plan refuses this deploy, named.
   No pairing a deploy or a rollback can reach calls it. Both rows and its handler go.

## Problems, under the fix that removes each

- One more kind on #5826's mechanism.
  - A held proposal had no list, no screen and no decline; the app never answered one.
- One apply path, its own door.
  - `policyWrite` carried an optional card whose shape only an apply knew, and rechecked the
    card against the document the caller re-sent.
- Every proposal a card with a page.
  - A proposal naming no `request_id` was receipted and held nowhere.
  - The tool's answer and no notice named a card or a page.
- One decline rule.
  - No decline existed; a proposal could only wait to be evicted at the pending bound.

## Found and left

- A card's id is its proposer's `request_id`, one key for every proposer: a client refiling an
  id another holds learns only that it is held ("a proposal is already held for this card"),
  never its content, and a page id another kind already uses shows that kind's older card
  first. Keying cards by the proposer would be a migration of the table for no harm seen.
- A proposal made against a generation since passed stays pending until declined or evicted,
  where a held call is swept: it is the owner's to read either way, and the card says so.
- The card shows the change as its JSON, and beside it the owner's settings as they stand,
  which an apply replaces whole (`namesFor` reads them with the askers' names; review,
  2026-11-23): what Apply takes away is on the card, not only what it writes. Plain words per
  change, or a diff, is a feature editor the platform does not build (Direction 3).
- `Policy.applyKeyRequest` keeps its own copy of the card check, owed with its removal.

## Owed

- [x] `untangle/drop-key-request`: take out the key request half-flow (`keyMint`'s
  `proposal`, `dropped` first, then `Policy.applyKeyRequest` and its proofs), after
  `untangle/scoped-key-agent` lands, since both change the mint. Done (`untangle/drop-key-request`,
  `drop-key-request.md`); the dropped field's removal is a live line there.
- [x] `untangle/policy-proposal-shape`: research what an agent's proposal should be so the
  owner's Apply can take it without replacing the document whole (an agent reads no policy,
  so a whole document it files replaces settings it never saw), as the major agents' rule
  suggestions are (Claude Code's "don't ask again"). Done: a proposal is a delta Apply
  merges onto the settings as they stand (`policy-proposal-shape.md`).
- [x] live: once the stage serves no doors version before this one, take out `policyProposal`
  (both tables' rows and its handler) and `policyWrite`'s dropped `proposal`. Done in
  `untangle/policy-proposal-shape`: the stage serves 8c80b02c8 (deploy run 45281806227).
- [ ] live: after the next deploy of the owner and the doors, on the stage: claude.ai's
  `policy_propose` answers an `approve_url`, the card is on Home, and Decline there removes it.
