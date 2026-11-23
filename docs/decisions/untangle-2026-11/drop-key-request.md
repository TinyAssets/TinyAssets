# drop-key-request as built (2026-11-23)

`untangle/drop-key-request`, owed by `policy-answers.md` (#5832) and `scoped-key-agent.md`
(#5831): the "key request" is no flow. It is a policy proposal whose change is
`{ type: "api_key_request", scopes }`, applied only through `keyMint`'s optional `proposal`
(`Policy.applyKeyRequest`). It is taken out.

## Research (Fact 14)

- **Where the type is accepted.** Only `Policy.applyKeyRequest` (`owner/src/policy.ts`) reads
  `api_key_request`: in `keyMint`'s unit, it checked that the named card was pending, of that
  type, its scopes the grant's, its digest the one shown, and marked it applied. `policyPropose`
  takes any change object (`object(change)`, bounded by `maxChange`), so the type was never a
  case there; `policyApply` refuses every change that is no whole policy, a key request
  included ("this proposal is not a whole set of rules to apply ... decline it").
- **Who sends `proposal`.** Nothing in the product. The app's key form posts
  `call("keys", { name, scopes: [] })` (`doors/app/app.js`, the same at 5d7073f70); the canary
  mints no key with one; no tool, skill or agent prompt names the type (`policy-answers.md`
  research). The doors' `mintKey` (`doors/src/api-keys.ts`) forwarded a `proposal` only when the
  browser's body carried one.
- **What the stage serves.** Run 45225234784 deployed 1ae659350 (`origin/main`, #5833) on all
  six Workers, 2026-11-23 (its apply then stopped at the platform canary, after every Worker was
  at 100%), so its rollback targets are 5d7073f70's versions (run 45217169623). At 1ae659350,
  at 5d7073f70, at 1685c187d and at b8424315e, `contract/doors.json`
  lists `keyMint`'s `proposal` as optional and `mintKey` forwards it when sent. So the doors at
  the serving version do not "never send" it: they send it exactly when a caller does, which no
  screen of theirs does. The field must stay taken one deploy.
- **The hold.** `handedHold` of this tree against `sideAt` of 1ae659350, 5d7073f70 and
  1685c187d is `[]` (as before this change; b8424315e's one problem, `actionRequest`, is #5832's
  and refused by `rollbackAsk`). With `proposal` taken out instead of dropped, against 1ae659350
  and 5d7073f70 it is
  "keyMint no longer takes proposal, which the doors' version before hands: drop it first".
- **Existing pending `api_key_request` cards.** None can have been filed by the app or any
  told agent; any that exist are ordinary policy proposal cards (`policy-answers.md` research):
  listed on Home's "Waiting for you" (`policyProposals`), refused by Apply in words that say
  to decline it, removed by Decline (`policyDecline`). `owner/test/policy.test.ts` ("applies
  only as a whole policy ...") holds an `api_key_request` card refused and still pending.
- **The `command_center_id` text** (`scoped-key-agent.md`'s owed line). The only text naming it
  for several spaces is `centerOf`'s refusal (`owner/src/authority.ts`), whose remedy is right
  for its caller that takes the field (`registerPropose`, its schema's description says the
  same). A key request card could have reached the mint with several spaces; with key requests
  gone, none does.

## Decided

1. **`keyMint`'s `proposal` is `dropped`** (`contract/doors.json`: taken and left out), for the
   doors' version before. `KeyMintRequest`, `KeysDoorNeeds.policy` and the mint's apply go, and
   so does `DoorTypes`' `keyMint` `proposal` (`contract/src/owner.ts`), so `tsc` refuses a doors call
   that sends one.
2. **The doors stop forwarding it** (`mintKey`): this tree's doors never send it, so the next
   deploy's doors are the version before that hands none, and the dropped field can go then.
3. **`Policy.applyKeyRequest` goes**, with its two proofs in `owner/test/proofs.test.ts` and the
   doors-live test that applied a card through a mint.
4. **Pending `api_key_request` cards stay as they are**: no migration. They are policy proposal
   cards the owner declines on Home; nothing applies them.
5. **Deploy pairings.** The owner deploys before the doors. New owner with doors 1ae659350: a
   `proposal` any caller sends is taken and left out, the key minted as asked, the card left
   pending to decline. A rollback takes the doors back first, then the owner to 1ae659350,
   which applies one as before. No pairing refuses a mint.

## Problems, under the fix that removes each

- The key request half-flow goes; the mint takes what the owner asks and nothing else.
  - `keyMint` carried an optional card whose shape only `applyKeyRequest` knew, a parameter for
    one flow nobody runs (caller vocabulary).
  - `KeysDoorNeeds` reached `policy` for that one call: the keys depended on the policy store.
  - `applyKeyRequest` kept its own copy of the card check (`policy-answers.md` "Found and left").
  - The doors forwarded a field only that flow used.
- Tests.
  - `doors-live.test.ts` "applies an agent's held key request with its grant, once" tested the
    removed flow; it goes, with the `ownerBinding` helper only it used.
  - `proofs.test.ts`' two `policy.applyKeyRequest` lines go with the widening.

## Found and left

- A scoped key has no source in the product now: the app mints full keys (`scoped-key-agent.md`
  5), and the key request was the one source it named. `keyMint` still takes `scopes` (the
  doors' tests and a caller of `/app/api/keys` with its own scopes), and a direct mint naming
  several spaces reads `centerOf`'s words, which name a field the mint does not take. Whether
  the mint keeps scopes is owed with a key's agent (below).
- `policy_propose` still files a `{ type: "api_key_request" }` change, as any change object:
  a card the owner can only decline, as with any change that is no whole policy. What a
  proposal should be is `untangle/policy-proposal-shape` (`policy-answers.md`), which refuses
  any change that is no delta when it is filed.
- The Python stack's `api_key_request_counts` (its rate limiter) and the import's rules that
  drop it are another thing of the same name, and stay.

## Owed

- [ ] live: once the stage serves no doors version before this one, take out `keyMint`'s
  dropped `proposal` (`contract/doors.json`).
- [ ] `untangle/key-agent-change`: with the key request gone, decide whether `keyMint` keeps
  `scopes` at all (no screen sends any), and so whether `centerOf`'s "name one ... as
  `command_center_id`" can reach a mint.
