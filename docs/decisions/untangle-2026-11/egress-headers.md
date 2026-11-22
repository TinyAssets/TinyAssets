# egress-headers as built (2026-11-22)

`untangle/egress-headers`, owed by #5766's review (`egress-redaction.md`): in the egress door's
path that adds a sealed key, a model source's or a connector's, every request header the sandbox
sent went on to the provider unless a denylist in the gateway's code named it
(`forward.ts` `CREDENTIAL_HEADERS`), while the query had become each API's allowlist, kept as data.

## Research first (Fact 14), read 2026-11-22

1. **How the door handles headers today (`1bf7b2ff2`).** The egress door
   (`doors/src/egress.ts`) hands the gateway the sandbox's request with every header it came with
   (E2B's `x-ta-sandbox` and the generation among them). The gateway (`gateway/src/forward.ts`
   `send`), for a model key and a connector's key alike, drops the names `CREDENTIAL_HEADERS`
   lists (`authorization`, `proxy-authorization`, `x-api-key`, `api-key`, `x-goog-api-key`,
   `cookie`, `host`, `forwarded`, `x-forwarded-*`, `x-real-ip`, three method overrides), the hop
   headers, `x-ta-*` and `cf-*`; on a metered call, also any name ending `-beta` that its API's
   `betas` (empty for every API) does not list. Every other header passes, then the sealed header
   is `set` and `accept-encoding: identity`.
2. **What the denylist lets through.** Google's system parameters are each "sent as URL query
   parameters or HTTP request headers" (docs.cloud.google.com/apis/docs/system-parameters, as
   searched; the host is outside this session's network): `$fields` is `X-Goog-FieldMask` (a
   partial answer: the usage the meter reads can be dropped), `$userProject` is
   `X-Goog-User-Project` (the project billed), `quotaUser` is `X-Goog-Quota-User`. `egress-redaction`
   refused exactly these as query parameters; as headers they passed. OpenAI reads
   `OpenAI-Organization` and `OpenAI-Project` (the org and project a call bills to; Codex 0.166.0
   sends them from `OPENAI_ORGANIZATION`, `OPENAI_PROJECT`, which the agent can set), and an
   `anthropic-version` other than the one the meter was written against changes the answer it
   reads. A connector's server may read any header it likes as a second credential. The class is
   open-ended, so a denylist can never be finished: the same reason the query is an allowlist.
   Smuggling data is no threat of its own: the destination is the sealed origin, which the body
   already reaches.
3. **What each API in our data needs**, from the pinned packages (`npm pack`, read 2026-11-22):
   - Anthropic (`anthropic-messages`; Claude Code 2.1.309, pi-ai 1.4.0 over `@anthropic-ai/sdk`
     0.129.0): the SDK sends `Accept`, `Content-Type`, `User-Agent`, `anthropic-version:
     2023-06-01`, `X-Stainless-*` (telemetry), the key, and its callers' `anthropic-beta`;
     Claude Code adds about 30 of its own (`x-app`, `X-Claude-Code-Session-Id`,
     `x-claude-code-agent-id`, `x-anthropic-additional-protection`, `anthropic-client-*`, ...),
     changing release to release, and `ANTHROPIC_CUSTOM_HEADERS`. Anthropic's API needs the key,
     `anthropic-version` and `content-type`; a beta only for a body that uses one.
   - OpenAI and xAI (`openai-responses`; Codex 0.166.0, pi-ai over `openai` 7.19.0): `Accept`,
     `Content-Type`, `User-Agent`, `X-Stainless-*`, the key; Codex adds `originator`,
     `session_id`, `x-client-request-id`, `x-codex-*`, `x-openai-subagent`, and `OpenAI-Beta`
     only on its WebSocket transport, which the door does not carry (`upgrade` is a hop header).
     pi-ai adds `x-client-request-id`, `x-session-id`, `session_id` for session affinity.
   - Chat completions (Cerebras, Groq, OpenRouter) and Mistral, by pi-ai: as OpenAI's; for
     OpenRouter pi-ai also sends its attribution (`HTTP-Referer`, `X-OpenRouter-Title`).
   - Google (`google-generative-ai`; Gemini CLI 0.66.0, pi-ai over `@google/genai` 2.21.0):
     `Content-Type`, `User-Agent`, `x-goog-api-client` (telemetry), `X-Server-Timeout` (a
     deadline hint), the key; Gemini CLI adds `x-gemini-api-privileged-user-id` (its
     installation id, with usage statistics on) and `GEMINI_CLI_CUSTOM_HEADERS`.
   - A connector (an MCP server's one URL; the agent's `mcp.json` gives a URL only,
     `agent-definition.ts` `McpHttp`): the streamable HTTP transport's own, 2025-11-25
     (`Accept`, `Content-Type`, `Mcp-Session-Id`, `MCP-Protocol-Version`, `Last-Event-ID`) and
     2026-07-28 (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, and `Mcp-Param-{Name}` for a
     tool parameter a server marks `x-mcp-header`; github.com/modelcontextprotocol/
     modelcontextprotocol, `docs/specification/2026-07-28/basic/transports/streamable-http.mdx`).
   So no engine's call needs any header beyond HTTP's own three and, per API, a version or a
   beta; everything else is telemetry, session affinity or attribution, which no provider needs
   to serve a call.
4. **What others do.** Credential-injecting proxies inject and overwrite, and strip a configured
   list: Fly's tokenizer (github.com/superfly/tokenizer: the secret into `Authorization` or a
   `dst` header, `allowed_hosts` per secret, `FILTERED_HEADERS` "a comma separated list of request
   headers to strip"), Envoy's credential injector (`credential_injector.proto`: `overwrite`
   "if the injected headers already exist"; no other header touched), Cloudflare AI Gateway's
   stored keys (developers.cloudflare.com/ai-gateway/configuration/bring-your-own-keys: remove
   the provider's auth headers, the gateway's own ride `cf-aig-*`), Anthropic's Managed Agents
   vaults (the token "injected automatically", by MCP server URL). Smokescreen-style egress
   proxies tunnel `CONNECT` by host and never see a header. None of them meters a call, or
   reserves its cost from what it read of the request, and that is what makes a header that
   changes what a call does or bills our problem, as it made the query ours.

## Chosen: a shared base plus each API's additions, as data

- **Why an allowlist.** The harm is a header that changes what a sealed call does (its answer's
  shape, its billing, its credential); that set is each provider's, open-ended and growing, while
  the set a call needs is small and known (item 3). The query took the same shape for the same
  reason.
- **Why drop, not refuse.** The query refuses an unnamed parameter; a header not named is
  dropped. Engines send dozens of telemetry headers that change every release (item 3), and
  dropping one changes nothing a provider serves; refusing would break each engine release. A
  header the rules name with a value its rule does not take is dropped too (one rule): a wrong
  `anthropic-version` dropped is the provider's own loud `400`.
- **Why a base.** `accept`, `content-type` and `user-agent` are HTTP's own, the same for every
  API and the transport: one list, not six copies. `content-type` is held to `application/json`
  (UTF-8 at most): a metered body is the gateway's own UTF-8 JSON, so the provider reads it as
  the gateway parsed it.
- **Checked against the intent.** One home for the rule (`egress-scope.ts` `egressHeaders`,
  beside `egressTarget`, which the gateway calls and which reads the scope's kind once), data
  not code (`api-rules.ts` `headers`, `betas`; the transport's beside its methods), and no
  provider named in code: the denylist's `x-goog-api-key` and the `-beta` suffix match go.

## As built

1. **`ApiRules.headers` and `ApiRules.betas` (`contract/src/api-rules.ts`)**, each a header name
   with the values it may take, as `query` is. `headers` go on every call: Anthropic's
   `anthropic-version: 2023-06-01`, none for the others. `betas` go on a free call only (a beta
   can widen a call past its reservation, the rule `betas` already kept): Anthropic's
   `anthropic-beta`, which Claude Code's token count sends. The `-beta` suffix rule is gone into
   it.
2. **The transport's headers (`egress-scope.ts` `SERVICE_HEADERS`)**, beside its methods: both
   transport versions' names, `mcp-param-*` as a prefix.
3. **One filter (`egress-scope.ts` `egressHeaders`)**: the base (`BASE_HEADERS`), the scope's
   API's `headers` (a service's: the transport's), and on a free call its `betas`; every other
   header dropped, the credential headers among them, since no list names one. Which rules a
   scope's call is held to, its query's and its headers', is one function's (`callRules`: a
   service's `SERVICE_RULES`, else its API's), read by `egressTarget` and `egressHeaders`
   alike (review: the two had each read the scope's kind, and fallen back to no rules for an
   API `supported` never lets a scope name; that now throws).
4. **The gateway (`gateway/src/forward.ts`)** sends what `egressHeaders` keeps, then sets the
   sealed header and `accept-encoding`. `CREDENTIAL_HEADERS` is gone; `HOP_HEADERS` and the
   `x-ta-`/`cf-` rule are the answer's alone.
5. **The fakes are steered by `user-agent`** (`gateway/test/fake-upstream.ts`,
   `doors/test/fake-providers.ts`), the one free-text header every call may carry, so the tests
   drive them through the rule as it is, with no test-only name on any list.

## Left, with why

- **Telemetry, session affinity and attribution are dropped** (`X-Stainless-*`, Claude Code's
  `x-claude-code-*`, Codex's `originator` and `session_id`, `x-goog-api-client`, OpenRouter's
  `HTTP-Referer`): no provider needs one to serve a call. If one ties a feature to one, it is a
  line of data.
- **`anthropic-beta` stays off a metered call**, as before: a beta a metered body needs is
  answered by Anthropic's own `400`, which the run sees. Allowing named betas on a metered call is
  their reservation's question, not this change's.
- **An owner's custom headers** (`ANTHROPIC_CUSTOM_HEADERS`, `GEMINI_CLI_CUSTOM_HEADERS`, pi's
  `models.json` `headers`) do not reach a sealed provider. None is a header a call needs (item 3).

## Owed

- [ ] live: at the dark redeploy, Claude Code, Codex, Gemini CLI and pi each complete a call
  through the egress door with only the headers these rules keep, and an MCP server bound to a
  connector answers its transport's headers (B5's check, with a key).
