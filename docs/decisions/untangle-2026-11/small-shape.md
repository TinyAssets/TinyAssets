# small-shape as built (2026-11-21)

Three owed shape fixes, one PR: `untangle/wake-canonical` (`pass-contract.md` §4),
`untangle/recorder-inbound` (`ci-recorders.md` item 8) and `untangle/events-transient`
(`recheck-doors-e2b.md` §3).

## 1. One canonical JSON (`untangle/wake-canonical`)

`contract/src/wake-kinds.ts` `canonicalJson` is gone; `follows` (the arguments' size bound)
and `subscriptionId` (the id's digest) call `canonical.ts` `canonical`.

**How the two differed,** by reading both. For every value JSON can carry they are
byte-identical. They part only on values no parsed request holds:

| Input | `canonicalJson` | `canonical` |
|---|---|---|
| An object field `undefined` | `"k":undefined`, not JSON | the field left out |
| An array item `undefined` | nothing between the commas (`[1,]`) | `null` |
| `undefined` itself | `undefined`, though typed a string | `"null"` |
| A non-plain object (a `Date`, a class) | its own keys (a `Date` is `{}`) | `JSON.stringify`'s (`toJSON`) |

**Who depended on which.** `canonicalJson`'s only callers were the two above. Every
`subscriptionId` caller passes plain JSON: the doors' `events.ts` (subscribe and unsubscribe,
the arguments `argumentsOf` rebuilds from strings, booleans and a sorted list) and `app.ts`
`doorbell` (a checked `agent_id`, `follows`' `board` and `rooms`). `follows`' callers pass
parsed params, or `{board, rooms}` whose fields may be `undefined` across the object's RPC
(`ledger.ts` `subscribe`), where only the size bound read it, two keys far below
`ARGUMENTS_CHARS`. No digest was computed with one and checked with the other: an id is only
derived by `subscriptionId` (the doors derive it, the object stores what it is given, an
unsubscribe derives it again), and `canonical`'s digests (the policy document's `_digest`,
the image tag) never went through `canonicalJson`. So no stored id moves, and no bug was live.

**Test:** `contract/guards/checkers.test.ts` `subscriptionId` pins an id to the SHA-256 of
its canonical JSON written out, and the same id with an `undefined` field added.

## 2. The wire recorder's gate-off inbound block goes (`untangle/recorder-inbound`)

`doors/test/record/wire.record.ts` `wire: inbound hooks` ran under `OPEN`, whose `inbound`
gate is off, so every event answered `404 not_found`. `wire-reach.record.ts` records the same
events, and more (a replay, a good proof, a bad UTF-8 body, a burst), with the gate on. The
block is deleted and the header points to `wire-reach`. Nothing it alone held is lost: the
gate-off door's `404` is `doors-live.test.ts`'s "does not exist while the control record's
inbound gate is off". The wire recording loses the block's nine lines on purpose; the body
declares it (`Recorders-Differ: wire`).

## 3. MCP Events answers a refusal for the moment as a recoverable failure (`untangle/events-transient`)

**Research** (read 2026-11-21, through the session's proxy):

- The MCP Events design sketch,
  `modelcontextprotocol/experimental-ext-triggers-events` `docs/design-sketch-proposal.md`
  at `c77b84395eff` (the repository's `HEAD`). Its error table has no code for a refusal
  for the moment: `-32024 Forbidden` is "not permitted ... or its access was revoked", and
  `-32025 ResourceExhausted` "a server-imposed limit or quota". Its one recoverable failure,
  "Transient per-occurrence error", is JSON-RPC's `-32603` with `data.reason`
  (`{"code":-32603,"message":"UpstreamError","data":{"reason":"Gmail API 503"}}`), on which
  "the subscription remains active and the server retries". Open question 6: the client
  branches on the codes, "`Forbidden` means stop". Its word for a wait is `retryAfterMs`
  (the subscribe result's `deliveryStatus`).
- MCP's core schema, `modelcontextprotocol/modelcontextprotocol` `schema/draft/schema.ts` at
  `2f3a95074916`: no code for a temporary failure; `-32603` is "an unexpected condition that
  prevents it from fulfilling the request", and the codes `-32000` to `-32019` carry no
  meaning across implementations, so a client could not know to retry one.
- OpenAI's MCP Events page (`developers.openai.com/plugins/build/mcp-events`, its Markdown
  twin) names no request error but `-32015`.

So `Forbidden` told the client to stop for a refusal that says nothing of the request.

**Built:** `doors/src/refused.ts` `asEvent` answers any `transient` refusal as `-32603`, its
message the refusal's, `data` `{reason, retryAfterMs}` from the refusal's message and wait:
the refusal's own `transient` and `retryAfter`, as `asShared` and `asRedirect` read them, so
`unavailable`, `restoring`, `busy`, `storage_busy` and #5721's `object_overloaded` are one
answer and no code is named. `EVENT_ERRORS` gains `internal`. The doors spec's MCP Events
requirement says it.

**Tests:** `doors/test/refused.test.ts` (a closed gate's, a restore's and a refusal's own
wait), and `doors/test/events.test.ts`, a full pool's refusal of the object's `wakeEvents`
across its real RPC answered `-32603` with a second, then the same subscribe succeeding.

## Found and left

- `canonical` reads an array's hole as nothing (`[,1]` is `[,1]`): `map` skips holes. No
  caller can hold one (parsed JSON and literals have none), so left.
- The sketch now numbers `CallbackEndpointError` `-32027` (earlier revisions `-32011` to
  `-32015`). The record's `verify_error_code` is OpenAI's `-32015`, which its page still
  states, and OpenAI is the one client that subscribes, so the record stands.
