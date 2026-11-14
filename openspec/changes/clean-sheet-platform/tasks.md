The clean-sheet platform's remaining work (ADR-032 to ADR-037). This file holds only what
isn't built. An item is ticked in the PR that lands it and then shrinks to one line, kept only
where something cites it: the code, the specs, the ADRs and the PRs hold how it was built. The
text before this cut, with every slice's as-built record and review rounds, is
`git show de098f01a:openspec/changes/clean-sheet-platform/tasks.md`.

Pointers: `d2 B<n>` is fix B<n> of the untangle's wave-2 log,
`docs/decisions/untangle-2026-11-wave-2.md`, and `d2 §X` a section of it; `d3 §X` is a section
of the untangle's log of the whole remaining build (2026-11-11),
`docs/decisions/untangle-2026-11-remaining-build.md`, and `list3 P<n>` a problem in that build's
`plan` review. An id with a colon, such as `d2:N56`, is a problem in a listing those logs cite.
§10 is the order of the work; §9 is the cutover window; the founder's open questions are at the
end.

## 1. Verify the unverified facts

- [x] E2B p50/p95 resume for a 1 GiB sandbox, measured live: p95 706 ms, so E2B stays
  (`e2b-resume-measurement.md`). What is still unmeasured is §10 step 2.

## 2. Doors and identity

- [x] 2.1 The doors Worker (`platform/doors`): the binder, our authorization server, the MCP
  door, `/api` and the owner surface.
- [x] Live AuthKit wiring: WorkOS User Management (`doors/src/oidc.ts`), set up by the deploy
  kit. The stage's first sign-in is its live proof (§10 step 8).
- [x] The owner's sign-in and session cookie, owed by 3.5 (the binder; `owner/src/sessions.ts`).
- [ ] DNS, routes and deploy: mode `routes` hands the doors `identity.json`'s `routes` (d3 §J1:
  the doors take `app*` and `api*` in place, as `mcp*`, and the old proxy is left with no
  route), then the handles canary (Fact 11). Every existing connector re-authorizes once, since
  the issuer moves from AuthKit to `https://tinyassets.io`. Built, not run
  (`platform/deploy/deploy.ts`, `platform/deploy/checklist.md`); it runs in the window (§9).
- [x] `/hooks/inbound`: capability URLs, and shared apps as data (`doors/shared-apps.json`;
  `doors/src/inbound.ts`, `owner/src/inbound.ts`).
- [x] Owner admission and erasure: the `admission` row, and `admissionSet` from the platform
  caller alone (`owner/src/admission.ts`).
- [x] The key store (`owner/src/keys.ts`, `keys/list`).
- [x] Owed by 6.1: the file door's bytes, streamed by the binder (`doors/src/file-bytes.ts`).
- [x] The owner app, served by the doors at `/app` (`doors/app/`, `doors/src/pages.ts`).

## 3. Owner object

- [x] 3.5 Policy, grants and sessions (`owner/src/policy.ts`, `grants.ts`, `sessions.ts`).
- [x] 3.6 The schedule: one table, run as jobs on the Agents SDK Lifecycle queue
  (`owner/src/schedule.ts`).
- [x] Append-only migrations from the first deploy: the locks (`owner/migrations.json`,
  `sandbox/migrations/`), their kit and command line (`ci/kit/locks.ts`) and the deploy kit's `lockRefusal`.
- The owner's action decision, which waited for the gateway's binding gate ADR-034 removed,
  is R5's (d3 §N), its slices in §10 step 4.

## 4. Gateway

- [x] G1 `platform/gateway`: the keyring, sealing, the OAuth exchanges and raw forwarding.
- [x] G2 The owner side: model connections and spend (`owner/src/connections.ts`, `usage.ts`).
- [x] G3 The connect doors (`doors/src/connect.ts`) and the source records (`platform/sources/`).
- [x] G4 The egress door, the sandbox's identity, its charge and its network document
  (`doors/src/egress.ts`, `owner/src/egress.ts`, `gateway/src/meter.ts`,
  `contract/src/api-rules.ts`). Its live probes are §10 step 8.

## 5. Agent

- [x] Erasure's order: mark, end every run, wipe. The erasure door closed the crash window
  with the directory's outside record (`doors/src/erase.ts`, `owner/src/erasure.ts`).
- [ ] H2 live, narrowed to code mode (`platform/deploy/h2-probes.ts`), first on the live stage
  (§10 step 8), since it decides code mode's on-by-default (list3 P14): a script past its
  interrupt budget against the Worker's `cpu_ms`; 10 s of one owner's script compute against
  another owner's; whether `exchange` calls count as subrequests of the owner object or of the
  code mode Worker; whether pi-codemode's own deadline fires in production while a script calls
  tools back to back; and, while a script loops tool calls, whether a revoke, an owner app read
  and the alarm reach the owner object promptly and the script's next call is refused (in
  workerd every other call starved, other owners' objects too). Mode `h2` is cutover-only
  (d3 §A5).
- [ ] 0c (live, slice L): the dark deploy. The first `dark` ran and stopped at the sandbox
  Worker; the migration lock holds its `first_deploy`. What is left is the dark redeploy of the
  untangled build (§10 step 7).

## 6. Files and sandbox

- [x] 6.1 The trusted file plane: the manifest in the owner's object and the files Worker
  (`owner/src/files.ts`, `platform/files`). Its create-once put on the live bucket is still
  unprobed: §10 step 7.

## 7. Wakes and outside agents

- [x] Wakes and doorbells (slice W): wake kinds as data (`contract/wake-kinds.json`), one
  doorbell (`owner/src/wakes.ts`, `gateway/src/doorbell.ts`), MCP Events at the MCP door
  (`doors/src/events.ts`). By choice, not built: a verification cache (each subscribe verifies
  again) and retries of a `5xx` (the wake settles failed; the next change rings).
- [x] Slice BRAIN, the doors half of the shared brain (`doors/src/instructions.ts`,
  `contract/apps.json`, `platform/plugins/`).
- [ ] **Owed by BRAIN, live**, on the stage (§10 step 8). Proof is observed tool calls and
  `writer` values, never an agent's say-so; the user only connects, signs in and answers.
  1. **DCR on** (`DCR_ENABLED: "true"`). For each `dcr` app (Grok via Cursor's backend, Gemini,
     Muse, Cursor) the founder's first connect is refused and logged once as `dcr_refused`, with
     its `client_name` and `redirect_uris`. Record those in `apps.json` with the date, and
     redeploy. Confirm Claude's and ChatGPT's CIMD hosts and redirects in their first grant rows
     (`vendor_verified` 1).
  2. **MCP-first onboarding** (`ui-test`, Claude.ai, a fresh account): "help me plan this
     week", then "what can you do?". Pass: `/workspace/USER.md` is read first; the task is
     done, then the onboarding questions come in one message; `USER.md` gains the answers and
     `Onboarded:` with Claude's client as `writer`; "ChatGPT" gets its `connect_url`.
  3. **The second vendor:** ChatGPT connected; a fresh conversation that needs the profile reads
     `USER.md`, runs no onboarding, and `agents_list.apps` shows both.
  4. **Write-back and check-first, both ways:** a nonce preference said in A is a new `USER.md`
     version with A as `writer`; B, asked without naming TinyAssets, calls `search` or `fetch`
     first and uses it. Then B to A.
  5. **Collision:** A and B make the same board item by one key: one `doing` row, and the other
     reports it taken.
  6. **The offer, once:** Claude offers an unconnected Grok once, with `connect_url#grok`, records
     it in `USER.md` and never offers again; a no records "Don't suggest connecting grok".
  7. **Goals:** a nonce outcome told to ChatGPT becomes a `status: draft` goal page and a board
     row naming it; a fresh Claude.ai conversation reads the goals index first and sets
     `status: stable`.
  8. **Layer ablation** (20 prompts each: direct, indirect, negative): connector only, with
     instructions, with the plugin. Record the check rate, false triggers, the approval prompts
     each write-back costs (`file_append` against `file_edit`), and whether claude.ai shows the
     `instructions`. This step sets the instructions' wording (d3 §A9).
  9. **By the founder's hand:** Grok Bot, Meta Muse, Claude Duet and Gemini, steps 2 to 6 each,
     once step 1 has recorded their redirects.
  10. **Codex CLI and Claude Code locally**, two agents on one repository: claims keyed
     `repo:<name>:<area>`, one `doing` row, and the other stops.

## 8. Founder live test

- [ ] The founder's own test, last on the live stage (§10 step 8), with a stage account: his
  main account never signs in to the stage and starts fresh at the cutover (d3 §H). Fixes land
  in the new stack only. It covers what only a real account shows:
  - a completed sign-in on each vendor, and how long a real sign-in with 2FA takes, which sets
    Claude's 600 s bound if that is too tight (`limits-audit.md`);
  - Gemini loading its `{system}` settings on a run past the sign-in; Kimi's skills, read once
    signed in;
  - onboarding writes `SOUL.md` and `USER.md`; "remember X" lands in knowledge and is recalled
    on another engine; a brief the agent made posts to `dm:main`; a main agent finds the
    knowledge with `search` and `fetch` (ChatGPT deep research, `ui-test`);
  - a wake-up pasted for a really registered main agent;
  - a sandbox-down drill.

## 9. Cutover window

The window is d3 §D steps 10 to 15, in this order. What the cutover deletes, edits, moves and
takes down is `platform/cutover.json`, the cutover's manifest in its three phases (d2 B24,
d3 §A10), held to the tree by `ci/guards/cutover.test.ts`.

- [ ] **The importer** (`import_research.md`; `platform/import/`, run on each run's disposable
  import host from the verified backup copy). It is deleted in the cutover's deletion change,
  with everything of the import window, as the manifest lists it.
  - [x] 1. The census (`census.ts`, #5461).
  - [x] 2. The bundle builder (`bundle.ts`, #5461).
  - [x] 3. The owner object's import doors (`owner/src/import.ts`, #5475).
  - [x] 4. The doors' import consumer (`doors/src/import.ts`, #5475).
  - [x] 5. `importCheck`, `importDiscard`, and the job's `verify`, `wipe`, `admit` and `dlq`
    (#5477).
  - [x] 6. The deploy kit's route record and mode `handback` (#5467).
  - [ ] 7. A dark rehearsal under `rehearsal-` names, measured, verified and wiped (the founder
    approved it on real data, 2026-11-07). Its runner is built: **Import window (manual)**
    (`import-window.yml`, slices 7a to 7e, #5479, #5486, #5487, #5488, #5490). `census` and
    `bundle` may run once the credentials are right; `stage`, `verify`, `wipe` and `admit` wait
    for the untangled build's dark redeploy (d2 §G3) and run on the stage (§10 step 8). It also
    owes:
    - the time a discarded object takes to be evicted, measured and written into the window's
      steps, since a re-import before then answers `busy`;
    - the window's credentials, made as a founder step (`platform/deploy/checklist.md`, "The
      import window's credentials"), and the Spaces read key, already asked (d3 §E7);
    - the dead-letter reader's limit noted: a message returns about 100 times before the pull
      consumer's `max_retries` drops it.
  - [x] 8. Onboarding tells imported owners what came across (#5463). The text is in the
    importer's README, out of the starter (d2 B20).
- [ ] **Step 10. Backup, census, off-site copy.**
  - The old platform's billing is turned off as the window opens (off without its Stripe
    secrets); every open Checkout Session completes or expires and its webhook is applied; then
    Stripe's endpoint (`/app/billing/webhook`) is disabled. No paid subscription exists (the
    founder; d2 B13), and a billing door is later work (§10 step 19).
  - The full backup, then the census on that copy. Any paid owner or Stripe id it counts is
    raised before the window, and any edge case (digest-only tombstones, live capability URLs,
    unacked mail, an owner past 30,000 entries) stops it loudly and goes to the founder (the
    founder, 2026-11-07). The window is not announced. The exclusion list and tester B are owed
    from the old stack's records and `docs/host-actions.md`.
  - Mode `offsite` copies the final backup to `tinyassets-final-backup` and reads each object
    back against its manifest (the founder, 2026-11-09).
- [ ] **Step 11. `bundle`, `stage`, `verify`, the deletion jobs compared, `admit`.**
  - The deletion jobs compared (d3 §C4, list3 P28): the old stack's unfinished deletion jobs
    (`tinyassets/broker/deletion_jobs.py` `load_unfinished_jobs`, read from the off-host
    authority, never a backup copy) against the census. An owner whose job was made after the
    copy is discarded (still `pending`) and withheld before any owner is admitted. No runner
    mode does this yet (§10 step 5).
  - `verify`, which holds every imported owner's check to its bundle, runs here, while
    `IMPORT_KEY` is still set (list3 P27).
  - `admit` admits each verified owner (`tierSet`, `admissionSet`, each suspension, the `o:`
    record). One failed owner blocks every admit; going on without it is the founder's call.
  - The founder's and the canary's owners start fresh (the founder, 2026-11-04): their old data
    is exported, kept privately and proven saved (Fact 13), never imported, and their new owners
    are admitted empty. The old canary stays provisioned until the switch, since
    `deploy/deploy_fail_safe.sh` refuses a data-moving deploy without it.
- [ ] **Step 12. Manifest phase 1:** `IMPORT_KEY` removed, since a replayed admit is accepted by
  design; and R1, `deploy-worker.yml` deleted, so no routine deploy of the old proxy takes routes
  back (d3 §J1: the proxy ends with no routes).
- [ ] **Step 13. `routes`, the handles canary, the platform's canary.** `identity.json`'s `host`
  is already `tinyassets.io` (d3 §H). Mode `routes` keeps its route record, then hands the
  routes over; the manifest disables `uptime-canary.yml` (d3 §J2), whose old canary scripts the
  platform's canary replaces rather than reworks (list3 P29). Verify: the handback's plan job
  (read-only); the control record opened for the probe owner (tester B) alone, through the
  operator path; its API key stored as `TA_PROBE_KEY`; `handles-canary.yml` with `side` `new`;
  the platform's canary. If verify fails, before the hold ends: `--mode handback --record <id>`,
  the old stack restarted on its untouched volumes, then `handles-canary.yml` with `side`
  `old`. Writes made on the new platform since `routes` are lost unless exported (decided at
  verify).
- [ ] **The public `/connect` page,** right after `routes`, in the site-at-the-cutover PR
  (d3 §C6 F2): branch `platform-brain-site` no longer builds, so the page is rebuilt from
  `contract/apps.json`, and its sweep checks one section per app.
- [ ] **Step 14. The gates opened through the operator path's `control` mode** (d3 §G): canary
  owners, then everyone.
- [ ] **Step 15. Manifest phase 2, the deletion** (Direction 5): two or three PRs in one window,
  each under GitHub's 3,000-file limit and proved by the manifest's dry run (d3 §F5). It
  deletes the old stack, the import window above, the old stack's workflows (`dr-drill.yml`
  among them), its docs, rules and specs, and `docs/architecture.md`'s S36 row then reads
  converged. `slow-tests` leaves the ruleset and the classic branch protection first (d3 §F4).
  - **Old tests leave with their code** (Direction 6): every test file
    `python scripts/affected_tests.py --retiring` lists (its `RETIRING` paths follow the
    manifest), and any other test that fails only because its code is gone. None is skipped,
    quarantined or rewritten to pass; a test that also reaches kept code keeps those cases. A
    structural guard (`scripts/ci_structural_guards.py` `GUARDS`) goes only once this line names
    its platform replacement or the decision that retires its principle. `tests.yml` loses
    `select`, `structural-guards`, `required-tests-shard`, `affected-tests`, `heavy-tests` and
    `slow-tests`; `required-tests` keeps `platform-tests`, `platform-sizes`, `platform-app` and
    `process`.

## 10. The remaining work, in order (d3 §D)

### Step 1. Now: B25, T0b, E2

The u3 PRs, C1 and P1 have landed (#5493 to #5499), with E1, E3, S0, S1 and T0.

- [x] B25, the code mode Worker's rename (d2 B25), with d3 §O2's fixes (#5506).
- [x] T0b, the kit's binding pass-through and its guard against host secrets (d3 §M6, #5510).
- [x] E2, re-applying a live lease's network (d3 §M2, #5509), with d3 §O1's fixes,
  which take in `SandboxStart.tokens.egress_token` (d3 §M3) and `owner/src/egress.ts`'s stale
  "5-minute" and "until the sandbox slice applies it again", with the specs' copies (d3 §I, §M).

### Step 2. Research and measurements

The research for B23, B4, the operator channel, the stage, R5 and B13 is decided: d3 §K, §G, §H,
§N and §J.

- [ ] E2B resume after days paused: a paused throwaway started now (75 minutes measured,
  `sandbox-probe.md`), then 14 and 30 days, which sets how often an owner signs in again, and a
  resume with 5–10 GB of scratch (d3 §C8, list3 P15). The probe is `e2b-pause-probe.yml`
  (`start` once, `check` at 14 and 30 days, then `cleanup`); ticked at the 30-day result. It
  runs alongside and does not gate the cutover: the cutover is done when the build is done
  (the founder, 2026-11-12).
  - Started 2026-11-12 20:55 UTC (run 43305055943): `small` `ixht089rvnrcl1yty661z`, paused in
    194 ms; `scratch` `ib3ngrmhs5jv82f0mzj9j`, 5 GiB written in 24 s, paused in 589 ms.
  - Due: on or after 2026-11-26 20:55 UTC, `check` with `ids: ib3ngrmhs5jv82f0mzj9j` only, so
    `small` stays paused unbroken; on or after 2026-12-12 20:55 UTC, `check` with no ids, then
    `cleanup`, and the result into `sandbox-probe.md`.
- [ ] Durable Object regional availability, from Cloudflare's status history (d3 §C8; ADR-032).
- [ ] The vendors' wake pages re-read, since `main-agents.md` could not read them: MCP Events
  plan tiers, any Grok or Meta Muse wake API; `wake-kinds.json` and `apps.json` corrected where
  they differ (d3 §C8, list3 P16).

### Step 3. This re-cut, P3, the manifest's inventory

P3 and P5 landed in #5504: no ritual body lines, no 8-file cap, the tamper check reads the
platform's tests, and `platform/deploy/` is release-critical (d3 §B2–§B4, §B7).

- [x] `.agents/activity.log` deleted (#5505).
- [ ] The manifest's inventory: every path in the repo that goes, is edited or moves, and every
  live object (d3 §A10).

### Step 4. The lanes, lane T, lane R, R5, the comments and tests passes, the re-check

- [ ] The wave-2 lanes as d2 §D orders them (C3–C5, S3–S8, O1–O5, D1–D4, V2–V4, X1–X5, I1–I2),
  with d3 §A's changes:
  - B3 after T1, on the cleaned constructors (d3 §A2, §K1).
  - B4: the door table generates the prototype methods at module load (d3 §K4). Its generator
    reads the table one way: `contract/requests.ts`'s own `tableRows()` beside `doorTable()`
    goes (lane O; C2).
  - B6: the queue names generated from the Worker configs into the contract (d3 §A3).
  - B6 (V2): `files/src/index.ts` `getChunks` charges a verified range its length but fetches
    and opens its whole chunk, so a batch of ranges of many chunks holds more than
    `BATCH_BYTES` (older than R1: under SSE-C a verified range was read whole too). With
    `verify` required, the budget bounds what a batch holds, not only what it returns (R1's
    review).
  - B8: the compatibility date is a kit check that the six configs agree (d3 §A4).
  - B11: a Worker's existence from `Cloudflare.workers`, never from its secret listing: the
    `workers` and `doors deployed` steps, which read a secret listing for it
    (`deploy/deploy.ts:1231-1236`, `:1394-1396`) (d3 §O2).
  - B12 reshapes only the modes that stay. `routes`' handover, `handback` and `h2` go on the
    manifest's end-of-hold list, and mode `probe` is deleted (d3 §A5, §H). The `apply` job runs
    the plan it was approved on: the plan passes as an artifact, and `apply` stops on any
    difference (list3 P26).
  - B13: the routes of d3 §J1; `/apple-touch-icon.png` moved off the `app` prefix, with a site
    test that fails on any site file under a doors pattern; one shared matcher of path plus
    query for the identity guard.
  - B14 builds only the platform's side of the canary (`deploy/canary.ts`); the old side stays
    in Python until the hold ends (d3 §A7).
  - `sources/chatgpt.json` and the `unwired` field deleted in their own PR, before B17 (d3 §A8,
    §L4).
  - B22: a test of the gateway's deadline before a provider's headers, on real or fake time;
    `resealDoorbell`, so an idle doorbell's blob leaves its kid, and the sweep's doorbell half
    (d3 §I).
  - B24: the whole cutover's manifest, in three phases, with its dry-run job (d3 §A10). Its
    platform Workers are entries in the deploy kit's `retired` list (`deploy/platform.json`),
    the one remover of a platform Worker (d3 §O2). Its vocabulary rule replaces the doors'
    check that the agent's definition says nothing of the import
    (`doors/guards/tools.test.ts`, B20).
  - B26: the specs and docs follow the code, and a check that every path and identifier the
    specs name exists (d3 §C7).
  - Lane O: the owner setting, off by default, that stops agent-to-agent mail past a chain
    depth (Fact 15; the coordinator, 2026-11-12, below).
  - B2: `owner/src/wakes.ts`'s swallowed doorbell open failure surfaces, rather than settling
    the wake `unknown` (Fact 8; d3 §I).
  - A later S PR: S13's `lease_id` half (d3 §L5).
  - Lane S: `sandbox/runner/sync-ignore.test.ts` tagged `large`, and the budgets re-derived with
    `node ci/budgets.mjs` (d3 §I): the large from the Vitest reports the `platform-sizes` legs
    upload, the runner's medium from a local `test:root` report (`platform-tests` uploads none).
  - Lane G's first PR: `MISTRAL` derived from `COMPLETIONS` in `contract/src/api-rules.ts`, with
    G-H11 and G-SP5 (d3 §O5).
  - Lane R's cipher PR: `files/src/cipher.ts`'s SSE-C premise (d3 §O8).
- [ ] Lane T: T1 on the owner before B3, T2 on the doors after D3, T3 the egress memory leg
  (d3 §K1, §K2). T1 is eight PRs, each green on its own:
  - [x] T1a: the sandbox a Worker bound by config behind a `ci/gate.ts` gate, `SANDBOX`
    required, `SandboxLeases` deleted whole (d3 §O4), and T0's two gate nits (d3 §M6).
  - [x] T1b: the ceilings of `Policy` (and `validate`'s), `Usage`, `Sessions`, `Egress` and
    `Connections` read where they are defined, their tests at production's numbers;
    `Schedule`'s `passMs` gone and `halted` required; `Files`' `reclaim`, `changed` and
    `manifested` required; `Connections`' unbound `GATEWAY` case gone.
  - [x] T1b2: `Grants`, `Registrations` and `Collections` read their own ceilings, their tests
    at production's numbers (the collections' fills `large`); `Collections`' `adopt` and
    `shares` required and `DEFAULT_SHARES` gone; code mode's `timeoutMs` gone, its wall time
    tested on the plain owner under a stepping clock.
  - [x] T1b3: the memory probe's archive cases at production's ledger limits, `LegSegments`
    deleted (d3 §K1); code mode's `CODEMODE_VM` required, its unbound case gone with its test;
    a test that a halted schedule runs no lane already due (`schedule.ts`'s `onJob`); the
    memory leg's floor never read high by earlier work's garbage (the pool's observer made
    outside `run`, the floor read again at the run's start).
  - [ ] T1b4: the ledger's decisions as pure functions tested with small numbers (`sealable`,
    `archiveDue`, `hotFull`, `postRoom`, `moveTake`); `admitted`, `changes`, `adopt` and `wall`
    required; `ledger-bounds` and `agent-mail`'s identity bounds at production's ceilings; the
    memory probe's measured move production's largest segment (d3 §K1).
  - [ ] T1b5: the archive's object tests at production ceilings (`archive-mail.test.ts`), then
    the ledger's `limits` parameter, `Internals.ledgerLimits`, `BigArchivingOwnerDO` and the
    other three classes' ceilings deleted, `LEDGER_LIMITS` checked once at module load, and a
    test of the purge queue's ceiling (`ledger.ts` `remove`, `maxAgentRows` purges waiting),
    which none holds today (`b23_research.md` question 3), and one that a lone straggler makes
    the lane due (no test fails today if `Ledger.archiveDue` asks `archiveDue` no straggler).
  - [ ] T1c: the real entry: `main: src/index.ts`, `Internals` and the test classes deleted,
    the unbound-`ERASURES` throw gone (`owner-do.ts:767-768`; the generated `OwnerEnv` already
    types it required, C2b) with its queue's fake consumer, the `FILES` and `GATEWAY` gates, the
    erasure and control record under a fake `Date`, the one-file starter by a Vite alias, the
    rig admitting by the door, the doors' entry with no third constructor argument, and
    `BackupParts`' test-only `sizes?` and `pool?` gone; the owner suite's lease clock under the
    fake `Date`, not moved by SQL (`egress.test.ts`'s `lease_expires = 1`) (list 6, item 6j);
    the object's `admitted` held by a test (no test fails today if the object passes the ledger
    `() => true`: a suspension between a wakes pass's claims, the `GATEWAY` gate holding its
    send); `ledger-support.ts` `ledgerParts`, a copy of the object's wiring, deleted.
  - [ ] T1d: the owner memory probe driven through the doors, with `LegArchive#ledger`, its
    copy of the object's wiring, deleted.
  - T3, with the egress memory leg's move: `GATEWAY_KEYRING` required
    (`gateway/src/keyring.ts`), as T0b left it (list 6, item 6i).
- [ ] Lane R, the operator path and recovery: its nine PRs as d3 §G orders them, before real
  data arrives (d3 §F3). Its `sweep` carries the key rotations: a new kid active, then
  `dataKeyRewrap` (or `gatewayReseal`) for every owner until each has answered (`left` 0), and
  only then the old kid removed from `FILES_KEYRING` (or `GATEWAY_KEYRING`).
- [ ] R5, where an action is decided (d3 §N): its six slices (`r5_research.md` §6), after B25
  and alongside lane O.
  - [ ] P-shape: the policy's `actions` table and `contract/acts.json`, the doors' rows naming
    their act, a rule nothing could see refused at write; the old rule tables, `spend` and
    egress `ask` deleted; the default document and its digest anew, with owner migration 6
    (its pin PR owed after it merges).
  - [ ] P-decide: the decide function and its receipts bucket; `action_request` cards and the
    once-approvals (bounded, generation-bound, swept); the owner's approve and refuse doors;
    the decide step in the runner, from the rows' `act`; a rule's `agent`, with the agent id's
    rule moved to the contract.
  - [ ] P-resume: an approval starts the conversation's resume run; Claude's `tool_deferred`
    stop matcher in its record's `events`; a run settles `waiting` on a card.
  - [ ] P-hooks: the hook helper and the run-scoped decide door; the `decide` block in
    `sources/src/schema.ts` and the four records; the runner writes the root-owned files only
    when needed, and runs the start check; the honest note (d3 §N5): `policyRead` gives each
    rule where it holds and where it can't (`holds_at`, `not_enforced`), from `acts.json` and
    the agent's engine record.
  - [ ] P-import: the census of non-default `entries`, `spend` and egress `ask` (read-only);
    the mapping, the old defaults written explicitly (d3 §N7), and the archive file; the
    notice. Until it lands, an old document is archived whole (`policy.refused.json`).
  - [ ] Later, only if an owner's rule needs it: hold-on-sync for `paths`, and asking for a
    host through web-fetch hooks.
- [ ] Process (d3 §B): P2 in order: reviews posted as the App's `review` check reads them and
  seen green on a real PR, then `review` required, then the receipt half deleted (d3 §F4);
  P6–P9 after the lanes.
- [ ] The comments pass and then the tests pass, one PR an area (d2 §E steps 4 and 5). The
  tests pass takes the two older tests that make a call refuse by patching a class's prototype
  (`owner/test/runs-authority.test.ts:857` `Files.prototype.open`,
  `owner/test/erasure.test.ts:83` `Lifecycle.prototype.disableAlarms`; list 6, item 9). The
  two patched calls are in-process (the owner's own SQLite; the agents library's
  `Lifecycle`), but each test's fault has a front door through `FILES`, so both move to a
  gate in front of it in the owner suite (`methodsOf` `ChunkStore`, as the ci suite's
  `files-gate`; B23): an `agent.json` over `INLINE_BYTES` read while its `getChunks` is held
  and the file is rewritten through the doors; the erasure's `deleteNamespace` refused once,
  for an owner with files, so it stops at step 2 before the wipe.
- [ ] d2's problem index, with decisions for the swallowed errors (`d2:N56`), the retries
  (`d2:N57`) and `s2:N51` (d3 §A11).
- [ ] The re-check loop (d2 §E step 6): every `owed` list empty, the layer guard with no
  exceptions, nothing worth fixing left, behaviour unchanged.

### Step 5. What the cutover needs that the lanes don't hold (d3 §C)

- [ ] The operator path's minimum: lane R's channel, `control` and `owner` (d3 §C2).
- [ ] The manifest's dry run green (d3 §A10).
- [ ] The platform's canary and one alert path, as d3 §J2 and §J3 design them (R6). Its alerts
  include the ones that are only log lines today (list3 P18): `sandbox_waiting`,
  `sandbox_renewal_refused`, `sandbox_runner_mismatch`, `exceededMemory`, and E2B's team metrics
  (`/teams/{id}/metrics/max`) against the concurrency ceiling. E2B's credit and spend have no
  API.
- [ ] The deletion-jobs step, a runner mode before `admit` (§9 step 11; d3 §C4).
- [ ] The site at the cutover, one PR (d3 §C6 F2): the site reads `identity.json`,
  `contract/apps.json` and `profiles.json`; the `/connect` page rebuilt (§9); the privacy and
  account-deletion pages corrected, in the founder's copy.
- [ ] The mark's source, `tinyassets/desktop/icon_gen.py`, moved out before the old stack is
  deleted, or the site stops deploying (d3 §C6 F4).
- [ ] The live stage's four PRs, after B13, B14's canary and `control` (d3 §H).
- [ ] The two app stores' dashboards checked for anything we must follow up (a review in
  flight, an auto-release, a testing-track deadline, a policy form); the founder's sign-in
  (d3 §F2).
- [ ] The data shapes already known to be wrong, fixed before real data, as decided in d3 §P
  (list3 P48), each in its own lane PR:
  - receipts onto the recipient row, deleting `ledger_receipts`, after T1b4;
  - rooms and room members with opaque ids, in lane O after T1c and before S4;
  - collection history (§P item 3): H1 merged (#5538); S1, the stream extraction into
    `owner/src/stream.ts`, after T1b4, receipts and rooms; H2, the erasures stream; H3, the
    history stream, bounding the deleter's name or taking the factor into the guard's term;
  - conversation ids carrying the order key, idle conversations to segments, after T1b4 and
    on S1's stream module (§P item 3);
  - files: F1 (retention off by default, one recorded byte ceiling, `#fit`'s silent prune
    deleted, the latency probe's workflow, scripts and branch deleted) after R5's P-shape; F2
    (file versions to segments, with mark-and-sweep) after R1 and §P item 3; F3 (the live
    index's R2 level) later, and whether it comes before the cutover is an open founder
    question;
  - `RUNS_KEPT`, the policy and egress receipts, and the settled wakes' week and 1,000
    recorded in `limits-audit.md` as deliberate retentions, each with its reason (§P item 6).

### Step 6. The engines' locks, the one paid template build, the pin PR

- [ ] The engines' locks refreshed, then the one paid template build after the re-check loop,
  then the pin PR (d3 §A12, list3 P19). The runner and probes bundles moved in u3-services and
  again in S2 (#5517), so the next deploy needs a pair check of the merged pair and the pin PR
  (d3 §I).

### Step 7. The dark redeploy of the untangled build

- [x] The migration locks pin E2's owner 5 and SandboxControl 2, and S2's SandboxControl 3
  (#5519), first: the kit's `locks` step refuses every mode until they do (`deploy/checklist.md`
  § The migration locks; #5511).
- [ ] `dark` with the untangled build, the sandbox Worker included (the first `dark` stopped
  there), its ring queue and control namespace. Checked live at it:
  - the deploy workflow's `pair` job, live;
  - E1's: a lease opened after the redeploy has its network marked applied, and its sandbox's
    model calls are admitted (d3 §K5, §M1); E3's: that lease's file holds no egress token
    (d3 §M3);
  - E2's (d3 §M2, §O1): with a run live on an API key, an allowlist egress policy written; the
    run's next model call through `egress.tinyassets.io` is answered, not refused 409; the
    sandbox's network, read from E2B, carries generation 2 and the same `x-ta-sandbox`; a host
    the policy dropped is unreachable from the sandbox;
  - the files Worker's create-once put of sealed bytes on the live bucket (`onlyIf:
    { etagDoesNotMatch: "*" }` with the sealed bytes' own `sha256`; `files/test/chunks.test.ts`),
    and the files bucket listed empty first: no object stored before the one cipher opens under
    it (d3 §G);
  - R5's (d3 §N9): Claude's `defer` with several tool calls in one turn; managed settings
    against an organization's server-managed settings; Codex, Gemini and pi loading our
    root-owned hooks unattended; the delay one decision adds; a census of non-default rules,
    spend caps and egress `ask` in the import.

### Step 8. The live stage (d3 §F1, §H), in order

- [ ] 1. H2 (§5).
- [ ] 2. The rehearsal's platform half (§9 item 7), which also proves the `aes-gcm` files under
  each owner's key: every chunk put by `stage` and read back by `verify`.
- [ ] 3. Slice S verifies each engine record live, on its pinned version in a headless E2B VM,
  the record fixed where it differs. The pinned installs, startup, users and the not-signed-in run
  passed (`sandbox-probe.md`); what is left needs a model or a sign-in:
  - every CLI: its event shapes against the matchers on a run and a resume; a resume after a
    cold create restores `session_paths`; MCP injection reaches the run's door with no bearer
    in the config; the tool filters and unattended flags run with no prompt; `no_update` holds;
    every credential file it writes is in `secret_paths` (Kimi's MCP OAuth stores not yet
    located); a signed-in run starts with every `forbid_env` name unset; on a key, its own
    model-call body and headers pass `api-rules.ts` through the egress door and any other call
    is cited free or refused; no synced file or transcript line holds a `secret_paths` file's
    bytes; project files that set a forbidden variable refuse the run; `HOME={home}` holds all
    a CLI or its SDK writes;
  - Claude Code: `--permission-mode auto` with `--permission-prompts none`; `--tools` and
    `--disallowedTools`; `CLAUDE_CODE_PROJECT_DIR_NAME`; `ENABLE_CLAUDEAI_MCP_SERVERS=false`;
    `auth login` headless with a pasted code; a key run on `ANTHROPIC_BASE_URL`, and whether it
    calls `/v1/messages/count_tokens`;
  - Codex: `exec --json … resume {sid} -`'s order; `forced_login_method="api"`;
    `approval_policy` and `sandbox_mode` inside E2B; `check_for_update_on_startup`;
    `-c mcp_servers.tinyassets.url` over streamable HTTP; `features.<tool>=false`;
    `login --device-auth` headless;
  - Gemini CLI: `GEMINI_CLI_SYSTEM_SETTINGS_PATH` and its keys; `selectedType` per credential;
    `--approval-mode yolo`; the `stream-json` fields; `--resume {sid}`; the `NO_BROWSER`
    sign-in; the prompt on stdin within 500 ms; whether the `gemini-api-key` type still sends
    to `GOOGLE_GEMINI_BASE_URL`; the node-pty and keytar install scripts;
  - Kimi Code: `--continue` in a per-conversation cwd; its `stream-json` fields and matchers
    for its errors and usage; the user-level `mcp.json`; where `session_index.jsonl` and the
    region marker live; whether `config.toml` holds sign-in state; `login --region` in both
    regions; `--prompt={prompt}`;
  - pi: `--mode json` event names, and a `final_text` matcher of the last assistant message
    only; `--session {sid}`; `PI_CODING_AGENT_DIR`; `models.json` `baseUrl`; the pi-ai key
    variables; `.pi/skills` under `--approve`; `--tools`; the user-level `mcp.json` over
    streamable HTTP; the prompt on stdin;
  - Gemini CLI's and pi's idle cuts, researched (the 300 s cut follows Claude Code's and
    Codex's; d3 §I); each CLI's real concurrency against a run's cost cap, and whether
    frontier-model calls serialize under the default caps.
- [ ] 4. The egress door's live probes (G4): E2B filters CIDRs, so 127.0.0.1, 10.0.0.1 and
  169.254.169.254 are unreachable; whether code in the sandbox can read its own transform
  header; whether E2B's proxy honours the VM's `/etc/hosts` or DNS for a transformed host, and
  SNI-fronting under the allowlist; that the project's API key can read transforms; that the
  sandbox resolves only through the pinned resolver; whether the VM has IPv6; whether Gemini's
  `thinkingBudget` counts inside `maxOutputTokens`; that a request in flight across
  `updateNetwork` carries the old generation; each CLI's base-URL variables against the CLI;
  E2B's transform on `/r/<run>/<secret>/mcp` and `/r/<run>/events`; whether a WebSocket would
  carry the frames.
- [ ] 5. The connector tests (`ui-test`): Claude and ChatGPT each add TinyAssets as a custom
  connector, see exactly the custom profile with `code` among it, and run one script of two
  tool calls; a client the owner names `directory` lists no `code`; each calls `inbox_digest`
  and `board_digest` and acts on the answer (an ack, a claim); a revoke refuses the client's
  next listing; MCP clients' tool-call timeouts against the 25 s wait; a long message read
  through to its end.
- [ ] 6. BRAIN steps 1 to 8 (§7).
- [ ] 7. The wakes: each vendor end to end (a ChatGPT dot and Work chat subscribing, a Claude
  `/fire`, a Grok webhook routine, Muse's catch-up); the SSRF probe (a callback whose DNS
  answers 127.0.0.1, 10.x or 169.254.169.254 is refused by `global_fetch_strictly_public`);
  Grok's webhook host, pinned as data once seen; whether Claude's over-limit fire answers
  `429`. E2B's lifecycle webhook: whether a kill of a paused sandbox carries the pause's
  execution and `sandbox_execution_id`, and E2B's retry timing against `BILLED_WINDOW_S`.
- [ ] 8. The founder's own test (§8).

Measured on the stage as it runs:

- [ ] The stage's first sign-in shows the access token's exact `iss` and `auth_time`, in place
  of mode `probe` (d3 §H).
- [ ] Live numbers: R2 read latency from an owner object; the longest put in flight against
  `PUT_GRACE_MS`; the owner's sustained write rate against the change log's ship rate and
  `LOG_BYTES`; `limits.subrequests` at 10,000,000; the usage receipts' rows per call; whether the
  isolate's 128 MB counts uncollected garbage (a 16 MiB request body), and each Worker's
  `memoryUsageBytes`, the gateway's and the doors' too, to reset the pool and baseline; an
  edit's wall time a chunk with R2, and the step at `SLOT_WAIT_MS`; a sandbox's real upload
  rate against `MIN_RATE`.
- [ ] The stage ends with the product's own erase door, then `handback`: both first live
  proofs. The stage is deleted, not kept (d3 §H).

### Step 9. Fix what the stage found

- [ ] Fixes in the new stack only, then the untangle's re-check once more (the founder,
  2026-11-10).
- [ ] MEMORY PR 3b (bodies as streams across RPC) and PR 4 (inbound, sync, the files Worker's
  pool, the AST scan, `file_append`'s `request_id`), on the production baselines (ADR-037).

### Steps 10 to 15. The window

§9.

### Step 16. The 14-day hold, then manifest phase 3

- [ ] On the founder's word: the droplet, the final backup and `tinyassets-final-backup` with
  its `deploy/platform.json` entry; mode `handback` and the route record; the handles canary's
  old side; the old proxy Worker `tinyassets-mcp-proxy`, its tunnel, the `mcp.` record and its
  Access token (list3 P23, P24).

### Step 17. Restore and the off-account copy

Moved before the window, into lane R (d3 §F3, §G).

### Step 18. Before the first contract change, and the first vendor-forced engine bump

- [ ] The deploy path's compatibility rule: Workers deploy one after another, so a contract
  change between two needs an order or a window (`platform/deploy/checklist.md`; list3 P44).
- [ ] A wrangler version bump reruns T0b's main-against-head comparison of the test Workers'
  bindings: `ci/workers.ts` `configBindings` restates wrangler 4.154.0's conversion (list 5
  item 22).
- [ ] The engine build cadence: one template a week, canaried, for vendor-forced minimum
  versions (`engines-replan.md`), with a standing spend approval (list3 P19).

### Step 19. Shells, billing, connectors, the comparison, the directories

- [ ] The store apps (paused until the new apps are ready, d3 §F2): one shell contract in
  `identity.json` (d3 §C6 F1); the mobile sign-in and connects through the platform's auth
  session (RFC 8252), as the app slice designed them on 2026-11-03 (the pre-cut text); the doors
  serving
  `/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json` (list3 P34); the
  desktop shell's twin (list3 P33). The stores also require: Sign in with Apple wherever Google
  or GitHub sign-in is offered (4.8); account deletion inside the app, with `erasureAnswer`
  wired at the erase door first (5.1.1(v), d3 §C6); the shell's native features (4.2).
- [ ] Billing: a billing door, Stripe's endpoint on it, and `storage_full`'s upgrade (list3 P38).
  Until then a tier changes through the operator path.
- [ ] Connectors for the owner's own agents: the agent's `mcp.json` merged into the run config,
  then a `browser` skill; where a connector's credential lives is undecided (list3 P40).
- [ ] Direction 1's standing comparison against the four main agents, and what "beat" is
  measured by (list3 P41).
- [ ] The directory submissions (`platform/plugins/SUBMIT.md`, the founder's outward act, after
  the live proof). Claude's plugin directory follows a repository, so the plugin may need its
  own; a Gemini listing needs a profile without `converse`, rooms and mail, outside Direction 7
  today; whether Muse's own client completes our OAuth is unverified; the listing's own door is
  the founder's question 4.
- Each adds, and none guards data or routing:
  - [ ] The shared app's install flow (Slack's OAuth v2 install, which proves the owner holds the
    workspace), writing the object's `shared` binding and its `i:` entry. Until then no event
    reaches the deployed shared-app half (list3 P45).
  - [ ] Server tools priced as data (Codex's web search, Anthropic's `mcp_servers`, OpenAI's
    `web_search_options`), so a key run that enables one is metered, not refused
    `egress_unmetered_tool` (list3 P20).
  - [ ] Gemini's `countTokens`: Google's page read (ai.google.dev) and listed `free` in
    `api-rules.ts` if it says so.
  - [ ] E2B's lifecycle webhooks as a second doorbell for the sandbox line, and one platform-wide
    pull of `/events/sandboxes` for the deliveries E2B gave up on (list3 P7).
  - [ ] The MCP Tasks extension once the SDK's `createMcpHandler` serves it: a task id the
    operation id, `tasks/get` `agentStatus`, `tasks/cancel` `agentCancel`.
  - [ ] A streamed file download, and an outside client's file-bytes tool.
  - [ ] The owner app: the API keys screen reads `keys/list` and deletes by `keys/delete`; the
    Export section uses the export door, and `MISSING.export` goes; the storage view's "deleted
    content" line from `storageUsage`'s `deleted_content`, and that a lower figure shortens
    recovery; "last used" on a source card.
  - [ ] An MCP tool for the past runs.
  - [ ] The region a Kimi run's provider hosts need, kept from the sign-in for its runs.
  - [ ] The runner reads a client's grant for its runs' agent definitions, so a full grant's run
    edits them with its CLI's own file tools, not only through the MCP door.
  - [ ] Codex's run secret as a header (`env_http_headers`).
  - [ ] ChatGPT reads only a search's first page (deep research doesn't follow `next_cursor`):
    scanning `/workspace/knowledge` first is one answer.
  - [ ] The knowledge lint script.
  - [ ] The files' history counted in bytes, as the collections' is, which lifts `max_entries`
    past 30,000 (`limits-audit.md`).
  - [ ] Sync-in of the working set alone (today the whole workspace), with the scratch design.
  - [ ] The gateway pool's two recorded Lows (MEMORY PR 3a's verification): a slow sender can
    hold its first unit early so wait-die favours it as the oldest (16 % lower throughput at
    saturation, measured); a user-defined endpoint that reads its upload slowly holds the
    call's units through it, bounded by `heldDeadlineMs`.

### Step 20. The later slices, re-listed, rooms and status first

Each needs its research first (Fact 14; list3 P49). The data shapes among them are fixed before
real data, in step 5 (d3 §P).

- [ ] S4, rooms and status: group rooms of three or more (the owner, main agents and TinyAssets
  agents, one post waking each member it addresses), on step 5's rooms table; `inbox_digest`
  over the owner's and outside agents' mailboxes.
- [ ] S6, the app manifest and agent players, with the app's view of mail-started runs and
  agents' chains.
- [ ] S7, the device runner; S8, real-time tiers.
- [ ] The founder-approved primitives in `docs/architecture.md`: N2 phone push, N3 voice and
  video, N4 a live takeover view, N6 undo by snapshot, N7 agent purchases, N9 media results.
- [ ] Mail archive's later phases (ADR-036), past step 5's four: a batched or tiered merge for
  sparse late acks; unread mail archived, with its own research.

S9 (Claude and Grok wakes) is built as slice W. S10 (Sign in with ChatGPT) is parked and its
record deleted (d3 §A8): it comes back as a record if OpenAI approves.

## For the founder

Already decided, so not asked again: the live stage, yes (d3 §F1); the store launch paused until
the new apps are ready (§F2); restore and the off-account copy built before real data (§F3,
§G); the ruleset is the coordinator's to change (§F4); the deletion as two or three PRs in one
window (§F5); the files cipher `aes-gcm` (§G); imported owners' old "ask first" defaults kept as
their own settings (§N7); no paid subscription exists (d2 B13); the founder's and the canary's
owners start fresh (2026-11-04); the window is not announced (2026-11-07).

Open questions, from the as-built record:
1. Codex, Gemini and Kimi sign-ins are off, because each vendor's terms for hosted use are
   unclear (`cli.login.hosted_use`). Turn any of them on?
2. Capture always on gives a recovery point of minutes instead of a night. It costs about $62 a
   month for a heavy owner and $0.20 for a light one, against about $0.70 for the heavy one
   today. Turn it on?
3. Non-model credentials (GitHub, Notion and the rest), git through credentials, WebSocket APIs
   and SigV4 need a TLS-terminating proxy, which is a host (Direction 8). Keep them out of v1, or
   choose one?
4. When the directory submission is made: should the listing get its own MCP door, with grants
   pinned to the `directory` profile, against Direction 2's "one MCP door"? The old stack built
   and retired `/mcp-directory`.
5. The published plugins say `license: "Proprietary"` (`platform/plugins/build.mjs`
   `PUBLISHER`), but the repository is MIT. Which should the plugins carry (list 6, item 6d)?

The coordinator's to decide (no spending, irreversible or outward-facing act), from the
as-built record, and decided (the coordinator, 2026-11-12):
- **Attended** (slice ID's review, H2): "attended" means a live caller is waiting for the
  answer. So `converse` is attended and a client's mail is unattended, as built. The rules key
  on that, never on which client sent it.
- **A chain-depth stop:** yes. An owner setting, off by default (Fact 15), stops agent-to-agent
  mail past a chain depth. It rides lane O (§10 step 4).
- **The OKF watcher's pull requests:** no separate reviewer. The one review gate (d3 §B6)
  reviews them like any other.

Steps only the founder can take, each handed over with its exact commands when it is ready: the
E2B API key rotated; the Spaces read key; the two store dashboards; AuthKit's production Google
and GitHub apps; a Backblaze B2 account for the off-account copy; the stage's two accounts; the
canary's dispatch token and free-tier model key (d3 §J4); the imported owners' notice wording
(d3 §N7); the window's credentials; asking E2B for workload identity (gateway research §13); the
end-of-hold deletions. Optional: a zone WAF rule with his own number
(`platform/deploy/checklist.md`).
