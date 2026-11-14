# T1c as built (2026-11-14)

T1c is split at its natural seams, in three PRs (T1c-a, T1c-b, T1c-c), each green on its own (B23's "about four PRs" for T1).

## T1c-a: the gates

1. **What T1c-a builds:** the `FILES` and `GATEWAY` gates in the owner suite (`ci/gate.ts`, as `SANDBOX`'s and the `SLOTS` gate are), and the five test classes and two archive hooks they replace:
   - `GatedOwnerDO` (`watched`, `faulted`, `GatewayCall`, `GatewayFaults`), `RungOwnerDO`, `ArchivingOwnerDO` (`ChainHold`), `DivergingOwnerDO` and `PurgingOwnerDO`, with their namespaces;
   - `Internals.chainStep` and `Internals.archiveBetweenPasses`, with `ChainStep`, `ArchiveMoveSeen` and the archive's two parts.

   It also builds the owner suite's lease clock under a fake `Date` (list 6, item 6j) and the object's `admitted` held by a test.
2. **What T1c-b builds, from `main` once T1c-a merges:** the real entry. That is `main: src/index.ts` and `Internals` deleted with the classes left (`OwnerDO`'s null starter, `SeededOwnerDO`, `FauxOwnerDO`, `SwitchedOwnerDO`, `CachedOwnerDO`), `test/worker.ts`, `test/namespaces.ts` and `test/admitted.ts`. It covers:
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

## Owed

- **T1c-b2** (the real entry and the starter) and **`BackupParts`' `sizes?` and `pool?`**, as T1c-b's item 6 says.
- **T1c-c: `ledger-support.ts` `ledgerParts`, `ledgerOn` and `inLedger`.** They copy the object's wiring of the ledger, and about 70 test reaches use them. `ballast` posts up to 50,000 messages in the ledger to seed production's ceilings, about 9 s a seed; through the doors that would be 50,000 door calls a seed. It is its own PR, after T1c-b (`tasks.md` holds the three lines), and is not built here.
  - **Decided (the coordinator, 2026-11-14):** the object exports its one ledger wiring function, the one it calls to build its ledger from its state and env. The tests call that same function in place of `ledgerParts`, so there is one wiring and no copy.
  - Test seeding is not routed through the doors, and no bulk SQL seeds (it would copy the post path's row shapes, which T1b5 rejected).
  - `ballast` keeps seeding through the ledger's own API, on the ledger that function builds.
- **The gate has no withdrawal of one arming.** `disarm` clears every arming, held call and record, so `watchMoves` disarms at its end.
