Build order is the plan of record's "Migration and build order" (ADR-032), one
section per slice. An item is ticked in the PR that lands it. A landed item whose
live acceptance is still owed says so.

**Re-cut by ADR-034 (2026-11-01).** Agents run on unmodified engines, and pi-durable
leaves the owner's object. The order from here is `engines-replan.md` §5: finish
G4, then one deletion slice (its §1.7, done: section 5), engine records, runs, the
sandbox (live), the MCP door, wakes, doors live, before real data, the founder's live
test, cutover.

- [x] Limits audit (founder, 2026-11-02: no tiny limits before he has used the platform): every bound in `platform/*/src` is raised to the tier numbers or a generous default, made an owner setting, or kept as a technical ceiling with its reason recorded. Listed in slice D; applied in slice LIM (founder, 2026-11-02 and 2026-11-03), after the placement rule and the structural changes it needed (ADR-035): `limits-audit.md` holds the values that ship, each classed. The files byte quota is the tier's storage; the model policy's per-run caps are off by default; a lease's tokens live while the lease does. The owner-bound guard's worst case is 42.3% of the 10 GB, 774.1 MB under half (`limits-audit.test.ts`). Owed, flagged in the audit: the collections' and ledger's byte bounds dropped for the tier's figure; the files' history rule counted in bytes (`max_entries` past 30,000); a live check of `limits.subrequests` at 10,000,000; R2 read latency from an owner object, measured live.
- [x] Tier numbers in the platform control record (ADR-034 decision 8): free 5 simultaneous runs and 25 GB, paid 10 and 250 GB (founder, 2026-11-02), editable without a deploy. Slice R: `c:control`'s `tiers` (`doors/src/directory.ts`, parsed by `owner/src/tier.ts`), handed by the binder to the run doors, the owner's tier on its own admission row set by the platform alone (`tierSet`); the simultaneous runs held at admission. The storage figure is the files byte quota's `max_bytes` (slice LIM), kept from every door's binding.

## 1. Verify the unverified facts

- [x] E2B p50/p95 resume for a 1 GiB sandbox, measured live; paused-storage price
  and retention, Pro-plan concurrency, regions. A p95 over 1.5 s switches the
  sandbox to Cloudflare Containers. Measured 2026-10-28: p50 247 ms, p95 706 ms
  over 40 cycles, so E2B stays; paused sandboxes are free, kept indefinitely and
  outside concurrency; Pro 100 concurrent (`e2b-resume-measurement.md`).
- [ ] E2B resume after hours or days paused (only up to 5 min was measured). Slice S: 75 minutes paused, resume to first byte 589 ms, its process alive and its files intact (`sandbox-probe.md`); days remain.
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
- [x] An owner's `grantSet` to `revoked` (the object's set door) revokes no OAuth provider
  grant: only the doors' `grantRevoke` path (`revoke.ts`) kills the client's refresh
  tokens. Route a set to `revoked` through the same provider revoke (rename review N1).
  Settled at slice L's merge with the app slice: the owner surface's `grants/set` refuses
  a set to `revoked` (`400`, the doors spec), so a client is revoked only by `grants/revoke`
  (the object, then `providerRevoke`) or a key by `keys/revoke`; slice L's `setClient`,
  which ran the provider revoke inside the set, went with the second door.
- [x] Live AuthKit wiring (slice L; its live check owed to the founder's deploy): an
  `Upstream` (`doors/src/upstream.ts`) for AuthKit's
  production environment, with its redirect URI `https://tinyassets.io/oauth/upstream/callback`,
  the code exchange and the ID token's signature, issuer, audience and `auth_time`
  against its JWKS; `OWNER_ISSUER` set to AuthKit's issuer (the config guard pins it
  unset until then); production Google and GitHub apps (founder dashboard work). Until
  then the deployed entry refuses every sign-in loudly (`UNWIRED`). No person registers
  WorkOS settings by hand: the deploy kit sets `WORKOS_API_KEY` on the doors Worker from
  the main-only `production` environment's secret store, never from a host (Direction 8;
  the founder moves it there once), and registers the redirect URI with the WorkOS API
  idempotently (`GET /user_management/redirect_uris`, then `POST` only if it is missing,
  never deleting).
  Owner creation by explicit platform admit at sign-up (the binder can't tell sign-up from
  sign-in today); until then a fresh object is created admitted on first sign-in, and
  erasure ends in `deleteAll` plus the `erased` row.
  Built (slice L): `doors/src/oidc.ts`, a generic OpenID Connect code flow with PKCE, as
  data: `OWNER_ISSUER` and the `UPSTREAM` record (endpoints, client id, the identity's
  deletion URL), both `not-configured` as checked in (the config guard), filled by the
  deploy kit (`platform/deploy/deploy.ts`) from the issuer's live discovery, which must name
  `OWNER_ISSUER` exactly, declare `client_secret_post` and https endpoints, and show
  `auth_time` (one real AuthKit token's claims, `OWNER_TOKEN_CLAIMS`, or else the
  discovery's `claims_supported`; an absent field is unknown, and unknown stops the kit),
  or nothing deploys (review, research 3b); the secret
  `WORKOS_API_KEY`. `prompt=login`, `scope=openid` and the PKCE challenge again as the ID
  token's `nonce`. A half-configured Worker throws (`upstreamOf`), and only at sign-in:
  `upstreamOf` is read lazily, so a bad `UPSTREAM` breaks sign-in alone and every other
  door serves (review L1). The deploy kit sets the key and registers the redirect URI
  idempotently; it is not run. **Explicit platform
  admit**: a fresh object is `pending` (migration 10's row; the admission guard's pin
  updated in place, nothing deployed) and every door and lane refuses it
  (`owner_unadmitted`) until `admissionSet {admit: true}` from the reserved caller, which
  the sign-in callback sends (`binder.ts`'s `bindPlatform`, the admin path's one constant,
  `admin/src/caller.ts`) when the directory holds no `o:` record of the owner, then writes
  `o:<owner>` admitted. An owner marked erasing or erased refuses the admit, and the
  callback refuses the sign-in. Owners migrated at cutover are admitted by the import
  (task 9): it admits each imported owner's object through the platform caller and writes
  its `o:` record, as sign-up does, so no imported owner is ever `pending` (review L8). The admit is idempotent and lifts no suspension. Owed live: the
  exact `iss` against a live token, and AuthKit's production Google and GitHub apps.
- [x] The AuthKit upstream refuses an ID token without `auth_time`: there is no clock
  fallback (this replaces the research's "use the binder's own clock"), since the fence
  and the session's 600 s bound both rest on the sign-in's real time. The binder names
  the owner from the token's issuer and subject: AuthKit's subjects stay owner ids as
  today, and a later issuer's (Sign in with ChatGPT) are namespaced `iss-<hash>-<sub>`
  once that provider's slice lists it in `FOREIGN_ISSUERS` (any unlisted issuer is
  refused), so linking a second provider to an existing owner is that slice's own act.
  The AuthKit slice sets `OWNER_ISSUER` to the exact `iss` its tokens carry, checked
  against a live token, since any other spelling refuses every sign-in.
  Slice L: `verifiedIdToken` refuses no `auth_time`, a wrong or trailing-slash issuer,
  another audience, another flow's nonce, an expired or future token, HS256 or `none`, an
  unknown key and a forged body (`doors/test/authkit.test.ts`, a real RSA key).
- [ ] DNS, routes and deploy: the doors Worker's routes on `tinyassets.io` (`/mcp`,
  `/api/*`, `/hooks/inbound/*`, `/app/api/*`, `/authorize`, `/oauth/*`,
  `/.well-known/oauth-*`), the `OAUTH_KV` and `DIRECTORY` namespaces, then the handles
  canary (Fact 11). Cutover (9); every existing connector re-authorizes once, since the
  issuer moves from AuthKit to `https://tinyassets.io`.
  Built, not run (slice L): `platform/deploy/deploy.ts` and the founder's
  `platform/deploy/checklist.md`. It prints its plan and changes nothing without `--yes`;
  creates the three KV namespaces, the bucket and the ring queue when missing; sets each
  Worker's secrets when missing (the routing key on the doors and the owner together or
  not at all); deploys files, gateway, hooks, sandbox, owner and doors with no route from
  their checked-in configs with only the ids and the upstream filled (`guards/deploy.test.ts`);
  writes a closed control record when there is none; registers the WorkOS redirect URI;
  then runs the handles canary. `--routes` adds the routes above and the
  `egress.tinyassets.io` custom domain (the cutover's: they take the paths from
  `tinyassets-mcp-proxy`). Owed: the founder's run; the handles canary's tool set for the
  new door before `--routes`.
- [x] `/hooks/inbound`: the capability-URL and shared-app (Slack HMAC) shapes in the
  binder, the directory's `h:` and `i:` entries, the 1 MiB cap and the `control.inbound`
  gate, and a new owner door `inboundAccept` (config, revision, dedupe, owner-held
  secrets, the breaker). Slice L: `doors/src/inbound.ts` and `owner/src/inbound.ts`. The
  body bound is the platform's one request ceiling (`INBOUND_BODY_MAX` is `API_BODY_MAX`,
  16 MiB), not a 1 MiB of its own; a body past 2 KiB is placed in R2 as mail's is. Shared
  apps are data (`doors/shared-apps.json`: Slack's `v0` HMAC, its five minutes' skew, its
  envelope's fields), proved by the platform's signing secret the record names
  (`INBOUND_SLACK_SIGNING_SECRET`; without it that app's door refuses loudly). An event is
  the owner's mail to the binding's agent, keyed by binding, revision and event id, so a
  retry is deduped by the ledger's own key; a route at an older revision is refused
  `binding_withdrawn`. The breaker (3 in 60 s, the Python's) counts a shared app's
  full-mailbox refusals and no-ops it until the owner changes the binding. Owner surface:
  `hooks/create` (a capability URL shown once), `hooks/list`, `hooks/set`, `hooks/revoke`.
- [ ] The shared app's install flow (Slack's OAuth v2 install, which proves the owner holds
  the workspace) writes the object's `shared` binding and its `i:` entry; until it lands
  no route makes one, and a shared app's event for an install no owner made is a no-op.
- [x] Owner admission and erasure, in the owner's object: an `admission` row
  (`pending | admitted | suspended{billing, abuse} | erased`; `pending` added by slice L), checked in `#bind` at every door
  and by every lane with `require_work` semantics, an erasure tombstone so a stale
  `getByName` never recreates an erased owner, and a new door `admissionSet` taken only
  from a reserved `platform` caller, with a guard keeping that name out of every source
  but `platform/admin`. Built with F1 (tasks 5); the erasure door stays slice 10's. This **reverses** the earlier wording here ("the binder refuses
  an unadmitted owner before it reaches the object, from the directory's admission
  state"), because the object is strongly consistent and already on every path (no
  extra read, no 60 s lag), the alarm reads it locally, and erasure needs an object
  tombstone anyway. The binder keeps the platform gates (`control.ordinary`, the
  canary) from the directory.
- [x] The key store in the owner's object: a new door holding each API key's id, name,
  mint time and last use, so the owner's key list and erasure can enumerate key ids
  (the grant row holds only the key's client hash), the 25-key count is strict, and
  an agent's held API-key request is applied with its grant (`_apply_key_request`).
  Slice L: `owner/src/keys.ts` (`owner_keys`, migration 15); `keyMint` writes the row and
  the grant in one unit, counting live keys strictly at LIM's 1,000 (the owner-set
  clients' bound, held equal by a test); a call by a key marks its last use at most once
  a minute; a revoke or a set to `revoked` marks the row, a forget removes it; `keyList`
  and the owner surface's `keys/list`. A held key request is a policy card of type
  `api_key_request` whose scopes must be the grant's, marked applied in the mint's unit.
- [x] Rate limits, by the founder's rule (no invented limits; slice L, after its second
  review): the platform sets no number on any caller, authenticated or not. The first
  round's per-IP counts (sign-in starts, metadata fetches, failures) are deleted with their
  bindings: each divided an account's included KV quota among IPs, which derives nothing,
  and the failure count refused valid callers behind an MCP host's shared backend address
  (review N1, now two tests that a valid key and a live refresh still succeed after many
  failures from their address). What bounds an unauthenticated caller instead:
  Cloudflare's always-on DDoS protection in front of every Worker; the OAuth provider's own
  per-fetch bounds on a metadata document (public URLs, 5 KiB, 10 s, one fetch out a
  request in), a technical ceiling in `limits-audit.md`; and an owner object the runtime
  reports overloaded answering `429` with `Retry-After`, Cloudflare's documented soft
  limit surfaced. A zone WAF rule with the founder's own number stays an optional founder
  step (`platform/deploy/checklist.md`).
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
- [x] Plan correction (doors section; applied to the plan of record by slice L): "plus the earlier revision for Claude channels"
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
- [x] Owed by 6.1. The file door's bytes: the binder streams chunks to `FILES` itself
  (decided: the binder is the trust root and already reaches every owner's object, so
  its reading an owner's namespace from that owner's object weakens no isolation), with
  the object's `fileUploadBegin`/`fileUploadCommit` around it and the whole-file digest
  (`Repr-Digest`) computed and served as it streams; the object's chunk doors stay for
  in-object callers (the environment, the sync agent's small files).
  Slice L (`doors/src/file-bytes.ts`, after its review, M4): the owner surface's
  `files/upload/begin`, one `PUT files/upload/<intent_id>/<index>` a chunk, and
  `files/upload/commit` (`files/upload/abort` ends one). One request carries one chunk, at
  most 4 MiB, so no request ceiling bounds a file: the policy's `max_file_bytes` does. The
  chunks go in any order and again after any failure; a chunk the intent holds is
  answered without a second put, so an upload resumes. The binder reads the intent's plan
  and the owner's namespace (`fileUploadTarget`), refuses bytes that are not the declared
  chunk, puts a needed one (R2 checks its hash again), reports it (`fileUploadStored`),
  and answers the chunk's SHA-256 as `Content-Digest`, checking one the client sent. A
  refusal mid-upload answers its fixed code, never a 500. Owed: a streamed download, and
  an outside client's file-bytes tool; a binder put racing an erasure's mark leaves its
  chunk in the erased namespace, which the erasure's prefix delete after its wipe takes.

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
  owns the single alarm; 3.4's direct `setAlarm`/`deleteAlarm`
  move onto it. Built (`schedule.ts`): `OwnerDO` holds a Lifecycle (`agents/lifecycle`,
  agents pinned exactly, by lockfile integrity too: 0.31.0, 0.32.0 since A5), never the SDK's `Agent`,
  installs none of its runtime handlers, and its `alarm` only hands the runtime's alarm
  to it. Each kind of timed work is a lane with its own rows and one single-flight
  Lifecycle job, `owner-schedule/<lane>`, derived from them: `wakes` (3.4's wakes and
  revokes), `files` (6.1's housekeeping, with its retention re-apply) and `connections`
  (the `threads` lane went with slice D; slice R added the `runs` lane). Every changing door, and the object's wake,
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
  Lifecycle's handlers, and off Lifecycle's tables.
  Stamped work (owed by 3.5): built as F1's scheduled launches in run threads, which slice
  D deleted with the threads; what stays is the stamp (client, grant generation, sign-in
  time) rechecked with `Grants.standing`, and a held registration holding its client's
  grant row (`held`, slice 3's review N3). Slice R stamps runs the same way. Slice
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
  triggers write: `model_spend` (each source's day and month; a `model_turns` per-turn
  counter went with slice D), so the reservation rows are receipts only. A connection
  keeps up to 1,024 models (the largest built-in catalog, 386, found by a scan,
  connects). Owner policy's optional `models` table: per-run requests, tokens and cost
  (µ$, held at the egress door since slice D), owner-wide and per-source (by source
  record) daily and monthly cost (pi's retries went with slice D).
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
  `usage.ts`: reserve (the policy and the spend read in the unit
  that inserts, output shrunk to what the caps leave, refusals kept as receipts),
  dispatched (re-checked: reserved, the policy generation, the connection's version, not
  aborted), settle (`succeeded` from pi-ai's usage, `failed` when the gateway answered
  with a refusal's status, its own or the provider's before any output, else `unknown`,
  which keeps its reservation), abandon (a dispatch or settle unit that threw), and a dead
  instance's open rows settled by the next reserve or receipts read; `usageReceipts`
  (owner only). `gateway-provider.ts` was A1's (deleted by slice D, which left the
  reservations uncalled until slice R keys them to runs). Owed by 3.5,
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
  ends in-flight turns and closes their tee views was A2's. Slice D deleted the turns,
  their counters and origin, the envelope, the tee and pi's retry naming this item
  describes; the reservation states, the spend counters, the lease, the reseal and the
  revoke stay. Open: the receipts' rows per call, measured live.
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
- [x] The owner app's connect page `/app/connected` (the app slice, not the website's): the
  callback's fragment (code, state, redirect URI) is read and cleared by `history.replaceState`
  in the page's first and only script, a classic one in the head (`doors/app/connected.js`),
  then posted to `connections/oauth/finish` in the tab's sign-in; the page loads no
  third-party script and is served with `Referrer-Policy: no-referrer` and the strict CSP
  (`doors/src/pages.ts`). `/app/signed-in` clears its code the same way. Proved in Chromium
  (`doors/test/app.e2e.mjs`): the instrumented `replaceState` saw the fragment while the
  page held only that script, the finish carried no referrer, the connect committed.
- [x] Slice SIGNIN: subscription sign-in inside the sandbox, and the past runs.
  - The flow, as data from each record's `cli.login`, no vendor's name in code: the owner's
    `signin/start` (its own door; the owner's own sandbox or one client's, for that client)
    opens the sandbox's lease with no run (or joins the live one) and the sandbox Worker
    starts the record's login there (`ta-runner login-launch`) as a pool user, on a terminal
    of its own (util-linux `script` in the VM, so the raw terminal never leaves it), its home
    from the engine's store. Only the record's shapes leave the VM: the record's pinned page
    (`cli.login.url`: its hosts among the login hosts and the chosen region's, its path, its
    fixed parameters exactly, its free ones unreserved values, no other parameter, so no other
    OAuth client's page and no redirect chain), a device code of at most 16 letters, digits
    and hyphens; a credential-shaped value is refused as a second line, in the VM (with the
    stores' secrets) and on the trusted side. The owner's object checks each prompt again
    outside the VM (`signinShape`) before it keeps or relays it, and stops a sign-in whose
    prompt is not the vendor's (a hijacked engine shows no look-alike page). Frames carry a
    sign-in's fixed states only (`SIGNIN_STATES`, a schema CHECK too), the URL and code, and a
    refusal's fixed reason (`SIGNIN_REFUSALS`), which the app says in plain words: no text
    the VM chose leaves it. Frames go to the app's socket by the sign-in's id (`ta.signin`),
    the app showing the vendor's page's host in words; a pasted code (one line, at most a
    Linux terminal line, 4095) goes on a door held to the owner's session, only to a sign-in
    whose record takes one and that is showing its prompt, through the Worker's upload, read
    and deleted at once (dropped if the hand-over fails, and any left by a pause deleted by
    `prepare` at the resume), over a socket to that login's stdin alone, never logged or
    stored, and refused once the login ended. The end: the secrets kept in the engine's
    store, presence reported (`status_argv`, or `state_checks` for a record with none). A
    sign-out runs the record's `logout_argv` first, then deletes its `secret_paths`.
  - An open sign-in's bound is each record's `cli.login.expires`, cited: Codex 900 s (its
    CLI's own text), Gemini 300 s and Kimi 900 s (their pinned CLIs' own timers): the
    vendors' own. Claude's 600 s is the platform's own bound on an open sign-in (from its
    start, so the owner's password and 2FA too), since an open sign-in keeps a metered
    sandbox up: Anthropic documents none and the CLI sets no timer, and RFC 6749's 10-minute
    code lifetime is context only (`limits-audit.md`); the time a real sign-in with 2FA takes
    is owed to the founder's live test, and sets it if too tight. The runner stops the login there,
    and the owner's object ends the sign-in `expired` one lease TTL past it however its
    runner beats, stopping its CLI and freeing its lease; the app then offers to start again.
  - A secret goes back to the store by compare-and-swap under a per-engine store generation:
    only one the run changed, only while the store still holds the copy the run started
    from, and only in the generation its home was made in. A completed sign-in that writes
    new secrets, and a sign-out, each move the generation on, so a run live across one never
    undoes it; one that only began, failed or was stopped moves nothing, so it never discards
    a live run's refresh. A sign-out also stops that engine's live runs in the sandbox. Every
    home's snapshot goes with the home. The store's lock names its holder with its boot (a
    pid repeats after a reboot), is waited on without blocking the runner's heartbeats, and
    for at most a command's bound, then the call fails loudly.
  - Each source's sign-in sits behind its own switch in the control record (`signins`),
    defaulting to its record's verdict on the vendor's terms for hosted use
    (`cli.login.hosted_use`, cited): Claude Code `allowed` (on); Codex, Gemini and Kimi
    `unclear` (off). Turning one on is the founder's call. The doors hand the switched-on
    records with every binding, as they hand the tier numbers, and the owner's object holds
    to them on every path: a sign-in (never a sign-out: a login is always removable), a
    pasted code, and a run's choice of source (a named engine refused `signin_off`, never a
    silent key; `auto` passes over a switched-off plan). The runs lane reads the control
    record itself (`ControlGate.signins`, fail-closed) at each pass, so a source switched
    off stops powering a schedule's or a mail's run though the owner makes no door call.
  - A standing check: every template build ends by running each record's login up to its
    prompt on a throwaway sandbox under the production egress allowlist (the egress door
    alone, widened by that engine's own hosts), `ta-runner login-probe`, `build.py
    --check-logins`, checking its pinned page and that it never sends the browser to
    localhost.
  - The past runs: `runs` (owner app) and `runList` (the object's door, held to the
    caller's grant: an outside client sees its own), newest first, a page at a time.
  - Owed: a completed sign-in on each vendor (the founder's account, his live test); the
    region a Kimi run's provider hosts need, kept from the sign-in for its runs; "last used"
    on the source card; an MCP tool for the past runs; resume after 14 and 30 days paused.
- [x] The owner app, first version (slice APP), in workerd, nothing deployed. **Where it
  lives (decided):** the doors Worker serves `/app` from its own files (`doors/app/`, Workers
  Static Assets bound as `ASSETS`, the Worker first on every request, `doors/src/pages.ts`),
  not the website's static export, because the `__Host-` Strict session cookie and the CSRF
  rule ride only same-origin posts to `/app/api`, the return pages are the doors' own
  constants (`WEB_REDIRECT`, `CONNECTED_PAGE`), the pages need headers GitHub Pages cannot
  set (`Referrer-Policy`, a strict CSP with `frame-ancestors`), the static export's runtime
  injects inline scripts a strict CSP refuses, and the pages ship in one deploy with the
  doors they call. The routes slice adds `tinyassets.io/app*` to the doors' routes. **What
  it is:** plain HTML and modules, no framework, no build, no third-party request, no HTML
  string sink (Trusted Types on). Screens: sign-in (PKCE, our own authorization server),
  home (the plan: tier, runs at once and storage, both tiers' numbers, nothing else;
  notices), agents (made as two files, `agent.json` and `AGENTS.md`, then edited as files;
  talk, the live run over the socket ticket, stop), rooms and board (the digests, a post),
  model sources (from the records: a key, OAuth, and what is not here yet), connected apps
  (code mode's switch, the owner's custom tools, each client's profile, disconnect), API
  keys (make, shown once; turn off), your data (files, export), and each main agent's
  wake-up. **New doors:** the owner's `tierRead`; the owner-surface routes `account`,
  `notices`, `policy`, `policy/write`, `grants`, `grants/set` (never a revoke), `files/read`,
  `files/write`, `sources`, `capabilities` (the grant capabilities in plain words, which the
  agent and key forms show, none chosen for the owner), `tools`, `tools/<name>` (the table,
  the owner as itself) and `agent/doorbell/kinds`; every owner-surface answer carries
  `nosniff` and a CSP of none. **Who changes the agent trees** (`OWNER_TREES`: agents'
  `agent.json`, instructions and skills; write, upload, mkdir, rename, remove, restore). The
  founder's rule (2026-11-03): every agent of the owner has full power by default to do what
  any other can, so an outside client under its default full grant, a run, and a sandbox
  sync change them; a grant the owner narrowed (`scoped`), its runs and its sync never do
  (`AGENTS_OWNER_ONLY`; the object and `Files.syncCommit`, by the client's grant as it stands).
  The owner's own app is a human's path, so its bearer token alone changes nothing there: a
  widening in the object, on every route, taken only in the `__Host-` session (review M1),
  against a leaked app token. The other `tools/<name>` need no session. This replaces the
  earlier rule that only the owner changed those trees. Owed to slice S's follow-up: the
  runner still projects a client's run's agent definitions read-only on its sandbox's disk,
  so a full grant's client run edits them through its MCP door's file tools or the owner's
  doors, not its CLI's own file tools, until the runner reads the client's grant too. A new agent is its `AGENTS.md` first and its `agent.json` last, the one
  write that makes the agent. Tests: `doors/test/app-pages.test.ts`, `app-routes.test.ts`,
  `guards/app-files.test.ts`, the config guard's `ASSETS`, `owner/test/agent-doors.test.ts`
  (the session on every agent-file door, receipted), and the browser test
  `doors/test/app.e2e.mjs` (`npm run test:app` in `platform/doors`, and the `platform-app`
  CI job, which `required-tests` needs with `platform-tests`, each to succeed (before
  this slice a red platform job merged): Chromium at the Playwright version `platform/package-lock.json` pins, its network
  mapped to the doors in workerd over HTTPS, so its cookies, `Sec-Fetch-*` and socket are
  the browser's own; it fails, never skips, without Playwright or Chromium): sign-in, a key
  connect, an OAuth connect through `/app/connected`, an agent made, a run started and its
  live frames and result seen, every screen opened, and the review's attacks kept as tests
  (`pages.ts` probes, the CSP's refusals, payloads through every rendered field, a malformed
  hash, framing, CSRF, a stranger's code, a leaked token alone, a missing tool's real 404),
  no third-party request. `replaceState` clears the code from this tab's history only; the
  browser's global history keeps the return URL, harmless as each code is single-use and
  PKCE-bound. Only the doors this app knows are missing say "not here yet" (`MISSING` in
  `app.js`), each before anything is clicked; any other refusal, a 404 included, is shown as
  the doors gave it. Owed by other slices:
  - **the sandbox sign-in** (a Claude or ChatGPT subscription signed in inside the sandbox):
    no door starts a record's `login.argv`, relays its URL to the app or takes its code back
    (slice S checks `status_argv` only). Owed: an owner door that starts the login run, its
    URL relayed as a frame on the app socket, and a door that hands the pasted code to the
    runner; the app shows it on the record's card.
  - **wake-ups** (the Claude `/fire` and Grok routine tokens), served since slice W landed:
    Connected apps lists the main agents that joined, each with a wake-up form from the
    records (`agent/doorbell/kinds`: the kinds whose `push.callback` is `owner`) posting W's
    `agent/doorbell`. Owed to the live test: a paste for a really registered main agent
    (W's `events.test.ts` covers the route; the browser test has no main agent to wake).
  - **export**: slice P's owner export door; until it lands the Export section says so, with
    no button, and the app's `MISSING.export` goes when it does.
  - **the key store** (slice L): the key list is the grant rows (names, no ids), so a listed
    key is turned off by `grants/revoke`, which for an API key's client also deletes its
    directory entry, as `keys/revoke` does (`revoke.ts`, slice L's merge review). Delete and
    last use come with L's list route (`keys/list`: id, name, mint time, last use, revoke
    time), which landed with slice L's merge; owed: the app's API keys screen reads it and
    deletes a key by `keys/delete`.
  - **a run list**: the sign-in slice (`platform-signin`) adds the door; until then Talk says
    plainly that only this tab's conversation is shown and is not kept across reloads.
  - **the sandbox sign-in**, again for the forms: no credential `vendor_login_in_sandbox` is
    offered for a new agent until the sign-in slice lands, and an engine that has only that
    credential is named as unable to run yet.
  - **the runner tests' failures (review N3, slice S's suite, fixed here):** one to seven
    different runner tests failed a run whenever two runner suites overlapped on one host
    (two worktrees, or a rerun beside a run). The harness gave every suite the same fixed
    uids (4243, 4250, 4300 to 4303), and real uids are the host's: the runner's `killAll`
    and `take` in one suite killed the other suite's CLIs and status checks mid-test. Each
    suite now claims a block of ids of its own under a host-wide lock
    (`sandbox/runner/harness.ts`, `claimIds`); the runner reads its ids from the sandbox's
    `/etc/passwd`, so the runner and its template are unchanged. With that gone, two pairs
    of overlapping suites on a loaded host still showed three timing assumptions: two runs
    "at once" held live by a 1.5 s sleep, and two renewal tests whose CLI was to fail or
    stay up across the renewal by a 3 s and an 8 s sleep. Each is now held by an event (the
    fake CLI's `holdUntil`, a file the test writes when the step it waits for is done), and the
    fake's record of each call is written whole and renamed into place, so a test polling it
    never reads half of one. Proved:
    the old harness, two suites overlapping, 3 and 7 failures; the fixed one, ten rounds of
    two overlapping suites, 32 of 32 in each. CI never showed it: of the last 40 Tests runs,
    `platform-tests` failed twice, both PR branches' own bugs at the time (platform-s, all 32
    runner tests in 0.3 s; platform-id, one agent-mail test), since a GitHub job runs alone.
  - **run events (found by the review, outside the slice, fixed here):** a session id outside
    the conversations' shape (`[!-~]{1,256}`) failed the runner's whole post with a SQLite
    error; the object refuses it as the run's fixed error `session_id_refused` (`runs.ts`), its
    frames relayed. The check is the object's, not `run-events.ts`', so the runner's bundle
    and the template tag stay as slice S built them.
- [x] Each RFC 6749 source's provider registration of its own redirect URI,
  `https://tinyassets.io/oauth/connect/callback/<source-id>`, registered exactly, never as
  a prefix, and matched exactly by the provider (RFC 9700 §4.1); OpenRouter's key exchange
  takes any callback: `.../callback/openrouter_user_models_v1/<state>`; Sign in with
  ChatGPT's hosted client and registration once OpenAI approves (founder), which then
  drops its record's `unwired`. Slice L: as data, one registration a source in
  `doors/redirect-registrations.json` (`exact` at its own callback, or a key exchange's
  `any_callback` with its state's segment), kept apart from the source records the sandbox
  runner bundles, held by a guard to the URI the doors derive (`redirectOf`), listed for
  the founder in `platform/deploy/checklist.md`. Owed: the
  registrations at the providers themselves (only `chatgpt`, parked, needs one today).
- [x] The key exchange's residual mix-up, before any uncurated source: a source on an
  authorization server the platform does not curate (an MCP server, or a source from an
  owner's package) must not share these connect flows while a key-exchange source such as
  OpenRouter (no `state`, no `iss`) can be connected; that source's own slice solves it
  (for example a separate flow family, or no key exchange beside it) before such sources
  are allowed. Slice L: a record may be marked
  `curated: false`, and `catalogOf` refuses any catalog serving one with OAuth beside a key
  exchange, naming the mix-up (`guards/registrations.test.ts`).
- [x] Append-only migrations from the first deploy: once a migration has run on a live
  object it is never edited (G3 edited migration 6 in place only because nothing is
  deployed), held by a guard that pins each deployed migration's text by digest. Slice P:
  `platform/owner/migrations.json` (`first_deploy`, `deployed` SHA-256s over each
  migration's statements as the code evaluates them) and `owner/guards/migrations.test.ts`,
  which fails a pinned migration changed or gone and anything pinned before the marker;
  today's migrations (slice S's column folded in) squashed into one baseline, checked equal
  to the old chain by a one-off workerd comparison. Each object's start also refuses to run
  when a migration it applied reads differently now (`schema_digests`, Flyway's
  `validate`), which catches what the repo's guard cannot (a hotfix branch, a revert), and
  refuses a migration it applied whose digest row is gone (it never passes by default).
  The lock itself only grows: the structural guard `tests/test_migration_lock_append_only.py`
  holds `migrations.json` against `origin/main` (the base's first-deploy marker never
  changes; the base's pins stay the head's first pins, in order), so a change that edits a
  deployed migration and its pin together fails in review. Until the marker is set, slices
  still edit in place.
- [x] The deploy kit holds the migration lock (slice P, review M5-2): mark the first deploy and pin each deploy's migrations,
  `PIN_MIGRATIONS=<commit> npx vitest run --project guards guards/migrations.test.ts` in
  `platform/owner` (it refuses to pin a changed one), committing the lock with the deployed
  code; and refuse to deploy the owner Worker unless the lock pins every migration it
  ships. The exact check, run on the commit being deployed before `wrangler deploy`:
  `node -e` reading `platform/owner/migrations.json` and the code's `MIGRATIONS` (the
  guards project's import of `owner/src/schema.ts`), and failing unless `first_deploy` is
  set and `deployed` equals `MIGRATIONS.map(migrationDigest)` element for element (same
  length, same order); that is, the guard test passes and
  `lock.deployed.length === MIGRATIONS.length`. Done: `platform/deploy/deploy.ts`
  `lockRefusal`, checked in every mode before any change, its digests from the guards
  project (`MIGRATION_DIGESTS`), refusing an unmarked lock, a migration unpinned, gone,
  changed or reordered (`doors/guards/deploy.test.ts`). Today's lock is unmarked, so the
  kit refuses until the founder pins at the first deploy's commit (the checklist).
- [ ] Mobile connects and sign-in (the app slice): on mobile the OAuth runs in an external
  browser (RFC 8252), whose cookies are not the WebView's, so a binding cookie set in the
  WebView never comes back with the callback. **Decided (app slice, 2026-11-03, corrected
  by its review M2 and the in-flight research §4b), built with the store app (web first):**
  the store app is the Capacitor shell that already loads `https://tinyassets.io/app`
  (`mobile/capacitor.config.json`), so the doors Worker serving `/app` keeps it working.
  - **The browser.** Every OAuth runs in the platform's auth session, never an embedded
    WebView, with PKCE.
  - **The return path.** The auth session's own https callback hands the final return page
    straight to the app that began the flow:
    - iOS 17.4 and later: `ASWebAuthenticationSession` with
      `Callback.https(host: "tinyassets.io", path: "/app/signed-in")` (and `/app/connected`);
    - Android: Auth Tab (`androidx.browser` 1.9.0 `AuthTabIntent`, https host and path),
      verified by Digital Asset Links.

    The doors serve `/.well-known/apple-app-site-association` (`webcredentials`) and
    `/.well-known/assetlinks.json` for them. The doors' own callbacks
    (`/oauth/upstream/callback`, `/oauth/connect/callback/*`) are never a return path: they
    run in the auth session's browser, which holds each flow's binding.
  - **The fallbacks.** The shell's existing pieces:
    - below iOS 17.4, its `ASWebAuthenticationSession` plugin with the `tinyassets://` scheme
      (`mobile/native/system-auth/ios`; RFC 8252 §7.1 private-use, PKCE as always);
    - on Android without Auth Tab, its loopback catcher (`mobile/native/android/
      LocalCallbackPlugin.java`, RFC 8252 §7.3) in Custom Tabs.

    Each fallback is a redirect URI of the mobile client's own CIMD document, never a
    prefix.
  - **Which client it is.** The mobile client is named by its own first-party client id
    (`/oauth/clients/ios.json`, `/oauth/clients/android.json`, beside `web.json` in
    `FIRST_PARTY_CLIENTS`), from the verified token, never by a header or a query flag.
  - **Sign-in.** The app opens `/authorize` in the auth session, which sets the provider's
    upstream binding there, and the callback checks it there, as on the web. The callback
    cannot set the WebView's session cookie, so for a mobile client it hands the return page
    a single-use session handoff (at most 60 s) instead. The handoff is bound to the
    sign-in's PKCE: the app redeems it on a same-origin route only with the verifier it
    alone holds, and that route sets the Strict cookie in the WebView. A handoff that lands
    anywhere else is worthless.
  - **Connect.** `connections/oauth`, for a mobile client, answers a single-use launch URL on
    our origin (`/oauth/connect/start?ticket=`) instead of setting the binding cookie in the
    WebView. It is routing-MAC'd, short-lived and bound to the flow begun in the owner's
    session.
    1. The auth session opens it; it sets the flow's `__Host-ta-connect-*` cookie in that jar
       and redirects to the provider.
    2. The callback's binding check holds there as on the web, and it hands the code to
       `/app/connected` in the fragment, which the auth session returns to the app.
    3. `connections/oauth/finish` takes it in the owner's session (`flowFinish`, in the
       session that began it). For a mobile client its binding check is the one made at the
       callback.

    No binding value ever sits in a URL or leaves the app's memory.
  - **Where a connect returns** (review N2). The callback's return target is the flow's
    client's, recorded on the flow at begin (`connectionPrepare`), never read from the
    request: the web client's is `CONNECTED_PAGE`, a mobile client's is the return its auth
    session was opened with, one of its CIMD document's redirects:
    - iOS 17.4+ and Android Auth Tab: `https://tinyassets.io/app/connected`, the fragment
      handed to the app by the auth session's https callback;
    - below iOS 17.4: the shell's `tinyassets://app/connected` scheme (its
      `ASWebAuthenticationSession` plugin's `callbackURLScheme`), the fragment intact, since the
      system hands the app the whole URL;
    - Android without Auth Tab: the shell's loopback catcher,
      `http://127.0.0.1:<port>/app/connected` in Custom Tabs. A fragment never reaches a
      loopback server, so for this target alone the callback puts the code and state in the
      query. That is safe there: the code needs the flow's PKCE verifier and the owner's
      session that began it, and the port is the app's own, open for this flow only.

    In each case the shell's plugin hands the URL to the app's `/app/connected` script,
    which finishes in the WebView's session as on the web.
  - **Owed by the store app beyond these** (App Store review guidelines):
    - Sign in with Apple, enabled in AuthKit, whenever Google or GitHub sign-in is offered
      (4.8);
    - deleting the account from inside the app, the erasure door reached from "Your data"
      (5.1.1(v); slice L wires the door);
    - the shell's native features (notifications, voice) kept, so the app is more than a
      repackaged website (4.2).
- [x] G4 (slice 8a) The egress door, the sandbox's identity, its spend and its network
  document, in workerd, nothing deployed and no E2B call; 8b (the sandbox) only calls it.
  The door is the main path for API-key model use: a provider CLI in the sandbox (or pi,
  one optional CLI there) reaches its provider through it with a placeholder key.
  **Identity**: E2B workload identity is a private beta, so v1's is our own. The owner's
  object mints two tokens per sandbox lease, each `<owner>.<expires>.<secret>.<mac>`: the
  owner id as the binder already names owners (`getByName`; AuthKit's opaque `user_…` id),
  base64url; the run's deadline in Unix seconds (the run's own, which 8b hands
  `SandboxLeases.open` as `deadline` for each run, at most `RUN_DEADLINE_S`, a day, which
  is also the default);
  256 random bits; and HMAC-SHA-256 under the routing key over the token's kind, owner,
  expiry and secret (`doors/src/routing-mac.ts`, the one module both Workers use). Past
  its expiry the door refuses a token `egress_token_expired` without reaching any object. The routing key,
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
  the model reservations move too: the model policy's caps, no more and no less (the spend
  table, `ask_above` among it, is the agent's purchases', never a model call's). Each open
  prunes the spend counters past their window, as `usage.ts`'s reserve does, so an owner who
  uses only the sandbox never fills them. **8b opens a lease per engine run**, so a run's
  caps are per run, not per sandbox life, and sets each run's tokens to expire at that
  run's own deadline (a closed lease's token is refused by the object until then). Nothing of
  it reads or imports pi's engine (a policy-boundary guard holds its runtime imports free
  of `usage.ts`, the thread host and pi-durable, read by the TypeScript compiler, every
  import resolved or the guard fails), so it outlived the in-object loop, which slice D
  deleted. **The
  reservation rule**: the call is reserved for the model it names, read where its API's
  rules say (since PR 3a the gateway's one parse reads the body's `model` and tier and asks
  `egressReserve` through the door's `reserve` callback; a path names Google's), refused
  `egress_unknown_model` when the connection does not list it, and priced at that model's
  dearest rates (input at the dearest of uncached, cache-read and cache-write) by pi-ai's
  `calculateCost` over the catalog's records: the input held at that model's context
  window (a body's bytes do not bound its tokens: a file, a cached context or a prior
  response it names can be far larger), and the output at that model's cap and what the
  caps leave, which the gateway clamps every output-cap alias the API reads to; the
  one parse is the only reading of the body, so no other model or tier can be sent than the
  one reserved, and a key the rules read spelled another way is refused
  `egress_aliased_key`; a call asking several choices (`n`, `candidateCount`)
  is refused `egress_multiple_choices`, and one enabling a server-side tool the provider
  bills beyond tokens (a web search, code execution, a hosted MCP connector, a plugin) is
  refused `egress_unmetered_tool`, by each API's data (`gateway/src/api-rules.ts`). **Price
  beyond tokens**, per field, by whether pi-ai prices it: the Responses API's
  `service_tier`, which pi-ai prices from the answer, is reserved at the most pi-ai
  applies for its value (`priority` and `auto` ×2.5, `default` and `flex` ×1, any other
  refused), and a call naming none at the dearest its API allows (×2.5: the account's own
  default may be priority; the settle still charges pi-ai's true price). The rule is
  general: a price-raising field left absent, where the provider's default may vary by
  account, is reserved at its dearest allowed value, or, where pi-ai prices no value but
  one, set to that one (`default_when_absent`: Anthropic's `service_tier` is sent
  `standard_only` when a call names none, as Claude Code's do, which changes only a
  priority-commitment account's speed through TinyAssets, never its price); and an answer
  that reports a served tier pi-ai does not price (`served`: Anthropic's
  `usage.service_tier` other than `standard`; chat completions' `service_tier` other than
  `default` or Groq's `on_demand`) settles `unknown`, its whole charge kept. The residual:
  a chat-completions provider that reports no tier in its answer. That is safe only
  because those providers' paid tiers are request-opt-in, and requests naming a tier are
  already refused (`egress_unpriced_option`). A Responses answer that reports no tier
  settles at the absent multiplier (×2.5), not ×1, failing closed; its tier is read from
  the terminal event alone (`response.completed`, `response.incomplete`; the rule's
  `events`), as pi-ai reads it, since `response.created` echoes what was asked (`auto`). The `served` scan
  decodes SSE as its specification does (CR, LF and CRLF endings, multi-line `data:`
  fields). Provider beta headers (`anthropic-beta`, `openai-beta`, any `*-beta`) are
  stripped from every metered call unless the API's rules list one its reservation
  accounts for (`betas`; none today), since a beta can widen a call past its
  reservation (a longer context); every option pi-ai does not price is refused `egress_unpriced_option`: chat
  completions' `service_tier`, `modalities` beyond `text` and `audio`; Anthropic's
  `speed` but `standard`, `inference_geo` but `global` and `service_tier` but
  `standard_only`; Google's `responseModalities` beyond text, `speechConfig` and
  `imageConfig` (an image, audio or agentic answer is priced at no text rate). So the
  charge bounds what the call can cost and the settle gives the difference back; refused
  below 1,024 output tokens. With the defaults a run's cost cap holds a few calls of a dear
  model at once, and many of a cheap one: fail-closed, and CLIs mostly call one after
  another; **owed to 8b's live measurement** of each CLI's real concurrency. An endpoint pi-ai cannot meter
  is refused, since a run's caps are always set (the stricter of the two rules). Then the
  gateway's `EgressGateway.forward`, **metered** (`gateway/src/meter.ts`): pi-ai's adapter
  for the sealed API, run once against a fetch of ours (no network, the credential's own
  secret handed in as the loop hands it), says which method and path its model call is,
  where the model id goes and which body field caps the output; the gateway reads the
  request's model there, finds the catalog's record, clamps the output cap, refuses a body
  past the reserved length, forwards, and tees the answer through the same adapter (pi-ai's
  own parser) to read its usage; a streamed call is made to ask for its usage report as
  pi-ai's own call does (`stream_options.include_usage` for chat completions), and an
  answer with none (zero tokens) is `unknown`. The usage is priced at pi-ai's own
  `usage.cost.total` for the model the call named (every multiplier pi-ai applies, a
  service tier's among them), or a dearer served model (pi-ai's `responseModel`) scaled by
  the same ratio, so a router (`openrouter/auto`) or a fallback list never settles cheaper
  than what served. A served id the catalog does not list settles at the named model's
  price only when it is that model's dated or versioned snapshot (the named id, a
  separator, then a date or a version: one pattern, `snapshotOf`) and the named model is
  concrete (priced, no fallbacks), the served id kept on the receipt; anything else
  uncatalogued is `unknown`. A model priced
  at nothing that names no other is `unknown` too. The outcome comes back on a second
  stream. With no metering (a read) it forwards `GET` and `HEAD` alone
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
  says so (`BOUNDARY_NOTE`, every network plan's `note`). **Placeholders** (`sandboxEnv`;
  since slice E's review, one engine run's, its own credential's alone):
  `<PROVIDER>_BASE_URL`/`_API_KEY` for each provider's oldest connection (Claude Code's
  `ANTHROPIC_*`, Codex's and Qwen Code's `OPENAI_*`, `MISTRAL_API_KEY`), `TA_<CONNECTION>_*`
  for every one, and each CLI's own names from its source record (slice E's
  `api_key_env`), such as Gemini CLI's
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
  IPv6 (`denyOut` holds IPv4's all-traffic only); whether calls on frontier models
  serialize under the default caps (a 1M-window model's charge holds most of a $25 run
  cap, so parallel calls wait); whether Gemini's `thinkingBudget` counts inside
  `maxOutputTokens` (if not, its output can pass the reservation, settled at the real
  cost); that a request in flight across
  `updateNetwork` carries the old generation; and each CLI's base-URL variables (the
  subscription research names only Grok's) against the CLI itself. **Out of v1**, each
  waiting for a TLS-terminating proxy (a founder call: a host, against Direction 8) or a
  tool's base-URL support: non-model credentials (GitHub, Notion and the rest), git through
  credentials (git is the file plane's and the sync agent's), WebSocket APIs, and SigV4; a
  model API's other billed calls pi-ai does not make (`countTokens`, embeddings),
  refused `egress_unmetered` until a reviewed record marks one unbilled (slice E marked
  Anthropic's `count_tokens` free, on Anthropic's page); and
  a non-streaming model call's answer: pi-ai's adapters parse their stream only, so a
  JSON answer has no usage pi-ai can read and settles `unknown`, its whole charge kept. The
  `anthropic`, `openai` and `xai` API-key records landed with slice E (§5), each metered by
  `api-rules.ts` (a test holds every API-key source's model call to its sealed endpoints).
## 5. Agent

ADR-034 replaced the in-object pi loop (sub-slices A0-A5, 0b, S1-S5, F1, H0-H1): agents
run on unmodified engines, and the work from here is `engines-replan.md` §5's slices E,
R, S, M and W.

- [x] D Deletions (ADR-034; `engines-replan.md` §1.7), in workerd, nothing deployed: the
  in-object pi loop (`agent.ts`, `files-env.ts`, `ta.ts`, pi's part of `authority.ts`
  and `owner-do.ts`), the thread stores and host (`thread-store.ts`, `remote-storage.ts`,
  `thread-host.ts`, `threads.ts`, the `PISTORE`/`ThreadStore` binding and class), the
  gateway provider's model calls (owner `gateway-provider.ts`; gateway `messages.ts`,
  `encode.ts`, `envelope.ts` and `POST /v1/messages`), per-model-call turn identity
  (`origin.ts`, `usage.ts`'s turn counters), `ta.scheduled`, `agent:delegate`, the skills
  section and pi packages (`resources.ts`), the live tee, and the remote hook wrappers
  (owner `hooks.ts`; hooks `runner.ts`, `protocol.ts`, the Worker Loader). Removed
  dependencies: `@earendil-works/pi-durable` and `@earendil-works/chord` (`pi-telemetry`
  stays only as pi-ai's own dependency). The schema rewritten in place, 11 migrations; the
  owner-bound guard's worst case 40.6% (4,064,822,714 bytes). The four agent doors,
  `usageMetrics`, and the `converse`, `run_graph`, `run_status` and `run_cancel` tools
  and `/app/api/agent/*` routes are absent until slice R. The policy loses `packages`,
  `runs`, `models.retries` and the hooks' `timeout_ms` and `cpu_ms`; `hooks.codemode`
  stays. The erasure's order is now: mark, end every run, wipe. The limits audit is
  `limits-audit.md`. Review round 1 (REQUEST_CHANGES), all taken: the cron tests carried
  over (`cron.test.ts`, H1) and its requirement restored; the erasure's resume by its own
  job, by its start alone, from a refused door at most once a retry interval, after a wipe
  that failed or hung, and its alarm disabled before the wipe (H2); the audit's raises
  costed by the guard's own `termsFor`, all together, and held under half by
  `limits-proposed.test.ts` (48.3%), the draft's that do not fit named with the
  structural change each needs (H3); `outside_agents_stamped` restored (M1); code mode's
  switch made slice M's to enforce (M2); the `auto` and notices requirements restored (M3);
  the audit's classes and missed bounds (M4, M5); the hooks Worker's name check tested
  (M6); `Files.watch` and `Notices.once` removed, and the stale comments (L1). Mutation:
  the review's 20 and 3 of mine, all 23 killed.
- [x] E Engine records (`engines-replan.md` §5 row E), data plus its checker, nothing run.
  `CliSpec` (`doors/src/sources.ts`) is an engine (ADR-033 decision 2): `engine.argv` and
  `resume_argv` (by `{sid}`, or `continue_in_cwd` where the stream names no id), event
  matchers (session id, final text, error, usage, tool calls), `instructions` (the
  `AGENTS.md` mapping, what shadows it, any cap) and `skills_paths`, `mcp` (a flag or file
  template carrying `{mcp_url}`, with its trust handling, or `unsupported` with the reason),
  `tools` filters, `unattended_argv`, `home_env` under `{home}` (sandbox-only, outside
  `/workspace`), `credential` (`vendor_login_in_sandbox`, `api_key`), `unattended` per
  credential (`allowed: true` only with `cite` and `read`; `false` with the reason),
  `per_credential` (argv, env, files, and arguments it forbids: Claude's `--bare`),
  `api_key_env` (each provider's base-URL and key variables, which `sandboxEnv` now takes
  from the records, `CLI_ENV` gone), `no_update`, `install.manager` npm, pip or a binary by
  digest, and `secret_paths` and `session_paths` in place of `state_paths`. The checker
  refuses unknown keys, `invoke`, a secret path inside `/workspace` or overlapping a
  session path, an uncited `allowed: true`, unknown placeholders and `{sid}` outside the
  resume argv. Records: Claude Code 2.1.309 (npm `stable`; `latest` is 2.1.316), Codex
  0.166.0, Gemini CLI 0.66.0, Kimi Code 2.4.1, and pi's CLI 1.4.0 (API key only), each
  pinned by a lock committed in `platform/sources/locks/<id>/`; Claude's `hosts.provider`
  gains `platform.claude.com`. Unattended: Claude Code allowed on a subscription and a key
  (code.claude.com/docs/en/legal-and-compliance, 2026-11-01); Codex, Gemini and Kimi
  refused on a subscription with the reason, Codex and Gemini allowed on a key. New
  `anthropic`, `openai` and `xai` keys; every key names its `engines`. `chatgpt` parked.
  Kimi has no key credential: no Moonshot API-key source and no read page for one.
  Review round 1 (`ta-work/rve_report.md`), each against the pinned tarball:
  - Every path a record names is under `{home}`, so secret and session paths compare in
    one namespace: an absolute root, `/proc`, `/workspace`, `..`, a `*` first segment and
    `{home}` itself are refused (H1). The reviewer's attacks are a guard,
    `doors/guards/sources-attacks.test.ts`.
  - pi has its built-in MCP client: `{home}/.pi/agent/mcp.json`, a user-level server that
    needs no project trust; `--approve` loads the agent's own `.pi/skills` in JSON mode;
    `PI_SKIP_VERSION_CHECK` (H2).
  - More credential files among `secret_paths`: Claude's `.claude.json`, Codex's
    `.credentials.json`, Gemini's `mcp-oauth-tokens.json`, `a2a-oauth-tokens.json` and
    `gemini-credentials.json`, pi's `mcp-auth.json` (M1).
  - Kimi's sign-in takes the owner's region as data (`regions`, `{region}`:
    `mainland-cn`, kimi.com, or `global`, kimi.ai, each with its hosts), and
    `KIMI_CODE_NO_AUTO_UPDATE` (M2).
  - A prompt goes on stdin (Claude, Codex, Gemini, pi) or joined to its option (Kimi's
    `--prompt={prompt}`); the checker refuses it bare or as two tokens (M3).
  - No argument, env value or template configures an engine to nothing (an empty value,
    list or map) (M4).
  - A lease opens one engine run (`SandboxLeases.open`'s `run`: engine, credential, an
    API-key run's connection), and its environment carries that credential alone
    (`sandboxEnv`, `runEnv`): a signed-in run gets no key or base-URL variable, and each
    record lists what its sign-in forbids (`per_credential.vendor_login_in_sandbox.forbid_env`,
    its `api_key_env` names among them) (M5).
  - A call its API's rules cite as free goes unmetered on a sealed endpoint
    (`api-rules.ts` `free`): Anthropic's `count_tokens`, "Token counting is free to use"
    (platform.claude.com/docs/en/build-with-claude/token-counting, 2026-11-02), sealed on
    the `anthropic` key. Gemini's `countTokens` stays refused (`egress_unmetered`):
    ai.google.dev is outside this session's network, so no Google page was read that says
    it is free. Codex's web search stays refused `egress_unmetered_tool` (M6).
  - Lows: the install-scripts guard is "iff" over the whole lock (Gemini, Kimi and pi
    declare `lifecycle_scripts`); a key naming an unwired engine is refused; Gemini's
    refusal reason says Google's terms leave it unclear; Codex's API-key citation carries a
    `read_note` that it was read through the search index only.
  - Egress admission holds a lease to its run's own connection: the lease keeps it
    (`file_space.run_connection`, in D's `file_space` table; null for a run on the vendor's
    own sign-in), and `egressOpen` refuses any other `egress_run_connection` (403) right after
    the lease check, so a run never spends on a connection it was not given and a
    signed-in run spends on none; only the run's own connection is refreshed for it.
  Review round 2 (approved at `6f176597c`; `ta-work/rve_report.md`):
  - A run's environment is an allow-list: `runEnv` gives `HOME={home}`, the record's own
    variables for the credential and the process basics it keeps (`RUN_BASICS`); the
    runner starts empty and adds an API-key run's key variables (`sandboxEnv`) alone. A
    record's own variable is never shaped like a credential or an endpoint, nor `HOME` or a
    basic. `forbid_env` stays the backstop, with `PREFIX_*` patterns, and now holds every
    variable the review found in the pinned packages (Claude's gateway, Foundry, AWS,
    Bedrock, Vertex and Mantle variables and `CLAUDE_CODE_OAUTH_TOKEN`; Codex's
    `CODEX_ACCESS_TOKEN`; Gemini's `CODE_ASSIST_ENDPOINT`, `GOOGLE_GENAI_USE_GCA`,
    `GOOGLE_CLOUD_ACCESS_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_VERTEX_BASE_URL`
    and the auth overrides; Kimi's `KIMI_CODE_BASE_URL`, `KIMI_MODEL_*`, the OAuth host
    overrides, `OPENAI_*` and `ANTHROPIC_*`). The checker scans every settings-file template
    for an environment block, a credential-shaped key or helper (`apiKeyHelper`), a
    forbidden variable and, in a signed-in run's file, an endpoint key. The review's
    attacks are a guard, `doors/guards/sources-attacks-r2.test.ts`.
  - pi's MCP door is `"exposure": "direct"`, so a `--tools` filter of plain names keeps its
    tools (pi's default, `codemode`, reaches them through codemode alone).
  - Free calls are keyed by provider and API (Anthropic's page covers Anthropic's
    endpoint, not another `anthropic-messages` source), and a guard holds every free call
    apart from its source's metered model call.
  - Every API-key source's sealed auth header and scheme is held to the one pi-ai sends
    (`sentHeaders`, learned with no network).
  - Review round 3 (approved at `e41874fe1`): every settings template is read whole, so
    no spelling hides a key: a `.json` file by its parse, its real keys and decoded values
    checked; any other file as lines of bare keys, with a quoted key, an escape or an
    inline table refused outright (no TOML parser is in the lock). The round's bypasses (a
    `\u` escape, TOML quoted keys) are a guard, `doors/guards/sources-attacks-r3.test.ts`.
    The lease's `open` and `config` hand the runner the run's whole environment (`env`:
    `HOME`, the record's variables, an API-key run's key variables) and `inherit`, which
    slice S sets as is. The free-call guard reads its APIs from the rules (`ruledApis`).
  - **Technical ceiling (limits audit):** Kimi's prompt rides its argument
    (`--prompt={prompt}`, the CLI reads no prompt from stdin), so it is bounded by Linux's
    per-argument limit, `MAX_ARG_STRLEN`, 128 KiB. The bound is the kernel's, not ours: the
    runner refuses a longer prompt for Kimi with its reason, never truncates it, and the
    other engines take theirs on stdin.
- [x] Slice R moves the run's connection from the sandbox's one lease to the run grant
  (`engines-replan.md` §2.4): `file_space.run_connection` is gone; the sandbox's lease is
  shared by its simultaneous runs (opened by the first, joined by the rest, given back with
  the last), and each run carries its own grant (`run_grants.connection`, or none, signed
  in). Each call names its run in its path (`/r/<run>/c/<connection>/...`) and the egress
  door holds it to that run's own connection (`egress_run_connection`), a settled run's
  call refused (`egress_run_closed`).
- [x] R Runs in the owner object (`engines-replan.md` §5 row R), in workerd, nothing
  deployed:
  - Schema, migration 12 in place: `runs` (the run and its receipt: agent, conversation,
    engine, credential, model, state, initiator, client and stamp, retry key, usage
    counters, result cut to 16 KiB, first error, exit code, times), `run_grants` (lease,
    stamp, origin, scopes, connection), `conversations` (agent, engine, client, the
    engine's session id, last run), `run_inputs` (a waiting run's input), `engine_signins`
    (each engine's sign-in, presence only), `run_schedules`, `platform_tiers` and
    `socket_tickets`; `egress_runs` gone, a call's charge counted to its run's row and its
    source's day and month; `sandbox_leases` (one an authority). Owner-bound guard:
    42.3% (4,232,733,666.75 bytes, slice R's tables 168.0 MB); with every proposed raise
    49.98% (4,997,920,528.75), 2.1 MB under half: the review's sandbox leases took 3.1 MB
    of the 5.2 MB margin, their sandboxes' own sign-ins with them, nothing shrunk to fit
    (`limits-audit.md`).
  - One sandbox an authority (review round 1, H-1): the owner's own runs share the
    owner's sandbox, and each outside client's runs that client's own, its own lease
    (`sandbox_leases`, keyed by authority), so runs of different authority never share a
    VM; the tier's simultaneous runs count across all of an owner's sandboxes, and the
    run-to-connection binding stays per run. A client's sandbox syncs only while that
    client's grant (at its live run's stamp) holds `read_files` or `write_files`, writes as
    that client, and never into `OWNER_TREES`. Each run holds a 256-bit secret of its own,
    minted at admission (only its SHA-256 on the run grant), which its URLs carry
    (`/r/<run>/<secret>/mcp`, `/r/<run>/<secret>/c/<connection>/...`, through its config's
    environment): every call of a run's presents it, so a run id alone authorizes nothing.
    The runner's own doors (a run's events, the sign-in report) take a runner secret
    (`x-ta-runner`), minted with the lease, distinct from the egress token and every run
    secret, so no CLI settles a run or reports a sign-in; only the owner's own sandbox
    reports the owner's sign-ins (`signin_owners`).
  - A grant change or revoke settles the client's runs at once, `failed` (`revoked`), live
    or waiting, frees their tier slots and conversations, drops the client's schedules and
    leaves the lane no job when nothing is left (H-2); `lost()` reads the runs' own rows
    (their lease and heartbeat), never their grants. A client's scheduled and mail runs
    keep its client on their row (the `runs` CHECK now says a client's run names its
    client and the owner's none), so they start, in the client's sandbox (H-3). A cancel
    revokes the run's grant at once (M-2). Each run's own heartbeat (`beat_at`, every
    events post for it) settles it `unknown` when it lapses for the lease's life, though
    another run keeps the lease (M-3); the object needs no `SANDBOX.status` for it, and
    stops the lost run's CLI through the sandbox. Every settle and every queued input
    prunes the receipts to `RUNS_KEPT` (M-1). The events post is bounded so the memory
    guard counts it whole (M-4). The binder hands `tiers: null` when
    the record holds none, and the object forgets its kept numbers and refuses (L-1); an
    absent `SANDBOX` refuses a cancel loudly (L-3); a lease past its tokens' deadline is
    not live, so no run joins dead tokens (L-4: a run's life is its sandbox's, whose
    continuous-runtime ceiling the tokens carry; a run that joins late shares what is left);
    `RUNS_CEILING` and the queue's bounds are classed as budget allocations (L-5).
  - Review round 2. Sign-ins are per sandbox, and a login is never copied between
    sandboxes (ADR-031 decision 2): the owner's own sandbox's in `engine_signins`, an
    outside client's sandbox's on its own lease's row (`signins`), each reported by that
    sandbox's own runner. `choose` reads the run's own sandbox's: a client's `auto` run
    takes a plan only where that client's sandbox signed in itself, else the owner's keys
    (held to the run's grant as every run is); an agent naming the vendor's sign-in in a
    client's sandbox with none there is refused `signin_required` (sign in inside that
    sandbox, through the vendor's own flow). A lapse there leaves a notice naming the
    sandbox. The receipts are pruned by when they settled, the newest `RUNS_KEPT` kept,
    never the one settling nor a live or waiting one, so a long run's receipt outlives
    any churn. A client's sandbox always reads its live runs' agents' own definitions
    (`/workspace/agents/<agent>` and the abilities' trees) read-only, the rest of the
    owner's files only as the client may read them, and writes only with `write_files`,
    never into `OWNER_TREES`. The run's secret goes as a header (`x-ta-run`) where the
    CLI's record says it takes one (`run_secret_env`, Claude Code's
    `ANTHROPIC_CUSTOM_HEADERS`, and its MCP file's `headers` with `{run_secret}`), the
    path carrying it otherwise; the doors take either. The events post is bounded
    (`EVENTS_BODY_MAX` 64 KiB, 64 frames, 32 Ki units a frame).
  - Review round 3. An agent naming the vendor's sign-in is admitted in a client's sandbox
    whose sign-in is unknown, as the owner's is; the sandbox's start checks the record's
    `status_argv` there and answers `{started: false, code: "signin_required"}` when no
    login is, and the run settles `failed` (`signin_required`) with a notice to sign in
    inside that sandbox. `auto` takes a client's plan only where it is known signed in. A
    client's sandbox's readable set is kept as a digest on its lease; when it changes (a
    new agent's run, a widened file scope) the next feed read answers `reset: true`, so
    the sync agent reads the feed again from its start. The object holds at most
    `EVENTS_IN_FLIGHT` (8) events posts at once, whatever the tier; one more is answered
    `events_busy` (503, `Retry-After`) and posted again; memory guard 15.5 MB worst case,
    12.1% of the isolate, with no tier number in it.
  - Admission in one gate unit (`runs.ts`): owner admitted, `SANDBOX` bound, agent's
    `agent.json` (`agent-file.ts`) read whole and enabled, the stamp standing, the retry
    key, the caller's conversation, the tier's simultaneous runs, the source chosen (the
    file's engine and credential, a named or the strongest listing connection, or `auto`),
    an unattended run only where its record allows it (ADR-033 D3), the owner's own
    spending caps; then the run, the lease opened or joined, the grant.
  - One live run a conversation (an engine session's own ceiling), and never a refusal
    for it: an input to a busy conversation is written `waiting`, its input kept
    (`run_inputs`), and the `runs` lane starts the waiting runs in order as each run
    before them settles, admitted whole again (a refused turn settles `failed`, noticed;
    a cancel settles it at once). The queue is bounded together by `QUEUE_ROWS` 256 and
    `QUEUE_BYTES` 6 MiB, a technical ceiling sized to the owner-bound guard's remaining
    budget (16.5 MB, its inputs and its waiting runs' rows; `limits-audit.md`).
  - `auto` sees subscriptions: the runner reports each engine's sign-in (its record's
    `status_argv`) at `POST egress.tinyassets.io/signin` (`signinReport`, by the egress
    token), one `engine_signins` row an engine (signed in or not, when checked and
    changed, the models it offers; never a credential), and `auto` ranks the signed-in
    engines with the connections by the same ranking, a subscription preferred to a key of
    the provider it covers. A lapse (signed in, then not) leaves a `signin_lapsed` notice
    ("sign in again"), and `auto`, or an agent naming that engine on its sign-in, is
    refused `signin_lapsed`, never a silent fall back to another source.
  - Run doors: `agentSubmit`, `agentRun` (now, `at`, `cron`; its `on_mail` and `if_mail`
    stand-ins removed by slice ID),
    `agentStatus` (from the receipt), `agentCancel` (through the sandbox); MCP `converse`,
    `run_graph`, `run_status`, `run_cancel`; `/app/api/agent/{submit,run,status,cancel}`.
  - A run's own doors on the egress host: `/r/<run>/mcp` (the table but the run tools,
    every door held to the run grant's scopes; a client's run to its intersection with
    that client's grant, its stamp rechecked, a grant change or revoke ending the run),
    `/r/<run>/events` (the runner's frames, read by the record's matchers in the object).
  - The `runs` lane: due schedules and lost runners only, never a poll (and, from slice
    ID, mail no run has taken).
  - Live output (`relay.ts`), A6 salvaged (its socket ticket, now a routing-MAC token of
    the `socket` kind; the binder's upgrade path; the ticket's grant and expiry binding;
    sign-out ending sockets): frames `{v, run, seq, at, kind, event}` relayed to the app's
    sockets on the conversation, a bounded in-memory tail (memory guard: 13.4 MB worst
    case, 10.5% of the isolate), never committed. Not A6's pi `watch()` or tee.
  - Carried: slice E's owed case (a record setting a variable a key's environment sets is
    refused by the checker, `network.test.ts`), slice D's flakes (`.wrangler` skipped by
    both config guards; the files-quota pass test's explicit timeout).
  - Mutation: 126 mutants over admission (12), the tier check (6), grant scoping (7), the
    doors' authority (7), settle (10), the relay (6), the conversations' queue (13),
    `auto`'s sign-ins (14), the review's round 1 (23), round 2 (17) and round 3 (11: the
    sign-in notice, the readable set's reset, the events posts' gate, the busy answer):
    126 killed, the gate's release on a refusal once tested. The reviewer's 74 and 5 forms
    the builder ported to the moved code: 12 no longer apply (each replaced by a later form,
    one, `r3-client-unknown-signin-ok`, now the rule), 5 rejected by `tsc` (their rewrites
    counted instead) and 2 ports killed by `tsc` alone (their runtime forms among the 126);
    of the 60 run, 57 killed, the 3 survivors equivalent (`r2-grant-epoch-unchecked`: lease
    ids are random; `r2-no-prune-wait`: the settle's prune bounds the table alone;
    `r3-header-unchecked-shape`: a malformed header's hash never matches). The 12 the
    round-1 review found untested (M-6) each have their test.
- [x] Slice S builds against the `SANDBOX` interface (`owner/src/sandbox.ts`), and only it:
  - the `tinyassets-sandbox` Worker as `SandboxService`: `start(owner, SandboxStart)`
    answers `{started: true}` or `{started: false, code}` (a fixed code) at once, the runner
    in the background; `cancel(owner, {run, lease_id})` sends SIGTERM, then a kill;
    `status(owner, {run, lease_id})`;
  - a start with `tokens` and `network` (the start that opened the lease) (re)configures
    the sandbox with that network and hands the sync token to the sync agent; a start
    without them runs in the configured sandbox;
  - the runner sets `config.env` and `config.inherit` exactly, invokes the record's
    `argv` (or `resume_argv` with `session_id`), the prompt on stdin or its argument, the
    record's `unattended_argv` when `unattended`, and `{mcp_url}` as `urls.mcp`;
  - it posts `{frames: [{seq, at, event}], end?: {exit_code}}` to `urls.events` (seq from
    1, rising; a batch at least every 20 s as the heartbeat, which renews the lease; the
    end once the CLI exited), and reports a vendor error category as an `error` event;
  - it posts `{signins: [{engine, signed_in, models?}]}` to `urls.signin` for every engine
    whose record signs in with the vendor's own login: at each start, before the CLI,
    each record's `status_argv` run (exit 0, or `status_equals`, is signed in), and at the
    end of a sign-in; models only as the CLI lists them. Presence only: never a credential;
  - the vendor sign-in flow itself (`cli.login`) opens the sandbox's lease for the owner
    with no run, so the first sign-in is reported before any run: `auto` sees a
    subscription-only owner's plan from then on;
  - one sandbox per (owner, authority) (`SandboxStart.authority`: `owner`, or one outside
    client's id), each its own lease, tokens and network. A login is never copied between
    sandboxes (ADR-031 decision 2): a client's sandbox runs on a vendor's sign-in only once
    the owner signed in inside it, through the vendor's own flow there, its own runner
    reporting it (`urls.signin`), never with the owner's sandbox's login. The sign-in flow
    opens a client's sandbox as well as the owner's. A start on the vendor's sign-in first
    runs the record's `status_argv` in its sandbox and answers `{started: false, code:
    "signin_required"}` when no login is there;
  - a client's sandbox syncs in its runs' agents' own definitions always, read-only, and the
    rest of the owner's files only as the change feed the object filters for it gives; on
    `reset: true` it reads the feed again from its start. Any content door slice S adds for
    the sync agent applies the same filter as the feed (`#syncer`'s readable set);
  - the runner runs as root and keeps the runner secret (`tokens.runner_token`) in a
    root-only file, posting it as `x-ta-runner` on its event and sign-in posts; each CLI
    runs under its own unprivileged uid, `/proc` is mounted `hidepid=2`, and the agent user
    has no sudo: no process reads another run's environment (its secret is in its URLs)
    nor the runner's secret;
  - it posts events within `EVENTS_BODY_MAX`, `EVENT_FRAMES_MAX` and `FRAME_UNITS_MAX`
    (`run-events.ts`), one post in flight a run, cutting a longer CLI event, marked, before
    it posts it, and posts again after `Retry-After` when answered `events_busy` (503);
  - it posts an empty batch, the run's first heartbeat, as soon as the sandbox is up and
    before the initial file sync, since a run with no post within `LEASE_TTL_S` of its
    admission is settled lost;
  - a CLI whose record names `run_secret_env` gets the run's secret as that header and its
    base URL without it; one whose MCP template carries `{run_secret}` gets `urls.mcp_bare`
    as `{mcp_url}` and the secret in the header; every other CLI, the path forms;
  - a reported model id goes into argv flag-safe (`--model=<id>`, never a bare argument a
    leading `-` could turn into a flag);
  - the owner config guard's `SERVICES` gains `SANDBOX` (a Worker that reaches nothing but
    E2B), and `wrangler.jsonc` binds it;
  - owed probes: E2B's transform on `/r/<run>/<secret>/mcp` and `/r/<run>/events`; whether a
    WebSocket would carry the frames (the POST batches are the path today).
  - Built (slice S, `platform/sandbox`): the Worker (`src/index.ts`, `src/e2b.ts`, the one
    module that fetches, E2B's two hosts alone) and its own classes: `SandboxControl`, one
    per owner, which serializes that owner's E2B calls (so a release's pause never freezes a
    run a start just launched) and looks after its sandboxes; and `SandboxSlots`, the
    platform's one line for one more running sandbox (`src/slots.ts`). The versioned
    template (`template/build.py`, its tag the digest of the runner's bundle, the records,
    their locks and `tier.ts`, `E2B_TEMPLATE` held to it by `guards/template.test.ts`); the
    runner (`runner/*.ts`, bundled; 34 workerd and 32 Node tests against a fake E2B and fake
    CLIs of each record's event shapes, the review's five attacks among them, refused; the
    runner's tests need real root and real uids, so `root-tests.mjs` runs them as root or
    through passwordless sudo, as on GitHub's runner, and fails loudly otherwise, never a
    skip); the
    sync doors on the egress host (`/sync/*`); `SANDBOX` in the owner config guard's
    `SERVICES` (its classes, the control record, the ring's producer, one var, its key) and
    bound. Probed live: `sandbox-probe.md`.
  - The founder's rule (2026-11-03): `sandbox_runtime_ceiling_s`,
    `sandbox_concurrency_ceiling` and `sandbox_renewal_window_s` are the control record's
    (`directory.ts`, no bound of the platform's own but the window inside the ceiling). The
    Worker renews a sandbox (pause with memory, resume, its slot its own) at a quiet moment
    inside the window before E2B's `endAt`, forced at the measured edge (`renewing` 252 ms +
    the longest pause 705 ms), `ta.renewing` and `ta.renewed` frames to the app; a CLI that
    fails just after is resumed from its session; a filesystem-only pause restarts the runs
    (`recover`). Its alarm is armed again on every path: a failed look backs off from the
    measured request time, never past the soonest edge; an unreadable control record is read
    as the last numbers read; a sandbox paused while a lease runs in it is resumed, E2B's
    refusal asked again and logged `sandbox_renewal_refused` (review H-5). No retry runs at
    alarm speed (verification F-1): a failed look backs off from the measured request time,
    sooner only for an edge still ahead, and a pending resume or pause backs off per row, at
    most a lease's TTL apart. A renewing sandbox holds its slot in the line across its pause
    and resume (F-2), so the first owner waiting cannot create into it. An idle sandbox
    pauses at its release, at its renewal look, or once idle a lease's TTL (the release
    never come); a pause E2B answered busy is asked again.
  - The line (review M-3, M-4): a start needing one more running sandbox waits its turn, so
    a newcomer never takes a freed slot ahead of an owner that waited longer; past the
    concurrency ceiling (E2B's team-wide `X-Total-Running`, advisory: none and the create is
    tried) or refused by E2B for room, the run waits (`waiting`, `sandbox_wait`), never
    refused and never polled. Each sandbox the platform pauses or kills rings the first in
    line through the ring queue, whose consumer (the doors) calls that owner's
    `sandboxRung`; the first in line is rung again, backing off from `LEASE_TTL_S` to the
    runtime ceiling, for frees no ring sees (other sandboxes of the team, E2B's own capacity).
  - The runner (review H-1 to H-4, M-1, M-2, M-7): root never follows a path another user
    placed (`fsafe.ts`: no-follow descriptors, ownership on the descriptor, a link replaced,
    never written through); each run runs as a user of its own from the template's pool
    (`ta-run-<n>`, `RUNS_CEILING` of them), its home made from its engine's store (the
    record's `secret_paths` and `session_paths` alone; each engine's store its own, only its
    `session_paths` restored), every process of that user killed at the run's end; the sync
    agent runs as `ta-sync`; the env-file refusal covers the CLIs' home settings and Gemini's
    upward `.env` search; Gemini's settings are a root-owned per-run system file outside
    `{home}` (`{system}`, folder trust off), which the records' checker admits; the agent's
    skills and the owner's shared ones (`/workspace/.agents/skills`, `/workspace/skills`)
    are projected into each engine's skills path, the agent's winning, a client's sandbox
    only what it may read; every path another user may write (others, or group `ta`) is made
    root's at every boot and resume and checked, the design's writable paths named with their
    reasons; and no user but root may `sudo` (E2B grants `user` it again at each boot:
    `prepare` takes it away and checks).
  - The sandbox's doors (review M-5, M-6, M-8, H-6): the sync doors take the sync token only
    beside its own lease's egress token (from inside that sandbox); a file past an inline
    write goes by the files plane's upload (`/sync/upload_begin`, `/sync/upload_chunks`),
    never refused for its size; sandboxes are created `secure` (envd refuses a call without
    the sandbox's token, probed); a run's doors must be `https` on the egress host; the
    owner's erasure kills every sandbox of theirs (paused ones, and strays its metadata tag
    finds) and deletes the Worker's state of them, in its step 2.
  - Deviations, each for the founder's rule or a live finding: `SandboxService` gained
    `release`, `unwait` and `erase`, and the object `sandbox_waiting` and `sandboxRung`
    (`runs.ts`, `sandbox_wait` on `runs`); the Worker keeps classes of its own (the plan said
    stateless: renewal needs a clock outside the VM, one owner's calls need one order, and
    waiting owners need one line); `RUN_BASICS` gained `NODE_EXTRA_CA_CERTS` (E2B re-signs
    the egress door's HTTPS). `sandbox_wait` is in migration 12 itself, as slice ID's columns
    are in theirs: no workflow deploys platform/, so no object holds an older schema.
- [ ] Owed by slice S:
  - [x] The vendor sign-in flow (`cli.login`): built by slice SIGNIN, below.
  - E2B's lifecycle webhooks as a second doorbell, so a free outside the platform rings at
    once rather than at the line's back-off: their payload and signature were not readable
    from this session (`e2b.dev` is outside its network), and registering one is a deploy act.
  - The founder's alerts: runs waiting on the concurrency ceiling (each wait is logged
    `sandbox_waiting`), a renewal's resume E2B refused (`sandbox_renewal_refused`), and E2B
    credit or spend, which E2B's public API does not expose (no balance or spend endpoint;
    team metrics give concurrency only). The repo's founder channel is Pushover from GitHub
    workflows (`scripts/pushover_page.py`), which no Worker reaches: a scheduled workflow
    reading E2B's `/teams/{id}/metrics/max` against the ceiling is the fit, with
    `E2B_API_KEY` as a repository secret.
  - Gemini loading its `{system}` settings, live (F-3): the founder's live test, on a run
    past the sign-in.
  - Deploy (slice L): the Worker, its secret, the ring queue, the template's build in the
    deploying project, and the control namespace's id.
- [x] Slice ID: TinyAssets agents get an identity in the ledger (`ledger.ts`, migration 13,
  `test/agent-mail.test.ts`), with its review's fixes (H1-H3, M1-M3, L1-L4). Core to the
  product: group chats that mix people, main agents and TinyAssets agents.
  - Each `/workspace/agents/<id>/agent.json` is a ledger identity (`native_agents`, keyed by
    `ta_` and its name's digest) beside the outside agents, made as the file appears and
    retired as it goes, in the file change's own unit (`Files`' path observer). A live one
    takes one of the ledger's reserved mailboxes (`ledger_agents_full` past them); its row
    counts in the registry, the oldest retired one forgotten first (ties at one instant by
    name). A retired agent's mail
    is kept and readable by the other side; new mail to or from it is refused
    `agent_retired`, and its untaken mail fails, what it sent included (a disabled outside
    agent's too).
  - Rooms are pairs: `dm:<a>` (the owner and `a`), `dm:<a>+<b>` (two agents, one at least
    the owner's own). A run posts as its own agent, never as the owner or client that
    started it, and reads and acks no mailbox (`run_no_mailbox`; the mailbox tools are out
    of the run profile): its mail is its input, and its agent's mailbox holds every
    authority's mail. Each mail keeps its sender's authority (`ledger_messages.authority`)
    and whether a person sent it (`attended`: the owner's own call).
  - Mail to an agent waits `queued` for the `runs` lane, which starts the agent's run
    through slice R's admission: its input its own mail's envelope (sender name, kind and
    id, room, message id) and then the body verbatim, fenced by the message's random id so no
    body can forge an envelope (review N4), all a woken agent needs to answer by
    a deliberate post (review N1); attended only when a person sent it
    (a run's mail and a client's are unattended); its conversation the thread's (one a
    thread, agent and authority, keyed by their digest, no index); its authority the
    mail's (a client's mail held to the intersection with its grant, refused once the grant
    moved); its retry key the mail. Past the tier it waits as R's inputs do; when the
    inputs' queue is full the mail is held in the ledger until it has room. The mail stays
    held until the run settles. Posting mail that starts an agent's run needs `converse`
    there (a client's scoped grant, a run's scopes); `agents_list` shows a scoped caller only
    the owner's agents it holds `converse` on (`owner_agents`).
  - The wake rule (H3, the group rule too): a post wakes only the members it addresses. A
    run's result is the reply only to a person's or an outside client's mail; a run woken
    by another agent's mail posts no automatic reply, so agents take turns only by
    deliberate `room_post`s, each needing `converse`. The owner reads any chain whole
    (`mailChain`, from the mail a run's receipt names).
  - Slice R's owner-mail schedules (`on_mail`, `if_mail`), their tests and spec text are
    gone.
  - Loops: no cap of the platform's, and no default turn: a chain advances only when a run
    chooses to post. The tier's simultaneous runs, the inputs' queue, each mailbox and the
    owner's own spending caps bound agents that do keep posting, each refused loudly
    (tested). For the founder: an owner setting, off by default (a chain-depth stop on
    agent-to-agent mail), is not built.
  - For the founder (H2's mismatch): a client's mail starts an unattended run, but a
    client's `converse` starts an attended one (`unattended: false`), so one client gets two
    answers to "is a person present" by door. Either both read the client's sign-in, or
    `converse` takes the mail's rule; neither is changed here.
  - Owed to slice S4 (rooms and status): group rooms of three or more (the owner, main
    agents and TinyAssets agents together, one post waking each member it addresses), and
    moving room ids off member-encoding (`dm:a+b`) onto a rooms table with members: S4 owns
    that migration, and moving the mail threads and conversations keyed by `chain_root`.
  - Owed to later slices: the app's view of mail-started runs and agents' chains (the owner
    app, S6); slice M's `inbox_digest` over the owner's and outside agents' mailboxes.
- [ ] Owed by slice E's review, outside slice S's checks:
  - Price server tools as data (Codex's web search, Anthropic's `mcp_servers`, OpenAI's
    `web_search_options`), so a call that enables one can be metered rather than refused
    `egress_unmetered_tool`.
  - Gemini's `countTokens`: read Google's page (ai.google.dev, to be allowed in the
    session's network) and list it in `free` if the page says it is free.
- [ ] Slice S verifies each engine record live, on the pinned version, in a headless E2B VM
  (each value below was not confirmable offline), and fixes the record where it differs:
  - Slice S verified the pinned installs, their startup and users, and each CLI's
    not-signed-in run through the runner (`sandbox-probe.md`); every check below
    that needs a model or a vendor sign-in is the founder's live test's.
  - **Every CLI:** the event shapes against the matchers (session id, final text, error,
    usage, tool calls) on one run and one resume; resume after a cold create restores
    `session_paths` and continues; MCP injection reaches the run-scoped door with no bearer
    in the config; the tool filters and unattended flags run with no prompt; the CLI keeps
    its pinned binary (`no_update`); nothing in `secret_paths` appears in a sync-out.
  - **Every CLI, its credentials:** every credential file it writes is in `secret_paths`,
    MCP OAuth stores included (Kimi's, `-tokens.json` and `-client.json`, not yet located);
    a signed-in run starts with every `forbid_env` name unset and runs on its sign-in.
  - **Every CLI on a key:** its own model-call body and headers pass `api-rules.ts`
    through the egress door (its tools, its `*-beta` headers, which a metered call strips),
    and any other call it makes (a token count, a WebSocket) is free and cited, or refused.
  - **The runner's content backstop (doors spec, "The runner never syncs a secret's
    bytes"):** no synced file or transcript line holds the bytes of a `secret_paths` file;
    the runner refuses the sync, from the records' list.
  - **Project-level env files:** a run's environment is the allow-list alone, though an
    agent can write the files a CLI loads variables from (Claude's project
    `.claude/settings.json` `env` and `apiKeyHelper`, Gemini's `.env` and `.gemini/.env`,
    any CLI's own): the runner checks for them in the agent's directory and refuses a run
    whose project files set a variable its credential forbids, or any credential-shaped
    one on a signed-in run.
  - **`HOME`:** every run has `HOME={home}`, so whatever a CLI or its SDK writes under the
    home (gcloud's ADC, the Anthropic SDK's config Kimi bundles) is under `{home}` and can
    be named in `secret_paths`.
  - **Claude Code:** `--permission-mode auto` with `--permission-prompts none`; `--tools`
    and `--disallowedTools` as the filters; `CLAUDE_CODE_PROJECT_DIR_NAME` pins the
    transcript path under `CLAUDE_CONFIG_DIR`; `ENABLE_CLAUDEAI_MCP_SERVERS=false`; `auth
    login` completes headless with a pasted code; an API-key run on `ANTHROPIC_BASE_URL`
    through egress, and whether it calls `/v1/messages/count_tokens` (unmetered, refused).
  - **Codex:** `exec --json … resume {sid} -` argument order; `CODEX_API_KEY` and
    `OPENAI_BASE_URL` for a key; `forced_login_method="api"`; `approval_policy` and
    `sandbox_mode` inside E2B; `check_for_update_on_startup`; `-c mcp_servers.tinyassets.url`
    over streamable HTTP; `features.<tool>=false` as the deny filter; `login --device-auth`
    headless.
  - **Gemini CLI:** `GEMINI_CLI_SYSTEM_SETTINGS_PATH` and the system settings' keys
    (`mcpServers.httpUrl`, `mcp.allowed`, `context.fileName`, `tools.core`,
    `tools.exclude`, `general.disableAutoUpdate`); `selectedType` per credential;
    `--approval-mode yolo`; the `stream-json` field names (`init.session_id`, assistant
    `message` deltas, `result.stats`, `error`); `--resume {sid}`; the `NO_BROWSER` sign-in;
    the prompt on stdin (0.66.0 reads it only if it arrives within 500 ms); with
    `GOOGLE_GEMINI_BASE_URL` set, 0.66.0's environment auth picks `gateway`: whether the
    enforced `gemini-api-key` type still sends to that base URL; the node-pty and keytar
    install scripts.
  - **Kimi Code:** `--continue` in a per-conversation cwd; the `stream-json` fields, and
    matchers for its errors and usage (it has none yet); the user-level `mcp.json` schema;
    where `session_index.jsonl` and the `region` marker live; whether `config.toml` holds
    sign-in state; `login --region` headless in both regions; `--prompt={prompt}`.
  - **pi:** `--mode json` event names (`session`, `message_end`, `tool_execution_start`),
    and a `final_text` matcher that reads only the last assistant message's text, not every
    tool-use turn's blocks; `--session {sid}`; `PI_CODING_AGENT_DIR`; `models.json`
    `baseUrl` per provider; the pi-ai key variables; `.pi/skills` loaded under `--approve`;
    `--tools`; the user-level `mcp.json` reaching the run's door over streamable HTTP; the
    prompt on stdin.
  - [x] **Codex:** whether `OPENAI_BASE_URL` still sets the built-in provider's base URL in
    0.166.0: no, removed in 0.119.0 (`codexurl_research.md`). Built (on `platform-signin`):
    Codex's key run reaches the door through a custom model provider in its API-key argv
    (`-c model_provider="tinyassets"`, `model_providers.tinyassets.{name,base_url,env_key,
    wire_api}`, `env_key` `CODEX_API_KEY`, `wire_api` `responses`), its `api_key_env`
    base-URL variable null. The checker holds four rules: a null base-URL variable needs a
    declared channel (a file template or the API-key argv); `{base_url}` only where only a
    key run reads it; joined to its key, never bare; and no argument a signed-in run gets is
    endpoint- or provider-shaped. An API-key run's home carries the store's sessions and
    never its secrets (a vendor sign-in's `auth.json` stays out of a key run's reach). The
    route probe (`ta-runner route-probe`, `build.py` after every build, on a sandbox whose
    network denies all but the pinned resolver): every keyed engine on every provider it
    keys, run as a run runs it with its door on a loopback capture server at the real path
    and a fake sign-in seeded in the store, passes only if the first model request reached
    the door's path with the run's key and secret, nothing connected outside (`strace`), and
    no request carried the fake sign-in; the tag is printed only then. Review round 3: the
    capture server answers each model request with a minimal valid reply in its sealed
    API's wire format (Messages, Responses, Chat Completions, Generative Language), so each
    CLI completes a turn; the probe then resumes it (its home made again from the store) and,
    where the record says how (`cli.compaction`: Codex's `model_auto_compact_token_limit=1`,
    its compact prompt's marker), compacts first, and judges every request of both turns,
    not only the first. A passing tag is recorded with its checks' results in
    `sandbox/template/passed.json`; the template guard fails CI when the pinned tag has no
    passing record covering every record's login and route. The endpoint rules name
    `provider` and `proxy` too (a signed-in run's files and argvs; every run's variables:
    `HTTPS_PROXY`, `ALL_PROXY`), and Codex's `--oss`. Review round 4: no record sets a
    variable that loads code into a CLI's process (`NODE_OPTIONS`, `LD_*`, `DYLD_*`, `BUN_*`,
    `PYTHON*`, Perl's, Ruby's, the JVM's, `BASH_ENV`); the pass record must show a record's
    compaction where it has `cli.compaction`; a resume passes only if its requests carry the
    first turn's own word. Left for later: the run secret into a header
    (`env_http_headers`).
  - Known gap (review round 3, L-c): the route probe traces `connect()` alone. An unconnected
    UDP `sendto` or an io_uring connect would not show; none of the pinned CLIs uses either
    today, and the probe's sandbox denies every outbound address but the resolver anyway.
- [x] Code mode's VM in the hooks Worker (H0, narrowed by D): `Hooks.codemodeExecute(name,
  request, exchange)` runs one execution in its `CodemodeSandbox` (a
  `CodemodeSandboxDurableObject` subclass with its interrupt budget and memory), handing
  out no stub; the owner's executor (`codemode.ts`) names each `<owner>/<uuid>`. Config
  guard: its own one class, no loader, no `OWNER`, no storage binding, no route; isolation
  guard over its source. P11's local part passed. The MCP door's `code` tool is slice M's.
- [x] M The MCP door for engines and main agents (`engines-replan.md` §5 row M), in
  workerd, nothing deployed. Review round 1 (REQUEST_CHANGES), all taken: every grant on
  `/mcp` and every API key names `custom`, and code mode is on until the owner turns it off
  (H1; the free row names `custom`); the object reads the control record's gates again at
  each script call, through its own `CONTROL` namespace, now the record's home (M1); an
  answer past `CODE_ANSWER_MAX` is refused `answer_too_large` (M2, `limits-audit.md`); the
  digests report deletes and clears, from their history entries stamped with the clock's
  change (M3); H2's probe list gains the starvation probe (M4); citations fixed (L1); the
  change indexes are on `change` alone (L2); a script's answer lists what landed (L3); the
  compute-stop test asserts the interrupt budget's message (L4); the gateway's forward test
  has its own 30 s timeout (L5). Verification (REQUEST_CHANGES), all taken: an answer is
  measured as V8 serializes it (`serializedBytes`: a string two bytes a unit when any unit
  is past Latin-1), the review's X1 a test (M2); `changed` names its indexes (`INDEXED
  BY collection_changes`, `collection_erasures`), its plan held by a test (L2); the
  `CONTROL` guard reads the source's syntax, so no spelling hides a use; a test of the
  control record's one-second read (`CachedOwnerDO`); a key's delete entry kept through the
  trim while the key is made again private under another writer; the doorbell below reads
  `CONTROL`. An owner's own MCP client is an outside client to the object (every `/mcp`
  and `/api` binding carries the client's origin), so its scripts take the outside gate in
  the object as at the door; only the owner's app (origin none) and a run take the
  maintenance gate, as their doors do. The details below are as built:
  - **Profiles, data** (`doors/profiles.json`): `custom` (the owner's own choice: policy
    `tools.custom`, else every tool), which every grant on `/mcp` and every API key names,
    since each is a custom connector (`outside_clients.profile`, a free row `custom`);
    `directory` (every tool but `code`, by OpenAI's plugin guidelines, "Tool independence
    and exposure": no generic executor enabling operations not individually exposed for
    review; Anthropic's software directory policy has no rule against an executor, its 2A,
    5B and 5E cited for the tools' wording and annotations; both read 2026-11-03); and
    `run` (the table but the run tools). A profile change moves the generation.
  - **Owed when the Direction 7 directory submission is made (the founder's call):** the
    listing gets its own MCP resource, a new door (Fact 11: the doors list gains it, and the
    handles canary runs after), whose grants are pinned to `directory`; `/mcp`'s grants stay
    `custom`. The object answers a caller's tools (`toolProfile`), asked by
    `/mcp`, the run's MCP door and `/api` before any tool is listed or called; a refused
    caller (revoked, fenced, ended run) gets `403` and no tool. Removed as redundant:
    `RUN_TOOLS`/`runProfile()` (the `run` profile, which on merging slice ID also leaves out
    the mailbox tools, `inbox_digest` among them, its door refusing a run `run_no_mailbox`)
    and the `run_code` capability with
    `authority.ts`'s `EXPLICIT` (code mode is the owner's choice of profile, not a
    capability).
  - **`code`** (`codeRun`): refused `code_mode_off` once the owner's policy turns
    `hooks.codemode` off (on by default; hidden from the caller's tools while off),
    `tool_not_served` unless the caller's profile holds it, and the control record's gate
    (`control-gate.ts`) at its start and at each call: the kill switch for an outside
    client, the maintenance gate for a run or the owner. The executor runs in the owner object (it binds
    `HOOKS`); a script's tools are the caller's profile's others, each call the tool's own
    door with the caller's binding, admitted again (grant, run, admission) and held to the
    profile and switch as they stand then. Its wall bound is `CODEMODE_TIMEOUT_MS`
    (`owner/src/codemode.ts`, slice LIM raises it to 300 s there), also checked at each
    tool call: pi-codemode's timer never fired in workerd while a script called tools back
    to back (623,807 calls in 101 s against a 2 s bound), so the object ends the execution
    itself. The answer counts calls by outcome, lists the calls of tools that change
    something (`landed`, run-length), and is refused `answer_too_large` past
    `CODE_ANSWER_MAX` (31 MiB as V8 serializes it, under Workers RPC's 32 MiB).
  - **Digests:** `inbox_digest` (the caller's unread mail from its own acknowledged cursor,
    and the rooms' rows past an owner-wide change cursor) and `board_digest` (the board's),
    each row as its head (`head_chars`, 512 by default) with its etag, and each delete or
    clear as the row's deletion. Every collection write, delete and clear takes the
    owner-wide `collection_clock`'s next change (`collection_rows.change`, and the erase
    entry's `collection_history.change` with the erased private row's writer), each indexed
    on `change` alone; held to `read_graph` and a private row's writer.
  - Found by review, outside slice M: a delete's or clear's history entry for a key never
    made again outlives the row, so history rows are bounded by keys ever erased, not by
    `maxOwnerRows x maxRevisions` as the owner-bound guard assumes. Owed to slice LIM's
    history-in-bytes change.
  - Owner-bound guard: 42.3% (4,232,734,503.75 bytes); with every proposed raise 49.98%
    (4,997,921,365.75), 2.08 MB under half.
  - Tests: `owner/test/code-door.test.ts`, `owner/test/digests.test.ts`, grants' profile,
    `doors/test/profiles.test.ts` (the real MCP client and hooks Worker), the tool guard's
    profiles; the doors' tests now run the real hooks Worker behind the object's `HOOKS`.
  - Owed by slice L (dark deploy), below: H2's probes and the live connector tests.
  - Owed by the owner surface (tasks 2), done in the app slice: `grants/set` names a
    client's profile, and `policy/write` the owner's `tools.custom` and code mode's switch.
- [ ] H2 live, narrowed to code mode (its probes built by slice L as
  `platform/deploy/h2-probes.ts`, run by `deploy.ts --yes --h2` once the doors are routed
  and two probe owners hold keys; owed to slice L's dark deploy, which needs the
  founder's OK to deploy the hooks Worker): probe what workerd cannot show: a script past
  its interrupt budget against the Worker's `cpu_ms`; the blocking isolation (10 s of a
  script's compute against another owner's); whether `exchange` calls count as
  subrequests of the owner object or the hooks Worker; and whether pi-codemode's own
  deadline timer fires in production while a script calls tools back to back (in workerd
  it did not, so the object checks the clock at each call, which stays); and, while a
  script loops tool calls, whether a revoke, an owner app read and the alarm reach the
  owner object promptly and the script's next call is refused (workerd starved every other
  call, other owners' objects too, until the script ended).
- [ ] Live connector tests (owed to slice L, `ui-test`): Claude and ChatGPT each add
  TinyAssets as a custom connector and see exactly the custom profile, `code` among it, and
  run one script of two tool calls; the owner names the client `directory`, and it lists
  no `code`; each calls `inbox_digest` and `board_digest` and acts on what
  they answer (an ack, a claim); a revoke refuses the client's next listing.
- [x] Erasure's order: mark the owner erasing, end every run (today the sandbox's egress
  streams, the app's sockets and each live run's cancel through the sandbox, slice R's;
  slice S adds the sandbox's kill and the session files' purge), then
  `deleteAll()` the owner. The tombstone that keeps a stale binder from making an erased
  owner again came with the admission row in F1; its door is slice 10's. Erasure
  completeness needs a record outside the owner object (the directory's or the
  identity's deletion: the owner's AuthKit identity and directory entries removed, so no
  sign-in or inbound route resolves the name again), or explicit platform admit at
  sign-up. The facet probe's Q8 (run 41247617223, `facet-probe.md`): with no crash the
  order is right (20 of 20 wiped and written); production's `ctx.abort()` rolled back
  everything since the last durable point, even an awaited `deleteAll`, so the crash
  window was not measured (workerd shows it: the delete kept, the tombstone lost). The
  window stands until slice 10's outside record closes it. Gate: the erasure door SHALL
  NOT ship before that outside record, or the explicit platform admit, exists; until
  then no door reaches erasure (`erase.ts`). **Closed by slice L, with both:** the erasure
  door (`POST /app/api/account/erase`, in session, confirmed by the owner's words;
  `doors/src/erase.ts`) writes the directory's `o:<owner>` erased first, deletes the
  owner's `k:`, `h:` and `i:` entries and revokes every OAuth grant it holds, then the
  object's `ownerErase`, then deletes the AuthKit identity (a failure there is retried at
  that identity's next sign-in, which is refused). A wiped object that lost its tombstone
  is `pending`, never admitted, and no sign-in admits the name again
  (`doors/test/doors-live.test.ts`). After slice L's review (H3, M3) the door only marks:
  the object's mark is durable first, and the object's own erasure job puts the owner on
  the erasure queue at the mark and at each attempt until the doors confirm, and retries
  every outside step until each is done: the doors' consumer writes the directory, deletes
  the keys and hooks, revokes the grants and deletes the AuthKit identity; the job itself
  kills the sandboxes (failing loudly without the sandbox binding) and deletes R2 by
  prefix. Each step is safe to run again, so a half-done erasure always finishes. The
  former residual (a location whose KV cached the owner's missing `o:` could admit a
  crash-wiped name once) is closed: the wipe waits for the doors' confirmation, which
  follows the durable `o:` erased, and a day under the mark (`PUT_GRACE_MS`), far past
  KV's 60 s cache, and a marked object refuses the platform's admit (review L2).
- [ ] 0c (live, slice L): the dark deploy of the owner and gateway Workers. The kit is
  built (`platform/deploy/deploy.ts`, all six Workers with no route) and runs from the
  manual workflow `.github/workflows/platform-deploy.yml`, which the founder dispatches
  from `main` in the main-only `production` environment; its secrets come from that store,
  never a host (review H2, research 3c). A failed lookup stops it, never reads as absent; a held
  `GATEWAY_KEYRING` is never generated or replaced, and is rotated only by adding a key;
  the control record is written only when the API answers it is absent; a later deploy
  uploads a version and deploys it, never touching a route. A `plan` job prints the plan
  with the Cloudflare token alone; the `apply` job needs it and waits on the `production`
  environment, so a required reviewer approves after reading it (review D1), and holds
  the handles canary's token (D2). `--routes` hands the doors' routes over one at a time
  through Cloudflare's routes API, a held pattern moved in place and another Worker's
  narrower one deleted only after the doors' wider one exists, so no path is ever served
  by nothing (D3, tested against a fake of the API). `--h2` runs the probes alone. The
  founder's run is owed (`platform/deploy/checklist.md`).
- [x] Code mode's switch (slice M): the MCP door's `code` tool refuses (`code_mode_off`)
  unless the owner's policy turns `hooks.codemode` on (`owner/test/code-door.test.ts`).
- [ ] `test_hygiene_gate` reads no platform TypeScript test (slice D review L4): it
  reported "tests added 0, removed 0" for a slice that removed 21 test files, so a lost
  test there is caught only by review. Follow-up for the gate: count `it(` cases under
  `platform/*/test` and `platform/*/guards` against the base.
- [x] Limits (slice LIM, ADR-035): the placement rule (a body past 2,048 bytes, a
  collection row's, a history entry's or a message's, lives in R2 through the files plane,
  up to 8 MiB in two chunks, written R2 first then its reference, a crashed placement's
  upload expiring in 10 minutes, charged to its writer's files scope within the tier's
  storage, retention making room first); the collection rows and history counted in
  laid-out bytes, tombstones among them; one recipient a message (the key); the
  mailboxes' caps within the ceilings (`ledger_full` when only unread mail is held); grant
  scopes counted in bytes; the collections' and ledger's byte caps and the egress
  per-minute rates removed; then every raise, and the files' `max_bytes` the tier's
  storage. The erasure refuses uploads and puts under its mark, waits out the puts in
  flight, and deletes the owner's R2 namespace by prefix before and after its wipe.
  Merged after slices ID and M: a mail to an own agent is bounded by the body bound alone,
  its run reading the body from R2 as it starts (a waiting run's input row keeps only the
  mail's reference); a long reply is placed before the settle's unit and posted in it by
  reference, so a crash leaves an orphan upload for the sweep, never a lost reply; the
  digests and a mail chain read placed bodies; slice M's change orders are counted in the
  collections' charges. Round 3: a pruned deletion resets a feed reader behind it
  (`erasure_floor`, `reset`); an egress reservation is beaten by its door every minute and
  lost three beats on, the day's deadline kept; one 16 MiB request-body ceiling (API, owner
  surface, egress door, gateway) with bodies at half of it; a race between an events post's
  units refuses the post (`events_raced`) so no reply is lost; the erasure holds its mark a
  day before its R2 step and wipe, its Lifecycle job waking it (no in-memory wait); a conflict copy's free name in one range
  read; a placed write past a chunk mid-delete begins again.
  Owed: the owner export (slice P) reads the placed bodies' chunks with the files'; SSE-C
  on them with the files' chunks; live, R2 read latency from an owner object and the
  longest put in flight against `PUT_GRACE_MS`; slice L's isolate memory probe, with a
  16 MiB request body; a live check of `limits.subrequests` at
  10,000,000.
- [x] STARTER The default agent every new owner gets (the research: `starter_research.md`,
  `ai_storage_research.md`, `okf_update_research.md`, `scratch_storage_research.md`; the
  founder's answers of 2026-11-03), in workerd, nothing deployed:
  - The v1 starter, one package of data (`platform/starter/`, version 1): `main`'s
    `AGENTS.md`, `agent.json` (`engine` `auto`, every run scope, `run_graph` among them),
    `SOUL.md` and `USER.md` (personal: seeded once, never updated), and the shared skills
    `knowledge` (OKF 0.2's core in our words, the spec vendored byte for byte at
    `GoogleCloudPlatform/open-knowledge-format@ad30107`, a provenance line first),
    `schedule`, `rooms-and-board`, `onboarding` and `scratch` (`$TA_SCRATCH`,
    `/scratch/<agent>`). `bundle.mjs` makes `starter.json`, which the owner's object
    carries; the starter guard holds it to its sources and every file to every engine's
    rules (Agent Skills frontmatter, `AGENTS.md` plain and under Codex's 32 KiB).
  - Seeding and updates (`owner/src/starter.ts`, migration 3, the `starter` lane): an
    admitted owner with nothing reconciled is seeded as the owner's own writes (history op
    `starter`), a receipt a path, `agent.json` last; a newer carried version replaces
    unedited managed files, writes `<file>.ta-new` beside edited ones with one notice,
    never restores a deleted one, never touches a personal one, and delivers a widening
    (more scopes, a script, a marked version) as a proposal beside its file; undo is the
    file's own history. A failed pass keeps what it wrote, tells the owner once a version
    and is tried again; `starterReceipts` is the owner's read. The tests' objects carry no
    package unless they test it (`internals.starter`; `SEEDED` is production's).
  - Self-scheduling: a run's MCP door serves `run_graph`, `run_status` and `run_cancel`
    (the `run` profile but `converse`), an owner-started or scheduled run as the owner held
    to its agent's `run_graph`, a client's run to that client's grant; what a run launches
    runs unattended. A scheduled run's result posts to the owner's room with its agent
    (`dm:<the agent's id>`, main's for main), once; an empty one posts nothing.
  - The MCP door reads files: `file_read` (`fileText`, UTF-8 windows at a character),
    `head_chars` on `file_list` (small files), and lexical, bounded `search` and `fetch` in
    ChatGPT's deep research shape (`file-search.ts`; whole words; paged by matches, every
    match reachable; `SEARCH_SCAN`, `SEARCH_ANSWER_BYTES` and `SEARCH_QUERY_BYTES` each a
    measured technical ceiling in `limits-audit.md`; `fetch` serves every id `search`
    answers), held to `read_files`, in the directory, custom and run profiles. A run's
    `file_list` is held to its grant (it went to the sync token's check).
  - Review round 1 (rvsa, REQUEST_CHANGES), its attack tests now the fixed behaviour
    (`owner/test/starter-review.test.ts`): a run's `run_status` and `run_cancel` held to its
    agent's `run_graph` (H1); only the owner's own schedules post to `dm:<agent>`, a
    client's result read through its `run_status` (M1), and `NO_REPLY` posts nothing; a
    widening of an edited file is a proposal (M2); `search` split into words once and paged
    by matches, its worst case measured (`guards/search-cpu.test.ts`: 766 ms of CPU at
    5,000 matching 4 KiB files and a 1 MiB query) (M3); the rooms skill says how mail
    between agents is answered (M4); the OKF watcher opens, readies and labels with
    `MERGE_ATTRIBUTION_TOKEN` as `main-red.yml` does (fails loudly without it), asks the
    founder's login, quotes upstream text escaped inside a fence, checks it for hidden
    characters, and holds least permissions with pinned actions (M5, L6); a retired path's
    deletion is kept (L1), an unadopted proposal stays one (L2), a door that needs an agent
    awaits the seed (L3), the blank letters and separators are hidden characters (L5), and a
    README- or tag-only upstream change ships no starter version (L7).
  - The OKF watcher (`.github/workflows/okf-watch.yml`, `platform/starter/okf-watch.ts`):
    daily, a content diff of the canonical spec's blob, its README for a relocation
    banner, new tags and hidden Unicode; one draft pull request a change, a cooldown of 7
    days, then ready for the automated review, or the founder asked when hidden text could
    widen what agents do; nothing merges on a version number or by the workflow.
  - Claude Code's auto memory off (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in its record), which
    moved the template's tag: rebuilt and pinned live, `tinyassets-sandbox:sc0a20c7fb88fb4d1` (after the merge with STORE).
  - Live, in three throwaway sandboxes from it (about 7 sandbox-minutes, each killed and
    gone): each engine's CLI, from `main`'s directory with the starter projected, against a
    local capture server as its base URL with a dummy key. Claude Code 2.1.309, Codex
    0.166.0, Gemini CLI 0.66.0 and pi 1.4.0 each put `AGENTS.md` and all five skills in the
    request they built, with no `CLAUDE.md` fallback needed; Kimi Code 2.4.1 (no key
    credential) read `AGENTS.md` before stopping at its sign-in.
- [ ] Owed by the starter slice:
  - **Deviation, ruled 2026-11-03 (review M2, L10):** the research named `policy_propose`,
    but it can't apply files (a held policy proposal applies only as a whole owner policy
    document, `Policy.write`); `.ta-new` plus a notice is the proposal. Nothing widens until
    the owner, or their agent at the owner's ask, adopts it; the `starter_proposal` notice
    and the starter's `AGENTS.md` and `onboarding` say how (ask the agent, or replace the
    file). No file-applying proposal kind is built.
  - **ChatGPT reads the first page of a search only** (review L8): deep research and
    company knowledge do not follow `next_cursor`, so past `SEARCH_SCAN` entries they see
    the first page in path order. A known gap: scanning `/workspace/knowledge` first is one
    answer.
  - Review round 2 (rvsa): a widening at a path the owner already holds is a proposal,
    a first seed over the owner's own `agent.json` included (R2-M1); a withdrawn widening's
    unadopted `.ta-new` is removed with one `starter_withdrawn` notice (R2-L1);
    `bundle.mjs` refuses a path dropped without being retired (R2-L4); `SEARCH_ANSWER_BYTES`
    bounds the whole serialised answer (worst case measured at 14.2 of 16 MiB); the
    watcher tells the founder by a bot comment and Pushover, never by a review request
    (the token is the founder's own), never failing the run on a notification, with the
    user token only in the steps that push or call `gh`, and its loops touching only its
    own `okf-watch/` branches (R2-M2, R2-L3).
  - Review round 3 (rvsa, APPROVE on the merge of main): merged slice L (its migration 15;
    the starter's then after it; after P squashed the baseline, it is 3, after STORE's 2);
    SEEDED test owners
    admitted as L's sign-up admits them; a retired path's unadopted proposal withdrawn
    (F3-retired-proposal); the race-free seed wait in the suite (F3-seed-wait); the bundle
    guard says loudly when main's history is missing.
  - **The shared brain** (`shared_brain_research.md` §6.2, items 1 to 5; starter version 2):
    the owner's profile moved to the owner-wide `/workspace/USER.md` (`moved_from` the retired
    `agents/main/USER.md`: an owner who filled the old one is seeded with a copy of it, the
    old file left, and told once; an untouched one gets the new template); the workspace's
    map `/workspace/index.md` seeded (OKF index lines, no frontmatter); a new
    `shared-brain` skill (look here before asking, write answers here, claim before acting;
    a newer TinyAssets entry wins over the agent's own memory); the `rooms-and-board` board
    section's create-only claims, `until:` leases and takeover by compare and swap (review
    R4-M1, R4-L3: claims are `private: false`, so a colliding agent reads who holds one; a
    holder extends `until:` on the row's etag before it passes and stops on a conflict,
    reads the row again right before the act, and a taker first checks whether the act's
    result already exists); the
    `knowledge` skill's scope the whole workspace, with the derived-map rule. A personal
    file may now sit outside the owner's trees; a managed one may not. The doors' part
    (client instructions, `file_write` and `file_edit`, descriptions, the connect page,
    plugins) is the BRAIN slice's, after STARTER and STORE merge.
  - **Goals and proactivity** (`goals_research.md` §6.1; starter version 3): a new `goals`
    skill (one page a goal under `/workspace/goals`: Done when, Agreed, Not wanted, Now,
    Links; the owner's own word alone sets the first three; its steward works on an active,
    confirmed goal unasked, inside the workspace, an outward act only as Agreed names it, and
    spending or anything irreversible always asks); the goals' index `/workspace/goals/index.md`
    seeded (personal: `# Active`, `# Paused`, `# Ended`); `AGENTS.md` reads the index at the
    start of a conversation, its Proactivity section is initiative per goal and interruption
    per owner, and its Trust names the goal pages; `USER.md`'s off/low/high dial and quiet
    hours become "Messaging me unasked", in the owner's words (more, less, none, and when
    not), which come before the default rule; onboarding asks what the owner is working toward;
    the schedule skill's brief is a pass over the goals at the owner's times, a dated step
    may get its own `at` run; an app's own goal or loop command is pointed at the goal's
    page; a board row names its goal on its second line; the
    shared-brain skill reads the goals' index before acting. `/workspace/goals` joins
    `OWNER_TREES` (a narrowed grant changes no goal; the default grant writes them; a client
    sandbox's always-read set stays the abilities' trees, `ABILITY_TREES`). One link rule for
    the whole workspace: root-absolute from `/workspace` (the knowledge skill and the map
    changed to it; a guard holds every link in the package to it). No counts, default
    schedules or fixed numbers in the text: the knowledge skill's page and index sizes
    became "short". The goals clause in the client instructions and the plugins are BRAIN's.
  - **Connect is the only step** (`autoconnect_research.md` §5.2 STARTER; starter version 4,
    text only): onboarding gains "their other assistants" (ask, name the connected ones,
    offer the rest each its connect step: `connect_url` from `agents_list`, which BRAIN
    builds from `apps.json`, or the connect page) and the profile's `Onboarded:` marker, so
    it runs once for all of the owner's agents whatever was answered (a profile with a time
    zone counts as onboarded); the shared-brain skill's rule 5 offers an assistant not
    connected once, records "Offered to connect" in `USER.md`, and after a no "Don't
    suggest connecting", so no agent offers it again; connecting closes the gap. `USER.md`
    gains "Assistants I use" and "Onboarded"; `AGENTS.md` tests the marker. No text asks an
    agent to save to its own memory or install anything. `apps.json`, `agents_list.apps`,
    the seed-awaiting reads and the instructions are BRAIN's.
  - The OKF watcher's review has no consumer of its own: a readied proposal waits for an
    agent review session's receipt, as every pull request does (founder item, if an
    automatic reviewer is wanted).
  - **Lands after `platform-store`**: the `scratch` skill describes STORE's runner
    (`TA_SCRATCH`, scratch caches, the cleared notice).
  - **Codex ignores `OPENAI_BASE_URL`** (0.166.0, live above): with only the record's
    variable its request went to api.openai.com; `-c openai_base_url="..."` reached the
    capture server. Codex's API-key runs need that argument in its record (slice E's
    checker takes base URLs as variables only), before any Codex key run.
  - Kimi's skills were not read before its sign-in stage: check with a signed-in Kimi in
    the founder's live test.
  - Slice L's explicit admit (pending owners) must re-sync the schedule, so a new owner's
    `starter` lane is armed the moment it is admitted.
  - Merged after STORE (main `9c0c7d038`: P's squashed baseline, STORE's migration 2): the
    starter's migration is 3; `starter_receipts` and `starter_state` are exported, with
    capture triggers (`LOGGED`) and their owner-bound terms (361,189 bytes, inside the
    starter's quarter of the room under half; no `REPLACE`, upserts only); the seed writes
    through the files plane as the owner, so its files count in the one storage number,
    sit in every generation, and a seed refused `storage_full` stops, tells the owner once
    and is tried again. The template is rebuilt and pinned once, after this merge.
  - The owner app's browser test runs the files Worker as production binds it (the doors'
    FILES its upload entrypoint, the owner object's its whole service), so a new owner's
    seed really runs there, and asserts it landed: `main`, `/workspace/USER.md`,
    `/workspace/index.md` and `agents/main/agent.json`, with no `starter_failed`. The
    doors' unit suite asserts the same through its own bindings (shown to fail with the
    files binding taken away), and the owner's that a seed unable to reach its store tells
    the owner and keeps its retry. The browser test's own agent is `helper`: `main` is
    seeded.
  - The founder's live test (8): onboarding writes `SOUL.md` and `USER.md`; "remember X"
    lands in knowledge and is recalled on another engine; the brief made by the agent
    itself posts to `dm:main`; a main agent finds the knowledge with `search` and `fetch`
    (ChatGPT deep research, `ui-test`).
  - Later, by the research's order: the knowledge lint script; `sync-ignore` defaults; the
    agent's own `mcp.json` merged into the run config, then a `browser` skill; the standing
    comparison against the four main agents.

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
  store while held. Slice D deleted the thread stores; P1's subrequest count still sets the
  owner Worker's `limits.subrequests`.
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
  awaited `deleteAll`). The design was: top-level `ThreadStore` objects hosted by
  `ThreadHost`, which slice D deleted with the in-object loop; Q1's ceiling and Q8's
  window still hold for the owner's object.
- [x] Per-owner SSE-C data keys on every chunk get and put (slice P, in workerd): Cloudflare
  documents `ssecKey` on the Workers binding's `get`, `put` and `createMultipartUpload`
  (r2/examples/ssec, the Workers API reference, read 2026-11-03), so it is SSE-C, not
  client-side encryption. Each owner's data key is 32 random bytes the files Worker mints
  for its namespace and the object keeps only wrapped (`file_space.data_key`: AES-256-GCM
  under HKDF-SHA256 of the files Worker's own `FILES_KEYRING`, bound to the namespace and
  kid; `files/src/datakey.ts`); every put and get presents it, a keyless or foreign one is
  refused before R2; an existing key is checked by a get under the key, never a keyless
  `head`. Erasure deletes the nightly manifests (each carries the wrapped key) before the
  wipe, so a stray chunk is unreadable. Miniflare ignores `ssecKey`, so the files tests
  hold the Worker to R2's documented answers (`files/test/ssec.ts`). A chunk's R2 name is
  HMAC-SHA256 of its hash under a key derived from the data key (`chunkName`), so a listing
  of the bucket confirms no known file; a manifest's root keeps the wrapped key in the clear
  and its part list sealed under the data key (review L5).
- [ ] Owed live (slice L): a probe of `ssecKey` with `onlyIf` and `sha256` on a put, of a get
  with another key and with none on an SSE-C object, and of a ranged SSE-C get. Should SSE-C
  not combine with the create-once put or R2's `sha256`, set `FILES_CIPHER` to `aes-gcm`: the
  fallback (AES-256-GCM per object in the files Worker, `files/src/cipher.ts`) is built and
  tested, and the config guards take either; the files
  Worker's create-once put on the live bucket (`onlyIf: { etagDoesNotMatch: "*" }` as a
  wildcard, and which R2 checks first when a key exists and the bytes miss the `sha256`);
  `FILES_KEYRING` generated and set at the files Worker's deploy. Each droplet volume
  imported into the manifest and bucket in the cutover window (9).
- [x] Key rotation's re-wrap (review M4): the files Worker's `rewrapKey` (the same data key
  wrapped again under the active kid) and `rewrapManifestRoot` (a root's wrapped key
  re-wrapped in place, only if the root did not change meanwhile); `putManifestRoot` always
  writes the active kid. The owner's `dataKeyRewrap` (the platform's door, as
  `admissionSet`) re-wraps `file_space.data_key` and then every finished manifest root, an
  idle owner's too, so no copy under a retiring kid outlives the sweep.
- [ ] Owed by slice L's admin path, at the first rotation: add the new kid as active, call
  `dataKeyRewrap` for every owner, and remove a kid from `FILES_KEYRING` only after every
  owner's call has answered (the object's key and each manifest root under the new kid).
- [x] The owner export and the nightly manifest (slice P), one artifact: a generation
  (`backup_shape_research.md` (d); `owner/src/backup.ts`, `changelog.ts`), format
  `tinyassets-export` version 2 (`export.ts`). A fuzzy base copy of every row of every
  exported table, read in bursts and put as whole-line parts, and a change log of full row
  images captured from S while the base is read (one trigger an exported table and event,
  `LOGGED`, in the writing unit's own transaction), shipped to R2 after every part as
  segments (`o/<ns>/m/<gen>/l/<a>-<b>`), each recorded pending, put, then acknowledged and
  deleted; the unit that reads the base's last page fixes E, the unit that records it turns
  capture off, and the root names the parts, the segments through E, S and E. Read last line
  per key wins, it is exactly the owner at E, however the owner writes meanwhile, and a crash
  anywhere repeats only what was not acknowledged (reviews M1, N1, R4-1 to R4-3). The export
  is a generation (the one running, the newest while nothing changed, or a new one), read by
  a cursor of the generation and an object's index, so any cursor resumes and any number of
  exports read at once; `owner_exports` pins its generation for the export's 7 days. Every
  table is exported (each column decided) or kept out with its reason; credentials, hashes (a
  policy receipt's session hash too, review M2), secrets and the wrapped key never. The one
  bound is `LOG_BYTES` (68 MiB, a quarter of the room under the database's half): past it, or an image past
  `IMAGE_MAX` (1.5 MiB, the widest real row's image, review L5-1), capture stops and the
  generation fails with one notice; the write lands. A migration while a generation runs
  gives it up with one notice (`manifest_schema`) and begins a fresh one under the new
  schema, never mixing two; and no `REPLACE` meets an exported table (a guard), since its
  delete fires no trigger (review L5-2). A
  generation a pass did not finish goes on `MANIFEST_CONTINUE_S` (5 s) later. Deleted bytes
  leave the owner's number at once, and garbage past the owner's storage figure is collected
  early, oldest first, never a chunk the newest finished generation names while it is under
  `MANIFEST_PROTECT_S` (2 days) old, and garbage waiting on a failed delete's retry never
  re-arms the files lane at once (reviews H1, N2, N3). The owner alone, in its live session:
  the owner surface's `export`, `export/chunks`, `export/end`. Nightly, only when something
  changed (the first change sets the lane's job), so an idle owner's object is never woken
  for it; past `MANIFEST_PARTS` objects it is given up once and the owner told.
- [ ] Owed by slice L: a live probe of the owner's sustained write rate against the change
  log's ship rate (R2 puts of 4 MiB segments), against `LOG_BYTES`.
- [ ] Owed (founder's cost decision): capture always on (`backup_shape_research.md` (c)): the
  same code with the log never off, giving a recovery point of minutes instead of a night.
  Each exported-row change then costs an image insert and a delete always: about $2 per
  million changes (Durable Objects bill rows written at $1 a million, deletes count); a heavy
  owner (about 12 changes a second) about $62 a month, a light one (an hour a day at one a
  second) about $0.20; R2 at most about $1.17 a month for a put every 10 s. Today, capture only
  during a generation costs the same heavy owner about $0.70 a month.
- [ ] Owed by the app slice: the storage view's line "Deleted files are kept up to 30 days,
  within your storage; past that the oldest go first.", labelled "deleted content", from
  `storageUsage`'s `deleted_content` (`kept_bytes`, `kept_up_to_days`), and the storage
  setting showing that a lower figure shortens recovery.
- [ ] Owed (founder's spending decision): a manifest copy outside this account and bucket
  (3-2-1); today's recovery point lives in the same bucket.
- [x] Sandbox time metered per owner (slice P, `owner/src/sandbox-time.ts`). The truth is
  E2B's: its signed lifecycle webhook (`POST /hooks/e2b/lifecycle`, `doors/src/e2b-hooks.ts`)
  is verified by E2B's signature scheme against `E2B_WEBHOOK_SECRET`, parsed strictly and
  routed by a sealed route the sandbox carries in its metadata (`ta_route`, `sealRoute`),
  and each paused or killed execution's billed ms, vCPU-ms and MB-ms is counted once by its
  `sandbox_execution_id` (a pause and a later kill may both carry it, review L1; each live
  sandbox's last execution kept until its kill, review N4), and only while the event's own
  signed `timestamp` is inside a day (`BILLED_WINDOW_S`), every execution counted kept that
  long by 8 bytes of its id's digest (100,000 of them), so a replay is refused by its time
  or its id (review L2); past that, an execution is counted anyway and tallied
  `unverified_executions` on its day, which `sandboxDays` shows (review N5); the body is read through a byte count that stops at its bound (review L3); split
  across UTC days. The estimate, and the fallback: the sandbox Worker
  logs each create, resume, pause and kill (`SandboxControl`, `meter`), which the object
  reads on its `sandbox` lane onto each run's receipt (`sandbox_s`) and each day.
  `sandboxDays` shows both. It limits nothing.
- [ ] Owed by slice L's deploy kit: register E2B's lifecycle webhook
  (`POST https://api.e2b.app/events/webhooks`, events `sandbox.lifecycle.paused` and
  `.killed`, the URL `https://tinyassets.io/hooks/e2b/lifecycle`) with a generated
  signature secret, set as the doors' `E2B_WEBHOOK_SECRET`; and one platform-wide pull of
  `/events/sandboxes` to catch deliveries E2B gave up on. Probe live: whether a kill of a
  paused sandbox carries the pause's `execution` (and the same `sandbox_execution_id`), and
  E2B's retry timing against `BILLED_WINDOW_S`.
- [ ] Owed by slice L's erasure door (`account/erase`, which marks first and answers at the
  mark): answer `erasureAnswer(markedAt, wipedAt)` (`owner/src/erasure.ts`, review L6):
  `erased` once the wipe ran, and `complete_at` = `max(mark + PUT_GRACE_MS, wipe)` +
  `RECOVERY_WINDOW_S` (30 days), so the answer at the mark already names the earliest
  completion and a retry that wiped later names its own.
- [x] One storage number (founder, 2026-11-03; slice P): every byte of user content counts
  toward the tier's storage wherever it lives. The inline bodies of collection rows, their
  history and mail are counted by triggers (`body_bytes`, owner-wide and in each writer's
  files scope), and the bound is `stored` = chunk bytes + small files + inline bodies against
  `max_bytes`; collection writes, posts and mail replies are held to it (`Files.held`),
  retention first (history no row still is, then acked mail, inline or placed), then
  `storage_full` (slice STORE's one wall). `storageUsage` (the app's `POST /app/api/storage`) answers used, the limit and
  where it is (files, messages, collections).
- [x] The sandbox Worker's side of `meter` (slice P, after slice S merged): `SandboxControl`
  logs each create, resume, pause (its own, and one E2B made, as first seen) and kill (a
  sandbox gone while it ran) with its authority and time, and drops what a read's cursor
  passed.
- [x] Slice STORE (founder, 2026-11-03: storage is the only wall):
  - `storage_full`, one code and one set of words at the tier's figure, 507 at the doors;
  - the agents' scratch on the sandbox disk (`/scratch/<agent>`), never synced or counted, kept as a cache, let go by `scratch_retention_days` and the least recently used under disk pressure;
  - sync-out that streams, the sessions first, with the owner's `/workspace/.ta/sync-ignore`;
  - a full disk that never loses the run.

  Review round 1 (REQUEST_CHANGES), fixed: a refused upload is refused alone with the doors' whole message, the mirror saved after each committed batch, `ta.storage_full` reported once; `uploads_open` (409) apart from `storage_full`, an `upload_abort` door, a lease's open uploads aborted when it ends; "used" is what is stored; sync-in streams to a temporary file (a file past the VM's memory synced in a test); sync-out's memory independent of the path count (an on-disk mirror, a walk a directory at a time); a file's identity checked at commit; sync-ignore edits receipted and noticed once, the rule file never ignored, default patterns stopping new paths only; a linear-time matcher; the reserve let go before the sessions are staged, and only changed sessions staged; scratch stamps per agent; the superseded template tags deleted.

  Verification round 2 (REQUEST_CHANGES), fixed: sessions staged by the sync mirror's hash, so one an earlier sync-out did not commit is sent again by the next run; a sync's uploads name their lease, and only those end with it, on every path that ends a lease (a new lease's taking among them), never a client's own write; each sync-ignore change's receipt and notice name who made it, and a new line that holds back kept files says how many and which, each sync-out reporting them too; a failed control read keeps the last scratch retention read, loudly; a change time within the filesystem's coarsest tick is never trusted, so the mirror's hash is of the bytes sent; the reserve's note says staging happens before the sync lock.

  Verification round 3 (REQUEST_CHANGES), fixed: the sync-ignore notice never scans the owner's files in a write (a sync's write carries its sync agent's count; any other says files may be held back), the matcher a set lookup for literal lines; the agent form's subscription signed in inside the sandbox restored beside APP's filter, with `test:app` cases for both paths; a session hashed again only when its size or change time moved; a secret-refused session reported once; a client named by its display name whatever its id.

  Verification round 4 (REQUEST_CHANGES), fixed: the run's heartbeat posts through sync-in and sync-out (the sync agent a child the runner waits on without blocking), so a sync longer than the lease keeps it; the matcher fast with no cap on lines (pattern lines behind a length and character check, each name and directory matched once), the reviewer's hostile lists 0.012 to 0.048 ms a path, a slow match said in the run's report; the notice's "may hold back" sentence on every added line, the sandbox's count only for the owner's own lease. Then main (slices L and SIGNIN) merged: the lease column is migration 17, and after slice P's baseline, migration 2.

  Verification round 5 (REQUEST_CHANGES), fixed: pattern lines bounded together by their `?` pieces between stars, `QUESTION_PIECE_CHARS` (384, derived from the measured worst case, a name past the basic plane, at the highest `max_entries` against E2B Hobby's hour; round 6 moved it from all pattern characters, which turned off honest lines), text past the basic plane matched the same linear way, those past it not applied and said in the report and the notice; the matcher's memory bounded by its keys' bytes (16 MiB); the sync child's stdin errors handled, so a child that dies before reading its job ends the run loudly.

  Verification round 7 (R6-1, research first, `matcher_research.md`): the matcher replaced by its compiled shape, indexes for lines whose cost does not grow with their count and one bit-parallel automaton for the rest, behind `AUTOMATON_STATES` (8,192) and `LIST_BYTES` (8 MiB, from the heap limit and the agent's peak measured in a sandbox); the lines read as git reads a `.gitignore` (the founder's ruling), `git check-ignore` the test oracle over GitHub's 310 templates and random lists; the automaton's step a standing check of the template build.

  Verification round 8 (R7, APPROVE with fixes): the step measured on the automaton's slowest branch (`/**/` lines, `STEP_NS` 6) and the audit's arithmetic restated (both automata within the 8,192, the indexes' 184 s, the slack); a folder's Aho–Corasick depth in the cost bound, tested 2,000 folders deep; the worst memory class (heads diverging, 45.0 a list byte) recorded, so `LIST_BYTES` is 6 MiB; a file or folder whose name is not UTF-8 said in the run's report, its bytes escaped, and in the owner's notice, never synced in silence; the heartbeat test seconds long, so `platform-tests` keeps within its ten minutes.

  Verification round 9 (R8): a CLI's line shaped as the runner's own event (`ta.…`, `ta_cut`) refused as `ta.cli_refused`, never posted as it, every runner event type tested forged; the unnamed-files notice in the object's own words; `platform-tests`' guard holds each leg's steps, keys and `defaults.run` exactly, the reviewer's mutations its test.

  Verification round 10 (R9): no tracked `.npmrc` nor `package.json` `config` may set `script-shell`, `ignore-scripts`, `shell` or `node-options` (a guard, the reviewer's `.npmrc` its test); the wall's held bytes less the chunks the change itself moved to uploading, so a begin that reclaims history meets `storage_full`, never `uploads_open`.

  Research: the STORE research and its cross-reviews. Probes:
  - `latency-probe.md`: option A measured;
  - `sandbox-probe.md`, "Scratch and the disk".
- [ ] Owed by slice STORE:
  - **Option B of the latency probe** (R2 get, put, list from an owner-shaped object) needs a token with Workers R2 Storage: Edit. The index decision (shards or R2 trees) waits on it, and the count caps stay until then. Its `probe-latency-run` branch is made again only for that run; afterwards the probe's workflow and scripts are removed.
  - **Old versions, collection history and read mail moved to R2 segments** (mail never evicted silently), encrypted under slice P's per-owner key: after P lands. Its schema baseline and its key are the base.
  - **Sync-in of the working set alone** (today it materializes the whole workspace), held for the scratch design.
  - **The starter package's agent instructions** naming `$TA_SCRATCH`: slice STARTER carries the scratch guidance, in its `AGENTS.md` and its scratch skill (the run door's MCP instructions carry it today).
  - **`linux_oracle.py`** for the scratch modes and the reserve: no Docker daemon in the build session. The runner's root tests cover the modes.
  - **The resume after hours** with 5–10 GB of scratch.

## 7. Wakes and outside agents

- [x] Alarms, doorbells for ChatGPT and Claude, the board with the W8 decisions. Built in
  slice W and its review: wake kinds are data (`owner/wake-kinds.json`, `wake-kinds.ts`),
  each record citing its vendor's page and limits: ChatGPT MCP Events (the client
  subscribes; Standard Webhooks), Claude `/fire` and Grok webhook routines (the owner
  pastes the vendor's URL and token in its session, the owner app's `agent/doorbell`;
  bearer), Muse with no push (it catches up on its own schedule; nothing polls). Mail
  rings its recipient, never its sender (slice ID's mail wakes settle against the
  `ledger_wakes` CHECK of 1 to 7 change bits). A board or room write rings only for the
  owner's own act (an outside client's or a run's rings no one, so no two agents wake each
  other without end), only agents subscribed to that board or room whose grant reads it,
  and only for a change anyone may see. Each wake folds into the agent's one open wake,
  which says what changed and the owner-wide cursor it is after, so `inbox_digest` or
  `board_digest` after it returns just the change. The wake cap is the owner's own
  setting, off by default (`agent/wake-limits`), spaced by one `wake_tat` column with no
  invented high; `ledger_rings` is gone.
- [x] The doorbell: a `WakeSender` the object constructs and its `wakes` lane calls in
  process. Its secrets are sealed by the gateway (`gateway/src/doorbell.ts`: `sealDoorbell`
  and `verifyDoorbell` on the doors' entrypoint, `ringDoorbell` on the owner's), bound to
  the owner and the subscription; the object holds the blob alone and the gateway opens it
  and makes the send, with no redirect and no body read, dual-signing with the replaced
  secret for the record's rotation window. An agent holds several subscriptions (a dot and
  a Work chat), each by the id derived from its principal, URL, event and arguments, at
  most `SUBSCRIPTIONS_PER_AGENT` (16) an enabled agent, counted in the schema and priced in
  the owner-bound guard, so no client fills another's room. The send is marked before its
  first byte and rechecked (subscriptions, grant, kill switch) just before it; a late or
  failed send settles `unknown` and is never replayed. Each subscription's outcome is its
  own: a retry rings only the ones a `429` left owed, and one that took the wake wakes the
  mail. A `429` waits its Retry-After as sent, an unreadable, zero or past one an
  exponential backoff; a `410` or `413` keeps the subscription; a record's dead-token
  status (Claude's `401`) notices the owner once, until a new token is pasted. The
  records and callback checks are `wake-records.ts`, which imports nothing but the errors
  and the records' file, and the gateway's isolation guard walks its whole import closure. A
  grant change erases a client's subscriptions only, never an owner-set token. The kill
  switch is the owner Worker's `CONTROL` read through `ControlGate`; admission is the
  object's own row (2). Callbacks to loopback, private, metadata, single-label or our own
  hosts are refused, the gateway's fetch is `global_fetch_strictly_public`, and vendor
  hosts are pinned as data where a vendor has one.
- [x] W2, MCP Events at the MCP door (`doors/src/events.ts`): `events` in `initialize` and
  `server/discover`, `events/list` (`tinyassets.changed`), `events/subscribe` with the
  gateway's signed challenge (a bounded read, a constant-time compare; a failure is
  OpenAI's `-32015` with a `data.reason`, not the sketch's provisional `-32027`), `ttlMs`
  granted as `refreshBefore`, `events/unsubscribe` by name, arguments and URL, and the
  event's own time as `timestamp`. A run's MCP door serves none of it.
- [ ] Owed live, none of it reachable from here: each vendor end to end (a ChatGPT dot and
  Work chat subscribing, a Claude `/fire`, a Grok webhook routine, Muse's catch-up); the
  SSRF probe (a callback whose DNS answers 127.0.0.1, 10.x or 169.254.169.254 is refused
  by `global_fetch_strictly_public`); Grok's webhook host, to pin as data once seen; and
  whether Claude's over-limit fire answers `429`.
- [ ] Not built, by choice: a verification cache (each subscribe verifies again) and
  retries of a `5xx` (the wake settles failed; the next change rings).

- [x] **Slice BRAIN: the doors half of the one shared brain** (`shared_brain_research.md`
  §6.2, `autoconnect_research.md` §5.2, `goals_research.md` §6.2; the founder's DCR ruling).
  - `CLIENT_INSTRUCTIONS` (`doors/src/instructions.ts`) on the `custom` and `directory`
    profiles, `RUN_INSTRUCTIONS` kept for `run`: the brain's rules in the first 511
    characters (the goals index before acting, look here before asking, `file_append` to
    add and `file_edit` to correct, a new board row to claim an act), then onboarding for a
    user with no `Onboarded` date and no time zone, and the once-only offer of an assistant
    `agents_list` shows not connected. It names only our tools, never "always", and asks no
    agent to save to its own memory or install anything.
  - The writers: `file_append` (additive, `destructiveHint: false`: read, append and write
    at one etag, reading again on a conflict; a chunked file's whole chunks kept, so any
    size; at most 4 MiB of text a call), `file_write` (`fileWrite` takes text; off the
    deferred list) and `file_edit` (one exact occurrence at the file's etag; none or several
    refused with how many; files up to `WHOLE_BYTES`, the files plane's ceiling for a write
    through the object), on every profile, each held to `write_files` and to the agent
    trees. `fetch` answers the etag. `search`, `fetch`, `file_read`, `collection_write` and
    `agents_list` say when to use them.
  - File reads and writes await a new owner's seed (`#seededFor`, after the caller's
    authority; the write-gate guard knows it), so a first `fetch` of `/workspace/USER.md`
    finds the seeded profile and a first append lands after it.
  - The vendor at sign-in (`doors/src/apps.ts`): a CIMD client by its document's host
    (verified), any other by a word of its registered name (unverified), carried in the
    grant's props and binding and kept on its grant row (migration 4: `vendor`,
    `vendor_verified`, exported, capture triggers made again). `agents_list.apps` to every
    grant, narrowed and run ones included: each `apps.json` app connected or not, with its
    `connect_url`, and unmatched clients as one line. The owner app's connected apps page
    names each app by vendor and links each one not connected.
  - `platform/doors/apps.json`: Claude, ChatGPT (with dots and Codex), Grok Bot, Meta Muse,
    Gemini and Cursor, each with match data, `registration`, connect link, sentence, steps
    and copy text, its vendor page and the date read. ChatGPT's steps are the current
    "Plugins, +, Add custom MCP server" (read 2026-11-04). The public `/connect` page built
    from it is on its own branch, `platform-brain-site`, which lands at the cutover (§9): a
    merge of `WebSite/site-react/**` redeploys the live site, and live `/mcp` is the old
    platform until then. Until it lands, `connect_url`'s anchors for Grok, Muse and Gemini
    are not on the live page; the owner app's connected apps page links them meanwhile.
    Grok's and Muse's `say` sentence asks their agent to save the TinyAssets skill: it is the
    user's own request, sent in their own chat as the connect act. Nothing TinyAssets serves to
    an agent (the instructions, every tool's text, the plugins) asks it to save to its memory
    or install anything; `doors/guards/apps.test.ts` holds that split.
  - DCR (founder ruling): served only with `DCR_ENABLED`, and then a registration is taken
    only when its `client_name` names a `dcr` vendor (Grok, Muse, Gemini, Cursor); a CIMD
    vendor's name or none is refused `invalid_client_metadata`. Production stays
    `DCR_ENABLED: "false"` until the go-ahead deploy.
  - Plugins (`platform/plugins/`, `build.mjs`, `--check` in the doors' guard): ChatGPT and
    Codex (Agent Plugins format, OpenAI extension, onboarding skill, first example prompt
    "Set me up on TinyAssets") and Claude (`.claude-plugin/`, `.mcp.json`), each the MCP
    server and the starter's `shared-brain`, `onboarding`, `goals`, `knowledge`,
    `rooms-and-board` and `schedule` skills byte for byte. `SUBMIT.md` is the founder's
    checklist; nothing was submitted.
  - Starter version 5, text only: onboarding writes `SOUL.md` only for a TinyAssets agent
    (an outside assistant has none); the shared-brain rule 2 names `file_append` and
    `file_edit` and one append near the end of a conversation.
  - The owner app follows a dropped socket (task #68): `follow()` attaches a new socket on
    any close but the doors' final ones (4400, 4403, 4410, 4440) and calls back so the
    sign-in panel and the talk view read their state again; `test:app` drops one.
  - The local proof, in workerd (`doors/test/brain.test.ts`): two MCP clients of one owner,
    a nonce one appends to `USER.md` found by the other's `search` and `fetch` with the
    seeded profile whole, corrected by `file_edit` at its etag; two concurrent creates of
    `offer:connect:grok` leave exactly one row and one conflict.
- [ ] **Owed by BRAIN, live, after the go-ahead deploy** (proof is observed tool calls and
  `writer` values, never an agent's say-so; the user's only acts are connect, sign in and
  answer):
  1. **Turn DCR on** in the go-ahead deploy (`DCR_ENABLED: "true"`). For each `dcr` vendor
     (Grok via Cursor's backend, Gemini, Muse, Cursor), the founder makes its first connect
     attempt while `wrangler tail` runs on the doors Worker (or with the doors' Workers logs
     open). That attempt's registration is refused, since its `match.redirects` is empty, and
     the doors log one `dcr_refused` line holding the app's id, its real `client_name` and its
     `redirect_uris` (bounded, escaped JSON; `apps.ts` `refusedRegistrationLine`). Record those
     redirects (and the name, if it is not matched as expected) in `apps.json` with the date,
     and redeploy: only then can it register, and only to those redirects. Confirm Claude's and ChatGPT's CIMD hosts and redirects (`claude.ai`,
     `chatgpt.com` or `openai.com`) in their first grant rows (`vendor_verified` 1).
  2. **MCP-first onboarding** (`ui-test`, Claude.ai): a fresh account that never opened
     the app; connect through `apps.json`'s Claude link; first message "help me plan this
     week". Pass: a `fetch`/`file_read` of `/workspace/USER.md` is seen; the task is done,
     then the onboarding questions in one message; `USER.md` gains the answers and
     `Onboarded:` with Claude's client as `writer`; asked about other assistants, "ChatGPT"
     gets `https://tinyassets.io/connect/#chatgpt`. Repeat with "what can you do?".
  3. **The second vendor**: follow that link, connect ChatGPT, then in a fresh ChatGPT
     conversation ask something that needs the profile. Pass: ChatGPT reads `USER.md`, runs
     no onboarding again, and `agents_list.apps` shows both connected.
  4. **Write-back and check-first, both ways** (`ui-test`, A/B): in A state a nonce
     preference in passing; pass: a new `USER.md` version with A's client as `writer`. In a
     fresh conversation in B ask what needs it without naming TinyAssets; pass: B calls
     `search` or `fetch` first and uses the nonce. Then B to A.
  5. **Collision**: ask A and B close together to make the same board item keyed by one
     stable key. Pass: one `doing` row; the other reports it taken.
  6. **The offer, once**: with Claude and ChatGPT connected and Grok not, say in Claude
     "Grok told me something different yesterday". Pass: Claude answers first, offers Grok
     once with `connect_url#grok`, `USER.md` gains "Offered to connect grok" and Grok on
     "Assistants I use"; no offer again in a new Claude or a ChatGPT conversation; a no
     (third account) records "Don't suggest connecting grok" and no agent offers it again;
     connecting Grok shows it connected.
  7. **Goals**: tell ChatGPT a nonce outcome in passing with one small task toward it; pass:
     a `status: draft` goal page with ChatGPT's client as its first writer and a board row
     naming it; confirm it in a fresh Claude.ai conversation; pass: Claude reads the goals
     index first and sets `status: stable`.
  8. **Layer ablation** (20 prompts each, direct, indirect and negative): connector only;
     with instructions; with the plugin. Record the check rate, the false-trigger rate, and
     the approval prompts each write-back costs on ChatGPT and Claude, `file_append`
     against `file_edit`; whether claude.ai shows the `instructions`.
  9. **By the founder's hand** (no driver here; name the app version and date): Grok Bot
     (the `say` sentence), Meta Muse, Claude Duet and Gemini, steps 2 to 6 each, after step 1
     recorded each one's redirects; before that, each one's connect is refused loudly at its
     registration, which is what step 1 reads.
  10. **Codex CLI and Claude Code locally**, two agents on one repository: claims keyed
     `repo:<name>:<area>`, one `doing` row, the other stops.
  - Review rvbr (REQUEST_CHANGES on `e4061205e`), fixed: an outside agent's or a run's
    `file_write` takes a string etag, `""` creating only where absent, and the object refuses
    null from them (H1); `follow()` shows a run's frame once across sockets, by its `seq`
    (M1), and waits between reconnects from 1 s doubling to 30 s, back to 1 s once a socket
    stayed open that long (L1); a CIMD client is verified only when its host and every
    redirect it registered are the app's (M2); a registration takes only a `dcr` vendor's
    own redirects, and one with none recorded takes none (M3); migration 4's columns are in
    the owner bound (L2); `/workspace/USER.md` joins `OWNER_TREES` and `file_append` refuses
    JSON, TOML and YAML (the founder on L3); `agents_list.apps` gives `last_used`, and the
    shared-brain skill asks before taking a long-unused app as connected (L4); the
    served-text guard covers titles, nested argument descriptions, skill descriptions,
    "always", "every time" and broad triggers (L5); `file_list` and `agents_list` wait for the
    seed (L6); an append rechecks the agent trees on each try and stops after
    `APPEND_TRIES` (65: one more than the owner's open uploads) lost tries, `file_busy` (L7).
  - Review rvbr round 2, fixed: a refused registration naming a `dcr` vendor logs one
    bounded `dcr_refused` line (its app, `client_name`, `redirect_uris`), which live step 1
    reads (R2-M1); a small file's append is read and written in one unit and never retries,
    and only a larger one's retry is capped, at `MAX_OPEN_INTENTS` (64) tries, the chunked
    writes that can be in flight at once (R2-L1); the old canary stays provisioned until the
    switch (R2-L2); `file_remove`, `file_rename` and `file_restore` take a string etag from
    clients and runs (R2-L3); `starter.ts`'s personal-file comment (R2-L4).
- [ ] Owed by BRAIN to later slices: the submissions themselves (`platform/plugins/SUBMIT.md`,
  the founder's outward act); Claude's plugin directory follows a repository, so the plugin
  may need its own repository or a published subfolder; a Gemini listing would need a
  profile without `converse`, rooms and mail (Google's agent-to-agent ban), outside
  Direction 7 today; Muse's own client completing our OAuth before a listing is unverified.

## 8. Founder live test

- [ ] The founder's real account, end to end. Fixes land in the new stack only.
  The canary follows.

## 9. Cutover window

- [ ] Full backup; each owner's broker stores imported into their object and each
  volume into object storage, each imported owner admitted (`admissionSet` from the
  platform caller, and its `o:` record), as sign-up admits one; DNS switched; the droplet paths deleted. S36 reads
  converged.
  Owed before slice L's deploy kit runs mode `routes`, each with this change since each moves
  production routing (slice L's final check, recorded, not yet fixed):
  - **R1, the old proxy's own deploy.** `deploy-worker.yml` deploys `tinyassets-mcp-proxy`
    on every push to `main` that touches `deploy/cloudflare-worker/**`, from
    `deploy/cloudflare-worker/wrangler.toml`, which still lists all five routes. After the
    handover its next routine deploy fails (wrangler refuses `mcp*` while the doors hold
    it), or, with `mcp*` removed to make it pass, re-adds the narrower
    `.well-known/oauth-*` routes and takes OAuth discovery back from the doors, breaking
    every connector's sign-in. So the cutover cuts that `wrangler.toml` to the one route the
    proxy keeps, `api*` (the doors take `app*`, below), with a guard holding it to the
    handover's end state (the handover test's `LIVE` table).
  - **The apply job runs the plan it was approved on.** `platform-deploy.yml`'s `apply`
    recomputes its plan rather than comparing it with the one the `plan` job showed for
    approval, so a live change between the two jobs makes it do something not shown. The
    plan passes as an artifact and `apply` stops on any difference.
  - **`/app` itself, found at L's merge with the app slice.** `deploy.ts`'s `ROUTES` give
    the doors `tinyassets.io/app/api/*` only, and the handover keeps `app*` on the proxy
    (`guards/deploy.test.ts` asserts `/app` stays the proxy's). The app slice serves `/app`
    and its pages from the doors, among them the return pages the doors name
    (`WEB_REDIRECT` `/app/signed-in`, `CONNECTED_PAGE` `/app/connected`), and expects the
    routes to give the doors `tinyassets.io/app*`. As built, after `routes` those pages
    still reach the daemon, so the owner app's sign-in and OAuth connects do not finish.
    Settle it in the handover before `routes`: the doors take `app*` in place (a `PUT`, as
    `mcp*`), the proxy keeps `api*` alone, and R1's cut and guard follow that end state.
    What else `app*` brings to the doors:
    - **Website paths.** A route's `*` matches any characters, not a path segment, so `app*`
      also matches the site's own paths that merely start with `app`, such as
      `/apple-touch-icon.png`, which the site really serves. The old proxy hands those back
      to the website origin (`worker.js` `belongsToWebsite`); the doors have no handback
      (`appPath` takes only `/app` and `/app/...`, anything else is the doors' `404`). The
      cutover needs one of two: the doors hand every other `app...` match back to the
      website origin, with a test; or the doors' routes narrow to `tinyassets.io/app` plus
      `tinyassets.io/app/*`, once the doors' return pages are proved to need no query (they
      carry the code in the fragment, which a route never sees).
    - **The old platform's query-bearing returns.** A route matches the whole URL, query
      included, so a narrowed `/app` takes only a bare `/app`. The old app's returns are
      `/app?code=...&state=...` (AuthKit's sign-in return, `tinyassets/onboarding/app.html`
      `redirectUri()`) and `/app?subscribed=1` (Stripe's checkout return). Under `app*` they
      land on the doors' `/app` page, which ignores the query; under the narrowed routes they
      match nothing and land on the website. A sign-in in flight at the switch starts again
      on the new app (its issuer moves anyway). A Stripe checkout in progress: the window
      opens by turning the old platform's billing off, so no new checkout starts (billing is
      off without its Stripe secrets, `tinyassets/billing/stripe_adapter.py`); it waits until
      every open Checkout Session has completed or expired and its webhook is applied; then
      the backup, and the import carries each paid owner's tier to its admission row. A
      buyer's late `/app?subscribed=1` return then lands on an app whose plan already shows
      the tier.
    - **The old Stripe webhook, `/app/billing/webhook`.** Stripe posts every subscription
      event there (renewal, failed payment, cancellation), and `app*` captures it. The doors
      have no billing door, so after the switch it is the doors' `404`, and a cancellation
      would never lower a tier. Before `routes`: a billing door on the new platform, with
      Stripe's endpoint moved to it; or the founder's word that no live subscription exists,
      and Stripe's endpoint disabled until that door is built.

- [ ] **The founder's and the canary's owners start fresh** (the founder's ruling, 2026-11-04):
  the founder's main account and every canary owner account begin on the new platform with
  nothing imported, so the founder goes through the whole onboarding from the first
  connection, and each fresh canary owner exercises it once, at its first run. In the
  cutover window:
  1. **Back up, never import.** Their old-platform data is exported and kept privately as a
     backup. It is proven saved before anything is dropped (Fact 13): the export's own
     listing and checksums are read back from where it is kept. It is **not** imported into
     their new owners, unlike every other owner's stores in the step above.
  2. **Admit empty.** Their new owner objects are admitted empty (`admissionSet` from the
     platform caller, and the `o:` record), so the starter seed and onboarding run at their
     first connection.
     **The old canary stays provisioned until the switch.** `deploy/deploy_fail_safe.sh`
     refuses a data-moving deploy while the core canary is not provisioned (`provisioned=1`
     from `scripts/verify_core_canary_provision.py`, which checks the pinned
     `TINYASSETS_CANARY_UNIVERSE_ID`), and the cutover's own moves are such deploys. So the
     old canary account and its variables are left as they are until the switch is done; the
     reworked scripts of step 3 and that gate change together, in one change after it.
  3. **A canary that starts from a fresh, seeded owner.** The canary's live checks run from
     an owner just seeded: onboarding first, then the usual checks. What the scripts
     `docs/host-actions.md` names, and `openspec/specs/core-capability-assurance/spec.md`,
     assume today, read 2026-11-04, nothing changed yet:
     - `scripts/core_canary_provision.py` and `scripts/verify_core_canary_provision.py`
       refuse an account whose home is not the repo variable `TINYASSETS_CANARY_UNIVERSE_ID`,
       then create or repair through the old tools (`read_graph target=model_options`,
       `target=branches`) a default model, a connection and a branch in that pinned home.
       A fresh owner has no such home id and none of those. They must: sign in, wait for
       the seed (`agents_list` shows `main`), check `/workspace/USER.md` has no
       `Onboarded:` date, run onboarding through an MCP client the way an outside agent does
       (answer it with fixed canary answers), then provision a model source on the new
       doors (`/app/api` connect), with no pinned home.
     - `scripts/owner_box_canary.py` requires both `TINYASSETS_CANARY_UNIVERSE_ID` and
       `TINYASSETS_CANARY_USER_ID` to match the token's `universe_id` and `principal_id`, then
       calls `converse` with `graph_id` set to that home. On a fresh owner both ids are new,
       and the new `converse` takes `agent_id` and `client_send_id`, not `graph_id`. The two
       variables become the new owner's id read after sign-in (the probe checks its own
       account through the binding, not a pinned universe), and the call becomes the new
       `converse`.
     - `scripts/canary_session.py`: one ordinary sign-in per chain
       (`TINYASSETS_CANARY_SESSION_SEED_DEPLOY`, `..._UPTIME`), whose seed names the old
       account's session. A fresh owner on the new issuer needs both chains signed in again,
       which is the founder's step in `docs/host-actions.md` ("Canary is an ordinary account"), repeated after the
       switch.
     - `scripts/mcp_public_canary.py --assert-handles` and `scripts/mcp_tool_canary.py`
       hold the old platform's handle set (`read_graph`, `write_graph`, `run_graph`,
       `read_page`, `write_page`, `converse`, `get_status`) and call `get_status` or
       `read_graph target=status`. The new door serves the tool table
       (`platform/doors/profiles.json`) and neither of those. Their handle set becomes the
       custom profile's, and their status call one the new table has (`agents_list`, whose
       `apps` and `owner_agents` also prove the seed ran).
     - `scripts/last_activity_canary.py` reads `daemon.last_activity_at` from `get_status`;
       the new platform has no daemon or `get_status`. Its proof becomes a run's receipt
       (`run_status` of the canary's scheduled run) on the fresh owner.
     - The spec's "Hosted live assurance" requirement names "a dedicated canary owner" and
       its "Canary not provisioned" scenario turns missing configuration into a warning. A
       fresh owner each cutover means "provisioned" is the state the provision script makes
       from empty, not pinned variables, so the spec's provisioning wording changes with
       these scripts.
     - The same scripts run every five minutes and after every deploy, so the onboarding run
       happens once per fresh owner, not per probe: it is marked by the `Onboarded:` date,
       and every later probe starts from the onboarded, seeded owner.
- [ ] **The public `/connect` page from `apps.json`** (slice BRAIN's website part, on branch
  `platform-brain-site`), after the routes move above, so the live site never shows steps the
  live `/mcp` does not serve: merge it to `main` (`deploy-site-react.yml` publishes it, its path
  filter then including `platform/doors/apps.json`), and its sweep checks one section per app
  `apps.json` names. It carries `WebSite/site-react/app/connect/page.tsx`,
  `WebSite/site-react/scripts/sweep.py`, the workflow's path filter and the
  `public-website-surface` spec's `/connect` line. Then `connect_url` reaches every app's
  section, Grok's, Muse's and Gemini's among them.

- [x] MEMORY PR 1, answers once, paged (`memory_research.md` §3, as `memshape_research.md`
  §5 re-shaped it): each tool result one text block of its JSON, no `structuredContent`;
  every read paged by its answer's encoded size, counted exactly, to `RESULT_CHARS` (50,000,
  Claude Code's inline default): mail, digests, chains, collection rows and history,
  conflicts, `file_read`, `fetch`, `file_list`, `file_history`, `search`, run views,
  `agents_list` (M3), `code` answers (output cut and said so, M6); a cut body says
  `body_bytes`, `body_next_offset` and `truncated`, read on by `body_offset` with the
  message's id (L2) or the row's etag (L3); a write answers its row without its body (H3);
  windows read by verified ranges (`FILES` rechecks the whole chunk and answers the range,
  L7); a cut title or fetch field says so, and a search's cursor is an opaque base64url token
  (L5). The byte guard of `1d284b1b1` is gone (memshape_research.md §5).
  Owed: after the deploy, PR 3b (bodies as
  streams across RPC), PR 4 (inbound, sync, the files Worker's pool, the AST scan,
  `file_append`'s `request_id`), production baselines, and the `ui-test` live check reading
  a long message through to its end.

- [x] MEMORY PR 2, memory bounded by structure (ADR-037): one pool of units an isolate
  (`slots.ts`), a path taking units for what it holds while its bytes are in hand, weighted by
  it (`holds.ts`), a reserve so a one-unit hold never waits behind larger ones (review M1),
  nor a larger one is starved by them (M-b), each class's waiters capped apart (L-a), the
  larger ones by the largest arguments a door takes (L-b), `busy` past the wait or the
  waiters (`503`, `Retry-After`); an edit's source chunks let go as their pieces are taken; `file_edit` chunk-wise, a step
  per chunk read and per chunk made and put, to `EDIT_BYTES_MAX` (500 chunks, from the
  worst edit's measured time); `file_append`, a body's windows and whole reads each a step;
  the relay's tails one budget an isolate (M5); `agents_list` paged by stable key; owner
  ranged reads verified, a tampered chunk failing loudly. The workerd memory leg
  (`test:memory`, its own CI leg) runs the real owner object, `Files` and `Bodies` at their
  worst (distinct content, 8 to 96 MiB), sampling at every chunk call, with a control; the
  counts test sums the live terms from the sources and names what it does not count
  (garbage, unmeasured in production; the holds owed by PR 3b and PR 4). The first leg's
  "retention" was a sampler artifact: a swept read must be atomic with its collection.
  Owed: the production probe (whether the 128 MB counts uncollected garbage), each
  Worker's `memoryUsageBytes` to reset the pool and baseline, an `exceededMemory` alert, an
  edit's wall time a chunk with R2.

- [x] MEMORY PR 3a, the egress door as a stream, re-planned as A′
  (`egressshape_research.md` §5, superseding memshape's scanner): the doors stream
  `request.body` unread, admit at `egressOpen` with no hints (the row open, uncharged) and pass
  `reserve`, a function the gateway calls back over RPC, which answers `egressReserve` and
  keeps a refusal's code and receipt for the door's answer. The owner charges an admitted
  model call once, for the model and tier the gateway read (the schema's triggers set its
  charge from none to held once, on an open row); an unreserved open row is open as any
  other (rate, beats, settled `lost` charged nothing). The gateway's metered path takes
  slots of its pool as its body arrives and by its weight (`GATEWAY_POOL_SLOTS` 16, PR 2's
  `slots.ts`; `busy` past the wait; see the review below), reads `Content-Length` decoded
  chunk-wise, parses once, refuses spelling
  aliases (`aliased`: `generation_config.max_output_tokens` and case-folded keys, the open
  metering bypass on Google calls), reads model and tier from that parse, awaits `reserve`,
  clamps in place, encodes once and uploads through a `FixedLengthStream` in 64 KiB writes,
  its slots let go at the upload's end.
  `hintsOf`, `HINT_KEYS` and `egress_reservation_mismatch`'s use are gone.
  Review rveg (REQUEST_CHANGES), fixed: a call is weighed by what its tree can cost, its bytes
  and its structural tokens counted as they stream in (`gateway/src/held.ts`: 4.25 a byte with
  a non-ASCII character, 2.25 without, 32 B a token, measured in workerd over 19 shapes, the
  worst empty objects at 11.67 and nesting at 15 times the body), one weighing more than the
  pool refused `egress_too_large` (H1), and the memory leg samples right after the parse, the
  stringify and the encode for ten shapes, failing on an under-count; its slots are taken as
  its bytes arrive, its weight's once the body is in, its read and its reserve stopped at its
  caller's stop and past `heldDeadlineMs` (the pool's wait plus the body at 0.18 MiB/s, one
  slot's worth a pool wait), and the pool's waiters are served round-robin by owner, sharing
  the waiters without a fixed cap (H2); a key past ASCII at a read level, and a body `model`
  where the path names it, refused (M1, M2); every end before the fetch settled `failed` (M3);
  a body shorter than its `Content-Length` refused, the slots let go on a throw before the
  fetch, a stopped call never reserved nor sent, and the deploy kit's one-after-another order
  recorded in `deploy/checklist.md` (lows). Measuring it showed a dead local of a suspended
  async function is still kept: the read now returns through `weighedBody`, so its text is
  held once.
  Verification rveg2 (REQUEST_CHANGES), fixed: a `\u` escape weighs as non-ASCII (Python's
  `json.dumps` writes them; the leg's eleventh shape, escaped text, passes); growth is
  wait-die in `slots.ts`'s one admission rule (`#fits`): only the oldest holder waits while it
  holds, a younger one gives everything back `busy` before its reserve, and while the oldest
  waits nothing else takes past the reserve; an owner holding half the pool or more takes no
  more while another owner waits; each unit's worth of body must arrive within the pool's
  wait of that unit's grant, its clock running only while the call waits on its sender, so
  16 silent calls let go at their units' deadlines and another owner lands; a stopped wait
  leaves the queue at once; the reserve waits at most the pool's wait (`busy`); a body is
  refused `egress_too_large` as soon as its weight passes the pool. A stop racing a reserve
  already in flight may still be charged whole, as before A′.
  Owed, the verification's Lows (recorded, no code): a slow sender can game "oldest", holding
  its first unit early so wait-die favours it (measured 16 % lower throughput at
  saturation); a user-defined endpoint that reads its upload slowly holds the call's units
  through the upload, bounded by `heldDeadlineMs`.
  The memory leg's egress cases (`owner/memory/egress.mjs`, run by `leg.mjs`: the real door
  and gateway in isolates of their own, sampled at the provider's checkpoints), measured in
  workerd: the door at 16 against 4 MiB held -0.38 MiB, the gateway -0.01 MiB (each at most a
  quarter chunk); the gateway uploading 4.25 MiB for 4 MiB and 16.08 MiB for 16 MiB (at most
  1.05·S + 0.25 MiB); 0.03 MiB a call more in the gateway at 8 against 4 calls held (at most
  0.25); a call behind a whole-pool upload lands, and is `busy` past the pool's wait; the
  control (a door reading the body whole) reads 11.99 MiB more. A caller's stop now ends the
  answer closed, not errored with its abort, so workerd's "Uncaught (in promise) AbortError"
  per stopped call (14 in the doors' egress suite, on `main` too) is gone.
  Owed: a sandbox's real upload rate, measured live, against `MIN_RATE`;
  `egress_aliased_key`, `egress_reserve_refused`, `egress_too_large` and `busy` stay in
  `forward.ts`'s own
  table (`EGRESS_CODES`) and join `failure.ts`'s, and `egress_reservation_mismatch` leaves
  it, at the next E2B template build (the runner bundles `failure.ts`, so a change to it is a
  new template); live, `memoryUsageBytes` for the gateway and the doors.

## 10. The plan's later slices

- [ ] Rooms and status (S4), app manifest and agent players (S6), device runner
  (S7), real-time tiers (S8), Claude and Grok wakes (S9), Sign in with ChatGPT
  (S10), rebuilt on the new architecture.
