# auto-usable as built (2026-11-23): `auto` ranks only models a run can call turn by turn

`untangle/auto-usable`. The stage finding of 2026-11-23: the stage owner connected an
OpenRouter key (the founder: a free key, the free daily allowance unlocked); the seeded `main`
agent (engine `auto`) ran on pi with `anthropic/claude-fable-5.1:batch`, and the run settled
`unknown` (its cause is diagnosed elsewhere). `auto` had placed a batch variant of a paid model
first. Direction 4: "default to their strongest connected source"; Direction 2: engine
differences are data.

## Research (Fact 14), from the code and the catalog

### What the catalog records about a model

- A connection's models are pi-ai's catalog for its provider, filtered to the record's API and
  to pi-ai's `chat` type (`gateway/src/catalog.ts` `modelInfos`), listed by the gateway's setup
  check (`gateway/src/forward.ts` `verify`), whose answer's body is never read. The record kept
  (`contract/src/model-record.ts` `ModelRecord`) is the id, name, input types, limits,
  reasoning, thinking levels, prompt-cache lifetimes and prices. Nothing in it says whether a
  model answers an interactive call; pi-ai types every OpenRouter entry `chat`.
- pi-ai 1.4.0's OpenRouter catalog (read through its API: `getModels()`, 386 models on
  `openai-completions`) carries the variant in the id: 75 `:batch`, 17 `:free`, the rest none.
  No other built-in provider has a suffix. A `:batch` entry's name says "(batch)" and its
  prices are half its model's (`anthropic/claude-fable-5.1:batch` $5/$25 per million).
  `:free` entries price at 0; seven unsuffixed routers (`openrouter/auto`, `openrouter/free`,
  ...) price at 0 too.
- Claude Fable is listed only as `anthropic/claude-fable-5:batch`,
  `anthropic/claude-fable-5.1:batch` and `~anthropic/claude-fable-latest`. Within the top
  entry `claude-fable-*`, "newer ids first" put `anthropic/claude-fable-5.1:batch` first:
  the stage's pick.
- What OpenRouter's `:batch` means is not recorded anywhere in the repo (no docs URL; the
  variant guide is not in `docs/decisions`, `openspec` or the sources). A batch tier at half
  price is, across Anthropic's, OpenAI's and Google's APIs, an asynchronous queue, never a turn
  of a conversation. Unverified for OpenRouter: owed below.

### What a connection knows about its key's limits

- Nothing. OpenRouter's key endpoint (`GET /api/v1/key`: limit, usage, remaining, free tier)
  is not among the record's endpoints; the setup check reads `/api/v1/models/user` for its
  status alone. No connection row, view or run reads a credit, a limit or a free tier.

### What the spec says

- The clean-sheet spec (`owner-object/spec.md`, "`auto` is the owner's strongest connected
  model", "A run is admitted ...") chooses the source once, at admission. There is no retry
  onto the next candidate: a model call's refusal (a vendor's answer to a key with no credit)
  reaches the engine as the egress door forwards it, and the run settles `failed`. The old stack's `provider-routing` spec had fallback and "free onboarding SHALL NOT
  authorize paid fallback"; it is the Python's, retiring, and binds nothing here.
- Direction 4's "strongest connected source" ranks what the owner can run. A model the key
  cannot call is no source of the owner's. A batch variant is settled by data (below). A model
  the key cannot pay for is not: nothing reads what a key can pay for, and the spec does not
  say whether `auto` should learn it, or fall back on the refusal. That is a design choice;
  options below.

## Problems found (untangle step 2), with the fix above each

- **A source record marks the models its catalog lists that answer no interactive call
  (`not_interactive`, model patterns in the ranking's grammar), and `auto` never picks one on
  its own (`sources/src/ranking.ts` `interactive`, read by `owner/src/runs.ts` `choose`'s
  `auto` loop, beside the unranked notice's `file.model === null` rule).** The OpenRouter
  record marks `*:batch`. It is the source's fact, not the ranking's: an owner's own ranking
  replaces the platform's whole and would re-admit them. It is read on the owner side, at
  admission, so the stage's existing connection is fixed with no reconnect.
  - `choose` took the strongest listed model, so a batch variant led.
  - The ranking's `matcher` built a regex, and `place` matched the id and its tail by hand:
    now one matcher does both, for the ranking and the record alike.
- **Decided in review: `ranked` still ranks every listed model.** The first head filtered in
  `ranked`, which `choose` reads twice: an `auto` agent naming a `:batch` model was refused
  `no_source`, against the spec's "SHALL pick only among the sources that offer it", and a
  named engine's `keyFor` refused a named connection listing only batch models. Only `auto`'s
  own pick skips; a model the agent names, and every named-engine candidate, are its own
  choice.
- Review: the record checker's model pattern is `contract/src/model-record.ts` `MODEL_ID`
  (it was a copy), and the tests run on real records with no pin of which records carry marks.
- The sources guard (`sources/guards/ranking.test.ts`) holds every `not_interactive` pattern to
  a model its provider's catalog lists (a stale pattern fails), and every key source to a
  ranked model a run can call (an over-broad one fails). The record checker
  (`sources/src/check.ts`) holds its shape.
- Left, with reasons:
  - The gateway still lists batch variants among a connection's models (the app's list, and a
    named model). Dropping them there needs the sealed upstream to carry the patterns (a
    contract change), and leaves every existing connection as it is until a reconnect. A
    model the agent names, or a connection it names on its engine, is its own choice, and
    runs.
  - The seven zero-priced routers and the `:free` variants are interactive; nothing changes
    for them.

## A key that can only call free models: the options (a design choice, not built)

1. **Read the key's limits as data.** The source record names a limits endpoint and where its
   answer says the free tier and the credit left; the gateway reads it at setup and on a
   lane; the connection keeps it; `auto` ranks only zero-priced models of a key with no credit.
   Matches Direction 4 best. Needs a record field, a sealed-upstream or binder change, a body
   read in the gateway (today it never reads one), a connection column and a refresh rule
   (credit changes between setup and run). Well past 200 lines.
2. **Fall back on the refusal.** On a payment refusal at the first model call, the run is
   re-admitted on the next candidate. A spec change (the source is chosen once, at admission),
   and a second admission path for the effects a first turn may have had.
3. **Say it loudly and let the owner choose.** A payment refusal on an `auto` run leaves the
   owner a notice naming the model and the fix (add credit, name a `:free` model in
   `agent.json`, or rank one first in `/workspace/model-ranking.json`). Small, works today,
   but `auto` keeps choosing a model the key cannot pay for.

## Owed

- [ ] live: at the stage's next run, OpenRouter's `/api/v1/chat/completions` on an `:batch` id answers no interactive call (or does; then the record's `not_interactive` goes), and what its variant guide says `:batch` is.
- [ ] untangle/auto-credit: `auto` on a key that can only call free models, by one of the options above, researched first; the stage key's limits read once (`GET /api/v1/key`) say which case the stage is in.
