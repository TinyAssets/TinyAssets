# T1c as built (2026-11-14)

T1c is split at its natural seams, in three PRs (T1c-a, T1c-b, T1c-c), each green on its own (B23's "about four PRs" for T1).

## T1c-a: the gates

1. **What T1c-a builds:** the `FILES` and `GATEWAY` gates in the owner suite (`ci/gate.ts`, as `SANDBOX`'s and the `SLOTS` gate are), and the five test classes and two archive hooks they replace:
   - `GatedOwnerDO` (`watched`, `faulted`, `GatewayCall`, `GatewayFaults`), `RungOwnerDO`, `ArchivingOwnerDO` (`ChainHold`), `DivergingOwnerDO` and `PurgingOwnerDO`, with their namespaces;
   - `Internals.chainStep` and `Internals.archiveBetweenPasses`, with `ChainStep`, `ArchiveMoveSeen` and the archive's two parts.

   It also builds the owner suite's lease clock under a fake `Date` (list 6, item 6j) and the object's `admitted` held by a test.
2. **What T1c-b builds, from `main` once T1c-a merges** (as planned; T1c-b's item 6 splits the real entry, the starter and `BackupParts` out of it): the real entry. That is `main: src/index.ts` and `Internals` deleted with the classes left (`OwnerDO`'s null starter, `SeededOwnerDO`, `FauxOwnerDO`, `SwitchedOwnerDO`, `CachedOwnerDO`), `test/worker.ts`, `test/namespaces.ts` and `test/admitted.ts`. It covers:
   - the erasure and the control record under a fake `Date`;
   - the unbound-`ERASURES` throw and `RecordingErasures`, the queue consumed by a fake Worker;
   - the one-file starter by a Vite alias;
   - the rig admitting by the door;
   - the doors' entry with no third constructor argument;
   - `BackupParts`' `sizes?` and `pool?`.
3. **A service gate names each call by its first argument when it is a string** (`ci/gate-worker.ts`). This extends T0's gate, and its "a name on a service gate is refused" case goes.
   - Every service the gates front takes its owner, or the owner's namespace, first.
   - Without a name, an arming meets the first matching call of any owner. The archive lanes of a file's earlier owners still run on their alarms, so that call need not be the test's own.
   - `owner/test/support.ts` `gateFor(gate, name)` arms, waits for, releases and reads back one name's calls. `spaceOf` gives an owner's files namespace, the `FILES` gate's name.
4. **An arming holds the next matching call, not every call until cleared** (the old promise gates did that). A test that lets a call go waits until it is held first (`held`), and sends it if the runtime has not.
5. **The chain-read tests (N9, N10) name the chain by one of its hot messages**, so no root read from a segment comes before the fences. The `FILES` gate holds the read's first archived read:
   - before it is sent: past the fences unit, for N10;
   - after its answer: before the hot unit, for N9.

   N9's chain now has an archived part (its first four moved), because a chain that is all hot makes no outside call to hold (`b23_research.md` question 2).
6. **The purge's bounds are read at the `FILES` gate** (`archive-mail.test.ts` `watchMoves`), in place of the `test_moves` table.
   - Every move uploads its segments before the unit that names them. The gate holds each upload of the owner's namespace while the move's row (`archive_moves`) and the fences are read.
   - The fences' change from one move's first upload to the next one's is the first move's commit: the segments it replaced and the records it wrote.
7. **The diverging move is a `putChunks` changed in flight** (`corrupt`): R2 refuses it by the chunk's hash, as research question 2 measured.
8. **A refresh answered already past its expiry** is a real answer, not a changed one. The gate cannot change an answer, and a fake `Date` past the fake's half-hour token would also lapse the sandbox lease (60 s).
   - The gateway's fake upstream answers the refresh token `rt-lapsed` with a one-second token. That is one line in `gateway/test/fake-upstream.ts`, lane V's file.
   - The test holds the answer at the gate while the object's clock passes the second.
9. **The lease clock** is `runs-support.ts` `later(seconds)`, a fake `Date` (a lease's life and one by default), in place of `UPDATE sandbox_leases SET lease_expires = 1`, in every test that moved it: the owner suite's `egress`, `runs`, `runs-authority`, `files-lease` and `storage-full`, and the doors' `egress`. The heartbeat test moves the clock 30 s and sees the lease renewed from there.
   - The `SANDBOX` gate's armings name their owner too (`whileOut(owner, …)`, `gateFor`), so no other owner's lane meets them.
10. **`admitted` held by a test** (`doorbell.test.ts`, "claims nothing more once the owner is suspended between a pass's claims"):
    - Two wakes are due in one pass. The `GATEWAY` gate holds the first ring, and the owner is suspended there.
    - The second wake stays pending and unclaimed, and is never rung.
    - It fails when the object hands the ledger `() => true`. No test failed on that before (T1b4's R1).

## T1c-b: the erasure, the control record and the rig

1. **What T1c-b builds** (`tasks.md` holds it as T1c-b):
   - `Internals.erasure`, `eraseRetryMs`, `putGraceMs` and `controlFreshMs` are deleted. `Erasure` reads `PUT_GRACE_MS` and `ERASE_RETRY_MS`, and `ControlGate` reads `CONTROL_FRESH_MS`; neither takes them as parts or parameters.
   - `FauxOwnerDO`, `SwitchedOwnerDO` and `CachedOwnerDO` are deleted, with their namespaces, `test/admitted.ts` and `test/erasure-fake.ts` (`RecordingErasures`).
   - The unbound-`ERASURES` throw is gone. `ERASURES` is the config's own queue in the owner suite, consumed by a fake Worker (`test/fake-erasures.ts`, as the sandbox suite's ring is), which records each owner's messages for a test to read through `ERASURES_SENT`.
   - The doors' entry passes no third constructor argument, and its `erasures` field goes. Its owner object sends to a queue the same fake consumes (`owner-erasures-recorded`), not `tinyassets-erasure`: the doors' config makes the doors Worker that queue's consumer, and the doors' tests run that consumer by hand on what was sent.
2. **The erasure runs through its doors and its own job** (`test/erasure-support.ts`, research question 2):
   - begun at `ownerErase`;
   - its outside record confirmed by the test, as the doors confirm it, once the queue took the object's message (`erasureOutsideDone`);
   - its put grace (a day) and retry interval (a minute) passed on the object's clock by a fake `Date` (`later`);
   - its job run by the alarm.

   A crash's state is made by the erasure itself, not by SQL rows: step 1 is its sandboxes' erase refused at the `SANDBOX` gate, and step 2 is its wait for the grace. One SQL write is left, the lost job (`DELETE FROM cf_agents_jobs`) of "resumes at the object's start alone", because that test is about a job that is gone.
3. **The control record is the config's own `CONTROL`, written by the test** (`code-door.test.ts`).
   - A script's second call is held at the `FILES` gate (its body placed in R2) while outside access turns off.
   - With the object's clock moved past `CONTROL_FRESH_MS`, the calls after it read the record again and are refused.
   - With the clock still, the script's one read serves all five.

   The read counter (`switching`) goes: the tests hold the behaviour, not the count.
4. **The rig admits by the door** (`support.ts` `admittedOwner`): every rig, `ownerObject` (now async) and `gated` send the platform's admit and await it before the test's first call. `get(idFromName())` stays the way to a pending object.
5. **`ERASE_CONFIRM` is exported** (`erasure.ts`), so a test erases with the phrase the door checks, not a copy of it.
6. **Split out of T1c-b, each with its evidence:**
   - **T1c-b2, the real entry and the starter: decide first.** The one-file starter by a Vite alias cannot stand beside the starter tests as they are.
     - `starter.test.ts` and `starter-review.test.ts` run a `Starter` built by hand over an owner object's storage at 47 call sites, each with a package of the test's own (`V1`, `V2`, …), which needs an object that carries no package (`starter: null`).
     - With the null path gone, every object seeds its own package once a door hands it tiers. Its `starter_state` version then stands at or above the test packages' version 1, so a hand-built `V1` pass does nothing, and its receipts sit beside the test package's.
     - A design is owed before it is built. The one this PR suggests: a new owner's seed is tested on the object's own seed (the carried package, with no alias, in the starter tests' project); updates and reviews are hand-built passes at versions above it, run once that seed has settled; then `Internals`, `test/worker.ts`, `test/namespaces.ts` and `SEEDED` go, and `main` is `src/index.ts`.
   - **`BackupParts`' `sizes?` and `pool?`: research first, with T1c-c.** The generation harness (`generation-support.ts` `generate`) builds `Backups` by hand, a second copy of the object's wiring, at sizes 64 to 128 times smaller than production's (`SMALL`: 16, 32 and 64 KiB against 1, 4 and 8 MiB) and with a pool of its own. Its multi-burst fixtures would grow as much at production's sizes (1,600 notices would become about 200,000). It is decided with T1c-c's one wiring function, which can build the backups lane as the object does.

## T1c-b2: the real entry and the starter

1. **Decided (the coordinator, 2026-11-14), superseding the one-file alias and the `seeded` project:** the whole owner suite runs on the real carried package, as production does.
   - The 42 tests the one-file starter broke need the same changes under the alias as without it.
   - The alias is a test seam that `ci/guards/isolation.test.ts` rightly refuses.
   - So the kit change for a second project (nested excludes, the deepest-folder match in `budgets.mjs`, an `owner/seeded` budget row, the guard's edit) is backed out as surplus. The starter tests stay in `owner/test/`.
2. **Decided (the coordinator, 2026-11-14), kept from the earlier decision:**
   - A new owner's seed is tested on the object's own seed.
   - Updates and reviews are hand-built passes at versions derived from the carried one (`SEED + 1`, `SEED + 2`, …), run once that seed has settled.
3. **One rig helper waits for the seed** (`support.ts` `seeded`, the `starterReceipts` door at `version === carried`). Every rig and `ownerObject` await it before the test's first call, which ends the alarm race. `unseededRig` is the starter's own tests' way to an owner before its seed.
4. **Tests that count files, bytes or manifest pages** derive the seeded part from `carried()`, with no literal for the starter's size. A test that means "nothing but what I wrote" asserts its own paths plus the starter's, or filters to its own.
5. **As built:**
   - `main` is the config's own, `src/index.ts`, with one namespace, `OWNER`, on the real `OwnerDO`.
   - `Internals`, `Starter`'s null package, `test/worker.ts`, `test/namespaces.ts` and `SEEDED` are deleted. `StarterDeps.ready` is required.
6. **Only a failed first seed lets the owner's own file come first.** An owner's file doors wait for its seed, and the import writes only the imported tree. So a test of the owner's file there before its seed writes it after a first seed failed part way, the files plane refusing the seed's upload at the `FILES` gate (`starter-support.ts` `failedOwner`).
7. **The tests that were not the starter's** move to `run-schedules.test.ts` and `file-readers.test.ts`. The profile's move and the goals' arrival are rewritten on paths the carried package does not seed; what each holds is unchanged.
8. **`starterSeed()` is the one helper for the seeded part** (`support.ts`): the seed's files, directories, entries (each one file version, so a test's own come after them), bytes inline and placed, content ids, chunks and the owner's agents it defines; `mine(items, nameOf)` keeps a test's own paths, contents or chunks. Quota and storage-full tests count the seed's bytes as any file's: each tier figure is the seed's bytes and the test's room. One figure is read from the object instead: the owner's index bytes in `files-share.test.ts`, since deriving them would copy the manifest's byte formula.
9. **An owner before any door bound it** is `unseededObject` (beside `unseededRig`): for what a first call binds (`owner.test.ts`), a never-seen owner's egress (`egress.test.ts`) and the schedule's probe, whose object then has no lane job of its own.
10. **The gate answers with the answer's own fields** (`ci/gate-worker.ts`). Handed on as it was, the real call's RPC result kept the calling object referenced, so every owner that had put a chunk through the `FILES` gate (every seeded owner) could not be evicted: `owner.test.ts` and `wakes.test.ts` waited out 38 s timeouts. A direct call never holds one.
11. **`windowEnd` lives in `bodies.ts`** (a product bug, found here): the pages, bodies and search modules' import cycle made esbuild, as wrangler bundles the owner, set `SEARCH_ANSWER_BYTES` from `PAGE_BYTES` before `PAGE_BYTES` was set, so it was `NaN` in production and a search never paged by its answer's bytes.
12. **Measured** through `heavy.sh`: the owner workerd suite 242 s wall against main's 272 s; `owner.test.ts` 128 s to 12 s, `wakes.test.ts` 104 s to 14 s.

## T1c-c: one wiring of the object's stores

1. **The object's one wiring function is `ownerStores(units, env, id, changed)`** (`owner-do.ts`). It builds the stores from the object's gate's units, its env and its id: the policy, notices, change log, files, placed bodies, collections, grants, ledger and nightly manifest (`Backups`). The object's constructor calls it once.
   - The ledger alone cannot be wired apart. Its parts are the files' adoption and wall, and the files' garbage protection reads the manifest (`backups.manifests()`), so one function builds both lanes and nothing is late-bound.
   - `changed` is the one input only the object holds: `fileChanged` over its whole state (its runs and a sync's held-back count among it). A test passes `NO_FILE_CHANGE`, which throws: the ledger and the manifest's lane change no file.
2. **The tests call that function** (`ledger-support.ts` `storesOn(state)`), over a write gate of their own, in place of `ledgerParts` and `ledgerOn`.
   - `inLedger` runs its act in one unit of that gate, as a door runs the object's ledger, so no test writes the ledger outside a unit. The tests that reach it directly write in its units too. Their `runWakes` takes that gate's `write`, not a second gate's.
   - `ledgerOn`'s `Ledger` subclass, which admitted a client inside `register`, is gone. `registerOn(stores, origin, request)` admits the client and then registers, as the door does.
   - The erasure test of a claim held while the owner is not admitted suspends the owner by the platform's door, in place of a ledger built with `() => false`.
   - `ballast` seeds through the ledger's own API, in the one unit of `inLedger`: up to 50,000 posts, no door and no SQL.
3. **`BackupParts`' `sizes?` and `pool?` are deleted, with `GenerationSizes.most` and `#page`'s size parameter.** Every lane cuts at `GENERATION_SIZES` and takes the isolate's pool (`ownerSlots`). The generation harness (`generation-support.ts` `generate`) builds each pass's lane with `ownerStores`.
   - Its outside calls are counted and failed through the env it hands the function (`FILES` wrapped, `through`), and its units noted through the `quiet` it hands it.
   - The owner's name, `serves`, `halted`, the schema and the notices are the object's own:
     - the migration test records the generation begun under the code before the last migration (its row's `schema`);
     - the erasure test marks the owner erasing in a unit during the ship's put;
     - a generation's notices are read from `owner_notices`.
4. **Research (the coordinator's two candidates).** Measured on `export.test.ts`, every tag, through `heavy.sh`:
   - **Production sizes, fixtures derived from them, chosen.** `generation-support.ts` `PAGE` and `BURST` are the notices that fill a page and a burst (`GENERATION_SIZES`). Each test asks for the boundaries it crosses, for example a burst and three pages for the cursor test, three bursts for the 5,000-file sync, with 125 files a step, not 25. The file's tests took 37.7 s, against main's 46.4 s at 16, 32 and 64 KiB: the crash-point test 17.7 s against 19.0 s, the sync 6.1 s against 10.0 s. A 29 MB generation at production sizes takes about 2 s, so the 64-to-128-fold growth costs little. The fixtures grow by the bursts a test crosses, not by the ratio: two bursts, not 25.
   - **Sizes from the env: not built.** The production config would carry a var only tests change, which is the test-only product parameter the untangle rule refuses. One suite env would also have to hold both sizes, the production-size tests' and everyone else's, so it needs a second project. Its suite time is main's, 46.4 s.
   - The changelog test of a connection's widest row crosses a burst (`BURST + PAGE`), so its refreshes land before E, as its 200 small-size notices did.
5. **The line past any part is untested now** (`MANIFEST_LINE`). At the schema's bounds no line passes a chunk: the widest row image is `IMAGE_MAX`, 1.5 MiB, and the owner-bound guard holds it. Its test needed a part size only it passed (`most`). The branch stays as the loud backstop for a later wider column.
6. **Mutations, each on the object's wiring, which no test reached before:**
   - the ledger handed `() => true` for its admission fails "holds the wakes lane's claims while the owner is not admitted";
   - the manifest's `halted` without the erasure mark fails "begins no burst once the owner's erasure begins while the log ships".

## T1d: the memory probe on the object's own wiring

1. **What T1d builds:** the owner memory probe (`memory/probe.ts`) builds nothing the object builds. `LegArchive#ledger`, `legFiles`, the leg's own `Bodies` and the two hand-built `Backups` (`legBurst`, `legShip`) are deleted.
   - Every case drives the real `OwnerDO` through its doors, as before: edit, append, the import check, the rest reading, and every ring of the archive lane.
   - Where a case reaches a store directly, it reaches the object's own wiring, `ownerStores`, over a gate of the leg's own on the object's storage, as `ledger-support.ts` `storesOn` does. Those cases are the placed bodies of fetch and window, the archive cases' ledger set-up, and the manifest's lane of burst and ship.
   - The object's env is the leg's, with `FILES` the checkpointed store (`received`), so the stores' chunk calls checkpoint in the owner isolate as before.
2. **Picked by what each leg measures**, so each measures the same thing:
   - Fetch and window measure `Bodies.fetch` and `Bodies.windows` alone, so they stay direct, on the object's bodies. A door would add its answer's own bytes.
   - Burst and ship measure the manifest's lane's run at production sizes, so they stay direct, on the object's `backups`.
     - Their outside calls are steered through the env handed to `ownerStores`, as the generation harness steers them (`generation-support.ts` `generate`). Each part and segment is a checkpoint, the burst's third part's put writes, and the roots are answered in the owner isolate with nothing kept (`ROOTLESS`).
     - The ship case's halt is now the object's own `halted`. The owner's erasure is marked in a unit during the second segment's put (`markErasing`, as `export.test.ts` does), in place of a `halted` of the leg's own (`segments >= 2`).
     - The lane takes a failed put as its pass's failure, so the case now throws if any part was put.
   - The archive cases' mail is set up in the object's own ledger: registered, posted, acked and removed, each act one unit of the leg's gate, as a door's is (T1c-c's `ballast` rule: no door seeding, no bulk SQL). The rest goes through the doors, as before: the lane is rung by an owner's post and its passes run on the object's own alarm.
3. **One leg class.** `LegArchive` differed from `LegOwner` only in its ledger copy, so the two merge. The `ARCHIVE` namespace and binding go from `leg.mjs`, and an archive case runs on the object its setup admitted.
4. **Every case's object waits for its seed** (`object`: the `starterReceipts` door, as `support.ts` `seeded` does).
   - The manifest's lane serves only a bound owner (`object_owner`), and the admit binds none.
   - So the burst and ship need a door first, and a door hands the tiers, which starts the seed.
   - Before, an archive case's first door was its first ring or post, so the seed's uploads could share the isolate with the measured pass.
5. **`NO_FILE_CHANGE` lives in `test/stores.ts`**, with no `cloudflare:test` import, so the tests' stores and the leg's share it.
6. **Measured** through `heavy.sh`, the memory leg before (main `eb752ef49`) and after. Every check passed both times, and every case's answer is the same: the burst's widest segment 4,193,206 bytes, the ship's 4,194,289, the tail seal's 8,072 records, the merge's 30,988, both bodies cases' 2, the rewrite's 1.

   | case (MiB) | before | after |
   |---|---|---|
   | baseline, the isolate at rest | 6.99 | 7.13 |
   | fetch 4 / 8 | 4.01 / 8.01 | 4.01 / 8.01 |
   | window 8, worst; 8 − 2, least | 0.06; −0.01 | 0.06; 0.00 |
   | burst, peak (BURST_UNITS 24) | 17.99 | 17.99 |
   | ship, peak (SHIP_UNITS 16) | 9.21 | 9.20 |
   | burst's ships − a ship alone | 0.08 | 0.08 |
   | merge, peak; at its passes' ends | 13.21; 4.73 | 13.21; 4.72 |
   | tail seal, peak; at its ends | 5.29; 1.29 | 5.30; 1.29 |
   | bodies 8, worst; 8 − 4, least | 8.04; 0.04 | 8.05; −0.01 |
   | rewrite 8 | 8.04 | 8.04 |
   | edit 192, worst; 192 − 96, least | 8.03; 0.01 | 8.04; 0.00 |
   | append 32, worst | 16.02 | 16.02 |

   - The baseline is 0.14 MiB more: the object's seed now lands before the reading, and the leg's stores of the object are built beside it. It is within `OWNER_BASELINE_BYTES` (8 MiB).
   - The leg took 3 min 6 s, against main's 3 min 5 s.
7. **Mutation, on the object's wiring, which the leg never reached before:** `ownerStores`' `halted` without the erasure mark fails the ship case ("the ship case began a burst"). Before the throw was added, the same mutant passed every check, its ship's peak moving from 9.2 to 11.35 MiB.

## Owed

- **`BackupParts`' `sizes?` and `pool?`**, with T1c-c, as T1c-b's item 6 says. Built in T1c-c (above).
- **T1c-c: `ledger-support.ts` `ledgerParts`, `ledgerOn` and `inLedger`.** Built in T1c-c (above). They copy the object's wiring of the ledger, and about 70 test reaches use them. `ballast` posts up to 50,000 messages in the ledger to seed production's ceilings, about 9 s a seed; through the doors that would be 50,000 door calls a seed. It is its own PR, after T1c-b (`tasks.md` holds the three lines), and is not built here.
  - **Decided (the coordinator, 2026-11-14):** the object exports its one ledger wiring function, the one it calls to build its ledger from its state and env. The tests call that same function in place of `ledgerParts`, so there is one wiring and no copy.
  - Test seeding is not routed through the doors, and no bulk SQL seeds (it would copy the post path's row shapes, which T1b5 rejected).
  - `ballast` keeps seeding through the ledger's own API, on the ledger that function builds.
- **The gate has no withdrawal of one arming.** `disarm` clears every arming, held call and record, so `watchMoves` disarms at its end.
- **`support.ts` `filesOn` and `collectionParts`** build the files and collections as their own modules' tests need them, with tier figures and refusals of their own (`TIERLESS_FULL`). They are the same kind of copy `ledgerParts` was, and `ownerStores` can now replace them. The generation harness's writer is one of them. Not T1c-c's scope.
  - **Not T1d's either: its own PR, researched first.** It is past T1d's 600-line bound. It covers about 90 `inFiles` calls, 18 `filesOn` and 28 `collectionParts`, `inObject` and `inFilled` reaches, in 15 test files.
  - The plan: `inFiles` and `inObject` reach `ownerStores` (through `storesOn`). Each test's `limits` (`keep_versions` 12 times, `retention_days` 11, `keep_recent` 3, `max_file_bytes` 3, `max_bytes` 2, `client_share` and `owner_reserve` 1 each) is set by the owner's real doors:
    - the files policy (`policyWrite`) for the policy figures;
    - the binding's tiers (`storage_bytes`) for `max_bytes`.
  - `TIERLESS_FULL` goes. Its two tests assert the refusal that names the tier.
  - `store` is steered through the env handed to `ownerStores`, as `generate` does.
  - **The research question:** a direct `Files` write calls `changed` (`files.ts`), and a test's stores throw there (`NO_FILE_CHANGE`). Either each act that writes a file moves to its door, or the owner's `fileChanged` is reached some other way. That is decided before the build.
- **The memory probe builds `Backups` by hand** (`memory/probe.ts` `legBurst`, `legShip`), beside `LegArchive#ledger`. Built in T1d (above).
- **The generation harness wraps `FILES` itself**, as it did before the `FILES` gate existed. The gate can hold and refuse a call, but cannot lose an answer that landed (`failAt` "after").
