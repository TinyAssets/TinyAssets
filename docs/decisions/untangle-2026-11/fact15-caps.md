# fact15-caps as built (2026-11-22)

Owed by `fact15-defaults.md`: the lives of the requests held for the owner each go into one of
Fact 15's kinds, and each ceiling's share of the owner object's 10 GB gets its reason in
`limits-audit.md`. Fact 15: the only limits are the tier's runs and storage, the owner's own
settings (off by default), vendors' limits and recorded technical ceilings, each with its
reason. Call 11: a retention is an owner setting, off keeping for ever, unless a ceiling is
recorded with its derivation.

## Research: the four lives (read at `1ce86cd4d`)

Every definition and reader:

- `owner/src/registrations.ts` `CARD_TTL_S`, 7 days: a registration card an MCP client files
  for its agent. Read by `file` (pruned by age first), `card` and `take` (an older card is
  neither shown nor applied), and shown as `expires_at` in the filing's answer
  (`agent_register`) and the owner's card read.
- `owner/src/actions.ts` `ACTION_LIMITS.cardTtlS`, 30 days, and `approvalTtlS`, a day: a call
  held for the owner and the yes that admits it once. Read by `#sweep` at every decision and
  answer, by `#pending`, and as the card's `expires_at`. `cardTtlS` rested on Claude Code's
  pinned `cleanupPeriodDays` (30), "which bounds the resume an answer starts".
- `contract/src/policy-document.ts` `POLICY_LIMITS.proposalTtlS`, 90 days: a pending policy
  or key proposal. Read by `propose` (pruned by age), `write`, `applyKeyRequest` and `held`.

What each protected, and whether anything else already does:

- **The object's 10 GB.** Each table is already bounded by a count, its guard term:
  `agent_cards` at `maxPending` (2.2 MB), `action_requests` at `held` (14.5 MB),
  `policy_proposals` at pending and applied (27.7 MB). No age bounds a byte the counts do not.
- **Staleness.** Each request is already bound to the authority it was asked under: a card to
  its client's grant (`standing` at `take`; a grant change drops it, `endClient`), a held call
  to the policy generation (any rule change sweeps it), a policy proposal to its
  `expected_generation` (`write` refuses another), a key request to its exact scopes, and
  every apply to the digest the owner was shown. An older request still standing under the
  same authority is the same request.
- **The resume.** `Runs.resume` already answers null when the held run's receipt or its
  conversation is gone (`RUNS_KEPT`, `CONVERSATIONS_KEPT`); the yes still admits one retry.
  The engine's own session is the engine record's (`cleanupPeriodDays`,
  `untangle/fact15-defaults-2`), and it held for Claude Code alone: Codex's, Gemini's and pi's
  sessions were never bound by 30 days, so the card's life was a copy of one record's pin.
- **Liveness of the bounded set.** This one is real: with no age and refusal at the bound,
  abandoned requests fill the set and every new one is refused. Policy proposals have no
  decline door at all, so a full set would be stuck for good; the owner finds a card only by
  its notice. The age was the only exit.

What held-approval flows elsewhere keep: a held approval waits as long as its conversation.
OpenAI's Agents SDK returns pending approvals as interruptions in a `RunState` the caller
serializes and resumes later, with no expiry (openai.github.io/openai-agents-python,
human_in_the_loop). Claude Code's and ChatGPT's permission prompts wait in the conversation.
Where a clock exists it is a vendor's own runtime bound: GitHub fails a job whose environment
approval is not given within 30 days (docs.github.com, reviewing deployments).

## Decided

1. **No held request has a life of its own: all four go.** None is a tier, an owner's
   setting, a vendor's limit or a recorded ceiling. An owner setting was not taken: a setting
   that shortens how long a request waits, which nobody asked for, would be a feature with no
   reader (as `fact15-defaults.md` decision 2 found for the settled wakes), and off would mean
   what removal means. `CARD_TTL_S`, `cardTtlS`, `approvalTtlS` and `proposalTtlS` are deleted
   with every reader, and `expires_at` leaves the card answers (`agent_register`'s and the
   owner's reads), since nothing expires.
2. **What replaces the age's one real protection: the count ceiling's oldest-out.** A
   request at its bound lets the oldest go to make its room, the asker's own when it is at its
   share, else the oldest of all (by filing time, then insertion). Nothing is refused for room,
   and the per-client shares still keep one client's asks from pushing out the rest. This is
   the settled wakes' rule (`maxSettledWakes`), and strictly less lossy than an age, which let
   every request go at its time. It also bounds the grant rows the cards hold
   (`outside_clients.held`) to `maxPending`, which the age used to free. Refusal texts gone:
   "this client's pending registrations are at their limit", "too many pending agent
   registrations", "this client's calls held for the owner are at their limit", "too many calls
   held for the owner", "too many pending policy proposals", "this client's pending policy
   proposals are at their limit". A yes waits until its one use or a policy change.
   - **No migration.** No column or index changes; the oldest is found on the existing
     `agent_cards_by_age` and `proposals_by_age`, and `action_requests` is at most 200 rows.
     A stored action card's JSON keeps its old `expires_at`, which nothing reads; its digest is
     the stored one.
3. **The shares' reasons.** `limits-audit.md` gains "Why each share is what it is": the room
   under the half is given in four kinds, the owner's content held hot (59.97%), the recovery
   copy's room (7.88%), one a thing another bound counts, and the newest kept or what waits at
   once. Each ceiling's row names its kind and its why in a `Share:` clause (44 rows), with its
   term's figure from the guard's print.
4. **The spec** (`owner-object`): the held call, the registration card and the proposal have
   no life, the bound's oldest-out, and a scenario for a card answered a year on.

## Found and left

- `USAGE_LIMITS.daysKept` 40 and `monthsKept` 3 have no derivation: the budgets read the
  current day and month, a call settles into its reservation's periods at most a day later,
  and nothing reads an older period. Keeping fewer changes `SPEND_KEPT` and its schema trigger
  (a migration), so it is its own change.
- `tasks.md` item 11 also names `openPerRun`, code mode's limits taken from pi's defaults and
  the 300 s idle cut (`gateway/src/forward.ts` `IDLE_CUT_MS`, made data per engine) under this
  branch. They are not in this PR's brief and need their own research (Fact 14).
- `owner/src/ledger.ts` `LedgerDoorNeeds.registrations` restates `take`'s signature by hand
  rather than `Pick<Registrations, "take">`, as its other needs do; changed only to match.

## Owed

- [ ] Keep the spend counters' current and previous periods alone, `SPEND_KEPT` derived from
  them, with its migration (`untangle/spend-periods`).
- [ ] `openPerRun`, code mode's limits taken from pi's defaults and the 300 s idle cut, each
  in one of Fact 15's kinds, researched first (`untangle/fact15-caps-2`).
