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

- [ ] Edge Workers as the only binder: the OAuth server (Workers OAuth Provider),
  the stateless MCP handler, `/api` keys, `/hooks/inbound`, and the owner
  directory (key and token hashes, published apps; no owner content).
- [ ] Owner admission, the Python `require_admitted` (an owner suspended, or a
  restored identity map not yet reconciled, admits no work): a deliberate
  difference from the ported primitive, which `OwnerDO` does not check. The
  binder refuses an unadmitted owner before it reaches the object, from the
  directory's admission state.
- [ ] Owed by 3.5. The owner's sign-in: the binder runs it (server PKCE,
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
  the consent screen, so only the owner's own consent brings a revoked client back. An
  agent's held API-key request applied with its grant (`_apply_key_request`) comes with
  the key store, whose key ids it needs.
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

- [ ] Sealed per-owner credentials, metering, the egress swap of placeholder
  credentials. Owed by 3.5: the gateway reads the owner's policy generation on every
  call, a policy write closes the owner's open egress tunnels before it commits (the
  Python's), and an egress destination's resolved addresses are checked here, at
  each call (the address check policy writes defer IP literals to).
- [ ] Connection flows (hosted model connect, connection OAuth, inline model connect)
  as owner-object state. Owed by 3.5: account revocation (`sessionCloseAll`) cancels
  every open flow with the sessions, as the Python's `revoke_owner` did.

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
  `_end_outside_streams`). PiHarness installs with `lifecycle.use` beside the schedule;
  since the object installs none of Lifecycle's runtime handlers, every door that reaches
  pi awaits `lifecycle.start()` first, so PiHarness's `onStart` (pi's open) has run.
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
  refused size or budget is `not_supported`; its reason is in the message, which `read`
  shows. Slice 6 (the review carries of slices 4 and 5): one ambient call budget per
  platform invocation (`calls.ts`), not per tool call, since a whole pi turn runs in one
  Lifecycle job of one alarm: the object holds a `CallScope` (an `AsyncLocalStorage` on
  the instance), the alarm and every RPC door (the invocation-scope guard) enter a fresh
  scope, so interleaved doors never share a count, and whatever makes a service-binding
  call reads the scope's budget (`Files`, `Schedule.onJob`, `runWakes` and `runWork`
  through what they call), never a threaded one; a draw outside any scope throws. The
  PiHarness wiring (5) builds its `ExecutionEnv` with no budget: its tool calls draw on the
  alarm's scope. `FILES` may bring the budget to 24 of the 32 service calls, the other 8
  named for the gateway (4) and hooks (5), which draw from the same budget when they land;
  every draw is refused, naming the budget, before a call would cross it. Whether the cap
  applies per Durable Object event is still the probe below, before the gateway slice. A
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
- [ ] Probe, before the agent (5) runs a turn on the object: the 32-service-binding-calls
  per request cap (`FILES`, the gateway, hooks) and the 15-minute alarm wall time,
  against one pi turn, including whether the cap applies per Durable Object event, before
  the gateway slice. It confirms or adjusts `INVOCATION_CALLS` and `GATEWAY_HOOKS_HEADROOM`
  (`calls.ts`), whose budget already models one platform invocation (the alarm, or a
  door).
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
  (ChatGPT MCP Events, then Claude `/fire`); it consumes the schedule's open wakes and
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
  (`require_work`), and both reach the alarm from the owner directory (2).

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
