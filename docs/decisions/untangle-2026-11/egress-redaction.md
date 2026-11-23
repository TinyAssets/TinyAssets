# egress-redaction as built (2026-11-22)

`untangle/egress-redaction`, owed by #5763's review (`connector-credentials.md`): in the egress
door's path that adds a sealed key, a model source's or a connector's, (1) the key was redacted
from an answer raw and JSON-escaped only, never base64 or URL-encoded; (2) any query string was
allowed on a sealed path; (3) the egress receipt kept the sandbox's query string.

## Research first (Fact 14), read 2026-11-22

1. **What an upstream can echo.** A key comes back in the spellings the upstream handed it or
   made of it: as it is, in a JSON string (a writer may escape `/` as `\/`, PHP's default, or
   `<`, `>`, `&` as `\u003c`, `\u003e`, `\u0026`, Go's `encoding/json` default), URL-encoded
   (a redirect's `Location` or an error quoting the request URL, as a URL component or as a
   form writes it, in upper-case hex or lower-case, .NET's `HttpUtility.UrlEncode`), and
   base64 (an error or a debug field echoing an `Authorization` header in `Basic` form, a
   token carried as base64url).
   A key inside a longer base64 text is spelled one of three ways by where it starts
   (its offset modulo 3), and the characters at its two ends mix in its neighbours' bits.
2. **What others do.** GitHub Actions' log masker
   (`actions/runner` `src/Sdk/DTLogging/Logging/ValueEncoders.cs`) masks each secret raw,
   JSON-escaped, `Uri.EscapeDataString`-escaped, XML-escaped and in base64 at three shifts
   (`Base64StringEscapeShift1/2`, the secret with its first one or two bytes dropped). Its
   shifts leave the edge characters in and drop none at the end, so a secret followed by more
   bytes is missed; here each shift keeps only the characters the secret alone decides. XML
   escaping is not taken: no model API or MCP transport answers in XML.
3. **Whether a sealed path needs a query.** Every engine and pi-ai, by the records' egress
   endpoints:
   - Anthropic (`anthropic-messages`): the SDK's beta client, pi-ai's
     (`anthropic-messages.js` `client.beta.messages.create`) and Claude Code's, posts
     `/v1/messages?beta=true` and `/v1/messages/count_tokens?beta=true`; Claude Code lists its
     models as `GET /v1/models?limit=1000` and tells a gateway to "match on the path, not the
     full URL" (code.claude.com/docs/en/llm-gateway-protocol).
   - Google (`google-generative-ai`): `@google/genai`, pi-ai's and Gemini CLI's, streams as
     `:streamGenerateContent?alt=sse` (and forces `alt=sse` on a stream).
   - OpenAI and xAI (`openai-responses`): Codex lists its models as
     `GET /models?client_version=<major.minor.patch>` (OpenAI's Codex model-catalog docs, as
     searched; the page's host is outside this session's network).
   - Chat completions (Cerebras, Groq, OpenRouter) and Mistral: no query.
   - A connector (an MCP server's one URL): the streamable HTTP transport is one endpoint; its
     session rides `Mcp-Session-Id`, and the contract already refuses a sealed URL with a query
     (`serviceSource`). No query.
   - Azure's `api-version` is no API the gateway runs (`api-rules.ts` `APIS`).
   - Checked in the pinned packages (review, 2026-11-22): `@anthropic-ai/sdk` 0.129.0
     (`resources/beta/messages/messages.js`: `/v1/messages?beta=true`,
     `/v1/messages/count_tokens?beta=true`); Claude Code 2.1.309's binary
     (`models.list({betas, limit: 1000})`, the gateway's `/v1/models?limit=1000`);
     `@google/genai` 2.21.0 (`requestStream` sets `alt=sse`; `pageSize`, `pageToken` and
     `filter` only on `models.list`, which neither Gemini CLI 0.66.0's bundle nor pi-ai 1.4.0
     calls); Codex 0.166.0's binary (`client_version=` in `models-manager`); pi-ai 1.4.0's
     Mistral, chat-completions and Responses calls send none.
   A query can change what a call does: Google's system parameters include `alt` (the answer's
   format, which the meter reads), `fields` (a partial answer, its usage dropped) and
   `userProject` (the project billed). So the rule is an allowlist, never a denylist.

## As built

1. **One redaction for every sealed key (`gateway/src/redact.ts`).** `redaction(secrets)` is
   built once per answer, from every spelling of each secret: raw, URL-encoded (component and
   form, either hex case) and base64 (three starts, standard and URL-safe alphabets, trimmed to
   the secret's own characters), each also in its three JSON spellings; `text` redacts a header
   value, `stream` a body across chunk boundaries. Needles are kept under their first byte, so
   the per-byte scan stays one look while the spellings grew from two to about thirty.
   `forward.ts` holds no redaction of its own.
2. **A sealed call's query is its API's data (`contract/src/api-rules.ts` `query`).** Each API
   names the parameters its engines send and the values each takes: Anthropic `beta=true` and
   `limit` (digits), Google `alt=sse`, Responses `client_version` (a version), chat completions
   and Mistral none, a service none.
3. **One rule, owner and gateway alike (`egress-scope.ts` `egressTarget`).** The target is
   sent with only its allowed parameters, written back by the URL parser (so what is sent is
   what was read), each credential parameter (`key`, `api_key`, `access_token`, any case)
   stripped; any other parameter, or a value its rule does not take, makes the target null:
   `egress_endpoint_refused` at the owner's open, `endpoint_refused` at the gateway. The
   gateway's own strip loop is gone into it.
4. **The receipt keeps the path alone (`owner/src/egress.ts` `#record`).** Every
   `egress_reservations` row, admitted or refused, stores its path without the query. The
   owner's open judges the free and metered call by the target's path, the one the gateway
   sends, so `freeCall`, `meteredCall` and the reserve no longer drop a query themselves.

## Left, with why

- **Headers stay a denylist** (`forward.ts` `CREDENTIAL_HEADERS`): engines send many headers a
  provider needs (`anthropic-version`, `anthropic-beta` on a sign-in, SDK telemetry), so an
  allowlist needs each engine's headers read first. Since done (`egress-headers.md`).
- **A secret that is itself an encoding** (an owner's `Basic` key, base64 of `user:pass`) is
  redacted as given and in its encodings, not decoded.
- **Receipts written before this change** keep their query until they age out (the newest 2,000
  admitted and 500 refused are kept); no migration rewrites them.
- **Parameters no engine is seen sending** (Gemini's `pageSize`, a provider's other options) are
  refused until one does; then each is one line of data.

## Owed

- [x] `untangle/egress-headers`: research each engine's request headers, then decide whether a
  sealed call's headers become an allowlist per API, as its query now is. Done
  (`egress-headers.md`): a shared base plus each API's additions, as data.
- [ ] live: at the dark redeploy, Claude Code's `?beta=true`, Gemini CLI's `?alt=sse` and Codex's
  `?client_version=` (where it lists its models on a key) each reach their provider through
  the egress door. No provider quotes a key back on purpose, so the redaction is the fake
  upstream's test (`gateway/test/forward.test.ts`), not a live check.
