# egress-drop-signal as built (2026-11-22)

`untangle/egress-drop-signal`, owed by #5771's review (`egress-headers.md`): a sealed egress call
drops every request header its API's data does not name (`egress-scope.ts` `egressHeaders`,
held to `callRules`), and nothing said so. The gateway logged nothing at all, so when a dropped
header was one a provider needed, the provider's own `4xx` was the only signal. A request
`content-encoding` was dropped too, while a free call's coded body streamed on as it came.

## Research first (Fact 14), read 2026-11-22

1. **Where the platform keeps signals of this kind (`10469c884`).**
   - **The egress receipt** (`owner/src/egress.ts`, `egress_reservations`, migration 11) is
     the call's row: what was asked (method, path without its query since #5766: "what was
     called, never what the sandbox wrote in the parameters"), how it ended, its status and
     charge. It is read only by the `egressReceipts` door, which no route or tool serves yet,
     so no owner reads it today. A new field there is a new migration (append-only,
     `schema.ts`), a field on the door's settle (`SettleSent`) and a channel from the gateway
     to the door beside `x-ta-resealed`.
   - **Owner notices** (`owner/src/notices.ts`) are "what the owner's work reports that no
     call fails with", read at the `notices` route, with `once` for a state that holds. A
     drop that mattered does fail its call, and the owner can do nothing about one: the fix
     is a line of the API's data (`api-rules.ts` `headers`), which is the platform's.
   - **Workers logs.** The gateway's config has `observability` on and the gateway wrote no
     line. #5761 set the rule for a log line built from untrusted input: names of keys, and
     a value only in a code's shape (`owner/src/sandbox.ts` `shown`), since a value may hold
     a secret.
2. **Who acts on a drop.** The maintainers, who add the header to its API's data. They read
   the logs, and the owed live check (`egress-headers.md`: each engine completes a call with
   only the headers the rules keep) is theirs.
3. **What a drop line can carry safely.** Header names: tokens, lower-cased by `Headers`, the
   sandbox's own. Never a value: `authorization` and its kin carry the placeholder, and any
   header may carry an owner's secret (`ANTHROPIC_CUSTOM_HEADERS`). No owner id and no URL (a
   custom endpoint or a connector's server is the owner's); the scope's API id, a pi-ai
   code, or `service`.
4. **Flood.** Every Claude Code call drops about 30 telemetry headers (`X-Stainless-*`,
   `x-claude-code-*`, `egress-headers.md` item 3), every Codex and pi call a handful, on every
   call. Aggregating per run or per API needs state across requests: the gateway may keep no
   module state (`ci/guards/module-state.test.ts`) and isolates are many, so that would be the
   owner's object, a migration, for a signal the owner cannot act on. A drop matters only
   when the provider refuses the call as sent, a `4xx` (`400` a missing version or session,
   `401`/`403` a server's second credential, `404` an unknown session), not a `408` or `429`,
   the provider's own state (`upstreamCode`).
5. **`content-encoding`, from what the forward path does with bodies.** A free call's body
   streams through unread (`forward.ts` `send`: `request.body`), so its coding still
   describes it; dropping the header sent coded bytes as plain JSON. A metered call's body
   is parsed once and re-serialized by the gateway, uncoded; a coded one failed the parse as
   `invalid_request` after being read whole under the pool's slots, its reason unsaid. The
   answer side already holds the rule (an answer the redaction rewrites loses its
   `content-encoding`). Codex 0.166.0 can send a coded body, but not through the door: its
   `enable_request_compression` (stable, on by default) zstd-codes a Responses body only
   for a ChatGPT-family sign-in to the provider named `OpenAI` (`codex-rs/core/src/client.rs`
   `responses_request_compression`, `protocol/src/auth.rs` `uses_codex_backend`: never
   `ApiKey`; `core/tests/suite/request_compression.rs`, read at `rust-v0.166.0` by the
   review, 2026-11-22), and its API-key run is `forced_login_method="api"` on the record's
   own provider, `TinyAssets` (`codex.json` `per_credential`), so no `-c` is needed. Workers
   cannot decode zstd (`DecompressionStream` takes gzip and
   deflate), and a decoded body's size is not its declared length, which the held bound and
   the owner's admission are both read by.

## Chosen

- **One log line, in the gateway, when the provider refuses the call (`4xx`)**: the names of
  the sandbox's request headers that were not sent on (what `egressHeaders` kept and the
  gateway set; so the placeholder's header, which the sealed one replaced, is no drop, nor
  Cloudflare's or the platform's own, `internal`), at most
  64, each at most 64 characters, the rest counted; the API id and the status. A served call
  logs nothing, so telemetry never floods it, and a refusal is where a drop shows. Not on the
  receipt: the receipt has no reader yet, needs a migration and a gateway-to-door channel, and
  would hold the same names twice beside a log the maintainers read.
- **`content-encoding` goes with the body when the body goes as it came** (`BODY_HEADERS`,
  on a free call, beside its betas), and **a metered call with one is refused
  `egress_body_encoded` (`415`)** before its body is read: the parse reads plain bytes, and
  decoding is no bound the reservation was made by.
- **Checked against the intent.** One home for each: the rule which headers go on stays
  `egressHeaders`'s (data, `BODY_HEADERS` beside `BASE_HEADERS`); the report of what went
  is the gateway's, where the call is sent; no value is logged; no provider is named in code.

## As built

1. **`contract/src/egress-scope.ts`**: `BODY_HEADERS` (`content-encoding`), kept on a free call
   (`egressHeaders`).
2. **`contract/src/refusal.ts`**: `egress_body_encoded`, `415`, one of `GATEWAY_CODES`.
3. **`gateway/src/forward.ts`**: a metered call with a `content-encoding` is refused before
   its slots are taken; `logDropped` writes the drop line when the provider refuses the call
   as sent (`AS_SENT`, by `upstreamCode`: a `4xx`, but a `408` or `429`).
4. **Tests**: a free call's gzip body arrives with its coding and its bytes; a metered coded
   body is refused `415` with no reserve asked; a provider's `400` logs the dropped names and
   no value, a `200`, `429` or `503` logs nothing (`gateway/test/forward.test.ts`, `entry.test.ts`; the fake's
   `say` mode answers at the word `status`).

## Left, with why

- **No owner-facing drop signal.** The owner cannot change what an API's rules name; when the
  egress receipts get a reader, the drop is still a maintainer's fix, not the owner's.
- **A metered coded body is refused, not decoded.** If a pinned engine is found to compress its
  metered calls through the door, decoding under a decoded-size bound is its own change, with
  its own research.

## Owed

- [ ] live: at the dark redeploy, in `egress-headers.md`'s engine check, a call its provider
  refuses shows its drop line in the gateway's Workers logs, names only.
