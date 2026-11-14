# The whole remaining build, in one order (2026-11-11)

This log joins three untangle reviews to the wave-2 plan:
- `list3_plan.md`: the rest of the build (69 problems, R1–R8);
- `list3_clients.md`: the surfaces outside `platform/` (65 problems, F1–F4);
- `list3_process.md`: the rules, gates and docs (105 problems, P1–P9).

The wave-2 log, [`untangle-2026-11-wave-2.md`](untangle-2026-11-wave-2.md) (`d2`: B1–B26, §G), still holds where this log doesn't change it, and [wave 1](untangle-2026-11-wave-1.md) under both. Problem ids keep their list's prefix: `p3:P13`, `c3:…`, `x3:T9`. The listings, and the other working files this log names (`*_research.md`, `founder_batch.md`, `brief_common.md`), were not kept in the repo: a name or an id points to the evidence, and each decision stands without it.

## A. Amendments to wave 2 (decided)

1. **B25 goes first, right after the u3 PRs and C1 merge** (`p3:P58`). Every later lane then writes `codemode`. Its builder first reads what the owner policy's `hooks` table and `hooks.codemode` switch mean (`p3:P59`): if that table names only code mode, it is renamed in the same PR, free today and a data migration after the cutover.
2. **B3 waits for B23's research questions 1–3** (`p3:P57`), so the owner's constructors are cut once. The same researcher answers B4's question (prototype-defined door methods under Workers RPC, `d2` §G1).
3. **B6's queue names are generated from the Worker configs** into the contract by `npm run generate` (B9). One home, and B10's literal guard holds (`p3:P54`).
4. **B8's compatibility date is a kit check that the six configs agree,** not a copy in `deploy/platform.json` (`p3:P66`).
5. **B12 reshapes only the modes that stay.** `probe`, the `routes` handover, `handback` and `h2` are cutover-only: they get the importer's rule (no reshaping beyond what the row table needs) and go on the manifest's end-of-hold list. `h2` isn't kept because a spec names it (`p3:P60`, `P61`). The plan artifact owed before `routes` joins B12 (`p3:P26`).
6. **B13's routes are researched before they're chosen** (`d2` §G10): they must take `/app?upgrade=1`, `/app?request=…` and every other `/app` URL the app, the site and the shells use.
7. **B14 builds only the platform's side of the canary** (`deploy/canary.ts`), which grows into the platform's own canary (R6). The old side stays in Python until the hold ends and is listed on the manifest (`p3:P67`).
8. **B17 loses its special case.** `sources/chatgpt.json` and the `unwired` field are deleted first: an absent record, never a stub (`p3:P46`, `P65`).
9. **B20 doesn't make a workspace file an instruction** (`p3:P55`, `P56`). The MCP instructions' onboarding sentence is generated from the onboarding skill's step list at build time, as the plugins are. The wording stays as it is today until BRAIN's live step 8 measures it. The import notice promises only what can be done (`p3:P31`).
10. **B24 becomes the whole cutover's manifest, in three phases** (R3, P4, F3): before `routes`, the cutover change, and the end of the 14-day hold. Each entry is a path to delete, an edit, a move, a carry-out with its new home, or a live object (Worker, route, bucket, queue, secret, DNS record, token). It covers every path in the repo, not only `platform/`: the old stack, the 31 workflows, the gate scripts, the docs, the rules, the specs, the site, the shells and the top-level folders. A CI job applies the phases to a temporary tree and runs the platform suites, the guards and actionlint. It is green when nothing that stays depends on what goes. Every change wave 2 makes to permanent code for the importer's sake is marked on it (`p3:P62`). The operator channel is carved out before the import consumer is listed for deletion (item C2).
11. **`d2` gets a problem index** (`p3:P64`): every list2 item is named by a fix, a u3 PR or a decision. The swallowed errors (`d2:N56`), the retries (`d2:N57`) and `s2:N51` get decisions.
12. **The one paid template build moves to after the re-check loop** (`p3:P63`). It carries a refresh of the engines' locks (`p3:P19`) and puts the E2B SDK's hashed lock (`requirements.txt`) in the tag (`p3:P68`, S0). The base image `e2bdev/base` is a tag E2B can move, so its content is in no tag: a cutover known limit.

## B. Process (decided, from `list3_process.md`)

1. **P1, the merge gate takes the cutover's shape now.** Building (`process/p1-merge-gate`). The Python suite gates only a diff that touches the retiring stack.
2. **P3, the PR body carries no ritual line.** `Net core lines:` has no reader, `Registry row(s):` feeds an advisory issue, and `Lane:` restates the diff. They go, with the 8-file hard cap (kept as a note for the reviewer). One PR, after P1.
3. **P5, the tamper check reads the platform's tests.** Today it reads only `tests/**/*.py`.
4. **`platform/deploy/` joins the release-critical paths.** It is the kit that deploys production.
5. **P6–P9** (process code in one place, one builder page, skill mirrors, the planning system after the cutover) follow the lanes. P7's builder page replaces `brief_common.md` and the working briefs, so the rules a builder needs are in the repo.
6. **P2, one review gate,** needs the founder's ruleset pin (§E). Until then the receipt stays, and reviews stay independent: no builder approves its own PR.
7. **The cutover's deletion can't pass today's gates** (`x3:X1`, `X2`, `T9`): the 8-file cap, a forbidden path, the 3,000-file limit, and `slow-tests` as a required check. P3 removes the cap and the forbidden path before the window. The deletion is two or three PRs in one window (code, tests, workflows and scripts; then `openspec/`; then docs), each proved by the manifest's dry run. The ruleset step is the founder's (§E).

## C. What the cutover needs that wave 2 doesn't hold (decided, some after research)

1. **R4, `tasks.md` holds only what isn't built.** One docs PR after u3-cross merges: about 240 KB becomes about 30 KB, every owed piece gets its own line, and §10 becomes the real list.
2. **R2, one operator path.** After the cutover nothing can set a tier, suspend an owner or run a key rotation: the importer's consumer is the only caller, and the cutover deletes it. Research first: keep the import's MAC'd queue as the operator's channel, or build a separate door. The minimum before the cutover is `control` and `owner` (tier, suspend, admit), as kit modes that print a plan and apply with `--yes`.
3. **R6, the platform's own canary and one alert path.** From `routes` on, nothing watches the new platform. Research first: whether a canary run needs a paid model source, and which Cloudflare log API the token can read.
4. **The deletion-jobs step** (`tasks.md:2464`, `p3:P28`). An owner who asked to be erased after the backup must not be imported and admitted. No runner mode does this yet. Blocking before `admit`.
5. **R5, the policy document holds only what something decides.** Imported "ask first" rules are carried, but no code reads them. Research first: where an action can be decided on unmodified engines. Then the founder chooses (§E).
6. **The clients** (`list3_clients.md`):
   - **F4:** the mark's source, `tinyassets/desktop/icon_gen.py`, moves out before the old stack is deleted, or the site stops deploying.
   - **F2:** the site reads the platform's contract (`identity.json`, `contract/apps.json`, `profiles.json`), as one "site at the cutover" PR. The `/connect` page is rebuilt from `contract/apps.json`: the parked branch no longer builds. The privacy and account-deletion pages are corrected (the founder owns the copy).
   - **F1:** one shell contract in `identity.json` for the store apps. It waits on the founder's choice (§E).
   - `erasureAnswer` is wired at the erase door before any store submission, since the stores require in-app deletion.
7. **R7, specs state behaviour,** and B26 ends with a check that every path and identifier the specs name exists.
8. **Cheap and early:** the E2B days-long resume measurement, the vendors' wake pages re-read, and Durable Object regional availability.

## D. The order

**Now, alongside the lanes:**
1. Land u3 ×4, C1 and P1. Then B25, then the lanes as `d2` §D orders them, with §A's changes.
2. Research, each its own step: B23 questions 1–3 with B4's prototype methods; the operator channel (R2); the stage's identity (R1); where an action can be decided (R5); B13's routes.
3. `tasks.md` re-cut (R4), P3, and the start of the manifest's inventory.

**Then:**
4. The lanes, lane T, the comments and tests passes, the re-check loop, and `d2`'s problem index.
5. What the cutover needs (§C): the operator path's minimum, the manifest's dry run, the canary and alerts, the deletion-jobs step, the site PR, and the shells if the founder says so.
6. The engines' locks refreshed, the one paid template build, the pin PR.
7. The dark redeploy of the untangled build, and mode `probe`.
8. The live stage, if the founder chooses it (§E). In order:
   1. H2;
   2. the SSE-C probe and the rehearsal's platform half;
   3. each engine's record;
   4. the connector tests;
   5. BRAIN steps 1–8;
   6. the wakes;
   7. the founder's own test.
9. Fix what the stage found, in the new stack only. Run the untangle's re-check once more.

**The window:**
10. Backup, census, the off-site copy.
11. `bundle`, `stage`, `verify`, the deletion jobs compared, `admit`.
12. Manifest phase 1.
13. `routes`, the handles canary, the platform's canary.
14. Open the gates through the operator path.
15. Manifest phase 2, the deletion.

**After:**
16. The 14-day hold, then manifest phase 3.
17. Restore, and the off-account copy.
18. The deploy path's compatibility rule and the engine build cadence.
19. Shells, billing, connectors, the Direction 1 comparison, directory submissions.
20. The later slices, re-listed, rooms and status first.

## E. For the founder (one list)

1. **A live stage before the cutover** (R1). Today nothing can reach the new doors until the cutover's `routes` run. So the first real model call, the first connector sign-in, H2 and the founder's own test would all happen inside the maintenance window, after every owner has been moved. A stage is the dark platform on its own hostname, open only to the founder and the canary. It needs a live hostname, a WorkOS redirect and an E2B hook. Recommendation: yes.
2. **The store apps.** Both shells break at `routes`. Are they in users' hands, and is a store review still running on the old app? The choice is to make them work across the cutover, or pause the store launch until the new app is ready.
3. **Recovery.** After the cutover the new backups sit in the same account, and nothing can read one back into an owner. The choice is whether restore and an off-account copy are built before real data arrives (a spending decision) or first after.
4. **The ruleset.** Pin the App's `review` check as required now (P2). In the window, remove `slow-tests` from the ruleset and the classic branch protection before the deletion merges.
5. **Direction 5's "deleted in the same change".** The deletion is over 3,000 files, more than one PR can carry. Is two or three PRs in one window the same change?
6. **Imported "ask first" rules** (R5), after the research gives the options.
7. **The Spaces read key** for the rehearsal, already asked.

## F. The founder's answers (2026-11-11), and what they decide

1. **The live stage: yes** (R1). Research first (how the identity varies without a second copy; whether Claude and ChatGPT accept a second issuer for a custom connector; what is wiped before the window), then one kit PR and one doors PR. Step D8 runs on it.
2. **The store launch is paused until the new apps are ready.** F1 (the shell contract and the new shells) moves to after the cutover, step D19. Before the cutover, the stores' dashboards are checked for anything we are required to follow up (a review in flight, an auto-release, a testing-track deadline, a policy form). Research names what each store requires; the dashboards themselves need the founder's sign-in, so that check goes on the list for his local session.
3. **Recovery is the coordinator's call, and the spend is small.** Decided: restore and an off-account copy are built before real data arrives, as part of the operator path (R2: mode `restore`). Research picks the off-account home and its cost.
4. **The ruleset is the coordinator's to change when the change is known to be correct.** P2 lands in this order: reviews are posted in the form the App's `review` check reads, and the check is seen green on a real PR; then `review` is made required; then the receipt half is deleted. `slow-tests` leaves the ruleset and the classic branch protection in the window, before the deletion merges. If the session can't write the ruleset, the exact command goes on the local-session list.
5. **The deletion is done the correct way: two or three PRs in one window,** each under GitHub's 3,000-file limit so the gates can read the whole change, each proved beforehand by the manifest's dry run.

## G. The operator path and recovery: decided (`operator_research.md`, 2026-11-11)

The research's design is adopted whole. In short:
- **Channel:** a permanent queue, `tinyassets-operator`, consumed by the doors behind a keyed MAC (`OPERATOR_KEYRING`), with a nonce accepted once and an answer written to a locked bucket the kit polls. The import's queue stays one family and is deleted whole, so B24 and B5 are no longer blocked on this.
- **`control`** edits the control record directly in KV (parse, difference, keep the previous, read back), because the kill switch must work when the doors don't.
- **`owner`** (tier, suspend or lift, admit) and **`sweep`** (data-key rewrap, gateway reseal, later the doorbell half) go through the channel.
- **Restore:** whole-owner, one generation, into the owner's own object only while it is pending and empty. It refuses another owner's generation, an erased owner and a non-empty object.
- **The files cipher is `aes-gcm`, decided now.** Under SSE-C the stored bytes can't leave the account without each owner's key, so no off-account copy could exist. This settles `v:S4` and the SSE-C probe (`tasks.md:1978`). The cipher PR proves the Worker's CPU and memory stay inside their measured budgets at production chunk sizes.
- **Off-account copy:** Backblaze B2, written nightly by a scheduled workflow, with a key that can write but never delete, 28-day Object Lock and version retention (inside the erasure's 30-day promise). About $0.50 a month today and $5–$6.50 at ten times the data.
- **Build order (nine PRs):** the cipher; the channel; `control` and `owner` (blocking for the cutover); the copy; restore's two halves; `sweep`; restore from the copy with a monthly drill; the manifest entries. They form lane R, after B25 and alongside the other lanes. Lane R's doors and owner files are sequenced with lanes D and O.

## H. The live stage: decided (`stage_research.md`, 2026-11-11)

The research's design is adopted:
- **One set of Workers, the production ones.** The stage is the kit's `routes` mode run while `platform/contract/identity.json` names `stage.tinyassets.io` as its `host`. The identity gains `zone` (`tinyassets.io`). There is no stage mode in the kit and no stage branch in product code.
- **Only `host` varies.** `egress.tinyassets.io` is attached for real during the stage, so the runner and probes pair is the same bytes on the stage and in production.
- **The kit learns three general things:** it makes the host's DNS record and hands it back; it finds E2B's hook by name and moves its URL; and mode `probe` is deleted, because the stage's first sign-in does its job (this joins §A5).
- **Only two stage accounts sign in,** made for the stage and admitted by the control record's canary gate. The founder's main account and the old canary's never touch the stage: an erased owner's id is barred for good, and they start fresh at the cutover.
- **The stage ends with the product's own erase door, then `handback`.** Both are first live proofs. The stage is deleted, not kept.
- **The cutover adds one line:** `host` becomes `tinyassets.io`, merged before the window opens.
- **Four PRs,** after B13 (the `/app` routes), B14's canary and the operator path's `control` mode: the zone and host split; the kit; the change to the stage host; the change back.
- **Still a first at the cutover:** an existing connector at `tinyassets.io/mcp` meeting the new issuer. No vendor page settles it and the stage can't test it. `handback` is the way out.

## I. Owed items gathered from the u3 reviews (2026-11-12)

Each joins the lane named. None blocks its u3 PR.
- **Lane S:** `sandbox/runner/sync-ignore.test.ts` is tagged `large` (it builds 12,000 real files and a git oracle) and the budgets are re-derived with `ci/budgets.mjs --write`. It timed out under load for four builders; its 38 s medium budget leaves no room.
- **B2 (refusals):** `owner/src/wakes.ts:326` catches a doorbell open failure without logging and settles the wake `unknown`, so the ring is dropped. Fact 8: it must surface.
- **B22:** the gateway deadline before a provider's headers is untested since the test-only parameter went; it gets a test that drives real time or B23's fake time. `owner/src/egress.ts:71` still says "5-minute".
- **After merge of u3-services:** the runner and probes bundles move (`2cf8172d`, `079923e2`), so the next deploy needs a pair check and the pin PR. No template build.
- **Engines' idle cuts:** the 300 s cut follows Claude Code's and Codex's own. Gemini CLI's and pi's were not researched; that research rides the live stage's engine checks.
- **Lane S, from S3 (B16):** the run `lchown`s only the top of a conversation directory (`runner.ts`), and the route probe gives it whole (`probes.ts`), so the probe does not judge the run's handover there. Research which one is right (a later run may take another pool user than the one that wrote the files), then the session owns the one way, for both. And a run whose CLI exited while a child of it holds its output stays live until a cancel (S3's review; before S3 the cancel did not reach it either): decide whether a run ends at its CLI's own exit.
- **The live stage's engine checks, from S3:** a sign-in's credential files are its user's (0600), a run's root's (0644). Whether Gemini CLI's login writes its `settings.json` is unmeasured; measure it, then settle the sign-in's mode.

- (doors review, 2026-11-12) When u3-cross and u3-services merge main after u3-doors: they conflict in text on one doors spec sentence, and u3-cross on `platform/tsconfig.json`. Take the sibling side in each. With the doors F1 fix (7ce8da937) all three merges typecheck.


## §J. B13 routes and R6 canary, decided from `research_b13_r6.md` (2026-11-12)

1. **B13:** the doors take `app*` and `api*` in place, as `mcp*` already does, and the old proxy ends with no routes.
   - `identity.json` `routes` = `/mcp*`, `/api*`, `/app*`, `/hooks/inbound/*`, `/hooks/e2b/*`, `/authorize*`, `/oauth/*`, `/.well-known/oauth-*`.
   - Why: a route pattern is matched against the query string too, so `/app` plus `/app/*` misses `/app?…`. Also `x*` beats `x/*`, so the checklist's `api*` split is wrong (`checklist.md:233-234`).
   - `/apple-touch-icon.png` moves off the `app` prefix before `routes`. A site-side test fails on any site file under a doors pattern.
   - The identity guard moves to one shared matcher that checks the path plus the query, against the report's URL list.
2. **R6:**
   - The canary's checks run outside-in on GitHub Actions (`platform-canary.yml`, `deploy/canary.ts`).
   - A Cron Trigger in the doors Worker dispatches it every 10 minutes. GitHub's own schedule stays as the watchdog.
   - Alerts go to a `platform-down` issue plus the Pushover page ladder. The cron pages Pushover directly when the dispatch fails, i.e. when GitHub is down.
   - Logging goes on in all six Worker configs.
   - The manifest disables `uptime-canary.yml` at `routes`.
3. **Decided (small or no spend, mine):**
   - A daily full-run canary on a free-tier model source.
   - One short E2B sandbox boot a day, which costs cents a month.
   - During a Cloudflare-wide outage, detection stays at GitHub's schedule. No third-party prober for now.
4. **Founder steps, once the canary PR is ready** (`founder_batch.md` item 5): a fine-grained GitHub token that may dispatch workflows on this repo, and a free-tier model key for the canary owner.


## §K. B23 and B4, decided from `b23_research.md` (2026-11-12)

1. **The order:**
   - **T0, the kit:** `ci/workers.ts` `auxiliary()` gives the Worker a namespace of its own class, and `ci/gate.ts` holds the gates.
   - **T1, the owner, before B3:**
     - the suite runs on its real entry, with its fakes as Workers bound by config;
     - `Internals`, the 11 test subclasses and every defaulted constructor parameter are deleted;
     - production ceilings, and the one-file starter package by a Vite alias;
     - the memory probe is driven through the doors.
     - (the coordinator, 2026-11-12) The ledger's `limits` parameter is deleted outright, with no test-only parameter or second constructor. The memory probe's `LegSegments` moves to production's ledger limits, as `LegArchive` did, its leg seeded so production's own seal rule makes the segment it measures, with large bodies. The ledger's ceilings come before T1d, since T1d needs T1c's real entry and T1c needs the ledger's ceilings.
     - (the coordinator, 2026-11-12, from T1b3's measurement) At production's seal (`archivePick`) two 4 MiB bodies seal together but an 8 MiB body, the cap, seals alone. So the leg's `bodies` case keeps 4 against 8 MiB as production seals them, and its `rewrite` meets the largest segment production makes: the kept body one byte short of sealing alone, then an 8 MiB one, about 16 MiB, its two records checked. No test-only seal size.
     - (the coordinator, 2026-11-12, T1b4) The measured move meets that largest segment too: the `bodies` case's muse body is the smaller of `mib` MiB and the largest body that does not seal alone, so at 8 MiB the run moves the segment of about 16 MiB, and the run checks loudly that it is one segment of two records.
   - **B3**, on the cleaned constructors.
   - **T2, the doors, after D3:**
     - the doors run on their real entry, with the owner as an auxiliary Worker;
     - the owner gate replaces the 72 SQL statements;
     - no `testRead` door.
   - **T3:** the egress memory leg moves to `gateway/memory/` and keeps its own workerd config.
2. **Time:**
   - The grace and retry tests use a fake `Date` plus `runDurableObjectAlarm`, at production's values.
   - `chainStep` and `betweenPasses` are reproduced by a gate Worker in front of `FILES`.
   - One rule for fakes: never hand a promise from one request to another.
3. **Code mode's compute bound** (343 s against the 286 s large budget) moves to the codemode Worker's own tests (B25).
4. **B4: the door table generates the prototype methods at module load.**
   - The builder refuses the reserved names at module load.
   - The generator still writes `OwnerDoors`, `requests.ts` and the typed handler map.
5. **New production bug, its own PR first (E1):** `SandboxLeases` has no production caller, so nothing marks a sandbox's network applied. Every sandbox model call should then be refused `egress_network_stale`; the tests hide this by calling the hook.
   - Fix: the launcher marks the network applied when the start that carried it is answered `started`.
   - The PR has a test that runs the real launch path with no hook, and a live check at the dark redeploy.
   - This is found by reading the code, so the builder confirms it first with that test failing on main.


## §L. Decided from S1's report (2026-11-12)

1. **`#forget`'s swallowed `unreserve` is a bug: lane S's next PR (S2) fixes it.**
   - Today a failed `unreserve` leaves a `held` row in `SandboxSlots` that never expires, so the line counts a slot for a sandbox that's gone, forever.
   - The fix: never swallow. The release is retried by the object's alarm until it lands.
   - A test fails `unreserve` once, then shows the slot freed after the alarm.
2. **S63's `bootId`: an unreadable boot id never matches,** not even another unreadable one. It fails closed, so a sandbox with an unknown boot is treated as a new boot. This rides S2. (The runner's own unreadable boot id is amended by item 8.)
3. **B7's runner items N23, N30 and N33 go to S2.** No S PR held them (a gap in `d2` §D).
4. **The `chatgpt.json` and `unwired` deletion (§A8) is its own PR.** It reaches the doors, the owner, the gateway tests, the contract guards and the spec.
5. **S8** (S13's `lease_id` half: the contract, the owner's call and the RPC) stays a later S PR.
6. **The host lock** (`heavy.sh`) now takes whichever slot is free, rather than choosing one by pid parity.
7. **S2's three choices beside B15's and B7's letter are taken (S2's review, 2026-11-12):**
   - **`N33`: the route probe's captured-body cap is `EGRESS_BODY_MAX`, not `CHUNK_BYTES`.** The cap belongs to the probe's fake gateway, which judges a model call's body as the egress door and the gateway read it; the files plane's chunk size has no reason to move it.
   - **`template/checks.ts` reads `COMMAND` and `EXIT` from `protocol.ts`, not from `protocol.json`.** It drives the real runner, which dispatches from `protocol.ts`, and a test holds `protocol.ts` to the frozen record.
   - **`startDocument` passes `config.egress` whole.** Mapping its two keys would fix one key order and change the document's bytes against main's; the policy's validator holds it to `mode` and `destinations`, and the Worker's test holds the nested shape to the record.
8. **Amends item 2 (S2's review, 2026-11-12, Fact 8): a runner that cannot read its own boot id throws.** It takes no lock, so it never takes over a live holder's. Under the old wording such a runner took over every lock, live holders' included, and exclusion was lost within the boot. A lock that names no boot, or another boot, is still taken over.
9. **Follow-up, after S2: a stale owed slot can let a renewal's fresh hold go.**
   - It needs a failure of the line first: a `#resume` whose give-back the line refused leaves its sandbox's slot owed while the sandbox runs on.
   - If a later look holds that same sandbox's slot again (`reserve`, its resume refused for room), the give-back after the look lets that hold go.
   - The fix: where a slot is reserved again (`#lookAt`, `#renew`), its owed row is deleted first, with a test that refuses one give-back, then has the next look's resume refused for room and shows the slot still held.


## §M. E1–E3, the sandbox's network (2026-11-12)

1. **E1 (`fix/e1-network-applied`, 6c201dec4).**
   - A lease's network generation is set in `Launcher.openLease`, at dispatch, for runs and sign-ins.
   - The order is proven on every start path (`control.ts`): the network is applied before anything can make a model call.
   - Review next, then the PR.
2. **E2: re-applying a live lease's network after a policy write, built as `e2_research.md` recommends.**
   - A new `SANDBOX.network(...)` applies E2B's whole-network update to a live sandbox. It keeps the live egress token, read back from E2B, and the live sign-in jobs' vendor hosts, which the owner hands over.
   - The `runs` lane drives it: any live lease behind the current generation gets a re-apply, and failures retry there. The lane is also kicked after a policy write's unit commits.
   - The lease gets a second, in-flight generation, set at dispatch. Egress admits a call when the confirmed or the in-flight generation is current; a failure clears the in-flight one.
   - The owner's unused `stale` and `network` lease calls are deleted.
   - It goes after E1 and E3.
3. **E3: the VM's lease file holds only the two tokens the runner reads.**
   - The egress token never enters the VM (`control.ts:743-746`), as `network.ts` says.
   - A test fails if the lease file holds the egress token.
   - It is its own small PR, before E2.
   - E3 review addition: drop the now-dead `SandboxStart.tokens.egress_token` (`contract/src/sandbox.ts:89`, filled at `owner/src/launch.ts:722`) in E2. E3 live check reads a lease opened after the redeploy.
   - E1 review additions for E2: (a) the stale "until the sandbox slice applies it again" wording in `owner/src/egress.ts:22` and the owner and doors specs; (b) a race: a joining job that runs before the opener has its vendor sign-in hosts dropped by the opener's full network replace (liveness, not a leak). E2's re-apply must carry the live sign-in jobs' hosts, which closes it.

§M has no items 4 and 5.

6. **T0 review (2026-11-12):** the binding pass-through in `auxiliary()` (the config's KV, queue, service and var bindings) is its own small kit PR (T0b) before T2. It makes `FILES_CIPHER` required in `files/src/index.ts`, because its `"ssec"` default exists only because auxiliaries never got the config's vars. It is proved with the same main-against-head comparison of the auxiliaries. Nits for T1: `Control.arm` refuses an arming that never acts; `held()` waits less than the test budget; and the gate uses the contract's reserved-name list once B4 adds it.
7. **T0b review (2026-11-12): no suite may read a real secret from the host environment.** (Filed first under §N as a second "7."; moved here 2026-11-12, so §N7 is only the imported defaults.)
   - wrangler's config-to-miniflare conversion fills `secrets.required` from `process.env`. So a suite built through `wrangler.configPath` (each workspace's `main`, and the doors browser test) picks up any production secret the host environment holds. One E2B key was printed that way.
   - The fix goes in T2, or in a small kit PR before it: the kit builds every `main` itself, with each required secret stated as a test value. A guard fails any suite whose built options carry a value taken from `process.env`.
   - The founder rotates the E2B key (`founder_batch.md` item 0).
8. **E2 built (b483632ea).** The departure is accepted as better: the live sign-in jobs' vendor hosts come from the sandbox Worker's own per-job records, not from a list the owner hands over. Only the Worker sees the order a lease's starts land in, so only it can close the joiner race, and one source is simpler than two.


## §N. R5, where an action is decided: decided from `r5_research.md` (2026-11-12)

1. **One rule list.**
   - The old tables and `spend` are replaced by one owner `actions` table, empty by default (Fact 15).
   - A contract file lists which places can see each kind of action.
2. **One decision.**
   - A single function in the owner object decides.
   - It is called by the door step and by one small hook helper.
   - The places are:
     - our MCP tools;
     - the owner-only doors;
     - file sync;
     - the model-call and network door;
     - each sandbox CLI's own pre-tool hook.
   - The hook configs live in root-owned paths. A rule written in the agent's own files is advisory only.
3. **Engine differences are data.** Each source record carries a `decide` block:
   - where the hook config goes;
   - its field paths and output templates;
   - whether the engine fails open or closed;
   - whether it can defer.
   - A start check before each run closes most of Codex's and Gemini's fail-open when a hook can't start.
4. **Asks.**
   - An ask is a new "action request" held card plus a one-time approval.
   - The answer comes back as a resume of the same conversation, where the engine can defer (Claude Code `defer`).
   - Where it can't, the call is refused with the card's id.
5. **The honest note.** The owner sees, for each rule, where it holds and where it can't. A rule nothing can decide is refused when it is written.
6. **Deleted:**
   - `spend`;
   - `commons.publish`;
   - egress `ask`, which is an allowlist plus a receipt, not an ask;
   - the connection-keyed tables.
   - Imported rules are kept as an archived file, with one notice per owner.
7. **Imported owners' defaults: (a), decided by me.**
   - An owner who never set a rule had the old defaults in force: ask first for messaging, sharing, browsing and spending; hand off for money, security and access.
   - The importer writes those into that owner's `actions` table explicitly, so nothing they relied on loosens.
   - They are the owner's own settings, which Fact 15 allows, and the owner can change them.
   - The notice's wording is outward-facing, so it goes to the founder (`founder_batch.md` item 6).
8. **Build slices, after B25 and alongside lane O:** the report's six. None adds spending.
   The honest note (§N5, `policyRead`'s `holds_at` and `not_enforced`), in no slice's list,
   rides slice 4 (P-hooks), once the engine records say where a hook holds.
9. **Live checks owed at the dark redeploy:**
   - Claude `defer` with several tool calls in one turn;
   - managed settings against an org's server-managed settings;
   - Codex, Gemini and pi loading our root-owned hooks when unattended;
   - the delay one decision adds;
   - a census of non-default rules, spend caps and egress `ask` in the import.


## §O. The cross-PR re-check (`list4_recheck.md`, 2026-11-12), decided

1. **E2 is fixed before it merges.** Its reviewer folds in items 1–3, 14, the swallowed error, the copied receipt block, `sandboxBound()`, and the `egress.ts:71` line:
   - one lane-call constant;
   - one contract reader and writer for the egress token in E2B's network document;
   - the retry records its generation, so a policy write only wakes the lane;
   - `leaseAcquire` sets the generation, and `leaseNetwork` goes.
2. **B25 is fixed before it merges.**
   - The `retired` step checks a Worker's existence through the Workers API, not its secrets (B11).
   - The deploy kit's `retired` list is the only remover of platform Workers; B24's manifest names entries in it.
   - The test holds behaviour, not the list.
3. **The order is B25, then T0b.** T0b merges main after B25. It renames its `hooks` auxiliary and kit-test entries to `codemode`, and wraps B25's codemode pool in `pool(...)`.
4. **T1 takes item 6.** It deletes `SandboxLeases` whole (no production caller; `close` is a third copy of release-and-settle) and the in-isolate sandbox fake. The tests then go through the real doors, with a config-bound fake Worker and gates.
5. **Log corrections** (`d2`; applied in `d2` itself when the logs moved into the repo, 2026-11-12):
   - Its "Already done by u3" list is wrong on four items. Two have no owner:
     - **R5′** (`MISTRAL` is a near-copy of `COMPLETIONS`, `contract/src/api-rules.ts:227`, `v2:G-H13`; plus G-H11 and G-SP5) goes to lane G's first PR.
     - **P-H1** (`okf-watch apply` runs only `bundle.mjs` and leaves the plugins stale, release-critical), with P-H2 and P-T2, goes to B9. `okf-watch apply` runs `npm run generate`, and the workflow adds `platform/plugins`.
   - B22's idle cut lives in `gateway/src/forward.ts` (`IDLE_CUT_MS`). Its only reader is the gateway, so that is the right home by B6. Logged.
   - B13's `/app` plus `/app/*` text is superseded by §J1 (`app*`).
6. **Lane C's first PR adds §G14's tools row.** It keeps `vitest.config.ts` files, which import `ci/`, apart from build tools, which must not. (C2 left it; C2b adds it, `ci/kit/layers.ts` "a workspace's tools".)
7. **R4 (the `tasks.md` re-cut) goes next, before more lanes edit `tasks.md`.** The file is 244 KB, and each PR adds history lines to it.
8. **The small items** (15, 16) ride the PR that next touches each file:
   - the `cipher.ts` SSE-C premise goes with lane R's cipher PR;
   - ADR-032's `HOOKS` gets a dated pointer;
   - the config literals in `sandbox/vitest.config.ts` are read from `wrangler.jsonc`.

## §P. Data shapes before real data (list3 P48, raised by R4, 2026-11-12)

Every known-wrong data shape is fixed before the cutover imports real data: a fix is free on a dark platform and a live migration after it. So none is deferred to "live migration later". Each one names its lane PR; R4's §10 step 20 points here.

**Decided from `datashapes_research.md`** (the coordinator, 2026-11-12; the research adopted whole, in its order):
1. **Receipts:** each fact moves onto the recipient row and `ledger_receipts` is deleted. After T1b4.
   Built (o-receipts, owner migration 9) with `read_at` and `failed_reason` on the recipient row, and without the research's `replied_at` and `reply_id`: a reply's sender is always its parent's recipient (`ledger.ts` `#addressed`), so the reply message is the reply fact, and the parent's row already says `replied` at `changed_at`. Two more columns would copy it. The reply's row holds its `reply_s` from the post (review: a time read from the parent was lost once the parent archived, ADR-036).
2. **Rooms:** a `rooms` table and room members, with opaque room ids. Lane O, after T1c and before S4.
3. **Collection history**, in the order `history_keys_research.md` adopted (it answered the
   first build's four findings, stopped under Fact 14):
   - H1, the history keys: merged (#5538, owner migration 7);
   - S1, the stream extraction into `owner/src/stream.ts`: the segment and lane code out of the
     ledger, no behaviour change, and a migration widening the segment tables' stream checks.
     After T1b4, receipts and rooms;
   - H2, the erasures stream;
   - H3, the history stream. It owes §3.4's check: a deletion can cost more than its row (a
     2,048-byte caller name deleting a 1-byte writer's row charges 4,096 against the row's 743,
     H1's body), so H3 bounds the deleter's name or takes the factor into the guard's term.
   Items 4 and F2 build on S1's module.
4. **Conversations:** ids carry their order key, and idle conversations move to segments. On S1's module.
5. **Files:**
   - F1: retention off by default, one recorded byte ceiling, `#fit`'s silent prune deleted, and the latency probe's workflow, scripts and branch deleted. After R5's P-shape.
   - F2: file versions move to segments, with mark-and-sweep. After R1 and item 3.
   - F3: the live index's R2 level, later. Whether F3 comes before the cutover is still an open founder question; the recommendation is no.
6. **Deliberate retentions**, each recorded with its reason in `limits-audit.md` unless a reader needs older facts:
   - `RUNS_KEPT`: a settled run's results live on in its mail reply and its transcript, so the row is only a receipt;
   - the policy and egress receipts: a recent audit window, while each decision's effect lives in the policy document and the run;
   - the settled wakes' week and 1,000: delivery telemetry for debugging the doorbell, not the owner's data.


## §Q. The questions buried in `tasks.md` (from R4's review, 2026-11-12)

**Decided by me** (no spending, nothing irreversible, nothing outward-facing):
1. **"Attended" means a live caller is waiting for the answer.** `converse` is attended and a client's mail is unattended. The rules key on that, never on the client.
2. **Chain-depth stop.** An off-by-default owner setting stops agent-to-agent mail past a chain depth (Fact 15). It rides lane O.
3. **No separate automatic reviewer for the OKF watcher.** The one review gate (§B6) reviews its PRs like any other.
4. **Route deletion.** Deleting `deploy-worker.yml`, in place of the old R1 route guard, is part of §J1's routes decision.

**The founder's** (`founder_batch.md` item 7; none is needed before the cutover):
- the Codex, Gemini and Kimi sign-in switches (vendor terms);
- capture always on: new recurring spend, about $62 a month for a heavy owner;
- non-model credentials through a TLS proxy: needs a host, against Direction 8;
- the directory listing's own MCP door: Direction 2, and the directory submission.

## §R. B9's Env half (2026-11-12)

C2 built B9's `npm run generate` and its staleness guard, and stopped at the `Env` half: d2 B9's design can't be built as written. C2 measured this against wrangler 4.154.0:
- **The six generated files can't all be compiled.** `wrangler types --env-interface <Worker>Env` still writes the global augmentations: `Cloudflare.Env`, `Cloudflare.GlobalProps`, `StringifyValues` and, for a Worker with string vars, `NodeJS.ProcessEnv`. No flag drops only those. With all six included, `tsc` gives 62 errors: `process.env` requires every Worker's string vars, so every `execFileSync(…, { env })` in Node code fails, and `Cloudflare.Env` merges all six Workers' bindings.
- **Wrangler types a binding to another Worker by its implementation, never by its contract.** Given only the Worker's own config, the binding is `Fetcher` or an untyped `Service`. Given the bound Worker's config too, it is `Service<typeof import("../gateway/src/index").default>` or `DurableObjectNamespace<import("../owner/src/index").OwnerDO>`, an edge into another Worker's `src`.
- Strict vars also type deploy-time values as the config's placeholders (`OWNER_ISSUER: "not-configured"`).

**Decided: option (a).**
- `ci/kit/types.ts` writes each `<Worker>Env` from wrangler's binding list.
- A binding to another Worker is typed by its contract interface, through a table in the contract.
- The output has no global augmentation.
- The hand `Env` interfaces and `envProblems` are deleted.
- `ERASURES` becomes required in the same change. `SANDBOX` already is: T1a made it required (#5515), so only `ERASURES` was still optional (`owner/src/owner-do.ts:282` at C2).

**Not (b), one tsconfig per Worker:** it types bindings by implementation, which the layer table refuses.

**Not (c), keeping wrangler's files excluded beside the hand interfaces:** it keeps hand copies held only by a name check, the hidden link the untangle removes.

It builds as its own PR, **C2b**, right after C2.

**As built (C2b):**
- Each Worker's env is `<worker>/src/env.ts`. Wrangler's `worker-configuration.d.ts` is no longer committed.
- The table is `contract/services.json`. It also types the queues' messages, with `OwnerMessage` (`contract/src/queues.ts`) for the ring and the erasure. The services guard reads the same table in place of its own list.
- The guards that hold a binding to its one module now take the generated env as that binding's declaration (`ci/kit/sources.ts` `envFile`).
- Every test env but the doors' `OWNER` is a `Pick` of its Worker's generated env. The doors' tests still call owner doors outside `OwnerDoors`, so that binding stays typed from `OwnerDO`, owed to T2 (B23).
- `SANDBOX` is required, as T1a made it. `ERASURES` is required in the type because the config binds it; the code's check for an unbound one goes with T1c.

## §S. C4 as built (2026-11-14)

1. **B6's egress door grammars are one reader, `egressDoor(path)` (`contract/src/egress.ts`), not regexes built from the ids.** The module-state guard refuses a regex a call builds at load, so the reader holds each segment to its own grammar in `ids.ts` (`RUN_ID`, `HEX64`, `CONNECTION_ID`, `SIGNIN_ID`) and returns the door it names. No regex copy is left, and `contract/guards/wire.test.ts` reads back every URL the contract makes. The sync agent's door names are `SYNC_DOORS`, and the runner's sync client takes a `SyncDoor`.
2. **Corrects `d2` B19:** `REMEMBERED_NAME_BYTES` and `automatonStates` stay in `contract/src/sync-ignore.ts`. `ignoredBy` reads the first, and the second measures the matcher's private automaton for its own tests. Only `SANDBOX_HEAP`, `AGENT_PEAK` and `HEAP_PER_LIST_BYTE` move to `sandbox/runner/sizing.ts` (S4).

## §T. D1 as built (2026-11-14)

1. **B7's kit text guard goes to lane C after C5.** It is one `ci/kit` guard (no SHA-256 outside `contract/src/digest.ts` in a Worker's `src` or the runner; no `Date.now()` in `owner/src` outside `clock.ts`) with an `owed` ratchet, and `ci/kit/owed.ts` is lane C's, with C5 in flight. The PR that adds it lists each lane's entries; after D1 the doors owe it none.
2. **The cookie reader stays in `session.ts`, not the doors' kit** (a departure from B7's list, accepted as better): the binder guard holds that only `session.ts` reads the `Cookie` header, so one private `cookies(request, name)` serves the session and connect cookies.
