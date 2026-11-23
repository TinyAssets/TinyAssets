# auto-credit as built (2026-11-23): `auto` ranks only what a connection's key can pay for

`untangle/auto-credit`, owed by `auto-usable.md`. The stage finding: the stage owner's OpenRouter
key has only the free daily allowance, and `auto` (after #5815) picks
`~anthropic/claude-fable-latest`, a priced model the key cannot pay for. The spec chooses a
run's source once, at admission, with no retry, so the refusal fails the run. The
coordinator's call: option 1 of `auto-usable.md`, a key's limits read as source-record data.
Direction 4's "strongest connected source" is read as the strongest the owner's keys can run;
Direction 2 puts the vendor's facts in its record, never in code.

## Research (Fact 14), from the repo

- **What the repo records of OpenRouter's limits.** The old stack's `daily_quota.py` cites
  `https://openrouter.ai/docs/api_reference/limits` (checked 2026-09-30, for its limit
  headers). Its OpenRouter record (`tinyassets/providers/source_records/`) bills it `free`, and
  maps a `402` to `provider_credit_exhausted`: the status a model call is refused with when
  the key cannot pay. The key endpoint (`GET /api/v1/key`) and its fields (`data.limit`,
  `data.limit_remaining`, `data.usage`, `data.is_free_tier`) are on that docs page, but no
  answer of it is recorded in the repo. The field names are owed a live read (below).
- **Where a secret may go.** The gateway sends a sealed secret only where the sealed plaintext's
  endpoints allow (`gateway/src/credential.ts`, `contract/src/egress-scope.ts` `allows`), and
  a sandbox may already `GET` any sealed `GET` endpoint for free (`forward.ts` `forwardRead`).
  So a read whose URL the caller hands over gives no new reach, as long as the gateway holds the
  URL to the sealed endpoints.
- **The stage connection.** It was sealed with the record's endpoints of the time:
  `chat/completions`, `models/user`, `benchmarks`. `/api/v1/key` is not among them, and no
  reseal changes what is sealed (`keyring.ts` reseals the value as it is). Its key's limits
  cannot be read without a reconnect, or a gateway reseal that widens sealed endpoints from the
  record. That reseal is a change to the sealing trust model, which this lane does not make.
  What it can learn without either is the provider's own refusal: every settle carries the
  status (`egress.ts` `settle`).
- **Which models a no-credit key is served.** pi-ai 1.4.0's OpenRouter catalog, read through
  its API, prices 24 models at zero. 17 are `:free` variants. The rest are routers:
  `auto`, `openrouter/auto`, `openrouter/auto-beta`, `openrouter/free`, `openrouter/fusion`,
  `openrouter/pareto-code`, `typesafe/jev-router`. A router's catalog price is zero but it
  bills the model it routes to, so "zero-priced" alone would pick `auto` (the first id) and be
  refused again. The models a vendor serves such a key are the vendor's fact: record data.
- **Reads and refreshes.** The owner's object reaches the gateway only through `GATEWAY`
  (refresh, revoke, reseal, doorbells). A read inside an admission unit is impossible (no
  outside call in a unit). The `connections` lane already makes the object's outside calls for
  connections (`revokeAtProvider`).

## Problems found (untangle step 2), with the fix above each

- **A source record says how its key reports what it can pay for (`credit`: the read's URL,
  a `GET` among its endpoints; the tests of its answer that mean no credit; the refusal
  statuses; the models served to such a key; the vendor's page). The connection keeps the
  verdict (`connection_credit`, migration 29), and `auto` reads it through the one predicate
  that says what it may pick on its own (`sources/src/ranking.ts` `pickable`).**
  - `choose` ranked every listed model of a key whatever the key could pay for.
  - Nothing read a key's limits. The setup check reads `models/user` for its status alone.
  - `interactive` was the source's half of the rule: it is now `pickable`, with the key's
    credit beside it, so the two exclusions are one rule in one place.
- **The gateway's `credit` reads it, held to the sealed endpoints and to the read's tests,
  checked by the one rule the record checker uses (`contract/src/egress-scope.ts` `creditRead`,
  `paysBy`).** An answer missing a test's path is a failed read, never a key that pays (Fact 8).
  - The rule sits beside `allows`, not in `gateway.ts`: the sandbox runner's bundle reads the
    source records through `sources/src/check.ts`, and `gateway.ts` imports `wake-records.ts`,
    which carries the doors' identity (`identity.json`) the runner must never ship (sandbox
    `guards/template.test.ts`, found in review). The owner's call's keys (`credit_read`) are
    checked by the gateway (`forward.ts`).
- **The `connections` lane reads it once a connection is active, and again every hour while the
  key pays for nothing.** An active connection with no credit row is due at once. That covers a
  new connection and every connection made before this, so none needs a reconnect, and no
  migration has to name the exported `connections` table.
- **A settle with one of the record's refusal statuses marks the key as paying for nothing
  (`Connections.refused`, from `egressSettle`).** This is how the stage connection learns it,
  at its next refused call, with no reconnect.
  - On such a blob the mark lapses at its hourly recheck (the read answers `endpoint_refused`,
    so `pays` goes back to unknown): credit bought is seen at the next call, and a key still
    dry is marked again by its next refusal (found in review: it stayed free-only for good).
  - `egressSettle` now writes a lane's rows, so its door is `synced` (`owner/doors.json`), as
    every door that changes a lane's rows is.
- **The owner sees it.** The connection's view carries `pays`. The app's sources screen says
  "Free models only: this key has no credit.", and a notice (`connection_free_only`) tells
  the owner once when a key starts to pay for nothing.
- Left, with reasons:
  - The stage connection's first priced call after this lands is refused, and that run fails.
    No read can reach its key without a reconnect or a widening reseal. A reconnect reads it at
    once. The widening reseal is a trust-model change for its own lane, if wanted.
  - A refusal on a key that pays but has run dry flips it to free models only, and the hourly
    re-read may flip it back when the key endpoint cannot tell (an account with no balance but
    no key limit: the balance is another endpoint). At most one refused run an hour, only while
    the owner runs.
  - Among the models a free key is served, the platform's ranking ranks none, so `auto` picks
    the first unranked one by id (`cohere/north-mini-code:free` in pi-ai 1.4.0) and tells the
    owner (`model_unranked`). Ranking free models needs research of its own.
  - A zero-priced router is metered at its catalog price, zero, though it bills the model it
    routes to. That is a metering finding beside this lane, not part of it.

## Owed

- [ ] live: at the stage, OpenRouter's `GET /api/v1/key` answers `data.is_free_tier` and `data.limit_remaining` as the record reads them, and says which case the stage key is in; the stage connection's next priced call is refused `402`, its sources row then says "Free models only", and the next `auto` run is on a `:free` model.
- [x] untangle/free-model-ranking: a ranking tier for the models a no-credit key is served (OpenRouter's `:free` variants), researched first. Done by the vendor's strength listing, not a tier (`free-model-ranking.md`).
- [ ] fix/router-metering: a zero-priced router (`openrouter/auto` and its kind) is reserved and settled at zero though it bills the routed model; researched first.
