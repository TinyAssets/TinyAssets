Build order is the plan of record's "Migration and build order" (ADR-032), one
section per slice. An item is ticked in the PR that lands it. A landed item whose
live acceptance is still owed says so.

## 1. Verify the unverified facts

- [ ] E2B p50/p95 resume for a 1 GiB sandbox, measured live; paused-storage price
  and retention, Pro-plan concurrency, regions. A p95 over 1.5 s switches the
  sandbox to Cloudflare Containers.
- [ ] Durable Object pricing and limits, and 2026 regional availability, from
  Cloudflare's own pages.
- [ ] Dynamic Workers and Facets availability and pricing.
- [ ] OpenAI MCP Events delivery and Sign in with ChatGPT plan-usage terms; any
  Grok or Meta Muse wake API.

## 2. Doors and identity

- [ ] Edge Workers as the only binder: the OAuth server (Workers OAuth Provider),
  the stateless MCP handler, `/api` keys, `/hooks/inbound`, and the owner
  directory (key and token hashes, published apps; no owner content).
- [ ] Owner admission, the Python `require_admitted` (an owner suspended, or a
  restored identity map not yet reconciled, admits no work): a deliberate
  difference from the ported primitive, which `OwnerDO` does not check. The
  binder refuses an unadmitted owner before it reaches the object, from the
  directory's admission state.

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
  and an owner-deleted agent's purged. Wake limits are owner policy (an owner-only
  setter until 3.5). Wakes and revokes sit on the object's one schedule; only its alarm
  claims and settles them, in process, so no RPC reaches a claim. A revoke carries the
  subscription generation it erases through, so a stale one never erases a re-enabled
  agent's newer callback.
- [ ] 3.5 Policy, grants and sessions. Owed by 3.4: the object checks the owner's live
  session row itself for an agent register, disable or delete and a wake-limits write
  (3.4 takes the binder's `interactive` flag), and a refused one leaves the Python's
  refusal receipt in the policy store's receipts; the wake-limits setter becomes part of
  owner policy.
- [ ] 3.6 The schedule: one table, one self-rearming alarm.

## 4. Gateway

- [ ] Sealed per-owner credentials, metering, the egress swap of placeholder
  credentials.

## 5. Agent

- [ ] pi-durable 1.4.x in the owner's object on its own mechanisms (hook names,
  transcript store, one `ExecutionEnv`, `replay: "unsafe"` by default,
  compaction, steering, subagents, `sleepUntil`, Agent Skills, deferred MCP
  tools through `tool_search` and code mode, `pi.agent` settings), with
  ADR-032's kept differences; packages.
- [ ] User hooks in a separately deployed Worker's Durable Object, called over
  RPC with JSON in and out and a timeout. A `beforeTool` timeout or error blocks
  the call; `api.memo` holds decisions that must survive a crash.

## 6. Files and sandbox

- [ ] Per-owner object storage with the file API; the E2B sandbox through our
  proxy; a sync agent with a single-writer lease.

## 7. Wakes and outside agents

- [ ] Alarms, doorbells for ChatGPT and Claude, the board with the W8 decisions.
- [ ] The doorbell: a `WakeSender` the object constructs and its alarm calls in process
  (ChatGPT MCP Events, then Claude `/fire`); it consumes the schedule's open wakes and
  revokes, and a send finishes inside the 60 s claim lease or its outcome is `unknown`.
  The subscribe door tags each callback with the generation `mailboxSubscriber`
  returns, and a revoke erases only callbacks at or before its generation.
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
