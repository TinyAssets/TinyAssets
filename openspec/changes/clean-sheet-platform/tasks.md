The clean-sheet platform's remaining work (ADR-032 to ADR-037). This file holds only what
isn't built. An item is ticked in the PR that lands it and then shrinks to one line, kept only
where something cites it: the code, the specs, the ADRs and the PRs hold how it was built. The
text before this cut, with every slice's as-built record and review rounds, is
`git show de098f01a:openspec/changes/clean-sheet-platform/tasks.md`.

Pointers: `d2 B<n>` is fix B<n> of the untangle's wave-2 log,
`docs/decisions/untangle-2026-11-wave-2.md`, and `d2 §X` a section of it; `d3 §X` is a section
of the untangle's log of the whole remaining build (2026-11-11),
`docs/decisions/untangle-2026-11-remaining-build.md`, and `list3 P<n>` a problem in that build's
`plan` review; its R1–R8 are not lane R's PRs R1–R9, whose R5 is `R5r`. An id with a colon,
such as `d2:N56`, is a problem in a listing those logs cite.
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
  - [x] 4. The doors' import consumer (`doors/src/import/`, #5475).
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
  design, by the window's `close` alone, once every owner it staged is admitted or discarded
  (`i-window-close.md`); and R1, `deploy-worker.yml` deleted, so no routine deploy of the old
  proxy takes routes back (d3 §J1: the proxy ends with no routes).
- [ ] **Step 13. `routes`, the handles canary, the platform's canary.** `identity.json`'s `host`
  is already `tinyassets.io` (d3 §H). Mode `routes` keeps its route record, then hands the
  routes over; the manifest disables `uptime-canary.yml` (d3 §J2), whose old canary scripts the
  platform's canary replaces rather than reworks (list3 P29). Verify: the handback's plan job
  (read-only); the control record opened for the probe owner (tester B) alone, through the
  operator path; its API key stored as `TA_PROBE_KEY`, and the free-tier key connected to it;
  the platform's canary with `full`, then armed (`PLATFORM_CANARY_TOKEN`). If verify fails,
  before the hold ends: `--mode handback --record <id>`, the old stack restarted on its
  untouched volumes, then `handles-canary.yml`. Writes made on the new platform since `routes`
  are lost unless exported (decided at verify).
- [ ] **The public `/connect` page,** right after `routes`, in the site-at-the-cutover PR
  (d3 §C6 F2): branch `platform-brain-site` no longer builds, so the page is rebuilt from
  `contract/apps.json`, and its sweep checks one section per app.
- [ ] **Step 14. The gates opened through the operator path's `control` mode** (d3 §G): canary
  owners, then everyone.
- [ ] **Step 15. Manifest phase 2, the deletion** (Direction 5): two or three PRs in one window,
  each under GitHub's 3,000-file limit and proved by the manifest's dry run (d3 §F5). It
  deletes the old stack, the import window above, the old stack's workflows (`dr-drill.yml`
  among them), its docs, rules and specs, and `docs/architecture.md`'s S36 row then reads
  converged.
  - **Old tests leave with their code** (Direction 6): every test file
    `python scripts/affected_tests.py --retiring` lists (its `RETIRING` paths follow the
    manifest), and any other test that fails only because its code is gone. None is skipped,
    quarantined or rewritten to pass; a test that also reaches kept code keeps those cases. A
    structural guard (`scripts/ci_structural_guards.py` `GUARDS`) goes only once this line names
    its platform replacement or the decision that retires its principle. `tests.yml` loses
    `select`, `structural-guards`, `required-tests-shard`, `affected-tests` and
    `heavy-tests`; `required-tests` keeps `platform-tests`, `platform-sizes`, `platform-app` and
    `process`.

## 10. The remaining work, in order (d3 §D)

### Process first: priority over every step below (d3 §B5)

Each in its own PR, taking the next free slot, never behind product work; the product lanes
keep running (the untangle skill's "Process first", founder 2026-11-15).

- [ ] P2 in order (d3 §F4; `untangle-2026-11/p2-review-gate.md`):
  - [x] the verdict posted as a `COMMENT` PR review on the commit read, as the App's `review`
    check reads it, and each decision one new check run, never an edit of one (an edit
    GitHub refused had left a red "could not decide" on #5656); ADR-038 drafted
    (`untangle/p2-review-gate`);
  - [x] the check seen green on a real PR (`live:` #5658's head and its merge group
    `54668a171`, then #5659's, each from its PR review);
  - [x] `review` required: `docs/ops/merge-queue-ruleset.json`, with `required-checks.test.ts`
    holding a check an App posts to the workflow named for it, and `enqueue-merge.yml`
    listening to it (`untangle/p2-review-required`; `untangle-2026-11/p2-review-required.md`);
  - [ ] the founder's `PUT` of that file (`live:` host-actions "Pin the context"; until then
    `enqueue-merge` refuses on the drift);
  - [x] a verdict counts only from an account that can push (GitHub's permission read, from
    every grant), and a permission it cannot read is no verdict, never a pass; lands before
    the receipt goes, which would let a read-only member's `APPROVE` pass alone
    (`untangle/p2-review-writers`; `untangle-2026-11/p2-review-writers.md`);
  - [ ] the receipt half deleted, from `land/7`'s `66d29f032` where it still applies, with
    ADR-038 accepted, superseding ADR-015's review line (`untangle/p2-receipt-delete`). With no
    body edit at landing, `enqueue-merge` no longer reruns after the PR review's decision
    (`review` on `workflow_run`, which it skips): decide there whether it follows that run or
    auto-merge alone queues the PR.
- [ ] P6, process code in one place, research first: the scripts' tests beside them, the queue
  helpers reading the platform legs, d3 §B8's three checks decided and §B9's three workflows
  measured, `enqueue_merge.py` reading each check's newest run as GitHub does
  (`untangle/p6-process-code`; `untangle-2026-11/p2-review-required.md` item 6).
- [x] P7's builder page: the untangle skill's "How this project uses it" and
  `docs/reference/executable-gates.md` hold the briefs' rules; `README.md` and
  `CONTRIBUTING.md` point at them (`untangle-2026-11/p7.md`).
- [ ] P7's rest: the workflow headers lose their dated history, which git holds (list3 T12;
  `untangle/p7-headers`).
- [x] P8's rest and P9's "can go now" list: the always-red watchers, the callerless scripts,
  the root leftovers, the OpenSpec prompts' copies, `setup-secure-merge-gate.sh`, the finished
  changes, `ideas/`' leftovers, the Windows hook and pre-commit installer (`untangle/p9-now`;
  what stayed and why: `untangle-2026-11/p9-now.md`).
- [ ] P9: `openspec/` keeps only `clean-sheet-platform`; the old changes and the archive go,
  their readers re-pointed, so the window's deletion is smaller (`untangle/p9-openspec`).
- [x] `slow-tests`, which collected no test, left the ruleset and the classic branch
  protection (the founder, 2026-11-16, d3 §E4), then its job went (`untangle/slow-tests`,
  from `land/3`; `untangle-2026-11/slow-tests.md`).
- [x] The required checks' names are held four times: the ruleset, the classic branch
  protection, `docs/ops/merge-queue-ruleset.json` and `enqueue_merge.py` `REQUIRED_CHECKS`,
  and nothing ties them. One source, researched first (`untangle/required-checks`; found by
  `untangle/slow-tests`): the file; the job names and `enqueue-merge.yml` are held to it,
  `enqueue_merge.py` reads it and stops while the live rules differ; the classic copy goes
  with a host-actions row (`untangle-2026-11/required-checks.md`).
- [x] `required-tests shard 2/6` took 24 to 27 minutes of its 30-minute watchdog on
  whole-surface merge groups, and `required-tests` reported 26 to 29 minutes after the start,
  against the queue's 35: a stale durations table put both eight-minute root proofs on it.
  Each venue is packed on its own by a table refreshed from whole-surface runs only, so the
  slowest shard's tests take 569 to 621 s, about 15 minutes of job (`untangle/shard-2-time`;
  found by `untangle/required-checks`; `untangle-2026-11/shard-2-time.md`).
- [ ] Whether the Python legs need cover for a runner five times slower before the cutover
  deletes them: no packing fits one (a 477 s case and the image build, 62 minutes at five
  times), and #5646's calibration is Vitest's only; research first
  (`untangle/python-slow-runner`; found by `untangle/shard-2-time`).
- [ ] `main_red.py` `REQUIRED_JOB` and `queue_attempts.py` `AGGREGATE_JOB` name the Tests
  aggregate by its job name, and nothing ties them to `tests.yml`: a rename leaves main-red
  reading no job (`untangle/aggregate-name`; found by `untangle/required-checks`).
- [x] An owed item may name a `process/` or `land/` branch as well as an `untangle/` or `fix/`
  one: `drain_review_gate.py` `_NAMES_FIX_RE` (P7's review).

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

- [ ] The wave-2 lanes (d2 §D): lane O (O1–O5) is left, unblocked since C3 and C4 merged; the
  merged lanes' owed items and d3 §A's changes:
  - [x] B3 after T1, on the cleaned constructors (d3 §A2, §K1), as `o1_research.md`'s option (b′):
    per-module `DoorNeeds`, four widening proofs, `untangle/o1` to `o1d`
    (`docs/decisions/untangle-2026-11/o2.md`, "B3's shape").
    - [x] `untangle/o1`: `door.ts`, the placement in `Bodies`, `Archive` in `ownerStores`, the
      row step `seeded`, the tiers kept at the bind, `call.scope`, the leaf modules on
      `DoorNeeds` (`untangle-2026-11/o1.md`).
    - [x] `untangle/o1b`: the four proofs, the policy stores, the guard cut
      (`untangle-2026-11/o1b.md`).
    - [x] `untangle/o1c`: files, collections, ledger, import (`untangle-2026-11/o1c.md`).
    - [x] `untangle/o1d`: runs, launch, signins, restore; `OwnerState` deleted
      (`untangle-2026-11/o1d.md`).
  - [x] B4: the door table generates the prototype methods at module load (d3 §K4). Its generator
    reads the table one way: `contract/requests.ts`'s own `tableRows()` beside `doorTable()`
    goes (lane O; C2). Built in O2 (`untangle-2026-11/o2.md`).
  - [x] B4's contract half (`o2.md` item 9), in two PRs:
    - [x] `untangle/o2b`: `OwnerDoors` written by the generator from the table, and the services
      guard's name and arity checks as type facts (`untangle-2026-11/o2b.md`).
    - [x] `untangle/o2c`: `contract/doors.json` split from `owner/doors.json` (the runner's
      steps), the door map written by the owner (`untangle-2026-11/o2c.md`).
  - Owed to lane O by merged PRs, one line each:
    - [x] S8: the owner adopts `served()` (`s8.md`). Built in `untangle/o-owed`: every owner read
      of a CLI record is `servedRecord`, and of one that signs in, `signsIn` (`o-owed.md`).
    - [x] S9: `LiveRun`'s `lease_id` and `epoch` through `RunHost.stop` (`s9.md`). The ids were
      O1d's; `untangle/o-owed` deletes `LiveRun`: `liveRuns` answers ids, `Runs.holds` the
      lease check (`o-owed.md`).
    - [x] C4: the object parses `#bind` and `egress.ts:330` `sandboxBinding` with `BINDING_PARSERS` (O3).
      Built in `untangle/o3`: each door's binding parsed by its kind's parser, by the kinds its
      caller takes (`CALLER_BINDINGS`), and the egress copy gone (`untangle-2026-11/o3.md`).
    - [x] O2c: `caller` `none` (`owner/doors.json`) beside `UNBOUND_DOORS`, held equal by
      `stepProblems`, goes with B5's `none` binding kind, `untangle/o3` (`o2c.md` item 7). Built:
      the two steps name no caller, and the runner's `none` is `UNBOUND_DOORS`' (`o3.md`).
    - [x] C5b: `owner/guards/config.test.ts` `controlOnly` (`c5b.md`). Built in
      `untangle/o-owed`: the owner's top level is a kit `CONFIG`, `CONTROL` held by binding
      name (`o-owed.md`).
    - [x] X1: B10's literal guard over every folder, `untangle/o4c` (`o4b.md` item 6). Built:
      `deploy/guards/literals.test.ts` reads the live names from `facts.ts`; its permanent
      exceptions are its own `KEPT`, each with its reason (`untangle-2026-11/o4c.md`).
    - [ ] X1b: the deploy kit's test reads the live names from `facts.ts`, and the founder's
      checklist points at the configs and `platform.json`: the 73 and 17 that
      `deploy/guards/rules.ts` owes the literal guard, `untangle/x1b` (`o4c.md`).
  - B12 reshapes only the modes that stay. `routes`' handover, `handback` and `h2` go on the
    manifest's end-of-hold list, and mode `probe` is deleted (d3 §A5, §H).
  - [x] V2-H1's `CONNECTION` goldens re-sealed (#5543; `gateway/src/keyring.ts:58`). Built in
    `untangle/o-keyrings`: the goldens sealed again by their own commit's code for a connection
    id, and the gateway's binding takes the owner's connection ids, a doorbell's its own
    `wake.<SUBSCRIPTION_ID>` (`o-keyrings.md`).
  - [x] B14's keyring half, decided (`x5.md` item 10): not built. `scripts/platform_setup.py`,
    its tests and `deploy.test.ts` "takes the keyring the founder's setup makes" are on the
    manifest's cutover list, so the deletion PR takes them together (`o-keyrings.md`).
  - [x] B22's sweep doorbell half (O5; d3 §I). Built by R7 (`r7-rotation.md` item 4), the
    comment by #5509 and the deadline test in `gateway/test/forward.test.ts`; proved in
    `untangle/o4c` by a mutation (`o4c.md`).
  - B24: the whole cutover's manifest, in three phases, with its dry-run job (d3 §A10). Its
    platform Workers are entries in the deploy kit's `retired` list (`deploy/platform.json`),
    the one remover of a platform Worker (d3 §O2).
    - `untangle/b24-manifest`: `scripts/platform_setup.py` is on the cutover list
      (`o-keyrings.md`). Its inventory of `scripts/` takes what still names it: `platform.json`
      `made`'s `why`, `deploy.ts`'s kit note, `checklist.md`'s setup line,
      `platform-deploy.yml`'s header, `docs/host-actions.md`, `.github/test-durations.json`,
      `deploy/review-app.html` (it runs the setup's `review-app` step, and only the setup's tests
      read it) and `tests/test_workflow_production_environment.py`'s note. It also decides where
      the setup's `workos` and `review-app` steps go, since the deletion takes them too.
  - B26: the specs and docs follow the code, and a check that every path and identifier the
    specs name exists (d3 §C7).
    - [x] Check concrete platform paths and backticked uppercase names in the three active
      specs, pin linked constants to their modules, and align code-mode answer sizing and
      the migration bound with the implementation (`untangle-2026-11/b26-spec-references.md`).
  - Lane O: the owner setting, off by default, that stops agent-to-agent mail past a chain
    depth (Fact 15; the coordinator, 2026-11-12, below).
  - [x] Lane G's first PR: `MISTRAL` derived from `COMPLETIONS` in `contract/src/api-rules.ts`,
    with G-H11 and G-SP5 (d3 §O5): one per-API table (`g1.md`).
- [x] Lane T: T1 on the owner before B3, T2 on the doors after D3, T3 the egress memory leg
  (d3 §K1, §K2).
  - [x] T1, the owner's tests at production's numbers through the object's one wiring, twelve
    PRs, T1a to T1e (#5515 to #5588; `t1c.md`).
    - [x] T1's last edges: the owner tests' five B23 entries in `owner/guards/rules.ts`
      (connections, export, gateway-support, holds, runs-authority), each through its Worker's
      front door (`t1-owner-edges.md`).
  - T2, the doors' tests through the owner's front door, four PRs (`t2.md`, `t2b.md`, `t2c.md`,
    `t2d.md`):
    - [x] T2a: the owner an auxiliary Worker behind the owner gate, every reach into its object
      gone, and the doors' test `OWNER` (C2b) typed as the doors' env types it; 13 of the 20
      B23 entries in `doors/guards/rules.ts`.
    - [x] T2b: the doors' real entry, its sign-in the live one by config against a fake AuthKit
      (`d2:N49`, `N50`, `N53`), and with it T1c-b's `owner-erasures-recorded` queue: the
      owner's erasure queue consumed by the doors' own entry; one B23 entry.
    - [x] T2c: the last six B23 entries, the doors' `OWED` empty: a doors test asserts an owner
      refusal by its code and the owner's suite holds the reason; `ERASE_CONFIRM` and
      `BODY_BYTES_MAX` in the contract; the fake sandbox in `ci/`; `d2:N52`, `N84`, `N85`
      (`N89` was already gone).
    - [x] T2d: the table doors' answers typed from the contract, not by hand: `callDoor` and
      `ownDoor<D>` answer `DoorAnswer<D>`, the 13 answers the doors' tests read are `DoorTypes`
      entries that `OwnerDO implements OwnerDoors` holds to the owner's, and the two #5657 left
      (`sandboxRung`, `restoreRelease`), which nothing reads, are gone.
  - [x] T3, with the egress memory leg's move: `GATEWAY_KEYRING` required
    (`gateway/src/keyring.ts`), as T0b left it (list 6, item 6i). The leg runs the real doors
    and gateway bundles (`gateway/memory/`, `npm run test:memory -w gateway`), and the
    metered path's tree is gone before its encode, which the leg found held (`t3.md`).
  - [x] A CI runner 4.5 to 5 times slower than usual fails tests at their budgets, past the
    budgets' factor of 4 and the watchdogs' 3, and `search-cpu`'s host CPU bound with them: the
    runner-variance rule (`untangle/budgets-variance`, #5640's review; `budgets-variance.md`).
- [ ] Lane R, the operator path and recovery: its nine PRs as d3 §G orders them, before real
  data arrives (d3 §F3). Its `sweep` carries the key rotations: a new kid active, then
  `owner.rewrap` (or `owner.reseal`) for every owner until a pass answers `left` 0 for every
  owner, and only then the old kid removed from `FILES_KEYRING` (or `GATEWAY_KEYRING`), no
  sooner than the off-account copy's versions under it expire (`r7-sweep.md` item 10;
  `r7-rotation.md`).
  - [x] The rotation's PR 1 (`r7-rotation.md`): `dataKeyRewrap` answers `{rewrapped, roots,
    left, kid}`, a changed root and a restore's hold counted in `left`, never refused; and
    `gatewayReseal` `{resealed, left, kid}`, resealing doorbell subscriptions' blobs
    (`wake_callbacks`) too, since an idle one otherwise broke at a gateway kid's retirement;
    the operator ops `owner.rewrap` and `owner.reseal`.
  - [x] The rotation's PR 2 (`untangle/r7-rotation-driver`): kit mode `rotation` over the
    directory's owners with its progress record in CONTROL, and mode `retire` with the
    founder's words, for `FILES_KEYRING` only after a second pass begun `KEEP_DAYS` + 1 days
    after the first copy night that followed the first (`r7-rotation.md` item 6;
    `r7-rotation-driver.md`).
  - [x] An imported owner whose admit never ran keeps its data and no `o:` record after the
    window, so no rotation reaches it: the window's `close` passes only when every bundle index
    the staging bucket holds, of any run, names an owner the directory lists, and the real
    window's pass alone deletes the doors' `IMPORT_KEY`, so `rotation`'s refusal holds until
    then (the founder's B, `r7-rotation-driver.md` item 8; `untangle/i-window-close`,
    `i-window-close.md`).
  - [x] A restore from the off-account copy between a `FILES_KEYRING` rotation's passes puts
    old-kid roots back and restarts the copy's expiry, so `retire`'s gate is read from that
    night (`r7-rotation-driver.md` item 11, `untangle/r8-retire-gate`): `retire` reads the copy's
    restore record (`deploy:offsite:restore`) and draws the gate from the first pass that swept
    and began after it, and needs no second pass (`r8-retire-gate.md`).
  - [x] R8, restore from the off-account copy and its monthly drill
    (`untangle/r8-copy-restore`): every write from the copy into R2 puts the restore record
    before its first byte, in the kit's concurrency group (`r8-retire-gate.md` item 3), and
    every erasure the copy can't show is applied before any owner is restored (`r4.md`
    item 13): mode `copyback`, one owner's namespace as its own object names it, so the doors'
    erasure records refuse an erased owner first, and only the objects a row of it names
    (`restore.names`), so nothing its own collection deleted comes back; and the monthly
    drill, read-only, on the whole bucket (`r8-copy-restore.md`).
  - [x] An erasure record that survives the account, option A (`r8-copy-restore.md` item 5):
    the files Worker marks each namespace it deletes on purpose (`e/<ns>`, no personal
    identifier) at the erasure's start and before its first delete, and nothing deletes a mark; every copy back reads the
    marks from R2 and the copy first and refuses a marked namespace, loudly when they can't be
    read, and the monthly drill reads them whole (`untangle/r8-account-loss`,
    `r8-account-loss.md`).
  - [x] Every owner after an account loss, restored from the copy: `locate` (part 0's header
    owner, read in the files Worker through its `FilesLocate` entrypoint, the operator op
    `restore.locate` addressed to the platform) and mode `recover`, the marks written back
    first and each unmarked namespace with a root written back, located and its owner's restore
    begun in the same run; and a night refuses to copy a bucket lacking a mark the copy holds,
    so no night before `recover` hides the marks (`r8-account-loss.md` item 7;
    `untangle/r8-locate`, `r8-locate.md`).
  - [x] No erasure misses an owner's namespace its object does not adopt: the files Worker's
    mark and delete take each namespace one holds (`o/<ns>/h/<held>`), `recover` holds a
    namespace whose owner is live by that owner's own (`restore.hold`) and begins none another
    holds, and `owner.admit` refuses an owner the directory records whose object is pending
    (`untangle/r8-erased-orphans`, `r8-erased-orphans.md`).
  - [x] A lost object, the account intact: the unmarked namespaces whose roots R2 holds
    located, the one naming `--owner` taken, then mode `restore` from it, every other one
    naming it held by the restored one; a live owner restored from a namespace it holds; and a
    located namespace whose owner is erased erased (`r8-account-loss.md` item 7, `r8-locate.md`
    item 7, `r8-erased-orphans.md` item 8; `untangle/r8-lost-object`, `r8-lost-object.md`).
  - [x] An erasure of a lost object marks nothing (its object and no restore record name a
    namespace): mode `erase` finds its namespaces first, and refuses while R2 holds one naming
    it, until it is restored (`r8-lost-object.md` item 8; `untangle/r8-lost-erase`).
  - [ ] A lost object whose namespace R2 lost too, only the copy holding it: no mode restores
    it, since `copyback` needs an object naming the namespace and `recover` begins every owner
    (`r8-lost-object.md` item 8; `untangle/r8-lost-copy`).
  - [ ] Live, on the stage: a copy back of a rehearsal owner's namespace with a day's R2 write
    key, then its restore (`live:` after `untangle/r8-copy-restore`).
  - [ ] Live, on the stage: a rotation of each keyring through `retire` on a rehearsal owner
    (`live:` after `untangle/r7-rotation-driver`).
  - [x] `restore.wipe`, a live owner brought to pending and empty for a restore, the
    generation to restore pinned first, since a fresh generation's finish prunes every older
    unpinned one (`r5r.md` item 14). Restore (#5587) and owner migration 10's pin (#5589) landed;
    the wipe landed as #5607.
  - [x] Owner migration 11's pin PR, after `restore.wipe` merges (`r5w.md` item 9): #5608.
  - [x] The sweep's rotation counts a restore record's `gen` and `fresh` (a wiped or failed
    restore's object knows neither), or does not finish while any owner is held
    (`r5w.md` item 14): #5616, then counted in `left`, so a pass never ends while any owner
    is held (`r7-rotation.md`).
  - [x] Owner migration 13's pin, after the generations' sweep merges (`r7-sweep.md` item 5):
    with `owner.state` (`owner-state-op.md`).
  - [x] A restore's unnamed chunks swept: those the owner put after E have no row in the
    restored object, so nothing collects them. Two restore steps under the hold, between
    `chunks` and `clear`: `names` (every `file_chunks` row's name from `FILES.chunkNames`, in a
    Bloom filter sized for the file index's most chunk rows, one row) and `sweep`
    (`FILES.listChunks` from the last name, each name the filter says no row has deleted by
    `deleteListed`; a false positive leaks at most about 1% of them until erasure); owner
    migration 14, its pin after (`r-restore-chunks.md`, `untangle/r-restore-chunks`).
  - [x] Owner migration 14's pin PR, after the restore's chunk sweep merges
    (`r-restore-chunks.md`, `untangle/r-restore-chunks-pin14`).
  - [x] A restore's kept generations' chunks kept from early collection: the files plane
    collects early only garbage the newest generation does not name, so the fallback a
    restore loads can lose a chunk and its restore fails at its chunks step (`r5w.md`
    item 11): #5611.
  - [x] A generation's restorability by what a restore stages: `log_bytes` counts every image
    the log appended, but a restore stages only the last per key, so an owner rewriting hot
    rows (`model_spend`, `runs`, `collection_clock`) during a base pass is refused a
    generation that would fit; the capture keeps the staged size, an owner migration
    (`r-fallback-chunks.md` item 5, `untangle/r-log-staged-bytes`): the capture's peak of
    each key's last line at a segment's end (`log_staged`), the restore's own count failing its
    log step past `LOG_BYTES`, owner migration 15 (`r-log-staged-bytes.md`).
  - [x] Owner migration 15's pin PR, after the staged count merges (`r-log-staged-bytes.md`,
    `untangle/r-log-staged-bytes-pin15`).
  - [x] A restore across a migration (`r-restore-schema.md`, `untangle/r-restore-schema`): a
    generation of an older schema brought forward by each migration's declared form
    (`FORWARD`; 10–16 change no exported table, so schema 9 on restores; 7 and 9 refused,
    named), the schema recorded at begin (`owner_restore.schema`, `source`), one check failing a
    restore a migration lands under, `restoreWipe` refusing a pin it could not load,
    `restorableAt` and `adopt` reading the generation's schema; owner migration 16.
  - [x] Owner migration 16's pin PR, after the schema restore merges
    (`untangle/r-restore-schema-pin16`).
  - [ ] Live, on the stage: a rehearsal owner pinned, wiped and restored, its check equal,
    proving a synced write outlives the wipe's reset in production (`r5w.md` item 2;
    `facet-probe.md` Q8 never sampled it).
  - [x] `owner.erase`, the email route's erasure (the site's `/account`, the Play Data safety
    answers): mode `erase` asks it only with `--confirm erase-<owner>`, and the doors begin the
    owner's own erasure for an owner the directory signed up (`owner-erase-op.md`).
  - [ ] Live: `owner.erase`'s first real use, the founder's call for each request; the stage's
    end uses the owner's own door (d3 §H).
  - [x] `owner.state`, mode `owner`'s before and after: a `reserved` owner door `ownerState`
    (admission, reasons, tier, storage used) and its op in `OPERATOR_OPS`, unblocked since
    T1c merged (`r3.md` item 9).
  - [ ] The off-account copy's first live nights, after the founder's Backblaze steps
    (`checklist.md`, "The off-account copy"; `r4.md`), and not before the canary alarms on a
    missed copy night (step 5; lane X): `KEEP_DAYS` keeps the 30-day erasure promise only
    then (`deploy/offsite.ts:112`). The dark bucket copied and checked;
    rclone hiding with a key that lacks `deleteFiles`; an object deleted in a rehearsal
    namespace hidden in B2 and still among its versions; a night past the deletion limit
    hiding every deletion and then failing;
    a one-day lock and the lifecycle rule together deleting a hidden version once the lock ends.
- [ ] R5, where an action is decided (d3 §N): its six slices (`r5_research.md` §6), after B25
  and alongside lane O.
  - [x] P-shape: the policy's `actions` table and `contract/acts.json`, the doors' rows naming
    their act, a rule nothing could see refused at write; the old rule tables, `spend` and
    egress `ask` deleted; the default document and its digest anew, with owner migration 6
    (#5531; its pin, #5537).
  - [x] P-decide: the decide function and its receipts bucket; `action_request` cards and the
    once-approvals (bounded, generation-bound, swept); the owner's approve and refuse doors;
    the decide step in the runner, from the rows' `act`; a rule's `agent`, with the agent id's
    rule moved to the contract (`untangle/r5-decide`; `r5-decide.md`).
  - [x] Owner migration 17's pin, after P-decide merges: rode `untangle/r5-resume-hooks`.
  - [ ] P-decide's receipts in the export: `action_receipts` joins it with the first migration
    that builds a forward form (`schema.ts` `FORWARD`; `r5-decide.md` item 5,
    `untangle/r5-receipts-export`).
  - [x] P-resume: an approval starts the conversation's resume run; Claude's `tool_deferred`
    stop matcher in its record's `events`; a held call's digest bound to its run's
    conversation; owner migration 18 (`untangle/r5-resume-hooks`; `r5-resume-hooks.md`).
  - [ ] Owner migration 18's pin, after P-resume merges (`untangle/r5-resume-pin18`).
  - [ ] P-hooks: the hook helper and the run-scoped decide door; the `decide` block in
    `sources/src/schema.ts` and the four records; the runner writes the root-owned files only
    when needed, and runs the start check; the honest note (d3 §N5): `policyRead` gives each
    rule where it holds and where it can't (`holds_at`, `not_enforced`), from `acts.json` and
    the agent's engine record; a held call the hook deferred resumes on no input, and its
    conversation waits on its card (`r5-resume-hooks.md` items 6 and 7). After the sandbox
    pass lands, since the runner writes the files (`untangle/r5-hooks`).
  - [ ] P-import: the census of non-default `entries`, `spend` and egress `ask` (read-only);
    the mapping, the old defaults written explicitly (d3 §N7), and the archive file; the
    notice. Until it lands, an old document is archived whole (`policy.refused.json`).
  - [ ] Later, only if an owner's rule needs it: hold-on-sync for `paths`, and asking for a
    host through web-fetch hooks.
- [ ] The comments pass and then the tests pass, one PR an area (d2 §E steps 4 and 5). The
  tests pass takes the two older tests that make a call refuse by patching a class's prototype
  (`owner/test/runs-authority.test.ts:864` `Files.prototype.open`,
  `owner/test/erasure.test.ts:92` `Lifecycle.prototype.disableAlarms`; list 6, item 9). The
  two patched calls are in-process (the owner's own SQLite; the agents library's
  `Lifecycle`), but each test's fault has a front door through `FILES`, so both move to a
  gate in front of it in the owner suite (`methodsOf` `ChunkStore`, as the ci suite's
  `files-gate`; B23): an `agent.json` over `INLINE_BYTES` read while its `getChunks` is held
  and the file is rewritten through the doors; the erasure's `deleteNamespace` refused once,
  for an owner with files, so it stops at step 2 before the wipe.
  - [x] `platform/sandbox/`, both passes in one PR, with `s2:N51` and the sandbox's own
    swallowed errors and retries decided (`untangle/pass-sandbox`;
    `untangle-2026-11/pass-sandbox.md`).
- [ ] d2's problem index, with decisions for the swallowed errors (`d2:N56`), the retries
  (`d2:N57`) and `s2:N51` (d3 §A11).
- [ ] The re-check loop (d2 §E step 6): every `owed` list empty, the layer guard with no
  exceptions, nothing worth fixing left, behaviour unchanged.
  - [ ] `sandbox/runner/runner.ts` `safeSignedIn` logs why a status check failed
    (`pass-sandbox.md` §4).
  - [ ] `contract/src/sync-ignore.ts` `wildcard`, with no reader since `s2:S95`, deleted
    (`pass-sandbox.md` §2).
  - [ ] A renewal waits for its runs' answers: the Worker gives `renew-wait` its own now as the
    deadline (`control.ts` `#renew`), and `tell` waits for an answer only to it, so no run's
    posts are drained before the pause, as `runner.ts`'s `Job` intends. Research the answer's
    bound first (`pass-sandbox.md` §2).

### Step 5. What the cutover needs that the lanes don't hold (d3 §C)

- [x] The operator path's minimum: lane R's channel, `control` and `owner` (d3 §C2; `r3.md`),
  and `owner.state` (`owner-state-op.md`).
- [ ] The manifest's dry run green (d3 §A10).
- [x] The platform's canary and one alert path, as d3 §J2 and §J3 design them (R6; X5,
  `x5.md`). Live from `routes` (§9 step 13).
- [ ] The canary's alerts for what is only log lines today (list3 P18): `sandbox_waiting`,
  `sandbox_renewal_refused`, `sandbox_runner_mismatch`, `exceededMemory`, and E2B's team metrics
  (`/teams/{id}/metrics/max`) against the concurrency ceiling. Reading the logs needs a
  read-only Cloudflare token (Workers Observability Read), not the deploy token. E2B's credit
  and spend have no API. (The off-account copy's alarm past two nights is built: `x7.md`.)
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
  - rooms and room members with opaque ids, in lane O after T1c and before S4;
  - collection history (§P item 3): H1 merged (#5538); S1, the stream extraction into
    `owner/src/stream.ts`, after T1b4, receipts and rooms; H2, the erasures stream; H3, the
    history stream, bounding the deleter's name or taking the factor into the guard's term;
  - conversation ids carrying the order key, idle conversations to segments, after T1b4 and
    on S1's stream module (§P item 3);
  - files: F1's owed census refusal at the index's ceiling (lane I; #5548's body); F2
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

### After the cutover: fixes for rare failures nothing builds on

The fixes the untangle skill's cutover rule holds until after the cutover (founder,
2026-11-15), each named with its branch.

- [ ] An owner a blob that no longer opens keeps owing (a refused data key, root,
  connection or doorbell blob, each counted left) has no remedy, so `retire` never passes while one
  exists: the founder's decision between the options in `r7-rotation-driver.md` item 9,
  then its build (`untangle/r7-unopenable`).

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
