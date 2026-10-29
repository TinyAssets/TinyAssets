Build order is the plan of record's "Migration and build order" (ADR-032), one
section per slice. An item is ticked in the PR that lands it. A landed item whose
live acceptance is still owed says so.

## 1. Verify the unverified facts

- [x] E2B p50/p95 resume for a 1 GiB sandbox, measured live; paused-storage price
  and retention, Pro-plan concurrency, regions. A p95 over 1.5 s switches the
  sandbox to Cloudflare Containers. Measured 2026-10-28: p50 247 ms, p95 706 ms
  over 40 cycles, so E2B stays; paused sandboxes are free, kept indefinitely and
  outside concurrency; Pro 100 concurrent (`e2b-resume-measurement.md`).
- [ ] E2B resume after hours or days paused (only up to 5 min was measured).
- [x] Durable Object pricing and limits (2026-10-28, facts-verified.md).
- [ ] 2026 regional availability from Cloudflare's status history (blocked).
- [x] Dynamic Workers and Facets: open beta, Workers Paid only, $0.002 per Dynamic
  Worker per day after 1,000 a month; on the hooks path.
- [x] OpenAI MCP Events delivery and Sign in with ChatGPT plan-usage terms.
- [ ] MCP Events plan tiers; any Grok or Meta Muse wake API (sites blocked).

## 2. Doors and identity

- [x] 2.1 The doors Worker (`platform/doors`, slice 7), in workerd, nothing deployed: the
  binder (`binder.ts`, the only `getByName` and the only `Binding`, every field from
  the verified credential), our authorization server on Workers OAuth Provider 1.2.4
  (issuer `https://tinyassets.io`, resources `/mcp` and `/app`, CIMD on with
  `global_fetch_strictly_public`, DCR only behind `DCR_ENABLED`, `prompt=login` on every
  sign-in, consent never remembered, `authenticated_at` in the grant's props and never
  moved by a refresh), the stateless MCP door on SDK v2's `createMcpHandler` 2.3.1 for
  2026-07-28 and 2025-11-25, one tool per outside-callable owner door with all four
  hints (`tools.ts`), `/api` keys in the Python's format, the owner surface
  `/app/api/*` with the session cookie and CSRF rules, and the owner directory's `k:`
  entries and fail-closed `c:control` record. Guards: the binder, the tool table, the
  config; the platform's module-state and self-reach guards scan it. Declared
  differences from the research: the first-party app is a CIMD client on our own
  origin (`/oauth/clients/web.json`), so the first-party list needs no registry row or
  config value; the doors port `apiKeyOrigin` (the object's `ledger.ts` is the object's
  alone), held equal to the object's by test; the MCP client derivation is
  domain-separated (`tinyassets-mcp-client\0issuer\0client_id`), which the research's
  revoke step wrote as `issuer\0client_id`; and the SDK answers a thrown tool error as
  the tool's error result, so a bug is logged in full and answered `internal error`.
  From slice 7's security review: an outside client naming a redirect on `tinyassets.io`
  or any subdomain is refused at `/authorize` (CIMD or DCR alike); sign-out revokes the
  presented token's grant (a token gone since validation is `401`); each device keeps its
  own first-party grant (`revokeExistingGrants: false`), while an outside client's
  re-authorization replaces its earlier grant from the same installation (the provider's
  default per exact redirect URI, and the doors' own replacement across a loopback
  client's ports); a loopback redirect's port (`localhost`, `127.0.0.0/8`, `::1`) is no
  part of an agent's `family` (RFC 8252 §7.3); a `control` record that is there but
  malformed or unreadable also closes the owner's surfaces; every body the doors read is
  bounded; no token exchange callback is installed; and the owner is named from the
  sign-in's issuer against an explicit list (`OWNER_ISSUER`, and `FOREIGN_ISSUERS`, empty
  until a provider's slice lists its issuer), any other issuer refused loudly.
- [ ] Live AuthKit wiring: an `Upstream` (`doors/src/upstream.ts`) for AuthKit's
  production environment, with its redirect URI `https://tinyassets.io/oauth/upstream/callback`,
  the code exchange and the ID token's signature, issuer, audience and `auth_time`
  against its JWKS; `OWNER_ISSUER` set to AuthKit's issuer (the config guard pins it
  unset until then); production Google and GitHub apps (founder dashboard work). Until
  then the deployed entry refuses every sign-in loudly (`UNWIRED`).
- [ ] The AuthKit upstream refuses an ID token without `auth_time`: there is no clock
  fallback (this replaces the research's "use the binder's own clock"), since the fence
  and the session's 600 s bound both rest on the sign-in's real time. The binder names
  the owner from the token's issuer and subject: AuthKit's subjects stay owner ids as
  today, and a later issuer's (Sign in with ChatGPT) are namespaced `iss-<hash>-<sub>`
  once that provider's slice lists it in `FOREIGN_ISSUERS` (any unlisted issuer is
  refused), so linking a second provider to an existing owner is that slice's own act.
  The AuthKit slice sets `OWNER_ISSUER` to the exact `iss` its tokens carry, checked
  against a live token, since any other spelling refuses every sign-in.
- [ ] DNS, routes and deploy: the doors Worker's routes on `tinyassets.io` (`/mcp`,
  `/api/*`, `/hooks/inbound/*`, `/app/api/*`, `/authorize`, `/oauth/*`,
  `/.well-known/oauth-*`), the `OAUTH_KV` and `DIRECTORY` namespaces, then the handles
  canary (Fact 11). Cutover (9); every existing connector re-authorizes once, since the
  issuer moves from AuthKit to `https://tinyassets.io`.
- [ ] `/hooks/inbound`: the capability-URL and shared-app (Slack HMAC) shapes in the
  binder, the directory's `h:` and `i:` entries, the 1 MiB cap and the `control.inbound`
  gate, and a new owner door `inboundAccept` (config, revision, dedupe, owner-held
  secrets, the breaker).
- [ ] Owner admission and erasure, in the owner's object: an `admission` row
  (`admitted | suspended{billing, abuse} | erased`), checked in `#bind` at every door
  and by every lane with `require_work` semantics, an erasure tombstone so a stale
  `getByName` never recreates an erased owner, and a new door `admissionSet` taken only
  from a reserved `platform` caller, with a guard keeping that name out of every source
  but `platform/admin`. This **reverses** the earlier wording here ("the binder refuses
  an unadmitted owner before it reaches the object, from the directory's admission
  state"), because the object is strongly consistent and already on every path (no
  extra read, no 60 s lag), the alarm reads it locally, and erasure needs an object
  tombstone anyway. The binder keeps the platform gates (`control.ordinary`, the
  canary) from the directory.
- [ ] The key store in the owner's object: a new door holding each API key's id, name,
  mint time and last use, so the owner's key list and erasure can enumerate key ids
  (the grant row holds only the key's client hash), the 25-key count is strict, and
  an agent's held API-key request is applied with its grant (`_apply_key_request`).
- [ ] Rate limits: two `ratelimits` bindings, 120 a minute per key and 600 per owner,
  declared approximate (their price and miniflare support unverified); the config guard
  admits them with their slice. They count JSON-RPC messages, not HTTP requests (a
  2025-11-25 batch is one request of many calls), and a third limit bounds the CIMD
  metadata fetches an unknown `client_id` makes the provider send.
- [ ] The agent tools: `run_graph` and `converse` as tools and scope capabilities, with
  the agent's doors (5).
- [ ] Plan correction (doors section): "plus the earlier revision for Claude channels"
  becomes "plus 2025-11-25 for clients still on MCP SDK 1.x, served statelessly from
  the same handler"; Claude channels are local stdio plugins that cannot use 2026-07-28,
  so a TinyAssets channel is a stdio plugin calling the door as an ordinary client (S9).
- [x] Owed by 3.5 (slice 7). The owner's sign-in: the binder runs it (server PKCE,
  `prompt=login`, the browser-bound callback, the cookie's attributes) and calls
  `sessionOpen` with the verified sign-in's time, which the object takes only when
  at most 600 s old; it forwards the browser's session cookie as the binding's
  `session` and never vouches for it. The cookie carries no owner: the edge takes the
  owner from a separate verified credential, never from the cookie and never from a
  directory map of cookie hashes. The binder attaches `session` only to the browser's
  same-origin JSON requests with the exact `Origin` header (the Python's
  `owner_sessions.require` CSRF guard), never to `/mcp`, `/api` or `/hooks` traffic,
  and a request carrying an outside origin never reaches an owner-surface door (the
  Python's `mailbox_door._owner`); the object refuses an outside origin with a
  session itself, at every door but `registerAgent`, the owner's browser registering
  an agent for an API key it holds; an MCP client's agent registers through the card the
  client files on its own call (`registerPropose`), which the owner's UI shows
  (`registerProposal`) and applies by id and digest. Every outside call
  carries its verified `source`. Every MCP call carries `authenticated_at`: the grant's
  original interactive sign-in, which a token refresh never moves; after a revoke, and
  once any MCP revoke is forgotten, an MCP call without it is refused. The outside kill switch
  (`TINYASSETS_OUTSIDE_DENY`) refuses every outside call at the binder, from the
  directory: the object has no env. A key's mint writes its grant (`grantSet`,
  source `api_key`), a key's revoke calls `grantRevoke` first, and the binder calls
  `grantForget` only after the directory has deleted an API key, never for an MCP
  client. On `grantRevoke` the binder also revokes the client's grant in the OAuth
  provider, so its refresh tokens die with it, and a sign-in after a revoke always shows
  the consent screen, so only the owner's own consent brings a revoked client back.
  Built in `platform/doors` with a test each; the outside kill switch reaching the
  object's alarm is task 7's doorbell, and `_apply_key_request` comes with the key store
  above.
- [ ] Owed by 6.1. The file door's bytes: the binder streams chunks to `FILES` itself
  (decided: the binder is the trust root and already reaches every owner's object, so
  its reading an owner's namespace from that owner's object weakens no isolation), with
  the object's `fileUploadBegin`/`fileUploadCommit` around it and the whole-file digest
  (`Repr-Digest`) computed and served as it streams; the object's chunk doors stay for
  in-object callers (the environment, the sync agent's small files).

## 3. Owner object

- [x] 3.1 The `platform/` TypeScript workspace: pinned versions, lockfile,
  `wrangler`, tests in workerd. No deploy.
- [x] 3.2 `OwnerDO`: SQLite-backed, schema migrated on wake, serving only the
  owner it is named by (its stored owner a second check), the caller a typed RPC
  argument; `disable_ctx_exports` set; guards for module-level state and writes
  through the global object, for a config that binds nothing, and for `exports`
  or `env` reach in the source.
- [x] 3.3 The one revision primitive (S34) ported from `broker/conversations.py`
  (`s5b-board`), with history and every bound; the Python tests ported. The
  bounds read per-scope counters kept by triggers in each write's transaction.
- [x] 3.4 The ledger, receipts and bodies; the mailbox (S30, S3a, S3b), ported from
  `broker/message_ledger.py` with its tests. Unacked mail is reserved per mailbox inside
  the ceilings, so eviction always makes progress; a disabled agent's mail is released
  and an owner-deleted agent's purged. Wake limits are owner policy (a policy write
  since 3.5). Wakes and revokes sit on the object's one schedule; only its alarm
  claims and settles them, in process, so no RPC reaches a claim. A revoke carries the
  subscription generation it erases through, so a stale one never erases a re-enabled
  agent's newer callback.
- [x] 3.5 Policy, grants and sessions, ported from `broker/policy.py` (the document,
  its write and held proposals, receipts, `receipt_refusal`), its P0-3 grants
  (`p0-3-outside-grants`, PR #5343) and `onboarding/owner_sessions.py`, with their
  tests. The object checks the owner's live session row itself for an agent register,
  disable or delete, a policy write, a wake-limits change and a grant change, and a
  refused one leaves a refusal receipt; an outside client's call carrying the owner's
  session is refused at every door but an agent's registration. The object admits
  every outside call, at every door the collections included, against its client's
  grant and scopes, and a grant change ends that client's agents' wakes and revokes
  their callbacks in its own transaction; `Grants.standing` rechecks saved work
  against the grant generation it was stamped with. The wake-limits setter is a
  policy write: in session, against the policy generation, receipted; the limits stay
  on the agent's row until owner policy feeds pi's settings (5), when they move into
  the policy document. Declared differences are in each module's header: the cookie
  alone is a proof (never its hash); the object mints it; sessions, proposals (with a
  per-client share), each receipt bucket and the grant rows the owner sets are
  bounded, and a grant row nothing depends on is forgotten oldest first rather than a
  new client refused; an egress host the URL parser reads as an IP address waits for
  the gateway's address check (4).
- [x] 3.6 The schedule: one table, run as jobs on the Agents SDK Lifecycle queue, which
  owns the single alarm and PiHarness's wake-up job; 3.4's direct `setAlarm`/`deleteAlarm`
  move onto it. Built (`schedule.ts`): `OwnerDO` holds a Lifecycle (`agents/lifecycle`,
  agents 0.31.0 pinned exactly, by lockfile integrity too), never the SDK's `Agent`,
  installs none of its runtime handlers, and its `alarm` only hands the runtime's alarm
  to it. Each kind of timed work is a lane with its own rows and one single-flight
  Lifecycle job, `owner-schedule/<lane>`, derived from them: `wakes` (3.4's wakes and
  revokes), `files` (6.1's housekeeping, with its retention re-apply) and `work`
  (stamped scheduled work, `scheduled.ts`). Every changing door, and the object's wake,
  re-derive the jobs, so any door added later that changes a lane's rows calls
  `#schedule.sync()` too; a re-derive keeps a job Lifecycle moved later (the
  memory-limit breaker's backoff) unless the rows have earlier work. A pass is short:
  it keeps each outside call's failure and each row's failed step in that row, backed
  off there, throws only for a bug (then its lane holds back `LANE_RETRY_S`), and hands
  long work off. It fits its single-flight hung timeout (`LANE_HUNG_S`, 60 s) by wall
  clock, not by batch size: it takes no new row once `LANE_PASS_S` (45 s) has passed,
  and each row's outside call is bounded by the rest. The alarm and policy-boundary
  guards are a tripwire for our own code, not proof against deliberate evasion: they
  keep sources off `setAlarm`, `deleteAlarm`, `getAlarm`, `disableAlarms` and
  `deleteAll` (the plain spellings and the obvious split ones), off computed access to a
  storage or a Lifecycle, off every `agents` import but `agents/lifecycle`, off
  Lifecycle's handlers, and off Lifecycle's and pi's tables.
  Stamped work (owed by 3.5): a scheduled turn or automation carries the outside
  authority it was made under (client, grant generation, sign-in time), rechecked with
  `Grants.standing` when made and before it is handed to pi; a grant change refuses the
  client's pending work in its own transaction; open work holds its client's grant row
  (`held`, slice 3's review N3); the lane only hands a turn to pi's durable submit,
  fenced by a lease and a may-have-been-handed mark, so `unknown` work is never handed
  again, and a runner that holds its hand-off past `WORK_HANDOFF_MS` is reported. Slice
  3's review carries: only the owner's own act lifts a revoke (L1, and slice 5's review
  M1): an API key's revoked row goes at the binder's `grantForget` once the directory
  deleted the key; an MCP client's only by the owner in session (`grantForgetRevoked`),
  and every forgotten MCP revoke moves the owner-wide fence (`grant_fences`), so a
  client with no row is admitted only from a sign-in after it; the owner's bound counts
  revoked rows and refuses with the way out named, and never deletes a row for another
  client's grant. An MCP agent registers through a card its client files on its own
  verified call (`registerPropose`), applied by the owner in session by card id and
  digest, so the owner's browser pairs an outside origin with its session only for an
  API key (L2). The scoped capabilities are declared and are the only ones a scope may
  name (N1). The refusal receipt's generation and the grant row's `CHECK` are held by
  tests (N4). The scheduled-work runner is the agent's (5): until it lands, pending work
  is held and gives the lane no job.
- [ ] 3.7 The owner's action decision, from `broker/policy.py` and
  `broker/authority_gate.py` (#5333): `decide` with spend reservations,
  once-approvals, standing grants and declared operation kinds, which the gateway's
  binding gate at OPEN (4) and the agent (5) call. It waits for the gateway because its
  request digest (`once_request_digest`) and connection facts (`connection_revision`)
  are the gateway's and the connection metadata's.

## 4. Gateway

Settled by the gateway research (2026-10-28): one keyring in the gateway's own Worker
secret seals each credential to its owner, connection and version with its endpoints
inside; the loop speaks pi-ai's own pi-messages protocol to the gateway, which runs pi-ai's
adapter for the source; sandbox egress is passed through raw under the sealed endpoints;
the owner object reserves and settles spend. Six slices, G1 to G3 with no spending, no
account and no deploy.

- [x] G0 The 32-call cap probe (with 6's probe item): two throwaway workers.dev Workers
  through a `workflow_dispatch` workflow, measuring service-binding RPC, `fetch`,
  Durable Object stub and public-URL calls per Worker request, per DO RPC, per alarm and
  per background work outliving its RPC, the 15-minute alarm cut, and whether Worker
  `fetch` reaches names resolving to private addresses (miniflare's limit enforcer is a
  no-op, so only live counts). It ran live (`cap-probe.yml`; the results are in
  `cap-probe.md`, and the follow-up runs are 6's probe item). The platform's subrequest
  limit (`limits.subrequests`) is the only cap on gateway calls: no per-kind budget is
  kept beside it.
- [x] G1 `platform/gateway`, the Worker, in workerd, nothing deployed. Config: no
  binding, no routes, no workers.dev or preview URL, `secrets.required:
  ["GATEWAY_KEYRING"]` (wrangler's schema shape), flags `nodejs_compat`,
  `nodejs_compat_do_not_populate_process_env` (so the keyring is never in `process.env`,
  where any SDK could read it), `disable_ctx_exports`, `global_fetch_strictly_public`.
  `keyring.ts` (the only reader of the secret: AES-256-GCM under HKDF-SHA256(root[kid],
  owner), AAD owner, connection, version and kid; a blob under an older kid also comes back
  resealed at the next version), `envelope.ts` (`x-ta-envelope`, the owner grammar of
  `owner/src/text.ts`), `messages.ts` and `encode.ts` (`POST /v1/messages` in pi-messages,
  pi-ai's built-in provider or a declared custom endpoint, the origin check, the clamp,
  `streamSimple` through an endpoint-guarding fetch that never follows a redirect, and the
  inverse of pi-ai's own client), `forward.ts` (raw egress and the setup check
  `verify`), `oauth.ts` (exchange with PKCE, refresh, RFC 7009 revoke) and `index.ts` (a
  `WorkerEntrypoint`: `fetch`, `seal`, `exchange`, `refresh`, `verify`, `forward`,
  `revokeAtProvider`, `reseal`). Every error is a fixed code. Guards: the config against
  wrangler's schema; the keyring named only in `keyring.ts`; no `process`, `OWNER` or
  `getByName`; the platform's module-state and self-reach guards scan `gateway/src`.
  Tested against pi-ai's faux provider and a fake upstream in each provider's wire format
  (Anthropic Messages, OpenAI Responses, Google Generative AI) through pi-ai's own
  pi-messages client, cancellation reaching the upstream included. Bundle 3,980 KiB,
  539 KiB gzip (the limit is 64 MiB uncompressed on both plans; local startup 32 ms of
  the 1 s limit). Declared differences from the research: Google's adapters refuse a
  custom fetch, so every model call runs in an `AsyncLocalStorage` scope whose global
  `fetch` is the call's guard (`egress.ts`, the module-state guard's one pinned exemption),
  which holds Google to the sealed origin, paths, methods and no redirects like the rest
  (the security review's key-leak finding); only APIs whose every request goes through
  fetch run, so Bedrock and the legacy Codex backend are refused; the clamp also lowers
  the model's own output limit, since a thinking budget otherwise raises the request's
  past it, and a reasoning call under 2048 tokens is refused (`reservation_too_small`);
  `forward` returns the provider's `Response`, its bytes counted by the binder that
  streams them, asks for an unencoded answer and redacts every secret from its headers
  and body; `verify` checks the sealed `verify_url` and lists pi-ai's catalog models (no
  per-provider listing parse); sealed URLs must be canonical; pi-ai's own refresh runs
  only for Sign in with ChatGPT, through its statically bundled OAuth flows
  (`registerBunOAuthFlows`, since its lazy loader's import cannot be bundled) and inside
  a scope that reaches only the sealed token endpoint.
- [x] G2 The owner side, in workerd, nothing deployed. `GATEWAY` joins the owner config
  guard's `SERVICES`: the gateway Worker binds nothing and declares only `GATEWAY_KEYRING`
  (`secrets.required`). Migration 6: `connections` (the clear facts: kind, provider,
  models with their metadata, version, OAuth expiry, the refresh lease, a pending
  window, a provider revoke owed), `connection_secrets` (one sealed blob per connection, at
  its version), `connection_flows` (the SHA-256 of the state, the PKCE verifier while
  open, the session) and `model_reservations`, with the counters every cap reads and only
  triggers write: `model_spend` (each source's day and month) and `model_turns` (each
  turn's requests, tokens and cost, kept until the turn is idle 30 days with no call
  open), so the reservation rows are receipts only. A connection keeps up to 1,024 models
  (the largest built-in catalog, 386, found by a scan, connects). Owner policy's optional
  `models` table: per-turn requests, tokens and cost (µ$), owner-wide and per-source
  (by source record) daily and monthly cost, pi's retries.
  `connections.ts`: `connectionPrepare`, `connectionCommit`, `connectionRevoke`,
  `connectionList`, `flowBegin`, `flowFinish`, each the owner's in session, a refusal
  receipted; the refresh lease (one refresh per connection, which concurrent calls wait
  on, stored by compare-and-swap; a refused grant fails the connection; a dead instance's
  lease is taken over); a reseal stored by compare-and-swap, never under a refresh lease;
  the `connections` lane (expired flows, unclaimed pending rows, provider revokes, each
  outcome in its row; a source with no revocation endpoint has its blob deleted with a
  notice; a refresh landing after a revoke keeps its rotated tokens for the lane to
  revoke; a dropped refresh clears its lease), every changing door syncing it.
  `usage.ts`: reserve (the policy, the turn's counters and the spend read in the unit
  that inserts, output shrunk to what the caps leave, refusals kept as receipts),
  dispatched (re-checked: reserved, the policy generation, the connection's version, not
  aborted), settle (`succeeded` from pi-ai's usage, `failed` when the gateway answered
  with a refusal's status, its own or the provider's before any output, else `unknown`,
  which keeps its reservation), abandon (a dispatch or settle unit that threw), and a dead
  instance's open rows settled by the next reserve or receipts read; `usageReceipts`
  (owner only). `gateway-provider.ts` is A1's. Owed by 3.5,
  done: `sessionCloseAll` cancels every open flow in its unit, `sessionClose` its
  session's; the envelope carries the policy generation read in the reserving unit, with
  no call back. Declared differences from the research: an OAuth revoke keeps the blob,
  unusable, until the provider revoke (it is the only copy of the token to revoke), then
  deletes it, or after 5 failed tries with a notice; an API key's goes in the revoke's
  unit; `verify` now lists each model with its metadata (G1's answer held ids only, and
  the object has no catalog); the connections row keeps no base URL (it is sealed); the
  model policy's per-turn limits replace the Python's constants and its free-tier pools
  have no counterpart; the daily and monthly ceilings default to none, as the Python's.
  Review fixes: a provider's refusal before any output is answered by the gateway with
  its code and status, so it settles `failed` at no cost (it had held its estimate as
  `unknown`), and pi sees the gateway's code, named so that pi-ai's own classifier
  retries a timeout, a rate limit, an overload or a server error and no other; pruning
  receipts no longer frees a turn's caps (5,000 requests of one turn hold); a turn is
  the input at the origin of the call's causal chain, not the newest user message (A1).
  Mutation, the reviewer's round-3 set of 85: 14 name code the origin design deleted (the
  session index, its anchors and the background walk); of the 71 left, 62 killed and the 9
  that live equivalent (a failed call's tokens and cost are settled 0, so counting it
  among the settled changes nothing; the dispatch's state guard, checked in its own unit
  first; dropping a settled row from the in-memory open set, which is read only for open
  rows; the reseal's own-refresh check, which the lease row also holds; `#owed`'s due
  check behind the lane's own due query; the tee's undefined session, which no subscriber
  can hold; the gateway's first-event check of an abort, which cannot coincide with a
  provider's refusal status). Mine, 126 across the caps, the turn counters, the
  reservation states, the turn's origin, the lease, the revoke, the envelope, `auto`, the
  gate placement, the upstream codes and the gateway's first event: 123 killed, the 3
  that live equivalent (the reseal's version-shape check of the gateway's own answer; the
  output cap the gateway clamps to anyway; `flowBegin`'s sync, which its pending row's
  sweep covers). Open: the
  doors' `GATEWAY` binding
  and the connect routes (G3); the policy write that ends in-flight turns and closes their
  tee views (A2); the receipts' rows per call, measured live (G5).
- [ ] G3 The connect doors (the doors Worker): paste-key connect and OAuth connect with
  its callback; the source records ported as data files with a schema; plaintext never
  reaches `OwnerDO`.
- [ ] G4 The egress door (with the sandbox slice): `egress.tinyassets.io/c/<connection>/*`,
  the sandbox identity (E2B workload identity or a lease token), `egressOpen`, `forward`,
  `egressSettle`; the E2B network document from owner policy. Owed by 3.5: each egress
  request is admitted at open against the owner's current policy generation, so no
  tunnel outlives a write; a policy write ends the owner's in-flight turns and re-sets the
  sandbox's network, fail-closed until it does; resolved addresses are not visible to
  Workers fetch, so the gateway fetches strictly publicly, the object refuses IP
  literals, E2B filters CIDRs, and a live probe confirms private addresses are
  unreachable.
- [ ] G5 Measurement and cost: pi-durable's partial commits at most once a second with
  the live tee, per-owner busy seconds, rows written, call durations and delta counts, and
  the rule for building the deferred mode for unattended turns.

## 5. Agent

- [ ] pi-durable 1.4.x in the owner's object on its own mechanisms (hook names,
  transcript store, which is the plan's append-only session event log, one
  `ExecutionEnv`, `replay: "unsafe"` by default, compaction, steering, subagents,
  `sleepUntil`, Agent Skills, deferred MCP tools through `tool_search` and code mode,
  `pi.agent` settings), with ADR-032's kept differences; packages. Owed by 3.5: owner
  policy feeds pi's settings, and the per-agent wake limits move into the policy
  document then; an agent run, launch or extension call made for an outside client
  stamps that client's authority and is rechecked with `Grants.standing` before each
  step; the `WorkRunner` the `work` lane calls marks a scheduled turn, hands it to pi's
  durable submit with `operationId` set to the row's `work_id` (so a second hand-off is
  pi's duplicate, deduped) and returns (its status is pi's receipts, found by
  `work_id`), and the door that schedules
  one stamps the caller's authority; a policy write ends the owner's in-flight turns and
  streams, and a grant change ends that client's (the Python's stream cancellation and
  `_end_outside_streams`). PiHarness installs with `lifecycle.use` beside the schedule.
  Every owner write goes through the SQLite queue pi's commits use (pi-durable README,
  "Storage"), so no owner write lands inside a pi transaction.
- [x] A0 pi in the object, on faux models, in workerd, nothing deployed (`agent.ts`,
  `gate.ts`, `notices.ts`, migration 5). PiHarness sits on the object's own Lifecycle
  beside the schedule, no handlers installed, `alarm` the one forwarded; pi opens in
  Lifecycle's startup (the first alarm or PiHarness call), and no door starts it first.
  One registry per object: pi's `CodingTools`, `read` wrapped `replay: "safe"`, then the
  object's further extensions, one pi refuses skipped and kept as an owner notice, so the
  open never fails for owner content. The environment is `OwnerFilesEnv` per call, its
  history writer the conversation's `ta.authority` stamp (`client:<id>`, else `agent`),
  which `conversationCreated` copies into a task's new conversation (a fork keeps it).
  Settings: progress at most once a second, `afterFailedRun: "withdraw"`, `restartLimit`
  5. `onReport` is an owner notice (`noticeList`, owner only, the newest 100). Models: an
  empty catalog, so turns settle `no_model`, until A1's gateway provider; the default is
  its `auto`. `calls.ts` is gone: no budget of ours, `limits.subrequests: 10000` pinned by
  the config guard. The write gate: one `DurableObjectSqliteDatabase` over the storage,
  sharing pi's queue; a unit waits for the queue (`close()`) and runs as a
  `transactionSync`, never a `storage.transaction()` of its own, whose input-gate hold
  deadlocked against Lifecycle's startup awaiting pi's open (the object reset after 30 s;
  the gate test reproduces it); a door's synchronous body is one unit (`#door`), a door that
  crosses to `FILES` writes its later steps as units, the lanes and the environment a
  unit per write, Lifecycle's job sets inside one; the modules see the storage through the
  gate's view, which refuses a write, or a second statement after a read, outside a unit.
  A unit reads what it decides on or re-checks it in its statement (review M1: the chunk
  collector's due list is read in the unit that marks it, so an upload's begin landing
  between can no longer lose a committed file's bytes); a whole write re-checks its
  caller's authority in each unit it writes; a refused extension is noticed once while
  kept. Guards: write-gate (doors begin in the gate, `ctx.storage` only makes it, no second
  adapter, no async storage transaction, an awaiting function's writes in units, and no
  raw SQL read outside a unit used inside one), `agents/harness/pi` allowed, the pi table ban kept, pi pin (schema version 2;
  pi-durable, pi-ai and chord 1.4.0 by integrity). Found in workerd: pi's commit runs its
  SQL transaction in a turn of its own and awaits only its own statements, so no
  continuation woken by I/O, a timer or a storage read lands inside one today; the gate
  holds to pi's documented contract regardless, and the rollback test shows a direct write
  rolled back with a transaction of the shared queue while a door's is kept. Owner bundle
  1,495 KiB, 303 KiB gzip (311 and 74 before; no provider SDK), local startup 20 ms active
  in a 50 ms window (16 in 28 before) of the 1 s limit.
- [x] A1 The gateway provider, in workerd, nothing deployed (`gateway-provider.ts`,
  `auto.ts`, `model-ranking.json`, `live-tee.ts`). One pi-ai `Provider`, `gateway`, written
  directly: `getModels` reads the owner's active connections (`<connection>/<model>`, each
  with the metadata the gateway's setup check gave) and, with any, `auto`; `streamSimple`
  is pi-ai's own pi-messages client, its `fetch` the `GATEWAY` service binding, inside the
  wrapper: the target, the turn, a refresh under the lease, reserve, dispatch, the
  envelope (owner, connection, version, blob, reservation, policy generation, deadline,
  as G1's `envelope.ts` reads it), the upstream's own model id, the reservation's output
  cap, settle before pi sees the end, a reseal stored, and a row abandoned when its own
  unit throws. The turn is the submitted input at the origin of the call's causal chain
  (`origin.ts`): the harness's telemetry adapter carries an origin, and nothing else,
  along pi's own propagation, durable in pi's records (an input's admission takes its
  caller's origin, else begins one; a task takes its creator's carrier; a request carries
  its phase's context). So a run's tool rounds, steers, compactions, subagents and the
  background work it starts, with that work's own child tasks and conversations, are the
  run's turn, after the run ended too, and count against its caps (the Python's
  `_borrowed`); a later input's run is another; a compaction asked for outside a run is
  its own; a call with no origin is `no_run`. Production's agent is it
  (`gatewayAgent`, the default `AgentFactory`), so nothing connected settles `no_model`.
  `auto`: ranked by the data file (provider and model globs, newer ids first, the rest by
  catalog output price), its metadata the strongest's; stickiness and fallback read from
  the request's own transcript (a continuation stays on the model that answered, a retry
  after a failure before any output goes to the next-ranked), so nothing is kept in
  memory. `identity`: TinyAssets' attribution. `settings()` spreads owner policy's part
  (`agentPolicy`: the model retry count) beneath the fixed ones, re-read when the policy
  generation moves. The live tee: every event, without pi-ai's running partial, to the
  subscribers of the request's `sessionId`, in memory on the instance, bounded, a throwing
  listener dropped. Tested against G1's gateway Worker (bundled by wrangler as a deploy
  bundles it, run beside the tests with its fake upstream): a real turn; `no_model`; the
  tee's 12 deltas while pi committed at most 3 partials; an abort reaching the upstream;
  a budget refusal as the turn's error, never sent; a revoke racing a call; a 429 retried
  by pi at no cost, and each upstream status retried or not as pi-ai reads its code; the
  turn across tool rounds, a steer, a compaction the run starts (and one an owner asks
  for), two levels of subagents, background work and its child task's work after the run
  and beside a later one, and an input from outside any run; background work refused by
  its spawning run's request cap while a later run is not charged; a
  dispatch or settle that throws. Declared
  differences from the research: stickiness reads the transcript rather than an in-memory
  map rebuilt from it; a continuation kept on an earlier target is estimated by its own
  metadata, but pi's compaction threshold reads `auto`'s (the strongest's) until the next
  user message; pi's abort reaches the gateway through the service binding's own
  cancellation, with no wrapper of ours; a fork with no owner is its own root (it runs
  its own submissions), so only owner edges are walked. Owner bundle 1,598 KiB, 329 KiB
  gzip (1,495 and 303 before A1), local startup 24 ms active in a 56 ms window (20 in 50
  before). Open: a package that commits outside the context pi hands it starts causal
  chains of its own, which only the owner's daily and monthly ceilings bound; the
  attribution reaches the gateway hop only (pi-messages carries no identity upstream); an
  owner pin of a model per agent (`pi.agent`) for `auto`; a package that keeps the
  ranking current; the tee's transport to the owner's app (A6).
- [ ] User hooks in a separately deployed Worker's Durable Object, which loads each
  owner's hook code with the Worker Loader, called over RPC with JSON in and out and a
  timeout. A `beforeTool` timeout or error blocks
  the call; `api.memo` holds decisions that must survive a crash.

## 6. Files and sandbox

- [x] 6.1 The trusted file plane, in workerd, nothing deployed: the manifest in the
  owner's object (migration 4: paths, versions and history, chunk references, upload
  intents, quotas, the change feed, the sync lease with fencing and conflict copies;
  `files.ts`, `paths.ts`), the `file*` RPC doors admitted through the binding, file
  housekeeping on the Lifecycle `files` lane of the object's one schedule (every changing
  door syncs it; `OwnerFilesEnv` requires it as `rearm`; also re-marking history in
  passes of 500 paths after a retention change), the files Worker (`platform/files`:
  the only R2 binding, verified create-once chunk puts, gets and deletes of at most two
  chunks' bytes a call, keys built by `keys.ts` alone), and the owner's one `FILES`
  service binding, its Worker held to one `r2_buckets` entry by the config guard.
  Quotas and retention are owner policy (the document's optional `files` table over
  `FILE_DEFAULTS`, refused unless `max_entries` times the larger of `keep_versions` and
  `keep_recent` fits `max_history`); a change that only frees is never refused by a quota; the owner alone
  purges a path's history (`fileHistoryPurge`); `/.ta/tmp` keeps no history. An upload
  declares its size and its 4 MiB plan, a chunk is stored only once `FILES` accepted
  it, content ids carry their kind and size, and a sync commit answers each op. Declared
  differences from the research: a version is named by the owner-wide seq (a dense
  per-path counter would repeat once a deleted path's history ages out); a content's
  refs count every entry and every kept history row naming it, in every root (a row or
  two more per write: 22 rows for a new one-chunk file); a whole-file SHA-256 is not
  stored (the content id is checked from the chunk hashes R2
  verified).
- [x] 6.2 The pi `ExecutionEnv` over the manifest (`files-env.ts`), passing pi-durable
  1.4.0's conformance suite but for `exec` (20 cases run, the 6 `exec` cases excluded),
  symlinks off, with a write to a path it read failing `changed since read` when
  another writer committed there since; bytes come two chunks a `FILES` call, a
  whole-file read or write moves at most 16 MiB (larger files by range, line or
  upload), and `readTextFile` keeps a BOM. pi's `edit` shows only an error's code, so a
  refused size is `not_supported`; its reason is in the message, which `read` shows.
  The calls are the platform's to count (subrequests per invocation, charged to the
  object's newest event); its own error is the loud failure, and the object keeps no
  budget (5, A0, which deleted slice 6's `calls.ts`). `Files` and `Schedule` take the
  object's write gate itself, so the object's wiring is tested through the object. A
  change at the history quota first prunes that many of the earliest-due rows of gone
  paths (`released`), raising the feed's floor as housekeeping does, so once housekeeping
  has applied the retention in force, and while the entries quota holds, the quota never
  refuses a change; a live path's rows are never taken early. The `wakes` lane bounds each
  send and revoke itself (`WAKE_SEND_MS`), a late send marked and left to its lease; the
  `files` lane begins no chunk delete with less than `DELETE_FLOOR_MS` left; the lane table
  is `ownerLanes`, its wiring tested with recording passes, the doorbell and runner read
  through getters.
- [ ] Doors (with tasks 2): the HTTP `/api/files` door and MCP file tools over the
  `file*` RPC doors, the scoped-grant capabilities `read_files` and `write_files` named
  as those tools are, and sandbox lease tokens in the owner directory (a 256-bit bearer
  bound to owner, lease id and expiry, resolved by the binder as the owner).
- [ ] The sandbox: the E2B sandbox through our proxy; the in-VM sync agent (sync in from
  the change feed, sync out as one fenced `fileSyncCommit`, a 20 s lease heartbeat and
  15 s checkpoints, `/proc` open-file deferral and SQLite file groups, the cold-create
  size cap); `exec` in the environment (wake, sync in, run, sync out, report conflicts
  and a fenced sync-out in its result); symbolic links in the manifest and the
  conformance suite's `exec` and symlink cases; and the sync-ignore defaults, which are
  the owner's file `/workspace/.ta/sync-ignore` (`node_modules`, `.venv`, `__pycache__`,
  `.cache`, build output), shown to the founder first since they decide what survives a
  lost sandbox.
- [x] Probe, before the agent (5) runs a turn on the object: the subrequests-per-
  invocation cap (`FILES`, the gateway, hooks) and the 15-minute alarm wall time,
  including whether the cap applies per Durable Object event, before the gateway slice.
  The runs measured bursts of service calls per request, DO RPC and alarm, not a pi
  turn. Run 1 answered the shape (below), and the agent keeps no budget of its own
  (5, A0).
  Method, cases M1–M12, reading key and results table: `cap-probe.md`; run by the manual
  `cap-probe.yml` (gateway G0), which has run live (results in `cap-probe.md`).
- [x] Done (`cap-probe.md`). Run B on Paid (40741034491): no cap within 60 calls per
  request or DO RPC, or 120 in one alarm, and private addresses refused. Run A is not
  needed: `calls.ts` is deleted by agent A0 (`platform-agent-a0`).
  Run 1 on Workers Free (run 40701631460) found this: the cap
  that bit was 50 subrequests per invocation, not the documented 32. A DO RPC and an
  alarm each start fresh, and calls are charged to the object's current event (its newest
  invocation, or a counter each event resets; one rule here: work in flight
  spends the newest event's count), not to the async context that made them. An alarm is
  cut at 15 minutes.
- [ ] On the owner Worker's first deploy: check that Paid accepts its explicit
  `limits.subrequests: 10000` (run B ran at the default limit and never set it).
- [ ] Before cutover: per-owner SSE-C data keys on every chunk get and put, after a live
  probe of keyless `head`/`get` and of `ssecKey` with `onlyIf` and `sha256` (miniflare
  does not emulate it); a live probe of the files Worker's create-once put, which
  miniflare passed but the live bucket must also: `onlyIf: { etagDoesNotMatch: "*" }`
  as a wildcard, and which R2 checks first when a key exists and the bytes miss the
  `sha256` (the precondition or the digest), since the Worker's recheck of an existing
  key reads either answer; the manifest tables in the nightly export; each droplet volume
  imported into the manifest and bucket in the cutover window (9).

## 7. Wakes and outside agents

- [ ] Alarms, doorbells for ChatGPT and Claude, the board with the W8 decisions.
- [ ] The doorbell: a `WakeSender` the object constructs and its alarm calls in process
  (ChatGPT MCP Events, then Claude `/fire`), its construction tested through the object,
  since the lanes read it through a getter whose capture no test can tell until then; it
  consumes the schedule's open wakes and
  revokes, and a send finishes inside the 60 s claim lease or its outcome is `unknown`.
  The lane bounds each send and revoke itself at `WAKE_SEND_MS`, `LANE_HUNG_S -
  LANE_PASS_S` (15 s), whatever the sender does (a late send is marked and left to its
  lease, never sent again; a late revoke is due again after it), and the
  pass takes no new row past `LANE_PASS_S`, so a pass ends inside its lane's hung
  timeout whatever the batch.
  The subscribe door tags each callback with the generation `mailboxSubscriber`
  returns, and a revoke erases only callbacks at or before its generation. The door
  stores the callback in the same object transaction as `mailboxSubscriber`, or the
  store re-checks, as it writes, that the generation is past the agent's
  `revoked_through` and that the agent is still enabled, so a revoke scheduled between
  the two can never be outlived by the callback. The doorbell rechecks the client's
  grant (`Grants.live`) immediately before it sends, and keeps each `wakes` lane pass
  short: Lifecycle's drive loop awaits a pass inline, so a slow one delays every other
  job on the object, PiHarness's wake job among them; a send already in flight when a
  revoke lands is not stopped, and counts as `unknown`, never replayed, only if it
  fails.
  Deferred from 3.4, as the Python's tick had them: no wake is claimed while the outside
  kill switch (`TINYASSETS_OUTSIDE_DENY`) is off or the owner is not admitted
  (`require_work`). The kill switch reaches the alarm through a `DOORBELL` service
  binding on the owner Worker, to a `tinyassets-doorbell` Worker that binds only
  `DIRECTORY` and reads its `c:control` record; the `wakes` lane asks `DOORBELL.open()`
  once a pass before it claims and claims nothing on closed or a failure, and the owner
  config guard admits it as `{kv_namespaces: 1}`. Admission is the object's own row (2).

## 8. Founder live test

- [ ] The founder's real account, end to end. Fixes land in the new stack only.
  The canary follows.

## 9. Cutover window

- [ ] Full backup; each owner's broker stores imported into their object and each
  volume into object storage; DNS switched; the droplet paths deleted. S36 reads
  converged.

## 10. The plan's later slices

- [ ] Rooms and status (S4), app manifest and agent players (S6), device runner
  (S7), real-time tiers (S8), Claude and Grok wakes (S9), Sign in with ChatGPT
  (S10), rebuilt on the new architecture.
