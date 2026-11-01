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
- [ ] An owner's `grantSet` to `revoked` (the object's set door) revokes no OAuth provider
  grant: only the doors' `grantRevoke` path (`revoke.ts`) kills the client's refresh
  tokens. Route a set to `revoked` through the same provider revoke (rename review N1).
- [ ] Live AuthKit wiring: an `Upstream` (`doors/src/upstream.ts`) for AuthKit's
  production environment, with its redirect URI `https://tinyassets.io/oauth/upstream/callback`,
  the code exchange and the ID token's signature, issuer, audience and `auth_time`
  against its JWKS; `OWNER_ISSUER` set to AuthKit's issuer (the config guard pins it
  unset until then); production Google and GitHub apps (founder dashboard work). Until
  then the deployed entry refuses every sign-in loudly (`UNWIRED`). No person registers
  WorkOS settings by hand: the doors deploy copies `WORKOS_API_KEY` from the production
  host's `/etc/tinyassets/env` into the doors Worker's secrets (the code exchange needs
  it) and registers the redirect URI with the WorkOS API idempotently (`GET
  /user_management/redirect_uris`, then `POST` only if it is missing, never deleting).
  Owner creation by explicit platform admit at sign-up (the binder can't tell sign-up from
  sign-in today); until then a fresh object is created admitted on first sign-in, and
  erasure ends in `deleteAll` plus the `erased` row.
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
- [x] Owner admission and erasure, in the owner's object: an `admission` row
  (`admitted | suspended{billing, abuse} | erased`), checked in `#bind` at every door
  and by every lane with `require_work` semantics, an erasure tombstone so a stale
  `getByName` never recreates an erased owner, and a new door `admissionSet` taken only
  from a reserved `platform` caller, with a guard keeping that name out of every source
  but `platform/admin`. Built with F1 (tasks 5); the erasure door stays slice 10's. This **reverses** the earlier wording here ("the binder refuses
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
- [x] The agent tools: `run_graph` and `converse` as tools and scope capabilities, with
  the agent's doors (5, A4): `converse`, `run_graph`, `run_status` and `run_cancel` in
  `tools.ts` (the first two destructive, not idempotent and open world, the agent acting
  on outside services; `run_status` a read; `run_cancel` destructive and idempotent), on
  `/mcp` and `/api/tools/*`; `converse` and `run_graph` in the object's `CAPABILITIES`;
  the tool guard holds open world to those two doors alone; the owner's own
  `/app/api/agent/{submit,run,status,cancel}`. Open: the MCP Tasks extension, which the
  SDK's `createMcpHandler` 2.3.1 does not serve (on 2026-07-28 it strips
  `capabilities.tasks` and `execution.taskSupport` and has no `tasks/*` handler), so a long
  `converse` answers `running` and is polled through `run_status`; when the SDK serves it, a
  task id is the operation id, `tasks/get` `agentStatus`, `tasks/cancel` `agentCancel`.
  MCP clients' tool-call timeouts against the 25 s default wait are a live probe.
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
  new client refused; an egress host the URL parser reads as an IP address is refused
  (G4 keeps it so: the gateway fetches strictly publicly and E2B filters CIDRs).
- [x] 3.6 The schedule: one table, run as jobs on the Agents SDK Lifecycle queue, which
  owns the single alarm and the thread host's wake jobs (one per agent thread, S3); 3.4's direct `setAlarm`/`deleteAlarm`
  move onto it. Built (`schedule.ts`): `OwnerDO` holds a Lifecycle (`agents/lifecycle`,
  agents pinned exactly, by lockfile integrity too: 0.31.0, 0.32.0 since A5), never the SDK's `Agent`,
  installs none of its runtime handlers, and its `alarm` only hands the runtime's alarm
  to it. Each kind of timed work is a lane with its own rows and one single-flight
  Lifecycle job, `owner-schedule/<lane>`, derived from them: `wakes` (3.4's wakes and
  revokes), `files` (6.1's housekeeping, with its retention re-apply), `connections` and
  `threads`. A scheduled launch is no lane's: it is a `ta.scheduled` pi task sleeping in
  its run thread, woken by that thread's own job (F1). Every changing door, and the object's wake,
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
  Lifecycle's handlers, and off Lifecycle's tables; there is no `pi_` table in the owner's
  `OwnerDO` database: pi's tables exist only in `ThreadStore` objects (S1-S3).
  Stamped work (owed by 3.5): a scheduled launch carries the outside authority it was
  made under (client, grant generation, sign-in time) as its run thread's stamp,
  rechecked with `Grants.standing` when made and in its delivery's own unit (F1); a
  grant change drops the client's threads, the sleepers with them, in its own
  transaction; a scheduled run holds its client's grant row as every thread does
  (`held`, slice 3's review N3). Slice
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
  tests (N4).
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
  turn's requests, tokens and cost, kept while pi records own the turn: migration 10),
  so the reservation rows are receipts only. A connection keeps up to 1,024 models
  (the largest built-in catalog, 386, found by a scan, connects). Owner policy's optional
  `models` table: per-turn requests, tokens and cost (µ$), owner-wide and per-source
  (by source record) daily and monthly cost, pi's retries.
  `connections.ts`: `connectionPrepare`, `connectionCommit`, `connectionRevoke`,
  `connectionList`, `flowBegin` (folded into `connectionPrepare` by G3), `flowFinish`,
  each the owner's in session, a refusal
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
  gate placement, the upstream codes and the gateway's first event: 123 killed, the 3 that
  live equivalent (the reseal's version-shape check of the gateway's own answer; the
  output cap the gateway clamps to anyway; a third in code G3 has since deleted). The
  doors' `GATEWAY` binding and the connect routes are G3's (done). The policy write that
  ends in-flight turns and closes their tee views is A2's (done). Open: the receipts' rows
  per call, measured live (G5).
- [x] G3 The connect doors (the doors Worker), in workerd through the real gateway Worker,
  nothing deployed. The doors bind `GATEWAY` to the gateway's new `ConnectGateway`
  entrypoint, which serves only `seal`, `exchange` and `verify` (no model call, no egress
  forwarding, no keyring); the config guard holds the binding and that class's methods,
  and only `connect.ts` names `GATEWAY` (the binder guard). Owner-surface routes, each in
  the live session with a closed body (owner from the token, connection and version from
  the object): `connections/key` (the key held to the gateway's own secret grammar,
  then prepare, `seal`, `verify`, commit), `connections/oauth` (prepare, which begins the
  flow in its own unit, the authorize URL and a per-flow `__Host-ta-connect-*` browser
  binding, Lax, as the provider's `beginUpstream` binds its own), `GET
  /oauth/connect/callback/<source>[/<state>]` (each source its own redirect URI, in its
  protocol's own form, with the issuer an RFC 9207 record declares; a top-level navigation
  with that binding; it reaches no object and hands the code, the state and the path it
  arrived on to the app page `/app/connected` in the fragment), `connections/oauth/finish`
  (the binding again, `flowFinish` in the beginning session, the path held to the flow's
  own redirect URI (the mix-up, RFC 9700 §4.4), the source re-checked, `exchange`,
  `verify`, commit), `connections/revoke` and `connections/list`; the connection doors
  join the tool guard's owner-only set. A connect failing before anything is issued
  revokes its pending row at once; a credential issued whose setup check then fails is
  committed `failed` (in `error`, revoked at the provider by the lane, or never stored
  with an owner notice to delete it there); each connection records at prepare whether
  its provider can be asked to revoke (`revocable`, from its record), so a key exchange's
  revoke claims none and tells the owner to delete the key at the provider. Source records:
  `platform/sources/*.json`, the nine Python records ported plus `chatgpt` (Sign in with
  ChatGPT, `unwired` until OpenAI's approval: its connect is refused `501` naming why),
  each checked whole by `doors/src/sources.ts`, whose gateway part is the gateway's own
  `source` check (one `SUPPORTED_APIS`); the sources guard holds every known provider to
  pi-ai's catalog base URL and API. Tests: a spy on the object stub's arguments, a scan of
  every table, the answers and the console for the key, the code and the tokens; an outside
  credential on every connect door; forged owner, connection and version; the callback's
  navigation, binding, state and refusal cases; replayed, expired, other-session, cancelled
  (`sessionCloseAll`, sign-out) flows; the mix-up, each source's own callback form and
  the issuer; a repeated response parameter in either order; a duplicate source id; a key
  outside the secret grammar; a setup check failing after an exchange (a gateway
  `deadline` answered `504`, its own status);
  a broken or unsupported record; `413`. From the security review (CHANGES, 54b29ce87):
  M1 the per-source redirect URIs, M2 the key grammar, L1 to L5 above, each of its probes
  now a test; round 2 (APPROVE, 1a40bf175): a repeated `state`, `code`, `iss` or `error`
  refused (RFC 6749 §3.1), a repeated source id refused by `catalogOf`, `deadline` `504`
  (8 mutants on those lines, all killed). Mutation: mine, 94 across the browser binding, the flow state, the
  plaintext path, the owner-only gating and the review's fixes, all killed; the
  reviewer's 54, 53 killed and the one that lives equivalent (record parameters set after
  the flow's, which the schema forbids from naming a flow parameter).
  Doors bundle 1,013 KiB, 212 KiB gzip (966 KiB and 200 KiB before);
  local startup 48 to 60 ms active over three runs, of the 1 s limit (45 to 47 before). Declared differences from the
  research: the callback cannot finish the flow itself, since a cross-site navigation
  carries neither the owner's token nor its Strict session cookie, so the app page posts
  the code back in session (the binding is checked at both steps); no `prompt` is forced
  on a provider's authorize URL (the sign-in's `prompt=login` exists for the session's
  fresh `auth_time`; a source record may set `prompt` in its `authorize_params`, which may
  never set a flow parameter); the gateway gains the `pkce_user_key_v1` exchange
  (`key_exchange` in the sealed source; G1 had RFC 6749 only), whose key is sealed as an
  API key on a connection the object holds as `oauth2` (its provider revoke is then "not
  revocable", with the owner's notice); a key-exchange source, which sends no `state`,
  carries the flow's state as the callback's last path segment (the Python's and pi-ai's
  own choice); Google AI Studio is ported to pi-ai's native `google` provider, not the
  Python's OpenAI-compatible endpoint (research §2.7); the four vendor-CLI records keep
  their install, sign-in, invocation and hosts for the sandbox and name no upstream (they
  never enter the gateway); the Python's model lists, cards and free-plan terms are not
  ported (pi-ai's catalog lists models); a record's OAuth client is public only (a
  confidential client's secret would need a secret binding); `flowFinish` also returns
  the connection's source record id. Owner changes: `connectionPrepare` begins the OAuth
  flow (the `flowBegin` door deleted), `connectionCommit` takes `failed`, migration 6's
  `connections` gains `revocable` and a provider revoke may be due on an `error` row.
- [ ] The owner app's connect page `/app/connected` (the website slice): read the code,
  the state and the redirect URI from the fragment and post them to
  `connections/oauth/finish`. `history.replaceState` clears the fragment before any other
  script runs; the page loads no third-party script; it is served with
  `Referrer-Policy: no-referrer` and a strict CSP.
- [ ] Each RFC 6749 source's provider registration of its own redirect URI,
  `https://tinyassets.io/oauth/connect/callback/<source-id>`, registered exactly, never as
  a prefix, and matched exactly by the provider (RFC 9700 §4.1); OpenRouter's key exchange
  takes any callback: `.../callback/openrouter_user_models_v1/<state>`; Sign in with
  ChatGPT's hosted client and registration once OpenAI approves (founder), which then
  drops its record's `unwired`.
- [ ] The key exchange's residual mix-up, before any uncurated source: a source on an
  authorization server the platform does not curate (an MCP server, or a source from an
  owner's package) must not share these connect flows while a key-exchange source such as
  OpenRouter (no `state`, no `iss`) can be connected; that source's own slice solves it
  (for example a separate flow family, or no key exchange beside it) before such sources
  are allowed.
- [ ] Append-only migrations from the first deploy: once a migration has run on a live
  object it is never edited (G3 edited migration 6 in place only because nothing is
  deployed), held by a guard that pins each deployed migration's text by digest.
- [ ] Mobile connects and sign-in (the app slice): on mobile the OAuth runs in an external
  browser (RFC 8252), whose cookies are not the WebView's, so the Lax connect binding (and
  the provider's own upstream binding for the owner's sign-in) does not come back with
  the callback; the app slice decides how (a claimed https redirect into the app, or the
  binding carried another way).
- [x] G4 (slice 8a) The egress door, the sandbox's identity, its spend and its network
  document, in workerd, nothing deployed and no E2B call; 8b (the sandbox) only calls it.
  The door is the main path for API-key model use: a provider CLI in the sandbox (or pi,
  one optional CLI there) reaches its provider through it with a placeholder key.
  **Identity**: E2B workload identity is a private beta, so v1's is our own. The owner's
  object mints two tokens per sandbox lease, each `<owner>.<secret>.<mac>`, base64url: the
  owner id as the binder already names owners (`getByName`; AuthKit's opaque `user_…` id),
  256 random bits, and HMAC-SHA-256 under the routing key over the token's kind, owner and
  secret (`doors/src/routing-mac.ts`, the one module both Workers use). The routing key,
  `ROUTING_MAC_KEY`, is a Worker secret of the owner Worker (which mints) and the doors
  Worker (which verifies), 32 random bytes generated at deploy, declared in both configs
  (`secrets.required`) and held by both config guards; a Worker without a usable one fails
  loudly. Only each secret's SHA-256 is kept, on the lease row. The **egress token** is for
  E2B's static transform on `egress.tinyassets.io` (`x-ta-sandbox`) alone, never the VM;
  the **sync token** is the in-VM sync agent's, and works only on the sync doors
  (`fileChanges`, `fileSyncCommit`) for its lease and epoch; the kind is in the MAC, so
  neither works on the other's doors. The door verifies the MAC before any stub call, so a
  forged, altered or other-kind token reaches no object; then routes by the token's own
  owner part (no directory entry, no KV on the path), and the object checks the hash, the
  lease and its expiry, strongly consistent. **No writes for strangers**: the object
  checks a sandbox binding is the binder's exact shape before it writes anything, and
  refuses one for an owner whose object never opened a lease (`NO_SANDBOX`), writing no row.
  Workload identity, once E2B grants it (founder, gateway research §13 item 2), swaps the
  door's identity check only. **One lease**: the sync lease (`files.ts`; its `lease_id` the
  claim token, its epoch the fencing number, for sync commits and egress alike), which
  migration 14 binds to the two hashes and the egress generation its network was applied
  under. The lease API is owner-internal (`SandboxLeases`: open, renew, close, network
  applied, network, stale, config); no door reaches it, and 8b's `exec`, which runs in the
  owner's object, gets it with the agent's parts when 8b wires `exec` (the tests take it
  through `Internals.sandbox`). **The door** (`doors/src/egress.ts`, on
  `egress.tinyassets.io` only): the token verified, the maintenance gate, then
  `OwnerDO.egressOpen` (`egress.ts`, `work`): the lease by the egress hash (checked first,
  so only a live lease's call refreshes OAuth), the transform's and the lease's egress
  generations, the connection, its sealed endpoints by the gateway's own `egressTarget`;
  a read (`GET`, `HEAD`: a model list) is free and goes unmetered, and any other call must
  be pi-ai's metered model call for its API (`meteredCall`) with a declared body length;
  64 open and 600 a minute; then **the charge**. Each refusal is a fixed code kept as a
  receipt, at most 60 a minute, in a bucket apart from the admitted calls'. **The charge**
  (`egress.ts`'s own, in the open's unit, so two calls at once never pass a cap between
  them): the call's row in `egress_reservations` holds it (`held` to settled once:
  `succeeded`, `failed` or `unknown`), and the schema's triggers alone count it, into
  `egress_runs`, its **sandbox run**'s requests, tokens and cost (the run is the lease and
  its epoch: a new lease is a new run; the model policy's per-turn limits are a run's caps,
  rules `run_requests`, `run_tokens`, `run_cost`), and into `model_spend`, its source's day
  and month, which the owner's and the source's daily and monthly ceilings read and which
  the loop's calls move too; and the spend table's `ask_above` caps the one call (a sandbox
  call has no one to ask mid-request, so past it is refused, rule `ask_above`). Nothing of
  it reads or imports pi's engine (a policy-boundary guard holds its runtime imports free
  of `usage.ts`, the thread host and pi-durable), so it outlives the in-object loop.
  **The reservation rule**: priced at the dearest rate any of the connection's models
  charges (input at the dearest of uncached, cache-read and cache-write), by pi-ai's
  `calculateCost` over the gateway's catalog records; the input bounded by the body's
  length (a token is at least a byte), and the output by the dearest model's cap and what
  the caps leave, which the gateway clamps the call's own cap to, so the charge bounds
  what the call can cost; refused below 1,024 output tokens. An endpoint pi-ai cannot meter
  is refused, since a run's caps are always set (the stricter of the two rules). Then the
  gateway's `EgressGateway.forward`, **metered** (`gateway/src/meter.ts`): pi-ai's adapter
  for the sealed API, run once against a fetch of ours (no network, the credential's own
  secret handed in as the loop hands it), says which method and path its model call is,
  where the model id goes and which body field caps the output; the gateway reads the
  request's model there, finds the catalog's record, clamps the output cap, refuses a body
  past the reserved length, forwards, and tees the answer through the same adapter (pi-ai's
  own parser) to read its usage, priced by the catalog record; the outcome comes back on a
  second stream. With no metering (a read) it forwards `GET` and `HEAD` alone
  (`forwardRead`). The answer streams through as the provider writes it (SSE unbuffered,
  but the redaction's hold-back of a secret's length less one). Then `egressSettle` once on
  every end, through a fresh stub with one retry, registered with `waitUntil` at open: the
  door's outcome and bytes, and the charge `succeeded` at pi-ai's cost, `failed` (nothing
  sent, or the provider refused before any output) or `unknown` (a cut, an abort, an
  answer pi-ai cannot read), which keeps the whole charge; one never settled is `lost`
  past six minutes, its charge `unknown`. Every stop (the object's end signal, the
  deadline, the client leaving) aborts the provider's request through a stop stream the
  gateway watches. **Ends**: a policy write, a suspension, the connection's revoke, the
  lease's close or a successor lease, and the erasure end the streams they cover (research
  §6 let them finish within five minutes: a declared difference). The object stays
  resident while it holds a stream's end signal, billed duration for it. **Owed by 3.5,
  done**: each call is admitted at open against the current **egress generation**, which
  moves only when the policy's egress table does (the grants' revision/authority split), so
  a write that changes egress leaves the network stale, egress refused, fail-closed, until
  8b applies it again (`SandboxLeases.stale` tells 8b's lane), and no other policy change
  stalls egress; the gateway fetches strictly publicly and the object refuses IP literals
  (`network.ts`'s `checkDestination`, the policy's own rule). **The network document**
  (`network.ts`, pure): E2B's `allowOut`, `denyOut`, `dnsServers` and `rules` (e2b SDK
  2.62.0 `SandboxNetworkOpts`/`SandboxNetworkUpdate`/`SandboxNetworkInfo`;
  docs.e2b.dev/network/internet-access): deny all, allow `*` in `open` mode or the
  policy's names on 80 and 443, always `egress.tinyassets.io`, DNS pinned to 1.1.1.1, the
  transform on the door alone; another port is refused (not expressible by name), and
  `ask` is an allowlist plus a refusal receipt at each apply. **Allowlist mode is not a
  data boundary in v1**: E2B allows all traffic to a DNS server, on every port, and its
  rules name no ports, so the resolver cannot be held to 53 (DNS tunnelling, DoH); the plan
  says so (`BOUNDARY_NOTE`, every network plan's `note`). **Placeholders** (`sandboxEnv`):
  `<PROVIDER>_BASE_URL`/`_API_KEY` for each provider's oldest connection (Claude Code's
  `ANTHROPIC_*`, Codex's and Qwen Code's `OPENAI_*`, `MISTRAL_API_KEY`), `TA_<CONNECTION>_*`
  for every one, and, as data, the CLIs the rule does not name: Gemini CLI's
  `GOOGLE_GEMINI_BASE_URL` (the door and the connection alone) and `GEMINI_API_KEY`; keys
  `ta_ph_<connection>_<random>`; `forward` strips every inbound credential, query keys of
  any case among them. Owner-bound guard 46.3% (4,627,732,900 bytes, about 372 MB under
  half), no trade needed. **The E2B project's API key is a platform secret** with the
  egress token's custody: E2B's sandbox info returns static transform headers. **Owed to
  8b's live probe**: that E2B filters CIDRs so private addresses (127.0.0.1, 10.0.0.1,
  169.254.169.254) are unreachable; whether code in the sandbox can read its own transform
  header; whether E2B's proxy honours the VM's `/etc/hosts` or VM DNS for a transformed host
  (a redirect would carry the egress token to the VM's choice), and SNI-fronting under
  allowlist; that the project's API key can read transforms (above); that the sandbox
  resolves only through the pinned resolver (its `/etc/resolv.conf`); whether the VM has
  IPv6 (`denyOut` holds IPv4's all-traffic only); that a request in flight across
  `updateNetwork` carries the old generation; and each CLI's base-URL variables (the
  subscription research names only Grok's) against the CLI itself. **Out of v1**, each
  waiting for a TLS-terminating proxy (a founder call: a host, against Direction 8) or a
  tool's base-URL support: non-model credentials (GitHub, Notion and the rest), git through
  credentials (git is the file plane's and the sync agent's), WebSocket APIs, and SigV4; a
  model API's other billed calls pi-ai does not make (`count_tokens`, `countTokens`,
  embeddings), refused `egress_unmetered` until a reviewed record marks one unbilled; and
  a non-streaming model call's answer, which pi-ai's stream parser cannot read, so it
  settles `unknown` (its whole charge kept). No API-key source record exists yet for
  Anthropic, OpenAI or xAI (Claude Code, Codex and Grok Build by key): each is a record
  file in `platform/sources/`, its endpoints sealed as above.
- [ ] G5 Measurement and cost: pi-durable's partial commits at most once a second with
  the live tee, per-owner busy seconds, rows written, call durations and delta counts, and
  the rule for building the deferred mode for unattended turns; measured through pi's and
  pi-ai's metrics on the origin adapter and pi's `pi.usage` (`usageMetrics`, 0b).

## 5. Agent

- [ ] pi-durable 1.4.x in the owner's object on its own mechanisms (hook names,
  transcript store, which is the plan's append-only session event log, one
  `ExecutionEnv`, `replay: "unsafe"` by default, compaction, steering, subagents,
  `sleepUntil`, Agent Skills, MCP tools and code mode reached through `ta` (A3),
  `pi.agent` settings), with ADR-032's kept differences; packages. Owed by 3.5: owner
  policy feeds pi's settings, and the per-agent wake limits move into the policy
  document then; an agent run, launch or extension call made for an outside client
  stamps that client's authority and is rechecked with `Grants.standing` before each
  step; a scheduled turn is a `ta.scheduled` pi task whose delivery submits with
  `requestId` its `work_id` (so a second delivery is pi's duplicate, deduped; its status
  is its task's record and that submission's), and the door that schedules one stamps the
  caller's authority; a policy write ends the owner's in-flight turns and
  streams, and a grant change ends that client's (the Python's stream cancellation and
  `_end_outside_streams`). The object's `ThreadHost` installs with `lifecycle.use`; each
  thread is one pi Session over its own `ThreadStore`; owner writes are synchronous gate
  units. A scheduled turn is a `ta.scheduled` pi task sleeping in its run thread (F1).
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
  its phase's context). So a run's tool rounds and the subagents it starts, with their
  own conversations, are the run's turn while it is open, and count against its caps (the
  Python's `_borrowed`); work joining it after it ended is refused `turn_ended` (M3), and
  so are its compactions, which keep the run's turn while they are live; a steer, a later
  input's run and a compaction outside any run are each a turn of their own; a call with
  no origin is `no_run`. Production's agent is it
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
  user message (0b: the provider holds it to the target's own); pi's abort reaches the gateway through the service binding's own
  cancellation, with no wrapper of ours. Owner bundle 1,598 KiB, 329 KiB
  gzip (1,495 and 303 before A1), local startup 24 ms active in a 56 ms window (20 in 50
  before). Out of scope now: an input submitted under no origin (a door's, an enqueued
  one, or code that ignores the context pi passes it) begins a turn, and a task created
  with a forged carrier takes its origin; only platform code can do either, since the
  object runs no package executable code (A5), hooks run remotely with no submit or task
  path (H0, H1) and tools have none of their own, so a future in-object package runtime
  would need an owner-edge check. Open: the
  attribution reaches the gateway hop only (pi-messages carries no identity upstream); an
  owner pin of a model per agent (`pi.agent`) for `auto`; a package that keeps the
  ranking current; the tee's transport to the owner's app (A6).
- [x] A2 Authority, ending work and the work runner, in workerd, nothing deployed
  (`authority.ts`, `threads.ts`, migration 7). The invariant: an outside client's
  authority is enforced in the environment pi builds for every tool call, a replay's
  included, whatever extensions are selected, and in each model call's reservation and
  dispatch units; `beforeTool` is the early gate, which refuses before a tool begins.
  Each conversation carries its authority in pi's `ta.authority` document: the owner's
  (`{by: "owner"}`, the root's and an unstamped thread's, explicit), a client's stamp
  (client, generation, sign-in, universe, agent), or none, which does nothing (a
  conversation the object did not make). The environment holds each operation to it
  (`permission_denied`): a read, each read of an opened reader too, needs `read_files`, a
  change `write_files`, again in the change's own unit, so a revoke that lands after a
  call began refuses its write, and `exec` is refused to a scoped grant; every check is
  `Grants.standing`, so a full grant also must still stand. `ta.authority`, installed
  first so its block wins, gates every call (`call.parent` too): `read` → `read_files`,
  `write` → `write_files`, `edit` → both, `bash` refused when scoped, `ta` by its own
  map (empty until A3; the `ta:` names are reserved for its ops), any other tool refused;
  a refusal is a block with a fixed reason and a receipt (rule `outside_authority`).
  Packages named `ta.*` are refused at install. The runtime backstop: a client's or no
  one's conversation whose stored selection (pi's `AgentDoc`) drops `ta.authority` makes
  no model call (`outside_authority`), and its environment already holds its tools, so
  it does nothing. Behind it, a guard (`agent-authority.test.ts`, a tripwire) keeps out
  of the owner sources any `extensions` key (quoted or computed too) or write, any
  `configure` (called, destructured or imported), any `AgentDoc` but the backstop's
  reads, any literal-key access to those names, and every conversation maker
  (`createConversation`, `createRootConversation`, `fork`, `forkConversation`, a
  reference too) outside `OwnerAgent.thread`. A model call finds its conversation by its provider session
  (`ta.session`, written in `conversationCreated`); a revoked client's call is refused
  in its reservation (a `refused` receipt, no fetch) or not sent at dispatch, and a
  session the object never made is `no_conversation`, so the abort is the prompt stop,
  not the enforcement. `conversationCreated` is the one inheritance path: a task's
  conversation takes its owner conversation's authority, and a fork's copy that
  disagrees throws. A thread is made by `OwnerAgent.thread`: pi writes the authority in
  the creating commit, then `agent_threads` records it in a unit that re-checks it
  stands; a thread never changes its stamp (trigger), holds its client's grant row
  (`held`, so `forgetRevoked` keeps the row), is bounded (64 a client, 1,024 of all
  clients, 10,000 the owner's; refused loudly before pi makes a conversation, and again
  in the recording unit), so `agent_threads` and the grant rows its threads hold stay
  bounded however many clients register, is never the root (`conversation_id > 1`, by
  schema), and is dropped in the unit of its client's grant
  set or revoke, which returns the dropped threads. After that unit commits the door ends
  the client's work: its pending waits, the views of those threads and of every live
  conversation stamped for it, and an abort (`background: true`) of each; a policy write
  does the same for every conversation with live work and closes every view. Either door
  opens pi and waits at most `END_WAIT_MS` (5 s); should that fail the door rejects though
  its write is committed, and what the end missed is refused anyway. A scheduled turn's
  hand-off is F1's: its `ta.scheduled` task's delivery checks its thread's stamp again
  (and that it still stands), then submits with `requestId = work_id` and `whenBusy:
  "followUp"`, the entry `ta.work` naming the client. `OwnerAgent.status` reads `pending()`, then waits
  `STATUS_WAIT_MS`, answering pending only when that wait timed out and failing loudly
  otherwise; `OwnerAgent.wait` is the waits a change ends (A4's doors). Tested: the
  capability map and each refusal with its receipt; the environment refusing with
  `ta.authority` configured away, and a replay's read after an eviction and a revoke;
  a lapsed grant refused in each change's unit and at an opened reader; the revoke raced
  against a tool call and a thread's recording through the held queue; a revoke ending a
  client's turn, its subagent's, its views and waits while others run on, its door
  waiting for a tool slow to stop; a revoke missing the work in its abort, or committing
  between reservation and dispatch, spending nothing; a conversation selecting the gate
  away spending nothing (the owner's still spends); a fork's disagreeing copy; the
  thread bounds, no conversation for a refused thread, `held` and the drop; the root
  never a thread; the hand-off at its generation; status; the guard's evasions.
  Mutation: this tree's 113 (the capability map, the standing check, the environment,
  inheritance, sessions, the backstop, the abort targets, the bounds, the triggers, the
  hand-off, status and the guard's rules), all killed, each alive one killed by a new
  test; the reviewer's: of round 1's 68, 38 killed and 29 no longer matching the
  rewritten code (each has a counterpart here), of round 2's 53, 48 killed and 4 no
  longer matching (counterparts killed here); alive and equivalent: `liveConversations`'
  submissions, which add nothing on pi 1.4.0 while every open submission's conversation
  also has a live task, and `modelAuthority`'s no-session branch, whose absent session
  pi answers as no conversation anyway. Declared
  differences from the research: a stamp also names the universe and agent, and
  `agent_threads` a `universe` column, since a scoped grant is triples; `edit` needs both
  capabilities; abort targets are read from pi's live work and each conversation's
  authority, the rows only adding their threads' views; a thread goes with its grant's
  change rather than by idle retention; a write-only client resolves paths too (pi's
  mutation queue reads a path's canonical form), which tells it whether a path exists:
  accepted, since its changes tell it anyway (a `remove` of a missing path fails
  `not_found`, a non-recursive `createDir` of an existing one `exists`), and no content
  leaks. Owner bundle 1,628 KiB, 337 KiB gzip
  (1,598 and 329 at A1), local startup 25 ms active in a 59 ms window. Landed with A4
  (below): thread retention by idle age; and the agent's doors address work by
  `thread_id`, resolved against the calling client's own row, never by a pi session
  string. A dropped thread's pi records → S4 (drops wipe the thread's store): since S3,
  each thread is one pi Session in a `ThreadStore` of its own, so a drop closes its Session
  and S4 wipes the store whole (`deleteAll`); A4's storage gauge is gone (S3), replaced by
  the owner-bound guard. Writing pi's tables directly stays banned. A3's `ta` re-derives its
  capability in its own `execute` (below).
- [x] A3 `ta`, in workerd, nothing deployed (`ta.ts`). The model is offered exactly
  `read`, `write`, `edit`, `bash` and `ta`: pi's `modelTools`, written in
  `conversationCreated` on every conversation the object creates (the root, threads,
  forks, subagents) over whatever pi copied, and nowhere else (the agent-authority guard:
  that one write of `AgentDoc`, no other `modelTools`, no `addTools`, no tool result
  `control`); a tool the model is not offered settles "not available". Held where the
  model is reached too (review M1: pi lets an `afterTool` hook's result `control` add a
  model tool, and a `beforeRequest` hook can rewrite a request): the gateway provider's
  reservation unit refuses (`tool_surface`, nothing sent) a request whose own transcript
  offers anything but exactly the five, or none (`ta`'s nested model call, a compaction),
  and `modelAuthority` refuses any conversation whose stored `modelTools` is not exactly
  the five, the owner's included; one source for what is offered (review round 2): the
  provider sends the gateway exactly the checked messages, never a caller's `onPayload`,
  and the gateway refuses a context carrying `tools` or `systemPrompt`. A package that
  provides or wraps a model tool's, a
  platform capability's or a `ta:` name, or provides a tool not callable by tools (it
  could never run), is refused at install with a notice; the catalog fails loudly on a
  tool of the object's own taking a platform name; each package is checked and installed
  as one frozen copy, so a getter cannot bring a shadow in. Tests' probes install as
  `AgentSource.trusted`, which production cannot pass: `gatewayAgent` takes none, and the
  ta guard holds `trusted` to its declaration and `#install`. `ta` is
  `callers: ["model"]`, `replay: "unsafe"` (replay-safe by its own rule since A5); its input is one object, `op` an enum and each
  op's fields optional, its shape checked in `execute` (any other field refused). `catalog`
  lists the platform capabilities the conversation's authority may call, then the tools
  registered `callers` naming "tools" (pi's `agent.callable`, deferred ones marked), each
  with its JSON Schema, ranked by a query with a copy of pi's `tool_search` BM25 and search
  text (pi exports neither; the ta guard runs pi's `tool_search` beside it on 300
  generated catalogs), and paged (`limit` at most 100, `offset`, `next_offset`). `call`
  runs a platform capability in the object (its arguments validated by pi-ai's
  validator), or reaches a tool through `api.executeTool`, returning its
  `structuredOutput`, `diagnostics` and `isError`; any other name, the file tools, `ta`
  and the `ta:` names are refused loudly. Authority: `TA_CAPABILITIES` in `authority.ts`
  (`ta:catalog`, `wake:list`, `model`: a standing authority; `policy:show`: the owner's
  alone, a new `OWNER_ONLY` need, as `policyRead`; `policy:propose`: `propose_policy`, as
  `policyPropose`; `files:usage`: `read_files`, as `fileUsage`); `ta.authority`'s
  `beforeTool` is the early gate, and `execute` re-derives the need from the arguments it
  received and re-checks the stamp (a refusal receipt kept), so a later hook's rewrite or
  a revoke after the gate is refused; a nested call passes `ta.authority` with
  `call.parent`, so a client reaches nothing through `ta` it could not directly. Each
  capability is a read or an effect, an effect unless classified a read: a read writes
  nothing, an effect re-checks its authority in its own unit (`policy:propose`) or in its
  reservation and dispatch (`model`). `model` calls the object's model source on the
  conversation's provider session and the call's telemetry context: reserved, dispatched
  and settled like the run's own calls, on the run's turn, against its caps. `wake:list`
  reads the authority's own scheduled launches from the owner's rows (F1: each run's
  thread row and its live row's wake, never a store).
  `agent:run` is A4's. Tested:
  the five on the root, a thread, a fork and a subagent (and after a parent's tools
  drifted), a model-callable and a deferred package tool never offered; the catalog's
  order, schemas, ranking and pages, per authority; a platform read and effect; refusals;
  nested calls through the gate; a rewrite and a revoke caught in `execute`; a revoke
  before an effect's unit; `policy:propose` and `policy:show` for the owner and outside
  clients with receipts; `model` through the gateway Worker on its run's turn, and refused
  by its cap, a revoke mid nested stream cancelling the upstream, its system prompt and
  output cap reaching the gateway; a hook's widening and a request hook's injected tool
  refused at the provider, nothing sent; `wake:list` per principal (owner, scoped, full);
  a client's proposal on its own share, refused at its limit while the owner's holds; the
  package refusals; a call with a catalog field. The test tools are registered callable
  by tools, so the A0 to A2 tests reach them through `ta`. Mutation, after review: ours,
  63 (classification, the capability map, the catalog's `callers` filter and paging, the
  five-tool fix, nested authority, the re-check, the model call, the guard's rules, the
  ranking, the provider and backstop checks, the package refusals, round 2's one source,
  `trusted` and frozen packages), 72, 71 killed; the reviewer's round 2, 77: 74 killed,
  one no longer applying (`pkg-as-trusted`: `gatewayAgent` no longer takes `trusted`; our
  S05 is its counterpart, killed by the guard). Alive and equivalent: a read handed a writing unit, since no
  read capability writes; an effect's unit re-checking only a standing grant rather than
  its capability, since a grant's scopes change only with its generation, which the
  standing check holds. Declared differences from the
  research: the input is an object with an `op` enum, not a root union, since pi-ai's
  Anthropic adapter keeps only a root's `properties` and strict sampling refuses object
  unions; the catalog also takes an `offset`, to page; the catalog lists only what the
  conversation's authority may call; deferred tools are listed from `agent.callable`
  (pi's deferred tools a tool may call are always among them); `policy:propose` takes the
  door's `{change, request_id?}`; `model` is in v1 (G2 landed) and `agent:run` is not.
  Owner bundle 1,649 KiB, 342 KiB gzip (1,628 and 337 at A2), local startup 25 to 28 ms
  active in a 58 to 60 ms window. Open: MCP tools (`mcp__<server>__<tool>`, callable by tools, after
  G3) and code mode as a tool `ta` calls; the sandbox's `ta` CLI through the egress door;
  a nested model call's events reach the conversation's live view, since it shares the
  conversation's provider session (A6 decides how a view shows them).
- [x] A4 The agent's doors, in workerd, nothing deployed (`owner-do.ts`'s agent doors,
  `threads.ts`, migration 9; the doors' `tools.ts`, `app.ts`). Four object doors, each
  through the binder's `Binding`, each admitting its caller and checking its scope in its
  first unit, before any pi call. `agentSubmit({message, client_send_id, agent_id?,
  steer?, wait_ms?})`: scope `converse` for the agent named (`main` by default) in the
  universe of the client's one enabled agent; the caller's one `converse` thread with the
  agent (one per client, agent and universe, a unique index, so two first messages at once
  make one), found or made stamped and configured; an outside client's text wrapped in the
  untrusted envelope naming the client (the Python's `untrusted.py`), its entry
  `ta.outside`; submitted with pi's request id `sha256(client ∥ client_send_id)` (the
  owner's `client` the word `owner`), so a resend is pi's duplicate, `whenBusy` `steer` or
  `followUp`; then a bounded wait (25 s default, 60 s most), past which `running` with the
  operation id. `agentRun({agent, input, at?})`: scope `run_graph`; a fresh `run` thread;
  submitted now (entry `ta.run`), or with `at` a scheduled launch, its `ta.scheduled` pi
  task sleeping in the thread until due (F1). An operation id is the thread's id and pi's
  request id; `agentStatus` and `agentCancel` resolve it against the caller's own rows
  (`Threads.mine`), so another client's, the owner's for a client, a forged one and a pi
  session string are refused alike ("no such operation"); status is `OwnerAgent.status`
  (pending, then settled), or for a scheduled launch its task's record and its last
  delivery (`scheduled`, or how it was refused; F1); cancel aborts a scheduled launch's
  task (F1), else PiHarness's `abort` by operation (withdraw, or abort the run it joined). No door hands
  pi a session but the one its thread row names, so none reaches the root. A thread is
  configured from `/workspace/agents/<agent>/AGENTS.md` as pi's conversation
  `instructions`, in the creating commit (an agent id is a safe path segment; a file not
  text, over 64 KiB or not a file is refused loudly), and its recording unit re-checks the
  scope where it acts. Idle retention, the `threads` lane: a thread untouched for 7 days
  is dropped in a unit that re-checks the activity it read; a thread whose conversation pi
  holds live work in is kept, its activity moved on (a task that owns a conversation stays
  live, completing, while that conversation works: a thread's subagents and background
  work keep it, found in workerd); a thread whose scheduled launch sleeps is live (F1);
  every door that makes or uses a thread touches it and syncs the schedule.
  `ta`'s `agent:run` (an effect, `run_graph` in `TA_CAPABILITIES`): the same launch path,
  stamped with the calling conversation's authority as the agent it names, so its
  recording unit refuses an agent the grant holds no `run_graph` for, before pi makes a
  conversation. A3's review nits: a `tool_surface` refusal refreshes no credential, and a
  package pushing onto its own `tools` array after install changes nothing, each tested.
  Tested in workerd: the owner's thread and no root; a resend one submission and one model
  request; two first messages one thread; the envelope; the bounded wait `running`, then
  `done`; a steer after the tool round, a follow-up after the turn; invalid fields refused
  before pi; another client, the owner, forged ids and pi session strings refused, another
  client's work id in one's own thread no work of one's own, cancel once; scoped clients
  without the scope, or for another agent, refused before pi, stamped where they act when
  granted; a revoke ending a pending wait (refused) and the run, then refusing the client;
  `AGENTS.md` as instructions; a scheduled launch handed by the runner, a cancel of pending
  work; `ta`'s launch per scope and the owner's; retention dropping the idle, keeping the
  live, the scheduled and the touched, freeing a full bound, the lane's due time and its
  run through the object; the doors' tools on `/mcp` and `/api` per owner and per scope,
  and `/app/api/agent/*` for the owner, refusing an MCP token. Mutation: 46 (the scope
  checks, the operation id's derivation, the ownership check, the thread lookup, retention,
  the hints, `agent:run`'s classification, the steer, the wait, the envelope, the cancel,
  A3's two nits), 45 killed; the one alive (the recording unit's scope re-check) is killed
  since by a test of a grant row changed in place (review round 1). Review round 1 (one
  high, two medium, four low), fixed: the owner's agent definitions are the owner's alone,
  since a client's `AGENTS.md` had configured the owner's and other clients' threads (H1):
  `/workspace/agents/**` changes only under the owner's own authority, refused to an
  outside client at every file door (write, upload commit, mkdir, remove, rename both
  ways, restore) and by the agent's environment in each change's own unit (write, append,
  truncate, mkdir, rename both ways, remove), and a thread is configured only from an
  `AGENTS.md` whose recorded writer is the owner or its agent, any other refused loudly;
  a launch's `at` is at most a year and a day ahead (M1: one past 2189 had broken the
  alarm, and a far one pinned its thread), scheduling no longer moves a thread's activity,
  and the lane waits on no held thread; a storage gauge (M2): the database size against
  the 10 GB limit before every new thread and launch and in each lane pass, an owner
  notice past 70% (`storage_high`), and past 90% a notice and a loud refusal of new threads
  and launches (`storage_full`), named constants, since the owner policy document has no
  platform-limit section; at most 16 waits per client and 32 of the 256 kept for the owner
  (L1); a scheduled launch's work room checked before its thread (L2); `converse` says a
  retry after a week-idle drop runs again (L3); the reviewer's surviving mutants killed by
  tests (L4), and its probes are tests. Mutation after round 1: ours, 77 (the first 46, a
  retention mutant retargeted to the claim, and 31 on the fixes), all killed; the
  reviewer's, 26: 25 killed, and `agent-dir-dotdot` no longer applying (the `..` check it
  removed was redundant, since an agent id's pattern admits no bare `..` segment, and is
  gone). Declared differences from the research: a thread is configured through pi's own `createConversation` agent in
  `OwnerAgent.thread`, never `sessions.create()` and `configure` (the agent-authority guard
  keeps `configure` and every other conversation maker out); the data files are the
  agent's `AGENTS.md` alone, as its instructions; an operation id names its thread, so
  status and cancel need no table of their own; the owner's entry is pi's own user entry;
  a dropped thread's pi conversation is not deleted (A2's entry above). Owner bundle 1,678
  KiB, 350 KiB gzip (1,649 and 342 at A3), local startup 25 to 26 ms active in a 61 to 63 ms
  window (24 to 26 in 57 to 60 at A3); doors bundle 1,017 KiB, 213 KiB gzip (1,013 and
  212), local startup 43 ms active in a 66 ms window (40 to 45 in 67 to 69). Open: the
  Tasks extension (tasks 2); an agent's model pin and further data files; the sandbox's
  sync writer (`lease:<epoch>`) for the agent definitions, refused as a writer until the
  sandbox slice decides whose authority its sync carries. Superseded, with no further
  work on either (the plan doc, rev 70): the storage gauge, by the storage slice (S1 to
  S3: one pi Session per thread on a `ThreadStore`, wiped whole with its thread, our own
  `ThreadHost` in place of PiHarness), so the harder stop at 97% round 2's review suggested
  is moot as well, since nothing deploys before S3; and a scheduled launch's `work` path
  (the `work` row naming a thread, handed by the `WorkRunner`), by a scheduled turn as a
  pi task (F1). Both superseded by S3/F1: the gauge is deleted in S3, and a dropped
  thread's conversation → S4 (drops wipe the thread's store). Review round 2 (approved): the owner is told by a notice
  (`agent_definition`) each time its agent creates or changes anything under
  `/workspace/agents`, since content another party wrote elsewhere can steer it, and
  tests hold that a name merely starting like a root or the tree (`/workspaceX`,
  `/workspace/agentsX`) is neither, and that an `AGENTS.md` the sandbox's sync wrote
  (`lease:<epoch>`) configures nothing.
- [x] 0b The pi-alignment PR, in workerd, nothing deployed (pi audit F3, F6, and F4 items 1
  to 3 and 6, all done; F4's task origins namespaced by thread go with S3, `maxTurns` with
  the pi upgrade). **F3**: the gateway reads a model call's refusal with pi-ai's own
  `isContextOverflow` before the generic mapping (`modelRefusalCode`): any 413, and any
  refusal but a 401, 403 or 429 whose adapter message pi-ai reads as an overflow, is
  `context_length_exceeded` (status 413), the phrase pi's overflow detection matches
  (`overflow.js`, `/context[_ ]length[_ ]exceeded/i`), so pi compacts and retries once
  (`generation.js:405`); a stream the provider fails for its context after a 2xx ends with
  the same code, and so does a body past the gateway's 16 MiB bound (`too_large` is gone; a
  non-numeric length is `invalid_request`). The adapter's message is still never shown.
  **F6**: `verify` lists each model's whole pi-ai record (`model-record.ts`: id, name,
  reasoning, `thinkingLevelMap`, `promptCache`, input types, `inputLimits`, context window,
  output limit, cost with its `tiers`), without the gateway's own fields; a declared
  endpoint's models are the same records, checked whole within the owner's own bounds (ids
  and names of 200 characters, prices of $1M a million tokens, token counts of 100M).
  `ModelInfo` is that record, checked whole at commit; migration 6's `models` cap and
  `MODELS_JSON_MAX` are 640 KiB, so 1,024 of the largest built-in record (607 bytes) fit
  (`maxModels` stays 1,024; every chat model of every built-in catalog commits). `listed()`
  spreads the record, so pi offers `xhigh` and `max` where the map has them and `read`
  resizes to `inputLimits`. A reservation is priced by pi-ai's `calculateCost` over the
  target's record, at the rates its estimated input's tier sets, the most output its cost
  room takes found by bisection (`largestFitting`, about 27 prices at the largest bound); a
  settled call keeps the cost the gateway's pi-ai adapter reported (its current catalog,
  with pi-ai's service-tier and fallback adjustments), which `pi.usage` records too. pi's
  compaction thresholds are each listed model's own (`compactionFor`: pi's defaults from
  256K tokens, an eighth, a quarter and an eighth of a smaller window), keyed
  `gateway/<id>` and `gateway/auto` (the strongest's), through `AgentSource.compaction`; a
  request `auto` routes to a model whose threshold is lower than the strongest's is refused
  `context_length_exceeded` over that threshold before any reservation, and `auto` retries
  an overflow on the same model. **F4**: the origin adapter carries a metrics capability
  (`OriginMetrics`, on its root, every span and every extracted parent: each series' count,
  sum, minimum and maximum, at most 1,024 series, drops counted), read with pi's own
  `pi.usage` by the owner's `usageMetrics` door, for G5; the span names it reads are typed
  against pi-durable's exported `DURABLE_TELEMETRY_SCHEMA`, and checked at run time with the
  phase's attributes and the compaction task's name; no adapter method throws (each
  guarded, a throwing callback a rejection); ADR-032 §4 declares it a kept difference.
  Tested: each wire format's overflow refusal, a bare 413, a 429 that reads like an
  overflow, a mid-stream overflow, the body bound; through the real gateway in the owner
  pool, an overflow refusal from the fake upstream compacted and retried, the turn done and
  the refusal settled at no cost; `auto` continuing on a smaller target compacted at that
  target's threshold and retried there, refused one token past it and not at it, and not
  refused where pi's own threshold was already as low; thinking levels from the map; a
  long-context tier reserved at its rates and settled at the tier's price the gateway
  priced (a declared endpoint); a stale stored record settled at the gateway's price; the
  reservation's search bounded in prices and timed at the policy's largest turn cost; a
  40K model compacted at its own thresholds, not pi's; the turn's metrics and `pi.usage` in
  the usage view; the adapter fed hostile input of every kind; 1,024 of the largest record
  committed. Review round 1 (CHANGES): M1 the settled cost is the gateway's, not one
  recomputed from the stored record (stale after a pi-ai price change, and blind to pi-ai's
  service-tier and fallback adjustments); M2 the reservation's one-token rounding walk
  (27,743 steps at worst, inside `transactionSync`) became the bisection; L1 the gateway's
  declared-model check refuses unknown thinking levels and bad tier thresholds and holds the
  owner's bounds; L2 the 640 KiB cap; L3 unchanged (pi's own classifier, bounded). Mutation: ours, 96
  across the overflow classification and the 413, the record's fields, checks and bounds on
  both sides, the reservation's `calculateCost` and bisection, the gateway's settled price,
  `auto`'s overrides and the routed target's threshold, the metrics and the never-throw
  adapter: 93 killed, 3 alive and equivalent (the stream classifier's status branch, which
  the first-event check answers first; a tier's threshold made optional in either checker,
  which its count check still refuses); the reviewer's 58: 55 killed and 3 no longer
  applying (the one-token walk's two and the settlement recompute, all deleted; their
  counterparts here are killed).
  Owner bundle 1,689 KiB, 353 KiB gzip (1,679 and 350 at A4), local startup 23 ms active
  in a 63 to 65 ms window (25 to 27 in 60 to 62); gateway 3,993 KiB, 542 KiB gzip (3,984
  and 540), 32 to 35 ms in 86 to 89 ms (33 to 35 in 89 to 91). Declared differences from the research: the overflow code answers 413, not the 502 of
  the upstream codes; `compat`, `headers` and the sampling parameters stay the gateway's
  (its adapter applies them from its own record; pi-messages reads none); pi's settings
  cannot see a conversation's target, so `auto`'s per-target threshold is held by the
  provider through pi's overflow recovery, `gateway/auto`'s keep-recent count staying the
  strongest's (a target whose window cannot hold it fails its run loudly,
  `context_length_exceeded`); the gateway's own body bound is the overflow too;
  `AgentParts.provider` is the `GatewayProvider` and `agent.ts` and `owner-do.ts` carry the
  plumbing (`gatewaySource`, `usageMetrics`).
- [x] S1-S3 The agent's storage and host, in workerd, nothing deployed (`thread-store.ts`,
  `remote-storage.ts`, `thread-host.ts`, `agent.ts`, `threads.ts`, `origin.ts`, migration 9
  edited in place, `wrangler.jsonc`). Each agent thread is one pi Session over its own
  `ThreadStore`, a second SQLite class in the owner Worker bound as `PISTORE` (no
  `script_name`; the config guard pins it, `disable_ctx_exports` kept), named by a random
  128-bit `store` its row holds, unique and named before the store exists; pi's
  `SqliteStorage` under `pi_` in the store's own database, served over RPC, behind a holder
  fence that refuses a stale Session; `size()`; `destroy()` (`deleteAll`, then a mark that
  refuses every later call). The store reads no `env` binding (a guard). `RemoteStorage`
  mints ids locally from the store's next id, retries a read once over a fresh stub, never
  retries a commit (the Session fails and reopens), checks aborted contexts, and is shaped
  for pi main's `commit(writes, adopt)`; pi's own storage conformance suite passes through
  it over RPC. `ThreadHost` replaces PiHarness: our own multi-Session Lifecycle
  capability, one shared registry, `Map<thread, Session>`, a wake job `pi-wake:<thread>`
  per thread driven by that Session's `onWake`, each change kept in `agent_live(thread,
  active, wake_at)` in a gate unit; Sessions opened on demand, closed idle after 2 minutes,
  least recently used first to make room, and an open past `OPEN_MAX` (64, live probe P4)
  refused at once, never waited on, a pinned (watched) Session never closed. PiHarness's
  wake mechanics and recovery tests are ported (MIT), keyed per thread; `agents/harness/pi`
  is no longer imported. A thread is its Session's root, its stamp written in the root's
  creating commit; compaction origins are `task:<thread>:<id>`. Status is one read of pi's
  submission record (F12). `contextRetentionMs` and `documentRetentionMs` are 60 s for many
  Sessions an isolate (F11, live probe P4). Each open Session calls its store over one
  `RpcTarget` session, released when the host closes it (live probe P7). The write gate
  keeps synchronous units and no queue: pi no longer shares the owner's database, so its
  shared-queue rule and chord dependency are deleted (F13 moot). A4's storage gauge is
  deleted; the owner-bound guard holds the owner's worst-case rows, at the policy's high bounds, under 50% of 10 GB, and
  names the tables no byte or count bound covers yet (file paths, chunk references, turn
  counters). `limits.subrequests` is 1,000,000 (live probe P1). Open: drop wiping, run
  retention and erasure ordering (S4-S5); scheduled turns as pi tasks (F1); rotation
  (deferred); live probes P5 and P8 (design-decision §6). P1-P4 and P7 are done
  (`store-probe.md`, run 1).
  Review round 1 (CHANGES), all taken: M1 a task pi's restart limit parked held its run
  forever (input placed, job and live row gone, follow-ups queued); each open now looks
  for one before it resumes and ends the run it holds up (`#endParked`: its conversations
  aborted, background included), the input settling `unanswered`, a `run_parked` notice,
  status `reason: "parked"` meanwhile (PiHarness's `_parked`, ported; the reviewer's
  repro is a test). L1 an ending is never refused (`ThreadHost.end` opens past `OPEN_MAX`,
  aborts before it resumes, and closes what it opened), and `endAll` rejects when a thread
  cannot be ended, so the door does. L3 a read retry attaches a new session for the same
  holder, never a claim; the host releases the store's session however the Session
  closes; a pinned Session pins its store's billed duration. L4 the job before the live
  row. L5 a destroy cut short between wipe and mark cannot revive its store: the owner
  never opens a dropping thread (tested). L6 the store guard also refuses the `"env"`
  string, `Reflect` and `this` other than by a member's name. L7 tests for each named gap.
  L8 `#open` takes the lease before anything runs. L2 conversation records are kept once
  read: a warm message's store calls (submit, wait, status) fell from 34 to 26, pinned by
  a test; the host's own `submissionByRequest` in `submit` stays (pi's submit does not say
  whether an input was new, and a commit count would misread a duplicate). Also: every
  close cancels a stale wake job when nothing runs or sleeps, so a leftover heartbeat never
  reopens a Session, taking a place, to find nothing; the owner opens a thread's store
  while its registry installs. Mutation: ours, 53: 50 killed, 3 alive (`th-sleep-unarmed`
  and `th-admit-unarmed` as before; caching a not-found conversation, equivalent, since
  a kept `undefined` reads as not kept); the reviewer's 30 (its 23, two re-anchored to the
  session API, and 7 for this round): 25 killed, 5 alive and outside the round's list
  (`#idle`'s admitting and waiting checks, which `wake().active` covers in each test
  path; the close's wake change; the budget's re-wake, at a 10-minute budget; the open's
  `wakeFor`, which pi's first `onWake` repeats).
  Review round 2 (REQUEST CHANGES), all taken: M1 the usage view, merged from 0b, opened
  and resumed every thread's Session; it now never opens one: an open Session's
  `pi.usage` is read live, every other thread's from a usage row (`agent_usage`, at most
  64 KiB, kept in a gate unit as the Session closes, going with its thread; the
  owner-bound guard counts it), and the reviewer's repro is a test (answered with the
  bound held, no Session opened). M2 a test holds the commit that carries an input and
  finds the thread's job already scheduled. L1 a parked task now ends with its own
  conversation's run (the task aborted, then that conversation), not the run at the top
  of its owner links: a subagent a tool starts is a nested `ta` call, which pi abandons at
  its first restart, so its tasks never park, and background work's conversations are
  where one sits outside the root; aborting the root there would have ended the root's
  other work. Tested: a read parked in background work's conversation settles that input
  `unanswered`, the root's sleeping background work survives, and the thread takes its
  next message. L2 endings run in batches of the bound (tested: at most the bound at once).
  L3 kept conversation records are frozen deeply (tested). Mutation of the touched code,
  13: 11 killed; alive, `ag-usage-opens` (it reads only Sessions already open, so it
  opens none: equivalent) and dropping the parked task's own abort, which round 3 showed
  was a real gap, not covered as this note first said.
  Review round 3 (all Low, all taken): L1 a parked background task (pi gives background
  work no `abandonOnRestart`, and its restart cascade skips it, so it parks) is ended with
  `abortTask` alone, never its conversation's run, whose inputs a conversation abort would
  withdraw; tested: the root's parked background work ends terminal and every input
  submitted as it ends is answered. L2 the root's parked read leaves a root background
  sleeper untouched (tested). L3 a reopened thread counts once in the usage view (tested).
  L4 a usage that cannot be kept at close leaves `usage_unkept` (tested). Not taken: the
  usage row kept also when a Session's wake turns idle, since reading `pi.usage` there
  adds store reads to every message and cannot share the live row's unit without reading
  it first. The reviewer's round-3 mutants all killed.
- [x] Before any deploy holding real data: S4's drop wipes its store (`destroy()`, the row
  `dropping` until it returned). Done in S4-S5 (below).
- [x] Before any deploy holding real data: a grant rename, or any set that changes neither
  mode, scope nor revocation, must not move the authority generation or drop threads;
  split the revision token from the authority generation (S4-S5 review round 2, L1). Done
  in workerd, nothing deployed (`grants.ts`, `owner-do.ts` `grantSet`, `api-keys.ts`,
  migration 12): `revision` moves on every set and revoke and is what the owner's set
  names (`expected_revision`); `generation` moves only when the mode or the set of scopes
  does, and stamps and `standing` read it alone, so only such a set, or a revoke, drops.
  A revoked grant set revoked again keeps its fence.
- [x] S4-S5 Drops wipe and run retention, in workerd, nothing deployed (`threads.ts`,
  `agent.ts`, `thread-host.ts`, `thread-store.ts`, `erasure.ts`, `erase.ts`, `policy.ts`,
  migration 10). One drop path for every reason (idle retention, a run's retention, a
  grant change or revoke, the owner's erasure): in one unit the row is marked `dropping`
  (no door resolves a dropping conversation, nothing opens its Session); its Session's
  work is ended and the Session closed through `ThreadHost.drop` (never resumed, never
  opened for it), at most `END_WAIT_MS`, past which a Session that will not close is cut
  off; `store.destroy()`; then in one unit the row is forgotten with its live and usage
  rows (by trigger), its usage added into the owner's total (`agent_usage_total`, keys
  past 48 a bucket summed into `other`, exact), and its wake job. The `threads` lane
  finishes any drop cut short, each held to 10 s and backed off in its row (`drop_retry`,
  60 s), the owner told once after 5 failures (`thread_drop_stuck`). A dropping thread
  keeps its client's grant row and counts against every bound until it is forgotten, so a
  grant made again at once makes none past them. A destroyed store keeps no mark: it is as
  empty as one never made (`databaseSize` 4,096 in workerd, against the mark's 8,192); the
  instance that destroyed it refuses at once, and the owner opens only stores its ready
  rows name. Kinds: `run` is every launch's thread (the `run_graph` door, `ta`'s
  `agent:run`, a scheduled launch), `converse` the door's conversation; none was left
  unmapped. A run settles once: when its Session reports nothing running and nothing
  asleep with its input settled, one unit keeps `settled_at` and its receipt
  (`run_receipts`: status, a reason of 64 characters at most, the first 768 bytes of its
  answer, two receipts a page), read from that Session,
  and adds its usage into the owner's total, keeping no usage row (counted once, the usage
  view the same before and after). A settled run takes no input: a new one is refused
  `run_settled` at the host's admission before pi commits it (a guard), from the doors,
  `ta` and scheduled work alike (the work fails, never retried); a run settles only in a
  unit that finds no input being admitted (a guard), so an admitted one is never refused
  late; and a late commit of its Session lets its turn owners go and keeps none, so it
  holds no turn, live row, wake job or usage row; F1's scheduled run is unsettled while it
  sleeps.
  A settled run's status and cancel are answered from the receipt, its Session never
  opened (F12's one read holds, polls move nothing), before its drop, during it and after;
  while its store is there an answer past the receipt's 768 bytes is read whole from it by a
  pure read (`ThreadStore.answer`: no holder claim, refused for a store never opened), and
  once it is wiped the receipt answers marked `truncated: true`;
  its store is wiped `keep_runs` after it settled (owner policy `runs.keep_runs`, 7 d, 1 to
  30), never while live; a run never seen settling goes `keep_runs` after its last
  activity, answering as one pi never saw. Conversations go at idle retention (7 d), and a
  live row moves no activity; a wake job that finds nothing running and nothing asleep
  rewrites the live row from its Session (settling a run) before it completes, and an open
  keeps the wake as it stands once its job is set, so a stale "running" row never holds a
  run; a run is told its input's request id as it is handed it, so a receipt kept without
  its settling names it (`dropped`). Bounds: conversations 64 a client, 1,024 every client's,
  10,000 the owner's; runs their own, `runs_kept` 25,000 kept stores and `runs.runs_live`
  (owner policy, 2,000, 1 to 2,000) runs not yet settled, a sleeping one among them, each
  refused at launch with a fixed code (`runs_live` for the live ones), an outside client
  holding at most `runs.client_share` (25%) of each and every client together what
  `runs.owner_reserve` (25%) leaves, so the owner can always launch. Receipts are a history
  with a row bound, never a refusal: 50,000, shared the same way, each scope's oldest let
  go past its share, kept `keep_run_receipts` from settling (90 d, 1 to 365, never under
  `keep_runs`); a client's receipts go with its grant's change. Every thread's kept usage is
  bounded at 64 MiB in all (a backstop, told as `usage_unkept`). Erasure as a tested internal path (its door comes
  in slice 10): one unit marks the owner erasing (`owner_erasure`) and every thread
  dropping, and pushes the erasure's Lifecycle job; every door then refuses (`ERASING`, in
  the binding check), lanes run nothing, no thread is recorded and no Session opens; every
  store a row names is destroyed through the drop path, each bounded, past one that fails
  to the next, the step recorded done (`step` 2) only with no row left; then Lifecycle's
  alarm is disabled for good and the write gate deletes the whole database
  (`WriteGate.wipe`, whose only caller is the erasure's wiring, a guard). Each step resumes
  from the recorded one at the object's next start, by the job, and at every refused door:
  a crash after the alarm's disabling, or a failed wipe, finishes at the next start. The
  owner-bound guard counts by state: every thread row at its widest (775 bytes, in place:
  a row names the outside agent's registration its stamp was made from, never a copy of the
  universe, and its agent id is its directory's ASCII name), and only
  the live ones (conversations and `runs_live`) with turn owners, `model_turns`, usage and
  live rows, wake jobs and keys; a settled run by its row and its receipt; the usage space
  and total and the erasure mark: 4,933,044,828 bytes, 49.3%, about 67 MB of headroom, so
  F1 and later slices trade terms. A registration a thread names never changes its id,
  universe or client (a trigger), and its delete is ignored while a thread names it; the
  owner's delete leaves a tombstone that goes with the last such thread (it counts against
  the registry's rows meanwhile); the alarm guard admits only `erase.ts`'s `disableAlarms` and
  `gate.ts`'s `deleteAll` beside the store's. Declared differences from the design: a
  closed dropping Session is not reopened to end it (nothing of it runs in memory); client
  runs share `runs_kept` rather than counting in the conversation bounds; a settled run's
  answer read after its store is wiped is its receipt's first 768 bytes, marked. Review
  round 1 (REQUEST CHANGES), all taken but the rename (since fixed: the rename blocker
  above): H1 erasure's wipe resumes at start; H2 settling and status as above;
  M1 run shares; M2 receipts as history; M3 no destroyed mark; M4 the usage total; the
  lows (retry and erasure tests, receipts during a drop, bounded drops with the notice,
  the wipe caller guard, receipts with the grant, slice 10, the guard's row width and
  Lifecycle's rows). Review round 2 (REQUEST CHANGES), all taken: H1 the whole answer until
  the wipe, then the marked receipt; M2 the job's rewrite and the open's ordering; M3 tests
  for a sleeping run, a reopen with background work active, a parked run, and a door's
  resume; L2 a door resumes an erasure at most once a retry interval; L3 a cut-off
  Session's usage kept before its row goes; L4 the handed request; the rename as the
  blocker above; M1 the re-budget at the files-quota merge. Review round 3 (APPROVE), its
  lows taken: L1 a settling run's usage that cannot be read is logged and noticed
  (`usage_unkept`); L2 a run's settling unit lets every turn owner it kept go, a lost
  unit's too, and each turn left with no owner (a trigger); L3 the registry-full refusal
  names threads beside revokes as what keeps a tombstone, and the guard counts every
  registration row; the nits: `request` is frozen once kept, and the recording unit holds
  one conversation a caller, agent and universe (a guard; no index can read the universe).
  L4, `runs_live`'s default at its high bound, is F1's. Still owed live: P8, `deleteAll()` frees a store's billed bytes and a
  destroyed store bills nothing (`store-probe.md`).
- [x] F1 and admission: `ta.scheduled` pi task, `wake:list` from rows, cancel and status
  paths, cron recurrence; delete the `work` lane, `WorkRunner` and migrations 7-8's work
  tables; a policy write aborts active Sessions' work only, sleepers enforced in `deliver`;
  the owner's admission row and `require_work`. Built in workerd, nothing deployed
  (`scheduled.ts`, `cron.ts`, `admission.ts`, `agent.ts`, `thread-host.ts`,
  `thread-store.ts`, `threads.ts`, `owner-do.ts`, `ledger.ts`, `gate.ts`, migration 13).
  A scheduled launch (`agentRun` with `at` or `cron`, `ta`'s `agent:run`) records its run
  thread naming its work (`request`, `work_…`) and its store, then makes its `ta.scheduled`
  task in the thread's root inside the thread's wake (`ThreadHost.admitted`), owned by the
  conversation, `background: true`, `trace: "root"`, its input `{work_id, thread, at,
  stamp, input, cron}`: the owner's one task creation point (the create-task guard, changed
  for exactly it). `wait` sleeps until `at`, its wake reaching `agent_live.wake_at` by the
  thread's job; `deliver` re-checks in one unit the thread and stamp, the admission, the
  stamp's standing and `run_graph`, and the run policy as it stands (`Threads.liveRefusal`),
  then submits through the host's admission with `requestId` the occurrence's and entry
  `ta.work` (its own turn); a delivery already made (an eviction after its submit) is pi's
  duplicate, not re-checked; no room (`thread_inputs_full`, `sessions_full`) sleeps again,
  30 s doubling to 10 min; a refusal ends the task `failed` with its code, and its run
  settles with that reason. Review round 1 (REQUEST CHANGES), all taken: H1 admission is
  enforced, not only signalled: `Authority.require` and `modelRule` refuse every operation
  and model call while the owner is not admitted, the host refuses a fresh input right
  before pi commits it, and a suspension ends the work of every thread with a live row,
  asleep in pi's retry backoff or deferred poll included, keeping only the scheduled
  launches' tasks (`endUnadmitted`); each door declares its class (`work`, `read`,
  `reduce`; the door-class guard), and a `billing` suspension refuses only `work`, so the
  owner signs in, reads, exports, cancels, revokes and deletes, while `abuse` refuses every
  door. M1 `deliver` re-checks inside the host's `admitted` step, which stops on the task's
  abort signal, and the abort handler counts an occurrence pi holds as delivered;
  `cancelScheduled` waits for the task to end, then withdraws its last delivery. M2 kept
  as built (the coordinator's decision): `deleteAll`, then the `erased` row. The facet
  probe's Q8 (run 41247617223): with no crash the order is right (20 of 20 wiped and
  written); production's `ctx.abort()` rolled back everything since the last durable
  point, even an awaited `deleteAll`, so the crash window was not measured. It stands
  until slice 10 closes it. M3 a suspension or a run-policy refusal holds a delivery,
  which sleeps again backing off (its checkpoint's `held`, status `held: <code>`); only a
  dropped thread, a stamp that no longer stands or a settled run ends it. Lows: the
  `THREAD_DROPPED` branch is live and tested; a parked schedule ends `restart_limit`; a
  launch whose task cannot be made drops its run; `createScheduled` makes only
  `ta.scheduled`; the tombstone's shape is pinned (a guard); cron is UTC only. Mutation
  after round 1: 30 on the fixes (admission in `require`, `modelRule` and the host, the
  suspension's ending and what it keeps, the door classes, cancel's wait and withdraw,
  the held re-sleep, its doubling and cap, `sameStamp`'s fields, the `request` check,
  `scheduledOf`'s state, the parked end, the dropped launch), 30 killed; three first
  alive were killed by tests added for them. Merged with the grant rename (#5377): the
  migration is 13, and every stamp check and scheduled refusal reads the generation, so
  a rename keeps a client's schedules (tested); a grant set is `work`, a revoke `reduce`.
  Two mutants on the merge (a rename moving the generation, the set's class), both killed,
  the second by a test added for it. Review round 2 (APPROVE, two fixes): the tombstone's
  window is named (`erase.ts`) and gates the erasure door (slice 10's item below); an
  admission hold does not poll: while the owner is not admitted a wake job opens nothing
  and looks again in a day (`ADMISSION_HOLD_MS`), the readmission sets due every live
  thread working or past its wake (`wakeHeld`, in batches of the host's bound, waited on
  at most `END_WAIT_MS`), and a launch not yet delivered reads held by the admission;
  run-policy and room holds keep their 30 s to 10 min backoff. Tested: a suspension
  holding with no Session opened past the backoff's cap, the readmission delivering at
  once, the day's hold delivering once when the wake is lost, a schedule not yet due left
  alone, a row a crash left working woken. Mutation: 7 on the hold (the step's check, the
  cap, the door's wake, `overdue`'s two terms, the held status, `wakeNow`'s time), all
  killed, two by tests added for them.
  Cron is ours: five numeric UTC fields, at most 64 characters
  and 16 items a field, occurrences at least 15 minutes apart, one within eight years; each
  delivery sleeps until the next, until a re-check refuses or a cancel. Cancel is
  `abortTask` (and the last delivery withdrawn); status is the task's record and its last
  delivery's, by a pure store read (`ThreadStore.task`, `answer`) that opens no Session,
  then the run's receipt; `wake:list` reads `Threads.scheduledOf` (the thread row, its live
  row, its receipt). A policy write aborts active Sessions' work only, never a sleeping
  task nor a scheduled launch's; a grant change (a set that moves the generation, or a
  revoke) still drops and wipes the client's threads,
  sleepers included. The scheduled launches hold at most `SCHEDULED_SHARE` (50%) of
  `runs_live`, shared by `client_share` and `owner_reserve`, refused `runs_scheduled` at the
  launch (S4-S5 review L4: a scheduled backlog never starves a launch made now; `runs_live`
  stays 2,000 default and high). Deleted: the `work` lane, `WorkRunner`, `Scheduled` and
  their tests, and threads' held-by-work retention case; migration 13 drops
  `scheduled_work` (migrations 9 to 11 name it, so it is dropped rather than edited out).
  Admission: `owner_admission`, admitted when the object is made, refused at every door in
  `#bind` (`owner_suspended`, `owner_erased`) and by `deliver` and the `wakes` claim (held,
  its lane given no job for open rows); `admissionSet` from `PLATFORM_CALLER` only, which
  reaches no other door and names no owner (the admission-caller guard: the name spelled
  once, in `admission.ts`, while no `platform/admin` exists); a suspension ends running work
  as a policy write does. The erasure's last step leaves one `erased` row in the wiped
  database, written in the wipe's own turn (and again once it resolves, only into a wiped
  database); an object that finds it migrates nothing, starts no Lifecycle and refuses
  every door. The owner-bound guard drops the work table's term and counts the admission
  row: 4,897,208,852 bytes, 49.0% (49.3% before). Tested: a three-day sleep through an
  eviction (pi's clock moved ahead) delivering once; a cancel asleep; a grant change wiping
  a sleeper; a policy write leaving a sleeper asleep while ending its thread's running
  input, its delivery then refused `runs_scheduled`; a suspension refusing a delivery; an
  eviction inside `deliver` giving one submission; a full thread sleeping again and
  delivering once; `wake:list` and status with no Session opened; status and cancel
  delivered and refused; cron property tests against a minute scan, and a recurring
  launch delivering three times until a grant change; the share; admission at every
  door, its platform-only door, the `wakes` claim; the tombstone across evictions, in the
  wipe's turn, and after each erasure crash. Mutation: 48 (the re-checks, the re-sleep,
  the requestId dedupe, cancel, the cron function, the policy-write rule, admission at
  `#bind` and the lanes, `admissionSet`'s caller, the tombstone's order, the guards'
  terms, the share), 48 killed; six first alive were killed by tests added for them, and
  one equivalent (an erased owner's `admissionSet`, which the binding check refuses first)
  went as dead code. Declared differences: the task's input also
  names its thread and cron (the registry is shared by every Session); a scheduled run's
  `request` is its work id from its recording, so its receipt names it however it ends; an
  owner absent an admission row is admitted (migration 13 writes it), the platform
  suspending. Live probe P10 still owed: a days-long sleep across hibernation and a
  deploy. `deleteAll` and the tombstone in one turn on production storage: the facet
  probe's Q8 (run 41247617223) found the order right and did not measure the crash window
  (production's abort rolled back even an awaited `deleteAll`); the follow-up crash point
  is `deleteAll`, then `sync()`, then the write, then the abort.
- [x] A5 with F5: skills, prompt templates and declarative packages, and `agent:delegate`
  with task-owned children. Built in workerd, nothing deployed (`resources.ts`,
  `delegate.ts`, `ta.ts`, `agent.ts`, `thread-host.ts`, `policy.ts`, `origin.ts`,
  `schema.ts`, `threads.ts`, `usage.ts`, `owner-do.ts`; agents 0.32.0). Skills are the
  Agent Skills format, pi-coding-agent's way: `SKILL.md` directories under
  `/workspace/.agents/skills` and `/workspace/skills` at any depth (one holding `SKILL.md`
  not looked into further, dot directories and `node_modules` skipped), then each selected
  package's, parsed with agents' `parseSkill`; the trusted `ta.skills` extension's `skills`
  section lists each `isModelInvocable` skill's name, description and path in pi's own
  words (`formatSkillsForPrompt`, ported, MIT), at most 128, and the model reads a skill
  with `read`: no skill tool. A name taken twice keeps the first; a skill the owner's own
  authority did not write (as `AGENTS.md`), or that does not parse, is skipped with a
  notice. The section is built at the first open and rebuilt (installed again under its
  name, pi's in-place reload) only when the files' change feed touches a `SKILL.md` or a
  template under a root, anything under a selected package's directory, or a directory
  above them, or a policy write moves the selection: never per request; one rebuild at a
  time, the asks meanwhile coalesced into one after it. Prompt templates are the direct
  `.md` children of `/workspace/.agents/prompts`, `/workspace/prompts` and selected
  packages' prompts; the owner's own input to `agentSubmit`, `agentRun` and `ta`'s
  `agent:run` expands at the door as pi's `prompt` does (`/skill:name args` into pi's
  `<skill>` block, a skill that disables model invocation included; then `/name args`
  with pi's `substituteArgs`, ported), reading the file as it stands; an unknown name
  stays as typed, and a client's enveloped input is data, never expanded. Packages are
  pi packages in the workspace, selected by owner policy's `packages` list (pi's settings
  form, at most 32, each a directory under `/workspace/.agents/packages`, filters per kind with pi's
  grammar, `applyPatterns` and its exact-path forms ported over a glob subset of
  minimatch's `**`, `*`, `?`); there is no `extensions` filter. A package contributes its
  skills and templates only; one that lists `pi.extensions` or holds an `extensions/`
  directory is refused whole, `package_executable`, with an owner notice. `agent:delegate
  {agent?, task}` is a `ta` effect under `run_graph` (a delegation launches an agent, the
  scope `agent:run` and the `agentRun` door hold; a scoped client also needs it where the
  named agent acts, checked again in the admission's unit): its child is made in the
  call's commit owned by the call's task (`createChild`, the create-task guard's second
  creation point and the agent-authority guard's second maker, its one `AgentDoc` draft
  writing `instructions` only), found again on a rerun by `scanConversations({
  ownerTaskId})`, configured as the named agent's `AGENTS.md` (no agent: the caller's,
  pi's copy), its authority and five tools from `conversationCreated`; its one input,
  `subagent:<task>`, is submitted under the call's context, so it joins the call's turn
  (its calls charged there, under its caps), admitted by the host's `linked` (the
  thread's open inputs, the admission, and `turn_ended` before pi admits it when the
  turn has no row); the call waits, ends the child's lingering work once answered, and
  answers `{conversation_id, status, text | reason}`. Aborting the call aborts the
  child and the caller is idle only once it is (pi's ownership); a revoke, a suspension
  and a policy write end it with its thread. `ta` is now `replay: "safe"` by its own
  rule, so a subagent survives its parent's eviction as example 22's does: a read and a
  delegation rerun, a tool reached through `executeTool` is reached again by its key
  (the nested call answers under its own policy), and every other effect commits a memo
  before it acts and is refused `interrupted` on a rerun (ADR-032 §4 amended). A thread
  holds at most `THREAD_CONVERSATIONS` (3) working conversations, its root among them,
  kept in the session's `ta.delegations` document and pruned in each creating commit
  (`thread_conversations_full`); compactions are per conversation, so `THREAD_COMPACTIONS`
  is 3 x 2, and a thread's subagents hold at most `THREAD_CHILD_INPUTS` (2) inputs (`c:`
  owners), apart from its root's eight (`THREAD_INPUTS`, so steering is never starved by
  subagents). The turn rows are bounded by the threads at work, not every live thread:
  `WORKING_THREADS` (1,024) threads may hold turn owners at once; the host admits a fresh
  input to a thread not at work only within `WORKING_THREADS - OPEN_MAX` of them, in a
  unit, counting the admissions in flight, a client within `client_share` and the clients
  within what `owner_reserve` leaves (`threads_working`, refused at once, a scheduled
  delivery held), so a compaction pi starts in an open Session always has a place;
  migration 10's trigger refuses a thread's first owner past the bound (edited in place:
  nothing is deployed). The owner-bound guard's turn terms are the working threads times
  sixteen owners: 4,597,482,932 bytes, 46.0%, about 403 MB under half (F1: 49.0%). F1 review lows carried: a wake job stepped while
  the owner is not admitted holds until the sooner of a day and its live row's wake still
  ahead (on pi's clock), and one whose row says working opens and finishes its work now
  (a cancel's abort handler an eviction cut off). agents 0.31.0 to 0.32.0: Lifecycle's
  chunk is byte-identical, and PiHarness's one change is `skills()` (now
  `loadSkillCatalog`, its tools erroring loudly); its wake and recovery code
  (`_admit`, `_wake`, `_wakeStep`, `_waitWhileActive`, `onJobError`, `_open`) is
  unchanged, so `thread-host.ts` takes nothing; `agents/skills` joins the agents import
  allow-list; agents now resolves under the owner's workspace (its required peer
  `@modelcontextprotocol/client` is pinned exactly at 2.4.0, the doors at 2.3.1, so npm
  nests it with its own MCP packages, which the owner never imports; it hoists again when
  the doors move to 2.4.0). Tested in workerd: a skill listed and read,
  one that disables model invocation not listed, one rebuild for an edit and none per
  request, skipped skills noticed, `/skill:` and template expansion, a package filtered
  by policy, a coded package refused with its notice, the selection's validation; a
  delegation answered as the named agent on the caller's turn with the five tools, a
  rerun after an eviction reusing the child and its input, the abort cascade and idle, the
  conversation cap, `turn_ended` at admission, a scoped client refused without
  `run_graph` and past its scope, a revoke, a suspension and a policy write ending the
  child, the caps through the gateway; the two lows. Mutation: 53 (the section's
  filtering, walk and first-wins, the rebuild trigger and a rebuild per request, the
  expansions, the package refusal, filters and selection, the delegation's ownership,
  reuse, request id, wait, instructions, scope and lingering work, the conversation cap and
  its pruning, the linked admission's inputs and turn checks, `ta`'s rerun rule, the two
  lows, the guards' terms and creation points), 51 killed: of those first alive, five were
  killed by tests added for them, and a sixth went with the code it mutated (a second copy
  of the build's model-invocable filter in the section); two survive as equivalent, each covering the other: the linked
  admission's own admission check and the admission unit's authority re-check (`require`
  reads the admission too, and every authority change drops the thread, its abort failing
  the submit). Declared differences from pi and the
  audit (F5, F9): standalone `.md` skills are not loaded (the directory form only); the
  `tinyassets` key's fields (settings, model routing hints) are skipped with a notice
  until they have a consumer (owner policy is the one settings source; `auto`'s ranking
  would take hints); no child configuration but its instructions (pi's example also
  picks a model and drops the subagent tool: the cap bounds nesting instead); templates'
  descriptions are not read (no command list yet). Review round 1 (REQUEST CHANGES), all
  taken: M1 the agent's abilities are owner-only trees as `/workspace/agents` is
  (`OWNER_TREES`: `/workspace/agents`, `/workspace/.agents`, `/workspace/skills`,
  `/workspace/prompts`), at the file doors and in the agent's environment, a client's
  change refused with the same code and every change the owner's agent makes there noticed
  (`agent_definition`), and a selected package must live under `/workspace/.agents/packages`
  (the policy refuses any other), so no client's file is ever beside an owner's skill or in
  a selected package; M2 the working-threads bound above, in place of the cut to the doors'
  inputs; L1 the admission unit's re-check of the named agent's scope is gone, being
  redundant by construction (a grant's scopes move only with its generation, which the
  unit's re-check of the caller's stamp reads), the scope checked before the child is made;
  L2 a package refuses on any npm key but its data (an entry point, `bin`, `scripts`), any
  `pi` key but `skills` and `prompts` (`themes`, `image` and `video` skipped with a
  notice; an empty `extensions` list declares none), and a `pi.extensions` string, each
  `package_executable`; L3 the child's admission checks (its subagent inputs, the
  admission, the turn) run in the commit that would make it, before it is made, so a
  refused delegation leaves nothing; L4 a rerun's refused effect answers as pi does an
  unsafe tool's, the `interrupted` code and pi's words; L5 the skills section reaches a
  conversation only while its authority holds `read_files`; L6 a resent subagent input is
  tested to skip the checks; the nits: the MCP peer note, `DELEGATE_TASK_MAX` in code
  points, an expanded owner input past `AGENT_TEXT_MAX` refused loudly, and every tool a
  package provides passes the early gate again as it executes, so a replay-safe one pi
  reruns after a restart (its recovery runs no hook) is held to the authority as it stands.
  Tested: a client's script beside an owner's skill, a skill at a root, a flood, a template,
  a package's `extensions/` and manifest each refused at the door and from its
  conversation; the owner's agent's skill noticed; packages outside the owner's tree
  refused; every code form of a manifest refused; the section hidden from a client without
  `read_files`; a client past its share of the working threads while the owner works, two
  admissions at once taking one place once, a settled thread freeing its place, a held
  delivery; eight door inputs beside two subagents, and while a delegation is admitted; the
  subagent inputs' bound refused before the child is made; a resent subagent input not
  checked again; pi's `interrupted` code on a rerun; a nested replay-safe tool's rerun
  refused by its own gate while the owner is not admitted; the task's and the expanded
  input's bounds. Mutation on the round's code: 25, 24 killed (six first alive killed by
  tests added for them), one equivalent (the linked admission's own admission check,
  which `Authority.require` reads at the call's start and in the unit); A5's own set re-run:
  44 still apply, 44 killed (the two that survived went with the code they mutated);
  the reviewer's runner, its patterns moved to the new lines: 24 apply, 24 killed (its M11
  went with the redundant line). Review round 2 (APPROVE), its lows: A the compaction
  reserve pinned (the owner-bound guard holds `workingMax: WORKING_THREADS - OPEN_MAX`) and
  tested at the limit, a compaction in an open Session still landing its owner; B the
  owner's reserve across clients tested; C and D owed (below); E the sandbox's sync owed
  under tasks 6. The reviewer's runner: 35, 34 killed, R13 equivalent (the linked room's
  admission check, read again by `Authority.require`).
- [ ] Persistent helpers (example 23): a background anchor task owning long-lived
  subagents, spawned, messaged, waited on, stopped and listed through one capability, each
  answer posted back by a background reporter task under a request id. Needs: the anchor
  and reporter as creation points of the create-task guard (background, carrying no
  origin), each reporter's submit admitted by `ThreadHost.linked`-style admission (its turn
  is its own, a fresh origin), and the anchor's children counted in
  `THREAD_CONVERSATIONS` or a cap of their own, traded in the owner-bound guard.
- [ ] A5's working places, owed (review round 2):
  - C, stale places: a crash between pi's commit and its turn-owner unit, with the live row
    idle, holds a place until the thread's Session reopens, it settles or retention drops
    it; a mass eviction holds every place until recovery drains them, 64 at a time, fresh
    inputs refused `threads_working` meanwhile; two fresh admissions for the last place see
    each other in flight and both are refused. Fix: sweep the owners of threads neither
    open nor active (by `agent_live`) on the alarm, or count the soft limit by `agent_live`.
  - D, a held delivery spends a Session open per retry (30 s to 10 min) while the places
    are full, competing with the recovery that frees them. Fix: have the wake step check
    the working places before it opens, as the admission hold does, or wake held
    deliveries as a place frees.
- [ ] Package data A5 left unloaded: the `tinyassets` key's settings and model routing
  hints (each skipped with a notice now), when owner policy and `auto` take them;
  package tool schemas (the plan's declarative tool schemas) reached through `ta`.
- [x] H0 with code mode (workerd only, nothing deployed): `platform/hooks`, the
  `tinyassets-hooks` Worker. `Hooks.run(request)` at the owner's own `HookRunner`
  (`getByName(owner)`, no storage use), which loads the owner's bundle with the Worker
  Loader under `<owner>:<bundle id>:shim<n>` (`compatibilityDate` the owner Worker's,
  `globalOutbound: null`, `env: {}`, `limits {cpuMs: 1,000 ceiling, subRequests: 0}`, each
  call asking the policy's `cpu_ms`), our shim dispatching the point to the module's export
  with JSON in and out, `need_code` and the resend, its own deadline, a dropped call's
  stub disposed and at most 16 pending; its prelude freezes the shared intrinsics before
  any package's module runs. `Hooks.codemodeExecute(name, request, exchange)` runs one
  execution in its `CodemodeSandbox` (a `CodemodeSandboxDurableObject` subclass with its
  interrupt budget and memory, served by pi's `serveCodemodeRemote`), handing out no stub. Config guard: its loader and its own two classes only, no
  `OWNER`, no storage binding, no route, no workers.dev; isolation guard over its source.
  P11's local part passed (`exchange` an RPC function over the `HOOKS` binding, a nested
  call's round trip, the interrupt budget a `timeout`).
- [x] H1: owner wiring. `HOOKS` in the owner config guard, its Worker held to its loader,
  own classes and limits. A selected package's `tinyassets.hooks` entries are remote
  wrappers on its `pkg:<name>` extension (modules prebundled ESM in the owner's own files,
  1 MiB together, one bundle per owner, its id `<owner>/<sha256>`); `pi.extensions` and
  every other in-process code still `package_executable`. Failure rules: `beforeTool`
  blocks, `afterTool` fails closed redacted, `onYield` continues nothing, `beforeCompact`
  leaves pi's summary, observe-only points report (250 ms); every wrapper total, a
  memo's read or store failing by its point's rule; a decision past 256 KiB `invalid`;
  each failure a `hook_failed` notice;
  each decision memoized (`ta.hook/<ext>/<point>/<key>`), so a rerun asks no hook; the
  deadline `inTime(HOOKS.run(...), deadline_ms + 250)`. Code mode is `ta call codemode`,
  tools-only, needing `run_code` (the owner's; a client's only where a scoped grant names
  it), its scripts' tools held to the caller's authority by the gate, a script's own
  `tools.codemode` refused, its model calls `tools.model_complete` (tools-only,
  replay-unsafe: through the gate and the packages' hooks, reserved and charged to the
  turn, in `pi.usage`, never twice). Review round 1 (REQUEST CHANGES), all taken: H-1,
  M-1, M-2, M-3 (owed to H2, below), L-1 to L-6. Policy: the
  `hooks` table (`timeout_ms` 100 to 10,000, default 2,000; `cpu_ms` 50 to 1,000, default
  50; `codemode`, off by default). No owner table added (the owner-bound guard's worst case
  unchanged, 46.0%): memos live in pi's thread stores.
- [ ] H2 live (founder: Workers Paid, Dynamic Workers in open beta): deploy the hooks Worker
  and probe what workerd cannot show: `cpuMs` enforcement (a hook past its `cpu_ms`, a
  script past its interrupt budget against the Worker's `cpu_ms`), the blocking isolation
  (10 s of compute in a hook or a script against the owner object's latency), and the
  Dynamic Worker count per day (one per owner per active day expected), with module state
  across isolates of one loader name. And the prelude's cost (review rounds 2 and 3): it
  hardens the whole global environment once per isolate, about 10 to 18 ms more of a first
  call in workerd (a method-heavy hook's cold call 25 to 33 ms against 15 ms unhardened).
  Per call, after round 3 tamed only the override-mistake names (and `constructor` not on
  the species prototypes), a method-heavy hook's warm call is 8 to 10 ms against 8 to 11 ms
  unhardened, a ratio of about 1.0; round 2's taming of every prototype property had made
  it 22 to 24 ms, about 2.4 times. SES's `lockdown()` measured about 30 ms and 236 KB a
  bundle and left host globals writable, so it was not taken. Live: the startup cost per
  isolate against `cpu_ms` (whether startup counts toward a call's `cpuMs`), the per-call
  ratio against the 50 ms default, and the Dynamic Worker CPU billed. And (review round 1): another
  owner's hook latency
  while one owner's script computes about 10 s in the same Worker (M-3; if it blocks, H2's
  design takes it: separate Workers for hooks and code mode, or a sandbox class per shard);
  an `afterTool` memo near the 256 KiB bound, and a decision over 2 MB refused, in real
  SQLite (`SQLITE_TOOBIG`); the lifetime of what `codemodeExecute` and the dropped hook
  calls leave in the owner's and the runner's invocations; and whether `exchange` calls
  count as subrequests of the owner object or the hooks Worker (which sets no
  `limits.subrequests`).
- [ ] Per-package isolation (founder's call, later): one isolate per selected package,
  one loader name each, at $0.002 per package per active day instead of per owner, so one
  package can no longer change what another exports. Until then an owner's packages are
  mutually trusted, and a guardrail package takes the guard position (`guard: true` in its
  selection), which runs its hooks after every other package's (H1, review round 3).
- [ ] The pi upgrade, within a slice of pi's release: `adopt` in `RemoteStorage`, the tee
  deleted, `persistWithinMs`, `maxTurns` from policy, `onReport`, parking code out of the
  host, the new watch API, the schema re-pinned.
- [ ] A6: agents `WebSockets` and `Conversation.watch()`; reset, compact, fork, withdraw
  and the task graph doors. The compact door must enforce a per-thread cap on manual
  compactions with a fixed refusal code, and update the owner-bound guard's `model_turns`
  term (`THREAD_COMPACTIONS` counts no manual one, since no such path exists yet).
- [ ] M (MCP) after G4: SDK v2 client in the owner over G4's forward, `mcp.json` with every
  server clamped to tools-only, OAuth via G3.
- [x] Erasure's store-first order: mark the owner erasing, destroy every store a row names,
  then `deleteAll()` the owner. Built as S4-S5's internal path (above); the tombstone that
  keeps a stale binder from making an erased owner again came with the admission row in
  F1; its door is slice 10's. Erasure completeness needs a record outside the owner object
  (the directory's or the identity's deletion: the owner's AuthKit identity and directory
  entries removed, so no sign-in or inbound route resolves the name again), or explicit
  platform admit at sign-up. The facet probe's Q8 (run 41247617223, `facet-probe.md`):
  with no crash the order is right (20 of 20 wiped and written); production's
  `ctx.abort()` rolled back everything since the last durable point, even an awaited
  `deleteAll`, so the crash window was not measured (workerd shows it: the delete kept,
  the tombstone lost). The window stands until slice 10's outside record closes it. A
  future run's crash point: `deleteAll`, then `sync()`, then the write, then the abort.
  Gate: the erasure door SHALL NOT ship before that outside record, or the explicit
  platform admit, exists; until then no door reaches erasure (`erase.ts`).
- [ ] 0c (live): the dark deploy of the owner and gateway Workers, the store probe's cases
  re-run on the real code (P1-P4, P7; P5 a store reset mid-run).
- [ ] User hooks in a separately deployed Worker's Durable Object, which loads each
  owner's hook code with the Worker Loader, called over RPC with JSON in and out and a
  timeout. A `beforeTool` timeout or error blocks
  the call; `api.memo` holds decisions that must survive a crash.

## 6. Files and sandbox

- [ ] Before any deploy holding real data, its own slice: the files byte quota.
  `file_entries` and `file_history` have no byte bound (a path's bytes are unbounded; the
  owner-bound guard names both), so a `write_files` client can fill the owner object's
  10 GB. Count path bytes against a quota.

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
- [x] Blocker: the files byte quota, before any deploy with real data (two reviews: an
  outside client holding `write_files` could fill the owner's 10 GB object through 4 KiB
  paths and churned history, and at `SQLITE_FULL` grants, revokes and policy writes fail
  too). In workerd, nothing deployed (`manifest.ts`, `files.ts`, `policy.ts`, migration
  10, the owner-bound guard). Every manifest row (entries, history, contents with their
  inline bytes and chunk lists, chunks, temporary directories, share rows) is charged by
  triggers the page bytes SQLite lays it out in, path bytes and overflow pages included,
  to `file_space.manifest_bytes`, against owner policy's `max_manifest_bytes` (512 MiB,
  its high too); the guard counts it at `WORST_FILL` (a thinned page may stay a third
  full), which a fill, thin and refill test holds, and every other table's rows as SQLite
  lays them out, their page share at `WORST_FILL` too, overflow pages whole. Each outside client holds at most
  `client_share` (25%) of every file quota and of the open uploads, all clients together
  what `owner_reserve` (25%) leaves, counted in `file_shares` (`client:<id>`, `*`,
  `owner`): each scope's own rows, and once for each content and chunk they or its
  uploads reference (`file_scope_contents`, `file_scope_chunks`), deduplicated within a
  scope and never across; an upload proves the bytes its writer's scope does not hold.
  The calls open at once and the spend counters are bounded (`CALLS_OPEN`, `SPEND_KEPT`).
  Refusals carry `file_quota` or `file_share`, in the change's own unit; a change
  past a byte or row bound first prunes gone paths' history by bytes and rows (the
  client's own for its share). A model turn's counters live as long as the pi records that
  own it (`model_turn_owners`, keyed by thread and pi record, since pi's ids are per
  store: each open input submission and live compaction, kept by the thread host's
  `subscribeCommits` listener in a unit at once, a unit that fails closing the Session,
  and reconciled from the store as a Session opens, before it resumes); a call of a turn
  with no row is refused `turn_ended`, a reservation's settle only moves a row that is
  there, and the turns' idle pruning is gone. A compaction a run made joins the run's
  turn, one outside any run is its own; an owner that joined a turn keeps it only while it
  is there. A thread holds at most `THREAD_INPUTS` (8) open root inputs (`thread_inputs_full`),
  pi at most `THREAD_COMPACTIONS` (6 since A5: two for each of three working conversations) live compactions, both refused past by the schema
  (`turn_owners_full`); a turn is at most `TURN_BYTES` (96); a dropped thread's turns go
  in its drop's unit. A thread's kept usage is at most 16 KiB (`USAGE_ROW_MAX`). An owner
  unit is all or nothing (its own savepoint); a Session's open reconcile passes over an
  owner past the bound with an owner notice (`turns_unkept`), so the thread still opens. A
  scheduled delivery that finds no room (`thread_inputs_full`, or every Session working)
  sleeps again, backing off, its wake never lost (F1). Every files bound can bind inside the manifest's: the
  defaults are 30,000 entries and 600,000 history rows (highs 500,000 and 600,000), and
  `max_inline_bytes` is at most 384 MiB. The guard's headroom is about 371 MB: a new table
  or a raised bound comes out of an existing term. The owner's sources create pi work at
  two points only, a scheduled launch's task and a subagent's conversation (the
  create-task guard). The owner-bound guard (`guards/owner-bound.test.ts`) has no exceptions and holds the
  worst case at the policy's high bounds under half of 10 GB.
- [ ] Doors (with tasks 2): the HTTP `/api/files` door and MCP file tools over the
  `file*` RPC doors, the scoped-grant capabilities `read_files` and `write_files` named
  as those tools are, and the sync agent's HTTP route by its sync token (the token, its
  binding and its acceptance at `fileChanges` and `fileSyncCommit` are G4's; the route,
  and the upload and chunk doors' acceptance of it, are this item's). The sync agent reaches
  the file doors through a transform too, so the VM never holds a token that spends.
- [ ] The sandbox (slice 8b), calling G4's `SandboxLeases` in the owner's object only (the
  agent's parts carry it to `exec`): the E2B client and lifecycle (`open` for a sandbox
  run, its egress token in the static transform and its network document and
  environment setting the sandbox up; `networkApplied` after each `updateNetwork`, and
  again whenever `stale` says the egress table moved, since egress is refused until it;
  `renew` on the 20 s heartbeat; `close` at pause, which ends the run whose caps its model
  calls were held to), the E2B project API key and `ROUTING_MAC_KEY` held as platform
  secrets, the template (its `/etc/resolv.conf` naming
  the pinned resolver), and G4's live probe (above); the
  E2B sandbox through our proxy; the in-VM sync agent (sync in from
  the change feed, sync out as one fenced `fileSyncCommit`, a 20 s lease heartbeat and
  15 s checkpoints, `/proc` open-file deferral and SQLite file groups, the cold-create
  size cap); `exec` in the environment (wake, sync in, run, sync out, report conflicts
  and a fenced sync-out in its result), which re-checks the conversation's authority
  (`Grants.standing` on its stamp) before it starts a command and honours the abort
  signal throughout, so a revoke ends a running command (A2); symbolic links in the manifest and the
  conformance suite's `exec` and symlink cases; and the sync-ignore defaults, which are
  the owner's file `/workspace/.ta/sync-ignore` (`node_modules`, `.venv`, `__pycache__`,
  `.cache`, build output), shown to the founder first since they decide what survives a
  lost sandbox. Those defaults are what make the 30k default for `max_entries` workable; a
  sync-out refused `file_quota` reports its refused paths as sandbox-only, never drops them
  silently; and the owner is shown the trade between entries and `keep_recent` when it
  first binds (files-quota review, carried by S4-S5).
  The sync path must hold the owner's trees (`OWNER_TREES`, `paths.ts`, A5) as the doors and
  the agent's environment do: a sync-in never carries a client's change into them, and a
  sync-out from a command a client's conversation ran never writes there (refused with
  `AGENTS_OWNER_ONLY`, reported as sandbox-only).
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
- [x] On the owner Worker's first deploy: check that Paid accepts its explicit
  `limits.subrequests` (run B ran at the default limit and never set it). The store
  probe's run 1 answered it: Paid accepted 10,000 and the raised 1,000,000 (P1c).
- [x] The store probe (slice 0c, batch 1), before the storage slice: P1, P2, P3, P4 and P7
  for the per-thread `ThreadStore`. Method, cases, reading key and results table:
  `store-probe.md`; run by the manual `store-probe.yml`. Run 1 (41029757222, 2026-10-30):
  P3 the same-script binding is accepted and reaches only `ThreadStore`; P1 store RPCs
  count on a separate per-invocation counter, which `limits.subrequests: 1,000,000` lifts,
  and the count resets across events (P1a and P1b void); P2 a store RPC adds 1-2 ms, a new
  thread's store opens about 0.3 s cold; P4 128 Sessions held with no break (`OPEN_MAX`
  64); P7 one `RpcTarget` session bills 2 requests for 200 calls against 202, and pins its
  store while held.
- [x] The facet probe P13 (with Q8, F1's erasure window). Method, key and results:
  `facet-probe.md`; run by the manual `facet-probe.yml`. Run 1 (41247617223, 2026-10-31):
  Q1 a parent and all its facets share one ceiling of about 10 GiB (read by hand; at the
  ceiling production resets the parent instead of a size error), so the real per-object
  ceiling is about 10 GiB and the owner-bound guard's 10 × 10^9 denominator is
  conservative; Q2 under `disable_ctx_exports` only a Worker Loader class works; Q3 a
  `ctx.exports` facet gets the parent's whole env; Q4 a facet's out-of-memory kills its
  parent (Q4's no-yield and over-CPU burns not measured: they ran far short of design);
  Q6 facet calls are under 1 ms and billed on the parent; Q8 the order is right with no
  crash, and the crash window was not measured (production's abort rolled back even an
  awaited `deleteAll`). The design stays: top-level
  `ThreadStore` objects hosted by `ThreadHost`; facets do not replace the store transport.
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
  (`require_work`: the claim reads the admission row in its own unit, built in F1). The kill switch reaches the alarm through a `DOORBELL` service
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
